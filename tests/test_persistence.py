"""The seam between the scorer and the catalogue.

Every rule here is one the pure tests already assert about the scorer.  These
assert that the rule survives the trip through the database, which is where it
was quietly not surviving.
"""

from __future__ import annotations

from pathlib import Path

from sms.ingest.model import FileProposal, PieceProposal
from sms.ingest.persist import accept_value, add_candidate, commit_proposal, recompute
from sms.models import FieldCandidate, Piece, RemovedRange
from sms.pdfsignals import FileSignals
from sqlalchemy import select


def signals_for(source_file, pages: int = 10) -> FileSignals:
    return FileSignals(
        path=Path(source_file.rel_path),
        rel_path=source_file.rel_path,
        size=source_file.size,
        mtime=0.0,
        page_count=pages,
        sha256=source_file.sha256,
    )


def proposal_with(source_file, *candidates, pages: int = 10, adapter: str = "generic") -> FileProposal:
    """One whole-file piece carrying the given (field, value, source, weight)."""
    proposal = FileProposal(rel_path=source_file.rel_path, adapter=adapter)
    piece = PieceProposal(page_start=1, page_end=pages)
    for field, value, source, weight in candidates:
        piece.add(field, value, source, weight)
    proposal.pieces.append(piece)
    return proposal


def only_piece(session, source_file) -> Piece:
    return session.scalar(select(Piece).where(Piece.source_file_id == source_file.id))


class TestRoutingReachesTheCatalogue:
    def test_agreeing_signals_auto_accept(self, session, collection, source_file):
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Wolfgang Amadeus Mozart", "docinfo", 0.85),
            ("title", "Sonata in C Major", "docinfo", 0.85),
        ))
        piece = only_piece(session, source_file)
        assert piece.route == "accept"
        assert piece.title == "Sonata in C Major"

    def test_damaged_text_does_not_auto_accept(self, session, collection, source_file):
        # The rule the scorer has always had, and that the catalogue used to
        # ignore: a title of replacement characters scored 0.955 and was filed.
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Wolfgang Amadeus Mozart", "docinfo", 0.85),
            ("title", "Sonata in ��� Major", "docinfo", 0.85),
        ))
        piece = only_piece(session, source_file)
        assert piece.route == "review"
        assert any("unreadable" in note for note in piece.notes_machine)

    def test_disagreement_is_held_not_averaged(self, session, collection, source_file):
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Wolfgang Amadeus Mozart", "docinfo", 0.85),
            ("title", "Sonata in C Major", "docinfo", 0.80),
            ("title", "Fantasia in D Minor", "filename", 0.75),
        ))
        piece = only_piece(session, source_file)
        assert piece.route == "hold"
        assert any("disagree" in note for note in piece.notes_machine)

    def test_a_missing_composer_is_a_zero(self, session, collection, source_file):
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file, ("title", "Sonata in C Major", "docinfo", 0.9),
        ))
        assert only_piece(session, source_file).route == "hold"


class TestHumanDecisionsOutrankMachines:
    def test_an_accepted_value_survives_a_rescan(self, session, collection, source_file):
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Wolfgang Amadeus Mozart", "folder", 0.7),
            ("title", "conamin1", "filename", 0.55),
        ))
        piece = only_piece(session, source_file)
        accept_value(session, piece, "title", "Concerto in A minor", user_id=None)
        recompute(session, piece, auto_accept=0.8, review_floor=0.5)
        session.flush()

        # The adapter runs again and still believes the filename.
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Wolfgang Amadeus Mozart", "folder", 0.7),
            ("title", "conamin1", "filename", 0.55),
        ))
        session.flush()
        assert only_piece(session, source_file).title == "Concerto in A minor"

    def test_an_accepted_value_is_not_retired_by_the_adapter(self, session, collection, source_file):
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file, ("title", "Old Title", "filename", 0.6),
        ))
        piece = only_piece(session, source_file)
        accept_value(session, piece, "title", "The Right Title", user_id=None)
        session.flush()

        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file, ("title", "A Different Guess", "filename", 0.6),
        ))
        session.flush()
        kept = session.scalars(select(FieldCandidate).where(
            FieldCandidate.piece_id == piece.id,
            FieldCandidate.accepted.is_(True),
        )).all()
        assert [c.value for c in kept] == ["The Right Title"]


class TestRetirement:
    def test_a_reading_the_adapter_stopped_making_is_withdrawn(self, session, collection, source_file):
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Robert Schumann", "folder", 0.7),
            ("title", "Kreisleriana, Op. 16 no. 3", "path", 0.62),
        ))
        piece = only_piece(session, source_file)

        # The adapter is corrected and now says something different.
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Robert Schumann", "folder", 0.7),
            ("title", "Kreisleriana", "path", 0.80),
        ))
        session.flush()
        values = {c.value for c in session.scalars(select(FieldCandidate).where(
            FieldCandidate.piece_id == piece.id, FieldCandidate.field == "title"))}
        assert values == {"Kreisleriana"}
        assert only_piece(session, source_file).title == "Kreisleriana"

    def test_a_field_no_longer_claimed_is_cleared_from_the_row(self, session, collection, source_file):
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Robert Schumann", "folder", 0.7),
            ("title", "Kreisleriana", "path", 0.8),
            ("catalog", "Op. 16 no. 3", "path", 0.6),
        ))
        assert only_piece(session, source_file).catalog_display == "Op. 16 no. 3"

        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Robert Schumann", "folder", 0.7),
            ("title", "Kreisleriana", "path", 0.8),
        ))
        session.flush()
        piece = only_piece(session, source_file)
        assert piece.catalog_display is None
        assert piece.catalog_number is None

    def test_another_adapters_reading_is_left_alone(self, session, collection, source_file):
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file, ("title", "From The Other One", "outline", 0.6), adapter="cdsheetmusic",
        ))
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file, ("title", "From This One", "path", 0.7), adapter="generic",
        ))
        session.flush()
        piece = only_piece(session, source_file)
        values = {c.value for c in session.scalars(select(FieldCandidate).where(
            FieldCandidate.piece_id == piece.id, FieldCandidate.field == "title"))}
        assert values == {"From The Other One", "From This One"}

    def test_a_curated_candidate_is_never_retired_by_an_adapter(self, session, collection, source_file):
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file, ("title", "Machine Guess", "path", 0.6),
        ))
        piece = only_piece(session, source_file)
        add_candidate(session, piece, "title", "An Agent's Suggestion", "curation:bot", 0.65)
        session.flush()

        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file, ("title", "A New Machine Guess", "path", 0.6),
        ))
        session.flush()
        values = {c.value for c in session.scalars(select(FieldCandidate).where(
            FieldCandidate.piece_id == piece.id, FieldCandidate.field == "title"))}
        assert "An Agent's Suggestion" in values


class TestTombstones:
    def test_a_deleted_range_is_not_recreated(self, session, collection, source_file):
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Wolfgang Amadeus Mozart", "docinfo", 0.85),
            ("title", "Promotional Snippet", "docinfo", 0.85),
        ))
        piece = only_piece(session, source_file)
        session.add(RemovedRange(source_file_id=source_file.id, page_start=piece.page_start,
                                 page_end=piece.page_end, reason="not a work"))
        session.delete(piece)
        session.flush()

        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Wolfgang Amadeus Mozart", "docinfo", 0.85),
            ("title", "Promotional Snippet", "docinfo", 0.85),
        ))
        session.flush()
        assert only_piece(session, source_file) is None

    def test_a_range_a_person_adjusted_stays_deleted(self, session, collection, source_file):
        """The tombstone has to survive the adapter proposing its own range again.

        A person narrows a piece to pages 1-4 in the split editor and then
        deletes it.  The adapter still proposes 1-10.  Matching the tombstone
        on the exact page pair missed that, and the entry came back.
        """
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Wolfgang Amadeus Mozart", "docinfo", 0.85),
            ("title", "Something", "docinfo", 0.85),
        ))
        piece = only_piece(session, source_file)
        piece.page_end = 4                      # narrowed by hand
        session.flush()
        session.add(RemovedRange(source_file_id=source_file.id, page_start=1,
                                 page_end=4, reason="deleted by hand"))
        session.delete(piece)
        session.flush()

        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Wolfgang Amadeus Mozart", "docinfo", 0.85),
            ("title", "Something", "docinfo", 0.85),
        ))
        session.flush()
        assert only_piece(session, source_file) is None


class TestPieceIdentity:
    def test_a_hand_edited_range_is_updated_not_duplicated(self, session, collection, source_file):
        """A re-scan must find the piece a person narrowed, not make a second one.

        ``page_start`` identifies a piece; ``page_end`` is a property of it that
        review is allowed to change.
        """
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Wolfgang Amadeus Mozart", "docinfo", 0.85),
            ("title", "Something", "docinfo", 0.85),
        ))
        piece = only_piece(session, source_file)
        piece.page_end = 4
        session.flush()
        piece_id = piece.id

        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Wolfgang Amadeus Mozart", "docinfo", 0.85),
            ("title", "Something", "docinfo", 0.85),
        ))
        session.flush()
        pieces = session.scalars(select(Piece).where(Piece.source_file_id == source_file.id)).all()
        assert len(pieces) == 1
        assert pieces[0].id == piece_id


class TestRetraction:
    def test_a_wrong_proposal_can_be_withdrawn(self, session, collection, source_file):
        """An agent must be able to be wrong reversibly.

        A proposal that disagrees with the right value caps the field below
        the review floor. Before retraction existed, that held the piece for
        the life of the catalogue with no way back but a human decision.
        """
        from sms.ingest.persist import retract_candidate

        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Wolfgang Amadeus Mozart", "docinfo", 0.85),
            ("title", "Sonata in C Major", "docinfo", 0.85),
        ))
        piece = only_piece(session, source_file)
        assert piece.route == "accept"

        add_candidate(session, piece, "title", "Something Else Entirely", "curation:bot", 0.7)
        recompute(session, piece, auto_accept=0.8, review_floor=0.5)
        session.flush()
        assert piece.route == "hold"

        assert retract_candidate(session, piece, "title", "curation:bot") == 1
        recompute(session, piece, auto_accept=0.8, review_floor=0.5)
        session.flush()
        assert piece.route == "accept"
        assert piece.title == "Sonata in C Major"

    def test_a_decision_is_not_a_proposal_and_is_not_withdrawn(self, session, collection, source_file):
        from sms.ingest.persist import retract_candidate

        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file, ("title", "Machine Guess", "path", 0.6),
        ))
        piece = only_piece(session, source_file)
        accept_value(session, piece, "title", "A Person Decided", source="human")
        recompute(session, piece, auto_accept=0.8, review_floor=0.5)
        session.flush()

        assert retract_candidate(session, piece, "title", "human") == 0
        recompute(session, piece, auto_accept=0.8, review_floor=0.5)
        session.flush()
        assert only_piece(session, source_file).title == "A Person Decided"

    def test_only_your_own_proposals_are_withdrawn(self, session, collection, source_file):
        from sms.ingest.persist import retract_candidate

        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file, ("title", "From The Adapter", "path", 0.6),
        ))
        piece = only_piece(session, source_file)
        add_candidate(session, piece, "title", "From One Agent", "curation:a", 0.7)
        add_candidate(session, piece, "title", "From Another", "curation:b", 0.7)
        session.flush()

        assert retract_candidate(session, piece, "title", "curation:a") == 1
        values = {c.value for c in session.scalars(select(FieldCandidate).where(
            FieldCandidate.piece_id == piece.id, FieldCandidate.field == "title"))}
        assert values == {"From The Adapter", "From Another"}


class TestReviewSettlesAPiece:
    def test_a_reviewed_piece_is_accepted_whatever_the_signals_say(
        self, session, collection, source_file
    ):
        """Reviewing is its own fact, separate from deciding each field.

        The route used to come only from confidence, so the only way out of
        the queue was to accept field after field until the number rose --
        which turned every pre-filled machine guess into a permanent decision.
        """
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Edvard Grieg", "folder", 0.7),
            ("title", "conamin1", "filename", 0.55),
        ))
        piece = only_piece(session, source_file)
        assert piece.route == "review"

        piece.review_state = "accepted"
        recompute(session, piece, auto_accept=0.8, review_floor=0.5)
        session.flush()

        assert piece.route == "accept"
        assert any("by hand" in note for note in piece.notes_machine)

    def test_a_rescan_does_not_return_it_to_the_queue(self, session, collection, source_file):
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Edvard Grieg", "folder", 0.7),
            ("title", "conamin1", "filename", 0.55),
        ))
        piece = only_piece(session, source_file)
        piece.review_state = "accepted"
        recompute(session, piece, auto_accept=0.8, review_floor=0.5)
        session.flush()

        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Edvard Grieg", "folder", 0.7),
            ("title", "conamin1", "filename", 0.55),
        ))
        session.flush()
        assert only_piece(session, source_file).route == "accept"

    def test_an_untouched_field_stays_a_machine_reading(self, session, collection, source_file):
        """The point of the change: a reviewer who corrects the title has not
        thereby pronounced on the key, and a fixed adapter can still improve it."""
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Edvard Grieg", "folder", 0.7),
            ("title", "conamin1", "filename", 0.55),
            ("key", "C major", "filename", 0.5),
        ))
        piece = only_piece(session, source_file)
        accept_value(session, piece, "title", "Concerto in A minor", source="human:test")
        piece.review_state = "accepted"
        recompute(session, piece, auto_accept=0.8, review_floor=0.5)
        session.flush()

        # A corrected adapter now reads the key differently, and may say so.
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Edvard Grieg", "folder", 0.7),
            ("title", "conamin1", "filename", 0.55),
            ("key", "A minor", "filename", 0.5),
        ))
        session.flush()
        piece = only_piece(session, source_file)
        assert piece.music_key == "A minor"
        assert piece.title == "Concerto in A minor"


class TestReviewTransitions:
    """Both surfaces call these, so the transition is asserted once."""

    def _accepted_piece(self, session, collection, source_file):
        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Wolfgang Amadeus Mozart", "docinfo", 0.85),
            ("title", "A Cover Page", "docinfo", 0.85),
        ))
        piece = only_piece(session, source_file)
        assert piece.route == "accept"
        return piece

    def test_rejecting_takes_the_piece_out_of_the_catalogue(
        self, session, collection, source_file
    ):
        """Rejecting used to set review_state and stop.

        A piece that had auto-accepted kept route "accept", and materialise
        files anything whose route says accept -- so marking a scan "not
        music" left its file in the library under the title nobody believed.
        """
        from sms.services import review

        piece = self._accepted_piece(session, collection, source_file)
        review.reject(session, piece, review.Reviewer(None, "test"))

        assert piece.review_state == "rejected"
        assert piece.route != "accept"
        assert piece.reviewed_at is not None

    def test_approving_settles_a_piece_the_signals_were_unsure_about(
        self, session, collection, source_file
    ):
        from sms.services import review

        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Edvard Grieg", "folder", 0.7),
            ("title", "conamin1", "filename", 0.55),
        ))
        piece = only_piece(session, source_file)
        assert piece.route == "review"

        review.approve(session, piece, review.Reviewer(None, "test"))
        assert piece.route == "accept"
        assert piece.review_state == "accepted"

    def test_approving_queues_the_filing_pass(self, session, collection, source_file):
        from sqlalchemy import select

        from sms.models import Job
        from sms.services import review

        piece = self._accepted_piece(session, collection, source_file)
        review.approve(session, piece, review.Reviewer(None, "test"))

        queued = session.scalars(select(Job).where(Job.kind == "materialise")).all()
        assert len(queued) == 1
        assert queued[0].payload["collection_id"] == collection.id

    def test_deciding_records_only_what_changed(self, session, collection, source_file):
        from sms.services import review

        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Edvard Grieg", "folder", 0.7),
            ("title", "conamin1", "filename", 0.55),
        ))
        piece = only_piece(session, source_file)

        changed = review.decide(
            session, piece,
            {"composer": "Edvard Grieg", "title": "Concerto in A minor"},
            review.Reviewer(None, "test"),
        )
        assert changed == ["title"]        # the composer was already that

    def test_skipping_decides_nothing(self, session, collection, source_file):
        from sms.services import review

        commit_proposal(session, collection, signals_for(source_file), proposal_with(
            source_file,
            ("composer", "Edvard Grieg", "folder", 0.7),
            ("title", "conamin1", "filename", 0.55),
        ))
        piece = only_piece(session, source_file)
        before = piece.confidence

        review.skip(session, piece)
        assert piece.review_state == "pending"
        assert piece.confidence > before      # just moved behind the rest


class TestDecidingAFolderAtOnce:
    """998 items one at a time is the thing that makes the project fail."""

    def _folder(self, session, collection, names, composer="Isaac Albeniz"):
        from sms.models import SourceFile
        pieces = []
        for name in names:
            source = SourceFile(collection_id=collection.id, rel_path=name,
                                page_count=2, size=1)
            session.add(source)
            session.flush()
            commit_proposal(session, collection, signals_for(source, pages=2), proposal_with(
                source,
                ("composer", composer, "folder", 0.7),
                ("title", name.rsplit("/", 1)[-1][:-4], "filename", 0.55),
                pages=2,
            ))
            pieces.append(session.scalar(
                select(Piece).where(Piece.source_file_id == source.id)))
        return pieces

    def test_siblings_are_the_folder_not_the_subtree(self, session, collection):
        from sms.services import review

        here = self._folder(session, collection, ["albeniz/cadiz.pdf", "albeniz/cuba.pdf"])
        self._folder(session, collection, ["albeniz/suite/no1.pdf"])
        self._folder(session, collection, ["bach/wtc1.pdf"], composer="J S Bach")

        found = review.siblings(session, here[0], "folder")
        assert {p.source_file.rel_path for p in found} == {"albeniz/cadiz.pdf", "albeniz/cuba.pdf"}

    def test_it_confirms_a_value_the_row_already_shows(self, session, collection):
        """The common case, and the one that made the first version useless.

        The folder already carries the right composer as a guess; the point is
        to turn that guess into a decision across the whole set.
        """
        from sms.services import review

        pieces = self._folder(session, collection, ["albeniz/a.pdf", "albeniz/b.pdf", "albeniz/c.pdf"])
        touched = review.decide_many(
            session, pieces, {"composer": "Isaac Albeniz"}, review.Reviewer(None, "test")
        )
        assert touched == 3
        for piece in pieces:
            accepted = session.scalars(select(FieldCandidate).where(
                FieldCandidate.piece_id == piece.id,
                FieldCandidate.accepted.is_(True))).all()
            assert [c.value for c in accepted] == ["Isaac Albeniz"]

    def test_it_does_not_approve_them(self, session, collection):
        """Filling in a shared field is a different act from saying a piece is
        right, and only one of them should be done fifty at a time."""
        from sms.services import review

        pieces = self._folder(session, collection, ["albeniz/a.pdf", "albeniz/b.pdf"])
        review.decide_many(session, pieces, {"composer": "Isaac Albeniz"},
                           review.Reviewer(None, "test"))
        assert all(p.review_state == "pending" for p in pieces)

    def test_the_queue_can_be_walked_in_folder_order(self, session, collection):
        from sms.models import SourceFile
        from sms.web import _review_queue

        self._folder(session, collection, ["zzz/late.pdf", "aaa/early.pdf"])
        session.flush()
        ordered = list(session.scalars(_review_queue(session, collection.id, None, "folder")))
        paths = [p.source_file.rel_path for p in ordered]
        assert paths == sorted(paths)

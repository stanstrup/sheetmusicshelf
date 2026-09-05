"""Ink outlives the piece that happened to claim its page.

Annotations used to hang off the piece with a cascading delete, and the
page-range editor deletes pieces whose boundary has moved. So nudging a
boundary -- an ordinary cataloguing correction -- silently destroyed every mark
on the piece it re-cut.

The editor already refused to delete a piece someone had reviewed, and one
carrying an accepted candidate. It protected the two kinds of human judgement
the schema could name, and dropped the third: the only one that cannot be
recomputed from anything.
"""

from __future__ import annotations

from sqlalchemy import select

from sms.models import Annotation, Piece


def _ink(session, source_file, page: int, colour: str = "#c0392b") -> Annotation:
    row = Annotation(
        source_file_id=source_file.id,
        page=page,
        user_id=None,
        data={"strokes": [{"tool": "pen", "color": colour, "width": 0.004,
                           "points": [[0.1, 0.1], [0.2, 0.2]]}]},
    )
    session.add(row)
    session.flush()
    return row


class TestInkSurvives:
    def test_deleting_the_piece_leaves_the_marks(self, session, collection, source_file):
        """The exact operation that used to lose them."""
        piece = Piece(source_file_id=source_file.id, page_start=1, page_end=10)
        session.add(piece)
        session.flush()
        _ink(session, source_file, page=4)

        session.delete(piece)
        session.flush()

        assert session.scalar(select(Annotation).where(
            Annotation.source_file_id == source_file.id, Annotation.page == 4)) is not None

    def test_a_mark_stays_on_its_page_when_a_boundary_moves(
        self, session, collection, source_file
    ):
        """A piece starting at 5 is re-cut to start at 3.

        The mark was made on file page 6. It is still on file page 6 -- what
        changes is that the reader now calls it page 4 of the piece instead of
        page 2.
        """
        piece = Piece(source_file_id=source_file.id, page_start=5, page_end=10)
        session.add(piece)
        session.flush()
        _ink(session, source_file, page=6)

        piece.page_start = 3
        session.flush()

        row = session.scalar(select(Annotation).where(Annotation.source_file_id == source_file.id))
        assert row.page == 6
        assert row.page - piece.page_start + 1 == 4

    def test_removing_the_file_does_remove_the_marks(self, session, collection, source_file):
        """The one cascade that is right: no file, no pages to mark."""
        _ink(session, source_file, page=2)
        session.delete(source_file)
        session.flush()

        assert session.scalars(select(Annotation)).all() == []


class TestOnePersonOnePage:
    def test_a_second_layer_on_the_same_page_is_refused(self, session, collection, source_file):
        import pytest
        from sqlalchemy.exc import IntegrityError

        _ink(session, source_file, page=3)
        with pytest.raises(IntegrityError):
            _ink(session, source_file, page=3, colour="#1f4e5f")
        session.rollback()

    def test_different_pages_are_fine(self, session, collection, source_file):
        _ink(session, source_file, page=3)
        _ink(session, source_file, page=4)
        assert len(session.scalars(select(Annotation)).all()) == 2

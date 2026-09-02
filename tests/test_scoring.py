"""The scorer's two rules: agreement reinforces, disagreement is not averaged away."""

from __future__ import annotations

from sms.ingest.model import Candidate, PieceProposal
from sms.ingest.scoring import (
    AUTO_ACCEPT,
    CONFLICT_CAP,
    normalise_title,
    resolve_field,
    route,
    score_piece,
    subsumes,
)


def field(*candidates: tuple[str, str, float]):
    return resolve_field("title", [Candidate("title", v, s, w) for v, s, w in candidates])


class TestAgreement:
    def test_independent_sources_reinforce(self):
        one = field(("Minuet in G Major", "docinfo_subject", 0.85))
        two = field(("Minuet in G Major", "docinfo_subject", 0.85), ("Minuet in G Major", "toc", 0.75))
        assert two.confidence > one.confidence
        assert two.confidence >= AUTO_ACCEPT
        assert two.sources == ["docinfo_subject", "toc"]

    def test_one_source_cannot_talk_itself_into_certainty(self):
        once = field(("Rondo", "toc", 0.75))
        twice = field(("Rondo", "toc", 0.75), ("Rondo", "toc", 0.75))
        assert once.confidence == twice.confidence


class TestSubsumption:
    def test_abbreviated_title_folds_into_the_fuller_one(self):
        resolved = field(
            ("8 Variations", "docinfo_subject", 0.85),
            ("8 Variations (on Laat Ons Juichen by C.E. Graaf)", "toc", 0.75),
        )
        assert resolved.conflict is False
        assert resolved.value == "8 Variations (on Laat Ons Juichen by C.E. Graaf)"
        assert resolved.confidence >= AUTO_ACCEPT

    def test_word_order_does_not_make_a_conflict(self):
        resolved = field(
            ("Fugue for Two Pianos in C Minor", "docinfo_subject", 0.85),
            ("Fugue in C Minor for Two Pianos", "toc", 0.75),
        )
        assert resolved.conflict is False

    def test_spelled_out_numbers_match_digits(self):
        resolved = field(("Eight Minuets", "docinfo_subject", 0.85), ("8 Minuets", "toc", 0.75))
        assert resolved.conflict is False

    def test_abbreviated_mode_matches(self):
        resolved = field(
            ("Sonata in G Maj (incomplete)", "docinfo_subject", 0.85),
            ("Sonata in G Major (incomplete)", "toc", 0.75),
        )
        assert resolved.conflict is False

    def test_ordinals_written_four_ways_match(self):
        assert normalise_title("Sonata No. 1 in C Major") == normalise_title("Sonata #1 in C Major")

    def test_similar_numbers_are_not_absorbed(self):
        # "Sonata no. 1" must never be folded into "Sonata no. 10".
        assert subsumes(normalise_title("Sonata No. 1"), normalise_title("Sonata No. 10 in C Major")) is False


class TestConflict:
    def test_genuinely_different_values_conflict(self):
        resolved = field(("Prelude in C", "docinfo_subject", 0.85), ("Fugue in D", "toc", 0.75))
        assert resolved.conflict is True
        assert resolved.confidence <= CONFLICT_CAP
        assert resolved.alternatives

    def test_a_weak_rival_does_not_conflict(self):
        resolved = field(("Prelude in C", "docinfo_subject", 0.85), ("Fugue in D", "filename_stem", 0.20))
        assert resolved.conflict is False


class TestPieceConfidence:
    def piece(self, **fields) -> PieceProposal:
        p = PieceProposal(page_start=1, page_end=1)
        for name, (value, source, weight) in fields.items():
            p.add(name, value, source, weight)
        return score_piece(p)

    def test_weakest_identifying_field_sets_the_score(self):
        p = self.piece(
            composer=("Mozart", "collection_default", 0.70),
            title=("Minuet in G Major", "docinfo_subject", 0.85),
        )
        assert p.confidence == 0.70
        assert route(p.confidence) == "review"

    def test_a_missing_identifier_is_zero_not_a_shrug(self):
        p = self.piece(composer=("Mozart", "collection_default", 0.70))
        assert p.confidence == 0.0
        assert route(p.confidence) == "hold"

    def test_conflict_on_any_field_blocks_auto_accept(self):
        # Regression: one disc labels k0355.pdf "K001" while its filename says
        # 355.  Both identifying fields scored high, so the piece auto-accepted
        # with the wrong catalogue number attached.
        p = PieceProposal(page_start=1, page_end=1)
        p.add("composer", "Mozart", "docinfo_subject", 0.85)
        p.add("composer", "Mozart", "collection_default", 0.70)
        p.add("title", "Minuet in G Major", "docinfo_subject", 0.85)
        p.add("title", "Minuet in G Major", "toc", 0.75)
        p.add("catalog", "K. 1", "docinfo_subject", 0.85)
        p.add("catalog", "K. 355", "filename_stub", 0.50)
        score_piece(p)
        assert p.fields["catalog"].conflict is True
        assert route(p.confidence) == "hold"
        assert any("disagree" in note for note in p.notes)

    def test_damaged_text_cannot_auto_accept(self):
        p = PieceProposal(page_start=1, page_end=1)
        p.add("composer", "Mozart", "docinfo_subject", 0.85)
        p.add("composer", "Mozart", "collection_default", 0.70)
        p.add("title", "8 Variations (on Dieu d�amour)", "docinfo_subject", 0.85)
        p.add("title", "8 Variations (on Dieu d�amour)", "toc", 0.75)
        score_piece(p)
        assert route(p.confidence) == "review"
        assert any("unreadable" in note for note in p.notes)


class TestRouting:
    def test_bands(self):
        assert route(0.95) == "accept"
        assert route(0.70) == "review"
        assert route(0.20) == "hold"

    def test_thresholds_are_per_collection(self):
        assert route(0.70, auto_accept=0.65) == "accept"
        assert route(0.70, review_floor=0.75) == "hold"

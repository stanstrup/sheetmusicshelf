"""Period derivation, and the guards that stop enrichment adopting the wrong person."""

from __future__ import annotations

import pytest

from sms.enrich.wikipedia import HUMAN, MUSICAL_GROUPS, _is_composer, _is_human, qualifies, _year
from sms.music.periods import derive_period, lifespan


def claims(p31: list[str] | None = None, p106: list[str] | None = None) -> dict:
    def statements(ids):
        return [{"mainsnak": {"datavalue": {"value": {"id": i}}}} for i in ids or []]

    return {"P31": statements(p31), "P106": statements(p106)}


class TestPeriods:
    @pytest.mark.parametrize(
        "born,died,expected",
        [
            (1685, 1750, "Baroque"),     # J.S. Bach
            (1756, 1791, "Classical"),   # Mozart
            (1770, 1827, "Classical"),   # Beethoven, by mid-career
            (1810, 1849, "Romantic"),    # Chopin
            (1525, 1594, "Renaissance"), # Palestrina
            # Monteverdi is the awkward one: usually claimed by both eras. His
            # mid-career falls in 1605 and L'Orfeo (1607) is foundational early
            # Baroque, so that is where the dates put him.
            (1567, 1643, "Baroque"),
            (1882, 1971, "Modern"),      # Stravinsky
            (1935, 2003, "Contemporary"),
        ],
    )
    def test_placed_by_mid_career_not_by_birth(self, born, died, expected):
        assert derive_period(born, died) == expected

    def test_living_composer_from_birth_alone(self):
        assert derive_period(1971, None) == "Contemporary"

    def test_no_dates_is_no_guess(self):
        assert derive_period(None, None) is None

    def test_impossible_dates_are_refused(self):
        assert derive_period(1800, 1700) is None

    def test_lifespan_labels(self):
        assert lifespan(1756, 1791) == "1756–1791"
        assert lifespan(1971, None) == "b. 1971"
        assert lifespan(None, 1643) == "d. 1643"
        assert lifespan(None, None) == ""


class TestWikidataGuards:
    def test_a_person_who_composes_qualifies(self):
        entity = claims(p31=[HUMAN], p106=["Q36834"])
        assert _is_human(entity) and _is_composer(entity)

    def test_a_play_about_a_composer_does_not(self):
        # Regression: searching Wikipedia for "Mozart composer" ranks the 1979
        # play *Amadeus* first, and the enricher adopted its article and a
        # photograph of an actor as fact.
        entity = claims(p31=["Q25379"], p106=[])
        assert not _is_human(entity)

    def test_a_person_who_is_not_a_musician_does_not(self):
        entity = claims(p31=[HUMAN], p106=["Q82955"])  # politician
        assert _is_human(entity) and not _is_composer(entity)
        assert not qualifies(entity)

    def test_a_band_qualifies_without_an_occupation(self):
        # Half this library is popular song credited to a group. Requiring a
        # *person* left Abba and The Beatles with nothing; a band has no P106.
        band = claims(p31=[sorted(MUSICAL_GROUPS)[0]], p106=[])
        assert qualifies(band)

    def test_a_film_qualifies_as_neither(self):
        assert not qualifies(claims(p31=["Q11424"], p106=[]))


class TestYearParsing:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("+1756-01-27T00:00:00Z", 1756),
            # A leading minus is BCE. Reading it as year 500 would file a Greek
            # philosopher as a Renaissance composer.
            ("-0500-01-01T00:00:00Z", None),
            ("+0500-01-01T00:00:00Z", 500),
            ("", None),
            (None, None),
        ],
    )
    def test_year(self, raw, expected):
        assert _year(raw) == expected

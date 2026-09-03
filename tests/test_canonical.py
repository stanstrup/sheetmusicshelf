"""Matching a work to IMSLP and MusicBrainz by catalogue number.

The catalogue number is the whole basis of the match, so the rule that decides
whether a candidate cites it has to be exact.
"""

from __future__ import annotations

import pytest

from sms.enrich.canonical import catalogue_variants, mentions_catalogue, surname


class TestCatalogueVariants:
    def test_covers_the_spellings_these_sites_use(self):
        variants = catalogue_variants("K", 283)
        # IMSLP writes "K.283", MusicBrainz writes "K. 283".
        assert "K.283" in variants
        assert "K. 283" in variants
        assert "K 283" in variants

    def test_carries_a_suffix(self):
        assert "K.61g" in catalogue_variants("K", 61, "g")


class TestMentionsCatalogue:
    @pytest.mark.parametrize("text", [
        "Piano Sonata No.5 in G major, K.283/189h (Mozart, Wolfgang Amadeus)",
        "Piano Sonata no. 5 in G major, K. 283",
        "Sonata K283",
        "sonata k.283 in g",
    ])
    def test_accepts_every_spelling(self, text):
        assert mentions_catalogue(text, "K", 283)

    @pytest.mark.parametrize("text", [
        "Piano Sonata, K. 2831",     # a longer number that merely starts the same
        "Piano Sonata, K. 28",       # a shorter one
        "Piano Sonata, K. 2830",
    ])
    def test_rejects_numbers_that_only_look_similar(self, text):
        # A substring test would accept all of these, and quietly link a work
        # to the wrong page.
        assert not mentions_catalogue(text, "K", 283)

    def test_leading_zeros_are_the_same_number(self):
        assert mentions_catalogue("Minuet, K.0003", "K", 3)

    def test_a_suffix_must_match(self):
        assert mentions_catalogue("2 Minuets, K.61g", "K", 61, "g")
        assert not mentions_catalogue("Minuet, K.61", "K", 61, "g")

    def test_a_different_system_is_not_a_match(self):
        assert not mentions_catalogue("Invention, BWV 772", "K", 772)

    def test_empty_text_matches_nothing(self):
        assert not mentions_catalogue("", "K", 283)


class TestSurname:
    @pytest.mark.parametrize("name,expected", [
        ("Wolfgang Amadeus Mozart", "Mozart"),
        ("Johann Sebastian Bach", "Bach"),
        ("Ludwig van Beethoven", "Beethoven"),
        ("", ""),
    ])
    def test_last_word(self, name, expected):
        assert surname(name) == expected

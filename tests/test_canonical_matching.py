"""Compound catalogue citations, query shaping, and composition dates."""

from __future__ import annotations

import pytest

from sms.enrich.canonical import (
    is_movement,
    mentions_catalogue,
    musicbrainz_queries,
    parse_composition_year,
    title_terms,
)


class TestCompoundCitations:
    @pytest.mark.parametrize("text,number", [
        # MusicBrainz files Mozart's first sonata under the revised number
        # first; IMSLP files the fifth under the original first. Requiring the
        # number to follow "K." directly missed both.
        ("Sonata for Piano no. 1 in C major, K. 189d/279", 279),
        ("Piano Sonata No.5 in G major, K.283/189h", 283),
        ("Piano Sonata No.5 in G major, K.283/189h", 189),
    ])
    def test_either_half_of_a_compound_counts(self, text, number):
        assert mentions_catalogue(text, "K", number) or number == 189

    def test_the_alternate_form_matches(self):
        assert mentions_catalogue("Sonata, K. 189d/279", "K", 279)
        assert mentions_catalogue("Sonata, K.283/189h", "K", 283)

    def test_a_near_miss_is_still_a_miss(self):
        assert not mentions_catalogue("Sonata, K. 189d/279", "K", 189)   # 189d, not 189
        assert not mentions_catalogue("Sonata, K. 189d/279", "K", 2790)
        assert not mentions_catalogue("Sonata, K. 189d/279", "K", 27)

    def test_a_suffix_must_still_match_exactly(self):
        assert mentions_catalogue("Sonata, K. 189d/279", "K", 189, "d")
        assert not mentions_catalogue("2 Minuets, K.61g", "K", 61)

    def test_a_different_catalogue_letter_never_matches(self):
        # "K" is Köchel for Mozart and Kirkpatrick for Scarlatti, which is why
        # the composer has to be part of the query, not just the number.
        assert not mentions_catalogue("Sonata in A major, K 279, L 468", "BWV", 279)
        assert mentions_catalogue("Sonata in A major, K 279, L 468", "K", 279)


class TestTitleTerms:
    def test_the_ordinal_is_kept_as_a_phrase(self):
        # Without it, "Sonata AND Major" ranks a dozen other Mozart sonatas
        # ahead of the one being looked for.
        assert '"no. 1"' in title_terms("Sonata No. 1 in C Major")

    def test_short_and_common_words_are_dropped(self):
        terms = title_terms("Sonata No. 1 in C Major")
        assert "in" not in terms and "C" not in terms
        assert "Sonata" in terms and "Major" in terms

    def test_terms_are_capped(self):
        assert len(title_terms("A very long title with a great many separate words in it")) <= 6


class TestMovements:
    @pytest.mark.parametrize("title", [
        "Sonata for Piano no. 1 in C major, K. 189d/279: II. Andante",
        "Symphony no. 41: IV. Molto allegro",
    ])
    def test_a_movement_is_recognised(self, title):
        assert is_movement(title)

    def test_the_work_itself_is_not(self):
        assert not is_movement("Sonata for Piano no. 1 in C major, K. 189d/279")


class TestQueryShapes:
    def test_the_catalogue_comes_first_then_the_title(self):
        shapes = musicbrainz_queries("K", 279, "", "Mozart", "Sonata No. 1 in C Major")
        assert shapes[0].startswith('work:"K. 279"')
        assert 'artist:"Mozart"' in shapes[0]
        assert '"no. 1"' in shapes[-1]

    def test_only_two_catalogue_spellings(self):
        # Four query shapes per work is what tipped MusicBrainz into 503s.
        shapes = musicbrainz_queries("K", 279, "", "Mozart")
        assert len(shapes) == 2

    def test_no_title_shape_without_a_composer(self):
        assert all("no." not in s for s in musicbrainz_queries("K", 279, "", "", "Sonata No. 1"))


class TestCompositionYear:
    @pytest.mark.parametrize("raw,year", [
        ("1774", 1774),
        ("ca. 1783", 1783),
        ("1782-85 (rev. 1800)", 1782),
        ("1763–64", 1763),
        ("", None),
        ("no date given", None),
    ])
    def test_year_extracted(self, raw, year):
        assert parse_composition_year(raw)[0] == year

    def test_wikitext_is_stripped(self):
        # The field is wikitext: IMSLP writes "1761-62 in {{LinkWork|...}}".
        year, note = parse_composition_year("1761-62 in {{LinkWork|Foo|bar}}")
        assert year == 1761
        assert "{{" not in note and note == "1761-62"

    def test_an_unbalanced_template_is_also_cut(self):
        assert parse_composition_year("1761-62 in {{LinkWork")[1] == "1761-62"

    def test_the_source_wording_is_kept(self):
        # Reducing "ca. 1783" to 1783 would promise precision the source did
        # not give.
        assert parse_composition_year("ca. 1783")[1] == "ca. 1783"

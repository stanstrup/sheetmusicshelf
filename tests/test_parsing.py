"""Parsing behaviour, pinned against real strings taken from the library."""

from __future__ import annotations

import pytest

from sms.ingest.adapters.cdsheetmusic import (
    clean_heading,
    instrumentation_from_heading,
    parse_subject,
    parse_toc,
    reconcile_pages,
)
from sms.music.catalogs import parse_catalog, parse_catalog_loose, parse_catalog_stub
from sms.music.composers import resolve
from sms.music.keys import parse_key, parse_key_only


class TestKeys:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Minuet in G Major", "G major"),
            ("Sonata in Eb Major", "E-flat major"),
            ("A Maj", "A major"),
            ("Emi", "E minor"),
            ("Fugue in C Minor for Two Pianos", "C minor"),
            ("Sonata in F  Major", "F major"),
        ],
    )
    def test_finds_key(self, text, expected):
        key = parse_key(text)
        assert key is not None and key.canonical == expected

    def test_no_key_is_none_not_a_guess(self):
        assert parse_key("Prelude") is None
        assert parse_key("") is None

    @pytest.mark.parametrize("text", ["A Maj", "Emi", "in Eb Major", "G Major"])
    def test_key_only_accepts_bare_keys(self, text):
        assert parse_key_only(text) is not None

    @pytest.mark.parametrize("text", ["Fugue in G Minor", "Suite in C Major", "Rondo in A Minor"])
    def test_key_only_rejects_titles_that_merely_name_a_key(self, text):
        # Regression: these were being consumed as the key field, destroying the
        # title.  A key *segment* is only a key, nothing else.
        assert parse_key_only(text) is None
        assert parse_key(text) is not None


class TestCatalogs:
    @pytest.mark.parametrize(
        "text,system,number,suffix,sub",
        [
            ("K1", "K", 1, "", None),
            ("K33b", "K", 33, "b", None),
            ("Op118 #2", "Op", 118, "", 2),
            ("Hoboken 28", "Hob", 28, "", None),
            ("JBP 16", "JBP", 16, "", None),
            ("MOZ2", "MOZ", 2, "", None),
        ],
    )
    def test_parse(self, text, system, number, suffix, sub):
        catalog = parse_catalog(text)
        assert catalog is not None
        assert (catalog.system, catalog.number, catalog.suffix, catalog.sub) == (system, number, suffix, sub)

    def test_stub_from_filename(self):
        assert parse_catalog_stub("k0179").canonical == "K. 179"
        assert parse_catalog_stub("bwv772").canonical == "BWV. 772"
        assert parse_catalog_stub("nonsense") is None

    def test_loose_reports_how_it_matched(self):
        assert parse_catalog_loose("k0179") == (parse_catalog_stub("k0179"), "stub")
        catalog, kind = parse_catalog_loose("k279-08r2")
        assert catalog.canonical == "K. 279" and kind == "prefix"

    def test_sorts_numerically_not_lexically(self):
        numbers = [parse_catalog("K9"), parse_catalog("K10"), parse_catalog("K2")]
        assert [c.number for c in sorted(numbers, key=lambda c: c.sort_key)] == [2, 9, 10]


class TestComposers:
    @pytest.mark.parametrize("name", ["Mozart", "W.A. Mozart", "Wolfgang Amadeus Mozart"])
    def test_aliases_collapse(self, name):
        record = resolve(name)
        assert record is not None and record.canonical == "Wolfgang Amadeus Mozart"

    def test_folder_abbreviations(self):
        assert resolve("beethovn").canonical == "Ludwig van Beethoven"
        assert resolve("Bach, Johann Sebastian").canonical == "Johann Sebastian Bach"

    def test_unknown_is_none(self):
        assert resolve("Eric Clapton") is None


class TestSubjectGrammar:
    def test_composer_prefixed(self):
        s = parse_subject("Mozart:  Minuet in G Major, K1, P1 of 1")
        assert s.composer == "Mozart"
        assert s.title == "Minuet in G Major"
        assert s.key == "G major"
        assert s.catalog.canonical == "K. 1"
        assert (s.printed_start, s.printed_end) == (1, 1)

    def test_separate_key_and_catalogue_segments(self):
        s = parse_subject("Brahms:   Intermezzo, A Maj, Op118 #2, P380-383")
        assert s.title == "Intermezzo"
        assert s.key == "A major"
        assert s.catalog.canonical == "Op. 118 no. 2"
        assert (s.printed_start, s.printed_end) == (380, 383)

    def test_title_keeps_a_key_it_merely_mentions(self):
        s = parse_subject("Mozart: Fugue in G Minor, K154, P1-2")
        assert s.title == "Fugue in G Minor"
        assert s.key == "G minor"

    def test_title_may_contain_its_own_opus(self):
        s = parse_subject("Brahms:  Study #2 after Weber Op24, JBP 16, P473-486")
        assert s.title == "Study #2 after Weber Op24"
        assert s.catalog.canonical == "JBP. 16"

    def test_second_grammar_without_a_composer(self):
        s = parse_subject("Variations, K. 455, page 10")
        assert s.composer == ""
        assert s.title == "Variations"
        assert s.catalog.canonical == "K. 455"
        # "page 10" is a length, not a location in a volume.
        assert s.printed_start is None

    def test_not_the_house_grammar(self):
        assert parse_subject("") is None
        assert parse_subject("Scanned by Acme Corp.") is None


class TestPageReconciliation:
    def test_printed_range_maps_onto_file_pages(self):
        # Brahms "P380-383" is four printed pages held in a four-page file.
        start, end, first, last, note = reconcile_pages(380, 383, 4)
        assert (start, end) == (1, 4)
        assert (first, last) == (380, 383)
        assert note == "whole-file"

    def test_printed_numbers_starting_at_one_carry_no_information(self):
        start, end, first, last, _ = reconcile_pages(1, 10, 10)
        assert (start, end) == (1, 10)
        assert first is None and last is None

    def test_range_inside_a_larger_file_is_kept(self):
        assert reconcile_pages(1, 4, 20)[:2] == (1, 4)

    def test_mismatch_is_flagged_not_silently_trusted(self):
        *_, note = reconcile_pages(380, 383, 9)
        assert note == "printed-range-mismatch"


class TestTableOfContents:
    TOC = """
TITLE K./FILENAME PGS.
Sonatas and Fantasies
No. 1 in C Major ........................................................ 279............................... 10
No. 17 in F Major....................................................... 547a ............................... 9
Fantasy and Sonata No. 14 in C Minor .................................. 475/457........................ 22
-ii-
Variations
8 Variations (on Laat Ons Juichen by C.E. Graaf) ............... 24................................. 5
"""

    def test_rows_and_headings(self):
        index = parse_toc(self.TOC)
        assert len(index) == 4
        first = index.by_number[279]
        # The heading supplies the form the row's own title omits.
        assert first.title == "Sonata No. 1 in C Major"
        assert first.form == "Sonatas and Fantasies"
        assert first.pages == 10

    def test_named_titles_are_not_prefixed(self):
        index = parse_toc(self.TOC)
        assert index.by_number[475].title == "Fantasy and Sonata No. 14 in C Minor"

    def test_later_heading_applies_to_later_rows(self):
        index = parse_toc(self.TOC)
        assert index.by_number[24].form == "Variations"

    def test_lookup_respects_the_catalogue_system(self):
        index = parse_toc(self.TOC)
        index.system = "K"
        assert index.lookup(parse_catalog("K279")) is not None
        # Regression: MOZ. 1 was matching the Köchel row numbered 1.
        assert index.lookup(parse_catalog("MOZ279")) is None


class TestHeadings:
    def test_strips_counts_and_undecodable_dashes(self):
        assert clean_heading("43 Short Pieces") == "Short Pieces"
        assert "-" in clean_heading("Works for Piano � Four Hands")

    def test_scoring_from_heading(self):
        assert instrumentation_from_heading("Works for Piano - Four Hands") == "piano four hands"
        assert instrumentation_from_heading("Works for Two Pianos") == "two pianos"

    def test_ambiguous_heading_names_nothing(self):
        assert instrumentation_from_heading("Works for Piano - Four Hands or Two Pianos") is None

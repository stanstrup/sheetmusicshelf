"""The Sheet Music Archive adapter: everything is inferred from the path."""

from __future__ import annotations

import pytest

from sms.ingest.adapters.sheetmusicarchive import (
    build_title,
    form_from_folder,
    form_from_stem,
    numbers_from_stem,
    title_from_wordlike_stem,
    uses_opus,
)


class TestForm:
    @pytest.mark.parametrize(
        "folder,expected",
        [("etudes", "Etude"), ("sww", "Song Without Words"), ("wtc", "Prelude and Fugue"),
         ("hunrhap", "Hungarian Rhapsody"), ("invents", "Invention"), ("misc", None)],
    )
    def test_from_folder(self, folder, expected):
        assert form_from_folder(folder) == expected

    @pytest.mark.parametrize(
        "stem,expected",
        [("et10_1", "Etude"), ("maz17_1", "Mazurka"), ("btsn101", "Sonata"),
         ("lz_hr12", "Hungarian Rhapsody"), ("b2part10", "Invention"),
         ("pre&fug8", "Prelude and Fugue"), ("cadiz", None)],
    )
    def test_from_filename_prefix(self, stem, expected):
        # The prefix is an independent reading of the form; where it agrees
        # with the folder, the piece is worth more than a single guess.
        assert form_from_stem(stem) == expected


class TestNumbers:
    @pytest.mark.parametrize(
        "stem,opus,number",
        [("et10_1", 10, 1), ("sww30_3", 30, 3), ("btsn10_1", 10, 1),
         ("btsn101", None, 101), ("lz_hr12", None, 12), ("cadiz", None, None)],
    )
    def test_parsed(self, stem, opus, number):
        assert numbers_from_stem(stem) == (opus, number)

    def test_prefix_digits_are_not_the_number(self):
        # "b2part10" is Invention no. 10, not no. 2.
        assert numbers_from_stem("b2part10") == (None, 10)


class TestTitles:
    def test_opus_composer(self):
        assert build_title("Etude", 10, 1, "Frederic Chopin") == "Etude, Op. 10 no. 1"

    def test_composer_who_does_not_use_opus_numbers(self):
        # Regression: this read as "Prelude, Op. 1 no. 5". Debussy's Preludes
        # are Book 1 no. 5, and he has no meaningful opus numbering.
        assert build_title("Prelude", 1, 5, "Claude Debussy") == "Prelude, Book 1 no. 5"
        assert not uses_opus("Johann Sebastian Bach")

    def test_sequence_forms_number_plainly(self):
        assert build_title("Hungarian Rhapsody", None, 12, "Franz Liszt") == "Hungarian Rhapsody, no. 12"

    def test_lone_number_is_an_opus_for_opus_composers(self):
        assert build_title("Sonata", None, 101, "Ludwig van Beethoven") == "Sonata, Op. 101"

    def test_no_form_no_title(self):
        assert build_title(None, 10, 1, "Frederic Chopin") is None


class TestWordlikeStems:
    @pytest.mark.parametrize("stem,expected", [
        ("leyenda", "Leyenda"),
        ("auldlang", "Auldlang"),
        ("curranda", "Curranda"),
    ])
    def test_a_named_file_supplies_its_own_title(self, stem, expected):
        # Files sitting directly under a composer folder have no form folder,
        # but they are named for the piece.
        assert title_from_wordlike_stem(stem) == expected

    @pytest.mark.parametrize("stem", ["et10_1", "btsn101", "b2part10", "ab", "index", "bsnindex"])
    def test_codes_and_structure_are_not_titles(self, stem):
        assert title_from_wordlike_stem(stem) is None

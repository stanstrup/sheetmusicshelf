"""The flat lead-sheet folder: the filename is the only signal."""

from __future__ import annotations

import pytest

from sms.music import composers
from sms.ingest.adapters.popcollection import (
    parse_stem,
    respace,
    smart_title,
    strip_index,
    strip_version,
    tidy,
)


class TestStripping:
    def test_index_is_removed_and_kept(self):
        assert strip_index("1002 - Beauty and the Beast") == ("Beauty and the Beast", 1002)

    def test_unnumbered_files_survive(self):
        assert strip_index("16 going on 17") == ("16 going on 17", None)

    @pytest.mark.parametrize("stem", ["The Way We Were (v2)", "TotalEclipse v2", "Something (version 2)"])
    def test_both_version_spellings(self, stem):
        # This folder marks versions as "(v2)" and as a bare trailing "v2".
        cleaned, version = strip_version(stem)
        assert version == 2 and "v2" not in cleaned.lower()


class TestTidy:
    def test_all_lowercase_is_recased(self):
        assert tidy("elton_john") == "Elton John"

    def test_shouting_is_recased(self):
        assert tidy("TOTAL ECLIPSE OF THE HEART") == "Total Eclipse of the Heart"

    def test_deliberate_casing_is_left_alone(self):
        assert tidy("The Way We Were") == "The Way We Were"

    def test_run_together_words_are_split(self):
        assert respace("TotalEclipseOfTheHeart") == "Total Eclipse Of The Heart"

    def test_short_or_spaced_text_is_untouched(self):
        assert respace("Michelle") == "Michelle"
        assert respace("Fly Me to the Moon") == "Fly Me to the Moon"

    def test_minor_words_stay_lowercase_inside_a_title(self):
        assert smart_title("time of my life") == "Time of My Life"

    def test_initialisms_survive(self):
        assert smart_title("ABBA") == "ABBA"


class TestParse:
    def test_artist_first(self):
        parsed = parse_stem("477 - The Beatles - Michelle")
        assert parsed["artist"] == "The Beatles"
        assert parsed["title"] == "Michelle"
        assert parsed["index"] == 477

    def test_ordering_is_flagged_as_a_guess(self):
        # "537 - Fernando - Abba" is Fernando *by* Abba: the same shape as
        # "The Beatles - Michelle" but the opposite meaning, and nothing in the
        # file distinguishes them. Saying so beats guessing silently.
        assert parse_stem("537 - Fernando - Abba")["ambiguous"] is True

    def test_a_known_composer_in_the_second_half_settles_the_order(self):
        # parse_stem stays purely syntactic; the adapter canonicalises the name
        # afterwards. What matters here is that the halves came back swapped.
        parsed = parse_stem("900 - Fur Elise - Beethoven")
        assert parsed["artist"] == "Beethoven"
        assert parsed["title"] == "Fur Elise"
        assert parsed["ambiguous"] is False
        assert composers.resolve(parsed["artist"]).canonical == "Ludwig van Beethoven"

    def test_title_only_files_have_no_artist(self):
        parsed = parse_stem("100 - all my life")
        assert parsed["artist"] is None
        assert parsed["title"] == "All My Life"

    def test_version_and_index_are_stripped_from_the_title(self):
        parsed = parse_stem("1001 - Barbara Streisand - The Way We Were (v2)")
        assert parsed["title"] == "The Way We Were"
        assert parsed["version"] == 2

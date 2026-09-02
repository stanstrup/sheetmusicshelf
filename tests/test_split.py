"""Boundary normalisation for the page-range editor."""

from __future__ import annotations

from sms.ingest.split import Boundary, normalise


def pages(boundaries: list[Boundary]) -> list[int]:
    return [b.page_start for b in boundaries]


class TestNormalise:
    def test_sorted_and_deduplicated(self):
        result = normalise([Boundary(9), Boundary(3), Boundary(9), Boundary(1)], 20)
        assert pages(result) == [1, 3, 9]

    def test_a_file_always_starts_a_piece_on_page_one(self):
        # Without this the opening pages belong to no piece and become
        # unreachable in the reader.
        assert pages(normalise([Boundary(5)], 20)) == [1, 5]

    def test_out_of_range_pages_are_dropped(self):
        assert pages(normalise([Boundary(1), Boundary(0), Boundary(99)], 10)) == [1]

    def test_a_later_entry_wins_so_an_edited_title_sticks(self):
        result = normalise([Boundary(4, "old"), Boundary(4, "new")], 10)
        assert [b.title for b in result if b.page_start == 4] == ["new"]

    def test_titles_are_trimmed(self):
        result = normalise([Boundary(1, "  Prelude  ")], 5)
        assert result[0].title == "Prelude"

    def test_a_file_reporting_no_pages_still_yields_one_piece(self):
        assert pages(normalise([], 0)) == [1]

"""Managed-tree naming.

The rule the layout turns on: a file holding several distinct pieces is a book
and cannot be filed under one work, so books keep their original path under
``_Books/<collection>/`` while single-work files are filed by composer.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sms.library import BOOKS_DIR, UNFILED, is_book, safe_component, target_path

MANAGED = Path("/library/managed")


def piece(**kwargs):
    base = {
        "composer_name": "Wolfgang Amadeus Mozart",
        "title": "Sonata No. 5 in G Major",
        "catalog_display": "K. 283",
        "edition": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def file_row(rel_path="works/k0283.pdf", size=1000):
    return SimpleNamespace(rel_path=rel_path, size=size)


COLLECTION = SimpleNamespace(name="CD Sheet Music: Mozart Complete Piano")


class TestSafeComponent:
    def test_strips_characters_smb_will_not_take(self):
        assert "/" not in safe_component("Prelude / Fugue")
        assert ":" not in safe_component("Op. 27: No. 2")

    def test_refuses_windows_device_names(self):
        assert safe_component("CON") != "CON"
        assert safe_component("aux.pdf").startswith("_")

    def test_drops_trailing_dots_and_spaces(self):
        assert not safe_component("Andante...").endswith(".")

    def test_empty_falls_back(self):
        assert safe_component("", "Unknown") == "Unknown"
        assert safe_component("///") == "Unknown"

    def test_long_names_are_truncated_not_rejected(self):
        assert len(safe_component("x" * 400)) <= 110


class TestTargetPath:
    def test_single_work_files_by_composer_and_work(self):
        path = target_path(MANAGED, COLLECTION, file_row(), [piece()])
        assert path.parent.parent.name == "Wolfgang Amadeus Mozart"
        assert path.parent.name == "Sonata No. 5 in G Major (K. 283)"
        assert path.suffix == ".pdf"

    def test_a_book_keeps_its_original_path(self):
        # A 378-page volume holding sixty pieces cannot sit in one work's
        # folder without lying about what it is.
        rows = file_row("works/mcln.pdf")
        pieces = [piece(title=f"Piece {n}") for n in range(3)]
        assert is_book(rows, pieces)
        path = target_path(MANAGED, COLLECTION, rows, pieces)
        assert BOOKS_DIR in path.parts
        assert path.name == "mcln.pdf"

    def test_unidentified_pieces_are_parked_not_invented(self):
        path = target_path(MANAGED, COLLECTION, file_row(), [piece(title=None)])
        assert UNFILED in path.parts

    def test_no_pieces_at_all_is_still_safe(self):
        path = target_path(MANAGED, COLLECTION, file_row(), [])
        assert UNFILED in path.parts

    def test_work_without_a_catalogue_number_omits_the_parenthesis(self):
        path = target_path(MANAGED, COLLECTION, file_row(), [piece(catalog_display=None)])
        assert path.parent.name == "Sonata No. 5 in G Major"

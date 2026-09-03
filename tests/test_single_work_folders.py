"""A work split over several files is one work, not one per file.

grieg/con_amin holds a concerto in three movements; chopin/preludes holds
twenty-four separate preludes.  Both are one filename stem plus a number, so
the folder is what tells them apart.
"""

from __future__ import annotations

import pytest

from sms.ingest.adapters.sheetmusicarchive import (
    SINGLE_WORK_FOLDERS,
    movement_from_stem,
    single_work_title,
)


class TestWhichFoldersHoldOneWork:
    @pytest.mark.parametrize("folder,stem,form,expected", [
        ("con_amin", "conamin1", "Concerto", "Concerto in A minor"),
        ("chilcor", "chilcor4", "Children's Corner", "Children's Corner"),
        ("gb_vars", "gb_var17", "Goldberg Variations", "Goldberg Variations"),
        ("pc_26", "mzk537a", "Piano Concerto", "Piano Concerto no. 26"),
    ])
    def test_a_named_folder_gives_every_file_one_title(self, folder, stem, form, expected):
        assert single_work_title(folder, stem, form) == expected

    @pytest.mark.parametrize("folder,stem,form", [
        ("preludes", "pre28_1", "Prelude"),      # 24 separate preludes
        ("ballades", "ballad1", "Ballade"),      # 4 separate ballades
        ("lyricpcs", "lp12_1", "Lyric Piece"),   # 66 separate pieces
        ("sonatas", "btsn10_1", "Sonata"),
        ("partitas", "partita3", "Partita"),
    ])
    def test_a_collection_folder_is_left_alone(self, folder, stem, form):
        assert single_work_title(folder, stem, form) is None

    def test_a_work_with_no_folder_of_its_own_is_named_by_its_stem(self):
        # franck/prchofg{1,2,3}.pdf sits straight in the composer folder.
        assert single_work_title("", "prchofg2", None) == "Prelude, Choral and Fugue"

    def test_the_form_name_stands_in_where_it_already_says_it(self):
        assert SINGLE_WORK_FOLDERS["kreis"] == ""
        assert single_work_title("kreis", "schm16_3", "Kreisleriana") == "Kreisleriana"


class TestMovementNumbers:
    @pytest.mark.parametrize("stem,expected", [
        ("conamin2", 2),
        ("gb_var27", 27),
        ("jsbc01", 1),
        ("schm16_8", 8),
    ])
    def test_a_stem_ending_in_digits_gives_the_movement(self, stem, expected):
        assert movement_from_stem(stem) == expected

    @pytest.mark.parametrize("stem", ["lispc1_a", "lzpc2_e", "mzk537a", "mzmf_ovt"])
    def test_a_stem_ending_in_a_letter_gives_none(self, stem):
        # Liszt's "lispc1_a" ends in a letter and its 1 is the concerto, not
        # the movement.  Reading it as one numbered every movement 1.
        assert movement_from_stem(stem) is None

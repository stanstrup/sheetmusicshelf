"""Filing, re-filing and emptying the drop folder.

The library holds the files, so a folder name made from metadata has to follow
the metadata when review changes it -- and a folder is only safe to empty once
the library really holds what was in it.
"""

from __future__ import annotations

from pathlib import Path

from sms.library import _prune_empty, _refile, _unique


class TestRefiling:
    def test_the_file_moves_and_the_old_folder_goes(self, tmp_path: Path):
        root = tmp_path / "library"
        old = root / "Frederic Chopin" / "conamin1" / "conamin1.pdf"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"%PDF-1.4 fake")
        new = root / "Edvard Grieg" / "Concerto in A minor" / "conamin1.pdf"

        _refile(old, new, root)

        assert new.read_bytes() == b"%PDF-1.4 fake"
        assert not old.exists()
        # A composer folder left behind after their last piece moved out reads
        # as a piece still being there.
        assert not (root / "Frederic Chopin").exists()

    def test_a_folder_with_something_left_in_it_stays(self, tmp_path: Path):
        root = tmp_path / "library"
        keep = root / "Frederic Chopin" / "Ballade" / "ballad1.pdf"
        keep.parent.mkdir(parents=True)
        keep.write_bytes(b"one")
        move = root / "Frederic Chopin" / "Nocturne" / "noct1.pdf"
        move.parent.mkdir(parents=True)
        move.write_bytes(b"two")

        _refile(move, root / "Edvard Grieg" / "Nocturne" / "noct1.pdf", root)

        assert keep.exists()
        assert (root / "Frederic Chopin").exists()
        assert not (root / "Frederic Chopin" / "Nocturne").exists()

    def test_a_name_already_taken_does_not_overwrite(self, tmp_path: Path):
        root = tmp_path / "library"
        target = root / "Composer" / "Work" / "file.pdf"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"the one already there")
        old = root / "Elsewhere" / "file.pdf"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"the one being moved")

        _refile(old, target, root)

        assert target.read_bytes() == b"the one already there"
        assert _unique(target).name != target.name


class TestPruning:
    def test_the_library_root_itself_is_never_removed(self, tmp_path: Path):
        root = tmp_path / "library"
        deep = root / "Composer" / "Work"
        deep.mkdir(parents=True)

        _prune_empty(deep, root)

        assert root.exists()
        assert not (root / "Composer").exists()

    def test_a_folder_outside_the_root_is_left_alone(self, tmp_path: Path):
        root = tmp_path / "library"
        root.mkdir()
        outside = tmp_path / "somewhere else"
        outside.mkdir()

        _prune_empty(outside, root)

        assert outside.exists()

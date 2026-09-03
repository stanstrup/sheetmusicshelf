"""Filing, re-filing and emptying the drop folder.

The library holds the files, so a folder name made from metadata has to follow
the metadata when review changes it -- and a folder is only safe to empty once
the library really holds what was in it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sms.library import _prune_empty, _refile, _unique, resolve_source


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


class TestRefileReportsWhereItWent:
    def test_it_returns_the_destination_it_used(self, tmp_path: Path):
        """The caller records the return value, not the target it asked for.

        `materialise` used to record the intended path. When the name was
        already taken the file went somewhere else, so the row pointed at
        another piece's PDF -- and renders are cached under the row's own
        hash, so the wrong pages would have been served from cache for good.
        """
        root = tmp_path / "library"
        taken = root / "Composer" / "Work" / "file.pdf"
        taken.parent.mkdir(parents=True)
        taken.write_bytes(b"already here")
        moving = root / "Elsewhere" / "file.pdf"
        moving.parent.mkdir(parents=True)
        moving.write_bytes(b"the one being moved")

        landed = _refile(moving, taken, root)

        assert landed != taken
        assert landed.exists()
        assert landed.read_bytes() == b"the one being moved"
        assert taken.read_bytes() == b"already here"

    def test_it_returns_the_target_when_the_name_is_free(self, tmp_path: Path):
        root = tmp_path / "library"
        target = root / "Composer" / "Work" / "file.pdf"
        moving = root / "Elsewhere" / "file.pdf"
        moving.parent.mkdir(parents=True)
        moving.write_bytes(b"content")

        assert _refile(moving, target, root) == target


class TestResolvingAFile:
    def test_the_library_copy_wins_when_it_exists(self, tmp_path: Path):
        managed = tmp_path / "library" / "Composer" / "Work" / "file.pdf"
        managed.parent.mkdir(parents=True)
        managed.write_bytes(b"%PDF")
        row = SimpleNamespace(
            managed_path=str(managed), rel_path="folder/file.pdf",
            collection=SimpleNamespace(source_path=str(tmp_path / "source")),
        )
        assert resolve_source(row) == managed

    def test_it_falls_back_to_the_original(self, tmp_path: Path):
        row = SimpleNamespace(
            managed_path=None, rel_path="folder/file.pdf",
            collection=SimpleNamespace(source_path=str(tmp_path / "source")),
        )
        assert resolve_source(row) == tmp_path / "source" / "folder" / "file.pdf"

    def test_a_recorded_copy_that_is_gone_falls_back(self, tmp_path: Path):
        # A library copy removed underneath the catalogue must not 404 the
        # piece while the original is still there to read.
        row = SimpleNamespace(
            managed_path=str(tmp_path / "library" / "vanished.pdf"),
            rel_path="folder/file.pdf",
            collection=SimpleNamespace(source_path=str(tmp_path / "source")),
        )
        assert resolve_source(row) == tmp_path / "source" / "folder" / "file.pdf"

"""A check nobody has seen fail is not yet a check."""

from __future__ import annotations

from pathlib import Path

from sms.models import Piece, RemovedRange, SourceFile, Work
from sms.verify import verify


def _checks(report) -> set[str]:
    return {problem.check for problem in report.problems}


class TestACleanCatalogue:
    def test_nothing_to_report(self, session, collection, source_file, tmp_path):
        real = tmp_path / "b.pdf"
        real.write_bytes(b"%PDF")
        source_file.managed_path = str(real)
        session.add(Piece(source_file_id=source_file.id, page_start=1, page_end=4))
        session.flush()

        assert verify(session).ok


class TestDrift:
    def test_a_library_copy_that_is_gone(self, session, collection, source_file, tmp_path):
        """The exact shape of the failure that has already happened: a run
        rolled the rows back and left the deletions done."""
        source_file.managed_path = str(tmp_path / "never-written.pdf")
        session.flush()

        assert "library copy missing" in _checks(verify(session))

    def test_two_rows_sharing_one_file(self, session, collection, source_file, tmp_path):
        real = tmp_path / "shared.pdf"
        real.write_bytes(b"%PDF")
        source_file.managed_path = str(real)
        session.add(SourceFile(
            collection_id=collection.id, rel_path="other.pdf",
            page_count=4, size=9, managed_path=str(real),
        ))
        session.flush()

        assert "one file, several rows" in _checks(verify(session))

    def test_a_file_with_neither_copy_nor_original(self, session, collection, source_file):
        # managed_path is already None and source_path points nowhere real.
        assert "file unreadable" in _checks(verify(session))

    def test_a_page_range_past_the_end_of_the_file(self, session, collection, source_file, tmp_path):
        real = tmp_path / "b.pdf"
        real.write_bytes(b"%PDF")
        source_file.managed_path = str(real)
        session.add(Piece(source_file_id=source_file.id, page_start=1, page_end=99))
        session.flush()

        assert "page range past the end" in _checks(verify(session))

    def test_a_work_nothing_points_at(self, session, collection, source_file, tmp_path):
        from sms.models import Composer

        real = tmp_path / "b.pdf"
        real.write_bytes(b"%PDF")
        source_file.managed_path = str(real)
        composer = Composer(canonical_name="Nobody", sort_name="Nobody")
        session.add(composer)
        session.flush()
        session.add(Work(composer_id=composer.id, title="Unreferenced"))
        session.flush()

        assert "works with no pieces" in _checks(verify(session))

    def test_an_entry_that_came_back_from_the_dead(self, session, collection, source_file, tmp_path):
        real = tmp_path / "b.pdf"
        real.write_bytes(b"%PDF")
        source_file.managed_path = str(real)
        session.add(Piece(source_file_id=source_file.id, page_start=1, page_end=4))
        session.add(RemovedRange(source_file_id=source_file.id, page_start=1,
                                 page_end=4, reason="deleted by hand"))
        session.flush()

        assert "deleted entry is back" in _checks(verify(session))


class TestTheReportIsUsable:
    def test_every_problem_says_what_to_do(self, session, collection, source_file, tmp_path):
        source_file.managed_path = str(tmp_path / "gone.pdf")
        session.flush()
        report = verify(session)

        assert report.problems
        assert all(p.remedy or "more" in p.detail for p in report.problems)

    def test_it_says_how_much_it_looked_at(self, session, collection, source_file):
        assert "pieces" in verify(session).checked

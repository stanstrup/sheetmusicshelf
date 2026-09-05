"""Check that the catalogue and the disk still agree.

Two kinds of drift. The catalogue against the disk, and the catalogue against
itself.

Filing moves files, materialise copies them, and neither is covered by the
database transaction: a failure part-way through rolls the rows back and
leaves the completed moves done.  That has already happened once in anger --
a file lock stopped a retirement pass nine files in, the transaction rolled
back, and nine library copies stayed deleted while their rows came back.

So drift is a thing that happens here, and the only way to find out is to
look.  Every check answers one question, reports rows rather than fixing
them, and says how to fix what it finds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import FieldCandidate, Piece, RemovedRange, SourceFile, Work


@dataclass
class Problem:
    """One thing that is wrong, and what to do about it."""

    check: str
    detail: str
    remedy: str


@dataclass
class Report:
    problems: list[Problem] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems

    def add(self, check: str, detail: str, remedy: str) -> None:
        self.problems.append(Problem(check, detail, remedy))


def verify(session: Session, *, limit: int = 50) -> Report:
    """Look for the ways the catalogue and the disk drift apart."""
    report = Report()

    # --- a library copy the catalogue believes in, that is not there --------
    filed = list(session.scalars(select(SourceFile).where(SourceFile.managed_path.is_not(None))))
    report.checked["files with a library copy"] = len(filed)
    missing = [row for row in filed if not Path(row.managed_path).exists()]
    for row in missing[:limit]:
        report.add(
            "library copy missing",
            f"file {row.id} ({row.rel_path}) -> {row.managed_path}",
            "sms collection materialise <id> --apply",
        )
    if len(missing) > limit:
        report.add("library copy missing", f"... and {len(missing) - limit} more", "")

    # --- two rows pointing at one file -------------------------------------
    shared = session.execute(
        select(SourceFile.managed_path, func.count())
        .where(SourceFile.managed_path.is_not(None))
        .group_by(SourceFile.managed_path)
        .having(func.count() > 1)
    ).all()
    for path, count in shared[:limit]:
        report.add(
            "one file, several rows",
            f"{count} rows share {path}",
            "clear managed_path on all but one, then materialise",
        )

    # --- neither a library copy nor a readable original --------------------
    unreadable = []
    for row in session.scalars(select(SourceFile).where(SourceFile.managed_path.is_(None))):
        original = Path(row.collection.source_path) / row.rel_path
        if not original.exists():
            unreadable.append(row)
    report.checked["files with no library copy"] = len(unreadable)
    for row in unreadable[:limit]:
        report.add(
            "file unreadable",
            f"file {row.id} ({row.rel_path}) is in no collection directory and has no copy",
            "restore the original, or delete the catalogue entry",
        )

    # --- pieces whose page range escapes the file --------------------------
    overrun = list(session.scalars(
        select(Piece).join(SourceFile).where(
            SourceFile.page_count > 0, Piece.page_end > SourceFile.page_count
        )
    ))
    for piece in overrun[:limit]:
        report.add(
            "page range past the end",
            f"piece {piece.id} covers {piece.page_start}-{piece.page_end} "
            f"of a {piece.source_file.page_count}-page file",
            f"fix the range at /split/{piece.source_file_id}",
        )

    # --- a work nothing points at any more ---------------------------------
    orphans = session.execute(
        select(func.count()).select_from(Work).where(
            ~Work.id.in_(select(Piece.work_id).where(Piece.work_id.is_not(None)))
        )
    ).scalar() or 0
    report.checked["works"] = session.scalar(select(func.count()).select_from(Work)) or 0
    if orphans:
        report.add(
            "works with no pieces",
            f"{orphans} works nothing points at",
            "sms work link --relink, then delete what is still empty",
        )

    # --- a piece that was deleted and came back ----------------------------
    resurrected = list(session.scalars(
        select(Piece).join(
            RemovedRange,
            (RemovedRange.source_file_id == Piece.source_file_id)
            & (RemovedRange.page_start == Piece.page_start),
        )
    ))
    for piece in resurrected[:limit]:
        report.add(
            "deleted entry is back",
            f"piece {piece.id} sits on a page range someone removed",
            "delete it again; the tombstone should have stopped this",
        )

    # --- the promise the whole design rests on ----------------------------
    # Everything above checks the catalogue against the disk. This checks the
    # catalogue against itself: an accepted candidate is a decision, and the
    # row is supposed to show it. Nothing else asserts that against a live
    # database, and it is the shape every write-side bug so far has taken --
    # a value recorded and a derived column left stale.
    from .ingest.persist import DENORMALISED

    mismatched = 0
    for row in session.scalars(
        select(FieldCandidate).where(FieldCandidate.accepted.is_(True))
    ):
        column = DENORMALISED.get(row.field)
        if column is None:
            continue
        shown = getattr(row.piece, column, None)
        if shown is None or str(shown) != row.value:
            mismatched += 1
            if mismatched <= limit:
                report.add(
                    "decision not showing",
                    f"piece {row.piece_id} {row.field}: decided {row.value!r}, "
                    f"row shows {shown!r}",
                    "sms collection recompute",
                )
    report.checked["decisions"] = session.scalar(
        select(func.count()).select_from(FieldCandidate).where(FieldCandidate.accepted.is_(True))
    ) or 0

    report.checked["pieces"] = session.scalar(select(func.count()).select_from(Piece)) or 0
    return report

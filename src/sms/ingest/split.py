"""Splitting a multi-piece file into pieces by page range.

Most of this library needs no splitting -- 87% of files are short enough to be
a single piece.  The ~470 that are books do, and where a PDF carries an outline
the ingester has already done it.  This is the authoritative fallback for the
rest, and the only place a person defines boundaries by hand.

The rule that governs it: **a boundary a person has reviewed is not thrown
away.**  Re-splitting a file rewrites its pending pieces freely, but a piece
that was accepted or rejected is kept and reported, never silently deleted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FieldCandidate, Piece, SourceFile


@dataclass
class Boundary:
    """Where a piece starts, and what it is called."""

    page_start: int
    title: str = ""


@dataclass
class SplitResult:
    created: int = 0
    updated: int = 0
    removed: int = 0
    kept_reviewed: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def pieces(self) -> int:
        return self.created + self.updated


def normalise(boundaries: list[Boundary], page_count: int) -> list[Boundary]:
    """Clean a submitted boundary list: sorted, in range, deduplicated.

    A file always starts a piece at page 1 -- otherwise its opening pages would
    belong to nothing and become unreachable in the reader.
    """
    seen: dict[int, Boundary] = {}
    for boundary in boundaries:
        page = int(boundary.page_start)
        if 1 <= page <= max(page_count, 1):
            # A later entry for the same page wins, so an edited title sticks.
            seen[page] = Boundary(page, (boundary.title or "").strip())
    if 1 not in seen:
        seen[1] = Boundary(1, "")
    return [seen[page] for page in sorted(seen)]


def current_boundaries(session: Session, file_row: SourceFile) -> list[Boundary]:
    pieces = session.scalars(
        select(Piece).where(Piece.source_file_id == file_row.id).order_by(Piece.page_start)
    )
    return [Boundary(piece.page_start, piece.title or "") for piece in pieces]


#: Facts that belong to the whole file rather than to one piece inside it.
#: A new piece carved out of a Bach organ volume is still by Bach and still for
#: organ; only its title, catalogue number and key are its own.
FILE_LEVEL_FIELDS = ("composer", "instrumentation", "form", "arranger", "publisher")


def _inherit_file_level(session: Session, file_row: SourceFile, piece: Piece) -> None:
    """Give a piece the file-level candidates its siblings hold.

    Only fields the piece knows nothing about are filled: a piece that already
    has an opinion about its composer keeps it, right or wrong, for the scorer
    to weigh as usual.
    """
    from .persist import add_candidate

    known = set(
        session.scalars(
            select(FieldCandidate.field).where(FieldCandidate.piece_id == piece.id)
        )
    )
    missing = [field for field in FILE_LEVEL_FIELDS if field not in known]
    if not missing:
        return

    siblings = session.scalars(
        select(Piece).where(Piece.source_file_id == file_row.id, Piece.id != piece.id)
    )
    seen: set[tuple[str, str]] = set()
    for sibling in siblings:
        for candidate in session.scalars(
            select(FieldCandidate).where(
                FieldCandidate.piece_id == sibling.id,
                FieldCandidate.field.in_(missing),
            )
        ):
            key = (candidate.field, candidate.value)
            if key in seen:
                continue
            seen.add(key)
            # Copied as a proposal, never as a decision: inheriting somebody
            # else's accepted value would put words in the reviewer's mouth.
            add_candidate(
                session, piece, candidate.field, candidate.value,
                candidate.source, candidate.weight,
                note="inherited from another piece in the same file",
            )


def apply_splits(
    session: Session,
    file_row: SourceFile,
    boundaries: list[Boundary],
    *,
    user_id: int | None = None,
) -> SplitResult:
    """Rewrite a file's pieces to match ``boundaries``."""
    from .persist import accept_value, recompute, was_removed

    result = SplitResult()
    page_count = max(file_row.page_count, 1)
    wanted = normalise(boundaries, page_count)
    collection = file_row.collection

    existing = {
        piece.page_start: piece
        for piece in session.scalars(
            select(Piece).where(Piece.source_file_id == file_row.id)
        )
    }
    wanted_starts = {boundary.page_start for boundary in wanted}

    # Drop pieces whose boundary is gone -- but never one a person has ruled on.
    for start, piece in list(existing.items()):
        if start in wanted_starts:
            continue
        if piece.review_state != "pending":
            result.kept_reviewed.append(piece.id)
            continue
        has_decision = session.scalar(
            select(FieldCandidate.id).where(
                FieldCandidate.piece_id == piece.id, FieldCandidate.accepted.is_(True)
            ).limit(1)
        )
        if has_decision:
            result.kept_reviewed.append(piece.id)
            continue
        session.delete(piece)
        existing.pop(start)
        result.removed += 1

    for index, boundary in enumerate(wanted):
        page_end = (
            wanted[index + 1].page_start - 1 if index + 1 < len(wanted) else page_count
        )
        page_end = max(page_end, boundary.page_start)

        piece = existing.get(boundary.page_start)
        if piece is None and was_removed(session, file_row, boundary.page_start):
            continue                   # deleted by hand; stays deleted
        if piece is None:
            piece = Piece(
                source_file_id=file_row.id,
                page_start=boundary.page_start,
                page_end=page_end,
                pages_confirmed=True,
            )
            session.add(piece)
            session.flush()
            result.created += 1
        else:
            piece.page_end = page_end
            piece.pages_confirmed = True
            result.updated += 1
        _inherit_file_level(session, file_row, piece)

        if boundary.title:
            # A title typed here is a decision, ranking above any machine guess.
            accept_value(
                session, piece, "title", boundary.title,
                user_id=user_id, source="human:page-range editor",
            )
        recompute(
            session, piece,
            auto_accept=collection.auto_accept,
            review_floor=collection.review_floor,
        )

    session.flush()
    return result

"""Annotations: a layer over a page, never a change to the PDF.

Two decisions carry this whole feature.

**Coordinates are normalised to 0..1** of the page box, not pixels.  The same
page is rendered at 320, 800, 1200 and 1800 pixels wide depending on the device
asking, and a phone rotated to landscape asks for a different one again.  Ink
stored in pixels would land in the wrong place on every surface but the one it
was drawn on.

**Originals are never touched.**  Marks live in their own table, keyed to the
*file* and the file's own page number, per user.  Deleting every annotation
leaves the library exactly as it was, and the Android client reads the same
rows the browser wrote.

Keyed to the file rather than the piece because a piece is a claim about where
a work starts, and claims get corrected: the page-range editor deletes pieces
whose boundary has moved, and a cascade from the piece took the ink with it.
Everything else here can be recomputed from the candidates.  A person's pencil
cannot.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal, require
from ..db import get_session
from ..models import Annotation, Piece

router = APIRouter(tags=["annotations"])

#: A page can hold a lot of pencil, but not an unbounded amount: this caps a
#: runaway client before it fills the database with one page of ink.
MAX_STROKES_PER_PAGE = 2000
MAX_POINTS_PER_STROKE = 5000


class Stroke(BaseModel):
    """One continuous mark."""

    tool: str = Field(default="pen", description="pen | highlighter")
    color: str = Field(default="#c0392b", max_length=32)
    #: Width as a fraction of the page width, so a line looks the same
    #: thickness whatever size the page was rendered at.
    width: float = Field(default=0.004, gt=0, le=0.2)
    #: [[x, y], ...] with x and y in 0..1 of the page box.
    points: list[tuple[float, float]]

    @field_validator("points")
    @classmethod
    def _bounded(cls, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not points:
            raise ValueError("a stroke needs at least one point")
        if len(points) > MAX_POINTS_PER_STROKE:
            raise ValueError(f"a stroke may hold at most {MAX_POINTS_PER_STROKE} points")
        # Clamp rather than reject: a finger sliding off the edge of a phone is
        # normal, and losing the whole stroke over it would be maddening.
        return [(min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)) for x, y in points]


class PageLayer(BaseModel):
    page: int = Field(ge=1, description="1-based, within the piece.")
    strokes: list[Stroke] = Field(default_factory=list)
    updated_at: datetime | None = None

    @field_validator("strokes")
    @classmethod
    def _not_too_many(cls, strokes: list[Stroke]) -> list[Stroke]:
        if len(strokes) > MAX_STROKES_PER_PAGE:
            raise ValueError(f"a page may hold at most {MAX_STROKES_PER_PAGE} strokes")
        return strokes


def _require_piece(session: Session, piece_id: int) -> Piece:
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")
    return piece


def _mine(user_id: int | None):
    """Only this reader's own marks.  A shared library is still several people."""
    return Annotation.user_id.is_(None) if user_id is None else Annotation.user_id == user_id


def _file_page(piece: Piece, page: int) -> int:
    """A reader's page number turned into the file's own.

    Readers count from the start of the piece, because that is what is in
    front of them.  Storage counts from the start of the file, because that is
    what does not move when a page range is corrected.
    """
    return piece.page_start + page - 1


def _row(session: Session, piece: Piece, page: int, user_id: int | None) -> Annotation | None:
    return session.scalar(
        select(Annotation).where(
            Annotation.source_file_id == piece.source_file_id,
            Annotation.page == _file_page(piece, page),
            _mine(user_id),
        )
    )


@router.get(
    "/pieces/{piece_id}/annotations",
    response_model=list[PageLayer],
    summary="Every annotated page of a piece",
)
def list_layers(
    piece_id: int,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require("catalog:read")),
) -> list[PageLayer]:
    """Fetched once when the reader opens, so page turns need no round trip.

    Only the pages this piece covers.  Marks made on a neighbouring piece in
    the same file are somebody else's business, even though they sit in the
    same table -- which is the price of keying on the file, and cheaper than
    losing ink every time a boundary moves.
    """
    piece = _require_piece(session, piece_id)
    rows = session.scalars(
        select(Annotation)
        .where(
            Annotation.source_file_id == piece.source_file_id,
            Annotation.page >= piece.page_start,
            Annotation.page <= piece.page_end,
            _mine(principal.user_id),
        )
        .order_by(Annotation.page)
    )
    return [
        PageLayer(
            page=row.page - piece.page_start + 1,
            strokes=(row.data or {}).get("strokes", []),
            updated_at=row.updated_at,
        )
        for row in rows
    ]


@router.put(
    "/pieces/{piece_id}/annotations/{page}",
    response_model=PageLayer,
    summary="Replace one page's marks",
)
def put_layer(
    piece_id: int,
    page: int,
    layer: PageLayer,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require("catalog:write")),
) -> PageLayer:
    """Whole-page replace rather than append.

    The client owns the page while it is open and knows the full stroke list,
    so replacing avoids the ordering and de-duplication problems an append API
    would create when a phone retries a request it is not sure landed.
    """
    piece = _require_piece(session, piece_id)
    if not 1 <= page <= piece.page_count:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"piece has {piece.page_count} page(s); asked for {page}"
        )

    row = _row(session, piece, page, principal.user_id)
    strokes = [stroke.model_dump() for stroke in layer.strokes]

    if not strokes:
        # An empty layer is an erasure, not a row full of nothing.
        if row is not None:
            session.delete(row)
        return PageLayer(page=page, strokes=[])

    if row is None:
        row = Annotation(
            source_file_id=piece.source_file_id,
            page=_file_page(piece, page),
            user_id=principal.user_id,
            data={},
        )
        session.add(row)
    row.data = {"strokes": strokes}
    row.updated_at = datetime.now(timezone.utc)
    session.flush()
    return PageLayer(page=page, strokes=layer.strokes, updated_at=row.updated_at)


@router.delete(
    "/pieces/{piece_id}/annotations/{page}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear one page's marks",
)
def delete_layer(
    piece_id: int,
    page: int,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require("catalog:write")),
) -> None:
    piece = _require_piece(session, piece_id)
    row = _row(session, piece, page, principal.user_id)
    if row is not None:
        session.delete(row)

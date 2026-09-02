"""Shelves, and the personal fields that belong to a player rather than a work.

A shelf is ordered, so it doubles as a setlist: "Christmas 2026" is the same
object as "pieces I am learning", just used differently.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import Principal, require
from ..db import get_session
from ..models import Piece, Shelf, ShelfItem

router = APIRouter(prefix="/shelves", tags=["shelves"])

#: The states a piece moves through in a player's hands.
STATUSES = ("unplayed", "learning", "repertoire", "retired")


class ShelfIn(BaseModel):
    name: str
    description: str | None = None


class ShelfOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    items: int = 0


class PersonalIn(BaseModel):
    """Everything about a piece that is true for you and nobody else."""

    difficulty: int | None = Field(default=None, ge=1, le=10)
    status: str | None = Field(default=None, description=" | ".join(STATUSES))
    rating: int | None = Field(default=None, ge=0, le=5)
    notes: str | None = None
    tags: list[str] | None = None


def _out(session: Session, shelf: Shelf) -> ShelfOut:
    count = session.scalar(
        select(func.count()).select_from(ShelfItem).where(ShelfItem.shelf_id == shelf.id)
    ) or 0
    return ShelfOut(id=shelf.id, name=shelf.name, description=shelf.description, items=count)


@router.get("", response_model=list[ShelfOut])
def list_shelves(
    session: Session = Depends(get_session),
    principal: Principal = Depends(require("catalog:read")),
) -> list[ShelfOut]:
    shelves = session.scalars(select(Shelf).order_by(Shelf.name))
    return [_out(session, shelf) for shelf in shelves]


@router.post("", response_model=ShelfOut, status_code=status.HTTP_201_CREATED)
def create_shelf(
    body: ShelfIn,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require("catalog:write")),
) -> ShelfOut:
    shelf = Shelf(name=body.name.strip(), description=body.description, owner_id=principal.user_id)
    if not shelf.name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "a shelf needs a name")
    session.add(shelf)
    session.flush()
    return _out(session, shelf)


@router.delete("/{shelf_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shelf(
    shelf_id: int,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require("catalog:write")),
) -> None:
    shelf = session.get(Shelf, shelf_id)
    if shelf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such shelf")
    session.delete(shelf)


@router.post("/{shelf_id}/pieces/{piece_id}", response_model=ShelfOut)
def add_piece(
    shelf_id: int,
    piece_id: int,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require("catalog:write")),
) -> ShelfOut:
    """Append a piece.  Adding one twice is a no-op, not an error."""
    shelf = session.get(Shelf, shelf_id)
    piece = session.get(Piece, piece_id)
    if shelf is None or piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such shelf or piece")

    existing = session.scalar(
        select(ShelfItem).where(ShelfItem.shelf_id == shelf_id, ShelfItem.piece_id == piece_id)
    )
    if existing is None:
        last = session.scalar(
            select(func.max(ShelfItem.position)).where(ShelfItem.shelf_id == shelf_id)
        )
        session.add(ShelfItem(shelf_id=shelf_id, piece_id=piece_id, position=(last or 0) + 1))
        session.flush()
    return _out(session, shelf)


@router.delete("/{shelf_id}/pieces/{piece_id}", response_model=ShelfOut)
def remove_piece(
    shelf_id: int,
    piece_id: int,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require("catalog:write")),
) -> ShelfOut:
    shelf = session.get(Shelf, shelf_id)
    if shelf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such shelf")
    item = session.scalar(
        select(ShelfItem).where(ShelfItem.shelf_id == shelf_id, ShelfItem.piece_id == piece_id)
    )
    if item is not None:
        session.delete(item)
        session.flush()
    return _out(session, shelf)


@router.put("/{shelf_id}/order", response_model=ShelfOut)
def reorder(
    shelf_id: int,
    piece_ids: list[int],
    session: Session = Depends(get_session),
    principal: Principal = Depends(require("catalog:write")),
) -> ShelfOut:
    """Set the running order.  Pieces left out keep their place at the end."""
    shelf = session.get(Shelf, shelf_id)
    if shelf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such shelf")

    items = {
        item.piece_id: item
        for item in session.scalars(select(ShelfItem).where(ShelfItem.shelf_id == shelf_id))
    }
    for position, piece_id in enumerate(piece_ids, start=1):
        if piece_id in items:
            items[piece_id].position = position
    for offset, piece_id in enumerate(sorted(set(items) - set(piece_ids)), start=1):
        items[piece_id].position = len(piece_ids) + offset
    session.flush()
    return _out(session, shelf)


personal_router = APIRouter(tags=["personal"])


@personal_router.put("/pieces/{piece_id}/personal", summary="Your own fields on a piece")
def set_personal(
    piece_id: int,
    body: PersonalIn,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require("catalog:write")),
) -> dict:
    """Difficulty, status, rating, notes and tags.

    Kept apart from the catalogue fields: these are opinions, not facts about
    the music, and no re-scan or enrichment ever touches them.
    """
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")

    if body.status is not None and body.status not in STATUSES and body.status != "":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"status must be one of: {', '.join(STATUSES)}"
        )

    if body.difficulty is not None:
        piece.difficulty = body.difficulty
    if body.status is not None:
        piece.status = body.status or None
    if body.rating is not None:
        piece.rating = body.rating
    if body.notes is not None:
        piece.notes = body.notes or None
    if body.tags is not None:
        piece.tags = sorted({t.strip() for t in body.tags if t.strip()})

    return {
        "id": piece.id,
        "difficulty": piece.difficulty,
        "status": piece.status,
        "rating": piece.rating,
        "notes": piece.notes,
        "tags": piece.tags,
    }

"""Composer authority records and their portraits."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import Principal, require
from ..config import get_settings
from ..db import get_session
from ..models import Composer, Piece

router = APIRouter(prefix="/composers", tags=["composers"])


def _payload(session: Session, composer: Composer) -> dict:
    pieces = session.scalar(
        select(func.count()).select_from(Piece)
        .where(Piece.composer_name == composer.canonical_name)
    ) or 0
    return {
        "id": composer.id,
        "name": composer.canonical_name,
        "sort_name": composer.sort_name,
        "born": composer.born,
        "died": composer.died,
        "lifespan": composer.lifespan,
        "period": composer.period,
        "description": composer.description,
        "wikipedia_url": composer.wikipedia_url,
        "wikidata_id": composer.wikidata_id,
        "portrait_url": f"/api/v1/composers/{composer.id}/portrait" if composer.portrait_file else None,
        # Attribution travels with the image, always: most Commons portraits
        # are CC-BY-SA and showing one uncredited is a licence breach.
        "portrait_credit": composer.portrait_credit,
        "portrait_license": composer.portrait_license,
        "portrait_source_url": composer.portrait_source_url,
        "pieces": pieces,
        "enriched": composer.enriched_at is not None,
    }


@router.get("", summary="Composers in the catalogue")
def list_composers(
    session: Session = Depends(get_session),
    _: Principal = Depends(require("catalog:read")),
    period: str | None = Query(None, description="Baroque | Classical | Romantic | ..."),
    q: str | None = None,
) -> list[dict]:
    query = select(Composer).order_by(Composer.sort_name)
    if period:
        query = query.where(Composer.period == period)
    if q:
        query = query.where(Composer.canonical_name.ilike(f"%{q.strip()}%"))
    return [_payload(session, composer) for composer in session.scalars(query)]


@router.get("/{composer_id}", summary="One composer")
def get_composer(
    composer_id: int,
    session: Session = Depends(get_session),
    _: Principal = Depends(require("catalog:read")),
) -> dict:
    composer = session.get(Composer, composer_id)
    if composer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such composer")
    return _payload(session, composer)


@router.get("/{composer_id}/portrait", summary="Cached portrait image")
def portrait(
    composer_id: int,
    session: Session = Depends(get_session),
    _: Principal = Depends(require("catalog:read")),
) -> FileResponse:
    """Served from the local cache, never hot-linked from Commons."""
    composer = session.get(Composer, composer_id)
    if composer is None or not composer.portrait_file:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no portrait for this composer")

    path = Path(get_settings().cache_root) / "portraits" / composer.portrait_file
    if not path.exists():
        raise HTTPException(
            status.HTTP_410_GONE,
            "portrait missing from the cache; use the Refresh from Wikipedia button on the composer page",
        )
    return FileResponse(
        path,
        media_type=mimetypes.guess_type(path.name)[0] or "image/jpeg",
        headers={"Cache-Control": "public, max-age=604800"},
    )

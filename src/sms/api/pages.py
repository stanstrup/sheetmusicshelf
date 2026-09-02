"""Page images.

The reader fetches one image per page rather than a whole PDF, so a six-page
piece inside a 378-page volume costs six small requests instead of a 50 MB
download.  See :mod:`sms.render` for why rendering happens server-side.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..auth import Principal, require
from ..db import get_session
from ..models import Piece, SourceFile
from ..render import THUMB_WIDTH, RenderUnavailable, render_page

router = APIRouter(tags=["pages"])

#: Renders are immutable for a given file, page and width, so the browser and
#: any reverse proxy may keep them indefinitely.
CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


def resolve_source(file_row: SourceFile) -> Path:
    """Prefer the managed copy when one exists, else the original."""
    if file_row.managed_path:
        managed = Path(file_row.managed_path)
        if managed.exists():
            return managed
    return Path(file_row.collection.source_path) / file_row.rel_path


def _cache_key(file_row: SourceFile) -> str:
    """Key renders by content hash so a re-scan of unchanged files reuses them,
    and an edited file gets fresh images automatically."""
    return file_row.sha256 or f"id{file_row.id}"


def _render(file_row: SourceFile, page: int, width: int) -> FileResponse:
    path = resolve_source(file_row)
    try:
        image = render_page(path, page, width=width, key=_cache_key(file_row))
    except FileNotFoundError:
        raise HTTPException(status.HTTP_410_GONE, f"file missing on disk: {file_row.rel_path}")
    except RenderUnavailable as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"cannot render: {exc}")
    return FileResponse(image, media_type="image/webp", headers=CACHE_HEADERS)


@router.get("/pieces/{piece_id}/pages/{page}", summary="One rendered page of a piece")
def piece_page(
    piece_id: int,
    page: int,
    width: int = Query(1200, description="Snapped to the nearest supported width."),
    session: Session = Depends(get_session),
    _: Principal = Depends(require("catalog:read")),
) -> FileResponse:
    """``page`` is 1-based **within the piece**, not within the file.

    The caller works in the piece's own numbering, so a piece at pages 42-57 of
    a book is simply pages 1-16 as far as the reader is concerned.
    """
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")
    if not 1 <= page <= piece.page_count:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"piece has {piece.page_count} page(s); asked for {page}",
        )
    return _render(piece.source_file, piece.page_start + page - 1, width)


@router.get("/pieces/{piece_id}/thumb", summary="Cover thumbnail for a piece")
def piece_thumb(
    piece_id: int,
    session: Session = Depends(get_session),
    _: Principal = Depends(require("catalog:read")),
) -> FileResponse:
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")
    return _render(piece.source_file, piece.page_start, THUMB_WIDTH)


@router.get("/files/{file_id}/pages/{page}", summary="One rendered page of a file")
def file_page(
    file_id: int,
    page: int,
    width: int = Query(320, description="Snapped to the nearest supported width."),
    session: Session = Depends(get_session),
    _: Principal = Depends(require("catalog:read")),
) -> FileResponse:
    """File-relative pages, for the anthology page-range editor.

    Splitting a book means looking at its pages as the *file* numbers them,
    before any piece exists to number them differently.
    """
    file_row = session.get(SourceFile, file_id)
    if file_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such file")
    if not 1 <= page <= max(file_row.page_count, 1):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"file has {file_row.page_count} pages")
    return _render(file_row, page, width)

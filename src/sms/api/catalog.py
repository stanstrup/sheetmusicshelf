"""Browsing, filtering and reading the catalogue."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import String, distinct, func, or_, select
from sqlalchemy.orm import Session

from ..auth import Principal, require
from ..db import get_session
from ..library import resolve_source
from ..catalog_query import Filters, all_facets, base_query, narrow
from ..models import Piece, SourceFile
from ..schemas import PieceOut

router = APIRouter(tags=["catalogue"])

#: How much of a large PDF to send when a client asks for an open-ended range.
RANGE_CHUNK = 1 << 20


@router.get("/pieces", response_model=list[PieceOut], summary="Browse and filter the catalogue")
def list_pieces(
    session: Session = Depends(get_session),
    _: Principal = Depends(require("catalog:read")),
    q: str | None = Query(None, description="Free text over title, composer and catalogue number."),
    composer: str | None = None,
    form: str | None = None,
    instrument: str | None = Query(None, description="What it is scored for, e.g. 'solo piano'."),
    period: str | None = Query(None, description="Baroque | Classical | Romantic | Modern, from the composer."),
    music_key: str | None = None,
    collection_id: int | None = None,
    route: str | None = Query(None, description="accept | review | hold"),
    review_state: str | None = None,
    status_: str | None = Query(None, alias="status", description="unplayed | learning | repertoire | retired"),
    min_difficulty: int | None = Query(None, ge=1, le=10),
    max_difficulty: int | None = Query(None, ge=1, le=10),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    order: str = Query("composer", description="composer | title | confidence | recent"),
) -> list[PieceOut]:
    # Text is matched by substring here because these values are typed, not
    # chosen from a list.  Everything else is the same narrowing the browse
    # page does, from the same place.
    query = narrow(
        base_query(),
        Filters(
            q=q, composer=composer, form=form, instrument=instrument, key=music_key,
            status=status_, route=route, review_state=review_state,
            period=period, collection_id=collection_id,
            min_difficulty=min_difficulty, max_difficulty=max_difficulty,
        ),
        text_match="contains",
    )

    orderings = {
        "composer": (
            Piece.composer_name.asc(),
            Piece.catalog_system.asc().nulls_last(),
            Piece.catalog_number.asc().nulls_last(),
            Piece.catalog_sub.asc().nulls_last(),
            Piece.title.asc(),
        ),
        "title": (Piece.title.asc(),),
        "confidence": (Piece.confidence.asc(),),
        "recent": (Piece.created_at.desc(),),
    }
    query = query.order_by(*orderings.get(order, orderings["composer"]))
    return [PieceOut.model_validate(p) for p in session.scalars(query.limit(limit).offset(offset))]


@router.get("/pieces/{piece_id}", response_model=PieceOut)
def get_piece(
    piece_id: int,
    session: Session = Depends(get_session),
    _: Principal = Depends(require("catalog:read")),
) -> PieceOut:
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")
    return PieceOut.model_validate(piece)


@router.get("/facets", summary="Distinct values available for filtering")
def facets(
    session: Session = Depends(get_session),
    _: Principal = Depends(require("catalog:read")),
    collection_id: int | None = None,
) -> dict:
    """What the filter sidebar offers, computed from what is actually catalogued."""

    return all_facets(session, collection_id)


@router.get(
    "/pieces/{piece_id}/pdf",
    summary="The PDF, with byte-range support",
    response_class=Response,
)
def piece_pdf(
    piece_id: int,
    request: Request,
    session: Session = Depends(get_session),
    _: Principal = Depends(require("catalog:read")),
    range_header: str | None = Header(None, alias="range"),
) -> Response:
    """Serve the file the piece lives in.

    Range requests are honoured because that is what makes the reader usable
    over a VPN: PDF.js fetches the pages it needs instead of pulling a 50 MB
    scan before showing anything.  The piece's ``page_start`` tells the client
    where to open.
    """
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")

    file_row = piece.source_file
    path = resolve_source(file_row)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, f"file missing on disk: {file_row.rel_path}")

    size = path.stat().st_size
    media_type = mimetypes.guess_type(path.name)[0] or "application/pdf"

    if not range_header:
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Accept-Ranges": "bytes", "Content-Length": str(size)},
        )

    match = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not match:
        raise HTTPException(status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE, "malformed Range header")
    start_raw, end_raw = match.groups()
    if start_raw:
        start = int(start_raw)
        end = int(end_raw) if end_raw else min(start + RANGE_CHUNK - 1, size - 1)
    else:
        # "bytes=-500": the last 500 bytes, which is how PDF.js finds the xref.
        length = int(end_raw or 0)
        start = max(size - length, 0)
        end = size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        raise HTTPException(
            status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            "range outside the file",
            headers={"Content-Range": f"bytes */{size}"},
        )

    def stream():
        remaining = end - start + 1
        with path.open("rb") as handle:
            handle.seek(start)
            while remaining > 0:
                chunk = handle.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        stream(),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
        },
    )

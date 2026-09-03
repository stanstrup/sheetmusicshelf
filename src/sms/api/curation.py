"""The curation API.

This is the surface an external tool -- an LLM session, a script, a
spreadsheet round-trip -- works through.  It is deliberately a first-class API
rather than a file export, because the useful loop is *read the uncertain
items, look at what the signals actually said, propose better values, and see
them scored against everything else*.

The contract in one line: **anything you propose is scored like any other
signal, and anything you decide is final.**  Nothing an agent sends can
silently overwrite a person's decision, and nothing a re-scan does can
overwrite either.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import Principal, require
from ..db import get_session
from ..ingest.model import KNOWN_FIELDS, REQUIRED_FIELDS
from ..ingest.persist import accept_value, add_candidate, recompute
from ..models import Collection, FieldCandidate, Piece, SourceFile
from ..pdfsignals import read_signals
from ..schemas import BulkResult, CandidateIn, CandidateOut, DecisionIn, PieceOut, QueueItem

router = APIRouter(prefix="/curation", tags=["curation"])


def _queue_item(session: Session, piece: Piece) -> QueueItem:
    file_row = piece.source_file
    collection = file_row.collection
    candidates = list(
        session.scalars(
            select(FieldCandidate)
            .where(FieldCandidate.piece_id == piece.id)
            .order_by(FieldCandidate.field, FieldCandidate.weight.desc())
        )
    )
    present = {c.field for c in candidates}
    conflicted = [
        part.strip()
        for note in piece.notes_machine or []
        if note.startswith("signals disagree on ")
        for part in note.removeprefix("signals disagree on ").split(",")
    ]
    return QueueItem(
        piece=PieceOut.model_validate(piece),
        collection=collection.name,
        rel_path=file_row.rel_path,
        page_count=file_row.page_count,
        has_text_layer=file_row.has_text_layer,
        candidates=[CandidateOut.model_validate(c) for c in candidates],
        missing_fields=[f for f in REQUIRED_FIELDS if f not in present],
        conflicted_fields=conflicted,
    )


@router.get(
    "/queue",
    response_model=list[QueueItem],
    summary="Uncertain pieces, with the signals behind each guess",
)
def queue(
    session: Session = Depends(get_session),
    _: Principal = Depends(require("curation:read")),
    collection_id: int | None = Query(None, description="Restrict to one collection."),
    route: str | None = Query(None, description="accept | review | hold. Defaults to review and hold."),
    review_state: str = Query("pending", description="pending | accepted | rejected"),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
) -> list[QueueItem]:
    """Work through this, oldest and least confident first.

    Each item carries every candidate value with its source and weight, so a
    curator can see *why* the piece is uncertain before proposing anything.
    """
    query = (
        select(Piece)
        .join(SourceFile, Piece.source_file_id == SourceFile.id)
        .where(Piece.review_state == review_state)
    )
    if collection_id is not None:
        query = query.where(SourceFile.collection_id == collection_id)
    query = query.where(Piece.route.in_([route] if route else ["review", "hold"]))
    query = query.order_by(Piece.confidence.asc(), Piece.id.asc()).limit(limit).offset(offset)
    return [_queue_item(session, piece) for piece in session.scalars(query)]


@router.get("/pieces/{piece_id}", response_model=QueueItem, summary="One piece in full")
def piece_detail(
    piece_id: int,
    session: Session = Depends(get_session),
    _: Principal = Depends(require("curation:read")),
) -> QueueItem:
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")
    return _queue_item(session, piece)


@router.get(
    "/pieces/{piece_id}/text",
    summary="Extracted text from the piece's opening pages",
)
def piece_text(
    piece_id: int,
    pages: int = Query(2, ge=1, le=10, description="How many pages from the start of the piece."),
    session: Session = Depends(get_session),
    _: Principal = Depends(require("curation:read")),
) -> dict:
    """Read the title page, when there is one to read.

    Most of this library is image scans with no text layer, so an empty result
    here is the normal case and not an error -- ``has_text_layer`` on the queue
    item says so in advance.
    """
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")

    file_row = piece.source_file
    path = _absolute_path(file_row)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, f"file missing on disk: {file_row.rel_path}")

    signals = read_signals(path, path.parent, with_hash=False, text_pages=piece.page_start - 1 + pages)
    wanted = range(piece.page_start - 1, min(piece.page_start - 1 + pages, file_row.page_count))
    return {
        "piece_id": piece.id,
        "rel_path": file_row.rel_path,
        "has_text_layer": signals.has_text_layer,
        "docinfo": signals.docinfo,
        "outline": [{"title": e.title, "page": (e.page_index or 0) + 1} for e in signals.outline[:50]],
        "pages": {str(i + 1): signals.page_text.get(i, "") for i in wanted},
    }


def _absolute_path(file_row: SourceFile):
    """Where the file can be read from -- the library copy, or the original.

    Built its own path from the collection's source directory until that
    directory stopped being mounted, at which point this endpoint returned 410
    for every piece in the catalogue while the page renderer, using the same
    rows, worked fine.
    """
    from ..library import resolve_source

    return resolve_source(file_row)


@router.post(
    "/candidates",
    response_model=BulkResult,
    summary="Propose values (scored, not applied)",
)
def propose(
    candidates: list[CandidateIn],
    session: Session = Depends(get_session),
    principal: Principal = Depends(require("curation:write")),
) -> BulkResult:
    """Add proposals to the pool of signals.

    A proposal does not become the value: it is combined with everything else
    already known about the field.  Agreeing with an existing signal raises
    that field's confidence; disagreeing with one flags a conflict and holds
    the piece for a person.  Send a whole batch in one call.
    """
    added, errors = 0, []
    for item in candidates:
        if item.field not in KNOWN_FIELDS:
            errors.append(f"piece {item.piece_id}: unknown field {item.field!r}")
            continue
        piece = session.get(Piece, item.piece_id)
        if piece is None:
            errors.append(f"piece {item.piece_id}: not found")
            continue
        add_candidate(session, piece, item.field, item.value, item.source, item.weight, item.note)
        collection = piece.source_file.collection
        recompute(session, piece, auto_accept=collection.auto_accept, review_floor=collection.review_floor)
        added += 1
    return BulkResult(accepted=added, errors=errors)


@router.post(
    "/decisions",
    response_model=BulkResult,
    summary="Decide values (final)",
)
def decide(
    decisions: list[DecisionIn],
    session: Session = Depends(get_session),
    principal: Principal = Depends(require("curation:write")),
) -> BulkResult:
    """Set a field's value for good.

    A decided value cannot be outvoted by any later signal, and re-running a
    corrected adapter over the collection will not disturb it.  Use this only
    when you are sure; use ``/candidates`` when you are merely confident.
    """
    applied, errors = 0, []
    for item in decisions:
        if item.field not in KNOWN_FIELDS:
            errors.append(f"piece {item.piece_id}: unknown field {item.field!r}")
            continue
        piece = session.get(Piece, item.piece_id)
        if piece is None:
            errors.append(f"piece {item.piece_id}: not found")
            continue
        accept_value(
            session, piece, item.field, item.value,
            user_id=principal.user_id,
            source=f"human:{principal.display_name}",
            note=item.note,
        )
        collection = piece.source_file.collection
        recompute(session, piece, auto_accept=collection.auto_accept, review_floor=collection.review_floor)
        applied += 1
    return BulkResult(accepted=applied, errors=errors)


@router.post("/pieces/{piece_id}/approve", response_model=PieceOut, summary="Mark a piece reviewed")
def approve(
    piece_id: int,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require("curation:write")),
) -> PieceOut:
    """Accept the piece as catalogued and take it out of the queue."""
    piece = _require_piece(session, piece_id)
    from datetime import datetime, timezone

    piece.review_state = "accepted"
    piece.reviewed_by = principal.user_id
    piece.reviewed_at = datetime.now(timezone.utc)
    return PieceOut.model_validate(piece)


@router.post("/pieces/{piece_id}/reject", response_model=PieceOut, summary="Mark a piece not music")
def reject(
    piece_id: int,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require("curation:write")),
) -> PieceOut:
    """Exclude the piece from the catalogue -- a cover page, a licence, a scan
    of something that is not music.  The file itself is untouched."""
    piece = _require_piece(session, piece_id)
    from datetime import datetime, timezone

    piece.review_state = "rejected"
    piece.reviewed_by = principal.user_id
    piece.reviewed_at = datetime.now(timezone.utc)
    return PieceOut.model_validate(piece)


def _require_piece(session: Session, piece_id: int) -> Piece:
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")
    return piece


@router.get("/summary", summary="How much is left to review")
def summary(
    session: Session = Depends(get_session),
    _: Principal = Depends(require("curation:read")),
    collection_id: int | None = None,
) -> dict:
    query = select(Piece.route, Piece.review_state, func.count()).join(
        SourceFile, Piece.source_file_id == SourceFile.id
    )
    if collection_id is not None:
        query = query.where(SourceFile.collection_id == collection_id)
    rows = session.execute(query.group_by(Piece.route, Piece.review_state)).all()

    by_route: dict[str, int] = {}
    by_state: dict[str, int] = {}
    for route_name, state, count in rows:
        by_route[route_name] = by_route.get(route_name, 0) + count
        by_state[state] = by_state.get(state, 0) + count
    return {
        "by_route": by_route,
        "by_review_state": by_state,
        "outstanding": sum(
            count for route_name, state, count in rows
            if state == "pending" and route_name in ("review", "hold")
        ),
    }

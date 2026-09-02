"""Managing collections and the scans that fill them."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import Principal, require
from ..db import get_session
from ..ingest.adapters.base import all_adapters, choose_adapter, get_adapter
from ..ingest.persist import upsert_collection
from ..jobs import enqueue
from ..models import Collection, Job, Piece, SourceFile
from ..schemas import CollectionIn, CollectionOut, CollectionStats, JobOut

router = APIRouter(prefix="/collections", tags=["collections"])


@router.get("", response_model=list[CollectionOut])
def list_collections(
    session: Session = Depends(get_session),
    _: Principal = Depends(require("catalog:read")),
) -> list[CollectionOut]:
    rows = session.scalars(select(Collection).order_by(Collection.name))
    return [CollectionOut.model_validate(row) for row in rows]


@router.post("", response_model=CollectionOut, status_code=status.HTTP_201_CREATED)
def create_collection(
    body: CollectionIn,
    session: Session = Depends(get_session),
    _: Principal = Depends(require("admin")),
) -> CollectionOut:
    """Register a source folder.  Nothing is read until you ask for a scan."""
    root = Path(body.source_path)
    if not root.is_dir():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"not a directory in this container: {root}")

    adapter = get_adapter(body.adapter) if body.adapter else choose_adapter(root)
    collection = upsert_collection(session, root, adapter.name, body.name)
    if body.auto_accept is not None:
        collection.auto_accept = body.auto_accept
    if body.review_floor is not None:
        collection.review_floor = body.review_floor
    session.flush()
    return CollectionOut.model_validate(collection)


@router.get("/adapters", summary="Available collection adapters")
def adapters(_: Principal = Depends(require("catalog:read"))) -> list[dict]:
    return [{"name": a.name, "ignore_globs": list(a.ignore_globs)} for a in all_adapters()]


@router.get("/{collection_id}", response_model=CollectionStats)
def collection_stats(
    collection_id: int,
    session: Session = Depends(get_session),
    _: Principal = Depends(require("catalog:read")),
) -> CollectionStats:
    collection = session.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such collection")

    files = session.scalar(
        select(func.count()).select_from(SourceFile).where(SourceFile.collection_id == collection_id)
    ) or 0
    rows = session.execute(
        select(Piece.route, Piece.review_state, func.count())
        .join(SourceFile, Piece.source_file_id == SourceFile.id)
        .where(SourceFile.collection_id == collection_id)
        .group_by(Piece.route, Piece.review_state)
    ).all()

    by_route: dict[str, int] = {}
    by_state: dict[str, int] = {}
    for route_name, state, count in rows:
        by_route[route_name] = by_route.get(route_name, 0) + count
        by_state[state] = by_state.get(state, 0) + count

    return CollectionStats(
        collection=CollectionOut.model_validate(collection),
        files=files,
        pieces=sum(by_route.values()),
        by_route=by_route,
        by_review_state=by_state,
    )


@router.post("/{collection_id}/scan", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def scan_collection(
    collection_id: int,
    session: Session = Depends(get_session),
    _: Principal = Depends(require("admin")),
    rescan: bool = False,
) -> JobOut:
    """Queue a scan.

    Always a background job: reading a few thousand files over SMB takes
    minutes, and the worker throttles itself against host load, so this must
    never block a request.
    """
    collection = session.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such collection")
    job = enqueue(session, "scan_collection", {"collection_id": collection_id, "rescan": rescan})
    return JobOut.model_validate(job)


@router.get("/{collection_id}/jobs", response_model=list[JobOut])
def collection_jobs(
    collection_id: int,
    session: Session = Depends(get_session),
    _: Principal = Depends(require("catalog:read")),
    limit: int = 20,
) -> list[JobOut]:
    rows = session.scalars(
        select(Job)
        .where(Job.payload["collection_id"].astext == str(collection_id))
        .order_by(Job.created_at.desc())
        .limit(limit)
    )
    return [JobOut.model_validate(row) for row in rows]

"""Committing scored proposals to the catalogue.

The contract that makes ingest safe to re-run: **a human decision permanently
outranks a machine one.**  Every signal, past and present, is kept as a
:class:`FieldCandidate` row, and re-ingesting a collection with a corrected
adapter adds candidates and recomputes confidences without ever overwriting a
value a person accepted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Collection, FieldCandidate, Piece, SourceFile
from ..pdfsignals import FileSignals
from .model import FileProposal
from .scoring import resolve_field
from .scoring import route as route_for

#: Weight given to a value a person (or an agent acting for one) chose.  Above
#: any signal weight, so an accepted value can never be argued down.
HUMAN_WEIGHT = 1.0

#: Fields copied onto the piece row for browsing and filtering.
DENORMALISED = {
    "composer": "composer_name",
    "title": "title",
    "catalog": "catalog_display",
    "key": "music_key",
    "form": "form",
    "arranger": "arranger",
    "edition": "edition",
    "publisher": "publisher",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def upsert_collection(session: Session, root: Path, adapter_name: str, name: str | None = None) -> Collection:
    source_path = str(root).replace("\\", "/")
    collection = session.scalar(select(Collection).where(Collection.source_path == source_path))
    if collection is None:
        collection = Collection(name=name or root.name, source_path=source_path, adapter=adapter_name)
        session.add(collection)
        session.flush()
    else:
        collection.adapter = adapter_name
    return collection


def upsert_file(session: Session, collection: Collection, signals: FileSignals) -> tuple[SourceFile, bool]:
    """Insert or update the row for one PDF.  Returns (row, changed)."""
    row = session.scalar(
        select(SourceFile).where(
            SourceFile.collection_id == collection.id,
            SourceFile.rel_path == signals.rel_path,
        )
    )
    if row is None:
        row = SourceFile(collection_id=collection.id, rel_path=signals.rel_path)
        session.add(row)
        changed = True
    else:
        changed = (
            row.size != signals.size
            or abs(row.mtime - signals.mtime) > 1.0
            or (signals.sha256 and row.sha256 != signals.sha256)
        )

    row.size = signals.size
    row.mtime = signals.mtime
    row.page_count = signals.page_count
    row.has_text_layer = signals.has_text_layer
    row.scan_error = signals.error or None
    if signals.sha256:
        row.sha256 = signals.sha256
    session.flush()
    return row, changed


def _piece_for(session: Session, file_row: SourceFile, page_start: int, page_end: int) -> Piece:
    """Find the piece covering this page range, or make one.

    Matching on the page range rather than on a title means a re-ingest that
    improves a title updates the existing entry instead of duplicating it.
    """
    piece = session.scalar(
        select(Piece).where(
            Piece.source_file_id == file_row.id,
            Piece.page_start == page_start,
            Piece.page_end == page_end,
        )
    )
    if piece is None:
        piece = Piece(source_file_id=file_row.id, page_start=page_start, page_end=page_end)
        session.add(piece)
        session.flush()
    return piece


def add_candidate(
    session: Session,
    piece: Piece,
    field: str,
    value: str,
    source: str,
    weight: float,
    note: str | None = None,
) -> FieldCandidate:
    """Record one opinion, without disturbing any that already exist."""
    existing = session.scalar(
        select(FieldCandidate).where(
            FieldCandidate.piece_id == piece.id,
            FieldCandidate.field == field,
            FieldCandidate.source == source,
            FieldCandidate.value == value,
        )
    )
    if existing is not None:
        existing.weight = max(existing.weight, weight)
        if note:
            existing.note = note
        return existing
    candidate = FieldCandidate(
        piece_id=piece.id, field=field, value=value, source=source, weight=weight, note=note
    )
    session.add(candidate)
    session.flush()
    return candidate


def recompute(session: Session, piece: Piece, *, auto_accept: float, review_floor: float) -> Piece:
    """Re-resolve every field on a piece from the candidates currently stored.

    An accepted candidate short-circuits its field entirely: no combination of
    machine signals can outvote it, and no re-scan can silently replace it.
    """
    from .model import Candidate as ScoringCandidate

    rows = list(session.scalars(select(FieldCandidate).where(FieldCandidate.piece_id == piece.id)))
    by_field: dict[str, list[FieldCandidate]] = {}
    for row in rows:
        by_field.setdefault(row.field, []).append(row)

    resolved_values: dict[str, str] = {}
    scores: list[float] = []
    conflicted: list[str] = []

    for field, candidates in by_field.items():
        accepted = next((c for c in candidates if c.accepted), None)
        if accepted is not None:
            resolved_values[field] = accepted.value
            if field in ("composer", "title"):
                scores.append(HUMAN_WEIGHT)
            continue

        resolved = resolve_field(field, [
            ScoringCandidate(c.field, c.value, c.source, min(max(c.weight, 0.01), 1.0)) for c in candidates
        ])
        if resolved is None:
            continue
        resolved_values[field] = str(resolved.value)
        if resolved.conflict:
            conflicted.append(field)
        if field in ("composer", "title"):
            scores.append(resolved.confidence)

    for field in ("composer", "title"):
        if field not in resolved_values:
            scores.append(0.0)

    from .scoring import CONFLICT_CAP

    confidence = min(scores) if scores else 0.0
    notes: list[str] = []
    if conflicted:
        confidence = min(confidence, CONFLICT_CAP)
        notes.append("signals disagree on " + ", ".join(sorted(conflicted)))

    piece.confidence = round(confidence, 4)
    piece.route = route_for(piece.confidence, auto_accept=auto_accept, review_floor=review_floor)
    piece.notes_machine = notes

    for field, column in DENORMALISED.items():
        if field in resolved_values:
            setattr(piece, column, resolved_values[field] or None)

    _split_catalog(piece)
    return piece


def _split_catalog(piece: Piece) -> None:
    """Break the catalogue string into sortable parts.

    Stored alongside the display form rather than instead of it: the display
    string is what a publisher wrote, the parts are what a database can order.
    Without them "Op. 10 no. 9" sorts between "no. 1" and "no. 11".
    """
    from ..music.catalogs import parse_catalog

    catalog = parse_catalog(piece.catalog_display or "")
    piece.catalog_system = catalog.system if catalog else None
    piece.catalog_number = catalog.number if catalog else None
    piece.catalog_suffix = (catalog.suffix or None) if catalog else None
    piece.catalog_sub = catalog.sub if catalog else None


def commit_proposal(
    session: Session,
    collection: Collection,
    signals: FileSignals,
    proposal: FileProposal,
) -> list[Piece]:
    """Persist one file's proposal, returning the pieces it touched."""
    file_row, _changed = upsert_file(session, collection, signals)
    if proposal.skipped:
        return []

    touched: list[Piece] = []
    for proposed in proposal.pieces:
        piece = _piece_for(session, file_row, proposed.page_start, proposed.page_end)
        piece.printed_first_page = proposed.printed_first_page
        piece.printed_last_page = proposed.printed_last_page
        for candidate in proposed.candidates:
            add_candidate(
                session, piece, candidate.field, str(candidate.value),
                candidate.source, candidate.weight, candidate.note or None,
            )
        recompute(
            session, piece,
            auto_accept=collection.auto_accept,
            review_floor=collection.review_floor,
        )
        touched.append(piece)
    return touched


def accept_value(
    session: Session,
    piece: Piece,
    field: str,
    value: str,
    *,
    user_id: int | None = None,
    source: str = "human",
    note: str | None = None,
) -> FieldCandidate:
    """Record a decision.  Exactly one candidate per field can be accepted."""
    for existing in session.scalars(
        select(FieldCandidate).where(
            FieldCandidate.piece_id == piece.id,
            FieldCandidate.field == field,
            FieldCandidate.accepted.is_(True),
        )
    ):
        existing.accepted = False

    candidate = add_candidate(session, piece, field, value, source, HUMAN_WEIGHT, note)
    candidate.accepted = True
    candidate.accepted_by = user_id
    candidate.accepted_at = _utcnow()
    session.flush()
    return candidate

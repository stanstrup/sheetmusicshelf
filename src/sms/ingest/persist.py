"""Committing scored proposals to the catalogue.

The contract that makes ingest safe to re-run: **a human decision permanently
outranks a machine one.**  Re-ingesting a collection with a corrected adapter
recomputes confidences without ever overwriting a value a person accepted.

Machine readings, unlike decisions, are retired when the adapter that made
them stops making them -- see :func:`_retire_superseded`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Collection, FieldCandidate, Piece, RemovedRange, SourceFile
from ..pdfsignals import FileSignals
from .model import FileProposal
from .scoring import resolve_field
from .scoring import route as route_for

#: Weight given to a value a person (or an agent acting for one) chose.  Above
#: any signal weight, so an accepted value can never be argued down.
HUMAN_WEIGHT = 1.0

#: Denormalised columns that hold a number rather than text.  Candidates are
#: stored as strings, so these need converting on the way onto the row.
_INT_COLUMNS = {"movement"}

#: Fields copied onto the piece row for browsing and filtering.
DENORMALISED = {
    "composer": "composer_name",
    "title": "title",
    "catalog": "catalog_display",
    "key": "music_key",
    "form": "form",
    "instrumentation": "instrumentation",
    "movement_no": "movement",
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


def was_removed(session: Session, file_row: SourceFile, page_start: int) -> bool:
    """Whether a person deleted the piece starting on this page.

    Matched on the starting page alone.  Requiring the end page to match too
    meant a tombstone written after someone narrowed a range no longer
    described anything the adapter proposed, and the entry they deleted came
    back on the next scan.
    """
    return bool(
        session.scalar(
            select(RemovedRange.id).where(
                RemovedRange.source_file_id == file_row.id,
                RemovedRange.page_start == page_start,
            ).limit(1)
        )
    )


def _piece_for(
    session: Session, file_row: SourceFile, page_start: int, page_end: int
) -> Piece | None:
    """Find the piece starting on this page, or make one.

    The starting page identifies a piece; the end is a property of it.  Keying
    on both meant that narrowing a range in the page-range editor and then
    re-scanning created a second piece at the same starting page, leaving the
    hand-edited one as a duplicate.

    A range someone confirmed by hand is left as they set it.  Otherwise the
    adapter's current reading of where the piece ends is taken, so a corrected
    adapter can still improve an untouched entry.

    Returns None for a piece someone deleted: a re-scan must not undo that.
    """
    piece = session.scalar(
        select(Piece).where(
            Piece.source_file_id == file_row.id,
            Piece.page_start == page_start,
        )
    )
    if piece is None and was_removed(session, file_row, page_start):
        return None
    if piece is None:
        piece = Piece(source_file_id=file_row.id, page_start=page_start, page_end=page_end)
        session.add(piece)
        session.flush()
    elif not piece.pages_confirmed and piece.page_end != page_end:
        piece.page_end = page_end
    return piece


def add_candidate(
    session: Session,
    piece: Piece,
    field: str,
    value: str,
    source: str,
    weight: float,
    note: str | None = None,
    adapter: str | None = None,
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
        if adapter:
            existing.adapter = adapter
        return existing
    candidate = FieldCandidate(
        piece_id=piece.id, field=field, value=value, source=source,
        weight=weight, note=note, adapter=adapter,
    )
    session.add(candidate)
    session.flush()
    return candidate


def _retire_superseded(
    session: Session, piece: Piece, adapter: str, current: list
) -> None:
    """Withdraw this adapter's earlier readings that it no longer makes.

    Keeping every signal for ever sounds like the conservative choice, but it
    is the opposite: a value an adapter has been *fixed* to stop emitting goes
    on arguing its case for the life of the catalogue, so re-scanning with the
    fix changes nothing.  Debussy's ``chilcor1.pdf`` held on to its stale
    "Children's Corner, no. 1" for exactly this reason after the adapter
    learned that the folder holds a single work.

    A field the adapter has stopped claiming altogether is withdrawn on the
    same principle: Schumann's Kreisleriana was eight works rather than one
    because a stale "Op. 16 no. 3" outlived the reading that produced it, and
    works are grouped by catalogue number before title.

    Scoped to the adapter that is speaking, so a person's decision and an
    agent's suggestion through the curation API both survive a re-scan --
    which is what keeps the promise at the top of this module intact.
    """
    if not adapter:
        return
    still = {(c.field, c.source, str(c.value)) for c in current}
    for row in session.scalars(
        select(FieldCandidate).where(
            FieldCandidate.piece_id == piece.id,
            FieldCandidate.adapter == adapter,
            FieldCandidate.accepted.is_(False),
        )
    ):
        if is_superseded((row.field, row.source, row.value), still):
            session.delete(row)
    session.flush()


def is_superseded(reading: tuple[str, str, str], still_made: set[tuple[str, str, str]]) -> bool:
    """Whether a stored reading is one this run no longer makes.

    A reading is (field, source, value): the same source changing its mind
    about a value supersedes the old one, and a field the adapter has stopped
    claiming at all is withdrawn entirely.
    """
    return reading not in still_made


def retract_candidate(
    session: Session, piece: Piece, field: str, source: str, value: str | None = None
) -> int:
    """Withdraw a proposal made by ``source``.  Returns how many were removed.

    The counterpart, for agents, of an adapter withdrawing a superseded
    reading.  Without it a wrong proposal argues its case for ever: it
    conflicts with the right value, caps the field below the review floor, and
    holds the piece with no way back.

    A decision is not a proposal and is never retracted here -- correcting one
    means accepting a different value, which is a decision in its own right
    and stays on the record as one.
    """
    query = select(FieldCandidate).where(
        FieldCandidate.piece_id == piece.id,
        FieldCandidate.field == field,
        FieldCandidate.source == source,
        FieldCandidate.accepted.is_(False),
    )
    if value is not None:
        query = query.where(FieldCandidate.value == value)
    removed = 0
    for row in session.scalars(query):
        session.delete(row)
        removed += 1
    session.flush()
    return removed

def recompute(session: Session, piece: Piece, *, auto_accept: float, review_floor: float) -> Piece:
    """Re-resolve every field on a piece from the candidates currently stored.

    An accepted candidate short-circuits its field entirely: no combination of
    machine signals can outvote it, and no re-scan can silently replace it.
    """
    from .model import Candidate as ScoringCandidate
    from .model import ResolvedField
    from .scoring import combine

    rows = list(session.scalars(select(FieldCandidate).where(FieldCandidate.piece_id == piece.id)))
    by_field: dict[str, list[FieldCandidate]] = {}
    for row in rows:
        by_field.setdefault(row.field, []).append(row)

    # Resolve each field to the same shape the scorer produces, so that the
    # routing rules can be applied by the one function that owns them rather
    # than restated here.  An accepted value is a decision, so it enters at
    # full weight and never counts as conflicted.
    outcomes: dict[str, ResolvedField] = {}
    for field, candidates in by_field.items():
        accepted = next((c for c in candidates if c.accepted), None)
        if accepted is not None:
            outcomes[field] = ResolvedField(
                field=field, value=accepted.value, confidence=HUMAN_WEIGHT,
                sources=[accepted.source],
            )
            continue

        resolved = resolve_field(field, [
            ScoringCandidate(c.field, c.value, c.source, min(max(c.weight, 0.01), 1.0)) for c in candidates
        ])
        if resolved is not None:
            outcomes[field] = resolved

    resolved_values = {name: str(f.value) for name, f in outcomes.items()}
    confidence, notes = combine(outcomes)

    piece.confidence = confidence
    piece.route = route_for(piece.confidence, auto_accept=auto_accept, review_floor=review_floor)

    # A person looking at the piece and accepting it settles it, whatever the
    # signals add up to.  Without this the only way to get a piece out of the
    # queue was to raise its confidence by accepting each field in turn, which
    # turned every pre-filled machine guess on the form into a permanent human
    # decision -- and spent, silently, the ability to re-run a fixed adapter.
    if piece.review_state == "accepted":
        piece.route = "accept"
        notes.append("catalogued by hand")
    elif piece.review_state == "rejected":
        piece.route = "hold"
    # The adapter's observations survive every recompute; the scorer's are
    # re-derived each time.
    piece.notes_machine = list(piece.notes_ingest or []) + notes

    # The row is a projection of the candidates and nothing more, so a field
    # whose last candidate has gone is cleared rather than left standing.
    # Otherwise an adapter can withdraw a reading and the value it produced
    # still shows in the catalogue, and still groups works by it.
    for field, column in DENORMALISED.items():
        value = resolved_values.get(field) or None
        if value is not None and column in _INT_COLUMNS:
            value = int(value) if str(value).lstrip("-").isdigit() else None
        setattr(piece, column, value)

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
        if piece is None:
            continue                       # deleted by hand; stays deleted
        piece.printed_first_page = proposed.printed_first_page
        piece.printed_last_page = proposed.printed_last_page
        piece.notes_ingest = list(proposed.notes)
        for candidate in proposed.candidates:
            add_candidate(
                session, piece, candidate.field, str(candidate.value),
                candidate.source, candidate.weight, candidate.note or None,
                adapter=proposal.adapter,
            )
        _retire_superseded(session, piece, proposal.adapter, proposed.candidates)
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

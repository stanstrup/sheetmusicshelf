"""The catalogue schema.

The structural decision the whole design rests on: **the file is not the
catalogue entry**.  A :class:`Work` is what you look for, a :class:`SourceFile`
is where a copy of it happens to live, and a :class:`Piece` is the page range
inside that file.  Separating the three is what makes "pages 380-383 of the
Brahms complete edition" a thing you can shelve, filter and open.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# --- authority records ----------------------------------------------------

class Composer(Base):
    __tablename__ = "composer"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    sort_name: Mapped[str] = mapped_column(String(200), index=True)
    born: Mapped[int | None] = mapped_column(Integer)
    died: Mapped[int | None] = mapped_column(Integer)
    #: Spellings that resolve to this record.  Grown by ingest and by phase-4
    #: enrichment; the seed list lives in ``sms.music.composers``.
    aliases: Mapped[list] = mapped_column(JSONB, default=list)
    musicbrainz_id: Mapped[str | None] = mapped_column(String(64))
    imslp_id: Mapped[str | None] = mapped_column(String(200))

    # --- enrichment, from Wikipedia and Wikidata ---
    period: Mapped[str | None] = mapped_column(String(24), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    wikipedia_url: Mapped[str | None] = mapped_column(Text)
    wikidata_id: Mapped[str | None] = mapped_column(String(24))
    #: Filename of the cached portrait on the cache volume.  The image is
    #: copied locally rather than hot-linked, so a phone on a VPN with no route
    #: to the internet still sees it.
    portrait_file: Mapped[str | None] = mapped_column(String(120))
    portrait_source_url: Mapped[str | None] = mapped_column(Text)
    #: Most Commons portraits are CC-BY-SA. Displaying one without crediting
    #: the photographer is a licence breach, so the credit is not optional.
    portrait_credit: Mapped[str | None] = mapped_column(Text)
    portrait_license: Mapped[str | None] = mapped_column(String(120))
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at = _now()

    works: Mapped[list["Work"]] = relationship(back_populates="composer")

    @property
    def lifespan(self) -> str:
        from .music.periods import lifespan

        return lifespan(self.born, self.died)


class Work(Base):
    """A composition, independent of any file that carries it.

    Self-referencing: a sonata is a work whose movements are also works, so a
    movement can be shelved and opened on its own without duplicating the
    parent's metadata.
    """

    __tablename__ = "work"
    __table_args__ = (
        # Catalogue numbers are the strong identifier; the same one twice under
        # one composer is the same work.
        UniqueConstraint(
            "composer_id", "catalog_system", "catalog_number", "catalog_suffix", "catalog_sub",
            name="uq_work_catalog",
        ),
        Index("ix_work_sort", "composer_id", "catalog_system", "catalog_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    composer_id: Mapped[int | None] = mapped_column(ForeignKey("composer.id", ondelete="SET NULL"), index=True)
    parent_work_id: Mapped[int | None] = mapped_column(ForeignKey("work.id", ondelete="CASCADE"), index=True)

    title: Mapped[str] = mapped_column(Text, index=True)
    #: Split rather than stored raw, so K.9 sorts before K.10.
    catalog_system: Mapped[str | None] = mapped_column(String(12), index=True)
    catalog_number: Mapped[int | None] = mapped_column(Integer, index=True)
    catalog_suffix: Mapped[str] = mapped_column(String(8), default="")
    catalog_sub: Mapped[int | None] = mapped_column(Integer)

    music_key: Mapped[str | None] = mapped_column(String(32), index=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    #: What the source actually said -- "1774", "1774-75", "ca. 1783". The year
    #: column is a single number for sorting; this keeps the qualification that
    #: number throws away.
    year_note: Mapped[str | None] = mapped_column(String(80))
    form: Mapped[str | None] = mapped_column(String(80), index=True)
    period: Mapped[str | None] = mapped_column(String(40), index=True)
    movement_no: Mapped[int | None] = mapped_column(Integer)

    # --- canonical sources ---
    # Links belong to the work, not to each copy of it: the six editions of
    # K. 283 in this library are one piece of music with one IMSLP page.
    musicbrainz_id: Mapped[str | None] = mapped_column(String(64), index=True)
    #: What MusicBrainz calls it. A bare MBID tells the reader nothing about
    #: whether the link is right, which is the whole point of showing it.
    musicbrainz_title: Mapped[str | None] = mapped_column(Text)
    imslp_title: Mapped[str | None] = mapped_column(Text)
    imslp_url: Mapped[str | None] = mapped_column(Text)
    wikidata_id: Mapped[str | None] = mapped_column(String(24))
    #: How the match was made and how far it can be trusted, in words. A link
    #: nobody can check is worse than no link.
    match_note: Mapped[str | None] = mapped_column(Text)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: True when a person confirmed the link.
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at = _now()

    composer: Mapped[Composer | None] = relationship(back_populates="works")
    parent: Mapped["Work | None"] = relationship(remote_side=[id], backref="movements")
    pieces: Mapped[list["Piece"]] = relationship(back_populates="work")

    @property
    def catalog_display(self) -> str:
        if not self.catalog_system or self.catalog_number is None:
            return ""
        base = f"{self.catalog_system}. {self.catalog_number}{self.catalog_suffix or ''}"
        return f"{base} no. {self.catalog_sub}" if self.catalog_sub is not None else base


# --- the library on disk --------------------------------------------------

class Collection(Base):
    """One source folder, ingested and reviewed as a unit."""

    __tablename__ = "collection"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    source_path: Mapped[str] = mapped_column(Text, unique=True)
    adapter: Mapped[str] = mapped_column(String(40), default="generic")
    ignore_globs: Mapped[list] = mapped_column(JSONB, default=list)
    defaults: Mapped[dict] = mapped_column(JSONB, default=dict)
    #: Per-collection routing.  The pop pile wants stricter thresholds than a
    #: scholarly edition with embedded metadata.
    auto_accept: Mapped[float] = mapped_column(Float, default=0.80)
    review_floor: Mapped[float] = mapped_column(Float, default=0.50)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at = _now()

    files: Mapped[list["SourceFile"]] = relationship(back_populates="collection", cascade="all, delete-orphan")


class SourceFile(Base):
    """A PDF as it exists on disk.  Never modified, only read and copied."""

    __tablename__ = "source_file"
    __table_args__ = (
        UniqueConstraint("collection_id", "rel_path", name="uq_file_path"),
        Index("ix_file_hash", "sha256"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    collection_id: Mapped[int] = mapped_column(ForeignKey("collection.id", ondelete="CASCADE"), index=True)
    rel_path: Mapped[str] = mapped_column(Text)
    #: Content hash, so a file renamed outside the app re-links on rescan
    #: instead of being catalogued twice.
    sha256: Mapped[str | None] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, default=0)
    mtime: Mapped[float] = mapped_column(Float, default=0.0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    has_text_layer: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Set once the file has been copied into the managed tree.
    managed_path: Mapped[str | None] = mapped_column(Text)
    scan_error: Mapped[str | None] = mapped_column(Text)
    created_at = _now()

    collection: Mapped[Collection] = relationship(back_populates="files")
    pieces: Mapped[list["Piece"]] = relationship(back_populates="source_file", cascade="all, delete-orphan")


# --- the catalogue entry --------------------------------------------------

REVIEW_STATES = ("pending", "accepted", "rejected")
ROUTES = ("accept", "review", "hold")


class Piece(Base):
    """A page range within one file: the thing you browse, shelve and open."""

    __tablename__ = "piece"
    __table_args__ = (
        CheckConstraint("page_end >= page_start", name="ck_piece_pages"),
        CheckConstraint("page_start >= 1", name="ck_piece_page_start"),
        Index("ix_piece_route", "route", "confidence"),
        Index("ix_piece_catalog_sort", "composer_name", "catalog_system", "catalog_number", "catalog_sub"),
        # The starting page identifies a piece within its file, so this is
        # a constraint and not a hint: two pieces starting on the same page is
        # the duplication bug, not a state worth indexing.
        UniqueConstraint("source_file_id", "page_start", name="uq_piece_file_page_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_file.id", ondelete="CASCADE"), index=True)
    work_id: Mapped[int | None] = mapped_column(ForeignKey("work.id", ondelete="SET NULL"), index=True)

    #: File-relative and 1-based -- what the reader needs to open.
    page_start: Mapped[int] = mapped_column(Integer, default=1)
    page_end: Mapped[int] = mapped_column(Integer, default=1)
    #: True once a person set this range in the page-range editor.  A re-scan
    #: may then improve everything about the piece except where it ends: the
    #: boundary is a decision, and decisions outrank the adapter.
    pages_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    #: The page number *printed in the original volume*, when metadata reveals
    #: it.  A different thing from the above, and never used to navigate.
    printed_first_page: Mapped[int | None] = mapped_column(Integer)
    printed_last_page: Mapped[int | None] = mapped_column(Integer)

    # Resolved values, denormalised so a piece is browsable before its work
    # authority record exists.
    title: Mapped[str | None] = mapped_column(Text, index=True)
    composer_name: Mapped[str | None] = mapped_column(String(200), index=True)
    catalog_display: Mapped[str | None] = mapped_column(String(64))
    # Split as well as displayed, so "Op. 10 no. 9" sorts before "Op. 10 no. 10"
    # instead of lexically between "no. 1" and "no. 11".
    catalog_system: Mapped[str | None] = mapped_column(String(12), index=True)
    catalog_number: Mapped[int | None] = mapped_column(Integer)
    catalog_suffix: Mapped[str | None] = mapped_column(String(8))
    catalog_sub: Mapped[int | None] = mapped_column(Integer)
    music_key: Mapped[str | None] = mapped_column(String(32), index=True)
    form: Mapped[str | None] = mapped_column(String(80), index=True)
    #: What the music is scored for, as a phrase: "solo piano", "violin and
    #: piano".  A denormalised string rather than a link to :class:`Instrument`
    #: because that is the shape the adapters can actually observe -- a folder
    #: called "violpian" says "violin and piano" and nothing finer.
    instrumentation: Mapped[str | None] = mapped_column(String(120), index=True)
    #: Which movement of its work this file holds, when the work is split over
    #: several files.  None for the ordinary case of one file, one piece.
    movement: Mapped[int | None] = mapped_column(Integer)
    arranger: Mapped[str | None] = mapped_column(String(200))
    edition: Mapped[str | None] = mapped_column(String(200))
    publisher: Mapped[str | None] = mapped_column(String(200))

    confidence: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    route: Mapped[str] = mapped_column(String(10), default="hold")
    review_state: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: What the adapter observed while reading the file ("collection index 537",
    #: "toc says 8pp, file has 12pp"). Kept apart from the scorer's own notes so
    #: recomputing can rebuild one without losing the other.
    notes_ingest: Mapped[list] = mapped_column(JSONB, default=list)
    #: Everything worth telling a reviewer: the adapter's notes plus the
    #: scorer's. Rebuilt on every recompute.
    notes_machine: Mapped[list] = mapped_column(JSONB, default=list)

    # Personal fields -- a defined set rather than runtime-definable columns.
    difficulty: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(20), index=True)
    rating: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    last_opened: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    source_file: Mapped[SourceFile] = relationship(back_populates="pieces")
    work: Mapped[Work | None] = relationship(back_populates="pieces")
    candidates: Mapped[list["FieldCandidate"]] = relationship(
        back_populates="piece", cascade="all, delete-orphan"
    )
    instruments: Mapped[list["PieceInstrument"]] = relationship(
        back_populates="piece", cascade="all, delete-orphan"
    )

    @property
    def page_count(self) -> int:
        return self.page_end - self.page_start + 1


class FieldCandidate(Base):
    """One signal's opinion about one field, kept permanently.

    This is the audit trail that makes every confidence number explainable, and
    it is what lets a corrected adapter be re-run without destroying review
    work: human decisions are rows here too, and they outrank machine ones.
    """

    __tablename__ = "field_candidate"
    __table_args__ = (
        Index("ix_candidate_piece_field", "piece_id", "field"),
        UniqueConstraint("piece_id", "field", "source", "value", name="uq_candidate"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    piece_id: Mapped[int] = mapped_column(ForeignKey("piece.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(60))
    #: The adapter that made this reading, or None for anything else -- a
    #: person, or an agent posting through the curation API.  Knowing who
    #: said it is what lets a corrected adapter withdraw its own old claims
    #: without touching anybody else's.
    adapter: Mapped[str | None] = mapped_column(String(40), index=True)
    weight: Mapped[float] = mapped_column(Float, default=0.5)
    note: Mapped[str | None] = mapped_column(Text)
    #: True once a person (or an agent acting for one) chose this value.
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    accepted_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at = _now()

    piece: Mapped[Piece] = relationship(back_populates="candidates")


# --- vocabulary and grouping ---------------------------------------------

class Instrument(Base):
    __tablename__ = "instrument"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    aliases: Mapped[list] = mapped_column(JSONB, default=list)


class PieceInstrument(Base):
    __tablename__ = "piece_instrument"

    piece_id: Mapped[int] = mapped_column(ForeignKey("piece.id", ondelete="CASCADE"), primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), default="solo")

    piece: Mapped[Piece] = relationship(back_populates="instruments")
    instrument: Mapped[Instrument] = relationship()


class Shelf(Base):
    """A user-defined collection.  Ordered, so a shelf doubles as a setlist."""

    __tablename__ = "shelf"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    created_at = _now()

    items: Mapped[list["ShelfItem"]] = relationship(
        back_populates="shelf", cascade="all, delete-orphan", order_by="ShelfItem.position"
    )


class ShelfItem(Base):
    __tablename__ = "shelf_item"
    __table_args__ = (UniqueConstraint("shelf_id", "piece_id", name="uq_shelf_piece"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shelf_id: Mapped[int] = mapped_column(ForeignKey("shelf.id", ondelete="CASCADE"), index=True)
    piece_id: Mapped[int] = mapped_column(ForeignKey("piece.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)

    shelf: Mapped[Shelf] = relationship(back_populates="items")
    piece: Mapped[Piece] = relationship()


# --- people, access and work queues --------------------------------------

class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    oidc_subject: Mapped[str | None] = mapped_column(String(200), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(200))
    display_name: Mapped[str | None] = mapped_column(String(200))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at = _now()


class ApiToken(Base):
    """A bearer token for an external agent or a device.

    Stored hashed: a leaked database row must not be a working credential.
    """

    __tablename__ = "api_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    #: e.g. ``["catalog:read", "curation:write"]``
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at = _now()

    user: Mapped[AppUser | None] = relationship()


class Annotation(Base):
    """A coordinate layer over one page of one file.

    Never flattened into the PDF, so originals stay pristine and every client
    reads what any other client wrote.

    Keyed to the **file** and the file's own page number, not to a piece.  A
    piece is a claim about where a work starts and ends, and claims get
    corrected: the page-range editor deletes pieces whose boundary has moved,
    and a cascade from the piece would take the ink with it.  Everything else
    in this schema can be recomputed from the candidates; a person's pencil
    cannot, so it is not hung off the one thing designed to be revised.

    Clients still ask in piece-relative pages, which is what a reader knows.
    The translation is ``piece.page_start + n - 1``, the same arithmetic the
    page renderer does.
    """

    __tablename__ = "annotation"
    __table_args__ = (
        Index("ix_annotation_file_page", "source_file_id", "page"),
        # One layer per page per person. NULLS NOT DISTINCT because an
        # unauthenticated (development) user has no id, and without it Postgres
        # would happily store a second row for the same page.
        UniqueConstraint(
            "source_file_id", "user_id", "page",
            name="uq_annotation_page",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file_id: Mapped[int] = mapped_column(
        ForeignKey("source_file.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    #: 1-based within the *file*, so it survives a piece being re-cut.
    page: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RemovedRange(Base):
    """A page range a person deleted, remembered so a re-scan cannot undo it.

    Without this, deleting a catalogue entry would only last until the next
    scan: the ingester matches pieces by page range and recreates whatever is
    missing. The file itself is never touched -- this records a decision about
    the *catalogue*, not about the disk.
    """

    __tablename__ = "removed_range"
    __table_args__ = (
        UniqueConstraint("source_file_id", "page_start", "page_end", name="uq_removed_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file_id: Mapped[int] = mapped_column(
        ForeignKey("source_file.id", ondelete="CASCADE"), index=True
    )
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    removed_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at = _now()


JOB_STATES = ("queued", "running", "done", "failed", "cancelled")


class Job(Base):
    """A unit of background work.

    Postgres-backed rather than Redis-backed: this box already runs some forty
    containers, and a job table claimed with ``FOR UPDATE SKIP LOCKED`` is
    entirely sufficient for a library of this size.
    """

    __tablename__ = "job"
    __table_args__ = (Index("ix_job_claim", "state", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    state: Mapped[str] = mapped_column(String(12), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

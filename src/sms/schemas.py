"""Request and response shapes.

These double as the API's documentation: FastAPI turns them into the OpenAPI
schema an external agent reads before it does anything else, so the field
descriptions here are written for that reader.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .ingest.model import KNOWN_FIELDS


class CandidateOut(BaseModel):
    """One signal's opinion, with the provenance that justifies its weight."""

    field: str
    value: str
    source: str = Field(description="What produced this value, e.g. 'docinfo_subject', 'toc', 'curation:manual'.")
    weight: float = Field(description="0-1. How much this source is trusted for this field.")
    accepted: bool = Field(description="True when a person, or an agent acting for one, chose this value.")
    note: str | None = None

    model_config = {"from_attributes": True}


class ResolvedFieldOut(BaseModel):
    value: str
    confidence: float
    sources: list[str]
    conflict: bool = Field(description="True when signals proposed genuinely different values.")
    alternatives: list[tuple[str, float, list[str]]] = []


class PieceOut(BaseModel):
    id: int
    title: str | None = None
    composer_name: str | None = None
    catalog_display: str | None = None
    music_key: str | None = None
    form: str | None = None
    page_start: int = Field(description="1-based and file-relative: the page to open.")
    page_end: int
    printed_first_page: int | None = Field(
        default=None,
        description="Page number printed in the original volume, when known. Never used to navigate.",
    )
    confidence: float
    route: str = Field(description="accept | review | hold")
    review_state: str = Field(description="pending | accepted | rejected")
    difficulty: int | None = None
    status: str | None = None
    rating: int | None = None
    tags: list[str] = []
    notes_machine: list[str] = []

    model_config = {"from_attributes": True}


class QueueItem(BaseModel):
    """Everything an external curator needs to identify one uncertain piece."""

    piece: PieceOut
    collection: str
    rel_path: str = Field(description="Path of the PDF relative to the collection root.")
    page_count: int = Field(description="Pages in the whole file.")
    has_text_layer: bool = Field(description="False for image-only scans; text signals will be absent.")
    candidates: list[CandidateOut]
    missing_fields: list[str] = Field(description="Identifying fields with no value at all.")
    conflicted_fields: list[str] = Field(description="Fields whose signals disagree.")


class RetractionIn(BaseModel):
    """Withdraw a proposal you made earlier.

    Only your own unaccepted proposals, identified by the same ``source`` you
    sent them with.  A value a person accepted is a decision, not a proposal,
    and is never withdrawn this way.
    """

    piece_id: int
    field: str = Field(description="One of: " + ", ".join(sorted(KNOWN_FIELDS)))
    source: str = Field(description="The source you used when proposing.")
    value: str | None = Field(
        default=None,
        description="Withdraw just this value; omit to withdraw all of yours for the field.",
    )


class CandidateIn(BaseModel):
    """A proposed value.  Lands in the review queue like any machine signal."""

    piece_id: int
    field: str = Field(description="One of: " + ", ".join(sorted(KNOWN_FIELDS)))
    value: str
    weight: float = Field(
        default=0.65,
        ge=0.01,
        le=1.0,
        description="How confident you are. Below 0.80 the piece still needs human review.",
    )
    source: str = Field(
        default="curation:external",
        description="Identify yourself, e.g. 'curation:claude'. Prefix with 'curation:' by convention.",
    )
    note: str | None = Field(default=None, description="Why you think so. Shown to the reviewer.")


class DecisionIn(BaseModel):
    """A decision, not a suggestion: this value becomes final for the field."""

    piece_id: int
    field: str
    value: str
    note: str | None = None


class BulkResult(BaseModel):
    accepted: int
    rejected: int = 0
    errors: list[str] = []


class CollectionOut(BaseModel):
    id: int
    name: str
    source_path: str
    adapter: str
    auto_accept: float
    review_floor: float
    last_scanned_at: datetime | None = None

    model_config = {"from_attributes": True}


class CollectionIn(BaseModel):
    source_path: str = Field(description="Absolute path of the collection root, inside the container.")
    name: str | None = None
    adapter: str | None = Field(default=None, description="Omit to auto-detect.")
    auto_accept: float | None = None
    review_floor: float | None = None


class CollectionStats(BaseModel):
    collection: CollectionOut
    files: int
    pieces: int
    by_route: dict[str, int]
    by_review_state: dict[str, int]


class JobOut(BaseModel):
    id: int
    kind: str
    state: str
    progress: float
    message: str | None = None
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class TokenIn(BaseModel):
    name: str
    scopes: list[str]


class TokenOut(BaseModel):
    id: int
    name: str
    scopes: list[str]
    token: str | None = Field(default=None, description="Shown once, at creation. Not recoverable.")

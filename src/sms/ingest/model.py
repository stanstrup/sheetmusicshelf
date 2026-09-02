"""The vocabulary shared by every adapter and by the scorer.

An adapter never decides anything on its own -- it emits *candidates*, each
tagged with the signal that produced it and a base weight.  The scorer is the
only place that turns candidates into an answer, which is what makes every
confidence number in the UI explainable by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Fields a piece cannot be auto-accepted without.  Instrumentation, key, year
# and the rest are welcome but never block: a correctly identified piece with
# an unknown key is still a correctly identified piece.
REQUIRED_FIELDS = ("composer", "title")

# Every field an adapter is allowed to propose.  Anything else is a typo.
KNOWN_FIELDS = frozenset({
    "composer", "title", "work_title", "catalog", "key", "year", "form",
    "instrumentation", "arranger", "edition", "publisher", "movement_no",
})


@dataclass(frozen=True, slots=True)
class Candidate:
    """One signal's opinion about one field."""

    field: str
    value: Any
    source: str
    weight: float
    note: str = ""

    def __post_init__(self) -> None:
        if self.field not in KNOWN_FIELDS:
            raise ValueError(f"unknown field {self.field!r}")
        if not 0.0 < self.weight <= 1.0:
            raise ValueError(f"weight out of range for {self.field}: {self.weight}")


@dataclass(slots=True)
class ResolvedField:
    """The scorer's verdict on one field, with the reasoning kept attached."""

    field: str
    value: Any
    confidence: float
    sources: list[str] = field(default_factory=list)
    conflict: bool = False
    alternatives: list[tuple[Any, float, list[str]]] = field(default_factory=list)

    @property
    def display(self) -> str:
        return "" if self.value is None else str(self.value)


@dataclass(slots=True)
class PieceProposal:
    """A candidate catalogue entry: a page range within one file, plus opinions.

    ``page_start``/``page_end`` are 1-based and **file-relative** -- they are
    what the reader needs to open.  ``printed_first_page`` records the page
    number printed in the original volume when metadata reveals it, which is a
    different thing and must never be confused with the former.
    """

    page_start: int
    page_end: int
    candidates: list[Candidate] = field(default_factory=list)
    printed_first_page: int | None = None
    printed_last_page: int | None = None
    fields: dict[str, ResolvedField] = field(default_factory=dict)
    confidence: float = 0.0
    #: What the adapter observed while reading the file. Durable: persisted and
    #: carried through every later recompute.
    notes: list[str] = field(default_factory=list)
    #: What the scorer concluded. Derived, so never persisted as an observation
    #: -- storing it would duplicate itself on the next recompute.
    scorer_notes: list[str] = field(default_factory=list)

    def add(self, field_name: str, value: Any, source: str, weight: float, note: str = "") -> None:
        """Ignore empty values so adapters can stay free of `if x:` noise."""
        if value is None or (isinstance(value, str) and not value.strip()):
            return
        self.candidates.append(Candidate(field_name, value, source, weight, note))

    @property
    def all_notes(self) -> list[str]:
        return [*self.notes, *self.scorer_notes]

    def get(self, field_name: str, default: Any = None) -> Any:
        resolved = self.fields.get(field_name)
        return default if resolved is None else resolved.value

    @property
    def page_count(self) -> int:
        return self.page_end - self.page_start + 1

    @property
    def spans_whole_file(self) -> bool:
        return self.page_start == 1 and "whole-file" in self.notes


@dataclass(slots=True)
class FileProposal:
    """Everything an adapter concluded about one file."""

    rel_path: str
    pieces: list[PieceProposal] = field(default_factory=list)
    adapter: str = ""
    skipped: str = ""       # non-empty when the file was deliberately not catalogued
    warnings: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        """A file is as trustworthy as its least trustworthy piece."""
        return min((p.confidence for p in self.pieces), default=0.0)

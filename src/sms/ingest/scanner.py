"""Walking a collection and turning it into scored proposals.

Reading thousands of files across an SMB mount is the expensive part of ingest,
so the walk yields as it goes: the CLI collects the results into a report, the
worker streams them straight into the database, and neither holds the whole
library in memory.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ..pdfsignals import FileSignals, read_signals
from .adapters.base import Adapter, CollectionContext, choose_adapter
from .model import FileProposal
from .scoring import route, score_file

PDF_SUFFIXES = {".pdf"}


@dataclass
class ScanStats:
    files_seen: int = 0
    catalogued: int = 0
    skipped: int = 0
    unreadable: int = 0
    pieces: int = 0
    by_route: dict[str, int] = field(default_factory=lambda: {"accept": 0, "review": 0, "hold": 0})

    def record(self, proposal: FileProposal, *, auto_accept: float, review_floor: float) -> None:
        self.files_seen += 1
        if proposal.skipped:
            self.skipped += 1
            if "unreadable" in proposal.skipped:
                self.unreadable += 1
            return
        self.catalogued += 1
        for piece in proposal.pieces:
            self.pieces += 1
            self.by_route[route(piece.confidence, auto_accept=auto_accept, review_floor=review_floor)] += 1


def iter_pdfs(root: Path) -> Iterator[Path]:
    """Depth-first walk, sorted, so runs are reproducible and diffable."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            if Path(name).suffix.lower() in PDF_SUFFIXES:
                yield Path(dirpath) / name


def build_context(root: Path, adapter: Adapter | None = None) -> tuple[Adapter, CollectionContext]:
    adapter = adapter or choose_adapter(root)
    context = CollectionContext(root=root, name=root.name, adapter=adapter.name)
    adapter.prepare(context)
    return adapter, context


def iter_proposals(
    root: Path,
    adapter: Adapter,
    context: CollectionContext,
    *,
    with_hash: bool = True,
    limit: int | None = None,
) -> Iterator[tuple[FileSignals | None, FileProposal]]:
    """Yield one scored proposal per PDF, with the raw signals that produced it.

    Signals come back alongside the proposal because the caller needs both:
    the proposal to catalogue, and the signals (hash, size, page count) to
    decide whether the file has changed since last time.
    """
    for index, path in enumerate(iter_pdfs(root)):
        if limit is not None and index >= limit:
            break

        rel = str(path.relative_to(root)).replace("\\", "/")
        if adapter.should_ignore(rel):
            yield None, FileProposal(rel_path=rel, adapter=adapter.name, skipped="ignored by adapter glob")
            continue

        signals = read_signals(path, root, with_hash=with_hash)
        yield signals, score_file(adapter.propose(signals, context))


def scan(
    root: Path,
    *,
    adapter: Adapter | None = None,
    with_hash: bool = True,
    limit: int | None = None,
    auto_accept: float | None = None,
    review_floor: float | None = None,
) -> tuple[CollectionContext, list[FileProposal], ScanStats]:
    """Scan one collection and return scored proposals.

    No database involved -- this is what ``sms report`` runs, so a collection's
    guesses can be judged before anything is committed.
    """
    from .scoring import AUTO_ACCEPT, REVIEW_FLOOR

    adapter, context = build_context(root, adapter)
    stats = ScanStats()
    proposals: list[FileProposal] = []
    thresholds = {
        "auto_accept": AUTO_ACCEPT if auto_accept is None else auto_accept,
        "review_floor": REVIEW_FLOOR if review_floor is None else review_floor,
    }

    for _signals, proposal in iter_proposals(root, adapter, context, with_hash=with_hash, limit=limit):
        proposals.append(proposal)
        stats.record(proposal, **thresholds)

    return context, proposals, stats

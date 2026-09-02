"""Background work, on Postgres rather than Redis.

This box already runs some forty containers, so the queue is a table claimed
with ``SELECT ... FOR UPDATE SKIP LOCKED``.  At the scale of one personal
library that is entirely sufficient, and it is one fewer service to keep alive.

The worker throttles itself against host load: a scan that ignores a busy NUC
is not a slow scan, it is an outage for everything else on the machine.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import session_scope
from .ingest.adapters.base import get_adapter
from .ingest.persist import commit_proposal
from .ingest.scanner import ScanStats, build_context, iter_proposals
from .models import Collection, Job

log = logging.getLogger("sms.jobs")

HANDLERS: dict[str, Callable[[Session, Job], str]] = {}


def handler(kind: str):
    def register(func: Callable[[Session, Job], str]) -> Callable[[Session, Job], str]:
        HANDLERS[kind] = func
        return func

    return register


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(session: Session, kind: str, payload: dict) -> Job:
    if kind not in HANDLERS:
        raise ValueError(f"no handler for job kind {kind!r}")
    job = Job(kind=kind, payload=payload, state="queued")
    session.add(job)
    session.flush()
    return job


def load_average() -> float:
    """One-minute load average, or 0.0 where the platform has none."""
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):
        return 0.0


def wait_for_headroom(ceiling: float, *, poll: float = 5.0, patience: int = 60) -> None:
    """Pause while the host is busy, but never forever."""
    if ceiling <= 0:
        return
    for _ in range(patience):
        if load_average() <= ceiling:
            return
        time.sleep(poll)
    log.warning("proceeding despite load %.2f above ceiling %.2f", load_average(), ceiling)


# --- handlers -------------------------------------------------------------

@handler("scan_collection")
def scan_collection(session: Session, job: Job) -> str:
    settings = get_settings()
    collection = session.get(Collection, int(job.payload["collection_id"]))
    if collection is None:
        raise ValueError(f"collection {job.payload.get('collection_id')} no longer exists")

    root = Path(collection.source_path)
    if not root.is_dir():
        raise ValueError(f"collection root is not readable: {root}")

    adapter = get_adapter(collection.adapter)
    adapter, context = build_context(root, adapter)
    stats = ScanStats()

    total_hint = job.payload.get("expected_files") or 0
    processed = 0

    for signals, proposal in iter_proposals(root, adapter, context, with_hash=True):
        stats.record(proposal, auto_accept=collection.auto_accept, review_floor=collection.review_floor)
        if signals is not None:
            commit_proposal(session, collection, signals, proposal)

        processed += 1
        if processed % settings.scan_batch_size == 0:
            session.commit()
            job.progress = min(processed / total_hint, 0.99) if total_hint else 0.0
            job.message = f"{processed} files, {stats.pieces} pieces"
            session.commit()
            wait_for_headroom(settings.load_ceiling)

    collection.last_scanned_at = _utcnow()
    session.commit()
    return (
        f"{stats.files_seen} files, {stats.skipped} skipped, {stats.pieces} pieces "
        f"({stats.by_route['accept']} accept / {stats.by_route['review']} review / {stats.by_route['hold']} hold)"
    )


# --- the loop -------------------------------------------------------------

def claim_one(session: Session) -> Job | None:
    """Take the oldest queued job, locking it against other workers."""
    job = session.scalar(
        select(Job)
        .where(Job.state == "queued")
        .order_by(Job.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.state = "running"
    job.started_at = _utcnow()
    job.attempts += 1
    session.commit()
    return job


def run_once() -> bool:
    """Run at most one job.  Returns True when something was done."""
    with session_scope() as session:
        job = claim_one(session)
        if job is None:
            return False

        log.info("running job %s (%s)", job.id, job.kind)
        try:
            message = HANDLERS[job.kind](session, job)
            job.state = "done"
            job.progress = 1.0
            job.message = message
        except Exception as exc:                      # noqa: BLE001 - recorded, not swallowed
            session.rollback()
            log.exception("job %s failed", job.id)
            job = session.get(Job, job.id)
            if job is not None:
                job.state = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
        finally:
            if job is not None:
                job.finished_at = _utcnow()
        return True


def worker_loop(idle_sleep: float = 5.0) -> None:
    settings = get_settings()
    log.info("worker started; load ceiling %.1f", settings.load_ceiling)
    while True:
        try:
            if not run_once():
                time.sleep(idle_sleep)
        except KeyboardInterrupt:  # pragma: no cover
            log.info("worker stopping")
            return
        except Exception:          # pragma: no cover - keep the worker alive
            log.exception("worker loop error")
            time.sleep(idle_sleep)

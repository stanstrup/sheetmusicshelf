"""The queue: reclaiming what a dead worker left, and not queueing twice."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from sms.jobs import (
    MAX_ATTEMPTS,
    STALE_AFTER,
    _utcnow,
    claim_one,
    enqueue,
    enqueue_once,
    reclaim_stale,
)
from sms.models import Job


class TestClaiming:
    def test_a_queued_job_is_claimed_and_counted(self, session, collection):
        enqueue(session, "scan_collection", {"collection_id": collection.id})
        session.flush()

        job = claim_one(session)

        assert job is not None
        assert job.state == "running"
        assert job.attempts == 1
        assert job.started_at is not None

    def test_an_empty_queue_claims_nothing(self, session):
        assert claim_one(session) is None

    def test_an_unknown_kind_is_refused_at_the_door(self, session):
        with pytest.raises(ValueError):
            enqueue(session, "not-a-real-job", {})


class TestReclaimingAbandonedJobs:
    def _abandoned(self, session, collection, *, attempts: int = 1) -> Job:
        job = enqueue(session, "scan_collection", {"collection_id": collection.id})
        job.state = "running"
        job.started_at = _utcnow() - STALE_AFTER - timedelta(minutes=1)
        job.attempts = attempts
        session.flush()
        return job

    def test_a_job_whose_worker_died_returns_to_the_queue(self, session, collection):
        """No lease and no heartbeat: the row lock died with the connection,
        so nothing else noticed the job was never going to finish."""
        job = self._abandoned(session, collection)

        assert reclaim_stale(session) == 1
        assert job.state == "queued"
        assert job.started_at is None

    def test_a_job_still_within_its_time_is_left_running(self, session, collection):
        job = enqueue(session, "scan_collection", {"collection_id": collection.id})
        job.state = "running"
        job.started_at = _utcnow() - timedelta(minutes=5)
        session.flush()

        assert reclaim_stale(session) == 0
        assert job.state == "running"

    def test_a_job_that_keeps_killing_workers_is_stopped(self, session, collection):
        # A queue that retries for ever never gets past the job that breaks it.
        job = self._abandoned(session, collection, attempts=MAX_ATTEMPTS)

        assert reclaim_stale(session) == 0
        assert job.state == "failed"
        assert "abandoned" in (job.error or "")


class TestQueueingOnce:
    def test_the_same_job_is_not_queued_twice(self, session, collection):
        """Review accepts one piece at a time; a filing job per piece would
        queue hundreds of passes over one collection to do the work of one."""
        payload = {"collection_id": collection.id}

        assert enqueue_once(session, "materialise", payload) is not None
        assert enqueue_once(session, "materialise", payload) is None

        queued = session.scalars(select(Job).where(Job.state == "queued")).all()
        assert len(queued) == 1

    def test_a_different_collection_still_queues(self, session, collection):
        from sms.models import Collection

        other = Collection(name="Another", source_path="/elsewhere", adapter="generic")
        session.add(other)
        session.flush()

        assert enqueue_once(session, "materialise", {"collection_id": collection.id})
        assert enqueue_once(session, "materialise", {"collection_id": other.id})

    def test_one_already_running_does_not_block_the_next(self, session, collection):
        # The running pass may already have walked past the piece just changed,
        # so a fresh pass still has work to do.
        payload = {"collection_id": collection.id}
        first = enqueue_once(session, "materialise", payload)
        first.state = "running"
        session.flush()

        assert enqueue_once(session, "materialise", payload) is not None

"""Database engine and session plumbing."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_engine = None
_Session: sessionmaker[Session] | None = None


def engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,      # the NUC sleeps; stale connections are normal
            pool_size=5,
            max_overflow=5,
            future=True,
        )
    return _engine


def session_factory() -> sessionmaker[Session]:
    global _Session
    if _Session is None:
        _Session = sessionmaker(bind=engine(), expire_on_commit=False, future=True)
    return _Session


@contextmanager
def session_scope() -> Iterator[Session]:
    """A transactional scope.  Commits on success, rolls back on any exception."""
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency.

    Note for anything that mutates and then redirects: FastAPI runs the exit
    code of a ``yield`` dependency *after* the response has been sent, so this
    scope's commit lands after the browser has already followed the redirect.
    Use :func:`commit_now` before returning, or the next page renders the state
    you just changed away from.
    """
    with session_scope() as session:
        yield session


def commit_now(session: Session) -> None:
    """Flush a change to the database before the response leaves.

    Redirect-after-POST is the whole reason this exists: without it, accepting
    a review sent the browser to a page that still showed the piece needing
    review, and only a manual refresh caught up.
    """
    session.commit()

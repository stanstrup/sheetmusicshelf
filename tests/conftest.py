"""A real database for the tests that need one.

Most of this suite is pure: adapters take a dataclass, the scorer takes a list
of candidates, the parsers take strings.  That covers the subtle logic well and
it is why the suite runs in three seconds.

It also let a scoring rule go unreachable.  ``score_piece`` and
``persist.recompute`` had drifted into two versions of the same rules, and only
``score_piece`` was tested -- so a title full of replacement characters was
asserted to stay out of auto-accept while the path that actually writes to the
catalogue accepted it.  Nothing pure could have caught that, because the fault
was in the seam between two tested things.

These fixtures exist for the seams.  They need PostgreSQL: the models use JSONB
and the queue uses ``FOR UPDATE SKIP LOCKED``, so SQLite would be testing
something else.  Point ``SMS_TEST_DATABASE_URL`` at a scratch database; if none
is reachable, the tests that need it skip rather than fail, so a checkout with
no database still runs the rest of the suite.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DEFAULT_URL = "postgresql+psycopg://sms:sms@localhost:5433/sms_test"


def _url() -> str:
    return os.environ.get("SMS_TEST_DATABASE_URL", DEFAULT_URL)


@pytest.fixture(scope="session")
def engine():
    """A scratch database with the schema built from the models.

    Built with ``create_all`` rather than by running the migrations: this is
    testing behaviour, not the upgrade path, and a migration chain that has to
    be replayed for every test run is a tax on writing tests at all.
    """
    from sms.models import Base

    engine = create_engine(_url(), future=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:                          # no database to hand
        pytest.skip(f"no test database at {_url()}: {exc}")

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine) -> Session:
    """One test's session, rolled back afterwards.

    The outer transaction is never committed, so tests can call code that
    commits internally without leaking rows into the next test.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def collection(session):
    """A collection to hang files on, with the default thresholds."""
    from sms.models import Collection

    row = Collection(name="Test Collection", source_path="/nowhere", adapter="generic")
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def source_file(session, collection):
    from sms.models import SourceFile

    row = SourceFile(
        collection_id=collection.id,
        rel_path="folder/example.pdf",
        page_count=10,
        size=1234,
        sha256="a" * 64,
    )
    session.add(row)
    session.flush()
    return row

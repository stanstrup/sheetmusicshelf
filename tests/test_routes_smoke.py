"""Every route, once, against a real database.

Not assertions about what the pages say -- assertions that they answer at all.
The curation text endpoint returned 410 for the entire catalogue because it
built its own path from a directory that had stopped being mounted, and
nothing noticed, because no test had ever called it. Twenty lines of this
would have.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sms.models import Collection, Composer, Piece, Shelf, SourceFile, Work


@pytest.fixture
def client(engine, monkeypatch):
    """The app, wired to the test database and with auth out of the way."""
    monkeypatch.setenv("SMS_AUTH_DISABLED", "true")
    monkeypatch.setenv("SMS_DEBUG", "true")
    monkeypatch.setenv("SMS_DATABASE_URL", str(engine.url))

    from sms import config, db

    config.get_settings.cache_clear()
    monkeypatch.setattr(db, "_engine", engine, raising=False)

    from sms.main import create_app

    app = create_app()

    from sms.db import get_session

    def _session():
        from sqlalchemy.orm import Session

        connection = engine.connect()
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
            connection.close()

    app.dependency_overrides[get_session] = _session
    with TestClient(app) as test_client:
        yield test_client
    config.get_settings.cache_clear()


@pytest.fixture
def catalogued(engine):
    """One of everything, committed, so the routes have something to render."""
    from sqlalchemy.orm import Session

    with Session(engine) as setup:
        composer = Composer(canonical_name="Edvard Grieg", sort_name="Grieg, Edvard")
        collection = Collection(name="Smoke", source_path="/nowhere", adapter="generic")
        setup.add_all([composer, collection])
        setup.flush()
        work = Work(composer_id=composer.id, title="Concerto in A minor")
        file_row = SourceFile(collection_id=collection.id, rel_path="a/b.pdf", page_count=4, size=9)
        setup.add_all([work, file_row])
        setup.flush()
        piece = Piece(
            source_file_id=file_row.id, work_id=work.id, page_start=1, page_end=4,
            title="Concerto in A minor", composer_name="Edvard Grieg",
            confidence=0.9, route="accept",
        )
        shelf = Shelf(name="Learning now")
        setup.add_all([piece, shelf])
        setup.commit()
        ids = {
            "piece": piece.id, "work": work.id, "composer": composer.id,
            "collection": collection.id, "file": file_row.id, "shelf": shelf.id,
        }
    yield ids
    with Session(engine) as teardown:
        for model in (Piece, Work, SourceFile, Shelf, Collection, Composer):
            for row in teardown.scalars(__import__("sqlalchemy").select(model)):
                teardown.delete(row)
        teardown.commit()


PAGES = ["/", "/review", "/shelves", "/manifest.webmanifest", "/sw.js"]


class TestPagesAnswer:
    @pytest.mark.parametrize("path", PAGES)
    def test_a_page_renders(self, client, catalogued, path):
        assert client.get(path).status_code == 200

    def test_the_pages_that_need_an_id(self, client, catalogued):
        for path in (
            f"/piece/{catalogued['piece']}",
            f"/work/{catalogued['work']}",
            f"/composer/{catalogued['composer']}",
            f"/read/{catalogued['piece']}",
            f"/shelf/{catalogued['shelf']}",
            f"/split/{catalogued['file']}",
        ):
            assert client.get(path).status_code == 200, path

    def test_a_missing_piece_is_a_404_not_a_crash(self, client, catalogued):
        assert client.get("/piece/999999").status_code == 404


class TestApiAnswers:
    def test_the_read_endpoints(self, client, catalogued):
        for path in (
            "/api/v1/pieces",
            "/api/v1/facets",
            "/api/v1/collections",
            "/api/v1/collections/adapters",
            "/api/v1/composers",
            "/api/v1/shelves",
            "/api/v1/curation/summary",
            "/api/v1/curation/queue",
            f"/api/v1/pieces/{catalogued['piece']}",
            f"/api/v1/curation/pieces/{catalogued['piece']}",
            f"/api/v1/collections/{catalogued['collection']}",
            f"/api/v1/collections/{catalogued['collection']}/jobs",
            f"/api/v1/composers/{catalogued['composer']}",
            f"/api/v1/pieces/{catalogued['piece']}/annotations",
        ):
            assert client.get(path).status_code == 200, path

    def test_the_text_endpoint_reports_a_missing_file_honestly(self, client, catalogued):
        """410 is right when the PDF is genuinely absent -- as it is here.

        What was wrong was returning it for every piece in a catalogue whose
        files were all present, because the path was built the one way that
        no longer resolved.
        """
        response = client.get(f"/api/v1/curation/pieces/{catalogued['piece']}/text")
        assert response.status_code == 410

    def test_the_instrument_filter_is_a_real_filter(self, client, catalogued):
        # Three-quarters of "filter by composer, form, instrument" used to work.
        assert "instrument" in client.get("/api/v1/facets").json()
        assert client.get("/api/v1/pieces?instrument=tuba").json() == []

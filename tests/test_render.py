"""Page rendering, including the concurrency that broke it in production."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from sms.config import get_settings
from sms.render import DEFAULT_WIDTH, WIDTHS, RenderUnavailable, cache_path, clamp_width, render_page


@pytest.fixture
def pdf(tmp_path):
    """A small multi-page PDF, built without touching the real library."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=612, height=792)
    path = tmp_path / "fixture.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    return path


@pytest.fixture(autouse=True)
def cache_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("SMS_CACHE_ROOT", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestWidths:
    def test_snapped_to_a_supported_width(self):
        # An open width parameter would let a client fill the cache volume with
        # near-identical images.
        assert clamp_width(1190) == 1200
        assert clamp_width(99999) == max(WIDTHS)
        assert clamp_width(None) == DEFAULT_WIDTH

    def test_cache_path_is_sharded(self):
        path = cache_path("abcdef0123", 3, 800)
        assert path.parent.name == "abcdef0123"
        assert path.parent.parent.name == "ab"
        assert path.name == "0003@800.webp"


class TestRender:
    def test_renders_and_caches(self, pdf):
        first = render_page(pdf, 1, width=320, key="k")
        assert first.exists() and first.stat().st_size > 0
        mtime = first.stat().st_mtime_ns
        second = render_page(pdf, 1, width=320, key="k")
        # A second request is served from the cache, not rasterised again.
        assert second == first and second.stat().st_mtime_ns == mtime

    def test_page_out_of_range(self, pdf):
        with pytest.raises(RenderUnavailable):
            render_page(pdf, 99, width=320, key="k")

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            render_page(tmp_path / "nope.pdf", 1, width=320, key="k")

    def test_concurrent_renders_do_not_corrupt_the_library(self, pdf):
        # Regression: PDFium keeps process-global state and is not thread-safe.
        # FastAPI runs sync endpoints in a threadpool, so a browse page asking
        # for sixty thumbnails rendered them concurrently -- which broke PDFium
        # so completely that every later load failed with "Data format error"
        # until the process was restarted.
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [
                pool.submit(render_page, pdf, (i % 4) + 1, width=320, key=f"c{i}")
                for i in range(24)
            ]
            for future in as_completed(futures):
                assert future.result().exists()

        # The library must still be usable afterwards.
        assert render_page(pdf, 2, width=800, key="after").exists()

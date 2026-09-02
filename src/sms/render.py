"""Rendering PDF pages to images, cached.

Why server-side rendering rather than PDF.js in the browser: **87% of this
library has no text layer**, so the things a JS PDF viewer buys you -- text
selection, in-document search, reflow -- do not exist for most of the
collection anyway.  Rendering to images instead means

* no CDN dependency, which matters on a phone reaching the server over a VPN
  with no route to the open internet;
* the same code path produces the page thumbnails the anthology page-range
  editor needs later;
* a scanned page arrives as one small WebP instead of a multi-megabyte PDF.

Renders are cached on the cache volume, keyed by file hash, so a page is
rasterised once and the NUC never repeats the work.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import get_settings

log = logging.getLogger("sms.render")

#: Widths the reader is allowed to ask for.  A closed set, because each one is
#: a separate cached rasterisation and an open parameter would let a client
#: fill the cache volume with near-identical images.
WIDTHS = (320, 800, 1200, 1800)
THUMB_WIDTH = 320
DEFAULT_WIDTH = 1200


class RenderUnavailable(RuntimeError):
    """pypdfium2 is not installed, or the page could not be rasterised."""


def clamp_width(width: int | None) -> int:
    if width is None:
        return DEFAULT_WIDTH
    return min(WIDTHS, key=lambda candidate: abs(candidate - width))


def cache_path(key: str, page: int, width: int) -> Path:
    """Shard by the first two hex characters: one flat directory holding tens of
    thousands of files is slow to list and unpleasant to inspect."""
    root = get_settings().cache_root / "pages" / key[:2] / key
    return root / f"{page:04d}@{width}.webp"


def render_page(pdf_path: Path, page: int, *, width: int, key: str) -> Path:
    """Rasterise one 1-based page, returning the cached file.

    Raises :class:`FileNotFoundError` if the PDF is gone and
    :class:`RenderUnavailable` if it cannot be rasterised.
    """
    width = clamp_width(width)
    target = cache_path(key, page, width)
    if target.exists() and target.stat().st_size > 0:
        return target

    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - deployment guard
        raise RenderUnavailable("pypdfium2 is not installed") from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    document = None
    try:
        document = pdfium.PdfDocument(str(pdf_path))
        if not 1 <= page <= len(document):
            raise RenderUnavailable(f"page {page} outside 1..{len(document)}")

        pdf_page = document[page - 1]
        # pdfium renders at 72 dpi * scale; derive scale from the target width
        # so a page is legible regardless of its physical size.
        page_width = pdf_page.get_width() or 612
        scale = max(width / page_width, 0.1)
        bitmap = pdf_page.render(scale=min(scale, 6.0))
        image = bitmap.to_pil()
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        # Scores are line art: quality 82 is indistinguishable here and roughly
        # a third the size of the lossless encode.
        image.save(target, format="WEBP", quality=82, method=4)
    except RenderUnavailable:
        raise
    except Exception as exc:                       # noqa: BLE001 - surfaced as 500 by the caller
        log.warning("render failed for %s page %s: %s", pdf_path.name, page, exc)
        target.unlink(missing_ok=True)
        raise RenderUnavailable(str(exc)) from exc
    finally:
        if document is not None:
            document.close()

    return target


def purge(key: str) -> int:
    """Drop every cached render for one file.  Returns the number removed."""
    root = get_settings().cache_root / "pages" / key[:2] / key
    if not root.is_dir():
        return 0
    removed = 0
    for path in root.glob("*.webp"):
        path.unlink(missing_ok=True)
        removed += 1
    try:
        root.rmdir()
    except OSError:
        pass
    return removed

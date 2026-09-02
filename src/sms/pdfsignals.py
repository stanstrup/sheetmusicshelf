"""Raw signal extraction from a PDF.

Strictly observation, no interpretation: this module never decides what a file
*is*, it only reports what the file physically contains.  Every judgement lives
in an adapter, so that re-running ingest with a better adapter needs no
re-reading of 3,686 files over SMB.
"""

from __future__ import annotations

import hashlib
import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path

warnings.filterwarnings("ignore", module="pypdf")
logging.getLogger("pypdf").setLevel(logging.ERROR)

from pypdf import PdfReader  # noqa: E402
from pypdf.generic import IndirectObject  # noqa: E402

# Pages are read for text only up to this many, and only when asked -- a full
# text pass over a 378-page scan is wasted work on an image-only library.
DEFAULT_TEXT_PAGES = 2
_HASH_CHUNK = 1 << 20


@dataclass(slots=True)
class OutlineEntry:
    title: str
    page_index: int | None   # 0-based; None when the destination will not resolve
    level: int = 0


@dataclass(slots=True)
class FileSignals:
    """Everything observable about one PDF, before anyone interprets it."""

    path: Path
    rel_path: str
    size: int
    mtime: float
    page_count: int
    sha256: str = ""
    docinfo: dict[str, str] = field(default_factory=dict)
    outline: list[OutlineEntry] = field(default_factory=list)
    page_text: dict[int, str] = field(default_factory=dict)   # 0-based page -> text
    error: str = ""

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def parent_name(self) -> str:
        return self.path.parent.name

    @property
    def has_text_layer(self) -> bool:
        return any(len(t.strip()) > 40 for t in self.page_text.values())

    @property
    def subject(self) -> str:
        return self.docinfo.get("/Subject", "")

    @property
    def producer_title(self) -> str:
        return self.docinfo.get("/Title", "")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _decode(value: object) -> str:
    """DocInfo values arrive as str, bytes or indirect objects, inconsistently."""
    if isinstance(value, IndirectObject):
        value = value.get_object()
    if isinstance(value, bytes):
        for encoding in ("utf-8", "utf-16", "latin-1"):
            try:
                return value.decode(encoding).rstrip("\x00").strip()
            except UnicodeDecodeError:
                continue
        return ""
    if value is None:
        return ""
    return str(value).rstrip("\x00").strip()


def _walk_outline(node: object, reader: PdfReader, level: int, out: list[OutlineEntry]) -> None:
    if isinstance(node, list):
        for child in node:
            _walk_outline(child, reader, level + 1 if not isinstance(child, list) else level, out)
        return
    title = _decode(getattr(node, "title", None) or (node.get("/Title") if isinstance(node, dict) else None))
    if not title:
        return
    page_index: int | None = None
    try:
        page_index = reader.get_destination_page_number(node)  # type: ignore[arg-type]
    except Exception:
        page_index = None
    out.append(OutlineEntry(title=title.strip(), page_index=page_index, level=level))


def read_signals(
    path: Path,
    root: Path,
    *,
    with_hash: bool = True,
    text_pages: int = DEFAULT_TEXT_PAGES,
) -> FileSignals:
    """Read one PDF.  Never raises -- a broken file becomes a signal with .error set."""
    stat = path.stat()
    try:
        rel = str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel = path.name

    signals = FileSignals(
        path=path,
        rel_path=rel,
        size=stat.st_size,
        mtime=stat.st_mtime,
        page_count=0,
    )

    try:
        reader = PdfReader(str(path))
        signals.page_count = len(reader.pages)
        signals.docinfo = {k: _decode(v) for k, v in (reader.metadata or {}).items()}

        try:
            outline = reader.outline
        except Exception:
            outline = None
        if outline:
            _walk_outline(outline, reader, 0, signals.outline)

        for index in range(min(text_pages, signals.page_count)):
            try:
                signals.page_text[index] = reader.pages[index].extract_text() or ""
            except Exception:
                signals.page_text[index] = ""
    except Exception as exc:
        signals.error = f"{type(exc).__name__}: {exc}"

    if with_hash and not signals.error:
        try:
            signals.sha256 = sha256_file(path)
        except OSError as exc:
            signals.error = f"hash failed: {exc}"

    return signals


def read_all_text(path: Path, max_pages: int = 40) -> str:
    """Full text of a small, text-bearing PDF -- used for table-of-contents files."""
    try:
        reader = PdfReader(str(path))
    except Exception:
        return ""
    parts: list[str] = []
    for page in reader.pages[:max_pages]:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            parts.append("")
    return "\n".join(parts)

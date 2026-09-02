"""The managed tree.

Calibre-style organisation, with one deliberate difference: files are
**copied**, never moved.  The originals under ``Z:\\Books\\Music`` stay exactly
as they are until you decide otherwise, and the source mount is read-only so a
bug here cannot reach them.

Layout::

    <managed>/<Composer>/<Title> (<Cat.>)/<edition>.pdf     single-work files
    <managed>/_Books/<Collection>/<original path>           multi-piece books

The split exists because a 378-page volume holding sixty pieces cannot sit in
one work's folder without lying about what it is.  Books keep their original
relative path so the collection stays recognisable.
"""

from __future__ import annotations

import logging
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Collection, Piece, SourceFile

log = logging.getLogger("sms.library")

BOOKS_DIR = "_Books"
UNFILED = "_Unfiled"

# Windows forbids these outright; the NAS is reached over SMB, so the stricter
# rule is the one that applies even though the container runs Linux.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRAILING = re.compile(r"[. ]+$")
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_COMPONENT = 110


def safe_component(text: str, fallback: str = "Unknown") -> str:
    """Turn arbitrary metadata into one safe path component."""
    text = unicodedata.normalize("NFC", (text or "").strip())
    text = _ILLEGAL.sub("-", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    text = _TRAILING.sub("", text)
    if len(text) > MAX_COMPONENT:
        text = text[:MAX_COMPONENT].rstrip(" -")
    if not text:
        return fallback
    if text.upper() in _RESERVED or text.split(".")[0].upper() in _RESERVED:
        text = f"_{text}"
    return text


def is_book(file_row: SourceFile, pieces: list[Piece]) -> bool:
    """A file holding several distinct pieces is a book, not a single work."""
    return len(pieces) > 1


def target_path(
    managed_root: Path,
    collection: Collection,
    file_row: SourceFile,
    pieces: list[Piece],
) -> Path:
    """Where this file belongs in the managed tree."""
    if is_book(file_row, pieces):
        rel = Path(file_row.rel_path)
        return managed_root / BOOKS_DIR / safe_component(collection.name) / rel

    piece = pieces[0] if pieces else None
    composer = safe_component(piece.composer_name if piece else "", UNFILED)
    if piece is None or not piece.title:
        # Nothing identified yet: park it under the collection rather than
        # inventing a folder name that a later review would have to undo.
        return managed_root / UNFILED / safe_component(collection.name) / Path(file_row.rel_path).name

    title = piece.title
    if piece.catalog_display:
        title = f"{title} ({piece.catalog_display})"
    folder = safe_component(title, "Untitled")

    suffix = Path(file_row.rel_path).suffix or ".pdf"
    stem = safe_component(piece.edition or Path(file_row.rel_path).stem, "score")
    return managed_root / composer / folder / f"{stem}{suffix}"


def _unique(path: Path) -> Path:
    """Avoid clobbering a different file that wants the same name."""
    if not path.exists():
        return path
    for index in range(2, 100):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem} ({path.stat().st_mtime_ns}){path.suffix}")


@dataclass
class MaterialiseResult:
    planned: int = 0
    copied: int = 0
    skipped_unchanged: int = 0
    skipped_unreviewed: int = 0
    errors: list[str] = field(default_factory=list)
    bytes_copied: int = 0
    sample: list[tuple[str, str]] = field(default_factory=list)


def materialise(
    session: Session,
    collection: Collection,
    *,
    dry_run: bool = True,
    only_accepted: bool = True,
    limit: int | None = None,
) -> MaterialiseResult:
    """Copy a collection's files into the managed tree.

    ``dry_run`` is the default on purpose: the first thing you want from this
    is to see the tree it *would* build.  Nothing is ever moved or deleted, and
    a file already present with the right size is left alone so re-running is
    cheap.
    """
    settings = get_settings()
    managed_root = settings.managed_root
    source_root = Path(collection.source_path)
    result = MaterialiseResult()

    files = list(
        session.scalars(
            select(SourceFile)
            .where(SourceFile.collection_id == collection.id)
            .order_by(SourceFile.rel_path)
        )
    )

    for file_row in files:
        pieces = list(
            session.scalars(
                select(Piece).where(Piece.source_file_id == file_row.id).order_by(Piece.page_start)
            )
        )
        if not pieces:
            continue

        # Only file what has actually been settled, unless told otherwise --
        # otherwise the tree fills with folders named after guesses that
        # review will change.
        if only_accepted and not any(
            p.review_state == "accepted" or p.route == "accept" for p in pieces
        ):
            result.skipped_unreviewed += 1
            continue

        source = source_root / file_row.rel_path
        target = target_path(managed_root, collection, file_row, pieces)
        result.planned += 1
        if len(result.sample) < 12:
            result.sample.append((file_row.rel_path, str(target.relative_to(managed_root))))

        if limit is not None and result.copied >= limit:
            continue

        if target.exists() and target.stat().st_size == file_row.size:
            result.skipped_unchanged += 1
            if file_row.managed_path != str(target):
                file_row.managed_path = str(target)
            continue

        if dry_run:
            continue

        try:
            if not source.exists():
                result.errors.append(f"missing on disk: {file_row.rel_path}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            destination = _unique(target) if target.exists() else target
            # copy2 keeps mtime, so a later integrity check can compare
            # timestamps against the original.
            shutil.copy2(source, destination)
            file_row.managed_path = str(destination)
            result.copied += 1
            result.bytes_copied += file_row.size
        except OSError as exc:
            result.errors.append(f"{file_row.rel_path}: {exc}")

    return result

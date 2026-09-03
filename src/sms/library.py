"""The managed tree.

Calibre's arrangement: the library holds the files, and where a file came from
stops mattering once it is in. Material arrives one of two ways.

An existing collection is **copied** in, and the originals are left exactly as
they are -- that mount is read-only, so a bug here cannot reach them. Anything
dropped in the ingest folder is copied in and then removed from the drop, the
way Calibre empties its auto-add folder: an ingest folder that never empties is
not a queue, and re-running would import everything twice.

Layout::

    <managed>/<Composer>/<Title> (<Cat.>)/<edition>.pdf     single-work files
    <managed>/_Books/<Collection>/<original path>           multi-piece books

The split exists because a 378-page volume holding sixty pieces cannot sit in
one work's folder without lying about what it is.  Books keep their original
relative path so the collection stays recognisable.

A folder name here is made from metadata, so review changing a title or a
composer means the file has to *move*. See :func:`_refile`: copying again
instead would leave a folder behind for every name a piece ever had.
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
    #: Copies already in the tree that review has since renamed.
    refiled: int = 0


#: The collection new material lands in.  One collection rather than one per
#: drop: after import the file lives in the library, and where it happened to
#: be dropped is not a thing anyone browses by.
INGEST_COLLECTION = "Ingest"


def ingest_collection(session: Session, root: Path) -> Collection:
    """The collection that owns the ingest folder, made on first use."""
    collection = session.scalar(select(Collection).where(Collection.name == INGEST_COLLECTION))
    if collection is None:
        collection = Collection(name=INGEST_COLLECTION, source_path=str(root))
        session.add(collection)
        session.flush()
    elif collection.source_path != str(root):
        collection.source_path = str(root)
    return collection


def clear_ingested(session: Session, collection: Collection, root: Path) -> tuple[int, list[str]]:
    """Remove from the ingest folder the files now safely in the library.

    Checked against the copy actually on disk rather than against the database,
    because the whole point of an ingest folder is that removing something from
    it is only safe once the library really holds it.
    """
    removed, errors = 0, []
    for file_row in session.scalars(
        select(SourceFile).where(SourceFile.collection_id == collection.id)
    ):
        dropped = root / file_row.rel_path
        if not dropped.exists():
            continue
        managed = Path(file_row.managed_path) if file_row.managed_path else None
        if managed is None or not managed.exists():
            continue
        if managed.stat().st_size != dropped.stat().st_size:
            errors.append(f"{file_row.rel_path}: library copy differs in size; left in place")
            continue
        try:
            dropped.unlink()
            _prune_empty(dropped.parent, root)
            removed += 1
        except OSError as exc:
            errors.append(f"{file_row.rel_path}: {exc}")
    return removed, errors


def _claimed_by_another(session: Session, target: Path, file_row: SourceFile) -> bool:
    """Whether a different catalogue row already holds this exact path."""
    other = session.scalar(
        select(SourceFile.id).where(
            SourceFile.managed_path == str(target),
            SourceFile.id != file_row.id,
        ).limit(1)
    )
    return other is not None


def _refile(current: Path, target: Path, managed_root: Path) -> None:
    """Move a copy the library already holds to the name it now deserves."""
    target.parent.mkdir(parents=True, exist_ok=True)
    destination = _unique(target) if target.exists() else target
    shutil.move(str(current), str(destination))
    _prune_empty(current.parent, managed_root)


def _prune_empty(folder: Path, stop_at: Path) -> None:
    """Remove folders a move has emptied, up to but never including the root.

    A composer folder left behind after their last piece was re-filed reads as
    a piece still being there.
    """
    try:
        stop = stop_at.resolve()
        here = folder.resolve()
    except OSError:
        return
    while here != stop and stop in here.parents:
        try:
            next(here.iterdir())
            return                          # not empty; nothing more to do
        except StopIteration:
            pass
        except OSError:
            return
        try:
            here.rmdir()
        except OSError:
            return
        here = here.parent

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

        # A copy the library already holds, under a name review has changed:
        # move it.  Copying again would leave the old name in place, so the
        # tree would accumulate a folder for every title a piece ever had.
        current = Path(file_row.managed_path) if file_row.managed_path else None
        if current is not None and current != target and current.exists():
            result.refiled += 1
            if not dry_run:
                try:
                    _refile(current, target, managed_root)
                    file_row.managed_path = str(target)
                except OSError as exc:
                    result.errors.append(f"{file_row.rel_path}: {exc}")
            continue

        # Adopting a file already at the target is what makes re-running cheap,
        # but only when it is *this* file.  Size alone is not identity: the pop
        # collection holds the same arrangement twice under different numbers,
        # and matching on size let the second row point at the first row's copy
        # and never get one of its own.
        claimed = _claimed_by_another(session, target, file_row)
        if target.exists() and target.stat().st_size == file_row.size and not claimed:
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
            destination = _unique(target) if (target.exists() or claimed) else target
            # copy2 keeps mtime, so a later integrity check can compare
            # timestamps against the original.
            shutil.copy2(source, destination)
            file_row.managed_path = str(destination)
            result.copied += 1
            result.bytes_copied += file_row.size
        except OSError as exc:
            result.errors.append(f"{file_row.rel_path}: {exc}")

    return result

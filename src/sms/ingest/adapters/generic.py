"""Fallback adapter: folder names and filename patterns.

Owns everything no specialised adapter claims.  It is deliberately timid --
its best signals top out below the auto-accept line on their own, so its output
lands in the review queue rather than quietly filling the catalogue with
guesses.
"""

from __future__ import annotations

import re
from pathlib import Path

from ...music import composers
from ...music.catalogs import parse_catalog, parse_catalog_loose
from ...music.keys import parse_key
from ...pdfsignals import FileSignals
from ..model import FileProposal, PieceProposal
from .base import Adapter, CollectionContext, register

# "134 - Eric Clapton - Tears In Heaven"  /  "elton_john - Can you feel..."
_NUMBERED = re.compile(r"^\s*(?P<index>\d{1,4})\s*[-_.]\s*(?P<rest>.+)$")
_ARTIST_TITLE = re.compile(r"^(?P<artist>[^-]{2,40}?)\s+-\s+(?P<title>.{2,})$")
_VERSION = re.compile(r"\s*\((?:v|ver|version)\s*\.?\s*(\d+)\)\s*$", re.I)


def _tidy(text: str) -> str:
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _titlecase_if_shouty(text: str) -> str:
    """"TOTAL ECLIPSE OF THE HEART" -> title case; leave mixed case alone."""
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        return text.title()
    return text


@register
class GenericAdapter(Adapter):
    name = "generic"

    W_FILENAME_ARTIST = 0.55
    W_FOLDER_COMPOSER = 0.70
    W_STUB_CATALOG = 0.50
    W_LOOSE_CATALOG = 0.40
    W_FILENAME_TITLE = 0.55
    W_OUTLINE = 0.80
    W_DOCINFO_TITLE = 0.45

    def detect(self, root: Path) -> float:
        return 0.05   # always available, never preferred

    def prepare(self, context: CollectionContext) -> None:
        record = composers.resolve(context.root.name)
        if record is not None:
            context.defaults["composer"] = record.canonical
            context.notes.append(f"collection composer from folder: {record.canonical}")

    def _composer_from_path(self, signals: FileSignals) -> str | None:
        """Walk up from the file looking for a folder that names a composer."""
        for part in reversed(Path(signals.rel_path).parent.parts):
            record = composers.resolve(part)
            if record is not None:
                return record.canonical
        return None

    def propose(self, signals: FileSignals, context: CollectionContext) -> FileProposal:
        proposal = FileProposal(rel_path=signals.rel_path, adapter=self.name)
        if self.should_ignore(signals.rel_path):
            proposal.skipped = "ignored by adapter glob"
            return proposal
        if signals.error:
            proposal.skipped = f"unreadable: {signals.error}"
            return proposal

        # Anthologies with a usable outline split into one piece per entry;
        # everything else is a single piece spanning the file.
        pieces = self._split_by_outline(signals)
        for piece in pieces:
            self._annotate(piece, signals, context)
        proposal.pieces.extend(pieces)
        if not pieces:
            proposal.skipped = "no pieces proposed"
        return proposal

    def _split_by_outline(self, signals: FileSignals) -> list[PieceProposal]:
        usable = [e for e in signals.outline if e.page_index is not None and e.title.strip()]
        # One bookmark called "Table of Contents" is not a piece list.
        meaningful = [e for e in usable if not re.match(r"^\s*(table of )?contents\s*$", e.title, re.I)]
        if len(meaningful) < 2 or signals.page_count < 4:
            piece = PieceProposal(page_start=1, page_end=max(signals.page_count, 1))
            piece.notes.append("whole-file")
            return [piece]

        meaningful.sort(key=lambda e: e.page_index or 0)
        pieces: list[PieceProposal] = []
        for position, entry in enumerate(meaningful):
            start = (entry.page_index or 0) + 1
            if position + 1 < len(meaningful):
                end = (meaningful[position + 1].page_index or 0)
                end = max(end, start)
            else:
                end = signals.page_count
            piece = PieceProposal(page_start=start, page_end=max(end, start))
            piece.add("title", _tidy(entry.title), "pdf_outline", self.W_OUTLINE)
            key = parse_key(entry.title)
            if key is not None:
                piece.add("key", key.canonical, "pdf_outline", self.W_OUTLINE - 0.10)
            catalog = parse_catalog(entry.title)
            if catalog is not None:
                piece.add("catalog", catalog.canonical, "pdf_outline", self.W_OUTLINE - 0.05)
            pieces.append(piece)
        return pieces

    def _annotate(self, piece: PieceProposal, signals: FileSignals, context: CollectionContext) -> None:
        stem = _tidy(signals.stem)
        version = _VERSION.search(stem)
        if version:
            stem = _VERSION.sub("", stem).strip()
            piece.notes.append(f"filename marks version {version.group(1)}")

        numbered = _NUMBERED.match(stem)
        if numbered:
            stem = numbered.group("rest").strip()
            piece.notes.append(f"collection index {numbered.group('index')}")

        whole_file = len(signals.outline) < 2 or piece.get("title") is None

        pair = _ARTIST_TITLE.match(stem)
        if pair and whole_file:
            artist = _titlecase_if_shouty(_tidy(pair.group("artist")))
            title = _titlecase_if_shouty(_tidy(pair.group("title")))
            piece.add("composer", composers.canonical_or_raw(artist), "filename_pattern", self.W_FILENAME_ARTIST)
            piece.add("title", title, "filename_pattern", self.W_FILENAME_TITLE)
        elif whole_file and stem:
            loose = parse_catalog_loose(signals.stem)
            if loose is not None:
                catalog, kind = loose
                weight = self.W_STUB_CATALOG if kind == "stub" else self.W_LOOSE_CATALOG
                piece.add("catalog", catalog.canonical, f"filename_{kind}", weight)
                # A pure catalogue stub is not a title; leave the title unset so
                # the piece is held rather than catalogued as "bwv772".
                if kind != "stub":
                    piece.add("title", _titlecase_if_shouty(stem), "filename_stem", 0.35)
            else:
                piece.add("title", _titlecase_if_shouty(stem), "filename_stem", self.W_FILENAME_TITLE - 0.10)

        folder_composer = self._composer_from_path(signals) or context.defaults.get("composer")
        if isinstance(folder_composer, str):
            piece.add("composer", folder_composer, "folder_composer", self.W_FOLDER_COMPOSER)

        docinfo_title = signals.docinfo.get("/Title", "").strip()
        if docinfo_title and not docinfo_title.lower().startswith(("microsoft", "untitled", "acrobat")):
            piece.add("title", _tidy(docinfo_title), "docinfo_title", self.W_DOCINFO_TITLE)

        instrumentation = context.defaults.get("instrumentation")
        if isinstance(instrumentation, str):
            piece.add("instrumentation", instrumentation, "collection_default", self.default_weight)

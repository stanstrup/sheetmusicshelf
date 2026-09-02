"""Adapter for a flat folder of popular-song lead sheets.

Measured over the 477 files in ``Sheet Music Collection``: 87% are named
``NNN - Artist - Title``, 13% are ``NNN - Title`` with no artist at all, 12.5%
have a text layer and 15% a DocInfo title.  The filename is effectively the
only signal.

**The ordering is not reliable.**  ``477 - The Beatles - Michelle`` is
artist-first, but ``537 - Fernando - Abba`` is title-first -- the same shape,
the opposite meaning.  Nothing in the file distinguishes them, so this adapter
proposes the common reading at a weight that keeps it in review and *records
the ambiguity as a note* rather than pretending to be sure.  A reviewer looking
at the page sees the answer printed on it in a second.
"""

from __future__ import annotations

import re
from pathlib import Path

from ...music import composers
from ...pdfsignals import FileSignals
from ..model import FileProposal, PieceProposal
from .base import Adapter, CollectionContext, register

# "1002 - Beauty and the Beast (v2)"
_INDEX = re.compile(r"^\s*(?P<index>\d{1,4})\s*[-_.]\s*(?P<rest>.+)$")
# Both "(v2)" and a bare trailing "v2" are used in this folder.
_VERSION = re.compile(r"[\s(]*\b(?:v|ver|version)\s*\.?\s*(?P<n>\d+)\)?\s*$", re.I)
_SPLIT = re.compile(r"\s+-\s+|\s-\s")

AMBIGUOUS_ORDER_NOTE = "filename order is ambiguous; may be Title - Artist"

#: Words that stay lowercase inside a title unless they open it.
_MINOR_WORDS = {
    "a", "an", "the", "and", "or", "but", "nor", "of", "in", "on", "at", "to",
    "for", "with", "from", "by", "as", "into", "onto", "over", "up", "vs",
}
_RUN_TOGETHER = re.compile(r"(?<=[a-z])(?=[A-Z])")


def respace(text: str) -> str:
    """Split run-together words: "TotalEclipseOfTheHeart" is unreadable."""
    if " " in text or len(text) < 8:
        return text
    return _RUN_TOGETHER.sub(" ", text)


def smart_title(text: str) -> str:
    """Title-case, keeping minor words lowercase inside the phrase."""
    words = text.split()
    out: list[str] = []
    for index, word in enumerate(words):
        lowered = word.lower()
        if index > 0 and lowered in _MINOR_WORDS:
            out.append(lowered)
        elif word.isupper() and len(word) <= 4:
            out.append(word)          # keep initialisms like ABBA, REM
        else:
            out.append(word[:1].upper() + word[1:].lower() if word else word)
    return " ".join(out)


def strip_index(stem: str) -> tuple[str, int | None]:
    match = _INDEX.match(stem)
    if not match:
        return stem.strip(), None
    return match.group("rest").strip(), int(match.group("index"))


def strip_version(stem: str) -> tuple[str, int | None]:
    match = _VERSION.search(stem)
    if not match:
        return stem.strip(), None
    return _VERSION.sub("", stem).strip(), int(match.group("n"))


def tidy(text: str) -> str:
    """Normalise a filename fragment into something presentable.

    This folder is inconsistent in every direction: ``elton_john``,
    ``TOTAL ECLIPSE``, ``TotalEclipseOfTheHeart`` and ``The Way We Were`` all
    appear.  Anything all-lower or all-upper is recased; text that already
    varies is left exactly as its author wrote it.
    """
    cleaned = re.sub(r"\s+", " ", respace((text or "").replace("_", " "))).strip(" -")
    letters = [c for c in cleaned if c.isalpha()]
    if not letters:
        return cleaned
    uppers = sum(c.isupper() for c in letters)
    if uppers == 0 or uppers / len(letters) > 0.8:
        return smart_title(cleaned)
    return cleaned


def parse_stem(stem: str) -> dict:
    """Split a lead-sheet filename into its parts.

    Returns ``artist`` and ``title`` under the *common* reading, plus the
    collection index, version number, and whether the reading is a guess.
    """
    rest, index = strip_index(stem)
    rest, version = strip_version(rest)
    parts = [tidy(part) for part in _SPLIT.split(rest) if tidy(part)]

    artist: str | None = None
    title: str | None = None
    ambiguous = False

    if len(parts) >= 2:
        artist, title = parts[0], " - ".join(parts[1:])
        # If the *second* half names a composer we know and the first does not,
        # the file is title-first and we can say so rather than guess.
        if composers.resolve(parts[-1]) is not None and composers.resolve(parts[0]) is None:
            artist, title = parts[-1], " - ".join(parts[:-1])
        else:
            ambiguous = True
    elif parts:
        title = parts[0]

    return {
        "artist": artist,
        "title": title,
        "index": index,
        "version": version,
        "ambiguous": ambiguous,
    }


@register
class PopCollectionAdapter(Adapter):
    name = "popcollection"

    #: Below the review floor is wrong -- the parse is usually right -- but
    #: well below auto-accept, because the artist/title order is a coin toss
    #: on any individual file.
    W_ARTIST = 0.55
    W_TITLE = 0.60
    W_DOCINFO_TITLE = 0.45

    def detect(self, root: Path) -> float:
        name = root.name.lower()
        if "sheet music collection" in name:
            return 0.9
        if not root.is_dir():
            return 0.0
        # A flat folder where most files are "NNN - something".
        pdfs = [p for p in root.glob("*.pdf")]
        if len(pdfs) < 20:
            return 0.0
        numbered = sum(1 for p in pdfs if _INDEX.match(p.stem))
        return 0.7 if numbered >= len(pdfs) * 0.6 else 0.0

    def prepare(self, context: CollectionContext) -> None:
        context.defaults["form"] = "Song"
        context.notes.append(
            "flat lead-sheet folder: the filename is the only signal, and its "
            "artist/title order is not reliable"
        )

    def propose(self, signals: FileSignals, context: CollectionContext) -> FileProposal:
        proposal = FileProposal(rel_path=signals.rel_path, adapter=self.name)
        if self.should_ignore(signals.rel_path):
            proposal.skipped = "ignored by adapter glob"
            return proposal
        if signals.error:
            proposal.skipped = f"unreadable: {signals.error}"
            return proposal

        parsed = parse_stem(signals.stem)
        piece = PieceProposal(page_start=1, page_end=max(signals.page_count, 1))
        piece.notes.append("whole-file")

        if parsed["index"] is not None:
            piece.notes.append(f"collection index {parsed['index']}")
        if parsed["version"] is not None:
            piece.notes.append(f"marked as version {parsed['version']}")
            piece.add("edition", f"version {parsed['version']}", "filename_version", 0.7)

        if parsed["title"]:
            piece.add("title", parsed["title"], "filename_pattern", self.W_TITLE)
        if parsed["artist"]:
            piece.add(
                "composer",
                composers.canonical_or_raw(parsed["artist"]),
                "filename_pattern",
                self.W_ARTIST,
            )
        else:
            proposal.warnings.append("no artist in filename")

        if parsed["ambiguous"]:
            piece.notes.append(AMBIGUOUS_ORDER_NOTE)

        # A DocInfo title is weak here (15% of files, often mangled) but it is a
        # genuinely separate source, so where it agrees it should count.
        docinfo = (signals.docinfo.get("/Title") or "").strip()
        if docinfo and not docinfo.lower().startswith(("microsoft", "untitled", "acrobat", "document")):
            piece.add("title", tidy(docinfo), "docinfo_title", self.W_DOCINFO_TITLE)

        form = context.defaults.get("form")
        if isinstance(form, str):
            piece.add("form", form, "collection_default", self.default_weight)

        proposal.pieces.append(piece)
        return proposal

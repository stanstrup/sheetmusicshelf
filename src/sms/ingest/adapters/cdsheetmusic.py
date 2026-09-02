"""Adapter for the CD Sheet Music discs.

These discs are the best-instrumented part of the library.  Every music file
carries a DocInfo ``/Subject`` in a consistent house grammar::

    Mozart:  Minuet in G Major, K1, P1 of 1
    Brahms:   Intermezzo, A Maj, Op118 #2, P380-383
    Haydn:  Sonata in Eb Major, Hoboken 28, P1-12

and each disc ships a text-bearing ``toc.pdf`` listing every piece under a form
heading.  The two are genuinely independent signals, so where they agree the
piece needs no human attention -- which is the whole point of the confidence
model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ...music import composers
from ...music.catalogs import PRIMARY_SYSTEM, CatalogNumber, parse_catalog, parse_catalog_loose
from ...music.keys import parse_key, parse_key_only
from ...pdfsignals import FileSignals, read_all_text
from ..model import FileProposal, PieceProposal
from .base import Adapter, CollectionContext, register

# --- the /Subject grammar -------------------------------------------------

# Trailing page spec: "P1 of 1", "P380-383", "P144-178"
_PAGES_RE = re.compile(r"^P\.?\s*(?P<start>\d+)\s*(?:of|-|–|—|to)\s*(?P<end>\d+)\s*$", re.I)
_PAGES_ONE = re.compile(r"^P\.?\s*(?P<start>\d+)\s*$", re.I)
# "page 10" -- a page *count*, not a position in a volume.
_PAGE_COUNT_RE = re.compile(r"^pages?\.?\s*\d+\s*$", re.I)

# A segment that is *nothing but* a catalogue number, so it can be lifted out
# of the title without damaging titles that legitimately contain one
# ("Study #2 after Weber Op24").
_CATALOG_ONLY = re.compile(
    r"^\s*(?:K\.?V?|BWV|Op(?:us)?|Hob(?:oken)?|D|HWV|WoO|Anh|S|BV|RV|Sz|L|JBP|MOZ)"
    r"\.?\s*\d{1,4}[a-z]?(?:\s*(?:no\.?|\#)\s*\d{1,3})?\s*$",
    re.I,
)


@dataclass(slots=True)
class Subject:
    composer: str = ""
    title: str = ""
    key: str = ""
    catalog: CatalogNumber | None = None
    printed_start: int | None = None
    printed_end: int | None = None
    raw: str = ""


def parse_subject(raw: str) -> Subject | None:
    """Parse one ``/Subject`` line.  Returns None when it is not a known grammar.

    Two grammars occur on these discs::

        Mozart:  Minuet in G Major, K1, P1 of 1     # composer-prefixed
        Variations, K. 455, page 10                 # no composer, "page N"

    The second leaves the composer to the collection default, which is exactly
    what the confidence model is for: such a piece scores lower and lands in
    review rather than being silently attributed.
    """
    if not raw or not raw.strip():
        return None

    composer = ""
    rest = raw
    if ":" in raw:
        head, _, tail = raw.partition(":")
        head = head.strip()
        # A composer, not a title fragment: short, and no sentence punctuation.
        if head and len(head) <= 40 and not head.endswith("."):
            composer, rest = head, tail

    segments = [seg.strip() for seg in rest.split(",")]
    segments = [seg for seg in segments if seg]
    if not segments:
        return None

    subject = Subject(composer=composer, raw=raw.strip())
    saw_pages = False

    # Pages come last, when present.
    match = _PAGES_RE.match(segments[-1]) or _PAGES_ONE.match(segments[-1])
    if match:
        segments.pop()
        saw_pages = True
        subject.printed_start = int(match.group("start"))
        subject.printed_end = int(match.groupdict().get("end") or match.group("start"))
    elif _PAGE_COUNT_RE.match(segments[-1]):
        # "page 10" is a length, not a location -- recorded as neither.
        segments.pop()
        saw_pages = True

    # Then a catalogue segment and/or a key segment, in either order.
    for _ in range(2):
        if not segments:
            break
        tail = segments[-1]
        if subject.catalog is None and _CATALOG_ONLY.match(tail):
            subject.catalog = parse_catalog(tail)
            segments.pop()
            continue
        if not subject.key:
            key = parse_key_only(tail)
            if key is not None:
                subject.key = key.canonical
                segments.pop()
                continue
        break

    subject.title = ", ".join(segments).strip()
    if not subject.title:
        return None

    # Without a composer prefix there is nothing to distinguish the house
    # grammar from a scanner's boilerplate, so the line must corroborate itself
    # with a catalogue number or a page spec before its title is trusted.
    if not subject.composer and subject.catalog is None and not saw_pages:
        return None

    if not subject.key:
        key = parse_key(subject.title)
        if key is not None:
            subject.key = key.canonical
    return subject


# --- the disc's table of contents -----------------------------------------

# "No. 1 in C Major ....... 279....... 10"   /   "No. 17 in F Major ..... 547a ..... 9"
_TOC_ROW = re.compile(
    r"^(?P<title>.+?)\s*\.{4,}\s*(?P<cat>\d[\w/]*)\s*\.{4,}\s*(?P<pages>\d+)\s*$"
)
_PAGE_MARKER = re.compile(r"^\s*-\s*[ivxlcdm\d]+\s*-\s*$", re.I)


@dataclass(slots=True)
class TocEntry:
    title: str
    form: str
    catalog_raw: str
    number: int
    pages: int


class TocIndex:
    """Catalogue number -> table-of-contents row for one disc.

    A disc's contents list is numbered in exactly one thematic catalogue.
    ``system`` records which, because the bare integers in the list would
    otherwise happily match a number from a different system entirely -- the
    Mozart disc's supplement files (MOZ. 1, MOZ. 2) were matching Köchel rows.
    """

    def __init__(self, system: str | None = None) -> None:
        self.by_number: dict[int, TocEntry] = {}
        self.entries: list[TocEntry] = []
        self.system = system

    def __len__(self) -> int:
        return len(self.entries)

    def add(self, entry: TocEntry) -> None:
        self.entries.append(entry)
        # First row wins: later duplicates are usually cross-references.
        self.by_number.setdefault(entry.number, entry)

    def covers(self, catalog: CatalogNumber | None) -> bool:
        """Whether this index is even the right catalogue to ask."""
        if catalog is None:
            return False
        return self.system is None or catalog.system == self.system

    def lookup(self, catalog: CatalogNumber | None) -> TocEntry | None:
        if not self.covers(catalog):
            return None
        assert catalog is not None
        return self.by_number.get(catalog.number)


def _singular(word: str) -> str:
    if word.lower().endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def clean_heading(text: str) -> str:
    """Tidy a contents heading.

    The discs' headings contain em-dashes that pypdf cannot decode, and some
    are prefixed with a count ("43 Short Pieces").  Neither belongs in a form
    label, but the heading's own words do -- they are the only place in the
    library where musical form is stated outright.
    """
    text = text.replace("�", "-").replace("—", "-").replace("–", "-")
    text = re.sub(r"\s*-\s*", " - ", text)
    text = re.sub(r"^\d+\s+", "", text.strip())
    return re.sub(r"\s+", " ", text).strip(" -")


def instrumentation_from_heading(heading: str) -> str | None:
    """Read scoring off a contents heading, when it is unambiguous.

    "Works for Piano - Four Hands or Two Pianos" names two different scorings,
    so it names neither: the piece goes to review rather than being filed as
    whichever was checked first.
    """
    lowered = heading.lower()
    four_hands = "four hands" in lowered or "4 hands" in lowered
    two_pianos = "two pianos" in lowered or "2 pianos" in lowered
    if four_hands and two_pianos:
        return None
    if four_hands:
        return "piano four hands"
    if two_pianos:
        return "two pianos"
    return None


def heading_is_ambiguous_scoring(heading: str) -> bool:
    lowered = heading.lower()
    return ("four hands" in lowered or "4 hands" in lowered) and (
        "two pianos" in lowered or "2 pianos" in lowered
    )


def parse_toc(text: str) -> TocIndex:
    """Read a disc's toc.pdf text into an index.

    Headings ("Sonatas and Fantasies") carry the musical form, which is the
    only place in the whole library that information is stated outright.
    """
    index = TocIndex()
    form = ""
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip() or _PAGE_MARKER.match(line):
            continue

        row = _TOC_ROW.match(line.strip())
        if row:
            raw_cat = row.group("cat")
            primary = re.match(r"(\d+)", raw_cat)
            if not primary:
                continue
            title = row.group("title").strip(" .")
            if form and re.match(r"^(no\.|nos\.)\s*\d", title, re.I):
                # "No. 1 in C Major" under "Sonatas and Fantasies" is a Sonata.
                title = f"{_singular(form.split(' and ')[0].split(' - ')[0].strip())} {title}"
            index.add(TocEntry(
                title=title,
                form=form,
                catalog_raw=raw_cat,
                number=int(primary.group(1)),
                pages=int(row.group("pages")),
            ))
            continue

        stripped = line.strip()
        if "..." not in stripped and 3 <= len(stripped) <= 70 and "TITLE" not in stripped:
            # A short, dotless line between rows is a form heading.
            if not re.search(r"[.:]$", stripped) and not stripped.lower().startswith(("instruction", "for faster", "by opening", "note:")):
                form = clean_heading(stripped)
    return index


# --- reconciling printed page numbers with file pages ---------------------

def reconcile_pages(
    printed_start: int | None,
    printed_end: int | None,
    page_count: int,
) -> tuple[int, int, int | None, int | None, str]:
    """Map a printed page range onto file-relative pages.

    ``/Subject`` reports the pages *in the printed volume* -- Brahms "P380-383"
    lives on pages 1-4 of a four-page file.  Conflating the two would send the
    reader to page 380 of a four-page PDF, so they are kept apart deliberately.
    """
    if page_count <= 0:
        return 1, 1, printed_start, printed_end, "unknown-page-count"
    if printed_start is None or printed_end is None:
        return 1, page_count, None, None, "whole-file"
    span = printed_end - printed_start + 1
    # Printed numbers starting at 1 tell the reader nothing the file does not
    # already say, so they are not recorded as volume pagination.
    keep_printed = printed_start > 1
    if span == page_count:
        if keep_printed:
            return 1, page_count, printed_start, printed_end, "whole-file"
        return 1, page_count, None, None, "whole-file"
    if printed_start == 1 and printed_end <= page_count:
        return printed_start, printed_end, None, None, ""
    return 1, page_count, printed_start, printed_end, "printed-range-mismatch"


# --- the adapter ----------------------------------------------------------

@register
class CdSheetMusicAdapter(Adapter):
    name = "cdsheetmusic"

    W_SUBJECT = 0.85
    W_TOC = 0.75
    W_TOC_PAGES = 0.45
    W_FOLDER_COMPOSER = 0.70
    W_STUB_CATALOG = 0.50
    W_LOOSE_CATALOG = 0.40
    W_FORM = 0.65

    ignore_globs = Adapter.ignore_globs + ("*/toc.pdf", "toc.pdf")

    def detect(self, root: Path) -> float:
        name = root.name.lower()
        if name.startswith("cd sheet music"):
            return 0.95
        if (root / "toc.pdf").exists() and (root / "works").is_dir():
            return 0.85
        return 0.0

    def prepare(self, context: CollectionContext) -> None:
        root = context.root

        toc_path = next((p for p in (root / "toc.pdf", root / "music" / "toc.pdf") if p.exists()), None)
        if toc_path is not None:
            index = parse_toc(read_all_text(toc_path))
            context.data["toc"] = index
            context.notes.append(f"table of contents: {len(index)} rows from {toc_path.name}")
        else:
            context.notes.append("no toc.pdf found -- relying on /Subject alone")

        # "Mozart - The Complete Works for Piano" -> composer Mozart, solo piano.
        folder = root.name
        lead = folder.split(" - ")[0].strip()
        lead = re.sub(r"^CD Sheet Music\s*[-–]?\s*", "", lead, flags=re.I).strip()
        record = composers.resolve(lead)
        if record is not None:
            context.defaults["composer"] = record.canonical
            context.notes.append(f"collection composer: {record.canonical}")
            # Tell the contents list which catalogue its bare numbers belong to.
            index = context.data.get("toc")
            system = PRIMARY_SYSTEM.get(record.canonical)
            if isinstance(index, TocIndex) and system:
                index.system = system
                context.notes.append(f"table of contents numbered in {system}.")
        lowered = folder.lower()
        if "piano" in lowered and "duet" not in lowered:
            context.defaults["instrumentation"] = "solo piano"
        elif "duet" in lowered:
            context.defaults["instrumentation"] = "piano four hands"
        elif "organ" in lowered:
            context.defaults["instrumentation"] = "organ"

    def propose(self, signals: FileSignals, context: CollectionContext) -> FileProposal:
        proposal = FileProposal(rel_path=signals.rel_path, adapter=self.name)
        if self.should_ignore(signals.rel_path):
            proposal.skipped = "ignored by adapter glob"
            return proposal
        if signals.error:
            proposal.skipped = f"unreadable: {signals.error}"
            return proposal

        subject = parse_subject(signals.subject)
        catalog: CatalogNumber | None = subject.catalog if subject else None

        # The filename stem is an independent read of the catalogue number.
        loose = parse_catalog_loose(signals.stem)
        stem_catalog, stem_kind = (loose if loose else (None, ""))
        if catalog is None:
            catalog = stem_catalog

        printed_start = subject.printed_start if subject else None
        printed_end = subject.printed_end if subject else None
        start, end, printed_first, printed_last, note = reconcile_pages(
            printed_start, printed_end, signals.page_count
        )

        piece = PieceProposal(
            page_start=start,
            page_end=end,
            printed_first_page=printed_first,
            printed_last_page=printed_last,
        )
        if note:
            piece.notes.append(note)

        if subject is not None:
            piece.add("composer", composers.canonical_or_raw(subject.composer), "docinfo_subject", self.W_SUBJECT)
            piece.add("title", subject.title, "docinfo_subject", self.W_SUBJECT)
            piece.add("key", subject.key, "docinfo_subject", self.W_SUBJECT - 0.05)
            if subject.catalog is not None:
                piece.add("catalog", subject.catalog.canonical, "docinfo_subject", self.W_SUBJECT)
        else:
            proposal.warnings.append("no parsable /Subject")

        if stem_catalog is not None:
            weight = self.W_STUB_CATALOG if stem_kind == "stub" else self.W_LOOSE_CATALOG
            piece.add("catalog", stem_catalog.canonical, f"filename_{stem_kind}", weight)

        composer_default = context.defaults.get("composer")
        if isinstance(composer_default, str):
            piece.add("composer", composer_default, "collection_default", self.W_FOLDER_COMPOSER)
        entry: TocEntry | None = None
        index = context.data.get("toc")
        if isinstance(index, TocIndex):
            entry = index.lookup(catalog)
            if entry is not None:
                piece.add("title", entry.title, "toc", self.W_TOC)
                piece.add("form", entry.form, "toc", self.W_FORM)
                # The contents list also states how long the piece is.  When
                # that matches the file, it is independent evidence that this
                # row describes *this* file and not merely this catalogue number.
                if entry.pages == signals.page_count:
                    piece.add("title", entry.title, "toc_pagecount", self.W_TOC_PAGES,
                              note=f"toc page count {entry.pages} matches file")
                else:
                    piece.notes.append(f"toc says {entry.pages}pp, file has {signals.page_count}pp")
            elif index.covers(catalog):
                assert catalog is not None
                proposal.warnings.append(f"{catalog.canonical} not found in table of contents")

        # Scoring named by the contents heading beats the disc-wide default:
        # a two-piano fugue on a solo-piano disc is still a two-piano fugue.
        heading = entry.form if entry is not None else ""
        heading_scoring = instrumentation_from_heading(heading) if heading else None
        if heading_scoring:
            piece.add("instrumentation", heading_scoring, "toc_heading", self.W_FORM)
        elif heading and heading_is_ambiguous_scoring(heading):
            piece.notes.append("contents heading names more than one scoring")
        else:
            instrumentation = context.defaults.get("instrumentation")
            if isinstance(instrumentation, str):
                piece.add("instrumentation", instrumentation, "collection_default", self.default_weight)

        proposal.pieces.append(piece)
        return proposal

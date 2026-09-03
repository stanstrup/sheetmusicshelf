"""Adapter for The Sheet Music Archive.

The opposite problem from the CD Sheet Music discs.  Measured over 60 random
files from this collection's 1,564: **no PDF outlines at all, 13% with a text
layer, and no usable DocInfo** -- no `/Subject`, no meaningful `/Title`.  Every
signal has to come from the path::

    chopin/etudes/et10_1.pdf     -> Chopin, Etude, Op. 10 no. 1
    mendelso/sww/sww30_3.pdf     -> Mendelssohn, Song Without Words, Op. 30 no. 3
    liszt/hunrhap/lz_hr12.pdf    -> Liszt, Hungarian Rhapsody no. 12
    bach/wtc/pre&fug8.pdf        -> Bach, Prelude and Fugue no. 8

Two of those signals are genuinely independent: the **folder** names the form
and so, usually, does the **filename prefix**.  Where they agree the form is
solid; where only one speaks, the piece stays in review, which is the honest
answer given no other evidence exists.

Because everything here is inferred rather than read, this collection is a good
candidate for a lowered ``auto_accept`` once you have spot-checked a sample --
that is exactly what per-collection thresholds are for.
"""

from __future__ import annotations

import re
from pathlib import Path

from ...music import composers
from ...music.catalogs import CatalogNumber
from ...pdfsignals import FileSignals
from ..model import FileProposal, PieceProposal
from .base import Adapter, CollectionContext, register

#: Subfolder name -> the musical form it collects.
FORM_FOLDERS: dict[str, str] = {
    "arabesq": "Arabesque", "bagatell": "Bagatelle", "ballades": "Ballade",
    "chilcor": "Children's Corner", "chrisorat": "Christmas Oratorio",
    "concerto": "Concerto", "pianocon": "Piano Concerto", "piancon1": "Piano Concerto",
    "piancon2": "Piano Concerto", "pc_26": "Piano Concerto", "viocon77": "Violin Concerto",
    "con_amin": "Concerto", "davidbun": "Davidsbundlertanze",
    "engsuite": "English Suite", "frensuit": "French Suite", "partitas": "Partita",
    "suite_d": "Suite", "etudes": "Etude", "transet": "Transcendental Etude",
    "viostudy": "Study", "51exc": "Exercise", "fant_c": "Fantasy",
    "gb_vars": "Goldberg Variations", "pag_vars": "Paganini Variations", "vars": "Variations",
    "hungdanc": "Hungarian Dance", "hunrhap": "Hungarian Rhapsody", "rhaps": "Rhapsody",
    "imprompt": "Impromptu", "invents": "Invention", "klavstuk": "Klavierstuck",
    "kreis": "Kreisleriana", "lyricpcs": "Lyric Piece", "magflute": "The Magic Flute",
    "mazurkas": "Mazurka", "mommus": "Moment Musical", "nocturns": "Nocturne",
    "novellet": "Novelette", "polonais": "Polonaise", "preludes": "Prelude",
    "rondo": "Rondo", "rondos": "Rondo", "scherzos": "Scherzo",
    "sonata": "Sonata", "sonatas": "Sonata", "sww": "Song Without Words",
    "symph": "Symphony", "btsymph": "Symphony", "waltzes": "Waltz",
    "wtc": "Prelude and Fugue", "24viocap": "Caprice", "paganini": "Paganini Study",
}

#: Subfolder name -> what the music is scored for, when the folder says so.
INSTRUMENT_FOLDERS: dict[str, str] = {
    "4h": "piano four hands", "4hand": "piano four hands",
    "organ": "organ",
    "violpian": "violin and piano", "viol_son": "violin and piano",
    "violin": "violin", "24viocap": "violin", "viocon77": "violin and orchestra",
    "viostudy": "violin",
}

#: Folders that group by opus rather than by form.
_OPUS_FOLDER = re.compile(r"^opus\s*(\d{1,3})$", re.I)

#: Filename prefix -> form.  Longest prefixes first so "prefug" beats "pre".
FORM_PREFIXES: tuple[tuple[str, str], ...] = tuple(sorted(
    {
        "prefug": "Prelude and Fugue", "pre&fug": "Prelude and Fugue",
        "prelud": "Prelude", "prelude": "Prelude", "pre": "Prelude",
        "etude": "Etude", "etud": "Etude", "et": "Etude",
        "mazurka": "Mazurka", "maz": "Mazurka", "mzk": "Mazurka",
        "noct": "Nocturne", "nocturne": "Nocturne",
        "sww": "Song Without Words",
        "lp": "Lyric Piece",
        "walz": "Waltz", "waltz": "Waltz", "wal": "Waltz",
        "bag": "Bagatelle",
        "poln": "Polonaise", "polon": "Polonaise",
        "nove": "Novelette",
        "hundanc": "Hungarian Dance", "hr": "Hungarian Rhapsody",
        "exc": "Exercise",
        "btsn": "Sonata", "mzsn": "Sonata", "sn": "Sonata",
        "scherz": "Scherzo", "imprm": "Impromptu",
        "b2part": "Invention", "b3part": "Sinfonia",
    }.items(),
    key=lambda item: -len(item[0]),
))

# "et10_1" -> opus 10, number 1.  Consistent across this collection: Chopin's
# etudes, Mendelssohn's songs and Beethoven's sonatas all file this way.
_OPUS_NUMBER = re.compile(r"(?<!\d)(\d{1,3})_(\d{1,2})(?!\d)")
_TRAILING_NUMBER = re.compile(r"(\d{1,3})(?!.*\d)")
_ALPHA_PREFIX = re.compile(r"^([a-z]+)", re.I)

#: Forms numbered in a plain sequence rather than by opus, so a lone number is
#: "no. 12" and not "Op. 12".
SEQUENCE_FORMS = {
    "Hungarian Rhapsody", "Invention", "Sinfonia", "Prelude and Fugue",
    "English Suite", "French Suite", "Partita", "Symphony", "Piano Concerto",
    "Violin Concerto", "Concerto", "Hungarian Dance", "Exercise", "Caprice",
    "Transcendental Etude", "Paganini Study", "Suite",
}

#: Composers whose works are not conventionally cited by opus number.  Reading
#: "debpr1_5" as "Prelude, Op. 1 no. 5" is simply wrong -- Debussy's Preludes
#: are Book 1 no. 5, and Bach and Scarlatti are cited by BWV and K instead.
NO_OPUS_COMPOSERS = {
    "Claude Debussy", "Johann Sebastian Bach", "Domenico Scarlatti",
    "Jean-Philippe Rameau", "George Frideric Handel", "Traditional",
}

#: Forms published in numbered books, where a leading number means the book.
BOOK_FORMS = {"Prelude", "Etude", "Image", "Prelude and Fugue"}


#: Folders holding ONE work split across files, mapped to what to call it.
#: A value of "" means the form name from :data:`FORM_FOLDERS` already says it.
#:
#: The trailing number in these folders is a movement, not a piece number:
#: ``grieg/con_amin/conamin2.pdf`` is the second movement of the A minor
#: concerto, not a second concerto.  Nothing in the file layout distinguishes
#: this from ``chopin/preludes/pre28_2.pdf``, which really is a separate
#: prelude -- both are one stem and a number -- so the folders are named here
#: rather than guessed at.  Getting it wrong in either direction is visible:
#: one work becomes twenty-seven, or twenty-four become one.
SINGLE_WORK_FOLDERS: dict[str, str] = {
    "chilcor": "",          # Children's Corner, 6 movements
    "chrisorat": "",        # Christmas Oratorio, one work in parts
    "con_amin": "Concerto in A minor",
    "davidbun": "",         # Davidsbundlertanze, Op. 6
    "fant_c": "Fantasy in C",
    "gb_vars": "",          # Goldberg Variations
    "kreis": "",            # Kreisleriana, Op. 16
    "magflute": "",         # The Magic Flute, by act and number
    "pag_vars": "",         # Paganini Variations, Op. 35, two books
    "pc_26": "Piano Concerto no. 26",
    "piancon1": "Piano Concerto no. 1",
    "piancon2": "Piano Concerto no. 2",
    "suite_d": "Suite in D",
    "viocon77": "Violin Concerto",
}

#: The same thing keyed on the filename, for works that sit directly in a
#: composer folder and so have no folder of their own to name them.
SINGLE_WORK_STEMS: tuple[tuple[str, str], ...] = (
    ("prchofg", "Prelude, Choral and Fugue"),
)

#: A movement number is only read from a stem that *ends* in digits.  Liszt's
#: "lispc1_a" ends in a letter and its "1" is the concerto, not the movement;
#: reading it as one would number every movement 1.
_ENDS_IN_NUMBER = re.compile(r"(\d{1,3})$")


def single_work_title(folder: str, stem: str, form: str | None) -> str | None:
    """What to call the one work this file belongs to, or None."""
    named = SINGLE_WORK_FOLDERS.get(folder.strip().lower())
    if named is not None:
        return named or form
    lowered = stem.strip().lower()
    for prefix, title in SINGLE_WORK_STEMS:
        if lowered.startswith(prefix):
            return title
    return None


def movement_from_stem(stem: str) -> int | None:
    match = _ENDS_IN_NUMBER.search(stem.strip())
    return int(match.group(1)) if match else None


def form_from_folder(folder: str) -> str | None:
    return FORM_FOLDERS.get(folder.strip().lower())


def form_from_stem(stem: str) -> str | None:
    """Read the form from a filename's leading letters, if it declares one."""
    cleaned = stem.strip().lower().lstrip("_-")
    # Strip a leading composer tag: "lz_hr12", "ch_op22a", "btpc3_1d".
    for tag in ("lz_", "ch_", "bt_", "jsb", "deb", "sch", "moz", "lz", "bt"):
        if cleaned.startswith(tag) and len(cleaned) > len(tag):
            candidate = cleaned[len(tag):].lstrip("_-")
            for prefix, form in FORM_PREFIXES:
                if candidate.startswith(prefix):
                    return form
    for prefix, form in FORM_PREFIXES:
        if cleaned.startswith(prefix):
            return form
    return None


def numbers_from_stem(stem: str) -> tuple[int | None, int | None]:
    """Extract (opus, number) from a stem.  Either may be None."""
    match = _OPUS_NUMBER.search(stem)
    if match:
        return int(match.group(1)), int(match.group(2))
    # A lone trailing number, but not the digits that belong to the prefix
    # ("b2part10" is Invention 10, not Invention 2).
    tail = _ALPHA_PREFIX.sub("", stem, count=1)
    match = _TRAILING_NUMBER.search(tail)
    if match:
        return None, int(match.group(1))
    return None, None


def uses_opus(composer: str | None) -> bool:
    return (composer or "") not in NO_OPUS_COMPOSERS


# A stem that reads as a word rather than a code: "leyenda", "auldlang".
_WORDLIKE = re.compile(r"^[a-z][a-z_'-]{3,}$", re.I)
#: Stems that are structural rather than titles.
_NOT_TITLES = {"index", "contents", "readme", "toc", "cover", "misc", "notes"}


def title_from_wordlike_stem(stem: str) -> str | None:
    """Use the filename as the title when it reads as a name, not a code.

    Files sitting directly in a composer folder have no form folder to consult,
    but they are named for the piece: ``albeniz/leyenda.pdf`` is *Leyenda*.
    Codes like ``et10_1`` are excluded by requiring letters only.
    """
    cleaned = stem.strip().replace("_", " ").strip()
    lowered = cleaned.lower()
    # "bsnindex" is the Beethoven-sonatas contents page, not a piece called
    # Bsnindex, so structural words disqualify a stem wherever they appear.
    if any(word in lowered for word in _NOT_TITLES) or not _WORDLIKE.match(stem.strip()):
        return None
    return " ".join(word.capitalize() if word.islower() else word for word in cleaned.split())


def build_title(
    form: str | None,
    opus: int | None,
    number: int | None,
    composer: str | None = None,
) -> str | None:
    """Compose a title from what the path revealed.

    What a leading number *means* depends on the composer: for Chopin
    "et10_1" is Op. 10 no. 1, but for Debussy "debpr1_5" is Book 1 no. 5.
    """
    if not form:
        return None

    qualifier = ""
    if opus is not None and number is not None:
        if uses_opus(composer):
            qualifier = f"Op. {opus} no. {number}"
        elif form in BOOK_FORMS:
            qualifier = f"Book {opus} no. {number}"
        else:
            qualifier = f"no. {number}"
    elif number is not None:
        if form in SEQUENCE_FORMS or not uses_opus(composer):
            qualifier = f"no. {number}"
        else:
            qualifier = f"Op. {number}"

    return f"{form}, {qualifier}" if qualifier else form


@register
class SheetMusicArchiveAdapter(Adapter):
    name = "sheetmusicarchive"

    W_FOLDER_COMPOSER = 0.70
    W_FORM_FOLDER = 0.70
    W_FORM_PREFIX = 0.62
    W_TITLE = 0.62
    W_STEM_TITLE = 0.55
    #: A named single-work folder is a stronger reading than an inferred form:
    #: the folder was identified by hand, so it does not have to be guessed.
    W_SINGLE_WORK = 0.80
    W_MOVEMENT = 0.72
    W_CATALOG = 0.60
    W_INSTRUMENT = 0.68

    ignore_globs = Adapter.ignore_globs + (
        "books/*", "*/books/*",          # plain-text e-books, not music
        "uploads/*", "*/uploads/*",
        "*index.pdf", "*/*index.pdf",   # per-folder contents pages
    )

    def detect(self, root: Path) -> float:
        if root.name.lower() == "the sheet music archive":
            return 0.95
        # Many composer-named folders, two levels deep, is this collection's shape.
        children = [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else []
        if len(children) >= 15:
            recognised = sum(1 for c in children if composers.resolve(c.name) is not None)
            if recognised >= len(children) * 0.6:
                return 0.75
        return 0.0

    def prepare(self, context: CollectionContext) -> None:
        context.defaults["instrumentation"] = "solo piano"
        context.notes.append(
            "path-only collection: no outlines, no usable DocInfo. "
            "Form is inferred from the folder and the filename prefix."
        )

    def propose(self, signals: FileSignals, context: CollectionContext) -> FileProposal:
        proposal = FileProposal(rel_path=signals.rel_path, adapter=self.name)
        if self.should_ignore(signals.rel_path):
            proposal.skipped = "ignored by adapter glob"
            return proposal
        if signals.error:
            proposal.skipped = f"unreadable: {signals.error}"
            return proposal

        parts = Path(signals.rel_path).parts
        composer_folder = parts[0] if parts else ""
        group_folder = parts[1] if len(parts) > 2 else ""

        piece = PieceProposal(page_start=1, page_end=max(signals.page_count, 1))
        piece.notes.append("whole-file")

        record = composers.resolve(composer_folder)
        if record is not None:
            piece.add("composer", record.canonical, "folder_composer", self.W_FOLDER_COMPOSER)
        elif composer_folder.lower() == "trad":
            piece.add("composer", "Traditional", "folder_composer", self.W_FOLDER_COMPOSER)
        else:
            proposal.warnings.append(f"unrecognised composer folder {composer_folder!r}")

        # Form: the folder and the filename prefix are independent readings.
        folder_form = form_from_folder(group_folder)
        stem_form = form_from_stem(signals.stem)
        if folder_form:
            piece.add("form", folder_form, "folder_form", self.W_FORM_FOLDER)
        if stem_form:
            piece.add("form", stem_form, "filename_form", self.W_FORM_PREFIX)

        opus_folder = _OPUS_FOLDER.match(group_folder or "")
        opus, number = numbers_from_stem(signals.stem)
        if opus_folder and opus is None:
            # "opus68/xyz3.pdf": the folder carries the opus, the file the number.
            opus, number = int(opus_folder.group(1)), number

        form = folder_form or stem_form
        composer_name = record.canonical if record is not None else None

        # One work spread over several files: every file gets the *same* title,
        # which is what folds them into a single work downstream.
        one_work = single_work_title(group_folder, signals.stem, form)
        if one_work:
            piece.add("title", one_work, "single_work_folder", self.W_SINGLE_WORK)
            movement = movement_from_stem(signals.stem)
            if movement is not None:
                piece.add("movement_no", movement, "filename_movement", self.W_MOVEMENT)
                piece.notes.append(f"movement {movement} of a work split over several files")
            else:
                piece.notes.append("part of a work split over several files")
            # No catalogue claim here.  The stem's number is a movement, and
            # the opus it looks like is often not the work's: "schdb2&3" is
            # the second and third dances, not opus 3.
            opus = number = None
            title = None
        else:
            title = build_title(form, opus, number, composer_name)
        if title:
            # Agreement between the two form signals is what lifts this above a
            # single guess; alone, it stays firmly in review.
            weight = self.W_TITLE + 0.10 if (folder_form and stem_form and folder_form == stem_form) else self.W_TITLE
            piece.add("title", title, "path_pattern", min(weight, 0.78))
        else:
            # No form folder: the filename is usually the piece's name.
            named = title_from_wordlike_stem(signals.stem)
            if named:
                piece.add("title", named, "filename_name", self.W_STEM_TITLE)
            else:
                proposal.warnings.append("no form or name recognised in path")

        # Only claim an opus number for composers who are actually cited that
        # way; a wrong catalogue number is worse than none.
        if opus is not None and uses_opus(composer_name):
            catalog = CatalogNumber("Op", opus, "", number)
            piece.add("catalog", catalog.canonical, "path_pattern", self.W_CATALOG)

        scoring = INSTRUMENT_FOLDERS.get((group_folder or "").lower())
        if scoring:
            piece.add("instrumentation", scoring, "folder_instrument", self.W_INSTRUMENT)
        else:
            default = context.defaults.get("instrumentation")
            if isinstance(default, str):
                piece.add("instrumentation", default, "collection_default", self.default_weight)

        proposal.pieces.append(piece)
        return proposal

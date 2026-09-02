"""Composer name authority.

A local seed list so ingest can resolve "Mozart", "JS Bach", "beethovn" and
"Frederic Chopin" onto one entity without a network round-trip.  Phase 4 grows
this from MusicBrainz/IMSLP; nothing here assumes it stays hand-written.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_SPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Composer:
    canonical: str
    sort_name: str
    born: int | None = None
    died: int | None = None


# canonical -> aliases.  Aliases are matched after folding, so accents and
# punctuation need not be repeated here.
_SEED: dict[tuple[str, str, int | None, int | None], tuple[str, ...]] = {
    ("Johann Sebastian Bach", "Bach, Johann Sebastian", 1685, 1750):
        ("bach", "js bach", "j s bach", "johann sebastian bach", "bach js"),
    ("Wolfgang Amadeus Mozart", "Mozart, Wolfgang Amadeus", 1756, 1791):
        ("mozart", "w a mozart", "wa mozart", "wolfgang amadeus mozart", "moz"),
    ("Ludwig van Beethoven", "Beethoven, Ludwig van", 1770, 1827):
        ("beethoven", "beethovn", "l van beethoven", "van beethoven", "ludwig van beethoven"),
    ("Johannes Brahms", "Brahms, Johannes", 1833, 1897):
        ("brahms", "johannes brahms"),
    ("Joseph Haydn", "Haydn, Joseph", 1732, 1809):
        ("haydn", "franz joseph haydn", "joseph haydn", "f j haydn"),
    ("Frederic Chopin", "Chopin, Frederic", 1810, 1849):
        ("chopin", "frederic chopin", "frederick chopin", "f chopin", "cho"),
    ("Franz Schubert", "Schubert, Franz", 1797, 1828):
        ("schubert", "franz schubert"),
    ("Franz Liszt", "Liszt, Franz", 1811, 1886):
        ("liszt", "franz liszt"),
    ("Robert Schumann", "Schumann, Robert", 1810, 1856):
        ("schumann", "robert schumann"),
    ("Felix Mendelssohn", "Mendelssohn, Felix", 1809, 1847):
        ("mendelssohn", "felix mendelssohn", "mendelssohn bartholdy"),
    ("Edvard Grieg", "Grieg, Edvard", 1843, 1907):
        ("grieg", "edvard grieg"),
    ("Domenico Scarlatti", "Scarlatti, Domenico", 1685, 1757):
        ("scarlatti", "domenico scarlatti"),
    ("Claude Debussy", "Debussy, Claude", 1862, 1918):
        ("debussy", "claude debussy"),
    ("Pyotr Ilyich Tchaikovsky", "Tchaikovsky, Pyotr Ilyich", 1840, 1893):
        ("tchaikovsky", "tschaikowsky", "chaikovsky", "pi tchaikovsky"),
    ("George Frideric Handel", "Handel, George Frideric", 1685, 1759):
        ("handel", "haendel", "g f handel", "george frideric handel"),
    ("Antonin Dvorak", "Dvorak, Antonin", 1841, 1904): ("dvorak", "antonin dvorak"),
    ("Bela Bartok", "Bartok, Bela", 1881, 1945): ("bartok", "bela bartok"),
    ("Isaac Albeniz", "Albeniz, Isaac", 1860, 1909): ("albeniz", "isaac albeniz"),
    ("Enrique Granados", "Granados, Enrique", 1867, 1916): ("granados",),
    ("Gabriel Faure", "Faure, Gabriel", 1845, 1924): ("faure", "gabriel faure"),
    ("Cesar Franck", "Franck, Cesar", 1822, 1890): ("franck", "cesar franck"),
    ("Muzio Clementi", "Clementi, Muzio", 1752, 1832): ("clementi",),
    ("Carl Czerny", "Czerny, Carl", 1791, 1857): ("czerny",),
    ("Charles-Louis Hanon", "Hanon, Charles-Louis", 1819, 1900): ("hanon",),
    ("Mily Balakirev", "Balakirev, Mily", 1837, 1910): ("balakirev", "balakir"),
    ("Vincenzo Bellini", "Bellini, Vincenzo", 1801, 1835): ("bellini",),
    ("Georges Bizet", "Bizet, Georges", 1838, 1875): ("bizet",),
    ("Edward Elgar", "Elgar, Edward", 1857, 1934): ("elgar",),
    ("John Field", "Field, John", 1782, 1837): ("field",),
    ("Louis Moreau Gottschalk", "Gottschalk, Louis Moreau", 1829, 1869): ("gottschalk", "gotts"),
    ("Charles Tomlinson Griffes", "Griffes, Charles Tomlinson", 1884, 1920): ("griffes",),
}

_ALIASES: dict[str, Composer] = {}
for (_canonical, _sort, _born, _died), _names in _SEED.items():
    _record = Composer(_canonical, _sort, _born, _died)
    for _alias in (*_names, _canonical.lower()):
        _ALIASES[_alias] = _record


def fold(name: str) -> str:
    """Fold a name for alias matching: accents dropped, punctuation removed."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[.,'`]", "", text)
    text = re.sub(r"[^\w\s-]", " ", text)
    return _SPACE.sub(" ", text).strip().casefold()


def resolve(name: str) -> Composer | None:
    """Map a raw name onto a known composer, or None when unrecognised."""
    if not name:
        return None
    folded = fold(name)
    if folded in _ALIASES:
        return _ALIASES[folded]
    # "Bach, Johann Sebastian" -> "johann sebastian bach"
    if "," in name:
        surname, _, rest = name.partition(",")
        flipped = fold(f"{rest} {surname}")
        if flipped in _ALIASES:
            return _ALIASES[flipped]
    # Trailing surname alone: "Ludwig van Beethoven" when only "beethoven" is known.
    parts = folded.split()
    if parts and parts[-1] in _ALIASES:
        return _ALIASES[parts[-1]]
    return None


def canonical_or_raw(name: str) -> str:
    """Best available spelling: the authority record's, else a tidied original."""
    record = resolve(name)
    if record is not None:
        return record.canonical
    return _SPACE.sub(" ", (name or "").strip())

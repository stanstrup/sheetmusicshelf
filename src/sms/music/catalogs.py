"""Thematic-catalogue numbers (K., BWV, Op., Hob., D. ...).

These are the strongest identifiers in a classical library: they survive
translation, transliteration and every publisher's retitling.  Parsing them
into (system, number, suffix) rather than keeping the raw string is what lets
the catalogue sort K.9 before K.10 and match `bwv772` to `BWV 772`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Canonical system -> the spellings that map onto it.  Order matters only for
# readability; matching is done on the normalised alias set below.
SYSTEMS: dict[str, tuple[str, ...]] = {
    "K": ("k", "kv", "koechel", "kochel", "köchel"),           # Mozart
    "BWV": ("bwv",),                                                 # J.S. Bach
    "Op": ("op", "opus"),                                            # generic opus
    "Hob": ("hob", "hoboken"),                                       # Haydn
    "D": ("d", "deutsch"),                                           # Schubert
    "HWV": ("hwv",),                                                 # Handel
    "WoO": ("woo",),                                                 # works without opus
    "Anh": ("anh", "anhang"),                                        # appendix
    "S": ("s", "searle"),                                            # Liszt
    "BV": ("bv",),                                                   # Busoni
    "RV": ("rv",),                                                   # Vivaldi
    "Sz": ("sz",),                                                   # Bartók
    "L": ("l",),                                                     # Debussy (Lesure)
    "JBP": ("jbp",),                                                 # CD Sheet Music's Brahms index
    "MOZ": ("moz",),                                                 # CD Sheet Music's Mozart supplement
}

_ALIAS_TO_SYSTEM = {alias: system for system, aliases in SYSTEMS.items() for alias in aliases}

#: The thematic catalogue a composer's works are normally numbered in.  Used to
#: stop a supplement number (MOZ. 1) from being matched against a Köchel index.
PRIMARY_SYSTEM: dict[str, str] = {
    "Wolfgang Amadeus Mozart": "K",
    "Johann Sebastian Bach": "BWV",
    "Joseph Haydn": "Hob",
    "Franz Schubert": "D",
    "George Frideric Handel": "HWV",
    "Franz Liszt": "S",
    "Claude Debussy": "L",
    "Bela Bartok": "Sz",
}

# Systems whose single-letter alias is too ambiguous to accept without a
# following number that looks like a catalogue number (not a page or a year).
_WEAK_ALIASES = {"s", "l", "d", "k"}

_CATALOG_RE = re.compile(
    r"""\b(?P<system>K\.?V?|BWV|Op(?:us)?|Hob(?:oken)?|D|HWV|WoO|Anh|S|BV|RV|Sz|L|JBP|MOZ|Köchel|Koechel)
        \.?\s*
        (?P<number>\d{1,4})
        (?P<suffix>[a-z](?:/\d+[a-z]?)?)?
        (?:\s*(?:no\.?|\#)\s*(?P<sub>\d{1,3}))?
    """,
    re.VERBOSE | re.IGNORECASE,
)

# A bare stub filename that *is* a catalogue number: bwv772, k0179, op27
_STUB_RE = re.compile(r"^(?P<system>[a-z]{1,4})[\s_-]?0*(?P<number>\d{1,4})(?P<suffix>[a-z])?$", re.I)


@dataclass(frozen=True, slots=True)
class CatalogNumber:
    system: str          # canonical, e.g. "K"
    number: int
    suffix: str = ""     # "a", "b" in K.33b
    sub: int | None = None   # the "#2" in Op. 118 no. 2

    @property
    def canonical(self) -> str:
        base = f"{self.system}. {self.number}{self.suffix}"
        return f"{base} no. {self.sub}" if self.sub is not None else base

    @property
    def sort_key(self) -> tuple[str, int, str, int]:
        return (self.system, self.number, self.suffix, self.sub or 0)

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.canonical


def _canonical_system(raw: str) -> str | None:
    token = raw.strip().rstrip(".").lower().replace("ö", "o")
    return _ALIAS_TO_SYSTEM.get(token)


def parse_catalog(text: str) -> CatalogNumber | None:
    """Extract the first catalogue number from a free-text fragment."""
    if not text:
        return None
    for match in _CATALOG_RE.finditer(text):
        system = _canonical_system(match.group("system"))
        if system is None:
            continue
        suffix = (match.group("suffix") or "").lower()
        sub = int(match.group("sub")) if match.group("sub") else None
        return CatalogNumber(system, int(match.group("number")), suffix, sub)
    return None


def parse_catalog_stub(stem: str) -> CatalogNumber | None:
    """Read a catalogue number out of a cryptic filename stem.

    ``k0179`` -> K. 179, ``bwv772`` -> BWV 772.  Deliberately strict: a stub
    that does not start with a known system alias returns None rather than
    inventing one.
    """
    match = _STUB_RE.match(stem.strip())
    if not match:
        return None
    system = _canonical_system(match.group("system"))
    if system is None:
        return None
    number = int(match.group("number"))
    if system.lower() in _WEAK_ALIASES and number == 0:
        return None
    return CatalogNumber(system, number, (match.group("suffix") or "").lower())


# Prefix of a messier stem: "k279-08r2" -> K. 279, "op27no2rev" -> Op. 27
_PREFIX_RE = re.compile(r"^(?P<system>[a-z]{1,4})[\s_-]?0*(?P<number>\d{1,4})(?P<suffix>[a-z](?![a-z]))?", re.I)
# A system alias embedded after a composer prefix: "mozk455" -> K. 455
_EMBEDDED_RE = re.compile(r"(?P<system>bwv|kv|hob|hwv|woo|anh|jbp|moz|rv|sz|bv|op|k|d|s|l)[\s_-]?0*(?P<number>\d{1,4})(?P<suffix>[a-z](?![a-z]))?", re.I)


def parse_catalog_loose(stem: str) -> tuple[CatalogNumber, str] | None:
    """Best-effort catalogue number from a filename stem, with its provenance.

    Returns the number plus a tag naming how confident the match is
    (``stub`` > ``prefix`` > ``embedded``), so the caller can weight it.  Kept
    separate from :func:`parse_catalog_stub` because a loose match is a real
    guess and must not be scored as though it were an exact one.
    """
    stem = stem.strip()
    exact = parse_catalog_stub(stem)
    if exact is not None:
        return exact, "stub"

    match = _PREFIX_RE.match(stem)
    if match:
        system = _canonical_system(match.group("system"))
        if system is not None:
            return CatalogNumber(system, int(match.group("number")), (match.group("suffix") or "").lower()), "prefix"

    # Scan right-to-left: the catalogue number is usually the last token.
    found = list(_EMBEDDED_RE.finditer(stem))
    for match in reversed(found):
        system = _canonical_system(match.group("system"))
        if system is None:
            continue
        if system.lower() in _WEAK_ALIASES and match.start() == 0:
            continue   # already covered by the prefix attempt above
        return CatalogNumber(system, int(match.group("number")), (match.group("suffix") or "").lower()), "embedded"
    return None

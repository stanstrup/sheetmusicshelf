"""Musical key parsing.

Editions write keys in wildly different shorthand -- "G Major", "A Maj",
"Eb Major", "Emi", "C# minor", "F Minor".  Everything here normalises to a
single canonical spelling ("E-flat major") plus a sortable tonic/mode pair.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TONICS = "ABCDEFG"

# Accidental spellings seen in the wild, longest first so "bb" beats "b".
_ACCIDENTALS = [
    ("##", "##"), ("bb", "bb"),
    ("#", "#"), ("♯", "#"),
    ("b", "b"), ("♭", "b"),
    ("-flat", "b"), ("-sharp", "#"),
    ("flat", "b"), ("sharp", "#"),
]

_MAJOR = {"major", "maj", "dur", "-dur", "M"}
_MINOR = {"minor", "min", "mi", "moll", "-moll", "m"}

_ACCIDENTAL_NAME = {"": "", "#": "-sharp", "b": "-flat", "##": "-double-sharp", "bb": "-double-flat"}

# "in Eb Major", "A Maj", "Emi", "c-moll"
_KEY_RE = re.compile(
    r"""(?:\bin\s+)?
        \b(?P<tonic>[A-Ga-g])
        \s*(?P<acc>\#\#|bb|[#b♯♭]|-?flat|-?sharp)?
        \s*(?P<mode>majeur|major|maj|dur|moll|minor|min|mi|m|M)\b
    """,
    re.VERBOSE | re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Key:
    tonic: str          # "E"
    accidental: str     # "", "#", "b"
    mode: str           # "major" | "minor"

    @property
    def canonical(self) -> str:
        return f"{self.tonic}{_ACCIDENTAL_NAME[self.accidental]} {self.mode}"

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.canonical


def _normalise_accidental(raw: str | None) -> str:
    if not raw:
        return ""
    token = raw.strip().lower()
    for needle, canon in _ACCIDENTALS:
        if token == needle.lower():
            return canon
    return ""


def _normalise_mode(raw: str) -> str | None:
    token = raw.strip()
    if token in _MAJOR or token.lower() in {m.lower() for m in _MAJOR}:
        return "major"
    if token in _MINOR or token.lower() in {m.lower() for m in _MINOR}:
        return "minor"
    return None


def parse_key(text: str) -> Key | None:
    """Find a key in a fragment of title or metadata text.

    Returns None rather than guessing: a wrong key is worse than a blank one,
    because it silently splits a work's editions apart in the catalogue.
    """
    if not text:
        return None
    for match in _KEY_RE.finditer(text):
        mode = _normalise_mode(match.group("mode"))
        if mode is None:
            continue
        tonic = match.group("tonic")
        acc = _normalise_accidental(match.group("acc"))
        # A bare lowercase letter + "m" is too weak on its own ("...problem").
        if match.group("mode") in {"m", "M"} and match.group("acc") is None:
            continue
        return Key(tonic.upper(), acc, mode)
    return None


# A fragment that is *nothing but* a key: "A Maj", "in Eb Major", "Emi".
_KEY_ONLY_RE = re.compile(
    r"""^\s*(?:in\s+)?
        (?P<tonic>[A-Ga-g])
        \s*(?P<acc>\#\#|bb|[#b♯♭]|-?flat|-?sharp)?
        \s*(?P<mode>majeur|major|maj|dur|moll|minor|min|mi|m|M)\.?\s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)


def parse_key_only(text: str) -> Key | None:
    """Parse a fragment that consists solely of a key, else None.

    Used to decide whether a comma-separated segment *is* the key field or
    merely a title that happens to name one -- "A Maj" is the former,
    "Fugue in G Minor" is the latter, and confusing them costs the title.
    """
    if not text:
        return None
    match = _KEY_ONLY_RE.match(text)
    if not match:
        return None
    mode = _normalise_mode(match.group("mode"))
    if mode is None:
        return None
    return Key(match.group("tonic").upper(), _normalise_accidental(match.group("acc")), mode)


def strip_key(text: str) -> str:
    """Remove a trailing key phrase from a title ("Sonata in F Major" -> "Sonata")."""
    return re.sub(r"\s*\bin\s+[A-Ga-g]\s*(?:\#\#|bb|[#b])?\s*(?:major|minor|maj|min)\b\.?\s*$", "", text, flags=re.I).strip()

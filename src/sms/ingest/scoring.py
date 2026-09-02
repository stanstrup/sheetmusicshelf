"""Turning candidates into confidences.

Two rules, and they are the whole design:

1. **Independent agreement reinforces.**  Two different signals proposing the
   same value combine with a noisy-OR, so a title confirmed by both the
   embedded ``/Subject`` and the disc's table of contents outranks either alone.
   Repeats from the *same* source do not reinforce -- that would let one noisy
   adapter talk itself into certainty.

2. **Disagreement is not averaged away.**  When two signals propose genuinely
   different values, the field is capped below the review threshold and flagged.
   A field nobody agrees on is exactly the field a human should look at.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable, Iterable

from .model import REQUIRED_FIELDS, Candidate, FileProposal, PieceProposal, ResolvedField

# Routing thresholds.  Per-collection overrides land on the `collection` row;
# these are the defaults a new collection starts with.
AUTO_ACCEPT = 0.80
REVIEW_FLOOR = 0.50

# A rival value this close to the leader means the signals genuinely disagree.
CONFLICT_RATIO = 0.55
# Confidence a conflicted field is capped to -- deliberately below REVIEW_FLOOR
# so conflicts are *held*, not merely queued.
CONFLICT_CAP = 0.45

#: What a PDF font that has no ToUnicode map decodes to.  Its presence in a
#: title means the text is damaged, not that the piece is misidentified.
REPLACEMENT_CHAR = "�"

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACE = re.compile(r"\s+")
# Publishers spell the same ordinal four ways: "No. 1", "#1", "Nr. 1", "n.1".
_ORDINAL = re.compile(r"\b(?:no|nr|num|number)\b\.?\s*(\d+)|#\s*(\d+)", re.I)
# "Eb" and "E-flat" are the same key; fold both to a single token.
_ACCIDENTAL_WORD = re.compile(r"\b([a-g])\s*[-\s]?(flat|sharp)\b", re.I)


def normalise(value: Any) -> str:
    """Fold a value to the form used for deciding whether two signals agree."""
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _PUNCT.sub(" ", text)
    return _SPACE.sub(" ", text).strip().casefold()


# Publishers alternate freely between "8 Minuets" and "Eight Minuets".
_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15",
    "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19",
    "twenty": "20", "thirty": "30",
}
_NUMBER_WORD_RE = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.I)
_MODE_WORD = re.compile(r"\b(maj|min|mi)\b\.?", re.I)


def _expand_shorthand(text: str) -> str:
    text = _NUMBER_WORD_RE.sub(lambda m: _NUMBER_WORDS[m.group(1).lower()], text)
    text = _MODE_WORD.sub(lambda m: "major" if m.group(1).lower() == "maj" else "minor", text)
    return _ACCIDENTAL_WORD.sub(
        lambda m: f"{m.group(1)}{'b' if m.group(2).lower() == 'flat' else '#'}", text
    )


def normalise_title(value: Any) -> str:
    """Title-aware folding.

    Without this, "Sonata No. 1 in C Major" from a table of contents and
    "Sonata #1 in C Major" from embedded metadata read as a *conflict* and the
    piece is held for review -- when in fact the two signals agree perfectly.
    The same goes for "8 Minuets"/"Eight Minuets" and "G Maj"/"G Major".
    """
    text = str(value or "")
    text = _ORDINAL.sub(lambda m: f" n{m.group(1) or m.group(2)} ", text)
    return normalise(_expand_shorthand(text))


def normalise_key(value: Any) -> str:
    return normalise(_expand_shorthand(str(value or "")))


#: Per-field folding.  A field absent here uses :func:`normalise`.
NORMALISERS = {
    "title": normalise_title,
    "work_title": normalise_title,
    "key": normalise_key,
}


def fold_for(field_name: str, value: Any) -> str:
    return NORMALISERS.get(field_name, normalise)(value)


def _noisy_or(weights: Iterable[float]) -> float:
    product = 1.0
    for weight in weights:
        product *= 1.0 - weight
    return min(1.0 - product, 0.99)


def subsumes(shorter: str, longer: str) -> bool:
    """True when ``longer`` is a more complete spelling of ``shorter``.

    Editions abbreviate.  A disc's table of contents says "8 Variations (on
    Laat Ons Juichen by C.E. Graaf)" where the file's own metadata says
    "8 Variations" -- those two signals *agree*, and treating them as a
    conflict would hold a perfectly identified piece for review.

    Matching is on whole tokens, never raw substrings, so "Sonata no. 1" is
    not absorbed into "Sonata no. 10 in C major".
    """
    if not shorter or shorter == longer:
        return False
    short_tokens = shorter.split()
    long_tokens = longer.split()
    if len(short_tokens) >= len(long_tokens):
        return False
    # A leading run of identical tokens: "8 variations" -> "8 variations on ..."
    if long_tokens[: len(short_tokens)] == short_tokens:
        return True
    # Or every token present, when the shorter form is distinctive enough to
    # be meaningful on its own ("Variations" -> "12 Variations (on ...)").
    if len(shorter) >= 4 and set(short_tokens) <= set(long_tokens):
        return True
    return False


def _merge(
    groups: dict[str, dict[str, float]],
    display: dict[str, Any],
    equivalent: "Callable[[str, str], bool]",
) -> None:
    """Collapse value-groups the caller considers the same, keeping the fuller
    spelling as the surviving display form and pooling both sets of sources."""
    for shorter in sorted(groups, key=lambda k: len(k.split())):
        if shorter not in groups:
            continue
        for longer in sorted(groups, key=lambda k: -len(k)):
            if longer == shorter or longer not in groups:
                continue
            if equivalent(shorter, longer):
                for source, weight in groups.pop(shorter).items():
                    groups[longer][source] = max(groups[longer].get(source, 0.0), weight)
                if len(str(display.get(shorter, ""))) > len(str(display.get(longer, ""))):
                    display[longer] = display[shorter]
                break


def resolve_field(field_name: str, candidates: list[Candidate]) -> ResolvedField | None:
    """Combine every candidate for one field into a single verdict."""
    if not candidates:
        return None

    # value-key -> {source: best weight from that source}, plus a display value.
    groups: dict[str, dict[str, float]] = {}
    display: dict[str, Any] = {}
    for candidate in candidates:
        key = fold_for(field_name, candidate.value)
        if not key:
            continue
        bucket = groups.setdefault(key, {})
        bucket[candidate.source] = max(bucket.get(candidate.source, 0.0), candidate.weight)
        # Prefer the longest spelling as the display form: "E-flat major" over "Eb".
        current = display.get(key)
        if current is None or len(str(candidate.value)) > len(str(current)):
            display[key] = candidate.value

    if not groups:
        return None

    # Fold equivalent spellings together *before* looking for conflicts, so
    # that agreement expressed differently reinforces rather than cancelling
    # out.  Two passes: same words in a different order, then abbreviations
    # absorbed into their fuller form.
    _merge(groups, display, lambda a, b: sorted(a.split()) == sorted(b.split()))
    _merge(groups, display, subsumes)

    scored = sorted(
        ((key, _noisy_or(sources.values()), sorted(sources)) for key, sources in groups.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    best_key, best_score, best_sources = scored[0]

    conflict = False
    if len(scored) > 1 and scored[1][1] >= best_score * CONFLICT_RATIO:
        conflict = True

    confidence = min(best_score, CONFLICT_CAP) if conflict else best_score

    return ResolvedField(
        field=field_name,
        value=display[best_key],
        confidence=round(confidence, 4),
        sources=best_sources,
        conflict=conflict,
        alternatives=[(display[k], round(s, 4), srcs) for k, s, srcs in scored[1:4]],
    )


def score_piece(piece: PieceProposal) -> PieceProposal:
    """Resolve every field on a piece and set its overall confidence."""
    by_field: dict[str, list[Candidate]] = {}
    for candidate in piece.candidates:
        by_field.setdefault(candidate.field, []).append(candidate)

    piece.fields = {}
    for field_name, candidates in by_field.items():
        resolved = resolve_field(field_name, candidates)
        if resolved is not None:
            piece.fields[field_name] = resolved

    # A piece is only as trustworthy as its weakest identifying field, and a
    # missing identifier is a zero, not a shrug.
    scores = []
    for required in REQUIRED_FIELDS:
        resolved = piece.fields.get(required)
        scores.append(0.0 if resolved is None else resolved.confidence)
    confidence = min(scores) if scores else 0.0

    # A disagreement anywhere blocks auto-accept, even on a field that does not
    # identify the piece.  One disc labels k0355.pdf as "K001" while its own
    # filename says 355 -- the entry may still be broadly right, but which of
    # the two is correct is a decision for a person, not for a default.
    conflicted = sorted(name for name, f in piece.fields.items() if f.conflict)
    if conflicted:
        confidence = min(confidence, CONFLICT_CAP)
        piece.scorer_notes.append("signals disagree on " + ", ".join(conflicted))

    # Text a PDF font could not map arrives as U+FFFD.  The piece is identified
    # correctly, but the spelling is damaged and must not be filed as final.
    damaged = sorted(
        name for name in REQUIRED_FIELDS
        if (f := piece.fields.get(name)) is not None and REPLACEMENT_CHAR in str(f.value)
    )
    if damaged:
        confidence = min(confidence, AUTO_ACCEPT - 0.05)
        piece.scorer_notes.append("unreadable characters in " + ", ".join(damaged))

    piece.confidence = round(confidence, 4)
    return piece


def score_file(proposal: FileProposal) -> FileProposal:
    for piece in proposal.pieces:
        score_piece(piece)
    return proposal


def route(confidence: float, *, auto_accept: float = AUTO_ACCEPT, review_floor: float = REVIEW_FLOOR) -> str:
    """Where a piece goes: ``accept`` | ``review`` | ``hold``."""
    if confidence >= auto_accept:
        return "accept"
    if confidence >= review_floor:
        return "review"
    return "hold"

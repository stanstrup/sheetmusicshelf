"""Assigning a composer to a musical period.

Wikidata's "movement" statement is patchy and often lists several, so the
period is derived from dates instead: it is deterministic, explainable, and
right far more often than a scraped label.  A composer is placed by the middle
of their working life, not their birth -- Beethoven was born in 1770 but is not
a Classical footnote, and Monteverdi died in 1643 without being a Baroque
latecomer.
"""

from __future__ import annotations

#: Upper bound (exclusive) of each period, by the composer's mid-career year.
PERIODS: tuple[tuple[str, int], ...] = (
    ("Medieval", 1400),
    ("Renaissance", 1600),
    ("Baroque", 1750),
    ("Classical", 1820),
    ("Romantic", 1900),
    ("Modern", 1945),
    ("Contemporary", 9999),
)

#: Years after birth at which a composer is taken to be mid-career, used when
#: no death year is known.
MID_CAREER_OFFSET = 35


def derive_period(born: int | None, died: int | None) -> str | None:
    """Return a period name, or None when the dates cannot support a guess."""
    if born is None and died is None:
        return None

    if born is not None and died is not None:
        if died < born:
            return None
        # Mid-point of adult life rather than of the whole lifespan: childhood
        # is not part of anyone's output.
        midpoint = born + max((died - born), 0) // 2
        midpoint = max(midpoint, born + 20)
    elif born is not None:
        midpoint = born + MID_CAREER_OFFSET
    else:
        assert died is not None
        midpoint = died - 15

    for name, upper in PERIODS:
        if midpoint < upper:
            return name
    return PERIODS[-1][0]


def lifespan(born: int | None, died: int | None) -> str:
    """A short human label: "1756-1791", "b. 1971", "d. 1643"."""
    if born and died:
        return f"{born}–{died}"
    if born:
        return f"b. {born}"
    if died:
        return f"d. {died}"
    return ""

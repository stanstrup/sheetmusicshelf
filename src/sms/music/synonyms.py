"""The same music, spelled two ways.

Publishers are not consistent, and a catalogue assembled from several of them
holds both spellings.  This library really does contain "Fantasia in F Minor"
and "Fantasy in D Minor" by the same composer, and "Etude" and "Study" for the
same form, so somebody typing one word and being shown only half of what they
own is a fault in the search, not in their spelling.

Only pairs that actually occur in a catalogue belong here.  A long list of
plausible equivalences is a way to make a search quietly wrong: fold two things
that are genuinely different and there is no way for the reader to un-fold them.
"""

from __future__ import annotations

#: Groups of spellings that mean the same thing when searching.  Each group is
#: expanded to all of its members, so any word finds the rest.
_GROUPS: tuple[frozenset[str], ...] = (
    # Both spellings are in this library, by the same composers.
    frozenset({"fantasy", "fantasia", "fantasie"}),
    # 37 waltzes and one valse.
    frozenset({"waltz", "valse", "walzer"}),
    # Chopin's are filed as etudes, Brahms's studies, and they are the same word.
    frozenset({"etude", "study"}),
    frozenset({"minuet", "menuet", "menuett"}),
    # Not present here yet, but the same word in two languages and cheap to be
    # right about the first time somebody adds a German edition.
    frozenset({"nocturne", "notturno"}),
    frozenset({"sonata", "sonate"}),
    frozenset({"symphony", "symphonie", "sinfonia"}),
    frozenset({"prelude", "praeludium", "preludio"}),
)

_INDEX: dict[str, frozenset[str]] = {
    word: group for group in _GROUPS for word in group
}


def expand(term: str) -> list[str]:
    """Every spelling of a search term, the term itself first.

    Returns the term alone when nothing is known about it, which is the common
    case and must stay cheap.
    """
    cleaned = term.strip().lower()
    group = _INDEX.get(cleaned)
    if group is None:
        return [term]
    return [term] + sorted(word for word in group if word != cleaned)

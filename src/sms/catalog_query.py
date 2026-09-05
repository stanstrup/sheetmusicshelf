"""One place that knows how to narrow the catalogue.

The browse page and the API answered the same question with two query
builders, and they had already drifted: one matched a composer exactly and the
other by substring, one could filter by period and the other could not, one
excluded rejected entries and the other did not.  Two builders that are
supposed to agree will keep drifting, and the failure is silent -- a filter
added to one and forgotten in the other just quietly returns the wrong rows.

The remaining difference between the callers is deliberate and named here:
the browse page matches text exactly because its values come from clicking a
facet, while the API matches by substring because its values are typed.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .models import Composer, Piece, SourceFile


@dataclass
class Filters:
    """Everything the catalogue can be narrowed by.

    Adding a filter means adding a field here and a line in :func:`narrow`,
    and both surfaces get it.
    """

    q: str | None = None
    composer: str | None = None
    form: str | None = None
    instrument: str | None = None
    key: str | None = None
    status: str | None = None
    route: str | None = None
    review_state: str | None = None
    period: str | None = None
    collection_id: int | None = None
    min_difficulty: int | None = None
    max_difficulty: int | None = None
    #: Rejected entries are not part of the catalogue, and are excluded unless
    #: something specifically asks after them.
    include_rejected: bool = False

    @classmethod
    def from_params(cls, params: dict) -> "Filters":
        """Build from a page's query string, ignoring keys that are not filters."""
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in params.items() if k in known and v not in (None, "")}
        collection = params.get("collection")
        if collection:
            values["collection_id"] = int(collection)
        return cls(**values)


def narrow(query, filters: Filters, *, text_match: str = "exact"):
    """Apply every filter that is set.  ``text_match`` is "exact" or "contains"."""

    def matches(column, value: str):
        return column.ilike(f"%{value}%") if text_match == "contains" else column == value

    if filters.q:
        query = _search(query, filters.q)
    for column, value in (
        (Piece.composer_name, filters.composer),
        (Piece.form, filters.form),
        (Piece.instrumentation, filters.instrument),
        (Piece.music_key, filters.key),
    ):
        if value:
            query = query.where(matches(column, value))

    # Never fuzzy: these are closed vocabularies, and "accept" must not match
    # a route someone invents later that happens to contain it.
    for column, value in (
        (Piece.status, filters.status),
        (Piece.route, filters.route),
        (Piece.review_state, filters.review_state),
    ):
        if value:
            query = query.where(column == value)

    if filters.collection_id is not None:
        query = query.where(SourceFile.collection_id == filters.collection_id)
    if filters.period:
        # Period lives on the composer authority record, not on the piece, so
        # filtering by it means going through the composer.
        query = query.join(
            Composer, Composer.canonical_name == Piece.composer_name
        ).where(Composer.period == filters.period)
    if filters.min_difficulty is not None:
        query = query.where(Piece.difficulty >= filters.min_difficulty)
    if filters.max_difficulty is not None:
        query = query.where(Piece.difficulty <= filters.max_difficulty)
    if not filters.include_rejected and not filters.review_state:
        query = query.where(Piece.review_state != "rejected")
    return query


#: Everything a free-text search looks in.
#:
#: The key is here because "chopin nocturne c minor" is a sentence people type
#: and every word of it has to land somewhere.  The *form* is deliberately not:
#: in the CD Sheet Music collection it holds the name of the volume a piece came
#: in, so every Mozart sonata carries form "Sonatas and Fantasies" and searching
#: for a fantasy returned eighteen sonatas ahead of the six real ones.  A
#: piece's title already says what it is.
def _searchable():
    return (
        Piece.title,
        Piece.composer_name,
        Piece.catalog_display,
        Piece.music_key,
    )


#: More words than this and the reader is not searching, they are pasting.
MAX_TERMS = 8


def _search(query, text: str):
    """Narrow by a phrase, one word at a time.

    Every word must match *something* -- title, composer, catalogue number,
    form or key -- but they need not all match the same thing.  That is what
    makes "mozart fantasy" work: one word is the composer, the other is the
    title, and matching the phrase as a single string against each column in
    turn could never find it.

    Words are combined with AND, so each one narrows.  Spellings within a word
    are combined with OR, so "study" also finds an etude.
    """
    from sqlalchemy import func

    from .music.synonyms import expand

    for term in text.split()[:MAX_TERMS]:
        term = term.strip()
        if not term:
            continue
        clauses = []
        if len(term) == 1:
            # A single letter is a key -- the F of "in F minor" -- and as a
            # plain substring it is noise: bare "f" otherwise matches Fantasy,
            # Fugue and half the catalogue.  So it is matched against the key,
            # and against a title that names its key in words.
            clauses.append(Piece.music_key.ilike(f"{term}%"))
            clauses.append(Piece.title.ilike(f"% in {term}%"))
        else:
            for spelling in expand(term):
                like = f"%{spelling}%"
                clauses.extend(column.ilike(like) for column in _searchable())
        # A catalogue number is written "K. 279" but typed "k279" as often as
        # not, so compare it with the spacing and dots taken out as well.
        bare = "".join(ch for ch in term if ch.isalnum())
        if bare:
            flattened = func.replace(
                func.replace(func.coalesce(Piece.catalog_display, ""), " ", ""), ".", ""
            )
            clauses.append(flattened.ilike(f"%{bare}%"))
        query = query.where(or_(*clauses))
    return query

def base_query():
    """A piece query with the file joined, which every filter may rely on."""
    from sqlalchemy import select

    return select(Piece).join(SourceFile, Piece.source_file_id == SourceFile.id)


def facet_values(
    session: Session,
    column,
    collection_id: int | None = None,
    limit: int = 200,
) -> list[tuple[str, int]]:
    """The values a facet can offer and how many pieces each holds.

    Counts, not bare values: "Nocturne (20)" tells you whether a filter is
    worth tapping, and an empty facet is not offered at all.  Ordered by count,
    because the useful values are the ones with things behind them.
    """
    from sqlalchemy import func, select

    query = (
        select(column, func.count())
        .where(column.isnot(None), Piece.review_state != "rejected")
        .group_by(column)
        .order_by(func.count().desc())
        .limit(limit)
    )
    if collection_id is not None:
        query = query.join(SourceFile, Piece.source_file_id == SourceFile.id).where(
            SourceFile.collection_id == collection_id
        )
    return [(value, count) for value, count in session.execute(query).all() if value]


def period_values(session: Session) -> list[tuple[str, int]]:
    """Periods, which live on the composer record rather than on the piece.

    Counted in pieces, like every other facet.  Counting composers instead
    would put "Romantic (35)" next to a filter that returns five hundred
    pieces, and a count beside a filter should say what tapping it gets you.
    """
    from sqlalchemy import func, select

    rows = session.execute(
        select(Composer.period, func.count(Piece.id))
        .join(Piece, Piece.composer_name == Composer.canonical_name)
        .where(Composer.period.isnot(None), Piece.review_state != "rejected")
        .group_by(Composer.period)
        .order_by(func.count(Piece.id).desc())
    ).all()
    return [(name, count) for name, count in rows if name]


def all_facets(session: Session, collection_id: int | None = None) -> dict:
    """Everything a client needs to build a filter sidebar."""
    from sqlalchemy import select

    from .models import Collection

    return {
        "composer": facet_values(session, Piece.composer_name, collection_id),
        "form": facet_values(session, Piece.form, collection_id),
        "instrument": facet_values(session, Piece.instrumentation, collection_id),
        "key": facet_values(session, Piece.music_key, collection_id),
        "status": facet_values(session, Piece.status, collection_id),
        "period": period_values(session),
        "collections": [
            (row.id, row.name)
            for row in session.execute(
                select(Collection.id, Collection.name).order_by(Collection.name)
            ).all()
        ],
    }

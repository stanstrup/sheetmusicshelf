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
        like = f"%{filters.q.strip()}%"
        query = query.where(or_(
            Piece.title.ilike(like),
            Piece.composer_name.ilike(like),
            Piece.catalog_display.ilike(like),
        ))
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


def base_query():
    """A piece query with the file joined, which every filter may rely on."""
    from sqlalchemy import select

    return select(Piece).join(SourceFile, Piece.source_file_id == SourceFile.id)


def facet_values(session: Session, column, collection_id: int | None = None) -> list[str]:
    """The distinct values a facet can offer, from what is actually catalogued."""
    from sqlalchemy import distinct, select

    query = select(distinct(column)).where(column.isnot(None))
    if collection_id is not None:
        query = query.join(SourceFile, Piece.source_file_id == SourceFile.id).where(
            SourceFile.collection_id == collection_id
        )
    return sorted(value for value in session.scalars(query) if value)

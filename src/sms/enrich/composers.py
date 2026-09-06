"""Building and enriching the composer authority.

Ingest records a composer *name* on each piece, because a name is all a PDF
gives you.  This turns those names into authority records, so "Mozart" written
five different ways across three collections becomes one composer with one set
of dates, one period and one portrait.
"""

from __future__ import annotations

import logging
import urllib.parse
from datetime import datetime, timezone

from sqlalchemy import distinct, func, select, update
from sqlalchemy.orm import Session

from ..models import Composer, FieldCandidate, Piece
from ..music import composers as authority
from ..music.periods import derive_period
from . import wikipedia

log = logging.getLogger("sms.enrich")


def _wikipedia_title(wikipedia_url: str) -> str:
    """Extract the display title from a Wikipedia URL, e.g. 'ABBA' from .../wiki/ABBA."""
    path = urllib.parse.urlparse(wikipedia_url).path
    slug = path.rstrip("/").rsplit("/", 1)[-1]
    return urllib.parse.unquote(slug).replace("_", " ")


def _rename_canonical(session: Session, composer: Composer, new_name: str) -> None:
    """Rename a composer's canonical name and update all denormalised fields."""
    old_name = composer.canonical_name
    if old_name == new_name:
        return
    session.execute(
        update(Piece).where(Piece.composer_name == old_name).values(composer_name=new_name)
    )
    session.execute(
        update(FieldCandidate)
        .where(FieldCandidate.field == "composer", FieldCandidate.value == old_name)
        .values(value=new_name)
    )
    aliases = list(dict.fromkeys([*(composer.aliases or []), old_name]))
    composer.aliases = aliases
    composer.canonical_name = new_name
    log.info("renamed composer %r → %r", old_name, new_name)


def _merge_into(session: Session, survivor: Composer, duplicate: Composer) -> None:
    """Move duplicate's pieces to survivor and delete the duplicate record."""
    dup_name = duplicate.canonical_name
    surv_name = survivor.canonical_name
    session.execute(
        update(Piece).where(Piece.composer_name == dup_name).values(composer_name=surv_name)
    )
    session.execute(
        update(FieldCandidate)
        .where(FieldCandidate.field == "composer", FieldCandidate.value == dup_name)
        .values(value=surv_name)
    )
    merged_aliases = list(dict.fromkeys([
        *(survivor.aliases or []),
        *(duplicate.aliases or []),
        dup_name,
    ]))
    survivor.aliases = merged_aliases
    session.flush()
    session.delete(duplicate)
    log.info("merged composer %r (id=%s) into %r (id=%s)", dup_name, duplicate.id, surv_name, survivor.id)


def sync(session: Session) -> tuple[int, int]:
    """Create composer records for every name the catalogue mentions.

    Returns (created, linked_names).  Names that the seed authority recognises
    are folded together; anything unrecognised gets its own record rather than
    being dropped, so nothing disappears from the catalogue.
    """
    names = [
        name for name in session.scalars(
            select(distinct(Piece.composer_name)).where(Piece.composer_name.isnot(None))
        ) if name and name.strip()
    ]

    created = 0
    for name in names:
        record = authority.resolve(name)
        canonical = record.canonical if record else name.strip()

        composer = session.scalar(select(Composer).where(Composer.canonical_name == canonical))
        if composer is None:
            composer = Composer(
                canonical_name=canonical,
                sort_name=record.sort_name if record else canonical,
                born=record.born if record else None,
                died=record.died if record else None,
                aliases=[],
            )
            if composer.born or composer.died:
                composer.period = derive_period(composer.born, composer.died)
            session.add(composer)
            session.flush()
            created += 1

        # Record the spelling that appeared in the library, so a later lookup
        # can resolve it without re-deriving.
        if name != canonical and name not in (composer.aliases or []):
            composer.aliases = sorted({*(composer.aliases or []), name})

    return created, len(names)


def enrich(session: Session, composer: Composer, *, force: bool = False) -> tuple[bool, str]:
    """Fill in description, dates, period and portrait from Wikipedia.

    Returns (changed, message).  Anything already known and not empty is left
    alone unless ``force`` -- enrichment is a supplement to what the library
    itself said, never an override of it.
    """
    if composer.enriched_at and not force:
        return False, "already enriched"

    try:
        facts = wikipedia.lookup(composer.canonical_name)
    except wikipedia.LookupUnavailable as exc:
        # Deliberately do not stamp enriched_at: this name has not been
        # answered, only deferred, and must come round again.
        return False, f"lookup unavailable ({exc})"

    if facts is None:
        composer.enriched_at = datetime.now(timezone.utc)
        return False, "no matching person or group on Wikidata"

    changed: list[str] = []

    if facts.description and (force or not composer.description):
        composer.description = facts.description
        changed.append("description")
    if facts.wikipedia_url and (force or not composer.wikipedia_url):
        composer.wikipedia_url = facts.wikipedia_url
    if facts.wikidata_id:
        composer.wikidata_id = facts.wikidata_id

    # Merge any duplicate that shares the same Wikipedia article.
    if facts.wikipedia_url:
        duplicate = session.scalar(
            select(Composer).where(
                Composer.wikipedia_url == facts.wikipedia_url,
                Composer.id != composer.id,
            )
        )
        if duplicate is not None:
            # Keep the one whose canonical_name matches Wikipedia, else keep composer.
            wiki_title = _wikipedia_title(facts.wikipedia_url)
            if duplicate.canonical_name == wiki_title:
                _merge_into(session, survivor=duplicate, duplicate=composer)
                # The current composer was deleted; enrich the survivor instead.
                return enrich(session, duplicate, force=force)
            else:
                _merge_into(session, survivor=composer, duplicate=duplicate)
            changed.append("merged duplicate")

    # Rename to the Wikipedia article title when it differs from the current name.
    if facts.wikipedia_url:
        wiki_title = _wikipedia_title(facts.wikipedia_url)
        if wiki_title and wiki_title != composer.canonical_name:
            _rename_canonical(session, composer, wiki_title)
            changed.append(f"renamed to {wiki_title!r}")

    # Dates from the seed authority are hand-checked; only fill gaps.
    if facts.born and (force or composer.born is None):
        composer.born = facts.born
        changed.append("born")
    if facts.died and (force or composer.died is None):
        composer.died = facts.died
        changed.append("died")

    period = derive_period(composer.born, composer.died)
    if period and period != composer.period:
        composer.period = period
        changed.append("period")

    if facts.has_image and (force or not composer.portrait_file):
        path = wikipedia.save_portrait(composer.id, facts)
        if path is not None:
            composer.portrait_file = path.name
            composer.portrait_source_url = facts.image_source_url
            composer.portrait_credit = facts.image_credit
            composer.portrait_license = facts.image_license
            changed.append("portrait")

    composer.enriched_at = datetime.now(timezone.utc)
    if not changed:
        return False, "nothing new" + (f" ({'; '.join(facts.notes)})" if facts.notes else "")
    return True, ", ".join(changed) + (f" [{'; '.join(facts.notes)}]" if facts.notes else "")


def pending(session: Session, limit: int | None = None) -> list[Composer]:
    query = (
        select(Composer)
        .where(Composer.enriched_at.is_(None))
        .order_by(Composer.canonical_name)
    )
    if limit:
        query = query.limit(limit)
    return list(session.scalars(query))


def counts(session: Session) -> dict[str, int]:
    total = session.scalar(select(func.count()).select_from(Composer)) or 0
    enriched = session.scalar(
        select(func.count()).select_from(Composer).where(Composer.enriched_at.isnot(None))
    ) or 0
    with_portrait = session.scalar(
        select(func.count()).select_from(Composer).where(Composer.portrait_file.isnot(None))
    ) or 0
    return {"total": total, "enriched": enriched, "with_portrait": with_portrait}

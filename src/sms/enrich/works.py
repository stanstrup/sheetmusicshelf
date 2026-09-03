"""Work authority records, and the canonical sources they link to.

Ingest produces *pieces*: a page range in a file, carrying whatever the file
said about itself.  Six editions of K. 283 across three collections are six
pieces -- but one piece of music, with one IMSLP page and one MusicBrainz
identifier.  This module builds that layer.

Identity is the catalogue number where there is one.  It is the strongest thing
a classical library has: it survives translation, transliteration and every
publisher's retitling, so "Sonata No. 5 in G Major" and "Klaviersonate Nr. 5"
land on the same work as long as both say K. 283.  Where there is no catalogue
number the composer plus a folded title is used instead, which is weaker and
recorded as such.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ingest.scoring import normalise_title
from ..models import Composer, Piece, Work
from ..music.catalogs import parse_catalog

log = logging.getLogger("sms.enrich.works")


@dataclass
class LinkResult:
    works_created: int = 0
    pieces_linked: int = 0
    skipped_no_composer: int = 0
    skipped_no_title: int = 0
    by_key: dict[str, int] = field(default_factory=lambda: {"catalogue": 0, "title": 0})


def _composer_for(session: Session, name: str | None) -> Composer | None:
    if not name:
        return None
    return session.scalar(select(Composer).where(Composer.canonical_name == name))


def find_or_create_work(session: Session, piece: Piece) -> tuple[Work | None, str]:
    """Return the work this piece is a copy of, creating it if new.

    The second element names the key used, so a weak match is visible rather
    than indistinguishable from a strong one.
    """
    composer = _composer_for(session, piece.composer_name)
    if composer is None:
        return None, "no composer"
    if not piece.title:
        return None, "no title"

    catalog = parse_catalog(piece.catalog_display or "")
    if catalog is not None:
        work = session.scalar(
            select(Work).where(
                Work.composer_id == composer.id,
                Work.catalog_system == catalog.system,
                Work.catalog_number == catalog.number,
                Work.catalog_suffix == (catalog.suffix or ""),
                Work.catalog_sub.is_(None) if catalog.sub is None else Work.catalog_sub == catalog.sub,
            )
        )
        key = "catalogue"
    else:
        # Fold the title so "Sonata No. 1" and "Sonata #1" are one work.
        folded = normalise_title(piece.title)
        work = next(
            (
                candidate
                for candidate in session.scalars(
                    select(Work).where(
                        Work.composer_id == composer.id, Work.catalog_system.is_(None)
                    )
                )
                if normalise_title(candidate.title) == folded
            ),
            None,
        )
        key = "title"

    if work is None:
        work = Work(
            composer_id=composer.id,
            title=piece.title,
            catalog_system=catalog.system if catalog else None,
            catalog_number=catalog.number if catalog else None,
            catalog_suffix=(catalog.suffix or "") if catalog else "",
            catalog_sub=catalog.sub if catalog else None,
            music_key=piece.music_key,
            form=piece.form,
            period=composer.period,
        )
        session.add(work)
        session.flush()
        return work, f"created:{key}"

    # Fill gaps from this copy without overwriting what the work already knows.
    if not work.music_key and piece.music_key:
        work.music_key = piece.music_key
    if not work.form and piece.form:
        work.form = piece.form
    if not work.period and composer.period:
        work.period = composer.period
    return work, key


def link(session: Session, collection_id: int | None = None, relink: bool = False) -> LinkResult:
    """Give every catalogued piece a work.

    Every non-rejected piece takes part, including uncertain ones: grouping is
    cheap, local and reversible, and it is what makes "other copies of this
    work" answerable at all.  Looking a work *up* against IMSLP and MusicBrainz
    is the step that requires confidence -- see :func:`enrich`.
    """
    from ..models import SourceFile

    result = LinkResult()
    query = select(Piece).join(SourceFile, Piece.source_file_id == SourceFile.id)
    if collection_id is not None:
        query = query.where(SourceFile.collection_id == collection_id)
    if not relink:
        query = query.where(Piece.work_id.is_(None))
    query = query.where(
        Piece.review_state != "rejected",
        Piece.title.isnot(None),
        Piece.composer_name.isnot(None),
    )

    for piece in session.scalars(query):
        work, how = find_or_create_work(session, piece)
        if work is None:
            if how == "no composer":
                result.skipped_no_composer += 1
            else:
                result.skipped_no_title += 1
            continue
        if how.startswith("created:"):
            result.works_created += 1
            how = how.split(":", 1)[1]
        result.by_key[how] = result.by_key.get(how, 0) + 1
        piece.work_id = work.id
        result.pieces_linked += 1

    return result


# --- canonical sources -----------------------------------------------------

def enrich(session: Session, work: Work, *, force: bool = False) -> tuple[bool, str]:
    """Attach IMSLP and MusicBrainz links to one work.

    Returns (changed, message).  A transient failure leaves ``enriched_at``
    unset so the work comes round again, rather than recording a negative that
    was never actually established.
    """
    from datetime import datetime, timezone

    from . import canonical

    if work.enriched_at and not force:
        return False, "already looked up"
    if work.confirmed and not force:
        return False, "confirmed by hand; left alone"

    composer = session.get(Composer, work.composer_id) if work.composer_id else None
    if composer is None:
        return False, "no composer to search by"

    if not settled(session, work):
        # Attaching an authoritative link to a guess is worse than attaching
        # none: it launders a guess into a citation. "The Magic Flute, Op. 1
        # no. 3" is a filename misread -- the work is K. 620 -- and it must not
        # come back carrying an IMSLP URL.
        return False, "no accepted copy yet; not looked up"

    try:
        links = canonical.lookup(
            composer.canonical_name,
            work.catalog_system,
            work.catalog_number,
            work.catalog_suffix or "",
            work.title or "",
        )
    except canonical.LookupUnavailable as exc:
        return False, f"lookup unavailable ({exc})"

    changed: list[str] = []
    if links.imslp_url and (force or not work.imslp_url):
        work.imslp_url = links.imslp_url
        work.imslp_title = links.imslp_title
        changed.append("IMSLP")
    if links.year and (force or work.year is None):
        work.year = links.year
        work.year_note = links.year_note
        changed.append(f"composed {links.year_note or links.year}")
    if links.musicbrainz_id and (force or not work.musicbrainz_id):
        work.musicbrainz_id = links.musicbrainz_id
        changed.append("MusicBrainz")

    work.match_note = "; ".join(links.notes) or (
        f"matched on {work.catalog_system}. {work.catalog_number}"
    )
    work.enriched_at = datetime.now(timezone.utc)
    return (bool(changed), ", ".join(changed) if changed else (work.match_note or "nothing found"))


def settled(session: Session, work: Work) -> bool:
    """Whether any copy of this work has been accepted.

    A work exists as soon as a piece mentions it, but it is only *established*
    once the catalogue is confident about at least one copy.
    """
    return bool(
        session.scalar(
            select(Piece.id)
            .where(
                Piece.work_id == work.id,
                (Piece.review_state == "accepted") | (Piece.route == "accept"),
            )
            .limit(1)
        )
    )


def pending(session: Session, limit: int | None = None) -> list[Work]:
    """Works worth looking up: those with a catalogue number and no links yet."""
    query = (
        select(Work)
        .where(
            Work.enriched_at.is_(None),
            Work.catalog_system.isnot(None),
            Work.catalog_number.isnot(None),
            # Only works with an accepted copy: see enrich().
            Work.id.in_(
                select(Piece.work_id).where(
                    Piece.work_id.isnot(None),
                    (Piece.review_state == "accepted") | (Piece.route == "accept"),
                )
            ),
        )
        .order_by(Work.composer_id, Work.catalog_system, Work.catalog_number)
    )
    if limit:
        query = query.limit(limit)
    return list(session.scalars(query))


def counts(session: Session) -> dict[str, int]:
    from sqlalchemy import func

    def count(*where) -> int:
        return session.scalar(select(func.count()).select_from(Work).where(*where)) or 0

    return {
        "works": count(),
        "with_catalogue": count(Work.catalog_system.isnot(None)),
        "looked_up": count(Work.enriched_at.isnot(None)),
        "imslp": count(Work.imslp_url.isnot(None)),
        "musicbrainz": count(Work.musicbrainz_id.isnot(None)),
    }

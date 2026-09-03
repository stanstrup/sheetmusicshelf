"""The browse UI and reader.

Server-rendered Jinja and plain forms.  No framework, no bundle, and nothing
loaded from a CDN -- a phone reaching this over a VPN may have no route to the
open internet, so every asset the page needs is served from here.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from .auth import Principal, current_principal
from .config import get_settings
from .db import commit_now, get_session
from .models import (
    Collection, Composer, FieldCandidate, Piece, RemovedRange, Shelf, ShelfItem,
    SourceFile, Work,
)

log = logging.getLogger("sms.web")

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# Stylesheets are served with a long cache lifetime, so the URL has to change
# when they do -- otherwise a deploy leaves everyone on the previous design.
_CSS = STATIC_DIR / "app.css"
ASSET_VERSION = str(int(_CSS.stat().st_mtime)) if _CSS.exists() else "0"
templates.env.globals["asset_version"] = ASSET_VERSION

router = APIRouter(include_in_schema=False)

PAGE_SIZE = 60

#: Notes that describe normal structure rather than something to look at.
BENIGN_NOTES = {"whole-file"}


def _query_string(params: dict) -> str:
    from urllib.parse import urlencode

    clean = {k: v for k, v in params.items() if v not in (None, "", "composer") or k == "q"}
    # Keep an explicit sort only when it is not the default.
    if params.get("sort") in (None, "", "composer"):
        clean.pop("sort", None)
    return f"/?{urlencode(clean)}" if clean else "/"


def _register_helpers(request: Request, params: dict) -> dict:
    """Link builders for the facet sidebar.

    Filters compose rather than replace, and changing a filter always returns
    to page 1 -- staying on page 7 of a result set that no longer has seven
    pages is the classic faceted-browse annoyance.
    """

    def url_with(name: str, value) -> str:
        merged = dict(params)
        merged[name] = str(value)
        if name != "page":
            merged.pop("page", None)
        return _query_string(merged)

    def url_without(name: str) -> str:
        merged = dict(params)
        merged.pop(name, None)
        merged.pop("page", None)
        return _query_string(merged)

    return {"url_with": url_with, "url_without": url_without}


def _viewer(request: Request, session: Session) -> Principal:
    """The UI's own principal lookup.

    Unlike the API, a browser that is not signed in should be sent to the login
    page rather than handed a 401 it cannot act on.
    """
    try:
        return current_principal(request, session)
    except HTTPException:
        raise HTTPException(status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/auth/login"})


def _filtered(session: Session, params: dict):
    query = select(Piece).join(SourceFile, Piece.source_file_id == SourceFile.id)

    if params.get("q"):
        like = f"%{params['q'].strip()}%"
        query = query.where(
            or_(
                Piece.title.ilike(like),
                Piece.composer_name.ilike(like),
                Piece.catalog_display.ilike(like),
            )
        )
    for column, key in (
        (Piece.composer_name, "composer"),
        (Piece.form, "form"),
        (Piece.music_key, "key"),
        (Piece.status, "status"),
        (Piece.route, "route"),
    ):
        value = params.get(key)
        if value:
            query = query.where(column == value)
    if params.get("collection"):
        query = query.where(SourceFile.collection_id == int(params["collection"]))
    if params.get("period"):
        # Period lives on the composer authority record, not on the piece, so
        # filtering by it means going through the composer.
        query = query.join(
            Composer, Composer.canonical_name == Piece.composer_name
        ).where(Composer.period == params["period"])

    # Rejected entries are not part of the catalogue.
    query = query.where(Piece.review_state != "rejected")

    orderings = {
        "composer": (
            Piece.composer_name.asc(),
            Piece.catalog_system.asc().nulls_last(),
            Piece.catalog_number.asc().nulls_last(),
            Piece.catalog_sub.asc().nulls_last(),
            Piece.title.asc(),
        ),
        "title": (Piece.title.asc(),),
        "uncertain": (Piece.confidence.asc(),),
        "recent": (Piece.created_at.desc(),),
    }
    return query.order_by(*orderings.get(params.get("sort") or "composer", orderings["composer"]))


def _facets(session: Session) -> dict:
    def values(column, limit: int = 200) -> list[str]:
        rows = session.execute(
            select(column, func.count())
            .where(column.isnot(None), Piece.review_state != "rejected")
            .group_by(column)
            .order_by(func.count().desc())
            .limit(limit)
        ).all()
        return [(value, count) for value, count in rows if value]

    periods = session.execute(
        select(Composer.period, func.count())
        .where(Composer.period.isnot(None))
        .group_by(Composer.period)
        .order_by(func.count().desc())
    ).all()

    return {
        "composer": values(Piece.composer_name),
        "form": values(Piece.form),
        "key": values(Piece.music_key),
        "period": [(name, count) for name, count in periods if name],
        "collections": session.execute(
            select(Collection.id, Collection.name).order_by(Collection.name)
        ).all(),
    }


def composer_ids(session: Session) -> dict[str, int]:
    """Name -> composer id, so a byline can link to its authority record."""
    rows = session.execute(select(Composer.canonical_name, Composer.id)).all()
    return {name: cid for name, cid in rows}


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    session: Session = Depends(get_session),
    q: str | None = None,
    composer: str | None = None,
    form: str | None = None,
    key: str | None = None,
    status_: str | None = Query(None, alias="status"),
    route: str | None = None,
    collection: str | None = None,
    period: str | None = None,
    sort: str = "composer",
    page: int = 1,
) -> Response:
    viewer = _viewer(request, session)
    params = {
        "q": q, "composer": composer, "form": form, "key": key,
        "status": status_, "route": route, "collection": collection,
        "period": period, "sort": sort,
    }
    query = _filtered(session, params)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    page = max(page, 1)
    pieces = list(session.scalars(query.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)))

    # The default sort is not a filter; showing it as a removable chip invites
    # the reader to "clear" something that was never set.
    active = {k: v for k, v in params.items() if v and not (k == "sort" and v == "composer")}
    link_params = dict(active)
    link_params["page"] = str(page)
    context = {
        "viewer": viewer,
        "pieces": pieces,
        "total": total,
        "page": page,
        "pages": max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1),
        "params": active,
        "facets": _facets(session),
        "composer_ids": composer_ids(session),
        "sort": sort,
        **_register_helpers(request, link_params),
    }
    return templates.TemplateResponse(request, "browse.html", context)


@router.get("/piece/{piece_id}", response_class=HTMLResponse)
def piece_detail(
    piece_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    viewer = _viewer(request, session)
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")

    siblings = list(
        session.scalars(
            select(Piece)
            .where(
                Piece.source_file_id == piece.source_file_id,
                Piece.id != piece.id,
            )
            .order_by(Piece.page_start)
            .limit(50)
        )
    )
    # Other copies come from the work record now, not from matching the
    # catalogue *string* -- which missed "Op. 10 no. 1" against "Op.10 No.1".
    work = session.get(Work, piece.work_id) if piece.work_id else None
    editions = []
    if work is not None:
        editions = list(
            session.scalars(
                select(Piece).where(Piece.work_id == work.id, Piece.id != piece.id).limit(20)
            )
        )
    composer = None
    if piece.composer_name:
        composer = session.scalar(
            select(Composer).where(Composer.canonical_name == piece.composer_name)
        )
    return templates.TemplateResponse(
        request,
        "piece.html",
        {
            "viewer": viewer, "piece": piece, "composer": composer, "work": work,
            "review_link": f"/review?piece={piece.id}",
            "shelves": list(session.scalars(select(Shelf).order_by(Shelf.name))),
            "file": piece.source_file, "collection": piece.source_file.collection,
            "siblings": siblings, "editions": editions,
        },
    )


@router.get("/composer/{composer_id}", response_class=HTMLResponse)
def composer_detail(
    composer_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    viewer = _viewer(request, session)
    composer = session.get(Composer, composer_id)
    if composer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such composer")

    base = select(Piece).where(
        Piece.composer_name == composer.canonical_name,
        Piece.review_state != "rejected",
    )
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    pieces = list(
        session.scalars(
            base.order_by(Piece.catalog_display.asc(), Piece.title.asc()).limit(24)
        )
    )
    return templates.TemplateResponse(
        request, "composer.html",
        {"viewer": viewer, "composer": composer, "pieces": pieces, "total": total},
    )


@router.get("/read/{piece_id}", response_class=HTMLResponse)
def read(
    piece_id: int,
    request: Request,
    session: Session = Depends(get_session),
    spread: int = Query(0, description="1 for two-up."),
) -> Response:
    viewer = _viewer(request, session)
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")

    from datetime import datetime, timezone

    piece.last_opened = datetime.now(timezone.utc)
    return templates.TemplateResponse(
        request,
        "reader.html",
        {
            "viewer": viewer, "piece": piece,
            "review_link": f"/review?piece={piece.id}",
            "pages": list(range(1, piece.page_count + 1)),
            "spread": bool(spread),
        },
    )


# --- review queue ---------------------------------------------------------

#: The fields a reviewer is asked to confirm, in the order they matter.
REVIEW_FIELDS = (
    ("composer", "Composer"),
    ("title", "Title"),
    ("catalog", "Catalogue number"),
    ("key", "Key"),
    ("form", "Form"),
    ("instrumentation", "Scored for"),
)


def _resolved_values(session: Session, piece: Piece) -> dict[str, str]:
    """What the form was pre-filled with, so a reviewer's edits can be told
    apart from the values they simply left alone."""
    from .ingest.persist import DENORMALISED

    values: dict[str, str] = {}
    for field, column in DENORMALISED.items():
        current = getattr(piece, column, None)
        if current is not None:
            values[field] = str(current)
    return values

def _review_queue(session: Session, collection_id: int | None, route: str | None):
    """The pending queue, least confident first.

    Worst guesses first: they are where a human eye changes the most.
    """
    query = (
        select(Piece)
        .join(SourceFile, Piece.source_file_id == SourceFile.id)
        .where(Piece.review_state == "pending")
    )
    query = query.where(Piece.route == route) if route else query.where(Piece.route.in_(["review", "hold"]))
    if collection_id is not None:
        query = query.where(SourceFile.collection_id == collection_id)
    return query.order_by(Piece.confidence.asc(), Piece.id.asc())


def _next_for_review(
    session: Session,
    collection_id: int | None,
    route: str | None,
    offset: int = 0,
) -> Piece | None:
    query = _review_queue(session, collection_id, route)
    return session.scalar(query.limit(1).offset(offset))


@router.get("/review", response_class=HTMLResponse)
def review(
    request: Request,
    session: Session = Depends(get_session),
    collection: int | None = None,
    route: str = "",
    piece: int | None = None,
    offset: int = 0,
) -> Response:
    viewer = _viewer(request, session)
    # Reviewing a specific piece beats the queue order: arriving here from a
    # piece you are looking at should review *that* piece.
    item = session.get(Piece, piece) if piece else None
    offset = max(offset, 0)
    if item is None:
        item = _next_for_review(session, collection, route or None, offset)

    outstanding_query = (
        select(func.count())
        .select_from(Piece)
        .join(SourceFile, Piece.source_file_id == SourceFile.id)
        .where(Piece.review_state == "pending", Piece.route.in_(["review", "hold"]))
    )
    if collection is not None:
        outstanding_query = outstanding_query.where(SourceFile.collection_id == collection)
    outstanding = session.scalar(outstanding_query) or 0

    fields: list[dict] = []
    file_row = None
    if item is not None:
        file_row = item.source_file
        candidates = list(
            session.scalars(
                select(FieldCandidate)
                .where(FieldCandidate.piece_id == item.id)
                .order_by(FieldCandidate.weight.desc())
            )
        )
        current = {
            "composer": item.composer_name, "title": item.title,
            "catalog": item.catalog_display, "key": item.music_key,
            "form": item.form, "instrumentation": None,
        }
        for name, label in REVIEW_FIELDS:
            options = [c for c in candidates if c.field == name]
            value = current.get(name)
            if value is None and options:
                value = options[0].value
            # Only offer alternatives; repeating the filled-in value as a
            # button to click is noise.
            alternatives = [c for c in options if c.value != value]
            fields.append({"name": name, "label": label, "value": value, "candidates": alternatives})

    # Filtered for display only. Assigning to item.notes_machine here would
    # mutate a mapped attribute inside a GET and be committed on the way out.
    notes = [n for n in (item.notes_machine or []) if n not in BENIGN_NOTES] if item else []
    ambiguous = bool(
        item is not None
        and any("order is ambiguous" in note for note in (item.notes_machine or []))
    )
    return templates.TemplateResponse(
        request, "review.html",
        {
            "viewer": viewer, "item": item, "file": file_row, "fields": fields,
            "outstanding": outstanding, "route": route, "ambiguous_order": ambiguous,
            "notes": notes, "offset": offset, "collection_id": collection,
            # True when a specific piece was asked for, rather than the queue.
            "single": piece is not None,
            "collection": session.get(Collection, collection) if collection else None,
        },
    )


@router.post("/review/{piece_id}")
async def review_submit(
    piece_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """Record a decision and move to the next piece."""
    from .ingest.persist import accept_value, recompute

    viewer = _viewer(request, session)
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")

    form = await request.form()
    action = form.get("action", "skip")
    route = form.get("next_route", "") or ""
    collection = form.get("collection")
    return_to = (form.get("return_to") or "").strip()

    if action == "reject":
        piece.review_state = "rejected"
    elif action == "accept":
        # Only what the reviewer actually changed becomes a decision.  The form
        # arrives pre-filled, so accepting every non-empty field would record
        # values nobody examined as permanent human judgements -- and a
        # decision cannot be argued down by a later, better adapter.
        current = _resolved_values(session, piece)
        for name, _label in REVIEW_FIELDS:
            value = (form.get(name) or "").strip()
            if value and value != (current.get(name) or ""):
                accept_value(
                    session, piece, name, value,
                    user_id=viewer.user_id, source=f"human:{viewer.display_name}",
                )
        piece.review_state = "accepted"
        source_collection = piece.source_file.collection
        recompute(
            session, piece,
            auto_accept=source_collection.auto_accept,
            review_floor=source_collection.review_floor,
        )
    else:
        # Skipped: leave it pending but push it behind the rest for now.
        piece.confidence = min(piece.confidence + 0.0001, 0.9999)

    if action in ("accept", "reject"):
        from datetime import datetime, timezone

        piece.reviewed_by = viewer.user_id
        piece.reviewed_at = datetime.now(timezone.utc)

    # Commit before the redirect: the dependency's own commit would land
    # after the browser has already fetched the next page.
    commit_now(session)

    # Reviewing one specific piece returns to it; working the queue moves on.
    if return_to.startswith("/") and not return_to.startswith("//"):
        target = return_to
    else:
        target = f"/review?route={route}"
        if collection:
            target += f"&collection={collection}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)



@router.post("/piece/{piece_id}/delete")
async def piece_delete(
    piece_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """Remove a catalogue entry for good.

    The PDF is not touched -- this is a decision about the catalogue. The page
    range is remembered so the next scan does not quietly recreate the entry,
    which is what would otherwise happen: the ingester matches pieces by page
    range and makes whatever is missing.
    """
    viewer = _viewer(request, session)
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")

    form = await request.form()
    file_row = piece.source_file
    collection_id = file_row.collection_id

    session.add(
        RemovedRange(
            source_file_id=file_row.id,
            page_start=piece.page_start,
            page_end=piece.page_end,
            removed_by=viewer.user_id,
            reason=(form.get("reason") or "").strip() or None,
        )
    )
    session.delete(piece)
    commit_now(session)
    return RedirectResponse(
        f"/?collection={collection_id}", status_code=status.HTTP_303_SEE_OTHER
    )



# --- works and canonical sources ------------------------------------------

def catalogue_label(work: Work) -> str:
    if not work.catalog_system or work.catalog_number is None:
        return ""
    label = f"{work.catalog_system}. {work.catalog_number}{work.catalog_suffix or ''}"
    return f"{label} no. {work.catalog_sub}" if work.catalog_sub is not None else label


@router.get("/work/{work_id}", response_class=HTMLResponse)
def work_detail(
    work_id: int,
    request: Request,
    session: Session = Depends(get_session),
    q: str | None = None,
) -> Response:
    """One work, its canonical links, and a search for setting them by hand."""
    viewer = _viewer(request, session)
    work = session.get(Work, work_id)
    if work is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such work")

    composer = session.get(Composer, work.composer_id) if work.composer_id else None
    # Movement order first: for a work split over several files the movement
    # number is the reading order, and id order is only the order the files
    # happened to be scanned in.
    pieces = list(
        session.scalars(
            select(Piece).where(Piece.work_id == work.id)
            .order_by(Piece.movement.nulls_last(), Piece.id)
        )
    )

    results = {"imslp": [], "musicbrainz": []}
    error = ""
    if q:
        from .enrich.canonical import search

        results["imslp"], results["musicbrainz"], error = search(
            q, composer.canonical_name if composer else None
        )

    return templates.TemplateResponse(
        request, "work.html",
        {
            "viewer": viewer, "work": work, "composer": composer, "pieces": pieces,
            "catalogue": catalogue_label(work),
            "q": q, "searched": q is not None, "results": results, "error": error,
            "suggested_query": " ".join(
                part for part in ((composer.canonical_name if composer else ""), work.title or "") if part
            ).strip(),
        },
    )


@router.post("/work/{work_id}/link")
async def work_link(
    work_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """Set, confirm or clear a work's canonical links.

    A link chosen here is marked confirmed, which stops a later automatic run
    from overwriting it -- the same rule the catalogue fields follow.
    """
    from datetime import datetime, timezone

    _viewer(request, session)
    work = session.get(Work, work_id)
    if work is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such work")

    form = await request.form()
    action = form.get("action", "")

    if action == "imslp":
        work.imslp_url = (form.get("url") or "").strip() or None
        work.imslp_title = (form.get("title") or "").strip() or None
        work.confirmed = True
        work.match_note = "IMSLP chosen by hand"
    elif action == "musicbrainz":
        work.musicbrainz_id = (form.get("mbid") or "").strip() or None
        work.musicbrainz_title = (form.get("mbtitle") or "").strip() or None
        work.confirmed = True
        work.match_note = "MusicBrainz chosen by hand"
    elif action == "confirm":
        work.confirmed = True
    elif action == "clear":
        work.imslp_url = work.imslp_title = None
        work.musicbrainz_id = work.musicbrainz_title = None
        work.confirmed = False
        work.match_note = "links cleared by hand"

    work.enriched_at = datetime.now(timezone.utc)
    commit_now(session)
    return RedirectResponse(f"/work/{work.id}", status_code=status.HTTP_303_SEE_OTHER)


# --- shelves and personal fields ------------------------------------------

@router.get("/shelves", response_class=HTMLResponse)
def shelves_index(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    viewer = _viewer(request, session)
    rows = session.execute(
        select(Shelf, func.count(ShelfItem.id))
        .outerjoin(ShelfItem, ShelfItem.shelf_id == Shelf.id)
        .group_by(Shelf.id)
        .order_by(Shelf.name)
    ).all()
    return templates.TemplateResponse(
        request, "shelves.html",
        {"viewer": viewer, "shelves": [(shelf, count) for shelf, count in rows]},
    )


@router.post("/shelves")
async def shelves_create(
    request: Request,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    viewer = _viewer(request, session)
    form = await request.form()
    name = (form.get("name") or "").strip()
    if name:
        session.add(Shelf(name=name, owner_id=viewer.user_id))
        commit_now(session)
    return RedirectResponse("/shelves", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/shelf/{shelf_id}", response_class=HTMLResponse)
def shelf_detail(
    shelf_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    viewer = _viewer(request, session)
    shelf = session.get(Shelf, shelf_id)
    if shelf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such shelf")

    pieces = list(
        session.scalars(
            select(Piece)
            .join(ShelfItem, ShelfItem.piece_id == Piece.id)
            .where(ShelfItem.shelf_id == shelf_id)
            .order_by(ShelfItem.position)
        )
    )
    return templates.TemplateResponse(
        request, "shelf.html",
        {"viewer": viewer, "shelf": shelf, "pieces": pieces},
    )


@router.post("/piece/{piece_id}/personal")
async def piece_personal(
    piece_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """Save the fields that are yours rather than the music's."""
    _viewer(request, session)
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")

    form = await request.form()

    def number(name: str, low: int, high: int) -> int | None:
        raw = (form.get(name) or "").strip()
        if not raw:
            return None
        try:
            return max(low, min(high, int(raw)))
        except ValueError:
            return None

    piece.difficulty = number("difficulty", 1, 10)
    piece.rating = number("rating", 0, 5)
    piece.status = (form.get("status") or "").strip() or None
    piece.notes = (form.get("notes") or "").strip() or None
    piece.tags = sorted({t.strip() for t in (form.get("tags") or "").split(",") if t.strip()})

    shelf_id = (form.get("shelf") or "").strip()
    if shelf_id.isdigit():
        existing = session.scalar(
            select(ShelfItem).where(
                ShelfItem.shelf_id == int(shelf_id), ShelfItem.piece_id == piece.id
            )
        )
        if existing is None:
            last = session.scalar(
                select(func.max(ShelfItem.position)).where(ShelfItem.shelf_id == int(shelf_id))
            )
            session.add(
                ShelfItem(shelf_id=int(shelf_id), piece_id=piece.id, position=(last or 0) + 1)
            )

    commit_now(session)
    return RedirectResponse(f"/piece/{piece.id}", status_code=status.HTTP_303_SEE_OTHER)


# --- page-range editor ----------------------------------------------------

@router.get("/split/{file_id}", response_class=HTMLResponse)
def split_editor(
    file_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    """Define where each piece starts inside a multi-piece file."""
    viewer = _viewer(request, session)
    file_row = session.get(SourceFile, file_id)
    if file_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such file")

    pieces = list(
        session.scalars(
            select(Piece).where(Piece.source_file_id == file_row.id).order_by(Piece.page_start)
        )
    )
    return templates.TemplateResponse(
        request, "split.html",
        {
            "viewer": viewer, "file": file_row, "pieces": pieces,
            "starts": {p.page_start for p in pieces},
            "titles": {p.page_start: (p.title or "") for p in pieces},
        },
    )


@router.post("/split/{file_id}")
async def split_apply(
    file_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    from .ingest.split import Boundary, apply_splits

    viewer = _viewer(request, session)
    file_row = session.get(SourceFile, file_id)
    if file_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such file")

    form = await request.form()
    boundaries = []
    for raw in form.getlist("start"):
        try:
            page = int(raw)
        except (TypeError, ValueError):
            continue
        boundaries.append(Boundary(page, (form.get(f"title_{page}") or "").strip()))

    apply_splits(session, file_row, boundaries, user_id=viewer.user_id)
    commit_now(session)
    return RedirectResponse(f"/split/{file_row.id}", status_code=status.HTTP_303_SEE_OTHER)


# --- PWA ------------------------------------------------------------------

@router.get("/manifest.webmanifest")
def manifest() -> JSONResponse:
    """What Android needs to install this as an app rather than a bookmark."""
    return JSONResponse(
        {
            # A stable id, so an install survives the start_url changing.
            "id": "/?installed=1",
            "name": "Sheet Music Shelf",
            "short_name": "Shelf",
            "start_url": "/?installed=1",
            "scope": "/",
            "display": "standalone",
            # Chrome on Android prefers the first mode it supports; falling back
            # through the list keeps older browsers on plain standalone.
            "display_override": ["window-controls-overlay", "standalone", "minimal-ui"],
            "orientation": "any",
            "background_color": "#0d0f13",
            "theme_color": "#2c4a8c",
            "description": "A cataloguing server for a personal sheet music library.",
            "categories": ["music", "productivity"],
            "icons": [
                {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
                # Maskable, so Android can crop it to the launcher's shape
                # instead of dropping the square icon in a white circle.
                {
                    "src": "/static/icon-maskable.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
            "shortcuts": [
                {"name": "Review queue", "url": "/review"},
                {"name": "Shelves", "url": "/shelves"},
            ],
        },
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/sw.js")
def service_worker() -> Response:
    """A deliberately minimal worker.

    Offline access to scores was explicitly out of scope, so this caches the
    app shell and static assets only.  Page images are already immutable and
    cached by the browser's ordinary HTTP cache; adding them here would just be
    a second, worse cache with a storage quota to manage.
    """
    return Response(
        (STATIC_DIR / "sw.js").read_text(encoding="utf-8"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


# --- authentication -------------------------------------------------------

auth_router = APIRouter(prefix="/auth", include_in_schema=False)


def _oauth():
    from authlib.integrations.starlette_client import OAuth

    settings = get_settings()
    oauth = OAuth()
    oauth.register(
        name="authentik",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        server_metadata_url=settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth


@auth_router.get("/login")
async def login(request: Request):
    settings = get_settings()
    if not settings.oidc_enabled:
        if settings.auth_disabled:
            return RedirectResponse("/")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No identity provider is configured. Set SMS_OIDC_ISSUER and SMS_OIDC_CLIENT_ID.",
        )
    oauth = _oauth()
    return await oauth.authentik.authorize_redirect(request, f"{settings.base_url}/auth/callback")


@auth_router.get("/callback")
async def callback(request: Request, session: Session = Depends(get_session)):
    from .models import AppUser

    oauth = _oauth()
    token = await oauth.authentik.authorize_access_token(request)
    claims = token.get("userinfo") or {}
    subject = claims.get("sub")
    if not subject:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "identity provider returned no subject")

    user = session.scalar(select(AppUser).where(AppUser.oidc_subject == subject))
    if user is None:
        # The first person through the door administers the library.
        first = session.scalar(select(func.count()).select_from(AppUser)) == 0
        user = AppUser(oidc_subject=subject, is_admin=bool(first))
        session.add(user)
    user.email = claims.get("email") or user.email
    user.display_name = claims.get("name") or claims.get("preferred_username") or user.display_name
    session.flush()

    request.session["sub"] = subject
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@auth_router.get("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

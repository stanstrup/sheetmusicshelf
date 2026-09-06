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
from datetime import datetime, timedelta, timezone

from .config import get_settings
from .db import commit_now, get_session
from .catalog_query import Filters, all_facets, base_query, narrow
from .services import review as review_service
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
        # Carry where they were going, so signing in lands there rather than
        # dropping them at the front page to navigate back.
        from urllib.parse import quote

        target = quote(request.url.path + ("?" + request.url.query if request.url.query else ""))
        raise HTTPException(
            status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": f"/auth/login?next={target}"},
        )


def _filtered(session: Session, params: dict):
    """The browse query: shared filters, then this page's own ordering.

    Text is matched exactly here because the values come from clicking a
    facet, where a substring match would quietly widen what was asked for.
    """
    query = narrow(base_query(), Filters.from_params(params), text_match="exact")

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
    """What the filter sidebar offers, from the same builder the API uses."""
    return all_facets(session)


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
    instrument: str | None = None,
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
        "instrument": instrument,
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


@router.post("/composer/sync")
async def composer_sync(
    request: Request,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """Create Composer records for any piece composer_name that lacks one."""
    from .enrich import composers as composer_enrich

    _viewer(request, session)
    form = await request.form()
    name = (form.get("name") or "").strip()

    composer_enrich.sync(session)
    commit_now(session)

    # If a specific name was given, redirect to that composer's page.
    if name:
        composer = session.scalar(select(Composer).where(Composer.canonical_name == name))
        if composer:
            return RedirectResponse(f"/composer/{composer.id}", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(request.headers.get("referer") or "/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/composer/{composer_id}/enrich")
async def composer_enrich_one(
    composer_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """Fetch and store Wikipedia/Wikidata data for one composer."""
    from .enrich import composers as composer_enrich

    _viewer(request, session)
    composer = session.get(Composer, composer_id)
    if composer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such composer")

    composer_enrich.enrich(session, composer, force=True)
    commit_now(session)
    return RedirectResponse(f"/composer/{composer_id}", status_code=status.HTTP_303_SEE_OTHER)


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

def _review_queue(session: Session, collection_id: int | None, route: str | None,
                  order: str = "uncertain"):
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
    if order == "folder":
        # Worst-first is right about information per decision and wrong about
        # what is scarce: reloading the composer, the folder convention and the
        # catalogue system on every item. Folder order lets a reviewer decide
        # one set with its context already in their head, and stop at a clean
        # boundary.
        return query.order_by(SourceFile.rel_path.asc(), Piece.page_start.asc())
    return query.order_by(Piece.confidence.asc(), Piece.id.asc())


def _next_for_review(
    session: Session,
    collection_id: int | None,
    route: str | None,
    offset: int = 0,
    order: str = "uncertain",
) -> Piece | None:
    query = _review_queue(session, collection_id, route, order)
    return session.scalar(query.limit(1).offset(offset))


@router.get("/review", response_class=HTMLResponse)
def review(
    request: Request,
    session: Session = Depends(get_session),
    collection: int | None = None,
    route: str = "",
    piece: int | None = None,
    offset: int = 0,
    order: str = "uncertain",
    applied: int = 0,
) -> Response:
    viewer = _viewer(request, session)
    # Reviewing a specific piece beats the queue order: arriving here from a
    # piece you are looking at should review *that* piece.
    item = session.get(Piece, piece) if piece else None
    offset = max(offset, 0)
    if item is None:
        item = _next_for_review(session, collection, route or None, offset, order)

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
            "form": item.form, "instrumentation": item.instrumentation,
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
            "order": order, "applied": applied,
            "folder_size": (
                len(review_service.siblings(session, item, "folder")) if item is not None else 0
            ),
            "folder": (
                review_service.folder_of(item.source_file.rel_path) or "this collection"
                if item is not None else ""
            ),
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

    reviewer = review_service.Reviewer(viewer.user_id, viewer.display_name)
    if action == "reject":
        review_service.reject(session, piece, reviewer)
    elif action == "accept":
        review_service.decide(
            session, piece,
            {name: form.get(name) or "" for name, _label in REVIEW_FIELDS},
            reviewer,
        )
        review_service.approve(session, piece, reviewer)
    elif action == "save":
        review_service.decide(
            session, piece,
            {name: form.get(name) or "" for name, _label in REVIEW_FIELDS},
            reviewer,
        )
    else:
        review_service.skip(session, piece)

    # Commit before the redirect: the dependency's own commit would land
    # after the browser has already fetched the next page.
    commit_now(session)

    if action == "save":
        # Stay on this piece so the user can continue editing or click canonical sources.
        return RedirectResponse(f"/review?piece={piece_id}", status_code=status.HTTP_303_SEE_OTHER)

    # Reviewing one specific piece returns to it; working the queue moves on.
    if return_to.startswith("/") and not return_to.startswith("//"):
        target = return_to
    else:
        target = f"/review?route={route}"
        if collection:
            target += f"&collection={collection}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)



@router.post("/review/{piece_id}/bulk")
async def review_bulk(
    piece_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """Apply this piece's values to every pending piece in its folder.

    Not an approval: it records what the folder has in common -- the composer,
    almost always -- and leaves each piece in the queue to be confirmed on its
    own. Filling in a shared field is a different act from saying a piece is
    right, and only one of them should be done fifty at a time.
    """
    viewer = _viewer(request, session)
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")

    form = await request.form()
    fields = form.getlist("bulk_field")
    values = {
        name: (form.get(name) or "").strip()
        for name, _label in REVIEW_FIELDS
        if name in fields
    }
    pieces = review_service.siblings(session, piece, form.get("scope") or "folder")
    touched = review_service.decide_many(
        session, pieces, values, review_service.Reviewer(viewer.user_id, viewer.display_name)
    )
    commit_now(session)

    query = _review_link(form)
    return RedirectResponse(
        f"/review?{query}&applied={touched}" if query else f"/review?applied={touched}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _review_link(form) -> str:
    """Carry the queue's own state back through a redirect."""
    parts = []
    for key in ("collection", "route", "order"):
        value = (form.get(key) or "").strip()
        if value:
            parts.append(f"{key}={value}")
    return "&".join(parts)


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



@router.post("/piece/{piece_id}/find-work")
async def piece_find_work(
    piece_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """Create a work record for this piece (if possible) and go to its canonical-source page."""
    from .enrich.works import find_or_create_work

    _viewer(request, session)
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")

    work, _ = find_or_create_work(session, piece)
    if work is None:
        # Piece has no title yet — create a bare work so the user can still
        # reach the canonical-source search page and link it by hand.
        from .enrich.works import _composer_for
        composer = _composer_for(session, piece.composer_name)
        if composer is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "piece needs a composer before a work can be created")
        work = Work(composer_id=composer.id, title="", catalog_suffix="")
        session.add(work)
        session.flush()

    piece.work_id = work.id
    commit_now(session)
    return RedirectResponse(f"/work/{work.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/piece/{piece_id}/fill-from-work")
async def piece_fill_from_work(
    piece_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """Copy the linked Work's canonical data into the piece's catalogue fields."""
    from .services import review as review_service

    viewer = _viewer(request, session)
    piece = session.get(Piece, piece_id)
    if piece is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such piece")
    if not piece.work_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "piece has no linked work")

    work = session.get(Work, piece.work_id)
    if work is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such work")

    composer = session.get(Composer, work.composer_id) if work.composer_id else None
    reviewer = review_service.Reviewer(user_id=viewer.id if viewer else None)

    values: dict[str, str] = {}
    if work.title:
        values["title"] = work.title
    if composer:
        values["composer"] = composer.canonical_name
    if work.music_key:
        values["key"] = work.music_key
    if work.form:
        values["form"] = work.form

    review_service.decide(session, piece, values, reviewer, changed_only=False)
    commit_now(session)
    return RedirectResponse(f"/piece/{piece_id}", status_code=status.HTTP_303_SEE_OTHER)


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
    no_parent: bool = False,
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
            "no_parent": no_parent,
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
    elif action == "musicbrainz_parent":
        from .enrich.canonical import find_parent_work
        if work.musicbrainz_id:
            parent = find_parent_work(work.musicbrainz_id)
            if parent:
                work.musicbrainz_id, work.musicbrainz_title = parent
                work.confirmed = True
                work.match_note = "MusicBrainz parent work chosen by hand"
            else:
                # No parent — skip enriched_at update and redirect with notice
                q = (form.get("q") or "").strip()
                qs = "?no_parent=1" + (f"&q={q}" if q else "")
                return RedirectResponse(f"/work/{work.id}{qs}", status_code=status.HTTP_303_SEE_OTHER)
    elif action == "confirm":
        work.confirmed = True
    elif action == "clear":
        work.imslp_url = work.imslp_title = None
        work.musicbrainz_id = work.musicbrainz_title = None
        work.confirmed = False
        work.match_note = "links cleared by hand"

    work.enriched_at = datetime.now(timezone.utc)
    commit_now(session)
    q = (form.get("q") or "").strip()
    suffix = f"?q={q}" if q else ""
    return RedirectResponse(f"/work/{work.id}{suffix}", status_code=status.HTTP_303_SEE_OTHER)


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

@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    """Tokens, and a link to the app."""
    from .auth import SCOPES
    from .models import ApiToken

    viewer = _viewer(request, session)
    _require_admin(viewer)
    tokens = list(
        session.scalars(
            select(ApiToken).order_by(ApiToken.revoked_at.is_not(None), ApiToken.id.desc())
        )
    )
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "viewer": viewer,
            "tokens": tokens,
            "scopes": sorted(SCOPES.items()),
            "minted": "",
            "minted_name": "",
        },
    )


@router.post("/settings/tokens")
async def settings_mint(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    """Create a token and show it once.

    Answered with the page itself rather than a redirect. Redirecting would
    have to carry the secret in the query string, which writes it into browser
    history and into any log that records a URL -- for the one value in the
    system that is never recoverable and never shown twice.
    """
    from .auth import SCOPES, mint_token
    from .models import ApiToken

    viewer = _viewer(request, session)
    _require_admin(viewer)

    form = await request.form()
    name = (form.get("name") or "").strip() or "unnamed"
    scopes = [s for s in form.getlist("scope") if s]
    if not scopes:
        # A token that may do nothing is not a useful object to have made.
        scopes = ["catalog:read"]

    secret, _row = mint_token(session, name, scopes, user_id=viewer.user_id)
    commit_now(session)

    tokens = list(
        session.scalars(
            select(ApiToken).order_by(ApiToken.revoked_at.is_not(None), ApiToken.id.desc())
        )
    )
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "viewer": viewer,
            "tokens": tokens,
            "scopes": sorted(SCOPES.items()),
            "minted": secret,
            "minted_name": name,
        },
    )


@router.post("/settings/tokens/{token_id}/revoke")
def settings_revoke(
    token_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """Stop a token working, at once and for good."""
    from datetime import datetime, timezone

    from .models import ApiToken

    viewer = _viewer(request, session)
    _require_admin(viewer)

    token = session.get(ApiToken, token_id)
    if token is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such token")
    if token.revoked_at is None:
        token.revoked_at = datetime.now(timezone.utc)
    commit_now(session)
    return RedirectResponse("/settings#tokens", status_code=status.HTTP_303_SEE_OTHER)


def _require_admin(viewer) -> None:
    """Tokens are keys to the whole catalogue, so only an administrator makes
    them. In development, where authentication is off, everyone is one."""
    if not viewer.can("admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only an administrator can manage tokens")


@router.get("/app", response_class=HTMLResponse)
def app_page(request: Request, session: Session = Depends(get_session)) -> Response:
    """Where a tablet goes to install or update the Android client.

    The tablet is already on this network and already talks to this server, so
    the server hands it its own client. That is the whole update mechanism:
    open the page, press the button.
    """
    from .api.app_release import APK_NAME, current

    viewer = _viewer(request, session)
    return templates.TemplateResponse(
        request,
        "app.html",
        {
            "viewer": viewer,
            "release": current(),
            "apk_name": APK_NAME,
            "apk_root": get_settings().apk_root,
        },
    )


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


#: Wrong guesses per address before that address is asked to wait.  Generous
#: enough for a mistyped password on a tablet keyboard, small enough that
#: guessing is not a strategy.
MAX_ATTEMPTS = 8
LOCKOUT = timedelta(minutes=5)

#: address -> (failures, when the lockout ends).  In memory on purpose: this is
#: one household on a private network, and a restart clearing it is a feature,
#: not a hole.
_attempts: dict[str, tuple[int, datetime]] = {}


def _caller(request: Request) -> str:
    return (request.client.host if request.client else "?") or "?"


def _locked_until(request: Request) -> datetime | None:
    failures, until = _attempts.get(_caller(request), (0, datetime.min.replace(tzinfo=timezone.utc)))
    now = datetime.now(timezone.utc)
    if failures >= MAX_ATTEMPTS and until > now:
        return until
    return None


def _note_failure(request: Request) -> None:
    caller = _caller(request)
    failures, _ = _attempts.get(caller, (0, datetime.now(timezone.utc)))
    _attempts[caller] = (failures + 1, datetime.now(timezone.utc) + LOCKOUT)


def _clear_failures(request: Request) -> None:
    _attempts.pop(_caller(request), None)


@auth_router.get("/login")
async def login(request: Request):
    settings = get_settings()

    if settings.password_enabled:
        until = _locked_until(request)
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "next_url": request.query_params.get("next", "/"),
                "error": "Too many attempts. Try again in a few minutes." if until else "",
                "locked": bool(until),
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS if until else status.HTTP_200_OK,
        )

    if not settings.oidc_enabled:
        if settings.auth_disabled:
            return RedirectResponse("/")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No way to sign in is configured. Set SMS_PASSWORD, or SMS_OIDC_ISSUER "
            "and SMS_OIDC_CLIENT_ID for authentik.",
        )
    oauth = _oauth()
    return await oauth.authentik.authorize_redirect(request, f"{settings.base_url}/auth/callback")


@auth_router.post("/login")
async def login_submit(request: Request):
    """Check the shared password and start a session."""
    from .auth import PASSWORD_SUBJECT, password_matches

    settings = get_settings()
    if not settings.password_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no password is configured")

    form = await request.form()
    target = (form.get("next") or "/").strip()
    # Only ever back into this site, so a crafted link cannot bounce somebody
    # through the login form and out to somewhere else.
    if not target.startswith("/") or target.startswith("//"):
        target = "/"

    if _locked_until(request) is not None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next_url": target, "error": "Too many attempts. Try again in a few minutes.",
             "locked": True},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    if not password_matches(form.get("password") or ""):
        _note_failure(request)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next_url": target, "error": "That is not the password.", "locked": False},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    _clear_failures(request)
    request.session["sub"] = PASSWORD_SUBJECT
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


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

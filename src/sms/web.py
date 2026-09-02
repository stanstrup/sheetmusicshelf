"""The browse UI and reader.

Server-rendered Jinja and plain forms.  No framework, no bundle, and nothing
loaded from a CDN -- a phone reaching this over a VPN may have no route to the
open internet, so every asset the page needs is served from here.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from .auth import Principal, current_principal
from .config import get_settings
from .db import get_session
from .models import Collection, Piece, SourceFile

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

    # Rejected entries are not part of the catalogue.
    query = query.where(Piece.review_state != "rejected")

    orderings = {
        "composer": (Piece.composer_name.asc(), Piece.catalog_display.asc(), Piece.title.asc()),
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

    return {
        "composer": values(Piece.composer_name),
        "form": values(Piece.form),
        "key": values(Piece.music_key),
        "collections": session.execute(
            select(Collection.id, Collection.name).order_by(Collection.name)
        ).all(),
    }


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
    sort: str = "composer",
    page: int = 1,
) -> Response:
    viewer = _viewer(request, session)
    params = {
        "q": q, "composer": composer, "form": form, "key": key,
        "status": status_, "route": route, "collection": collection, "sort": sort,
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
    editions = []
    if piece.catalog_display:
        editions = list(
            session.scalars(
                select(Piece)
                .where(
                    Piece.catalog_display == piece.catalog_display,
                    Piece.composer_name == piece.composer_name,
                    Piece.id != piece.id,
                )
                .limit(20)
            )
        )
    return templates.TemplateResponse(
        request,
        "piece.html",
        {
            "viewer": viewer, "piece": piece,
            "file": piece.source_file, "collection": piece.source_file.collection,
            "siblings": siblings, "editions": editions,
        },
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
            "pages": list(range(1, piece.page_count + 1)),
            "spread": bool(spread),
        },
    )


# --- PWA ------------------------------------------------------------------

@router.get("/manifest.webmanifest")
def manifest() -> JSONResponse:
    return JSONResponse(
        {
            "name": "Sheet Music Shelf",
            "short_name": "Shelf",
            "start_url": "/",
            "display": "standalone",
            "orientation": "any",
            "background_color": "#f3f5f8",
            "theme_color": "#2c4a8c",
            "icons": [
                {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
            ],
        },
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

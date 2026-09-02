"""The FastAPI application.

Serves the JSON API, the OIDC login round-trip, and (from phase 2) the
server-rendered browse UI and PWA reader.
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from .api import catalog, collections, curation, pages
from .auth import Principal, current_principal
from .config import get_settings
from .db import engine
from .web import STATIC_DIR, auth_router
from .web import router as web_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sms")

DESCRIPTION = """
A cataloguing server for a personal sheet music library.

**The model in one line:** a *work* is what you look for, a *file* is where a
copy of it happens to live, and a *piece* is the page range inside that file.

**For external curation tools**, start at `GET /api/v1/curation/queue`. Every
uncertain piece comes back with the individual signals behind each guess and
their weights, so you can see why it is uncertain before proposing anything.
Send proposals to `/api/v1/curation/candidates` (scored against everything
else) or decisions to `/api/v1/curation/decisions` (final). Authenticate with
`Authorization: Bearer <token>`; mint a token with `sms token create`.
"""


def create_app() -> FastAPI:
    settings = get_settings()

    if settings.auth_disabled and not settings.debug:
        raise RuntimeError(
            "SMS_AUTH_DISABLED is set without SMS_DEBUG. Refusing to start an "
            "unauthenticated server that can read your library."
        )

    app = FastAPI(
        title="Sheet Music Shelf",
        version="0.1.0",
        description=DESCRIPTION,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax")

    api_prefix = "/api/v1"
    app.include_router(catalog.router, prefix=api_prefix)
    app.include_router(collections.router, prefix=api_prefix)
    app.include_router(curation.router, prefix=api_prefix)
    app.include_router(pages.router, prefix=api_prefix)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(auth_router)
    # Last: the browse UI owns "/" and would otherwise shadow the routes above.
    app.include_router(web_router)

    @app.get("/health", tags=["meta"], summary="Liveness and database reachability")
    def health() -> dict:
        try:
            with engine().connect() as connection:
                connection.execute(text("SELECT 1"))
            database = "ok"
        except Exception as exc:                      # noqa: BLE001 - reported, not raised
            database = f"unreachable: {type(exc).__name__}"
        return {"status": "ok" if database == "ok" else "degraded", "database": database}

    @app.get("/api/v1/whoami", tags=["meta"], summary="What this credential can do")
    def whoami(principal: Principal = Depends(current_principal)) -> dict:
        return {
            "display_name": principal.display_name,
            "via": principal.via,
            "is_admin": principal.is_admin,
            "scopes": sorted(principal.scopes),
        }

    log.info(
        "starting; source=%s managed=%s oidc=%s",
        settings.source_root, settings.managed_root, settings.oidc_enabled,
    )
    return app


app = create_app()

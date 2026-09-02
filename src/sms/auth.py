"""Authentication for people and for agents.

Two paths, deliberately:

* **People** sign in through authentik (OIDC), so there is no second password
  to manage and group membership comes from the identity provider.
* **Agents and devices** present a bearer token.  External curation tools are
  first-class clients here, not a workaround, so a token is a real credential
  with its own scopes that can be revoked without disturbing anyone's login.

Tokens are stored hashed: a leaked database row must not be a working key.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_session
from .models import ApiToken, AppUser

TOKEN_PREFIX = "sms_"

SCOPES = {
    "catalog:read": "Browse the catalogue.",
    "catalog:write": "Edit catalogue entries and personal fields.",
    "curation:read": "Read the review queue and the signals behind each guess.",
    "curation:write": "Propose values and accept or reject them.",
    "admin": "Manage collections, scans, users and tokens.",
}


@dataclass
class Principal:
    """Whoever is making this request."""

    user_id: int | None = None
    display_name: str = "anonymous"
    scopes: set[str] = field(default_factory=set)
    is_admin: bool = False
    via: str = "none"       # "oidc" | "token" | "open"

    def can(self, scope: str) -> bool:
        return self.is_admin or "admin" in self.scopes or scope in self.scopes


def hash_token(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def mint_token(
    session: Session,
    name: str,
    scopes: list[str],
    user_id: int | None = None,
) -> tuple[str, ApiToken]:
    """Create a token.  The plaintext is returned once and never stored."""
    unknown = sorted(set(scopes) - set(SCOPES))
    if unknown:
        raise ValueError(f"unknown scopes: {', '.join(unknown)}")
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(name=name, token_hash=hash_token(secret), scopes=sorted(set(scopes)), user_id=user_id)
    session.add(row)
    session.flush()
    return secret, row


def _principal_from_token(session: Session, secret: str) -> Principal | None:
    row = session.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(secret)))
    if row is None or row.revoked_at is not None:
        return None
    row.last_used_at = datetime.now(timezone.utc)
    return Principal(
        user_id=row.user_id,
        display_name=f"token:{row.name}",
        scopes=set(row.scopes or []),
        is_admin="admin" in (row.scopes or []),
        via="token",
    )


def _principal_from_session(session: Session, request: Request) -> Principal | None:
    subject = request.session.get("sub") if hasattr(request, "session") else None
    if not subject:
        return None
    user = session.scalar(select(AppUser).where(AppUser.oidc_subject == subject))
    if user is None:
        return None
    return Principal(
        user_id=user.id,
        display_name=user.display_name or user.email or subject,
        scopes=set(SCOPES) if user.is_admin else {"catalog:read", "catalog:write", "curation:read", "curation:write"},
        is_admin=user.is_admin,
        via="oidc",
    )


def current_principal(request: Request, session: Session = Depends(get_session)) -> Principal:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        principal = _principal_from_token(session, header[7:].strip())
        if principal is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or revoked token")
        return principal

    principal = _principal_from_session(session, request)
    if principal is not None:
        return principal

    settings = get_settings()
    if settings.auth_disabled:
        # Development only; main.py refuses to start with this set outside debug.
        return Principal(display_name="dev", scopes=set(SCOPES), is_admin=True, via="open")

    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "sign in, or present a bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require(scope: str):
    """Dependency factory: ``Depends(require("curation:write"))``."""

    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.can(scope):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"this credential lacks the {scope} scope",
            )
        return principal

    return dependency

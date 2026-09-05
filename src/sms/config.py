"""Runtime configuration.

Everything is environment-driven so the container needs no config file, and the
same settings object serves the API, the worker and the CLI.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMS_", env_file=".env", extra="ignore")

    # --- database ---------------------------------------------------------
    database_url: str = "postgresql+psycopg://sms:sms@localhost:5432/sms"

    # --- library ----------------------------------------------------------
    #: Where the untouched originals live.  Mounted read-only in the container.
    source_root: Path = Path("/library/source")
    #: Where the app builds its own tree.  Originals are copied here, never moved.
    managed_root: Path = Path("/library/managed")
    #: Where new material is dropped for import, Calibre-style.  Files here are
    #: filed into the managed tree and then removed, so this folder is a queue
    #: and not a location: nothing is ever read from it after import.
    ingest_root: Path = Path("/library/ingest")
    #: Thumbnails and extracted text.  A named volume, never the CIFS share.
    cache_root: Path = Path("/cache")
    #: Where a published Android APK is read from, so the tablet can install
    #: from the server it already talks to.  Inside the library mount, because
    #: that is already shared with the host and needs no new volume.
    apk_root: Path = Path("/library/managed/_app")

    # --- behaviour --------------------------------------------------------
    auto_accept: float = 0.80
    review_floor: float = 0.50
    #: The worker pauses between files while the host's 1-minute load average is
    #: above this.  Zero disables the check (and any machine without /proc).
    load_ceiling: float = 6.0
    scan_batch_size: int = 200

    # --- auth -------------------------------------------------------------
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    #: Signs the browser session cookie.  Must be set in production.
    secret_key: str = "change-me"
    #: When true, unauthenticated requests are treated as an admin.  Local
    #: development only -- refused at startup unless the app is in debug mode.
    auth_disabled: bool = False

    base_url: str = "http://localhost:8000"
    debug: bool = False

    # --- outbound identity ------------------------------------------------
    #: MusicBrainz requires a User-Agent naming the application *and* a way to
    #: reach whoever runs it, and blocks clients that do not comply. There is
    #: deliberately no default: a placeholder contact is worse than none,
    #: because it looks compliant while being unreachable.
    contact: str = ""

    @property
    def has_contact(self) -> bool:
        who = self.contact.strip()
        # An address or a URL. "unknown", "none" and the like are not contacts.
        return "@" in who or who.startswith(("http://", "https://"))

    @property
    def user_agent(self) -> str:
        if not self.has_contact:
            raise RuntimeError(
                "SMS_CONTACT is not set to an email address or URL. MusicBrainz "
                "requires a reachable contact in the User-Agent and blocks "
                "clients that omit one, so no request is made without it."
            )
        return f"SheetMusicShelf/0.1 ( {self.contact.strip()} )"

    @field_validator(
        "source_root", "managed_root", "cache_root", "ingest_root", "apk_root",
        mode="before",
    )
    @classmethod
    def _as_path(cls, value: object) -> object:
        return Path(str(value)) if value else value

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()

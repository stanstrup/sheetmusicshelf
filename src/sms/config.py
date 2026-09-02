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
    #: Thumbnails and extracted text.  A named volume, never the CIFS share.
    cache_root: Path = Path("/cache")

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

    @field_validator("source_root", "managed_root", "cache_root", mode="before")
    @classmethod
    def _as_path(cls, value: object) -> object:
        return Path(str(value)) if value else value

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()

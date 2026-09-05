"""Handing the Android app to a tablet that is already talking to this server.

There is no app store here and no reason to want one. The tablet is on the LAN
or the VPN, it already knows this server's address, and it already holds a
token for it. So the server serves its own client: point the tablet's browser
at ``/app`` and install what it offers.

The alternative -- a git remote, a release, a download over the internet --
needs the tablet to have internet at all, which is exactly the assumption this
project spent its design deciding not to make.

The APK is read from a directory rather than baked into the image, so a rebuild
of the client does not mean a rebuild of the server. `android/tools/publish.cmd`
puts it there.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from ..auth import Principal, require
from ..config import get_settings

router = APIRouter(tags=["app"])

APK_NAME = "sheetmusicshelf.apk"
VERSION_NAME = "version.json"

#: Android's own type for an APK. Served with it so the browser offers to
#: install rather than to open as an unknown file.
APK_MEDIA_TYPE = "application/vnd.android.package-archive"


@dataclass(frozen=True)
class Release:
    """What is currently on offer, if anything."""

    path: Path
    version_code: int
    version_name: str
    size: int
    built_at: str

    @property
    def megabytes(self) -> float:
        return round(self.size / (1024 * 1024), 1)


def current() -> Release | None:
    """The published APK, or None when none has been published yet."""
    root = get_settings().apk_root
    apk = root / APK_NAME
    if not apk.exists():
        return None

    meta: dict = {}
    manifest = root / VERSION_NAME
    if manifest.exists():
        try:
            # utf-8-sig: PowerShell's Set-Content -Encoding utf8 writes a BOM,
            # and json.loads treats it as a syntax error, so the manifest was
            # silently ignored and every version read back as unknown.
            meta = json.loads(manifest.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            meta = {}

    stat = apk.stat()
    return Release(
        path=apk,
        version_code=int(meta.get("versionCode") or 0),
        version_name=str(meta.get("versionName") or "unknown"),
        size=stat.st_size,
        built_at=str(
            meta.get("builtAt")
            or datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M")
        ),
    )


@router.get("/app/version", summary="What version of the Android client is on offer")
def version(_: Principal = Depends(require("catalog:read"))) -> dict:
    """Small enough for the app itself to poll when it opens.

    The app compares this with its own versionCode and says nothing unless
    there is something newer, so a tablet that is up to date is never nagged.
    """
    release = current()
    if release is None:
        return {"available": False}
    return {
        "available": True,
        "versionCode": release.version_code,
        "versionName": release.version_name,
        "size": release.size,
        "builtAt": release.built_at,
        "url": f"/app/{APK_NAME}",
    }


@router.get(f"/app/{APK_NAME}", summary="Download the Android client")
def download(_: Principal = Depends(require("catalog:read"))) -> FileResponse:
    release = current()
    if release is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "no app has been published; run android/tools/publish.cmd",
        )
    return FileResponse(
        release.path,
        media_type=APK_MEDIA_TYPE,
        filename=APK_NAME,
        headers={"Cache-Control": "no-store"},
    )

"""Linking a work to IMSLP and MusicBrainz.

The same standard as the composer enricher: a match must be *checkable*, and a
miss returns nothing rather than a plausible-looking wrong link.

The catalogue number does the work.  IMSLP titles its pages
``Piano Sonata No.5, K.283 (Mozart, Wolfgang Amadeus)`` and MusicBrainz stores
the same number as a work attribute, so a candidate is only accepted when its
title carries **both** the composer's surname and the catalogue number we
searched for.  Title similarity alone is not enough -- "Piano Sonata No. 5"
exists for a dozen composers, and several times over for some of them.

Works with no catalogue number are not looked up at all.  There is nothing
here strong enough to identify them, and guessing is what this project spends
its time avoiding.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from .wikipedia import USER_AGENT, LookupUnavailable, _get, _client

log = logging.getLogger("sms.enrich.canonical")

IMSLP_API = "https://imslp.org/api.php"
MUSICBRAINZ_API = "https://musicbrainz.org/ws/2"

#: MusicBrainz asks for no more than one request a second from a named client.
MUSICBRAINZ_PAUSE = 1.3
#: IMSLP is a small site. Four variant queries fired back to back is what
#: produced its 503s, not the interval between works.
IMSLP_PAUSE = 0.6

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


@dataclass
class CanonicalLinks:
    imslp_title: str = ""
    imslp_url: str = ""
    musicbrainz_id: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def any_found(self) -> bool:
        return bool(self.imslp_url or self.musicbrainz_id)


def catalogue_variants(system: str, number: int, suffix: str = "") -> list[str]:
    """The spellings a catalogue number appears in across these sites.

    IMSLP writes "K.283" and "BWV 772"; MusicBrainz writes "K. 283". Searching
    only one spelling misses most of the matches.
    """
    body = f"{number}{suffix or ''}"
    return [f"{system}.{body}", f"{system}. {body}", f"{system} {body}", f"{system}{body}"]


def _tokens(text: str) -> set[str]:
    return {t for t in _NON_WORD.split((text or "").casefold()) if t}


def mentions_catalogue(text: str, system: str, number: int, suffix: str = "") -> bool:
    """Whether ``text`` cites this exact catalogue number.

    Guards against K. 283 matching K. 2831 or K. 28, which a naive substring
    test would happily do.
    """
    pattern = re.compile(
        rf"\b{re.escape(system)}\.?\s*0*{number}{re.escape(suffix or '')}(?![\d])",
        re.IGNORECASE,
    )
    return bool(pattern.search(text or ""))


def surname(canonical_name: str) -> str:
    parts = [p for p in (canonical_name or "").split() if p]
    return parts[-1] if parts else ""


# --- IMSLP -----------------------------------------------------------------

def find_imslp(
    client: httpx.Client,
    composer_name: str,
    system: str,
    number: int,
    suffix: str = "",
) -> tuple[str, str] | None:
    """Search IMSLP's MediaWiki for a work page.  Returns (title, url).

    Raises :class:`LookupUnavailable` if every query errored, so a failed
    search is never reported as an absent work.
    """
    last = surname(composer_name)
    attempts = failures = 0
    for variant in catalogue_variants(system, number, suffix):
        if attempts:
            time.sleep(IMSLP_PAUSE)
        attempts += 1
        response = _get(client, IMSLP_API, params={
            "action": "query", "format": "json", "list": "search",
            "srsearch": f"{variant} {last}", "srlimit": 8, "srnamespace": 0,
        })
        if response.status_code != 200:
            failures += 1
            continue
        for hit in (response.json().get("query") or {}).get("search", []):
            title = hit.get("title") or ""
            # IMSLP page titles carry the composer in parentheses, so both
            # halves of the check can be made on the title alone.
            if not mentions_catalogue(title, system, number, suffix):
                continue
            if last and last.casefold() not in title.casefold():
                continue
            slug = title.replace(" ", "_")
            return title, f"https://imslp.org/wiki/{slug}"
    if failures == attempts:
        raise LookupUnavailable(f"IMSLP: every query failed ({failures})")
    return None


# --- MusicBrainz -----------------------------------------------------------

def find_musicbrainz(
    client: httpx.Client,
    composer_name: str,
    system: str,
    number: int,
    suffix: str = "",
) -> tuple[str, str] | None:
    """Search MusicBrainz works.  Returns (mbid, matched title)."""
    last = surname(composer_name)
    attempts = failures = 0
    for variant in catalogue_variants(system, number, suffix):
        attempts += 1
        query = f'work:"{variant}" AND artist:"{last}"' if last else f'work:"{variant}"'
        response = _get(client, f"{MUSICBRAINZ_API}/work", params={
            "query": query, "fmt": "json", "limit": 8,
        })
        time.sleep(MUSICBRAINZ_PAUSE)
        if response.status_code != 200:
            failures += 1
            continue
        for work in response.json().get("works", []):
            title = work.get("title") or ""
            haystack = " ".join(
                [title, *(str(v) for v in (work.get("disambiguation"),) if v)]
            )
            # A MusicBrainz work stores the catalogue number as an attribute as
            # often as in the title, so check both.
            for attribute in work.get("attributes", []):
                haystack += " " + str(attribute.get("value") or "")
            if not mentions_catalogue(haystack, system, number, suffix):
                continue
            if last:
                artists = " ".join(
                    (relation.get("artist") or {}).get("name", "")
                    for relation in work.get("relations", [])
                )
                credited = artists or title
                if last.casefold() not in credited.casefold() and last.casefold() not in haystack.casefold():
                    continue
            return work.get("id", ""), title
    if failures == attempts:
        raise LookupUnavailable(f"MusicBrainz: every query failed ({failures})")
    return None


def lookup(
    composer_name: str,
    system: str | None,
    number: int | None,
    suffix: str = "",
) -> CanonicalLinks:
    """Find canonical sources for one work.

    Raises :class:`LookupUnavailable` for transient failures so the caller can
    defer rather than record a negative it has not really established.
    """
    links = CanonicalLinks()
    if not system or number is None:
        links.notes.append("no catalogue number; nothing strong enough to match on")
        return links

    with _client() as client:
        client.headers["User-Agent"] = USER_AGENT
        try:
            found = find_imslp(client, composer_name, system, number, suffix)
        except LookupUnavailable:
            raise
        except httpx.HTTPError as exc:
            raise LookupUnavailable(str(exc)) from exc
        if found:
            links.imslp_title, links.imslp_url = found
        else:
            links.notes.append("no IMSLP page cites this catalogue number")

        try:
            found = find_musicbrainz(client, composer_name, system, number, suffix)
        except LookupUnavailable:
            raise
        except httpx.HTTPError as exc:
            raise LookupUnavailable(str(exc)) from exc
        if found:
            links.musicbrainz_id = found[0]
        else:
            links.notes.append("no MusicBrainz work cites this catalogue number")

    return links

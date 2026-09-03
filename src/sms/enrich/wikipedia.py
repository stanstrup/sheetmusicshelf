"""Composer enrichment from Wikidata and Wikipedia.

**Wikidata first, deliberately.**  The obvious approach -- search Wikipedia for
"<name> composer" and take the top hit -- ranks the 1979 play *Amadeus* above
Mozart, and will cheerfully return an article about anything.  Instead a
candidate must *be* a person who writes or performs music (P31=Q5 plus a music
occupation) or a band (P31 a musical group), and its label must still resemble
the name asked for.  A miss returns nothing rather than a confident answer about
the wrong subject, which is the same standard the ingest scorer holds itself to.

A *transient* failure is not a miss.  Wikimedia rate-limits, and reporting a 429
as "no article found" would record a definitive negative that is never retried,
so those raise :class:`LookupUnavailable` instead.

Then, per composer:

1. Wikidata entity -- birth and death years, the portrait file, the article link.
2. Wikipedia summary -- a paragraph of description, via the Wikidata sitelink.
3. Commons image info -- the portrait itself, plus **who to credit and under
   what licence**, because most of these images are CC-BY-SA and reusing them
   without attribution is not on.

The portrait is downloaded and cached locally rather than hot-linked: a phone
reaching this server over a VPN may have no route to the open internet, and
hot-linking Commons from every page view is rude besides.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from rapidfuzz import fuzz

from ..config import get_settings
from ..music.periods import derive_period

log = logging.getLogger("sms.enrich")

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Wikimedia asks for a descriptive User-Agent that identifies the tool and a
# way to reach its operator.  Anonymous scraping gets rate-limited, rightly.
USER_AGENT = "SheetMusicShelf/0.1 (self-hosted personal music library; +https://github.com/)"

TIMEOUT = httpx.Timeout(15.0, connect=8.0)
#: Wikimedia rate-limits anonymous clients. Back off and retry rather than
#: treating a 429 as an answer.
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 4
BACKOFF_BASE = 2.0
MAX_IMAGE_BYTES = 8 * 1024 * 1024
PORTRAIT_WIDTH = 640

#: Wikidata items used to prove a candidate is a person who wrote music.
HUMAN = "Q5"
#: ...or a band.  Half this library is popular song credited to a group, and
#: requiring a *person* left Abba and The Beatles with nothing.  A group has no
#: P106 occupation, so being a musical ensemble is the qualification itself.
MUSICAL_GROUPS = {
    "Q215380",   # musical group
    "Q2088357",  # musical ensemble
    "Q5741069",  # rock band
    "Q9212979",  # musical duo
    "Q281643",   # pop music group... also used for duos
}
MUSIC_OCCUPATIONS = {
    "Q36834",    # composer
    "Q1259917",  # lyricist... also used for song writers
    "Q486748",   # pianist
    "Q765778",   # organist
    "Q158852",   # conductor
    "Q639669",   # musician
    "Q753110",   # songwriter
}
#: How closely a candidate's label must resemble the name we searched for.
NAME_MATCH_FLOOR = 72

# Wikidata times carry an explicit sign; "-0500-..." is 500 BCE, not year 500.
# Dropping the sign would file a Greek philosopher as a Renaissance composer.
_YEAR = re.compile(r"^\+(\d{4})")
_HTML = re.compile(r"<[^>]+>")


@dataclass
class ComposerFacts:
    name: str
    description: str = ""
    born: int | None = None
    died: int | None = None
    period: str | None = None
    wikipedia_url: str = ""
    wikidata_id: str = ""
    #: The label Wikidata actually matched, so a wrong match is visible rather
    #: than silently adopted as fact.
    matched_label: str = ""
    image_source_url: str = ""
    image_credit: str = ""
    image_license: str = ""
    image_bytes: bytes | None = field(default=None, repr=False)
    image_suffix: str = ".jpg"
    notes: list[str] = field(default_factory=list)

    @property
    def has_image(self) -> bool:
        return bool(self.image_bytes)


class LookupUnavailable(RuntimeError):
    """The service could not be reached or asked us to slow down.

    Distinct from "not found": the caller must not record a result, because
    there is nothing to record and the name deserves another try later.
    """


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )


def _get(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    """GET with backoff on the statuses that mean "later, not never"."""
    delay = BACKOFF_BASE
    for attempt in range(MAX_RETRIES):
        try:
            response = client.get(url, **kwargs)
        except httpx.HTTPError as exc:
            if attempt == MAX_RETRIES - 1:
                raise LookupUnavailable(str(exc)) from exc
            time.sleep(delay)
            delay *= 2
            continue

        if response.status_code in RETRY_STATUSES:
            if attempt == MAX_RETRIES - 1:
                # Name the host: "503 after 4 tries" does not say which of the
                # three services gave up, which is the first thing you need.
                raise LookupUnavailable(
                    f"{httpx.URL(url).host}: {response.status_code} after {MAX_RETRIES} tries"
                )
            # Honour Retry-After when the server sets it.
            wait = response.headers.get("retry-after")
            try:
                pause = float(wait) if wait else delay
            except ValueError:
                pause = delay
            log.info("wikidata %s; waiting %.0fs", response.status_code, pause)
            time.sleep(min(pause, 60))
            delay *= 2
            continue
        return response
    raise LookupUnavailable("exhausted retries")


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", _HTML.sub("", text or "")).strip()


def _year(value: str | None) -> int | None:
    """Wikidata times look like "+1756-01-27T00:00:00Z"; we want 1756."""
    if not value:
        return None
    match = _YEAR.match(value)
    if not match:
        return None
    year = int(match.group(1))
    return year if 500 <= year <= 2100 else None


def _find_entity(client: httpx.Client, name: str) -> tuple[str, str, dict] | None:
    """Find the Wikidata item for a composer: (qid, matched label, claims).

    Deliberately not a Wikipedia full-text search.  Searching "Mozart composer"
    ranks the 1979 play *Amadeus* above the man himself, and a search result is
    happy to hand back an article about anything.  Here the candidate must
    *be* a human (P31=Q5) whose occupations (P106) include composing, and whose
    label still resembles what we asked for -- so a miss returns nothing rather
    than a confident answer about the wrong subject.
    """
    response = _get(client, WIKIDATA_API, params={
        "action": "wbsearchentities", "format": "json", "language": "en",
        "uselang": "en", "type": "item", "limit": 10, "search": name,
    })
    response.raise_for_status()
    candidates = response.json().get("search") or []

    for candidate in candidates:
        qid = candidate.get("id")
        label = candidate.get("label") or ""
        if not qid:
            continue
        if fuzz.token_set_ratio(name.casefold(), label.casefold()) < NAME_MATCH_FLOOR:
            continue

        claims = _entity(client, qid)
        if not qualifies(claims):
            continue
        return qid, label, claims
    return None


def _claim_ids(claims: dict, prop: str) -> set[str]:
    found: set[str] = set()
    for statement in claims.get(prop, []):
        value = (statement.get("mainsnak") or {}).get("datavalue", {}).get("value")
        if isinstance(value, dict) and "id" in value:
            found.add(value["id"])
    return found


def _is_human(claims: dict) -> bool:
    return HUMAN in _claim_ids(claims, "P31")


def _is_group(claims: dict) -> bool:
    return bool(_claim_ids(claims, "P31") & MUSICAL_GROUPS)


def _is_composer(claims: dict) -> bool:
    return bool(_claim_ids(claims, "P106") & MUSIC_OCCUPATIONS)


def qualifies(claims: dict) -> bool:
    """Whether this item is a plausible attribution for a piece of music.

    Either a person who writes or performs music, or a band.  A play about a
    composer, a film, or a politician is none of those and is refused.
    """
    return (_is_human(claims) and _is_composer(claims)) or _is_group(claims)


def _article_title(claims_entity: dict) -> str:
    sitelinks = claims_entity.get("sitelinks") or {}
    return (sitelinks.get("enwiki") or {}).get("title", "")


def _summary(client: httpx.Client, title: str) -> tuple[str, str]:
    response = _get(
        client,
        f"https://en.wikipedia.org/api/rest_v1/page/summary/{httpx.URL(title).path.lstrip('/')}",
    )
    if response.status_code != 200:
        return "", ""
    data = response.json()
    return (
        _strip_html(data.get("extract") or ""),
        (data.get("content_urls", {}).get("desktop", {}) or {}).get("page", ""),
    )


_ENTITY_CACHE: dict[str, dict] = {}


def _entity_full(client: httpx.Client, wikidata_id: str) -> dict:
    """Claims plus sitelinks, memoised for the life of the process."""
    if wikidata_id in _ENTITY_CACHE:
        return _ENTITY_CACHE[wikidata_id]
    response = _get(client, WIKIDATA_API, params={
        "action": "wbgetentities", "format": "json", "ids": wikidata_id,
        "props": "claims|sitelinks|labels", "languages": "en", "sitefilter": "enwiki",
    })
    response.raise_for_status()
    entity = (response.json().get("entities") or {}).get(wikidata_id) or {}
    _ENTITY_CACHE[wikidata_id] = entity
    return entity


def _entity(client: httpx.Client, wikidata_id: str) -> dict:
    return _entity_full(client, wikidata_id).get("claims") or {}


def _claim_value(claims: dict, prop: str) -> str | None:
    for statement in claims.get(prop, []):
        value = (statement.get("mainsnak") or {}).get("datavalue", {}).get("value")
        if isinstance(value, dict) and "time" in value:
            return value["time"]
        if isinstance(value, str):
            return value
    return None


def _commons_image(client: httpx.Client, filename: str) -> dict:
    """Fetch a scaled portrait plus its attribution metadata."""
    response = _get(client, COMMONS_API, params={
        "action": "query", "format": "json", "titles": f"File:{filename}",
        "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": PORTRAIT_WIDTH,
    })
    response.raise_for_status()
    pages = (response.json().get("query") or {}).get("pages") or {}
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        if info:
            return info
    return {}


def lookup(name: str) -> ComposerFacts | None:
    """Gather what Wikipedia and Wikidata know about one composer.

    Returns None only when no article can be found at all; a partial result
    (dates but no portrait, say) still comes back, with what is missing noted.
    """
    facts = ComposerFacts(name=name)
    try:
        with _client() as client:
            found = _find_entity(client, name)
            if not found:
                return None
            facts.wikidata_id, facts.matched_label, claims = found

            title = _article_title(_entity_full(client, facts.wikidata_id))
            if title:
                facts.description, facts.wikipedia_url = _summary(client, title)
                if not facts.wikipedia_url:
                    facts.wikipedia_url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
            else:
                facts.notes.append("no English Wikipedia article")

            # A band has no birthday: its inception (P571) and dissolution
            # (P576) are the equivalent span, and give it a period like anyone.
            facts.born = _year(_claim_value(claims, "P569")) or _year(_claim_value(claims, "P571"))
            facts.died = _year(_claim_value(claims, "P570")) or _year(_claim_value(claims, "P576"))
            facts.period = derive_period(facts.born, facts.died)

            filename = _claim_value(claims, "P18")
            if not filename:
                facts.notes.append("no portrait on Wikidata")
                return facts

            info = _commons_image(client, filename)
            thumb = info.get("thumburl") or info.get("url")
            if not thumb:
                facts.notes.append("portrait listed but not retrievable")
                return facts

            meta = info.get("extmetadata") or {}
            facts.image_source_url = info.get("descriptionurl") or thumb
            facts.image_credit = _strip_html(
                (meta.get("Artist") or {}).get("value", "")
            ) or "Wikimedia Commons"
            facts.image_license = _strip_html(
                (meta.get("LicenseShortName") or {}).get("value", "")
            ) or "see source"

            image = client.get(thumb, headers={"Accept": "image/*"})
            if image.status_code == 200 and len(image.content) <= MAX_IMAGE_BYTES:
                facts.image_bytes = image.content
                suffix = Path(httpx.URL(thumb).path).suffix.lower()
                facts.image_suffix = suffix if suffix in (".jpg", ".jpeg", ".png", ".webp") else ".jpg"
            else:
                facts.notes.append(f"portrait download failed ({image.status_code})")
    except LookupUnavailable:
        raise
    except httpx.HTTPError as exc:
        raise LookupUnavailable(str(exc)) from exc
    return facts


def portrait_path(composer_id: int, suffix: str = ".jpg") -> Path:
    return get_settings().cache_root / "portraits" / f"{composer_id}{suffix}"


def save_portrait(composer_id: int, facts: ComposerFacts) -> Path | None:
    if not facts.image_bytes:
        return None
    path = portrait_path(composer_id, facts.image_suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(facts.image_bytes)
    return path

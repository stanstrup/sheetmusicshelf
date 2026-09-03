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

from .wikipedia import LookupUnavailable, _client, _get, user_agent

log = logging.getLogger("sms.enrich.canonical")

IMSLP_API = "https://imslp.org/api.php"
MUSICBRAINZ_API = "https://musicbrainz.org/ws/2"

# Spacing is enforced globally in sms.enrich.throttle, not by sleeping here:
# per-call sleeps only space requests within one lookup, and it was the gaps
# *between* lookups that were missing.

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


@dataclass
class CanonicalLinks:
    imslp_title: str = ""
    imslp_url: str = ""
    musicbrainz_id: str = ""
    musicbrainz_title: str = ""
    year: int | None = None
    year_note: str = ""
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


# "K. 189d/279", "K.283/189h" -- a citation may list several numbers for the
# same work, separated by slashes, in either order. Köchel especially is
# routinely written with both the original and a revised number.
_CITATION = re.compile(r"\b([A-Za-z]{1,4})\.?\s*([\dA-Za-z]+(?:\s*/\s*[\dA-Za-z]+)*)")
_ALTERNATE = re.compile(r"^0*(\d+)([a-z]?)$", re.IGNORECASE)


def mentions_catalogue(text: str, system: str, number: int, suffix: str = "") -> bool:
    """Whether ``text`` cites this exact catalogue number.

    Guards against K. 283 matching K. 2831 or K. 28, which a naive substring
    test would happily do -- while still matching a compound citation. Requiring
    the number to follow the letters directly missed MusicBrainz's
    "K. 189d/279" and IMSLP's "K.283/189h", which are the same works written
    with their revised Köchel numbers.
    """
    wanted_suffix = (suffix or "").lower()
    for match in _CITATION.finditer(text or ""):
        if match.group(1).lower() != system.lower():
            continue
        for alternate in match.group(2).split("/"):
            parsed = _ALTERNATE.match(alternate.strip())
            if parsed and int(parsed.group(1)) == number and parsed.group(2).lower() == wanted_suffix:
                return True
    return False


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

# An ordinal is the discriminator in a title like "Sonata No. 1 in C Major":
# without it, "Sonata AND Major" ranks a dozen other Mozart sonatas first.
_ORDINAL_IN_TITLE = re.compile(r"\b(?:no\.?|nr\.?|#)\s*(\d+)", re.IGNORECASE)
#: Words too common to narrow anything.
_STOP_TERMS = {"the", "and", "for", "in", "of", "on", "by", "with"}
#: "Sonata ...: II. Andante" is a movement of the work, not the work.
_MOVEMENT = re.compile(r":\s*[IVXLC]+\s*\.", re.IGNORECASE)


def title_terms(title: str) -> list[str]:
    """Lucene terms from a work title, ordinals kept as phrases."""
    terms = [f'"no. {m.group(1)}"' for m in _ORDINAL_IN_TITLE.finditer(title)]
    stripped = _ORDINAL_IN_TITLE.sub(" ", title)
    for word in re.findall(r"[A-Za-z][\w']{2,}", stripped):
        if word.lower() not in _STOP_TERMS and word not in terms:
            terms.append(word)
    return terms[:6]


def is_movement(title: str) -> bool:
    """Whether a MusicBrainz title names a movement rather than the work."""
    return bool(_MOVEMENT.search(title or ""))


def musicbrainz_queries(
    system: str,
    number: int,
    suffix: str,
    surname_: str,
    title: str = "",
) -> list[str]:
    """Query shapes to try, in order.

    The catalogue phrase alone is not enough. MusicBrainz files Mozart's first
    sonata as "K. 189d/279", where the token after "K." is the *revised* number,
    so a phrase search for "K. 279" finds nothing at all. Searching the work's
    own title as well casts a wider net; :func:`mentions_catalogue` still has to
    approve whatever comes back, so widening the search cannot loosen the match.
    """
    # Two spellings, not four: MusicBrainz's phrase matching does not
    # distinguish "K.279" from "K 279", and five queries per work is what
    # tipped it into 503s.
    body = f"{number}{suffix or ''}"
    shapes = []
    for variant in (f"{system}. {body}", f"{system}.{body}"):
        shapes.append(f'work:"{variant}" AND artist:"{surname_}"' if surname_ else f'work:"{variant}"')
    if title and surname_:
        terms = title_terms(title)
        if terms:
            shapes.append(f'work:({" AND ".join(terms)}) AND artist:"{surname_}"')
    return shapes


def find_musicbrainz(
    client: httpx.Client,
    composer_name: str,
    system: str,
    number: int,
    suffix: str = "",
    title: str = "",
) -> tuple[str, str] | None:
    """Search MusicBrainz works.  Returns (mbid, matched title)."""
    last = surname(composer_name)
    attempts = failures = 0
    for query in musicbrainz_queries(system, number, suffix, last, title):
        attempts += 1
        response = _get(client, f"{MUSICBRAINZ_API}/work", params={
            "query": query, "fmt": "json", "limit": 8,
        })
        if response.status_code != 200:
            failures += 1
            continue
        accepted: list[tuple[str, str]] = []
        for work in response.json().get("works", []):
            # Note: not `title`, which is this function's parameter and is still
            # needed for the remaining query shapes.
            found_title = work.get("title") or ""
            haystack = " ".join(
                [found_title, *(str(v) for v in (work.get("disambiguation"),) if v)]
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
                credited = artists or found_title
                if last.casefold() not in credited.casefold() and last.casefold() not in haystack.casefold():
                    continue
            accepted.append((work.get("id", ""), found_title))

        if accepted:
            # A title search surfaces a work's movements alongside the work
            # itself, and often ranks them first. Linking a sonata to its own
            # second movement would be wrong, so the parent wins.
            for mbid, found_title in accepted:
                if not is_movement(found_title):
                    return mbid, found_title
            return accepted[0]
    if failures == attempts:
        raise LookupUnavailable(f"MusicBrainz: every query failed ({failures})")
    return None


def lookup(
    composer_name: str,
    system: str | None,
    number: int | None,
    suffix: str = "",
    title: str = "",
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
        try:
            found = find_imslp(client, composer_name, system, number, suffix)
        except LookupUnavailable:
            raise
        except httpx.HTTPError as exc:
            raise LookupUnavailable(str(exc)) from exc
        if found:
            links.imslp_title, links.imslp_url = found
            # The page is already identified, so the composition date is one
            # more read rather than another search.
            try:
                links.year, links.year_note = imslp_composition_year(client, links.imslp_title)
            except (LookupUnavailable, httpx.HTTPError):
                links.notes.append("could not read the composition date")
        else:
            links.notes.append("no IMSLP page cites this catalogue number")

        try:
            found = find_musicbrainz(client, composer_name, system, number, suffix, title)
        except LookupUnavailable:
            raise
        except httpx.HTTPError as exc:
            raise LookupUnavailable(str(exc)) from exc
        if found:
            links.musicbrainz_id, links.musicbrainz_title = found
        else:
            links.notes.append("no MusicBrainz work cites this catalogue number")

    return links


# --- free-text search, for linking by hand ---------------------------------

@dataclass
class Candidate:
    title: str
    url: str = ""
    id: str = ""
    disambiguation: str = ""
    #: Who wrote it. Without this a search for "Chiquitita" is five identical
    #: rows reading "Chiquitita - Song", and no way to tell them apart.
    credits: str = ""


#: Relation types that name a person responsible for the music itself.
CREDIT_RELATIONS = {"composer", "writer", "lyricist", "librettist", "arranger"}


def credited_people(work: dict, limit: int = 3) -> str:
    """Names from a MusicBrainz work's relations, in the order given.

    Work search results already carry these, so no extra request is needed --
    they were simply being thrown away.
    """
    seen: list[str] = []
    for relation in work.get("relations", []):
        if relation.get("type") not in CREDIT_RELATIONS:
            continue
        name = (relation.get("artist") or {}).get("name")
        if name and name not in seen:
            seen.append(name)
    return ", ".join(seen[:limit]) + (" and others" if len(seen) > limit else "")


def search(
    query: str,
    composer: str | None = None,
    limit: int = 8,
) -> tuple[list[Candidate], list[Candidate], str]:
    """Search both services by free text, for a person to choose from.

    Deliberately unfiltered, unlike :func:`lookup`.  Automatic matching has to
    refuse anything it cannot verify; a person looking at the results can tell
    a Mozart fantasia from a Mozart sonata at a glance, and the works that most
    need linking by hand are exactly the ones with no catalogue number for a
    machine to check.

    ``composer`` is used to narrow the MusicBrainz side. The page calling this
    already knows whose work it is, so making the reader type the name again --
    and hoping Lucene weighs it sensibly -- would be pointless.

    Returns (imslp, musicbrainz, error) -- an error message rather than an
    exception, because a half-working search is still useful.
    """
    query = (query or "").strip()
    if not query:
        return [], [], ""

    imslp: list[Candidate] = []
    musicbrainz: list[Candidate] = []
    problems: list[str] = []

    with _client() as client:
        try:
            response = _get(client, IMSLP_API, params={
                "action": "query", "format": "json", "list": "search",
                "srsearch": query, "srlimit": limit, "srnamespace": 0,
            })
            if response.status_code == 200:
                for hit in (response.json().get("query") or {}).get("search", []):
                    title = hit.get("title") or ""
                    slug = title.replace(" ", "_")
                    imslp.append(Candidate(title=title, url=f"https://imslp.org/wiki/{slug}"))
        except (LookupUnavailable, httpx.HTTPError) as exc:
            problems.append(f"IMSLP search failed ({exc})")

        # Search titles first. Handing raw text to MusicBrainz's Lucene parser
        # searches every field, so "Mozart Allegro K.3" returns works literally
        # *titled* "Mozart" ahead of the Allegro. A title search is what a
        # person typing a work name actually means; the broad query is the
        # fallback for when that finds nothing.
        # Lucene defaults to OR, so "Mozart Allegro K.3" scores a work titled
        # simply "Mozart" above the Allegro. Require every term instead.
        #
        # The artist constraint is tried first but must not be the only shape:
        # MusicBrainz credits a *work* to its writers, so "Chiquitita" is filed
        # under Andersson and Ulvaeus, not under ABBA, and constraining by the
        # performing act would find nothing at all.
        terms = " AND ".join(token for token in query.split() if token)
        artist = surname(composer or "")
        shapes = []
        if artist:
            shapes.append(f'work:({terms}) AND artist:"{artist}"')
        shapes.append(f"work:({terms})")
        shapes.append(query)

        for attempt in shapes:
            try:
                        response = _get(client, f"{MUSICBRAINZ_API}/work", params={
                    "query": attempt, "fmt": "json", "limit": limit,
                })
            except (LookupUnavailable, httpx.HTTPError) as exc:
                problems.append(f"MusicBrainz search failed ({exc})")
                break
            if response.status_code != 200:
                continue
            for work in response.json().get("works", []):
                # Type, language and attributes tell one "Allegro" from another.
                extra = [work.get("disambiguation") or "", work.get("type") or ""]
                extra += [str(a.get("value") or "") for a in work.get("attributes", [])]
                language = work.get("language") or ""
                if language:
                    extra.append(language)
                musicbrainz.append(
                    Candidate(
                        title=work.get("title") or "",
                        id=work.get("id") or "",
                        disambiguation=" · ".join(x for x in extra if x),
                        credits=credited_people(work),
                    )
                )
            if musicbrainz:
                break

    return imslp, musicbrainz, "; ".join(problems)


# --- composition year ------------------------------------------------------

#: IMSLP work pages carry this field in their General Information template.
_YEAR_FIELD = re.compile(r"\|\s*Year/Date of Composition\s*=\s*([^\n|]+)", re.IGNORECASE)
#: The first plausible year inside whatever that field says.
_FIRST_YEAR = re.compile(r"\b(1[0-9]{3}|20[0-2][0-9])\b")


#: The field is wikitext, so it carries templates and links: IMSLP writes
#: "1761-62 in {{LinkWork|...}}" and "[[Category:...]]".
_WIKI_NOISE = re.compile(r"\{\{.*?\}\}|\[\[[^\]]*\]\]|<[^>]*>|<!--.*?-->", re.DOTALL)
_TRAILING_JUNK = re.compile(r"[\s,;(]*(?:\{\{|\[\[).*$", re.DOTALL)


def parse_composition_year(field: str) -> tuple[int | None, str]:
    """Read a year out of an IMSLP composition-date field.

    The field is free wikitext: "1774", "1774-75", "ca. 1783",
    "1761-62 in {{LinkWork|...}}". The year is extracted for sorting and the
    original words are kept alongside it -- reducing "ca. 1783" to 1783 loses
    the "about", and the catalogue should not promise precision the source did
    not give.
    """
    raw = _WIKI_NOISE.sub(" ", field or "")
    # An unbalanced template ("... in {{LinkWork") survives the pass above.
    raw = _TRAILING_JUNK.sub("", raw)
    raw = " ".join(raw.split()).strip(" ,;-–")
    # Cutting a template off mid-phrase leaves a dangling connective:
    # "1761-62 in {{LinkWork" becomes "1761-62 in".
    raw = re.sub(r"\s+(?:in|and|or|at|for|to|of|the)$", "", raw, flags=re.IGNORECASE).strip(" ,;-–")
    match = _FIRST_YEAR.search(raw)
    return (int(match.group(1)) if match else None), raw[:80]


def imslp_composition_year(client: httpx.Client, page_title: str) -> tuple[int | None, str]:
    """Fetch a work's composition year from its IMSLP page."""
    if not page_title:
        return None, ""
    response = _get(client, IMSLP_API, params={
        "action": "query", "format": "json", "prop": "revisions",
        "rvprop": "content", "rvslots": "main", "titles": page_title,
    })
    if response.status_code != 200:
        return None, ""
    pages = (response.json().get("query") or {}).get("pages") or {}
    for page in pages.values():
        revision = (page.get("revisions") or [{}])[0]
        text = (revision.get("slots", {}).get("main", {}) or {}).get("*", "") or revision.get("*", "")
        found = _YEAR_FIELD.search(text or "")
        if found:
            return parse_composition_year(found.group(1))
    return None, ""

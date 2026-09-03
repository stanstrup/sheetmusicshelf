# Sheet Music Shelf

A cataloguing server for a personal sheet music library — piece-level,
page-accurate, and honest about how sure it is.

**The model in one line:** a **work** is what you look for, a **file** is where a
copy of it happens to live, and a **piece** is the page range inside that file.
Keeping those three apart is what makes *"pages 380–383 of the Brahms complete
edition"* something you can shelve, filter and open.

---

## Why it guesses the way it does

The library this was built for is ~3,700 PDFs whose metadata quality varies
enormously by *collection*, not by file:

| Collection | Best signal | Result |
|---|---|---|
| `CD Sheet Music/*` | DocInfo `/Subject` carries composer, title, key, catalogue number and page span, plus a text-bearing `toc.pdf` per disc | high confidence |
| `The Sheet Music Archive/*` | No DocInfo; folder names the composer, filename stub encodes the catalogue number (`bwv772`) | needs authority lookup |
| `Sheet Music Collection/` | Flat `NNN - Artist - Title.pdf`, duplicates present | needs dedup |

So ingest is a set of small per-collection **adapters**, not one global
heuristic. An adapter never decides anything: it emits *candidates*, each tagged
with the signal that produced it and a weight. The scorer is the only thing that
turns candidates into an answer, which is what makes every confidence number
explainable.

### Two scoring rules

1. **Independent agreement reinforces.** Two different signals proposing the same
   value combine with a noisy-OR. Repeats from the *same* source do not — that
   would let one noisy adapter talk itself into certainty.
2. **Disagreement is not averaged away.** Genuinely different values cap the
   field below the review threshold and flag it. A field nobody agrees on is
   exactly the field a human should look at.

Agreement is judged on meaning, not spelling: `8 Variations` and
`8 Variations (on Laat Ons Juichen by C.E. Graaf)` agree; so do
`Fugue in C Minor for Two Pianos` / `Fugue for Two Pianos in C Minor`, and
`Eight Minuets` / `8 Minuets`. But `Sonata no. 1` is never folded into
`Sonata no. 10`.

Routing: **≥ 0.80 auto-accept**, **0.50–0.79 review**, **below that, or any
conflict, held**. Thresholds are per-collection.

### Measured results

| Collection | Files | Pieces | accept | review | hold |
|---|--:|--:|--:|--:|--:|
| CD Sheet Music: Mozart | 87 | 87 | **79** | 4 | 4 |
| The Sheet Music Archive | 1,366 | 1,366 | 0 | 998 | 368 |
| Sheet Music Collection (pop) | 477 | 477 | 0 | 358 | 119 |

The Mozart disc auto-accepts 91% because two independent signals agree on
nearly every file. The other two auto-accept nothing, and that is the honest
answer: every value there is inferred from a path or a filename with no second
source to confirm it. Both are candidates for a lowered `auto_accept` once you
have spot-checked a sample — which is what per-collection thresholds are for.

One Mozart hold is worth describing: the disc labels `works/k0355.pdf` as
`K001` while its own filename says 355. The scorer catches the disagreement and
refuses to auto-accept — the whole point of scoring conflicts rather than
picking a winner.

---

## Quick start

```bash
pip install -e ".[dev]"

# Judge a collection before committing anything to the database.
sms report "Z:/Books/Music/CD Sheet Music/Mozart - The Complete Works for Piano" \
    --no-hash --show problems

# Then, against a live database:
export SMS_DATABASE_URL="postgresql+psycopg://sms:sms@localhost:5432/sms"
sms db upgrade
sms collection add "Z:/Books/Music/CD Sheet Music/Mozart - The Complete Works for Piano"
sms collection scan 1
sms collection list
```

`sms report` needs no database on purpose: the first thing you do with a new
collection is look at what the adapter would say, not commit it.

---

## The curation API

External tools — an LLM session, a script, a spreadsheet round-trip — are
first-class clients. No model runs inside the service; the API is the seam.

```bash
sms token create claude-curation -s curation:read -s curation:write -s catalog:read
```

Interactive docs at `/api/docs`. The loop:

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/curation/queue` | Uncertain pieces, each with **every candidate value, its source and its weight**, plus which fields are missing or conflicted |
| `GET /api/v1/curation/pieces/{id}/text` | DocInfo, PDF outline and extracted text for the opening pages (empty for image-only scans — the queue item says so in advance) |
| `POST /api/v1/curation/candidates` | Propose values. **Scored, not applied** — agreeing raises confidence, disagreeing flags a conflict |
| `POST /api/v1/curation/decisions` | Decide values. **Final** |
| `POST /api/v1/curation/pieces/{id}/approve` \| `/reject` | Take a piece out of the queue |

Authenticate with `Authorization: Bearer <token>`.

**The contract:** anything you propose is scored like any other signal; anything
you decide is final. A decided value cannot be outvoted by a later signal of any
weight, and re-running a corrected adapter over the collection will not disturb
it. Every signal ever seen is kept as a `field_candidate` row, so ingest is
safe to re-run as adapters improve.

---

## The web UI

Server-rendered Jinja and plain forms. No framework, no bundle, and **nothing
loaded from a CDN** — a phone reaching this over a VPN may have no route to the
open internet, so every asset comes from the server.

- **Browse** — faceted by composer, period, form, key, collection and
  cataloguing state. Cards preview each *piece's* own first page, so a work
  inside a multi-work PDF shows the right page. Catalogue numbers sort
  numerically, so Op. 10 no. 9 comes before no. 10 and after Op. 9.
- **Piece** — metadata, confidence and why, siblings in the same file, other
  copies of the same work.
- **Reader** — one image per page rather than a whole PDF, so a six-page piece
  inside a 378-page volume costs six small requests. Keyboard, tap-zones and a
  two-up spread; the chrome hides so the whole screen can be score.
- **Composer** — portrait, dates, derived period, and everything in the library
  by that composer.
- **Review** — the page image beside the fields, every rival value with its
  source and weight, one keystroke to accept. Accepting records decisions.
- **Page ranges** — a thumbnail grid for splitting a book into pieces by hand,
  the authoritative fallback where a PDF carries no outline.

## Annotations

Marks are a layer over the page, never a change to the PDF. Pen, highlighter
and eraser in the reader; whole strokes erase rather than pixels, because on a
score you want the mark gone.

**Coordinates are normalised to 0..1 of the page box, not pixels.** The same
page is served at 320/800/1200/1800px depending on the device asking, and a
phone turned to landscape asks for a different one again — pixel coordinates
would put the ink in the wrong place on every surface but the one it was drawn
on. Verified by drawing on a page and switching to the two-up spread: the
stroke stays over the same notes at a completely different size.

Originals are untouched: `k0283.pdf` still carries its 2004 mtime after a page
of ink. Clearing every annotation leaves the library exactly as it was.

## On the phone

Installable as a PWA — maskable icon, a stable id, shortcuts to the review
queue and shelves. Two reader controls exist for one specific reason: your
hands are on the keys.

- **Awake** holds a screen wake lock, so a score does not dim mid-phrase. It
  hides itself where the browser has no Wake Lock API rather than offering a
  button that cannot work, and re-acquires after the tab comes back, because
  the browser drops the lock whenever the tab is hidden.
- **Full** goes fullscreen.

There is no native Android client, and it is not currently needed: offline
access was explicitly out of scope, and in-app annotation now works in the
browser. Those were the two things a native app would have bought.

### Why pages are rendered server-side

**87% of this library has no text layer.** The things a browser PDF viewer buys
you — text selection, in-document search, reflow — do not exist for most of the
collection anyway. Rendering to WebP instead means no CDN dependency, the same
code path that will drive the anthology page-range editor, and a scanned page
arriving as one small image. Renders are cached on the cache volume, keyed by
file hash, so a page is rasterised once.

## Composer enrichment

```bash
sms composer sync      # authority records from the names already catalogued
sms composer enrich    # descriptions, dates, periods and portraits
```

**Wikidata first, deliberately.** Searching Wikipedia for "Mozart composer"
ranks the 1979 play *Amadeus* above the man — and an early version of this
happily stored that play's description and a photograph of an actor. A
candidate must now *be* a human (P31=Q5) whose occupations (P106) include
composing, and whose label still resembles the name asked for. A miss returns
nothing rather than a confident answer about the wrong subject.

Periods are **derived from dates**, not scraped: Wikidata's movement statement
is patchy and often lists several. A composer is placed by the middle of their
working life, so Beethoven reads as Classical rather than by his 1770 birth.

Portraits are downloaded and cached locally rather than hot-linked, and the
photographer credit and licence are stored and displayed with them — most
Commons portraits are CC-BY-SA and showing one uncredited is a licence breach.

## Canonical sources

```bash
sms work link       # build work records from the catalogue, link pieces to them
sms work enrich     # match works to IMSLP and MusicBrainz
```

Six editions of K. 283 across three collections are six *pieces* but one
*work*, with one IMSLP page and one MusicBrainz id. `work link` builds that
layer; identity is the catalogue number where there is one, because it is the
one identifier that survives translation and every publisher's retitling.

Matching is on the catalogue number alone. A candidate is accepted only when
its title cites **the exact number** — "K. 283" must not match K. 2831 or K. 28
— *and* carries the composer's surname. Title similarity is not enough: "Piano
Sonata No. 5" exists for a dozen composers.

**Only works with an accepted copy are looked up.** Attaching an authoritative
link to a guess launders the guess into a citation; the archive adapter reads
one folder as "The Magic Flute, Op. 1 no. 3" when the work is K. 620, and that
must never come back carrying an IMSLP URL. Works with no catalogue number are
not looked up at all — there is nothing strong enough to identify them.

Verified against the live APIs: the stored URLs resolve, and the MusicBrainz
titles match the works they are attached to.

## The managed tree

```bash
sms collection materialise 1            # show the tree it would build
sms collection materialise 1 --apply    # copy
```

Files are **copied, never moved**; the source mount is read-only so a bug here
cannot reach the originals. Layout:

```
<managed>/<Composer>/<Title> (<Cat.>)/<edition>.pdf    single-work files
<managed>/_Books/<Collection>/<original path>          multi-piece books
```

Books keep their original path because a 378-page volume holding sixty pieces
cannot sit in one work's folder without lying about what it is. Only pieces
that have been accepted are filed, so the tree does not fill with folders named
after guesses that review will change.

---

## Deployment

`deploy/sheetmusicshelf.yml` targets the existing NUC stack. Three containers:
the API on `:8014`, a single-process worker, and a Postgres that is deliberately
**not** published (host 5432 already belongs to mealie's).

**Bring it up from WSL space only** — from Windows, `/mnt/z` resolves to an
empty path and the library binds come up empty:

```bash
wsl bash -c "cd /mnt/z/docker/compose && docker compose -f sheetmusicshelf.yml up -d"
```

If containers die with exit 127 after a Docker Desktop restart, that is the
stale bind-mount shim; `restart` will not fix it, only `--force-recreate` from
WSL space will.

Choices made against that host, not against a clean slate:

- **The source mount is `:ro`.** The design says copy, never move; the mount
  enforces it if the code ever gets it wrong.
- **No Redis.** The job queue is a Postgres table claimed with
  `FOR UPDATE SKIP LOCKED`. Sufficient at this scale, one fewer service.
- **No search engine.** Postgres full-text over a few thousand works.
- **No bulk OCR.** Only 12.7% of the library has a text layer, and a background
  pass over the rest would tax the box for days. OCR is an on-demand button.
- **The worker throttles on load average.** A scan that ignores a busy NUC is
  not a slow scan, it is an outage for everything else on the machine.
- Thumbnails go on a named volume, not CIFS — the same reason SQLite configs do.

---

## Status

**1,930 pieces catalogued across three collections, 306 composers.**

Built and validated: signal extraction; four adapters (CD Sheet Music, The
Sheet Music Archive, the flat lead-sheet folder, and a generic fallback); the
scorer; persistence where human decisions permanently outrank machine ones; the
curation API; server-side page rendering; byte-range PDF serving; the browse
UI, reader, composer pages, review queue and page-range editor; composer
enrichment; annotations; the managed-tree copier; shelves and personal fields;
and the compose deployment. 166 tests.

Next: MusicBrainz/IMSLP enrichment for *works* (composers are done), and
duplicate detection and merge for the pop collection.

See `docs/plan.html` for the full plan and phasing.

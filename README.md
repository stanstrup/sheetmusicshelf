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

### Measured result on the first collection

`CD Sheet Music/Mozart - The Complete Works for Piano`, 94 files:

```
94 files (7 skipped) -> 87 pieces: 79 accept / 4 review / 4 hold
```

Both holds and all four reviews are genuine. One of them is worth describing:
the disc labels `works/k0355.pdf` as `K001` while its own filename says 355. The
scorer catches the disagreement and refuses to auto-accept — which is the whole
point of scoring conflicts rather than picking a winner.

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

Built and validated: signal extraction, the CD Sheet Music and generic adapters,
the scorer, persistence with human-decision precedence, the curation API, byte-range
PDF serving, and the compose deployment. 61 tests.

Next: the browse UI and PWA reader, then the review queue UI and page-range
editor for anthologies, then adapters for the remaining collections.

See `docs/plan.html` for the full plan and phasing.

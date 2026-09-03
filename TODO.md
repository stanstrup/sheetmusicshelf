# TODO

_(nothing outstanding — add items here)_

## Open questions for you

- **Native Android client** — not built. There is no Android toolchain on this
  machine, so none of it could be compiled or verified. It is also not clearly
  needed any more: offline was ruled out, and annotation now works in the
  browser, which were its two justifications. Say the word if you want it
  anyway and I will write it as unverified source for you to build.

## Done

1) ~~enable git~~ — repo initialised, `.gitattributes` normalises line endings.
2) ~~make gui~~ — browse with facets, piece detail, reader, composer pages,
   review queue, page-range editor, shelves and annotations.
3) ~~"works" should preview the first page~~ — thumbnails render each *piece's*
   own first page, so a work inside a multi-work PDF previews correctly.
4) ~~enrich composer metadata from wikipedia with an image and time period~~ —
   Wikidata-first lookup, cached portrait with attribution, period derived from
   dates.
6) ~~prefill the canonical search with the title~~ — composer and title, since
   that is what you would type anyway.
7) ~~show that a search is in progress~~ — the button disables and says so;
   both services are rate-limited so a search takes seconds.
8) ~~pull a composition year~~ — from the IMSLP page already linked, so it costs
   one extra read rather than another search. 66 of 75 linked works are dated.
   `sms work years` backfills.
9) ~~why is /work/4 not linked to MusicBrainz~~ — it is now. MusicBrainz files
   that sonata as "K. 189d/279", the *revised* Köchel number first, and both the
   search and the verifier required the number to follow "K." directly. Fixing
   that took MusicBrainz coverage from 35 to 57 of 78.
10) ~~use MusicBrainz correctly~~ — a real contact is now required before any
   lookup runs, and every outbound request goes through a process-wide rate
   gate with a circuit breaker.

5) ~~pieces should link canonical sources/catalogues — MusicBrainz, IMSLP~~ —
   pieces now link to a *work*, and the work carries the links. Matched on the
   catalogue number, and only for works with an accepted copy, so a guess never
   comes back wearing a citation.
6) ~~clicking Review on a piece should review that piece~~ — the nav link
   carries the piece, and submitting returns to it rather than the queue.
7) ~~a button to delete a piece~~ — on the piece page. The PDF is untouched;
   the page range is remembered so a re-scan cannot recreate the entry.
8) ~~it is unclear how to accept a canonical match; there should be a search~~ —
   `/work/<id>` shows the current links with confirm/clear, and searches IMSLP
   and MusicBrainz so you can pick one by hand. A hand-picked link is marked
   confirmed and automatic runs leave it alone.

11) ~~say what "skip" and "Not music" do~~ — the review dialog now spells out
   all four actions, including what survives a re-scan and what happens to the
   PDF (nothing, in every case).
12) ~~arrows in the review dialog~~ — move through the queue without deciding,
   by the arrows or the left/right keys. Ignored while typing in a field.
13) ~~a delete button in the review tab~~ — same tombstone as the piece page, so
   a re-scan cannot bring the entry back.
14) ~~the MusicBrainz contact should be configurable~~ — `SMS_CONTACT`, and it
   has no default: no lookup runs at all until it is set, which is what stops
   the service being hit anonymously.
15) ~~list the MusicBrainz name in the source list~~ — the work stores the title
   MusicBrainz uses, so the link reads like the IMSLP one instead of showing a
   bare MBID.
16) ~~import to its own folder, and a compose file~~ — `Z:\Books\SheetMusic`
   now holds the 76 accepted Mozart pieces (67 MB, copies; the originals are
   untouched). `sheetmusicshelf.yml` and its two env files are in
   `Z:\docker\compose`, with the build context in `Z:\docker\build`. Nothing
   is started: fill in the secrets and run
   `docker compose -f sheetmusicshelf.yml up -d`.
17) ~~a work split over several files~~ — `grieg/con_amin` is one concerto in
   three movements again, not three concertos. Fourteen folders that name a
   single work are listed by hand in the adapter, because nothing in the file
   layout separates them from `chopin/preludes`, which really is 24 preludes:
   both are one stem and a number. In those folders the trailing number is
   read as a movement.

   This turned up a deeper fault. Candidates were only ever added, never
   retired, so a value an adapter had been *fixed* to stop emitting went on
   arguing its case for ever and re-scanning with the fix changed nothing.
   Candidates now record which adapter made them, an adapter's own superseded
   readings are withdrawn on a re-scan, and a field whose last candidate is
   gone is cleared from the row instead of standing. Human decisions and
   anything posted through the curation API are untouched.

18) ~~don't mount the whole music folder; have an ingest folder like Calibre~~ —
   the library now holds the files, so where one came from stops mattering once
   it is in. All 1,926 catalogued files are in `Z:\Books\SheetMusic` (875 MB),
   and the compose file mounts only that plus a drop folder,
   `Z:\Books\SheetMusicIngest`. The music collection is not mounted at all.

   `sms ingest` is the Calibre auto-add flow: drop PDFs in, they are
   catalogued, filed into the library and removed from the drop. Dry run by
   default, and nothing is deleted until the library holds a copy of the same
   size (`--keep` skips the removal entirely).

   Two things had to be fixed to make the library authoritative. A folder name
   is made from metadata, so review changing a title or composer now *moves*
   the file and prunes the folder it emptied -- copying again would leave a
   folder behind for every name a piece ever had. And two catalogue rows can no
   longer share one file: the pop collection holds the same arrangement twice
   under different numbers, and matching on size alone let the second row point
   at the first row's copy and never get one of its own.

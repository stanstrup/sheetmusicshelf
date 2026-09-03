# TODO

1) automatically put the title in the search page in teh canonical sources page.
2) give an indicator that are search is in progress (canonical sources page)
3) I'd like to pull composition year for each composition from somewhere.
4) why is http://localhost:8014/work/4 not linked to musicbrainz?
5) not clear what happens on "skip" and "Not music" in the review dialog.
6) <- -> arrows in teh review dialog would be nice
7) review tab should also have a delete button
8) the email used for musicbrainz ID should be configurate.
9) didn't we say that it should import to its own folder? I don't see a folder with a copy of the sheet music and I don't see a docker compose file...?
10) some of the things from Z:\Books\Music\The Sheet Music Archive\ is a mess. e.g. Z:\Books\Music\The Sheet Music Archive\grieg\con_amin where the concert is splity over 3 files.



_(nothing outstanding — add items here)_

## Open questions for you

- **Native Android client** — not built. There is no Android toolchain on this
  machine, so none of it could be compiled or verified. It is also not clearly
  needed any more: offline was ruled out, and annotation now works in the
  browser, which were its two justifications. Say the word if you want it
  anyway and I will write it as unverified source for you to build.
- **Managed tree** — the copier has only been run against a scratch directory,
  never against `Z:\Books\SheetMusic`. Run
  `sms collection materialise <id>` (dry run) when you want to see the real plan.

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

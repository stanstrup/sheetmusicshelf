# TODO

*  app: the ... button has almost white on white.
*  app: needs a dark mode.

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

19) ~~merge the works that arrived as one PDF per part~~ — done as a one-off
   repair, not a feature: which folders hold a single work is a judgement about
   the music, and it does not belong in the adapter.

   15 works, 118 parts -> 15 files. Order came from the filenames, and was
   checked against the printed page numbers in the scans: Liszt's first
   concerto is filed as lispc1_a, liszpc1b, lispc1c ... -- the stem is not even
   spelled the same way twice -- and the printed numbers run 10, 11-21, 22-28,
   29-42, 43-52, 53-62, 63-72, 73-82, so the trailing letter is the sequence
   and sorting on the filename would have been wrong.

   The archive repeats, at the top of each part, the page on which that part
   begins, so every part opens standalone. Ten such pages were dropped at the
   seams; Bach's Christmas Oratorio alone would have gained six duplicates.
   Verified after the fact through the app: the merged Grieg runs title page,
   Allegro molto moderato, and the Adagio starts exactly at the seam on p21.

   The split versions are gone from the catalogue and from the library, with
   tombstones so a re-scan cannot bring them back. Nothing under
   `Z:\Books\Music` was touched -- those scans are the only copy there is.

   Still open: **Tchaikovsky's first concerto** (work 1161, 11 files). It is
   filed as three overlapping series -- tchpc1a1..a6, b1, b2, c1..c3 -- so
   "movement 1" names three different files and there is no single order to put
   them in. Left split rather than guessed at.

20) ~~act on the independent architecture review~~ — all eight findings, plus
   the test strategy it argued for. In its own ranking:

   1. **`recompute` was a second, divergent scorer** — and the one that writes
      to the database. It was missing the damaged-text rule, so a title of
      U+FFFD replacement characters scored 0.955 and auto-accepted while
      `score_piece`, the one the tests exercise, returned 0.75 and sent it to
      review. The rules now live in `scoring.combine` and both callers use it.
   2. **Piece identity was the exact page pair.** Narrowing a range by hand and
      re-scanning made a second piece, and a tombstone for the narrowed range
      no longer matched what the adapter proposed, so a deleted entry came
      back. `page_start` now identifies a piece, with a unique constraint, and
      a range set by hand is marked confirmed.
   3. **Three ways to find a file**, one of which built its path from the
      unmounted source directory — so the curation text endpoint returned 410
      for the whole catalogue. One `resolve_source` now.
   4. **`_refile` recorded the path it wanted**, not the one it used when the
      name was taken, pointing the row at another piece's PDF with renders
      cached under the wrong hash. Mine, from earlier today.
   5. **An agent could never withdraw a proposal.** One wrong value held a
      piece for good. `POST /curation/retractions`.
   6. **Accept froze six machine guesses as human truth.** Route came only from
      confidence, so the only way out of the queue was to accept field after
      field. Reviewing is now its own fact; only changed fields become
      decisions.
   7. **The instrument axis did not exist** — collected, confirmed in review,
      then discarded. One of the three original requirements, three-quarters
      built. Backfilled from 1,324 candidates already stored.
   8. **Jobs**: a dead worker's job stayed `running` for ever; filing was
      CLI-only so the tree drifted from the catalogue between manual runs.

   Also `sms verify` (drift is a thing that happens here and there was no way
   to find out), one shared filter builder for the browse page and the API,
   and the ingest folder no longer deletes a dropped file on matching size
   alone with the hash sitting unused.

   The review's most useful finding was a negative one: **`web.py` is not the
   problem.** Twenty-five independent handlers averaging thirty lines. The one
   thing worth extracting was the query builder it duplicated with the API.

   Tests went from 256 pure to 308, with a PostgreSQL fixture for the seams.
   Every fault above lived in a seam between two separately-tested things.

21) ~~type to narrow a filter in the browser~~ — any facet with more than
   twelve values gets a box that filters it as you type. Done in the page: the
   values are already there, and a round trip per keystroke to narrow a list
   you are looking at would be slower and no more correct.
22) ~~the filter sidebar could not scroll~~ — it is `position: sticky` with no
   height limit, so with several facets open it was taller than the screen and
   its own bottom was unreachable: the page scrolled, the pinned sidebar did
   not. Capped and scrollable now. Checked in Chrome: 1,765px of sidebar in a
   920px box, scrolling to the end.
23) ~~the app needs a clear button~~ — next to the search box, appearing only
   when there is something to clear, and emptying the query and every filter
   together. Dropping filters one chip at a time is fine for one; starting over
   should not take five taps.
24) ~~act on the second architecture review~~ — this one designed the system
   independently first and then diffed, which found things a straight audit had
   not:

   * **Rejecting a piece did not stop it being filed.** Both surfaces set
     `review_state` and stopped, so a piece that had auto-accepted kept
     `route = "accept"`, and `materialise` files anything whose route says
     accept. Approving through the API had the mirror fault: state recorded,
     nothing recomputed, so the file was never filed at all.
   * **The split editor destroyed annotations.** They hung off the piece with a
     cascading delete, and the editor deletes pieces whose boundary has moved.
     It already refused to delete a reviewed piece, or one with an accepted
     candidate -- it protected the two kinds of human judgement the schema could
     name and dropped the third, the only one that cannot be recomputed.

   The cause of the first is that reads have had a shared home since
   `catalog_query` and writes never got one, and had now drifted three times.
   `services/review.py` owns the transitions.

25) ~~decide a folder at a time~~ — the archive auto-accepts nothing by design
   and leaves 998 pieces to review, every one sharing a composer with its
   folder-mates. The queue can now be walked in folder order as well as
   worst-first, and the review page offers "apply to all N pending pieces in
   this folder" for the fields the piece already has.

   Worst-first is right about information per decision and wrong about what is
   actually scarce: reloading the composer, the folder convention and the
   catalogue system on every single item. Folder order lets you decide a set
   with its context already in your head, and stop at a clean boundary.

   Deliberately not an approval. It fills in what the folder has in common and
   leaves each piece in the queue to be confirmed on its own, because saying
   what a piece is called is not the same act as saying it is right, and only
   one of those should be done fifty at a time.

26) ~~`sms verify` checks the catalogue against itself~~ — the six existing
   checks compare the catalogue with the disk. This one asserts the promise the
   whole design rests on: an accepted candidate is a decision, and the row is
   supposed to show it. That is the shape every write-side fault so far has
   taken. 62 decisions in the live catalogue, all showing.

27) ~~scrollbars smaller and dark~~ — thin, in the page's own colours, with a
   transparent track. `scrollbar-width` is the standard property and Firefox
   honours only that; the `-webkit-` rules are for Chromium, which honours only
   those, so both are needed and both say the same thing.
28) ~~a MusicBrainz link showing an id instead of a name~~ — 56 of the 58 links
   were made before the title was stored. `sms work mbtitles` looks each one up
   *by id* rather than searching again, so an established link cannot drift
   onto a different work. All 58 now show a name; /piece/26 reads "Sonata for
   Piano no. 5 in G major, K. 189h/283".

* can we make scrollbars smaller and dark?
* http://localhost:8014/piece/26 has the id for teh musicbrainz name instead of the name of the piece. that is becauise it was imported before we changed that. can you fix all over?

29) ~~an easy way to update the app on the tablet~~ — the server hands out its
   own client. Open `/app` on the tablet and press Install; updating later is
   the same page again. No git remote, no release, no account, and nothing
   needing the tablet to have internet — which is the assumption this project
   spent its design deciding not to make.

   `android/tools/publish.cmd` builds and publishes. The version is the commit
   count, so every build is newer than the last without anybody bumping
   anything: Android refuses to install an APK that is not newer than the one
   installed, and a version that never changes is a tablet that never updates.
   The app checks on launch and offers the new build if there is one.

30) ~~making a token should not mean finding a shell in a container~~ — it is
   in **Settings** in the web interface now: name it, tick what it may do,
   press the button. Existing tokens are listed with what they may do and when
   they were last used, and revoking is a button.

   The secret is shown by answering the form with the page itself rather than
   redirecting. A redirect would have to carry it in the query string, which
   writes the one value in the system that is never recoverable into browser
   history and into anything that logs a URL.

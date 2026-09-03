# TODO

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

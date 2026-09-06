## [1.6.1](https://github.com/stanstrup/sheetmusicshelf/compare/v1.6.0...v1.6.1) (2026-09-06)


### Performance Improvements

* search IMSLP and MusicBrainz in parallel ([a0056b6](https://github.com/stanstrup/sheetmusicshelf/commit/a0056b6161f669317fcb7e7b02d90963820fa9df))

# [1.6.0](https://github.com/stanstrup/sheetmusicshelf/compare/v1.5.1...v1.6.0) (2026-09-06)


### Features

* merge duplicate composers and rename to Wikipedia canonical name on enrich ([5c465e6](https://github.com/stanstrup/sheetmusicshelf/commit/5c465e68197eda915b40d4a01fa6d2de270ce3dc))

## [1.5.1](https://github.com/stanstrup/sheetmusicshelf/compare/v1.5.0...v1.5.1) (2026-09-06)


### Bug Fixes

* fill-from-work prefers musicbrainz_title over work.title ([33c6978](https://github.com/stanstrup/sheetmusicshelf/commit/33c6978782bbcaeba73b97c5cb87c5fe2eec195f))

# [1.5.0](https://github.com/stanstrup/sheetmusicshelf/compare/v1.4.0...v1.5.0) (2026-09-06)


### Bug Fixes

* correct viewer.user_id and add recompute to fill-from-work route ([048664e](https://github.com/stanstrup/sheetmusicshelf/commit/048664e96c8370d7b5a54f363213dba01f552ca6))


### Features

* show app version and add update check to Settings screen ([476dacb](https://github.com/stanstrup/sheetmusicshelf/commit/476dacbe6e1d35b3e499f0fa9d74d61304325274))

# [1.4.0](https://github.com/stanstrup/sheetmusicshelf/compare/v1.3.0...v1.4.0) (2026-09-06)


### Bug Fixes

* handle multi-composer names and broken portrait message ([aa47fd3](https://github.com/stanstrup/sheetmusicshelf/commit/aa47fd3bd063600c0ef8f54cb220480fbd4d67e8))
* Save fields now updates piece columns immediately ([028c9e0](https://github.com/stanstrup/sheetmusicshelf/commit/028c9e05f51ed36de36778157c7acd2867fc1cda))


### Features

* collection scan buttons and web UI for CLI commands ([f059bdf](https://github.com/stanstrup/sheetmusicshelf/commit/f059bdfb50f6199467d7b86b2698019937ad71f2))
* create and enrich composer records from the web UI ([c9ac22c](https://github.com/stanstrup/sheetmusicshelf/commit/c9ac22c962c9b057acf2df909f74f573122234f7))
* edit work metadata (title, key, form, year) from the web UI ([2690134](https://github.com/stanstrup/sheetmusicshelf/commit/26901345282af5f1639706b44bd1db36b15d7907))
* trigger ingest from the web UI (Settings page) ([f01e3bd](https://github.com/stanstrup/sheetmusicshelf/commit/f01e3bdb1ccea9d748d35a55aa1f413bb34c9508))

# [1.3.0](https://github.com/stanstrup/sheetmusicshelf/compare/v1.2.1...v1.3.0) (2026-09-06)


### Features

* "Use parent work instead" for MusicBrainz movement links ([0969ff9](https://github.com/stanstrup/sheetmusicshelf/commit/0969ff9299ac89c0e9b789364a7451f0926f7d9d))
* fill piece fields from linked work's canonical data ([8981266](https://github.com/stanstrup/sheetmusicshelf/commit/89812660bbf098701320aa1ddde17d3103429396))

## [1.2.1](https://github.com/stanstrup/sheetmusicshelf/compare/v1.2.0...v1.2.1) (2026-09-06)


### Bug Fixes

* preserve search query after linking a canonical source ([049cbad](https://github.com/stanstrup/sheetmusicshelf/commit/049cbad5a55634177ac928398a8cb2abc9d75565))

# [1.2.0](https://github.com/stanstrup/sheetmusicshelf/compare/v1.1.0...v1.2.0) (2026-09-05)


### Features

* canonical sources and save-fields for review queue ([a14c469](https://github.com/stanstrup/sheetmusicshelf/commit/a14c469cba665cb86ece692135bf1d27d0c9536a))
* reach canonical sources from any piece, not just ones already linked ([bbdb51a](https://github.com/stanstrup/sheetmusicshelf/commit/bbdb51af6636b32aa11c2798d046f85b84136a7b))

# [1.1.0](https://github.com/stanstrup/sheetmusicshelf/compare/v1.0.2...v1.1.0) (2026-09-05)


### Bug Fixes

* gradlew execute bit; bump setup-java to v5 ([346fd1f](https://github.com/stanstrup/sheetmusicshelf/commit/346fd1f869a2114dc232489a6657e86e476b1325))


### Features

* build and release Android APK; add Android section to website ([1fb40d5](https://github.com/stanstrup/sheetmusicshelf/commit/1fb40d529d13da3bf638ae356eaedd0dc6215c9d))

## [1.0.2](https://github.com/stanstrup/sheetmusicshelf/compare/v1.0.1...v1.0.2) (2026-09-05)


### Bug Fixes

* add .nojekyll so GitHub Pages serves _astro/ CSS ([1e0c88f](https://github.com/stanstrup/sheetmusicshelf/commit/1e0c88f960e58644c603a525aa748fec35d9682b))

## [1.0.1](https://github.com/stanstrup/sheetmusicshelf/compare/v1.0.0...v1.0.1) (2026-09-05)


### Bug Fixes

* **ci:** switch pages deploy to JamesIves/github-pages-deploy-action ([b0f2815](https://github.com/stanstrup/sheetmusicshelf/commit/b0f2815c55b4386a26852d63f7e797ce496bfec2)), closes [#pages](https://github.com/stanstrup/sheetmusicshelf/issues/pages) [#pages](https://github.com/stanstrup/sheetmusicshelf/issues/pages)

# 1.0.0 (2026-09-05)


### Bug Fixes

* use built-in GITHUB_TOKEN for semantic-release ([1bb36ee](https://github.com/stanstrup/sheetmusicshelf/commit/1bb36ee0622c79300376a5e176fdadcd98d71f54))


### Features

* semantic-release for automatic versioning and changelog ([aeae74c](https://github.com/stanstrup/sheetmusicshelf/commit/aeae74c6b3a1c0d29f30aa67d56f7c41d229dd10))

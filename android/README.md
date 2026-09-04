# Sheet Music Shelf — Android client

A small reader for the catalogue: search it, open a piece, draw on the pages.

## What it does

- **Browse** — search by title, composer or catalogue number.
- **Read** — one page per swipe, at the width your screen actually has.
- **Annotate** — pen and highlighter, undo, clear. Marks are saved to the
  server the moment a stroke finishes and appear in the web reader too, because
  both write the same rows.

The screen is kept awake while a piece is open. A reader that sleeps halfway
down a page is worse than no reader when both hands are busy.

## Pages are images, not a PDF

The app asks the server for rendered pages rather than downloading the PDF and
rendering it itself. Three reasons, all of them properties of this library:

- 87% of it has no text layer, so the server is rendering anyway, and it caches
  each render by content hash.
- Asking for one page at screen width beats pulling a 50 MB scan over a VPN to
  look at one page of it.
- Offline was ruled out for this project by design, so there is no case where
  the app needs the file itself.

## Setup

1. Make a token on the server:

   ```
   sms token create "phone" --scope catalog:read --scope catalog:write
   ```

   `catalog:read` alone is enough to browse and read; `catalog:write` is what
   lets the app save marks.

2. Install the APK and open it. It goes straight to Settings on first run.
   Enter the server address (`192.168.1.10:8014` is enough — the scheme is
   filled in) and the token, then **Save and test**.

Cleartext HTTP is permitted, because the server is on the LAN or a VPN and is
very likely served over plain http on a private address. Blocking it would make
the app useless in exactly the setting it was built for.

## Building

No Android Studio needed, and nothing needs administrator rights.

```
export JAVA_HOME=/path/to/jdk17
echo "sdk.dir=C:/Users/you/tools/android-sdk" > local.properties
gradle assembleDebug
```

The APK lands in `app/build/outputs/apk/debug/app-debug.apk`.

Two things that will waste an hour if you hit them:

- `local.properties` is a Java properties file, so **use forward slashes**.
  `C:\Users\...` is read with `\U` and `\t` as escapes, and the path silently
  becomes nonsense. The error you get is `The filename, directory name, or
  volume label syntax is incorrect`, which names neither the file nor the line.
- A JDK unpacked to a directory with `+` in its name (Temurin's default,
  `jdk-17.0.20.1+1`) fails the same way. Rename it.

## Layout

| File | What is in it |
|---|---|
| `Api.kt` | Every call to the server, and the two model types |
| `Prefs.kt` | Server address and token |
| `BrowseActivity.kt` | The catalogue list and search |
| `ReaderActivity.kt` | Pager over pages, the ink toolbar, saving marks |
| `AnnotationOverlay.kt` | The ink layer: touch, drawing, coordinates |
| `SettingsActivity.kt` | Address, token, and a connection test |

### One thing worth knowing about the ink

Marks are stored in 0..1 of the page box and only turned into pixels when
drawn. That is what lets a mark made on a phone land in the right place on a
tablet, and in the browser. `AnnotationOverlay.setPageBox` is given where the
page image actually sits inside the view — `fitCenter` letterboxes, so the
image is *not* the view, and ink placed against the view's bounds would drift
by the size of the margins.

Paging is disabled while drawing is on. Otherwise every stroke is also a swipe.

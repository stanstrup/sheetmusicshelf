# Running the Android app

Everything needed is already installed under `C:\Users\jan\tools\` — JDK 17,
the Android SDK, Gradle and an emulator. None of it needed administrator
rights, and none of it is on your `PATH`: the scripts in `tools\` set their
own, so nothing on this machine changed except those variables.

Two ways in. **The tablet** is the real thing and now takes one page visit.
**The emulator** needs nothing but this PC and is the faster loop when you are
changing the app.

---

## The tablet

### Once: let the tablet reach the server

Windows Firewall is blocking inbound 8014, so nothing on the network can reach
the server yet. **This is the only step that needs administrator rights**, and
the only one I could not do for you. In an *Administrator* PowerShell:

```powershell
New-NetFirewallRule -DisplayName "Sheet Music Shelf" -Direction Inbound `
  -LocalPort 8014 -Protocol TCP -Action Allow -Profile Private
```

`-Profile Private` keeps it to networks you have marked private; it does not
open the port on a café wifi. To undo it:

```powershell
Remove-NetFirewallRule -DisplayName "Sheet Music Shelf"
```

Check it worked from the tablet's browser: `http://192.168.1.9:8014` should
show the catalogue. If that fails, it is the rule, not the app.

### Then: install

Open **`http://192.168.1.9:8014/app`** on the tablet and press **Install**.

Android will ask whether to allow installing apps from your browser. Say yes —
the file is coming from your own server on your own network.

Open the app and fill in Settings:

```
Server    192.168.1.9:8014
Token     sms_a3-fk82ejQNZ9JccA9PrdhdGQhx5-SkzX4t1e-eJx04
```

(That one already exists. To make your own, open **Settings** in the web
interface and press *Make a token*.)

Press **Save and test**. It should say *Connected*.

### Updating it later

Same page, same button. Android installs the new build over the old one and
keeps the address and token you entered.

You will usually not have to remember: the app asks the server on launch and
says *"Version 1.0.50 is available"* with a **Get it** button when there is
something newer. It says nothing when there is not.

### Publishing a new build

```
android\tools\publish.cmd
```

Builds, copies the APK to `Z:\Books\SheetMusic\_app`, and writes the version
beside it. That is the directory `/app` reads, so the tablet sees the new build
immediately.

The version number is the repository's commit count, so every build is newer
than the last without anybody bumping anything. That matters more than it
sounds: **Android silently refuses to install an APK whose version is not
greater than the installed one**, so a version that never changes is a tablet
that never updates.

### Over the VPN

This PC also has a VPN address, `10.8.0.2`. If the tablet is on the VPN rather
than the house wifi, use that instead of `192.168.1.9`, and the firewall rule
may need `-Profile Any`.

---

## The emulator

Two windows, from `android\tools\`:

1. **`emulator.cmd`** — starts a Pixel 5 on Android 14. First boot takes about
   a minute. Leave the window open; closing it shuts the phone down.
2. **`install.cmd`** — builds, installs and launches.

After a code change, `install.cmd` again. The emulator can stay running.

The emulator reaches this PC at the special address **`10.0.2.2`** — not
`localhost`, which from inside the emulator is the emulator itself:

```
Server    10.0.2.2:8014
Token     sms_a3-fk82ejQNZ9JccA9PrdhdGQhx5-SkzX4t1e-eJx04
```

No firewall rule is needed for the emulator; it is on this machine.

It deliberately software-renders the GPU, which is reliable but not fast. Fine
for checking the app works — judge how it *feels* on the real tablet.

---

## The token

The one above is real and current (`jan's phone`, read + annotate).

To make another, open **Settings** in the web interface — there is a link in
the header — and press *Make a token*. It is shown once and once only, because
the server keeps a hash of it and nothing else. Losing one costs nothing:
revoke it and make another.

`catalog:read` alone is enough to browse and read. `catalog:write` is what
lets the app save annotations. Both are ticked by default.

The same thing from a shell inside the container, if you would rather:

```
docker compose -f sheetmusicshelf.yml exec sheetmusicshelf sms token create "tablet" --scope catalog:read --scope catalog:write
```

A revoked token is not a mystery in the app: the catalogue screen says *"The
token was refused. Check it in Settings."*

---

## Signing in to the web interface

The dev server on this machine runs with `SMS_AUTH_DISABLED=true`, so it asks
nobody to sign in and the header says *authentication off*. There is no "dev"
account and no password: the name it used to show was a placeholder for
"whoever is looking".

For the deployed stack, set **`SMS_PASSWORD`** in
`Z:\docker\compose\.sheetmusicshelf.env` — one password for the whole
library, typed once on each device. Or set the three `SMS_OIDC_*` values for
authentik accounts, which take precedence.

The app does not use either: it authenticates with a token, which is
independent of how people sign in to the browser.

## What to try

- **Browse** — the **Filter** button opens the facets: composer, period, form,
  scored for, key, collection, each with a count. Long lists filter as you
  type. What you picked shows as chips; tap one to drop it.
- **Search** — type and press the search key. It reads a phrase word by word,
  so `mozart fantasy` works even though no single field holds both.
- **Clear** — the button beside the search box, which appears only when there
  is something to clear, empties the query and every filter at once.
- **Sort** — in the ⋮ menu.
- **Read** — tap a piece and swipe through the pages.
- **Draw** — the pencil at the bottom right. Paging is turned off while it is
  on, or every stroke would also be a swipe. Highlighter, undo and clear appear
  beside it.
- **Check a mark carries across** — draw on a piece, then open the same piece
  in the browser. Both write the same rows, in the same coordinates, so the
  mark should be in the same place on the page.

## The scripts

| Script | What it does |
|---|---|
| `publish.cmd` | Build, and hand it to the server for `/app` to give out |
| `install.cmd` | Build, install and launch on whatever is attached |
| `build.cmd` | Just build the APK |
| `emulator.cmd` | Start the test phone |
| `devices.cmd` | What is attached right now |
| `logs.cmd` | Follow the app's log; Ctrl+C stops |

All of them set their own environment, so none needs anything on your `PATH`.

## If something goes wrong

**`/app` says no app has been published** — run `publish.cmd`. It is looking in
`Z:\Books\SheetMusic\_app`.

**"Could not reach the server"** — wrong address for where the app is running.
Emulator wants `10.0.2.2`, tablet wants `192.168.1.9`. From the tablet's
browser, try `http://192.168.1.9:8014`; if that fails too it is the firewall
rule, not the app.

**"The token was refused"** — it has been revoked, or mistyped. **Settings** in
the web interface lists every token and shows which are still live.

**The install button downloads but nothing happens** — Android is waiting for
you to allow installs from the browser. It usually appears as a notification
rather than a dialogue.

**Installing does nothing and reports no error** — the APK is not newer than
the one on the device. `publish.cmd` prints the version it published; compare
it with Settings → Apps → Sheet Music Shelf.

**`install.cmd` says no device** — run `devices.cmd`. An emulator shows as
`emulator-5554`; a tablet shows its serial and must say `device`, not
`unauthorized` (accept the prompt on its screen).

**The app lists nothing and says nothing** — pull the log with `logs.cmd`.

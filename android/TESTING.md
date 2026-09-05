# Testing the Android app on Windows

Everything is already installed under `C:\Users\jan\tools\` — JDK 17, the
Android SDK, Gradle and an emulator. None of it needed administrator rights and
none of it is on your `PATH`; the scripts in `tools\` set their own.

There are two ways to test. The emulator needs nothing but this PC. A real
phone is the honest test, and needs one firewall rule.

---

## A. On the emulator

Two windows. From `android\tools\`:

1. **`emulator.cmd`** — starts a Pixel 5 running Android 14. First boot takes
   about a minute. Leave the window open; closing it shuts the phone down.
2. **`install.cmd`** — builds the app, installs it, and launches it.

That is the whole loop. After a code change, `install.cmd` again — the emulator
can stay running.

The emulator reaches this PC at the special address **`10.0.2.2`**, so in the
app's Settings put:

```
Server    10.0.2.2:8014
Token     (see below)
```

`localhost` will *not* work from inside the emulator — that is the emulator
itself.

## B. On your own phone or tablet

The easy way, once the firewall rule below is in place: open
**`http://192.168.1.9:8014/app`** in the tablet's browser and press Install.
The server hands out its own client, so there is no file to copy about and no
account anywhere.

Publish a build with `android\tools\publish.cmd` — it builds, copies the APK
to `Z:\Books\SheetMusic\_app`, and writes the version beside it. Updating the
tablet later is the same page again: Android installs it over the old one and
keeps the address and token you entered.

The version comes from the commit count, so every build is newer than the last
without anybody bumping anything — Android refuses to install an APK that is
not newer than the one already on the device, and a version that never changes
is a tablet that never updates. The app also checks on launch and offers the
new one if there is one.

## B2. The manual way

The phone talks to the server over the LAN, so it needs the PC's real address
and a hole in the firewall.

### One-time: let the phone reach the server

Windows Firewall is currently blocking inbound 8014. **This is the only step
that needs administrator rights.** Open PowerShell as Administrator and run:

```powershell
New-NetFirewallRule -DisplayName "Sheet Music Shelf" -Direction Inbound `
  -LocalPort 8014 -Protocol TCP -Action Allow -Profile Private
```

`-Profile Private` keeps it to networks you have marked private — it does not
open the port on a café wifi.

To undo it later:

```powershell
Remove-NetFirewallRule -DisplayName "Sheet Music Shelf"
```

### Then

1. Copy `app\build\outputs\apk\debug\app-debug.apk` to the phone — email,
   USB, a shared folder, whatever is easiest — and tap it. Android will ask
   whether to allow installing from that app; say yes.
2. Open it and fill in Settings:

```
Server    192.168.1.9:8014
Token     (see below)
```

3. Press **Save and test**. It should say **Connected**.

If you would rather install over USB: turn on Developer options (tap Build
number seven times), enable USB debugging, plug the phone in, accept the
prompt on the phone, then run `install.cmd` — it installs to whatever is
attached.

### Over the VPN

The PC also has a VPN address, `10.8.0.2`. If the phone is on the VPN rather
than the house wifi, use that instead of `192.168.1.9` and the firewall rule
may need `-Profile Any`.

---

## The token

One has been made for you, with permission to read the catalogue and to save
annotations:

```
sms_a3-fk82ejQNZ9JccA9PrdhdGQhx5-SkzX4t1e-eJx04
```

It is not recoverable from the server — it is stored hashed — so if it goes
missing, make another and revoke this one:

```
sms token create "phone" --scope catalog:read --scope catalog:write
sms token list
sms token revoke 2
```

A revoked token is not a mystery in the app: the catalogue screen says *"The
token was refused. Check it in Settings."*

---

## What to try

- **Search** — type a composer and press the search key.
- **Open a piece** — swipe left and right through the pages.
- **Draw** — the pencil at the bottom right turns drawing on. Paging is
  disabled while it is on, or every stroke would also be a swipe. The
  highlighter, undo and clear buttons appear next to it.
- **Check a mark carried across** — draw on a piece, then open the same piece
  in the browser at `http://localhost:8014`. Both write the same rows, so the
  mark should be in the same place on the page.

## Other scripts

| Script | What it does |
|---|---|
| `build.cmd` | Just build the APK |
| `install.cmd` | Build, install, launch |
| `emulator.cmd` | Start the test phone |
| `devices.cmd` | What is attached right now |
| `logs.cmd` | Follow the app's log; Ctrl+C stops |
| `publish.cmd` | Build and hand it to the server, for `/app` to give out |

## If something goes wrong

**"Could not reach the server"** — the server is not running, or the address is
wrong for where the app is. Emulator wants `10.0.2.2`, phone wants
`192.168.1.9`. From the phone's browser, try `http://192.168.1.9:8014` — if
that fails too, it is the firewall rule, not the app.

**The app lists nothing but says nothing either** — pull the log with
`logs.cmd`.

**`install.cmd` says no device** — run `devices.cmd`. An emulator shows as
`emulator-5554`; a phone shows its serial and must say `device`, not
`unauthorized` (accept the prompt on the phone).

**The emulator is slow** — it is software-rendering the GPU deliberately, which
is reliable but not fast. It is fine for checking the app works; judge how it
actually feels on the real phone.

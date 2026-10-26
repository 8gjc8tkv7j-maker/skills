---
name: android-tv-debloat
description: Find an Android TV / Google TV on the local network, connect over ADB, back it up, disable the manufacturer's ads, telemetry and junk reversibly (never uninstalling), and replace the home screen with TV Home, a fast Apple TV-style launcher with a dock of favourite apps and rotating nature wallpapers. Every step is checked with screenshots from the TV and a restore script is kept up to date. Use when someone wants to clean up, speed up, de-ad or re-home an Android TV, Google TV, Chromecast, Shield or similar box.
license: Complete terms in LICENSE.txt
---

# Android TV debloat and new home screen

This skill drives `scripts/tvctl.py` (Python 3.8+, stdlib only) and installs `launcher/dist/TVHome.apk`.
It needs `adb` and a computer **on the same network as the TV**: a cloud sandbox can't reach a home
Wi-Fi. If `tvctl.py discover` finds nothing because you're running remotely, say so plainly and ask the
person to run the session on their own computer (Claude Desktop, or `claude remote-control` in this repo).
Don't fake results.

## Rules that are never bent

- **Back up first.** `tvctl.py backup` runs before anything changes. Mutating commands back up
  automatically if there is no backup for this TV, but run it explicitly and show the person the
  restore script paths.
- **Disable, never uninstall.** Only `pm disable-user --user 0` and `pm enable`. Never run
  `pm uninstall`, `pm clear`, `pm hide` or `rm` on system files, even if a guide online says so.
  The only thing ever uninstalled is TV Home itself, and only by restore.
- **Ask before touching anything the person might use.** Ads and telemetry may be offered as one batch;
  every app or feature (voice search, casting, the phone-remote service, screensavers, streaming apps,
  recordings, TCL Guard and similar) is asked about individually and kept unless they say otherwise.
  Unknown manufacturer packages are kept unless they choose them.
- **Look at every screenshot.** After each batch tvctl saves a PNG under `<backup>/screens/` and prints
  its path. Read it with the Read tool and describe what you see before continuing. If the screen is
  black, shows an error, or isn't the home screen you expected, stop and undo the last batch
  (`tvctl.py undo PKG...`).
- **Never disable a stock home screen until TV Home is installed and has been seen working** in a
  screenshot. `tvctl.py home set-default` enforces this order.
- **Protected packages stay alone**: TV inputs/HDMI (`com.tcl.tv`, `com.tcl.tvinput`, `com.sony.dtv.tvx`,
  `com.mediatek.*`, `org.droidtv.*` …), Play services and Store, Settings, system UI, keyboards,
  remote/IR services. They're listed in `scripts/catalog.json` and tvctl refuses them.

## Workflow

Run commands from the skill folder (or give the full path to `scripts/tvctl.py`). Backups go to
`./tv-backups/` in the current directory; pass `--dir` to change it. On Windows the Python command is
usually `py` rather than `python3`; tvctl also finds adb in `C:\platform-tools` when it isn't on PATH.

### 1. Find and connect

```bash
python3 scripts/tvctl.py discover            # mDNS + port scan of the local /24; --json for machine output
python3 scripts/tvctl.py connect 192.168.1.42
```

If ADB is off, walk them through it on the TV: **Settings > System > About** (Google TV) or
**Settings > Device Preferences > About** (Android TV), press OK on **Build** seven times, then
**Developer options**: turn on **USB debugging** and **Network debugging**. On Android 11+ devices that
only offer **Wireless debugging**, use *Pair device with pairing code* and
`tvctl.py pair <ip>:<pairing port> <code>`, then `tvctl.py connect <ip>:<port shown>`.
`connect` waits while they press **Allow** (tick *Always allow*) on the TV.

### 2. Back up

```bash
python3 scripts/tvctl.py backup
```

Read `screens/01-before-any-change.png`, then tell them the backup folder and its `restore.sh` /
`restore.cmd`. The backup holds every package's state, all settings (`settings list global|secure|system`),
the home-screen holder, the launchable app list and `getprop`. Apps are never uninstalled, so no app data
is at risk and no APK backup is needed.

### 3. Debloat (ask, then apply in batches)

```bash
python3 scripts/tvctl.py debloat --plan --json
```

The plan has four groups:

| group | what | how to handle |
|---|---|---|
| `batch` | ads, sponsored rows, ACR/analytics, crash uploaders | Show the list with what each does; one confirmation; let them keep any |
| `ask` | apps and features someone might use | Ask about each (AskUserQuestion, a few per question, multiSelect); default is keep |
| `review` | other manufacturer packages tvctl doesn't know | Show them; only disable what they pick; look a name up if they ask |
| `launcher` | stock home screens | Not now: step 5 |

Then apply exactly what they agreed to:

```bash
python3 scripts/tvctl.py debloat --apply com.tcl.overseasappshow com.google.android.feedback ...
```

tvctl disables in batches of 8. After each package it checks `system_server` is alive and that no
"has stopped"/"isn't responding" dialog appeared, and **turns the package back on automatically** if
one did (exit code 4). After each batch it presses Home, takes a screenshot and checks it again. Read
the screenshot, then continue. Turning off `com.google.android.tvrecommendations` or `com.tcl.guard`
deserves an extra check: ask them to try Live TV and the Input button, or reboot (`adb reboot`), and
take a screenshot afterwards.

Without a person typing at a terminal the wizard (`tvctl.py debloat` with no flags) refuses to guess;
use `--plan` and `--apply`. People running it themselves can use the interactive wizard.

### 4. Optional tweaks

```bash
python3 scripts/tvctl.py tweaks --plan
python3 scripts/tvctl.py tweaks --apply animations scanning      # only what they said yes to
```

`animations` (0.5x, feels much faster), `scanning` (no background Wi-Fi/BLE scans) and `adblock-dns`
(Private DNS via AdGuard, which blocks ads in every app but can stop Pluto TV/Tubi from playing; off by
default, so ask). Every old value is journaled; unset values are deleted again on restore.

### 5. TV Home: the new home screen

```bash
python3 scripts/tvctl.py home install                      # installs, opens it, screenshot
python3 scripts/tvctl.py home dock                         # lists apps with names (from TV Home)
python3 scripts/tvctl.py home dock com.netflix.ninja com.google.android.youtube.tv settings
python3 scripts/tvctl.py home set-default                  # role/default home, press Home, screenshot
```

Read the first-launch screenshot before going on. Ask which apps they want in the dock and in what
order (HDMI inputs can go in too, as `input:<id>` ids from the list). `set-default` makes TV Home the home
app (role `android.app.role.HOME` on Android 10+, `set-home-activity` before that), presses Home and
checks what came up. On Google TV the stock launcher usually still grabs the Home button: tell them,
then with their OK run

```bash
python3 scripts/tvctl.py home set-default --disable-stock com.google.android.apps.tv.launcherx
```

If Google TV's setup app (`com.google.android.tungsten.setupwraith`) then takes over Home, tvctl says
so. Explain the catalog's warning (reported to break volume control on some Sony TVs) before adding it
to `--disable-stock`.

Optional: their own photos instead of the nature set:
`python3 scripts/tvctl.py home wallpapers --folder ~/Pictures/TV`.

Finish with a checklist they try on the remote, with a screenshot of anything odd: Home, Back, an app
from the dock, **Input/Source** (TVs), the microphone if they kept voice search, the Netflix/YouTube
buttons, Settings.

### 6. Wrap up

`tvctl.py status` summarises what changed. Point them at `<backup>/report.html` (every screenshot in
order) and the restore scripts. Suggest turning **Network debugging** off again in Developer options
(an open ADB port on the network is a risk); tvctl can reconnect later after they turn it back on.

## Restoring

Any of these puts back everything tvctl changed, newest session first and back to the original backup:

- `sh <backup>/restore.sh [TV_IP] [--keep-tv-home]` on macOS/Linux
- `<backup>\restore.cmd [TV_IP] [--keep-tv-home]` on Windows
- `python3 scripts/tvctl.py restore [--keep-tv-home] [--all]`

They re-enable every package tvctl disabled (`--all` also re-enables apps found disabled since the
backup), restore changed settings, hand the home role back to the original launcher, remove TV Home and
press Home. They're regenerated after every single change and the journal (`changes.jsonl`) is written
*before* each change, so even an interrupted session can be undone. A factory reset also undoes
everything, because disabling is per-user state.

## TV Home (the launcher)

`launcher/` holds the source, a Gradle-free `build.sh` and the prebuilt `dist/TVHome.apk`
(package `dev.tvhome`, minSdk 21, targetSdk 33, about 1 MB, framework-only Java with no libraries).
Previews rendered from the real views are in `launcher/preview/`.

- Full-screen wallpaper that crossfades every few minutes, with a slow drift and a caption. Photos come
  from your own pictures (`files/wallpapers/`), then **featured nature photos from Wikimedia Commons**
  (free licences; the credit line is shown on screen, about 6 new ones every 6 hours, 40 cached), then six
  built-in illustrated landscapes so it works offline.
- Frosted-glass dock of favourite apps (banners lift and glow on focus, name underneath). **Down** opens
  every app, HDMI input and Settings over a blurred backdrop; **Up** or **Back** returns.
- **Hold OK** (or Menu) on a tile: Move (◀ ▶, OK), Add to / Remove from Dock, App info.
- **Home settings** tile: change wallpaper now, interval (1 to 60 min), Commons photos on/off, motion,
  clock, reset dock.
- Config is plain files in `/sdcard/Android/data/dev.tvhome/files/` (`dock.txt`, `home.properties`,
  `apps.tsv`, `wallpapers/`). tvctl also uses
  `am start -n dev.tvhome/.HomeActivity --es dock a,b,c --ez next_wallpaper true`.

Rebuilding needs Android build-tools and a platform 33+ `android.jar` (`ANDROID_HOME` set, or
`ANDROID_JAR`/`FRAMEWORK_RES` pointing at them): `launcher/build.sh`. A rebuild is signed with your own
local key, so `adb uninstall dev.tvhome` the prebuilt one first. `launcher/tools/make_art.py`
regenerates the built-in wallpapers, icon and banner.

## Tests

`python3 -m unittest discover -s tests` runs tvctl end to end against `tests/fake_adb.py`, a simulated
TCL Google TV. The tests cover discovery parsing, the plan's protections, auto-revert when a package
breaks something, the Home-button takeover by Google TV's launcher and setup app, and full restores,
including running the generated `restore.sh` and chaining across several backup sessions.

## Adding packages to the catalog

`scripts/catalog.json` maps package names to `label`, `group` (`ads`, `telemetry`, `app`, `feature`,
`launcher`), `what`, `breaks`, and optional `"ask": true` to force a one-by-one question. Only add a
package when you know what it does on TVs; anything that touches inputs, tuners, audio, remotes or
setup belongs in `protected`.

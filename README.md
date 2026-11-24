# Corporation logos for EveJS

An optional EveJS add-on that shows a corporation with your own picture
instead of the in-game emblem builder's three layers, the way alliances
already show PNG logos.

There are two ways to give a corporation a logo:

- **Standalone:** `corp_logo.py` (or double-click `Set-Corporation-Logo.cmd`
  on Windows). Works on any EveJS server, with or without Playerbots.
- **Playerbots dashboard:** with the Playerbots add-on installed, a
  corporation's page on the dashboard has a **Logo** panel to upload and
  remove logos, and logos show next to corporation names across the
  dashboard.

Both store the same files, so you can mix them.

## How it works

Logos are stored where EveJS keeps its other runtime images, one PNG per
size, like alliance logos:

```
<runtime images>/Corporation/<corporationID>_<size>.png    sizes 32, 64, 128, 256, 512, 1024
```

On Docker that folder is inside the `evejs-data` volume
(`/var/lib/evejs/gameStore/images`); on a native install it is next to the
game store data.

The EVE client never asks the image server for a player corporation's logo:
it always draws the emblem from the corporation's shape and colour layers.
`install.py` adds a few lines to the script EveJS already sends the client
at login. For every corporation that has a PNG, the client fetches it from
the image server through its own corporation-logo loader and draws it in
place of the emblem: menus, search, info windows and banners. Every other
corporation keeps its emblem. No client files are changed.

The ID list is sent at login, so players see a new logo from their next
login. A client that already cached a picture of the same corporation may
keep it until its `Pictures\Corporations` cache folder is cleared.

## Install (once per server)

Download the release ZIP from `dist/` (or clone this repository), then pick
the route that matches how you run EveJS.

### Docker, or native without the launcher

Double-click `install.cmd` (or run `python install.py`) and answer the
questions: your EveJS folder, and whether EveJS runs in Docker, through
EVEJS Launcher, or natively with `StartServer.bat`. Without questions:

```sh
python install.py "C:\EVE\evejs" --docker --rebuild   # Docker: install, rebuild and restart
python install.py "C:\EVE\evejs" --native             # native, Game stopped
python install.py "C:\EVE\evejs" --check              # report only
python install.py "C:\EVE\evejs" --remove --docker --rebuild
```

- **Docker:** `--rebuild` rebuilds the image and restarts the server. With
  Playerbots installed it runs `addons/playerbots/playerbots.py rebuild`, so
  Playerbots' own Compose settings (launcher or Server Panel files, project
  name) are kept; otherwise it runs `docker compose build init` and
  `up -d server`. Without `--rebuild`, rebuild yourself (for example
  `RebuildPlayerbots.bat`).
- **Native:** Game must be stopped; the installer checks its port and
  changes nothing while Game runs. Start Game afterwards.

### EVEJS Launcher (native)

1. Stop Game.
2. **Mods → Import** and choose `evejs-corporation-logos-launcher-v<version>.zip`.
3. Enable **Corporation Logos**, then start Game.

Disable and Remove in the launcher put the EveJS files back; logo images
are kept. Don't also run `install.py` on a launcher-managed folder: it
refuses native changes there. The mod's folder holds
`Set-Corporation-Logo.cmd` for setting logos.

### What changes in EveJS

Each file is backed up first, to `<evejs>\.corp-logo-addon-backup-<time>\`
(`install.py`) or `<evejs>\_local\corporation-logos\backups\` (launcher):

| File | Change |
| --- | --- |
| `server/src/services/corporation/corporationLogoClientDelivery.js` | New: builds the client patch. |
| `server/src/network/tcp/handshake.js` | One block that adds the patch to the login script. |
| `server/src/_secondary/image/imageRequestResolver.js` | Lets the image server answer `/Corporation/<id>_<size>.png` with the stored PNG. Left alone when the file already does this (an earlier hand edit); removal takes out only the lines this add-on added. |

The add-on never edits a file Playerbots manages, in either its Docker or
its launcher form. An EveJS update can replace these files: run
`install.py --check` (or re-enable the mod) afterwards.

## Method 1: standalone

Double-click `Set-Corporation-Logo.cmd` and answer the questions, or:

```sh
python corp_logo.py set    "C:\EVE\evejs" 98000002 tyrell.png
python corp_logo.py remove "C:\EVE\evejs" 98000002
python corp_logo.py list   "C:\EVE\evejs"
```

- **Docker:** the server must be running. The logo is written into its
  volume with `docker compose exec`, using the same Compose files and project
  as Playerbots when it is installed; nothing is rebuilt. `--project-name`
  and `--compose-file` override them. Add `--docker` or `--native` to choose
  explicitly.
- **Native:** the logo goes into `_local/gameStore/images/Corporation` in the
  EveJS folder. If your game store lives elsewhere, add `--data-dir` with its
  `data` folder.
- **Images:** with Pillow installed (`pip install pillow`), any PNG, JPEG,
  WebP or GIF works: it is centred on a transparent square and resized for
  each size. Without Pillow, use a square PNG; it is stored as is at every
  size, as EveJS stores alliance logos.
- **Which corporations:** player and bot corporations (ID 2,000,000 or
  above, such as 98000002). NPC corporations keep their game logos. The ID is
  on the corporation's info window and on the Playerbots dashboard.

## Method 2: Playerbots dashboard

Needs Playerbots with corporation-logo support: the `corp-logos` branch of
[ubermidas/evejs-playerbots-addon](https://github.com/ubermidas/evejs-playerbots-addon/tree/corp-logos)
until it is released.

1. Open `http://127.0.0.1:8080/#corporations` and open a corporation.
2. In the **Logo** panel, click **Choose image…**, check the preview and
   click **Save logo**. **Remove logo** puts the emblem back.

The dashboard works without this add-on, but then logos only show on the
dashboard. The Logo panel says whether the in-game patch is installed.

## Working alongside Playerbots

Playerbots gives each new bot corporation a random in-game emblem. To give
new bot corporations PNGs instead, set `EVEJS_PLAYER_BOTS_CORPORATION_EMBLEMS=0`
(or turn off **Random emblems for new bot corporations** in the Windows
launcher). Corporations with a PNG show the PNG either way.

## Files

| Path | What it is |
| --- | --- |
| `install.py`, `install.cmd` | The installer for Docker and native installs, with questions or flags. |
| `launcher/` | The EVEJS Launcher mod: `evejs-launcher.mod.json` and `helper.cjs`, the lifecycle helper the launcher runs. |
| `lib/patch.cjs` | The server changes in Node.js, used by the launcher helper; tests check it writes exactly what `install.py` writes. |
| `corp_logo.py`, `Set-Corporation-Logo.cmd` | The standalone logo tool and its double-click launcher. |
| `tools/corp-logo-store.js` | The part of `corp_logo.py` that runs where EveJS runs (passed with `node -e`). |
| `addon_common.py` | Compose settings, Game-port check and prompts shared by the two Python tools. |
| `files/server/src/services/corporation/corporationLogoClientDelivery.js` | The client patch copied into EveJS. |
| `tools/build.py`, `dist/` | Builds the launcher ZIP and the full release ZIP; both are committed. |
| `tests/` | `python -m unittest discover tests` (needs Node.js; Pillow optional). |

## Limits

- The launcher package follows the format of the Playerbots launcher
  package (manifest schema 3, helper protocol `evejs_launcher_mod_v1`, EVEJS
  Launcher 1.0.91 or later). It is tested by driving its helper the way the
  launcher does, not yet in EVEJS Launcher itself.
- The client patch targets `eveIcon.CorpIcon.DoLayout` in client build
  3396210, the build EveJS 261115.2 serves. Check it again after a client
  update.
- The patch is tested by code review and unit checks only so far; confirm
  it in game after installing (the server log shows
  `CORP_LOGO_PNG_PATCH:OK:<count>` at each login).

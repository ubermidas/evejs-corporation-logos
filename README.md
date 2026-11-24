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

From this folder:

```sh
python install.py --check  "C:\EVE\evejs"    # report only
python install.py          "C:\EVE\evejs"    # install
python install.py --remove "C:\EVE\evejs"    # remove
```

Then rebuild the server image (Docker: `RebuildPlayerbots.bat`, or
`docker compose up --build --detach`) or restart EveJS (native).

What it changes, each file backed up first to
`<evejs>\.corp-logo-addon-backup-<time>\`:

| File | Change |
| --- | --- |
| `server/src/services/corporation/corporationLogoClientDelivery.js` | New: builds the client patch. |
| `server/src/network/tcp/handshake.js` | One block that adds the patch to the login script. |
| `server/src/_secondary/image/imageRequestResolver.js` | Lets the image server answer `/Corporation/<id>_<size>.png` with the stored PNG. Left alone when the file already does this (an earlier hand edit); `--remove` takes out only the lines this add-on added. |

The add-on never edits a file Playerbots manages. Check that it is in
place after updating EveJS itself, since an update can replace these files.

## Method 1: standalone

Double-click `Set-Corporation-Logo.cmd` and answer the questions, or:

```sh
python corp_logo.py set    "C:\EVE\evejs" 98000002 tyrell.png
python corp_logo.py remove "C:\EVE\evejs" 98000002
python corp_logo.py list   "C:\EVE\evejs"
```

- **Docker:** the server must be running. The logo is written into its
  volume with `docker compose exec`; nothing is rebuilt. Add `--docker` or
  `--native` to choose explicitly.
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
| `install.py` | Installs, checks or removes the server side (once per server). |
| `corp_logo.py`, `Set-Corporation-Logo.cmd` | The standalone tool and its Windows double-click launcher. |
| `tools/corp-logo-store.js` | The part of `corp_logo.py` that runs where EveJS runs (passed with `node -e`). |
| `files/server/src/services/corporation/corporationLogoClientDelivery.js` | The client patch `install.py` copies into EveJS. |
| `tests/` | `python -m unittest discover tests` (needs Node.js; Pillow optional). |

## Limits

- The client patch targets `eveIcon.CorpIcon.DoLayout` in client build
  3396210, the build EveJS 261115.2 serves. Check it again after a client
  update.
- The patch is tested by code review and unit checks only so far; confirm
  it in game after installing (the server log shows
  `CORP_LOGO_PNG_PATCH:OK:<count>` at each login).

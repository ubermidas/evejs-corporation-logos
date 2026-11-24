#!/usr/bin/env python3
"""Install or remove the Corporation Logos add-on in an EveJS folder.

    python install.py                              # asks everything (or double-click install.cmd)
    python install.py C:\\EVE\\evejs [--docker | --native] [--rebuild]
    python install.py C:\\EVE\\evejs --remove [--rebuild]
    python install.py C:\\EVE\\evejs --check     # report only, change nothing

EVEJS Launcher users import the launcher package instead (Mods > Import):
evejs-corporation-logos-launcher-v<version>.zip, next to this file in a
release or in dist/ in the repository.

What it changes, each file backed up first to
<evejs>/.corp-logo-addon-backup-<time>/:

- server/src/services/corporation/corporationLogoClientDelivery.js (new):
  the client patch, sent to the game client at login, that draws
  corporations with a PNG from the image server instead of their emblem.
- server/src/network/tcp/handshake.js: one block that adds that patch to the
  login script.
- server/src/_secondary/image/imageRequestResolver.js: lets the image server
  answer /Corporation/<id>_<size>.png with the stored PNG. Skipped when the
  file already does this (an earlier hand edit), and only lines this add-on
  added are taken out again on --remove.

It never edits a file the Playerbots add-on manages. Docker installs need a
rebuild afterwards (--rebuild does it, through Playerbots' playerbots.py when
Playerbots is installed); native installs need Game restarted, and Game must
be stopped while the files change.
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import addon_common as common  # noqa: E402

VERSION = (HERE / "VERSION").read_text(encoding="utf-8").strip() if (HERE / "VERSION").is_file() else "dev"
DELIVERY = "server/src/services/corporation/corporationLogoClientDelivery.js"
HANDSHAKE = "server/src/network/tcp/handshake.js"
RESOLVER = "server/src/_secondary/image/imageRequestResolver.js"
ANCHOR = b"  // Last, in its own try block: the GM Server Health window"
BLOCK_LINES = [
    b"  // Custom corporation logos: draw player corps that have a PNG in the image store",
    b"  // through photo.GetCorporationLogo instead of the three emblem layers.",
    b"  const corporationLogoDelivery = require(path.join(",
    b"    __dirname,",
    b'    "../../services/corporation/corporationLogoClientDelivery",',
    b"  ));",
    b"  source.push.apply(source, corporationLogoDelivery.buildCorporationLogoSignedFuncSource());",
    b"",
    b"",
]

MARK = b"// corp-logo-addon"
RESOLVER_REQUIRE_ANCHOR = b'const path = require("path");'
RESOLVER_FUNCTION_ANCHOR = b"function resolveCorporationLogoPath(corporationID, size) {"
RESOLVER_REQUIRES = [
    b'const corpLogoAddonFs = require("fs"); // corp-logo-addon',
    b'const { resolveRuntimeImagesDir: corpLogoAddonImagesDir } = require("../../gameStore/storeRoot"); // corp-logo-addon',
]
RESOLVER_FUNCTION = [
    b"// corp-logo-addon: begin",
    b"// A corporation's own PNG from <runtime images>/Corporation/<id>_<size>.png,",
    b"// the requested size first. Added by the corporation logo add-on.",
    b"function corpLogoAddonFind(corporationID, size) {",
    b"  const id = toPositiveInteger(corporationID, null);",
    b"  if (!id) return null;",
    b'  const root = path.join(corpLogoAddonImagesDir(), "Corporation");',
    b"  const sizes = [size, 32, 64, 128, 256, 512, 1024].filter((value, index, all) => value && all.indexOf(value) === index);",
    b"  for (const candidate of sizes) {",
    b"    const filePath = path.join(root, `${id}_${candidate}.png`);",
    b"    if (corpLogoAddonFs.existsSync(filePath)) return filePath;",
    b"  }",
    b"  return null;",
    b"}",
    b"// corp-logo-addon: end",
    b"",
]
RESOLVER_CALL = [
    b"  const corpLogoAddonPath = corpLogoAddonFind(corporationID, size); // corp-logo-addon",
    b"  if (corpLogoAddonPath) return corpLogoAddonPath; // corp-logo-addon",
]


def newline_of(text: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in text else b"\n"


def block_for(text: bytes) -> bytes:
    return newline_of(text).join(BLOCK_LINES)


def resolver_state(text: bytes) -> str:
    if MARK in text:
        return "patched"
    if b"findCustomCorporationLogoPath" in text:
        return "hand-edited"
    if text.count(RESOLVER_REQUIRE_ANCHOR) == 1 and text.count(RESOLVER_FUNCTION_ANCHOR) == 1:
        return "stock"
    return "unsupported"


def patch_resolver(text: bytes) -> bytes:
    out: list[bytes] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip(b"\r\n")
        ending = line[len(body):] or newline_of(text)
        if body == RESOLVER_FUNCTION_ANCHOR:
            out.extend(part + ending for part in RESOLVER_FUNCTION)
            out.append(line)
            out.extend(part + ending for part in RESOLVER_CALL)
            continue
        out.append(line)
        if body == RESOLVER_REQUIRE_ANCHOR:
            out.extend(part + ending for part in RESOLVER_REQUIRES)
    return b"".join(out)


def unpatch_resolver(text: bytes) -> bytes:
    out: list[bytes] = []
    in_block = False
    after_block = False
    for line in text.splitlines(keepends=True):
        body = line.rstrip(b"\r\n")
        if after_block:
            # The blank line the add-on put after its function.
            after_block = False
            if not body:
                continue
        if body == b"// corp-logo-addon: begin":
            in_block = True
            continue
        if in_block:
            if body == b"// corp-logo-addon: end":
                in_block = False
                after_block = True
            continue
        if MARK in body:
            continue
        out.append(line)
    return b"".join(out)


def state(root: Path) -> dict:
    handshake = (root / HANDSHAKE).read_bytes()
    delivery = root / DELIVERY
    return {
        "handshakeAnchor": handshake.count(ANCHOR) == 1,
        "handshakePatched": block_for(handshake) in handshake,
        "deliveryPresent": delivery.is_file(),
        "deliveryCurrent": delivery.is_file() and delivery.read_bytes() == (HERE / "files" / DELIVERY).read_bytes(),
        "imageServer": resolver_state((root / RESOLVER).read_bytes()),
    }


def backup(root: Path, relative: str, folder: Path) -> None:
    source = root / relative
    if source.is_file():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def install(root: Path, folder: Path) -> list[str]:
    current = state(root)
    if not current["handshakePatched"] and not current["handshakeAnchor"]:
        raise SystemExit(f"{HANDSHAKE} does not have the expected anchor; this EveJS version is not supported.")
    if current["imageServer"] == "unsupported":
        raise SystemExit(f"{RESOLVER} does not look as expected; this EveJS version is not supported.")
    done = []
    if current["deliveryPresent"] and not current["deliveryCurrent"]:
        backup(root, DELIVERY, folder)
    if not current["deliveryCurrent"]:
        shutil.copyfile(HERE / "files" / DELIVERY, root / DELIVERY)
        done.append(f"installed {DELIVERY}")
    if not current["handshakePatched"]:
        backup(root, HANDSHAKE, folder)
        text = (root / HANDSHAKE).read_bytes()
        (root / HANDSHAKE).write_bytes(text.replace(ANCHOR, block_for(text) + ANCHOR, 1))
        done.append(f"patched {HANDSHAKE}")
    if current["imageServer"] == "stock":
        backup(root, RESOLVER, folder)
        (root / RESOLVER).write_bytes(patch_resolver((root / RESOLVER).read_bytes()))
        done.append(f"patched {RESOLVER}")
    elif current["imageServer"] == "hand-edited":
        done.append(f"left {RESOLVER} as it is: it already serves corporation PNGs")
    return done


def remove(root: Path, folder: Path) -> list[str]:
    current = state(root)
    done = []
    if current["handshakePatched"]:
        backup(root, HANDSHAKE, folder)
        text = (root / HANDSHAKE).read_bytes()
        (root / HANDSHAKE).write_bytes(text.replace(block_for(text), b"", 1))
        done.append(f"unpatched {HANDSHAKE}")
    if current["imageServer"] == "patched":
        backup(root, RESOLVER, folder)
        (root / RESOLVER).write_bytes(unpatch_resolver((root / RESOLVER).read_bytes()))
        done.append(f"unpatched {RESOLVER}")
    if current["deliveryPresent"]:
        backup(root, DELIVERY, folder)
        try:
            (root / DELIVERY).unlink()
            done.append(f"removed {DELIVERY}")
        except OSError:
            # Some shared folders refuse deletes; nothing loads the file once
            # handshake.js no longer requires it.
            done.append(f"left {DELIVERY} in place (could not delete it); it is no longer loaded")
    return done


def launcher_package() -> Path | None:
    for folder in (HERE, HERE / "dist"):
        found = sorted(folder.glob("evejs-corporation-logos-launcher-v*.zip"))
        if found:
            return found[-1]
    return None


def rebuild(root: Path) -> None:
    """Rebuild and restart the Docker server so it runs the changed files."""
    tool = root / common.PLAYERBOTS_TOOL
    if tool.is_file():
        print("Rebuilding through Playerbots (playerbots.py rebuild); this takes a few minutes...", flush=True)
        subprocess.run([sys.executable, str(tool), "rebuild"], cwd=root, check=True)
        return
    settings = common.compose_settings(root)
    print("Rebuilding the EveJS image; this takes a few minutes...", flush=True)
    subprocess.run(common.compose_command(settings, "build", "init"), cwd=root, check=True)
    subprocess.run(common.compose_command(settings, "up", "-d", "--no-deps", "--force-recreate", "server"), cwd=root, check=True)
    print("Server restarted. It is ready when `docker compose ps` shows it healthy.")


def wizard() -> list[str]:
    print(f"Corporation Logos {VERSION}: show corporations with your own PNG logos in game.\n")
    default = r"C:\EVE\evejs" if sys.platform == "win32" else "."
    folder = input(f"EveJS folder [{default}]: ").strip().strip('"') or default
    root = Path(folder).expanduser().resolve()
    if (root / HANDSHAKE).is_file():
        current = state(root)
        if current["handshakePatched"] and current["deliveryCurrent"]:
            print("\nCorporation Logos is already installed in this folder.")
            action = common.ask("What now?", {"1": "keep it (nothing to do)", "2": "remove it"}, "1")
            if action == "1":
                return [str(root), "--check"]
            mode = "--docker" if common.looks_like_docker(root) else "--native"
            rebuild_now = mode == "--docker" and common.ask("Rebuild the server afterwards?", {"y": "yes", "n": "no"}, "y") == "y"
            return [str(root), "--remove", mode, *(["--rebuild"] if rebuild_now else [])]
    print("\nHow do you run EveJS?")
    mode = common.ask("Choose", {
        "1": "Docker Compose",
        "2": "Native with EVEJS Launcher",
        "3": "Native without the launcher (StartServer.bat)",
    }, "1" if common.looks_like_docker(root) else "2")
    if mode == "2":
        return [str(root), "--launcher"]
    if mode == "3":
        return [str(root), "--native"]
    print()
    rebuild_now = common.ask("Rebuild and restart the server afterwards?", {"y": "yes", "n": "no, I'll rebuild later"}, "y")
    return [str(root), "--docker", *(["--rebuild"] if rebuild_now == "y" else [])]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        argv = wizard()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", type=Path, help="EveJS folder (the one with server/)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--remove", action="store_true", help="remove the add-on")
    group.add_argument("--check", action="store_true", help="report the current state only")
    group.add_argument("--launcher", action="store_true", help="show the EVEJS Launcher package to import, change nothing")
    how = parser.add_mutually_exclusive_group()
    how.add_argument("--docker", dest="mode", action="store_const", const="docker", help="EveJS runs in Docker Compose")
    how.add_argument("--native", dest="mode", action="store_const", const="native", help="EveJS runs natively (Game must be stopped)")
    parser.add_argument("--rebuild", action="store_true", help="Docker: rebuild and restart the server afterwards")
    args = parser.parse_args(argv)
    root = args.target.expanduser().resolve()
    if not (root / HANDSHAKE).is_file() or not (root / RESOLVER).is_file():
        raise SystemExit(f"{root} does not look like an EveJS folder ({HANDSHAKE} not found).")
    if args.check:
        for key, value in state(root).items():
            print(f"{key}: {value}")
        return 0
    if args.launcher:
        package = launcher_package()
        print("In EVEJS Launcher, stop Game, open Mods > Import and choose:")
        print(f"  {package}" if package else "  evejs-corporation-logos-launcher-v<version>.zip (build it with tools/build.py)")
        print("Then enable Corporation Logos and start Game. Disable or Remove it there too.")
        print("Do not also run install.py on a launcher-managed folder.")
        return 0
    mode = args.mode or ("docker" if common.looks_like_docker(root) else "native")
    launcher_state = common.read_json(root / "_local" / "corporation-logos" / "state.json")
    if mode == "native" and launcher_state.get("status") in ("active", "installing", "restoring"):
        raise SystemExit("EVEJS Launcher manages Corporation Logos in this folder. Enable, disable or remove it in the launcher's Mods page instead.")
    if mode == "native" and common.game_running(root):
        host, port = common.game_address(root)
        raise SystemExit(f"Stop Game first: something is using its address {host}:{port}. Nothing was changed.")
    if args.rebuild and mode != "docker":
        raise SystemExit("--rebuild is for Docker installs; restart Game instead.")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    folder = root / f".corp-logo-addon-backup-{stamp}"
    done = remove(root, folder) if args.remove else install(root, folder)
    print("\n".join(done) if done else "Nothing to do: the files were already in place.")
    if folder.exists():
        print(f"Backups: {folder}")
    if mode == "docker":
        if args.rebuild:
            rebuild(root)
        elif done:
            print("Rebuild the EveJS server for the change to take effect"
                  + (" (python addons/playerbots/playerbots.py rebuild)." if (root / common.PLAYERBOTS_TOOL).is_file() else " (python install.py <folder> --docker --rebuild)."))
    elif done:
        print("Start Game (or restart it) for the change to take effect.")
    if not args.remove:
        print("Next: give a corporation a logo with Set-Corporation-Logo.cmd (or corp_logo.py), or on the Playerbots dashboard.")
    return 0


if __name__ == "__main__":
    interactive = len(sys.argv) == 1
    try:
        code = main()
    except SystemExit as stop:
        if interactive and stop.code not in (0, None):
            print(stop.code if isinstance(stop.code, str) else f"Stopped (exit {stop.code}).")
            if sys.platform == "win32":
                input("\nPress Enter to close.")
        raise
    except subprocess.CalledProcessError as error:
        print(f"A Docker command failed (exit {error.returncode}); its messages are above. The files were changed and backed up; rerun the rebuild when Docker is fixed.")
        code = 1
    if interactive and sys.platform == "win32":
        input("\nPress Enter to close.")
    sys.exit(code)

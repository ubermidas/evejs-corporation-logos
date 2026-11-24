#!/usr/bin/env python3
"""Install or remove the corporation logo add-on in an EveJS folder.

    python install.py /path/to/evejs            # install
    python install.py --remove /path/to/evejs   # remove
    python install.py --check /path/to/evejs    # report only, change nothing

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

It never edits a file the Playerbots add-on manages. Rebuild the server
image afterwards (Docker) or restart EveJS (native).
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", type=Path, help="EveJS folder (the one with server/)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--remove", action="store_true", help="remove the add-on")
    group.add_argument("--check", action="store_true", help="report the current state only")
    args = parser.parse_args()
    root = args.target.resolve()
    if not (root / HANDSHAKE).is_file() or not (root / RESOLVER).is_file():
        raise SystemExit(f"{root} does not look like an EveJS folder ({HANDSHAKE} not found).")
    if args.check:
        for key, value in state(root).items():
            print(f"{key}: {value}")
        return 0
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = root / f".corp-logo-addon-backup-{stamp}"
    done = remove(root, folder) if args.remove else install(root, folder)
    print("\n".join(done) if done else "Nothing to do.")
    if folder.exists():
        print(f"Backups: {folder}")
    print("Rebuild the EveJS server image (Docker) or restart EveJS (native) for the change to take effect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

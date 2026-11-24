#!/usr/bin/env python3
"""Build the release ZIPs into dist/.

    python tools/build.py

dist/evejs-corporation-logos-launcher-v<version>.zip
    The EVEJS Launcher package (Mods > Import): the launcher manifest and
    helper, the server changes, and the logo tool.
dist/evejs-corporation-logos-v<version>.zip
    Everything: the installer for Docker and native installs, the logo tool,
    and the launcher package.

The ZIPs are reproducible: the same sources give the same bytes.
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
STAMP = (2026, 1, 1, 0, 0, 0)

COMMON = [
    "files/server/src/services/corporation/corporationLogoClientDelivery.js",
    "corp_logo.py",
    "addon_common.py",
    "Set-Corporation-Logo.cmd",
    "tools/corp-logo-store.js",
    "VERSION",
]

LAUNCHER_README = """Corporation Logos {version} for EVEJS Launcher
================================================

Install
  1. Stop Game in EVEJS Launcher.
  2. Mods > Import, and choose this ZIP.
  3. Enable Corporation Logos, then start Game.

Give a corporation a logo
  Double-click Set-Corporation-Logo.cmd in the mod's folder and answer the
  questions (EveJS folder, corporation ID, image), or use the Playerbots
  dashboard's Logo panel if you run Playerbots. Players see a logo from their
  next login.

Disable or Remove in the launcher puts the EveJS files back; the logo images
are kept. Full instructions: README.md in the full release, or
https://github.com/ubermidas/evejs-corporation-logos
"""


def write_zip(path: Path, entries: list[tuple[str, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries):
            info = zipfile.ZipInfo(name, STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)


def read(relative: str) -> bytes:
    return (ROOT / relative).read_bytes()


def main() -> int:
    version = read("VERSION").decode().strip()
    manifest = json.loads(read("launcher/evejs-launcher.mod.json"))
    if manifest["version"] != version:
        raise SystemExit(f"launcher/evejs-launcher.mod.json says {manifest['version']}, VERSION says {version}.")
    DIST.mkdir(exist_ok=True)
    for old in DIST.glob("evejs-corporation-logos-*.zip"):
        old.unlink()

    folder = "evejs-corporation-logos"
    launcher = [
        (f"{folder}/evejs-launcher.mod.json", read("launcher/evejs-launcher.mod.json")),
        (f"{folder}/helper.cjs", read("launcher/helper.cjs")),
        (f"{folder}/lib/patch.cjs", read("lib/patch.cjs")),
        (f"{folder}/README.txt", LAUNCHER_README.format(version=version).replace("\n", "\r\n").encode()),
        *[(f"{folder}/{relative}", read(relative)) for relative in COMMON],
    ]
    launcher_zip = DIST / f"evejs-corporation-logos-launcher-v{version}.zip"
    write_zip(launcher_zip, launcher)

    release_folder = f"evejs-corporation-logos-v{version}"
    release = [
        *[(f"{release_folder}/{relative}", read(relative)) for relative in [
            *COMMON, "install.py", "install.cmd", "README.md", "lib/patch.cjs",
            "launcher/evejs-launcher.mod.json", "launcher/helper.cjs",
        ]],
        (f"{release_folder}/{launcher_zip.name}", launcher_zip.read_bytes()),
    ]
    write_zip(DIST / f"{release_folder}.zip", release)
    for built in sorted(DIST.glob("evejs-corporation-logos-*.zip")):
        print(f"{built.relative_to(ROOT)}  {built.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

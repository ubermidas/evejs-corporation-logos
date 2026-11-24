"""Tests for install.py and corp_logo.py against a small stand-in EveJS tree.

Run from the repository root:  python -m unittest discover tests
Needs Node.js on PATH. The Pillow test is skipped without Pillow.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HANDSHAKE = (
    "function buildSource() {\n"
    "  const source = [];\n"
    "  // Last, in its own try block: the GM Server Health window, when enabled.\n"
    "  return source;\n"
    "}\n"
)

# The shape of EveJS 261115.2's image resolver around the lines the add-on
# touches; mixed line endings, as in the real file.
RESOLVER = (
    '"use strict";\r\n'
    "\r\n"
    'const path = require("path");\r\n'
    "\r\n"
    "const DEFAULT_CORPORATION_LOGO_PATH = path.join(__dirname, \"evejscorp.png\");\r\n"
    "function toPositiveInteger(value, fallback = null) {\r\n"
    "  const numeric = Number(value);\r\n"
    "  return Number.isSafeInteger(numeric) && numeric > 0 ? numeric : fallback;\r\n"
    "}\r\n"
    "\r\n"
    "function resolveCorporationLogoPath(corporationID, size) {\n"
    "  return DEFAULT_CORPORATION_LOGO_PATH;\n"
    "}\n"
    "\r\n"
    "module.exports = { resolveCorporationLogoPath };\r\n"
)

STORE_ROOT = (
    'const path = require("path");\n'
    "module.exports = { resolveRuntimeImagesDir: () => path.join(__dirname, '../../../data/images') };\n"
)


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), ROOT / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + b"\x80\x40\x20\xff" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


class EveJSTree(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="corp-logo-test-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        files = {
            "server/src/network/tcp/handshake.js": HANDSHAKE.encode(),
            "server/src/_secondary/image/imageRequestResolver.js": RESOLVER.encode(),
            "server/src/gameStore/storeRoot.js": STORE_ROOT.encode(),
        }
        for relative, data in files.items():
            path = self.dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.dir / "server/src/services/corporation").mkdir(parents=True)

    def run_tool(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)


class InstallTests(EveJSTree):
    def test_install_then_remove_restores_every_file(self) -> None:
        before = {p: p.read_bytes() for p in self.dir.rglob("*") if p.is_file()}
        first = self.run_tool("install.py", str(self.dir))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("patched server/src/_secondary/image/imageRequestResolver.js", first.stdout)
        self.assertIn("Nothing to do.", self.run_tool("install.py", str(self.dir)).stdout)
        check = self.run_tool("install.py", "--check", str(self.dir)).stdout
        self.assertIn("imageServer: patched", check)
        self.assertIn("handshakePatched: True", check)
        removed = self.run_tool("install.py", "--remove", str(self.dir))
        self.assertEqual(removed.returncode, 0, removed.stderr)
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data, path)
        self.assertFalse((self.dir / "server/src/services/corporation/corporationLogoClientDelivery.js").exists())

    def test_patched_image_server_serves_the_stored_png(self) -> None:
        self.run_tool("install.py", str(self.dir))
        logos = self.dir / "data/images/Corporation"
        logos.mkdir(parents=True)
        (logos / "98000002_128.png").write_bytes(png(128, 128))
        script = (
            "const r = require(process.argv[1]);"
            "console.log(JSON.stringify([r.resolveCorporationLogoPath(98000002, 256), r.resolveCorporationLogoPath(98000005, 128)]));"
        )
        resolver = self.dir / "server/src/_secondary/image/imageRequestResolver.js"
        result = subprocess.run(["node", "-e", script, str(resolver)], capture_output=True, text=True, check=True)
        found, fallback = json.loads(result.stdout)
        self.assertTrue(found.endswith("98000002_128.png"))
        self.assertTrue(fallback.endswith("evejscorp.png"))

    def test_a_hand_edited_image_server_is_left_alone(self) -> None:
        resolver = self.dir / "server/src/_secondary/image/imageRequestResolver.js"
        edited = resolver.read_bytes().replace(b"function resolveCorporationLogoPath", b"function findCustomCorporationLogoPath() {}\nfunction resolveCorporationLogoPath")
        resolver.write_bytes(edited)
        result = self.run_tool("install.py", str(self.dir))
        self.assertIn("left server/src/_secondary/image/imageRequestResolver.js as it is", result.stdout)
        self.run_tool("install.py", "--remove", str(self.dir))
        self.assertEqual(resolver.read_bytes(), edited)

    def test_the_client_patch_lists_only_player_corporations(self) -> None:
        logos = self.dir / "logos"
        logos.mkdir()
        for name in ("98000002_128.png", "98000002_256.png", "1000125_128.png", "notes.txt"):
            (logos / name).write_bytes(b"x")
        delivery = ROOT / "files/server/src/services/corporation/corporationLogoClientDelivery.js"
        shutil.copyfile(delivery, self.dir / "server/src/services/corporation/corporationLogoClientDelivery.js")
        script = (
            "const d = require(process.argv[1]);"
            "const ids = d.listCustomCorporationLogoIDs(process.argv[2]);"
            "console.log(JSON.stringify({ ids, source: d.buildCorporationLogoSignedFuncSource(ids) }));"
        )
        result = subprocess.run(
            ["node", "-e", script, str(self.dir / "server/src/services/corporation/corporationLogoClientDelivery.js"), str(logos)],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(result.stdout)
        self.assertEqual(data["ids"], [98000002])
        self.assertIn(" _evejs_eveIcon._evejsCustomCorpLogoIDs = set([98000002])", data["source"])
        self.assertFalse(any('"' in line for line in data["source"]), "the handshake wraps these lines in double quotes")


class CorpLogoTests(EveJSTree):
    def logo_dir(self) -> Path:
        return self.dir / "data/images/Corporation"

    def test_set_list_and_remove_a_square_png(self) -> None:
        image = self.dir / "square.png"
        image.write_bytes(png(64, 64))
        tool = load("corp_logo.py")
        images = tool.make_sizes(image)
        self.assertEqual(sorted(images, key=int), ["32", "64", "128", "256", "512", "1024"])
        reply = tool.run_store(self.dir, "native", {"command": "set", "corporationID": 98000002, "images": images})
        self.assertEqual(reply["sizes"], [32, 64, 128, 256, 512, 1024])
        self.assertEqual(len(list(self.logo_dir().glob("98000002_*.png"))), 6)
        listed = tool.run_store(self.dir, "native", {"command": "list"})
        self.assertEqual(listed["logos"], {"98000002": [32, 64, 128, 256, 512, 1024]})
        removed = tool.run_store(self.dir, "native", {"command": "remove", "corporationID": 98000002})
        self.assertEqual(removed["removed"], [32, 64, 128, 256, 512, 1024])
        self.assertEqual(list(self.logo_dir().iterdir()), [])

    def test_npc_corporations_and_broken_images_are_refused(self) -> None:
        tool = load("corp_logo.py")
        with self.assertRaises(SystemExit):
            tool.corporation_id("1000125")
        with self.assertRaises(SystemExit) as stop:
            tool.run_store(self.dir, "native", {"command": "set", "corporationID": 98000002, "images": {"32": base64.b64encode(b"nope").decode()}})
        self.assertIn("not a PNG", str(stop.exception))
        self.assertFalse(self.logo_dir().exists())

    def test_command_line_round_trip(self) -> None:
        image = self.dir / "logo.png"
        image.write_bytes(png(40, 40))
        result = self.run_tool("corp_logo.py", "set", str(self.dir), "98000009", str(image), "--native")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Logo saved for corporation 98000009", result.stdout)
        self.assertIn("98000009: 32, 64, 128, 256, 512, 1024 px", self.run_tool("corp_logo.py", "list", str(self.dir), "--native").stdout)

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is not installed")
    def test_pillow_centres_other_shapes_on_a_transparent_square(self) -> None:
        from PIL import Image

        image = self.dir / "wide.jpg"
        Image.new("RGB", (300, 150), (200, 40, 40)).save(image)
        images = load("corp_logo.py").make_sizes(image)
        for size in (32, 1024):
            with Image.open(io.BytesIO(base64.b64decode(images[str(size)]))) as result:
                self.assertEqual(result.size, (size, size))
                self.assertEqual(result.getpixel((0, 0))[3], 0)
                self.assertEqual(result.getpixel((size // 2, size // 2))[:3], (200, 40, 40))


if __name__ == "__main__":
    unittest.main()

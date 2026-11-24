"""Tests for install.py and corp_logo.py against a small stand-in EveJS tree.

Run from the repository root:  python -m unittest discover tests
Needs Node.js on PATH. The Pillow test is skipped without Pillow.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import shutil
import socket
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

# Like EveJS: EVEJS_GAMESTORE_DATA_DIR wins; images sit beside the data folder.
STORE_ROOT = (
    'const path = require("path");\n'
    "const dataDir = () => process.env.EVEJS_GAMESTORE_DATA_DIR ? path.resolve(process.env.EVEJS_GAMESTORE_DATA_DIR) : path.join(__dirname, '../../../data/data');\n"
    "module.exports = { resolveRuntimeImagesDir: () => path.join(dataDir(), '..', 'images') };\n"
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


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class EveJSTree(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="corp-logo-test-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        files = {
            "server/src/network/tcp/handshake.js": HANDSHAKE.encode(),
            "server/src/_secondary/image/imageRequestResolver.js": RESOLVER.encode(),
            "server/src/gameStore/storeRoot.js": STORE_ROOT.encode(),
        }
        self.port = free_port()
        files["config/server.json"] = json.dumps({"network": {"serverPort": self.port, "gameServerBindHost": "127.0.0.1"}}).encode()
        for relative, data in files.items():
            path = self.dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.dir / "server/src/services/corporation").mkdir(parents=True)

    def run_tool(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, env=env)

    def snapshot(self) -> dict:
        return {p.relative_to(self.dir).as_posix(): p.read_bytes() for p in self.dir.rglob("*")
                if p.is_file() and ".corp-logo-addon-backup-" not in p.as_posix() and "_local" not in p.parts}

    def hold_game_port(self) -> socket.socket:
        holder = socket.socket()
        holder.bind(("127.0.0.1", self.port))
        holder.listen()
        self.addCleanup(holder.close)
        return holder

    def fake_docker(self) -> tuple[dict, Path]:
        """A stand-in docker on PATH that logs its arguments and says the server runs."""
        bin_dir = self.dir / "fake-bin"
        bin_dir.mkdir()
        log = self.dir / "docker.log"
        script = bin_dir / "docker"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if 'ps' in sys.argv: print('abc123')\n"
            "if 'exec' in sys.argv:\n"
            "    sys.stdin.read(); print(json.dumps({'ok': True, 'folder': '/var/lib/evejs/gameStore/images/Corporation', 'logos': {}}))\n"
        )
        script.chmod(0o755)
        env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        return env, log


class InstallTests(EveJSTree):
    def test_install_then_remove_restores_every_file(self) -> None:
        before = {p: p.read_bytes() for p in self.dir.rglob("*") if p.is_file()}
        first = self.run_tool("install.py", str(self.dir))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("patched server/src/_secondary/image/imageRequestResolver.js", first.stdout)
        self.assertIn("Nothing to do", self.run_tool("install.py", str(self.dir)).stdout)
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


class NodeCoreTests(EveJSTree):
    """lib/patch.cjs, used by the launcher helper, must write exactly what install.py writes."""

    def node_patch(self, action: str, root: Path) -> None:
        script = (
            "const p = require(process.argv[1]);"
            "const r = p[process.argv[2]](process.argv[3], process.argv[4], require('path').join(process.argv[3], '_local/b'));"
            "console.log(JSON.stringify(r));"
        )
        subprocess.run(["node", "-e", script, str(ROOT / "lib/patch.cjs"), action, str(root), str(ROOT)], check=True, capture_output=True)

    @staticmethod
    def twin_files(twin: Path) -> dict:
        return {p.relative_to(twin).as_posix(): p.read_bytes() for p in twin.rglob("*")
                if p.is_file() and "_local" not in p.parts and ".corp-logo-addon-backup-" not in p.as_posix()}

    def test_node_and_python_write_identical_files(self) -> None:
        for resolver_edit in (False, True):
            with self.subTest(hand_edited_resolver=resolver_edit):
                if resolver_edit:
                    resolver = self.dir / "server/src/_secondary/image/imageRequestResolver.js"
                    resolver.write_bytes(resolver.read_bytes().replace(b"function resolveCorporationLogoPath", b"function findCustomCorporationLogoPath() {}\nfunction resolveCorporationLogoPath"))
                twin = Path(tempfile.mkdtemp(prefix="corp-logo-twin-"))
                self.addCleanup(shutil.rmtree, twin, True)
                shutil.copytree(self.dir, twin, dirs_exist_ok=True)
                start = self.snapshot()
                self.run_tool("install.py", str(self.dir), "--native")
                self.node_patch("install", twin)
                python_installed = self.snapshot()
                self.assertNotEqual(python_installed, start)
                self.assertEqual(self.twin_files(twin), python_installed)
                self.run_tool("install.py", str(self.dir), "--remove", "--native")
                self.node_patch("remove", twin)
                self.assertEqual(self.snapshot(), start)
                self.assertEqual(self.twin_files(twin), start)


class LauncherTests(EveJSTree):
    """The EVEJS Launcher package, built and unpacked, driven the way the launcher drives it."""

    def setUp(self) -> None:
        super().setUp()
        subprocess.run([sys.executable, str(ROOT / "tools/build.py")], check=True, capture_output=True)
        version = (ROOT / "VERSION").read_text().strip()
        self.package = Path(tempfile.mkdtemp(prefix="corp-logo-launcher-"))
        self.addCleanup(shutil.rmtree, self.package, True)
        import zipfile
        with zipfile.ZipFile(ROOT / f"dist/evejs-corporation-logos-launcher-v{version}.zip") as archive:
            archive.extractall(self.package)
        self.helper = self.package / "evejs-corporation-logos" / "helper.cjs"
        self.count = 0

    def launch(self, verb: str, **changes) -> dict:
        self.count += 1
        request = {
            "protocol": "evejs_launcher_mod_v1", "requestId": f"r{self.count}", "action": verb, "profile": None,
            "runtime": {"backend": "native", "evejsRoot": str(self.dir)}, "mod": {"id": "evejs-corporation-logos"},
        }
        request.update(changes)
        request_file = self.package / f"request-{self.count}.json"
        result_file = self.package / f"result-{self.count}.json"
        request_file.write_text(json.dumps(request))
        subprocess.run(["node", str(self.helper), "--request", str(request_file), "--result", str(result_file)], check=True, capture_output=True)
        result = json.loads(result_file.read_text())
        self.assertEqual(result["protocol"], "evejs_launcher_mod_v1")
        self.assertEqual(result["requestId"], f"r{self.count}")
        return result

    def launcher_state(self) -> dict:
        return json.loads((self.dir / "_local/corporation-logos/state.json").read_text())

    def test_manifest_matches_the_launcher_format(self) -> None:
        manifest = json.loads((self.package / "evejs-corporation-logos/evejs-launcher.mod.json").read_text())
        self.assertEqual(manifest["schemaVersion"], 3)
        self.assertEqual(manifest["kind"], "source-integrated")
        self.assertEqual(manifest["supportedBackends"], ["native"])
        self.assertEqual(manifest["launcherApi"]["helper"], {"runtime": "node", "path": "helper.cjs"})
        self.assertEqual(manifest["version"], (ROOT / "VERSION").read_text().strip())

    def test_enable_disable_and_recover(self) -> None:
        start = self.snapshot()
        installed = self.launch("install")
        self.assertTrue(installed["success"], installed["message"])
        self.assertEqual(self.launcher_state()["status"], "active")
        self.assertIn("imageServer: patched", self.run_tool("install.py", str(self.dir), "--check").stdout)
        self.assertIn("already installed", self.launch("install")["message"])
        disabled = self.launch("prepare_disable")
        self.assertTrue(disabled["success"], disabled["message"])
        self.assertEqual(self.launcher_state()["status"], "stock")
        self.assertEqual(self.snapshot(), start)
        self.assertIn("already disabled", self.launch("prepare_remove")["message"])
        # An interrupted enable is finished by recover.
        (self.dir / "_local/corporation-logos/state.json").write_text(json.dumps({"schema": 1, "version": "1.0.0", "status": "installing"}))
        self.assertTrue(self.launch("recover")["success"])
        self.assertEqual(self.launcher_state()["status"], "active")
        self.assertTrue(self.launch("prepare_remove")["success"])
        self.assertEqual(self.snapshot(), start)

    def test_game_must_be_stopped_and_nothing_changes_otherwise(self) -> None:
        start = self.snapshot()
        self.hold_game_port()
        result = self.launch("install")
        self.assertFalse(result["success"])
        self.assertIn("Stop Game", result["message"])
        self.assertEqual(self.snapshot(), start)

    def test_foreign_requests_are_refused(self) -> None:
        for change in ({"mod": {"id": "something-else"}}, {"runtime": {"backend": "docker", "evejsRoot": str(self.dir)}}, {"action": "explode"}):
            with self.subTest(change=change):
                self.assertFalse(self.launch("install", **change)["success"])

    def test_install_py_leaves_launcher_managed_folders_alone(self) -> None:
        self.launch("install")
        result = self.run_tool("install.py", str(self.dir), "--remove", "--native")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EVEJS Launcher manages", result.stdout + result.stderr)


class InstallerFlowTests(EveJSTree):
    def test_native_install_refuses_while_game_runs(self) -> None:
        start = self.snapshot()
        self.hold_game_port()
        result = self.run_tool("install.py", str(self.dir), "--native")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Stop Game first", result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), start)

    def test_launcher_route_points_to_the_package(self) -> None:
        subprocess.run([sys.executable, str(ROOT / "tools/build.py")], check=True, capture_output=True)
        result = self.run_tool("install.py", str(self.dir), "--launcher")
        self.assertIn("Mods > Import", result.stdout)
        self.assertIn("evejs-corporation-logos-launcher-v", result.stdout)
        self.assertFalse((self.dir / "server/src/services/corporation/corporationLogoClientDelivery.js").exists())

    def test_docker_rebuild_keeps_playerbots_compose_settings(self) -> None:
        env, log = self.fake_docker()
        (self.dir / "compose.yaml").write_text("services: {}\n")
        (self.dir / "addons/playerbots").mkdir(parents=True)
        (self.dir / "addons/playerbots/compose.override.yaml").write_text("services: {}\n")
        (self.dir / ".playerbots-install-state.json").write_text(json.dumps({"compose": {"baseFile": "compose.yaml", "extraFiles": ["launcher.yaml"], "projectName": "evejs"}}))
        result = self.run_tool("install.py", str(self.dir), "--docker", "--rebuild", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        expected = ["compose", "--project-name", "evejs", "-f", "compose.yaml", "-f", "launcher.yaml", "-f", "addons/playerbots/compose.override.yaml"]
        self.assertEqual(calls[0], [*expected, "build", "init"])
        self.assertEqual(calls[1], [*expected, "up", "-d", "--no-deps", "--force-recreate", "server"])

    def test_docker_rebuild_goes_through_playerbots_when_installed(self) -> None:
        env, log = self.fake_docker()
        (self.dir / "compose.yaml").write_text("services: {}\n")
        tool = self.dir / "addons/playerbots/playerbots.py"
        tool.parent.mkdir(parents=True)
        marker = self.dir / "playerbots-called"
        tool.write_text(f"import sys\nopen({str(marker)!r}, 'w').write(' '.join(sys.argv[1:]))\n")
        result = self.run_tool("install.py", str(self.dir), "--docker", "--rebuild", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(marker.read_text(), "rebuild")
        self.assertFalse(log.exists())


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

    def test_docker_exec_uses_the_recorded_compose_settings(self) -> None:
        env, log = self.fake_docker()
        (self.dir / "compose.yaml").write_text("services: {}\n")
        (self.dir / ".playerbots-install-state.json").write_text(json.dumps({"compose": {"projectName": "evejs", "extraFiles": ["launcher.yaml"]}}))
        result = self.run_tool("corp_logo.py", "list", str(self.dir), env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        exec_call = next(call for call in calls if "exec" in call)
        self.assertEqual(exec_call[:7], ["compose", "--project-name", "evejs", "-f", "compose.yaml", "-f", "launcher.yaml"])
        self.assertEqual(exec_call[7:13], ["exec", "-T", "server", "node", "-e", (ROOT / "tools/corp-logo-store.js").read_text()])

    def test_native_data_dir_option(self) -> None:
        image = self.dir / "logo.png"
        image.write_bytes(png(32, 32))
        other = self.dir / "elsewhere" / "data"
        other.mkdir(parents=True)
        tool = load("corp_logo.py")
        reply = tool.run_store(self.dir, "native", {"command": "set", "corporationID": 98000002, "images": tool.make_sizes(image)}, data_dir=str(other))
        self.assertEqual(Path(reply["folder"]), (self.dir / "elsewhere" / "images" / "Corporation").resolve())

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

#!/usr/bin/env python3
"""Give a corporation a PNG logo, list logos, or remove one.

    python corp_logo.py set    <evejs folder> <corporation ID> <image file>
    python corp_logo.py remove <evejs folder> <corporation ID>
    python corp_logo.py list   <evejs folder>
    python corp_logo.py                      (asks for everything)

The logo is stored in EveJS's runtime image folder as
Corporation/<corporationID>_<size>.png at 32, 64, 128, 256, 512 and 1024
pixels. With Pillow installed (pip install pillow), any PNG, JPEG, WebP or GIF
works: it is centred on a transparent square and resized for each size.
Without Pillow the file must be a square PNG, and it is stored as is at every
size, the way EveJS stores alliance logos.

For Docker installs the server must be running; the logo is written into its
data volume with `docker compose exec`. For native installs it is written
into the EveJS folder's game store. Nothing needs rebuilding. Players see a
new logo in game the next time they log in, once install.py has added the
client patch.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORE_SCRIPT = HERE / "tools" / "corp-logo-store.js"
SIZES = (32, 64, 128, 256, 512, 1024)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
FIRST_PLAYER_CORPORATION_ID = 2_000_000


def png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 33 or not data.startswith(PNG_MAGIC) or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def make_sizes(image_path: Path) -> dict[str, str]:
    data = image_path.read_bytes()
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        size = png_size(data)
        if not size:
            raise SystemExit("Without Pillow the image must be a PNG. Install Pillow (pip install pillow) to use other formats.")
        if size[0] != size[1]:
            raise SystemExit(f"The PNG is {size[0]}x{size[1]}. Without Pillow it must be square; install Pillow to have it centred automatically.")
        if len(data) > 2 * 1024 * 1024:
            raise SystemExit("The PNG is larger than 2 MB.")
        encoded = base64.b64encode(data).decode("ascii")
        return {str(s): encoded for s in SIZES}
    with Image.open(io.BytesIO(data)) as source:
        source.seek(0)
        image = source.convert("RGBA")
    images: dict[str, str] = {}
    current = image
    for size in sorted(SIZES, reverse=True):
        scale = min(size / current.width, size / current.height)
        width, height = max(1, round(current.width * scale)), max(1, round(current.height * scale))
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.paste(current.resize((width, height), Image.LANCZOS), ((size - width) // 2, (size - height) // 2))
        buffer = io.BytesIO()
        canvas.save(buffer, "PNG", optimize=True)
        if buffer.tell() > 2 * 1024 * 1024:
            raise SystemExit(f"The {size} px logo is larger than 2 MB; use a simpler image.")
        images[str(size)] = base64.b64encode(buffer.getvalue()).decode("ascii")
        current = canvas
    return images


def corporation_id(value: str) -> int:
    try:
        number = int(str(value).strip())
    except ValueError:
        raise SystemExit(f"{value!r} is not a corporation ID.")
    if number < FIRST_PLAYER_CORPORATION_ID:
        raise SystemExit("Give a player or bot corporation ID (such as 98000002). NPC corporations keep their game logos.")
    return number


def detect_mode(root: Path, requested: str | None) -> str:
    if requested:
        return requested
    if (root / "compose.yaml").is_file() and shutil.which("docker"):
        probe = subprocess.run(
            ["docker", "compose", "-f", "compose.yaml", "ps", "-q", "server"],
            cwd=root, capture_output=True, text=True,
        )
        if probe.returncode == 0 and probe.stdout.strip():
            return "docker"
    if shutil.which("node"):
        return "native"
    if (root / "compose.yaml").is_file():
        raise SystemExit("The EveJS server container is not running. Start EveJS, then try again.")
    raise SystemExit("Node.js was not found, so the logo cannot be stored. Install Node.js or run this where EveJS runs.")


def run_store(root: Path, mode: str, request: dict) -> dict:
    script = STORE_SCRIPT.read_text(encoding="utf-8")
    if mode == "docker":
        command = ["docker", "compose", "-f", "compose.yaml", "exec", "-T", "server", "node", "-e", script]
    else:
        command = ["node", "-e", script]
    result = subprocess.run(command, cwd=root, input=json.dumps(request).encode("utf-8"), capture_output=True)
    output = result.stdout.decode("utf-8", "replace").strip().splitlines()
    try:
        reply = json.loads(output[-1]) if output else {}
    except json.JSONDecodeError:
        reply = {}
    if not reply.get("ok"):
        detail = reply.get("error") or result.stderr.decode("utf-8", "replace").strip()[-400:] or "no reply"
        raise SystemExit(f"Storing the logo failed: {detail}")
    return reply


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip().strip('"')
    return answer or default


def interactive() -> list[str]:
    print("Corporation logo: give a corporation a picture in place of its emblem.\n")
    root = ask("EveJS folder", r"C:\EVE\evejs" if sys.platform == "win32" else "")
    action = ask("Set, remove or list logos? (set/remove/list)", "set").lower()
    if action.startswith("l"):
        return ["list", root]
    corp = ask("Corporation ID (shown on the corporation's info window, such as 98000002)")
    if action.startswith("r"):
        return ["remove", root, corp]
    image = ask("Image file (PNG; with Pillow also JPEG, WebP or GIF)")
    return ["set", root, corp, image]


def main(argv: list[str]) -> int:
    paused = not argv
    if paused:
        argv = interactive()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["set", "remove", "list"])
    parser.add_argument("target", type=Path, help="EveJS folder (with server/)")
    parser.add_argument("corporation", nargs="?", help="corporation ID")
    parser.add_argument("image", nargs="?", type=Path, help="image file (set only)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--docker", dest="mode", action="store_const", const="docker", help="EveJS runs in Docker")
    mode.add_argument("--native", dest="mode", action="store_const", const="native", help="EveJS runs natively")
    args = parser.parse_args(argv)
    root = args.target.expanduser().resolve()
    if not (root / "server" / "src" / "gameStore" / "storeRoot.js").is_file():
        raise SystemExit(f"{root} does not look like an EveJS folder.")
    if args.command != "list" and not args.corporation:
        raise SystemExit("Give the corporation ID.")
    if args.command == "set" and not args.image:
        raise SystemExit("Give the image file.")

    if args.command == "set":
        image = args.image.expanduser()
        if not image.is_file():
            raise SystemExit(f"Image not found: {image}")
        request = {"command": "set", "corporationID": corporation_id(args.corporation), "images": make_sizes(image)}
    elif args.command == "remove":
        request = {"command": "remove", "corporationID": corporation_id(args.corporation)}
    else:
        request = {"command": "list"}

    reply = run_store(root, detect_mode(root, args.mode), request)
    if args.command == "set":
        print(f"Logo saved for corporation {reply['corporationID']} at {', '.join(map(str, reply['sizes']))} px.")
        print("Players see it in game the next time they log in (the client patch from install.py must be installed).")
    elif args.command == "remove":
        if reply["removed"]:
            print(f"Logo removed for corporation {reply['corporationID']}. The game shows its emblem again after players next log in.")
        else:
            print(f"Corporation {reply['corporationID']} had no logo.")
    else:
        logos = reply.get("logos") or {}
        if not logos:
            print("No corporation has a logo yet.")
        for corp, sizes in sorted(logos.items(), key=lambda item: int(item[0])):
            print(f"{corp}: {', '.join(map(str, sizes))} px")
    if paused and sys.platform == "win32":
        input("\nPress Enter to close.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(main(sys.argv[1:]))
    # Double-clicked: keep the window open long enough to read any error.
    try:
        sys.exit(main([]))
    except SystemExit as stop:
        if stop.code not in (0, None):
            print(stop.code if isinstance(stop.code, str) else f"Stopped (exit {stop.code}).")
            if sys.platform == "win32":
                input("\nPress Enter to close.")
        raise

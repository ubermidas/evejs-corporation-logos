"""Shared helpers for install.py and corp_logo.py: finding how an EveJS folder
runs (Docker Compose or native), building Docker Compose commands that match
the Playerbots add-on's recorded settings, and checking that Game is stopped.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from pathlib import Path

PLAYERBOTS_STATE = ".playerbots-install-state.json"
PLAYERBOTS_OVERRIDE = "addons/playerbots/compose.override.yaml"
PLAYERBOTS_TOOL = "addons/playerbots/playerbots.py"


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def compose_settings(root: Path, project_name: str = "", extra_files: list[str] | None = None, env_file: str = "") -> dict:
    """The Compose files and project for this folder: what the caller passed,
    else what the Playerbots installer recorded, else the EveJS defaults. The
    Playerbots override is included whenever it is installed, so a rebuild or
    an exec never drops Playerbots' settings from the server."""
    saved = read_json(root / PLAYERBOTS_STATE).get("compose") or {}
    files = [saved.get("baseFile") or "compose.yaml", *(saved.get("extraFiles") or []), *(extra_files or [])]
    if (root / PLAYERBOTS_OVERRIDE).is_file():
        files.append(PLAYERBOTS_OVERRIDE)
    unique: list[str] = []
    for file in files:
        if file and file not in unique:
            unique.append(str(file))
    return {
        "files": unique,
        "projectName": project_name or saved.get("projectName") or "",
        "envFile": env_file or saved.get("envFile") or "",
    }


def compose_command(settings: dict, *arguments: str) -> list[str]:
    command = ["docker", "compose"]
    if settings.get("projectName"):
        command += ["--project-name", settings["projectName"]]
    if settings.get("envFile"):
        command += ["--env-file", settings["envFile"]]
    for file in settings["files"]:
        command += ["-f", file]
    return command + list(arguments)


def docker_available() -> bool:
    return shutil.which("docker") is not None


def docker_server_running(root: Path, settings: dict) -> bool:
    if not docker_available():
        return False
    probe = subprocess.run(compose_command(settings, "ps", "-q", "server"), cwd=root, capture_output=True, text=True)
    return probe.returncode == 0 and bool(probe.stdout.strip())


def looks_like_docker(root: Path) -> bool:
    return (root / "compose.yaml").is_file() and docker_available()


def game_address(root: Path) -> tuple[str, int]:
    network = read_json(root / "config" / "server.json").get("network") or {}
    port = int(network.get("serverPort") or 26000)
    host = str(network.get("gameServerBindHost") or "127.0.0.1")
    return host, port


def game_running(root: Path) -> bool:
    """True when something holds Game's port (native installs only)."""
    host, port = game_address(root)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return True
    return False


def ask(question: str, choices: dict[str, str], default: str) -> str:
    for key, label in choices.items():
        print(f"  {key}) {label}")
    keys = "/".join(choices)
    while True:
        answer = input(f"{question} [{keys}, default {default}]: ").strip().lower() or default
        if answer in choices:
            return answer
        print(f"Please answer {' or '.join(choices)}.")

import json
import logging
import os
import subprocess
from collections.abc import Callable
from typing import Literal

from pydantic import validate_call

from ankama_launcher_emulator.consts import ANSI_ESCAPE, CYTRUS_INSTALLED
from ankama_launcher_emulator.interfaces.ankama_release import ReleaseJson

logger = logging.getLogger()

Game = Literal["dofus"]
Release = Literal["dofus3"]


@validate_call
def cytrus_get_latest_version(game: Game, release: Release) -> str:
    result = subprocess.run(
        [
            "cytrus-v6",
            "version",
            "--game",
            game,
            "--release",
            release,
            "--platform",
            "windows",
        ],
        capture_output=True,
        text=True,
        encoding="locale",
        check=True,
        shell=True,
    )
    return result.stdout.strip()


def cytrus_download(
    game: Game,
    release: Release,
    version: str,
    output_dir: str,
    log_prefix: str,
    on_progress: Callable[[str], None] | None = None,
) -> None:
    process = subprocess.Popen(
        [
            "cytrus-v6",
            "download",
            "--game",
            game,
            "--release",
            release,
            "--version",
            version,
            "--output",
            output_dir,
        ],
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="locale",
    )
    if process.stdout:
        for line in process.stdout:
            line = ANSI_ESCAPE.sub("", line).rstrip()
            if line:
                logger.debug(f"[{log_prefix}] {line}")
                if on_progress:
                    on_progress(line)
    process.wait()
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, "cytrus-v6")


def check_cytrus_installation(
    game: Game,
    release: Release,
    exe_path: str,
    release_json_path: str,
    log_prefix: str,
    on_progress: Callable[[str], None] | None = None,
) -> None:
    if not CYTRUS_INSTALLED:
        return logger.warning("Cytrus not installed, skipping auto update")
    latest_version = cytrus_get_latest_version(game, release)
    logger.info(f"[{log_prefix}] Latest cytrus version: {latest_version}")

    if not os.path.exists(release_json_path):
        raise FileNotFoundError(release_json_path)
    with open(release_json_path, encoding="utf-8") as file:
        text = file.read()
    release_model = ReleaseJson.model_validate_json(text)
    local_version = release_model.version or release_model.repositoryVersion
    if not local_version:
        raise ValueError(text)

    if local_version != latest_version:
        game_dir = os.path.dirname(exe_path)
        logger.info(
            f"[{log_prefix}] Version mismatch (local={local_version}, latest={latest_version}), downloading..."
        )
        if on_progress:
            on_progress(f"Update available ({local_version or 'none'} → {latest_version}), downloading...")
        cytrus_download(game, release, latest_version, game_dir, log_prefix, on_progress)
        logger.info(f"[{log_prefix}] Download complete.")
        release_model.version = latest_version
        os.makedirs(os.path.dirname(release_json_path), exist_ok=True)
        with open(release_json_path, "w", encoding="utf-8") as file:
            json.dump(release_model.model_dump(), file, indent=2)

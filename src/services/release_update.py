import hashlib
import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Annotated
from zipfile import BadZipFile, ZipFile

import psutil
import requests
from filelock import FileLock, Timeout
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from src.services.game_version import VERSION_PATH, GameVersion
from src.utils.project_paths import BUNDLE_ROOT, PROJECT_ROOT, USER_DATA_ROOT
from src.utils.runtime_support import RuntimeSetupError

RELEASE_URL = "https://api.github.com/repos/Valentin-alix/AstraBot/releases/latest"
SKIP_UPDATE_ENV = "THISISASTRABOT_SKIP_UPDATE_ONCE"
STATUS_PATH = USER_DATA_ROOT / "update-error.txt"
UPDATE_ROOT = PROJECT_ROOT / ".astrabot-update"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "AstraBot"}


class ReleaseAsset(BaseModel):
    name: str
    size: Annotated[int, Field(strict=True, gt=0)]
    browser_download_url: Annotated[
        str,
        Field(
            pattern=r"^https://github\.com/Valentin-alix/AstraBot/releases/download/",
        ),
    ]
    digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | None = None


class GitHubRelease(BaseModel):
    name: GameVersion
    assets: list[ReleaseAsset]


def download_archive(asset: ReleaseAsset, destination: Path, progress: Callable[[str], None]) -> None:
    if asset.digest is None:
        raise RuntimeSetupError("The release archive has no SHA-256 digest.")
    expected = asset.digest.removeprefix("sha256:")
    digest = hashlib.sha256()
    downloaded = 0
    with requests.get(asset.browser_download_url, headers=HEADERS, timeout=(5, 30), stream=True) as response:
        response.raise_for_status()
        with destination.open("wb") as output:
            for chunk in response.iter_content(1024 * 1024):
                downloaded += len(chunk)
                if downloaded > asset.size:
                    raise RuntimeSetupError("The release archive exceeds its announced size.")
                output.write(chunk)
                digest.update(chunk)
                progress(f"Downloading update… {downloaded * 100 // asset.size}%")
    if downloaded != asset.size or digest.hexdigest() != expected:
        raise RuntimeSetupError("The downloaded release archive is incomplete or corrupted.")


def extract_archive(
    archive: Path, destination: Path, expected_version: str, progress: Callable[[str], None]
) -> None:
    root = destination.resolve()
    with ZipFile(archive) as package:
        for name in package.namelist():
            if ":" in name or not (root / name).resolve().is_relative_to(root):
                raise RuntimeSetupError("The release archive contains an unsafe path.")
        entries = package.infolist()
        total_size = sum(entry.file_size for entry in entries)
        extracted = 0
        percentage = -1
        for entry in entries:
            package.extract(entry, root)
            extracted += entry.file_size
            current = extracted * 100 // max(total_size, 1)
            if current != percentage:
                progress(f"Preparing installation... {current}%")
                percentage = current
    bundle = root / "AstraBot"
    if not (bundle / "AstraBot.exe").is_file():
        raise RuntimeSetupError("The release archive has no executable.")
    if (bundle / "_internal/VERSION").read_text(encoding="utf-8").strip() != expected_version:
        raise RuntimeSetupError("The extracted bundle has an inconsistent Dofus version.")


def launch_installer() -> None:
    installer = UPDATE_ROOT / "AstraBot.exe"
    shutil.copyfile(PROJECT_ROOT / "AstraBot.exe", installer)
    runtime = UPDATE_ROOT / "_internal"
    runtime.mkdir()
    for path in BUNDLE_ROOT.iterdir():
        if path.is_file():
            shutil.copy2(path, runtime / path.name)
    shutil.copytree(BUNDLE_ROOT / "psutil", runtime / "psutil")
    subprocess.Popen(
        [str(installer)],
        env=os.environ | {"PYINSTALLER_RESET_ENVIRONMENT": "1", "THISISASTRABOT_INSTALL_UPDATE": "1"},
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def prepare_update(progress: Callable[[str], None]) -> bool:
    temporary: Path | None = None
    handed_off = False
    lock_path = PROJECT_ROOT / ".update.lock"
    try:
        with FileLock(lock_path, timeout=0):
            progress("Checking for updates…")
            local = TypeAdapter[str](GameVersion).validate_python(
                VERSION_PATH.read_text(encoding="utf-8").strip()
            )
            with requests.get(RELEASE_URL, headers=HEADERS, timeout=(5, 10)) as response:
                response.raise_for_status()
                release = GitHubRelease.model_validate_json(response.content)
            local_numbers = tuple(int(part) for part in local.replace("_", ".").split("."))
            remote_numbers = tuple(int(part) for part in release.name.replace("_", ".").split("."))
            if remote_numbers <= local_numbers:
                return False
            asset = next(
                (asset for asset in release.assets if asset.name == f"AstraBot-{release.name}.zip"), None
            )
            if asset is None:
                raise RuntimeSetupError("The release has no Windows archive.")
            recover_update_staging()
            UPDATE_ROOT.mkdir()
            temporary = UPDATE_ROOT
            archive = temporary / "release.zip"
            download_archive(asset, archive, progress)
            progress("Preparing installation…")
            extract_archive(archive, temporary / "extracted", release.name, progress)
            launch_installer()
            handed_off = True
            progress("Installing update and restarting…")
            return True
    except Timeout as error:
        raise RuntimeSetupError("An update is already in progress for this installation.") from error
    except (requests.RequestException, ValidationError, BadZipFile, UnicodeError, OSError) as error:
        raise RuntimeSetupError("Unable to update the bot. The current installation was kept.") from error
    finally:
        if temporary is not None and not handed_off:
            shutil.rmtree(temporary)


def recover_update_staging() -> None:
    root = UPDATE_ROOT.resolve()
    if root.parent != PROJECT_ROOT.resolve() or root.name != ".astrabot-update":
        raise RuntimeSetupError("The update staging directory is outside the installation.")
    if not root.exists():
        return
    installer = root / "AstraBot.exe"
    for process in psutil.process_iter(["exe"]):
        executable = process.info["exe"]
        if executable and Path(executable).resolve() == installer:
            raise RuntimeSetupError("An update is already in progress for this installation.")
    backup = root / "backup"
    if backup.exists() and any(backup.iterdir()):
        raise RuntimeSetupError(
            f"A previous update left installation backups in {backup}. "
            "Restore the installation before retrying the update."
        )
    shutil.rmtree(root)


def consume_update_status() -> str | None:
    try:
        message = STATUS_PATH.read_text(encoding="utf-8-sig")
        STATUS_PATH.unlink()
        return message
    except FileNotFoundError:
        return None


def clean_update_staging() -> None:
    updater_pid = os.environ.pop("THISISASTRABOT_UPDATER_PID", None)
    if updater_pid is None:
        return

    threading.Thread(target=_remove_update_staging, args=(int(updater_pid),), name="update-cleanup").start()


def _remove_update_staging(updater_pid: int) -> None:
    try:
        try:
            psutil.Process(updater_pid).wait(timeout=10)
        except psutil.NoSuchProcess:
            pass
        shutil.rmtree(UPDATE_ROOT)
    except (OSError, psutil.Error) as error:
        import logging

        logging.getLogger(__name__).warning("Unable to remove update staging: %s", error)

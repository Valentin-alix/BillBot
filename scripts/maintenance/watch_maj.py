import ctypes
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "AnkamaLauncherEmulator"))

import requests
from ankama_launcher_emulator.installation.cytrus import cytrus_download
from ankama_launcher_emulator.interfaces.ankama_release import ReleaseJson
from ankama_launcher_emulator.utils.environment import RELEASE_JSON_PATH
from pydantic import TypeAdapter, ValidationError

from DBDofusUnity.consts import OBF_GAME_DIR, require_game_toolchain
from src.services.game_version import CYTRUS_URL, VERSION_PATH, CytrusIndex, GameVersion

logger = logging.getLogger(__name__)


class ConfigurationError(RuntimeError):
    pass


def notify_update_completed(version: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"update-maj terminé pour la version {version}.\n\nVeuillez vérifier manuellement le résultat.",
            "AstraBot — Mise à jour terminée",
            0x40 | 0x10000 | 0x40000,
        )
    except OSError:
        logger.exception("Unable to display the update completion notification.")


def read_release() -> ReleaseJson:
    try:
        release = ReleaseJson.model_validate_json(Path(RELEASE_JSON_PATH).read_bytes())
        TypeAdapter[str](GameVersion).validate_python(release.version)
        if Path(release.location).resolve() != OBF_GAME_DIR.resolve():
            raise ValueError("release.json location must match OBF_GAME_DIR.")
        if not (Path(release.location) / "Dofus.exe").is_file():
            raise ValueError("Dofus.exe is missing; install the game with the Ankama launcher first.")
    except (OSError, ValueError) as error:
        raise ConfigurationError(f"Invalid Dofus installation: {error}") from error
    return release


def check_for_update() -> None:
    release = read_release()
    try:
        compatible = TypeAdapter[str](GameVersion).validate_python(
            VERSION_PATH.read_text(encoding="utf-8").strip()
        )
    except (OSError, ValueError) as error:
        raise ConfigurationError(f"Invalid bot VERSION file: {error}") from error

    with requests.get(CYTRUS_URL, timeout=10) as response:
        response.raise_for_status()
        latest = CytrusIndex.model_validate_json(response.content).games.dofus.platforms.windows.dofus3

    if release.version != latest:
        logger.info("Installing Dofus %s (installed: %s).", latest, release.version)
        cytrus_download("dofus", "dofus3", latest, release.location, "DOFUS3", logger.info)
        release.version = latest
        Path(RELEASE_JSON_PATH).write_text(release.model_dump_json(indent=2), encoding="utf-8")

    if compatible != latest:
        logger.info("Running update-maj for %s.", latest)
        subprocess.run([sys.executable, "-m", "scripts.maintenance.main", "update-maj"], cwd=ROOT, check=True)
        logger.info("update-maj completed for %s.", latest)
        Thread(target=notify_update_completed, args=(latest,), daemon=True).start()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        require_game_toolchain()
        if shutil.which("cytrus-v6") is None:
            raise ConfigurationError("Install cytrus-v6 and make it available on PATH.")
        logger.info("Watching Cytrus every 60s. Ctrl+C to stop.")
        while True:
            try:
                check_for_update()
            except requests.RequestException, ValidationError, OSError, subprocess.CalledProcessError:
                logger.exception("Update failed; retrying in 60s.")
            time.sleep(60)
    except (ConfigurationError, ValueError) as error:
        logger.error("Configuration error: %s", error)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Watcher stopped.")


if __name__ == "__main__":
    main()

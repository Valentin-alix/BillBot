import os
import subprocess
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory

from utils.env_config import get_optional_path

from ankama_launcher_emulator.consts import ASAR_PATH
from ankama_launcher_emulator.interfaces.ankama_release import (
    PackageJson,
    ReleaseJson,
)


@cache
def get_zaap_version() -> str:
    filename = "package.json"
    zaap_version = "none"
    app_asar_path = ASAR_PATH
    if app_asar_path is not None and app_asar_path.exists():
        with TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / filename
            subprocess.run(
                ["asar", "extract-file", str(app_asar_path), str(output_path)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=True,
            )
            if output_path.exists():
                package = PackageJson.model_validate_json(output_path.read_text(encoding="utf-8"))
                zaap_version = package.version

    if zaap_version == "none":
        zaap_version = "3.12.19"

    return zaap_version


def _get_zaap_data_path() -> str:
    configured_path = get_optional_path("ZAAP_PATH")
    if configured_path is not None:
        return str(configured_path)
    return os.path.join(os.environ["APPDATA"], "zaap")


def get_client_version() -> str:
    zaap_path = _get_zaap_data_path()
    release_json_path = os.path.join(
        zaap_path, "repositories", "production", "dofus", "dofus3", "release.json"
    )
    if os.path.exists(release_json_path):
        with open(release_json_path, encoding="utf-8") as file:
            release = ReleaseJson.model_validate_json(file.read())
        if release.version:
            return release.version
    raise FileNotFoundError(release_json_path)

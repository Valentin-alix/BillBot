from pathlib import Path
from zipfile import BadZipFile, ZipFile

import msgspec
from playwright.sync_api import Error, sync_playwright

from src.utils.project_paths import BUNDLE_ROOT, FRIDA_SCRIPT_PATH, IS_PACKAGED
from src.utils.runtime_support import RuntimeSetupError


def check_resource(path: Path) -> None:
    repair = "Extract the full release again." if IS_PACKAGED else "Run git lfs install, then git lfs pull."
    if not path.is_file():
        raise RuntimeSetupError(f"Ressource absente : {path.name}. {repair}")
    with path.open("rb") as file:
        if file.read(100).startswith(b"version https://git-lfs.github.com/spec/v1"):
            raise RuntimeSetupError(f"Git LFS pointer not downloaded: {path.name}. {repair}")


def validate_resources() -> None:
    bundles = BUNDLE_ROOT / "DBDofusUnity" / "datas" / "bundles"
    paths = [
        BUNDLE_ROOT / "resources" / "icons" / "logo.png",
        bundles / "data" / "SubAreasDataRoot.json",
        bundles / "i18n.json",
        bundles / "standalone" / "world-graph.json",
        bundles / "maps.zip",
        FRIDA_SCRIPT_PATH,
    ]
    paths.extend((bundles / "data").glob("*.json"))
    for path in dict.fromkeys(paths):
        check_resource(path)
        if path.suffix == ".json":
            try:
                msgspec.json.decode(path.read_bytes())
            except msgspec.DecodeError as error:
                raise RuntimeSetupError(
                    f"Invalid JSON resource: {path.name}. Restore the project data."
                ) from error
    try:
        with ZipFile(bundles / "maps.zip") as archive:
            entries = [
                name for name in archive.namelist() if name.startswith("map/map_") and name.endswith(".json")
            ]
            if not entries:
                raise RuntimeSetupError("The maps.zip archive contains no maps.")
            msgspec.json.decode(archive.read(entries[0]))
    except (BadZipFile, msgspec.DecodeError) as error:
        raise RuntimeSetupError("The maps.zip archive is unreadable. Restore the project data.") from error


def validate_browser() -> None:
    try:
        with sync_playwright() as playwright, playwright.chromium.launch(channel="chromium") as browser:
            page = browser.new_page()
            page.set_content("<title>Installation OK</title>")
            if page.title() != "Installation OK":
                raise RuntimeSetupError("The Chromium diagnostic did not complete.")
    except Error as error:
        raise RuntimeSetupError(browser_repair_message()) from error


def browser_repair_message() -> str:
    repair = "Extract the full release again." if IS_PACKAGED else "Run uv run playwright install chromium."
    return f"Playwright Chromium is missing or cannot start. {repair}"

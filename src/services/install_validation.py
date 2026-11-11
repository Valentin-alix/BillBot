import logging
import os
from importlib.metadata import version
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import BadZipFile, ZipFile

import msgspec
lazy import playwright as playwright_package
lazy from playwright.sync_api import sync_playwright

from src.utils.project_paths import BUNDLE_ROOT, FRIDA_SCRIPT_PATH, IS_PACKAGED, USER_DATA_ROOT
from src.utils.runtime_support import RuntimeSetupError

logger = logging.getLogger(__name__)
_DIAGNOSTIC_VERSION = 1


class ValidationCache(msgspec.Struct):
    version: int
    files: dict[str, tuple[int, int]]
    environment: dict[str, str]


def _cache_path(name: str) -> Path:
    return USER_DATA_ROOT / "resources" / f"startup-{name}.json"


def _read_cache(name: str) -> ValidationCache | None:
    try:
        cached = msgspec.json.decode(_cache_path(name).read_bytes(), type=ValidationCache)
    except OSError, msgspec.DecodeError:
        return None
    return cached if cached.version == _DIAGNOSTIC_VERSION else None


def _file_stamps(paths: list[Path]) -> dict[str, tuple[int, int]]:
    stamps: dict[str, tuple[int, int]] = {}
    for path in sorted(set(paths)):
        stat = path.stat()
        stamps[str(path)] = (stat.st_size, stat.st_mtime_ns)
    return stamps


def _write_cache(name: str, cached: ValidationCache) -> None:
    temporary: Path | None = None
    try:
        path = _cache_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(msgspec.json.encode(cached))
        temporary.replace(path)
        temporary = None
    except OSError:
        logger.warning("Unable to save the %s startup diagnostic cache", name)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning("Unable to remove a temporary startup diagnostic cache")


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
        BUNDLE_ROOT / "DBDofusUnity" / "datas" / "protos" / "game_mappings.json",
        FRIDA_SCRIPT_PATH,
    ]
    paths.extend((bundles / "data").glob("*.json"))
    paths = list(dict.fromkeys(paths))
    for path in paths:
        check_resource(path)
    stamps = _file_stamps(paths)
    cached = _read_cache("resources")
    if cached is not None and cached.files == stamps and cached.environment == {}:
        return
    for path in paths:
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
    _write_cache("resources", ValidationCache(_DIAGNOSTIC_VERSION, stamps, {}))


def validate_browser() -> None:
    environment = {
        "playwright": version("playwright"),
        "browser_path": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
        "local_app_data": os.environ.get("LOCALAPPDATA", ""),
        "bundle_root": str(BUNDLE_ROOT),
    }
    cached = _read_cache("browser")
    if cached is not None and cached.environment == environment and cached.files:
        try:
            if _file_stamps([Path(path) for path in cached.files]) == cached.files:
                return
        except OSError:
            pass

    try:
        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
            with playwright.chromium.launch(channel="chromium") as browser:
                page = browser.new_page()
                page.set_content("<title>Installation OK</title>")
                if page.title() != "Installation OK":
                    raise RuntimeSetupError("The Chromium diagnostic did not complete.")
    except Exception as error:
        raise RuntimeSetupError(browser_repair_message()) from error
    driver = Path(playwright_package.__file__).parent / "driver"
    paths = [
        executable,
        driver / "node.exe",
        driver / "package" / "cli.js",
        driver / "package" / "browsers.json",
    ]
    _write_cache("browser", ValidationCache(_DIAGNOSTIC_VERSION, _file_stamps(paths), environment))


def browser_repair_message() -> str:
    repair = "Extract the full release again." if IS_PACKAGED else "Run uv run playwright install chromium."
    return f"Playwright Chromium is missing or cannot start. {repair}"

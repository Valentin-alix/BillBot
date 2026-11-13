import sys
from pathlib import Path

IS_PACKAGED = getattr(sys, "frozen", False)
PROJECT_ROOT = Path(sys.executable).resolve().parent if IS_PACKAGED else Path(__file__).resolve().parents[2]
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
FRIDA_SCRIPT_PATH = BUNDLE_ROOT / "AnkamaLauncherEmulator/ankama_launcher_emulator/server/dofus3/script.js"
USER_DATA_ROOT = Path.home() / "AppData" / "Local" / "AstraBot" if IS_PACKAGED else PROJECT_ROOT
ENV_PATH = PROJECT_ROOT / ".env"


def ensure_packaged_runtime_data() -> None:
    (USER_DATA_ROOT / "resources").mkdir(parents=True, exist_ok=True)
    launcher_resources = USER_DATA_ROOT / "AnkamaLauncherEmulator" / "resources"
    launcher_resources.mkdir(parents=True, exist_ok=True)
    schedule_profiles_path = launcher_resources / "schedule_profiles.json"
    try:
        with schedule_profiles_path.open("x", encoding="utf-8") as file:
            file.write('{"profiles": {}}\n')
    except FileExistsError:
        pass

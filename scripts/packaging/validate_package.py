import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path
from zipfile import ZipFile

import psutil


def close_window(pid: int) -> None:
    try:
        owner_process = psutil.Process(pid)
        owners = {pid, *(child.pid for child in owner_process.children(recursive=True))}
    except psutil.NoSuchProcess:
        return
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

    def visit(hwnd: int, _: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in owners:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
        return True

    user32.EnumWindows(callback_type(visit), 0)


def validate_package(folder: Path) -> None:
    version = folder / "_internal/VERSION"
    if not version.is_file():
        raise RuntimeError("Packaged VERSION file is missing")
    with ZipFile(folder / "_internal/DBDofusUnity/datas/bundles/maps.zip") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Packaged maps.zip is corrupted")
    with tempfile.TemporaryDirectory(prefix="bot-package-") as temporary:
        root = Path(temporary)
        package = root / "release"
        shutil.copytree(folder, package)
        profile = root / "profile"
        environment = dict(os.environ)
        environment.update(
            USERPROFILE=str(profile),
            APPDATA=str(profile / "AppData/Roaming"),
            LOCALAPPDATA=str(profile / "AppData/Local"),
            DEBUG="1",
            PYTHON_DOTENV_DISABLED="1",
            PLAYWRIGHT_BROWSERS_PATH=str(root / "absent-browser-cache"),
            THISISASTRABOT_SKIP_UPDATE_ONCE="1",
        )
        for name in (
            "OBF_GAME_DIR",
            "NON_OBF_GAME_DIR",
            "PROTOC_PATH",
            "IDA_EXE",
            "OPENAI_API_KEY",
            "SONJI_API_KEY",
        ):
            environment.pop(name, None)
        driver = package / "_internal/playwright/driver"
        subprocess.run(
            [
                str(driver / "node.exe"),
                str(driver / "package/cli.js"),
                "screenshot",
                "--channel=chromium",
                "about:blank",
                str(root / "browser.png"),
            ],
            cwd=root,
            env=environment | {"PLAYWRIGHT_BROWSERS_PATH": "0"},
            check=True,
            timeout=60,
        )
        log_path = profile / "AppData/Local/AstraBot/resources/diagnostics.log"
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        process = subprocess.Popen(
            [str(package / "AstraBot.exe"), "--no-auto"],
            cwd=root,
            env=environment,
            startupinfo=startup,
        )
        try:
            deadline = time.monotonic() + 120
            log = ""
            while time.monotonic() < deadline:
                log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                if "The installed Dofus version is missing or unreadable." in log:
                    break
                if process.poll() is not None or "ERROR" in log:
                    raise RuntimeError(f"Packaged startup failed:\n{log}")
                time.sleep(0.2)
            else:
                raise TimeoutError(f"Packaged startup did not finish:\n{log}")
            deadline = time.monotonic() + 30
            while process.poll() is None:
                close_window(process.pid)
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Packaged error dialog did not close:\n{log}") from None
            if process.returncode != 1:
                raise RuntimeError("Packaged startup must refuse an installation without Dofus")
            if "Application ready." in log:
                raise RuntimeError("GUI started despite missing Dofus")
            print("PASS: bundled Chromium, VERSION file, missing-game startup refusal and exit code 1")
        finally:
            if process.poll() is None:
                try:
                    children = psutil.Process(process.pid).children(recursive=True)
                except psutil.NoSuchProcess:
                    children = []
                for child in children:
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.kill()
                process.wait()


if __name__ == "__main__":
    validate_package(Path(sys.argv[1]).resolve())

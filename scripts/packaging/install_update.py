import os
import subprocess
import sys
from pathlib import Path

import psutil
from filelock import FileLock


def install_update() -> None:
    temporary = Path(sys.executable).resolve().parent
    if temporary.name != ".astrabot-update":
        raise ValueError("The updater must run from the bundle's .astrabot-update directory.")
    target = temporary.parent
    source = temporary / "extracted" / "AstraBot"
    status = Path.home() / "AppData/Local/AstraBot/update-error.txt"
    backup = temporary / "backup"
    names = ("AstraBot.exe", "_internal")
    saved: list[str] = []
    installed: list[str] = []
    environment = os.environ | {
        "PYINSTALLER_RESET_ENVIRONMENT": "1",
        "THISISASTRABOT_UPDATER_PID": str(os.getpid()),
        "THISISASTRABOT_SKIP_UPDATE_ONCE": "1",
    }
    try:
        owner = psutil.Process().parent()
        if owner and Path(owner.exe()).resolve() == target / "AstraBot.exe":
            owner.wait(timeout=120)
    except psutil.NoSuchProcess:
        pass
    with FileLock(target / ".update.lock", timeout=120):
        try:
            backup.mkdir()
            for name in names:
                (target / name).rename(backup / name)
                saved.append(name)
            for name in reversed(names):
                (source / name).rename(target / name)
                installed.append(name)
        except OSError as error:
            recovery_errors: list[str] = []
            for name in installed:
                try:
                    (target / name).rename(source / name)
                except OSError as recovery_error:
                    recovery_errors.append(f"{name}: {recovery_error}")
            for name in saved:
                try:
                    (backup / name).rename(target / name)
                except OSError as recovery_error:
                    recovery_errors.append(f"{name}: {recovery_error}")
            status.parent.mkdir(parents=True, exist_ok=True)
            recovery_status = (
                f"Restoration is incomplete. Backups were kept in {backup}.\n" + "\n".join(recovery_errors)
                if recovery_errors
                else "The previous bundle was restored."
            )
            status.write_text(f"The update failed: {error}\n{recovery_status}", encoding="utf-8")
            if recovery_errors:
                return
    subprocess.Popen(
        [str(target / "AstraBot.exe")],
        cwd=target,
        env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def main() -> None:
    try:
        install_update()
    except (OSError, ValueError, psutil.Error) as error:
        status = Path.home() / "AppData/Local/AstraBot/update-error.txt"
        status.parent.mkdir(parents=True, exist_ok=True)
        status.write_text(f"Unable to complete the update: {error}", encoding="utf-8")


if __name__ == "__main__":
    main()

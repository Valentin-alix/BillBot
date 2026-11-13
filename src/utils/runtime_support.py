import ctypes
import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

from src.utils.project_paths import IS_PACKAGED, USER_DATA_ROOT


class RuntimeSetupError(RuntimeError):
    pass


def error_message(error: object) -> str:
    if isinstance(error, RuntimeSetupError):
        return str(error)
    if isinstance(error, ImportError):
        return "A Python dependency is missing or cannot be loaded. Reinstall the release or run uv sync."
    if isinstance(error, OSError):
        filename = Path(error.filename).name if error.filename else "ressource locale"
        return f"Unable to access {filename}. Check that it exists and that you have access rights."
    return f"The operation failed ({type(error).__name__}). Check the diagnostic log."


class DiagnosticFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.exc_text = None
        return super().format(record)

    def formatException(
        self, ei: tuple[type[BaseException], BaseException, TracebackType | None] | tuple[None, None, None]
    ) -> str:
        # Exception values can contain credentials or entire validation inputs.
        error = ei[1]
        if error is None:
            return ""
        frames = traceback.extract_tb(error.__traceback__)
        return "".join(
            f"  {frame.filename}:{frame.lineno} in {frame.name}\n" for frame in frames
        ) + error_message(error)


def configure_diagnostics() -> Path:
    path = USER_DATA_ROOT / "resources" / "diagnostics.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = DiagnosticFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(formatter)
    root.addHandler(handler)
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
    return path


def report_fatal(error: BaseException, *, dialog: bool = True) -> None:
    message = error_message(error)
    logging.getLogger(__name__).error("Unable to start: %s", message, exc_info=error)
    if dialog and sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, "AstraBot — Error", 0x10)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


def check_platform() -> None:
    if sys.platform != "win32":
        raise RuntimeSetupError("This program requires Windows.")
    if not os.environ.get("APPDATA"):
        raise RuntimeSetupError("The Windows APPDATA variable is missing. Check your Windows user profile.")


def configure_browser_path() -> None:
    if IS_PACKAGED:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"

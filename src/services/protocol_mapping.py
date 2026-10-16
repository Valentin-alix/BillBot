import importlib.util
import logging
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import Any, Protocol, cast

from src.utils.project_paths import BUNDLE_ROOT

ARTIFACT = BUNDLE_ROOT / "resources/mapping/_billbot_protocol.cp312-win_amd64.pyd"


class NativeTranslator(Protocol):
    def decode(self, payload: bytes) -> tuple[str, bytes, str, int]: ...
    def encode(self, message_type: str, payload: bytes, root_type: str, uid: int = -1) -> bytes: ...
    def inspect_envelope(self, payload: bytes) -> dict[str, Any]: ...
    def info(self) -> dict[str, Any]: ...


_translator: NativeTranslator | None = None
_lock = Lock()
logger = logging.getLogger(__name__)


def load_translator(artifact: Path) -> NativeTranslator:
    specification = importlib.util.spec_from_file_location("_billbot_protocol", artifact.resolve())
    if specification is None or specification.loader is None:
        raise ImportError("Cannot load the native translator")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return cast(NativeTranslator, module)


def initialize_mapping(progress: Callable[[str], None] = lambda status: None) -> NativeTranslator:
    global _translator
    with _lock:
        if _translator is None:
            progress("Loading protocol mapping...")
            _translator = load_translator(ARTIFACT)
            logger.info("Native protocol loaded for Dofus %s", _translator.info()["game_version"])
        return _translator


def get_translator() -> NativeTranslator:
    return initialize_mapping()

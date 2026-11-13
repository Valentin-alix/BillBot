import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.services import game_version, install_validation


def test_entrypoint_defers_validation_to_the_gui_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location("version_entry", Path(__file__).parents[1] / "__main__.py")
    assert spec is not None and spec.loader is not None
    entry = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, entry)
    spec.loader.exec_module(entry)
    gui = Mock(return_value=0)
    monkeypatch.setattr(entry, "check_platform", Mock())
    monkeypatch.setattr(entry, "start_gui", gui)
    assert entry.main(["bot", "--no-auto"]) == 0
    assert gui.call_args.args[:2] == (["bot"], False)
    assert gui.call_args.args[2] is entry.validate_startup
    actions = Mock()
    monkeypatch.setattr(game_version, "validate_game_version", actions.validate_game_version)
    monkeypatch.setattr(install_validation, "validate_resources", actions.validate_resources)
    monkeypatch.setattr(install_validation, "validate_browser", actions.validate_browser)
    monkeypatch.setattr(entry, "ensure_packaged_runtime_data", actions.ensure_packaged_runtime_data)
    gui.call_args.args[2](Mock())
    assert [call[0] for call in actions.mock_calls] == [
        "validate_game_version",
        "validate_resources",
        "validate_browser",
        "ensure_packaged_runtime_data",
    ]

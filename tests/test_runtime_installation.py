import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, call, patch
from zipfile import ZipFile

import pytest
from ankama_launcher_emulator.server import server as server_module
from ankama_launcher_emulator.server.dofus3 import launch
from ankama_launcher_emulator.utils import environment
from pydantic import BaseModel

from src.utils import project_paths
from src.utils import runtime_support
from src.core.bot.lifecycle.account_scheduler import AccountScheduler
from src.services import install_validation
from src.services.background import Worker
from utils.env_config import get_bool_from_env
from utils.local_json import read_local_model


@pytest.mark.parametrize("platform", ["linux"])
@pytest.mark.parametrize("package", ["src", "DBDofusUnity", "ankama_launcher_emulator"])
def test_packages_reject_non_windows_before_loading_dependencies(platform: str, package: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, 'AnkamaLauncherEmulator'); "
            "sys.platform = sys.argv[1]; __import__(sys.argv[2])",
            platform,
            package,
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "RuntimeError: This program requires Windows." in result.stderr


def test_non_windows_entrypoint_does_not_load_application(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location(
        "installation_entry", Path(__file__).parents[1] / "__main__.py"
    )
    assert spec is not None and spec.loader is not None
    entry = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, entry)
    spec.loader.exec_module(entry)
    monkeypatch.setattr(sys, "platform", "linux")
    with (
        patch.object(entry, "configure_diagnostics") as diagnostics,
        patch.object(entry, "report_fatal") as report,
    ):
        assert entry.main(["bot"]) == 1
    diagnostics.assert_not_called()
    assert "Windows" in str(report.call_args.args[0])


def test_runtime_data_initialization_preserves_existing_invalid_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(project_paths, "USER_DATA_ROOT", tmp_path)
    project_paths.ensure_packaged_runtime_data()
    path = tmp_path / "AnkamaLauncherEmulator/resources/schedule_profiles.json"
    assert path.read_text().strip() == '{"profiles": {}}'
    path.write_text("broken")
    project_paths.ensure_packaged_runtime_data()
    assert path.read_text() == "broken"


def test_invalid_json_reports_filename_without_private_input(tmp_path: Path) -> None:
    class Configuration(BaseModel):
        enabled: bool

    path = tmp_path / "settings.json"
    path.write_text('{"enabled": "private-secret"}')
    with pytest.raises(runtime_support.RuntimeSetupError) as failure:
        read_local_model(path, Configuration)
    assert "settings.json" in str(failure.value)
    assert "private-secret" not in str(failure.value)
    assert "private-secret" in path.read_text()


def test_debug_environment_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEBUG", raising=False)
    assert get_bool_from_env("DEBUG") is False
    monkeypatch.setenv("DEBUG", "0")
    assert get_bool_from_env("DEBUG") is False
    monkeypatch.setenv("DEBUG", "true")
    assert get_bool_from_env("DEBUG") is True
    monkeypatch.setenv("DEBUG", "invalid")
    with pytest.raises(runtime_support.RuntimeSetupError):
        get_bool_from_env("DEBUG")


def test_worker_logs_failure_and_finishes_without_error_subscriber(caplog: pytest.LogCaptureFixture) -> None:
    def fail(_: object) -> None:
        raise OSError("private-secret")

    worker = Worker(fail)
    finished = Mock()
    worker.finished.connect(finished)
    worker.run()
    finished.assert_called_once()
    record = next(record for record in caplog.records if record.name == "src.services.background")
    rendered = runtime_support.DiagnosticFormatter().format(record)
    assert "Task interrupted" in rendered
    assert "private-secret" not in rendered


def test_unexpected_worker_error_propagates() -> None:
    def fail(_: object) -> None:
        raise AssertionError("broken invariant")

    worker = Worker(fail)
    errors = Mock()
    finished = Mock()
    worker.error.connect(errors)
    worker.finished.connect(finished)
    with pytest.raises(AssertionError, match="broken invariant"):
        worker.run()
    errors.assert_not_called()
    finished.assert_called_once()


def test_launcher_kills_port_owner_and_waits_before_listening() -> None:
    owner = Mock(pid=123, status=server_module.CONN_LISTEN, laddr=Mock(port=server_module.LAUNCHER_PORT))
    unrelated = Mock(pid=456, status=server_module.CONN_LISTEN, laddr=Mock(port=80))
    actions = Mock()
    with (
        patch.object(server_module, "net_connections", return_value=[owner, owner, unrelated]),
        patch.object(server_module, "Process", return_value=actions.process) as process,
        patch.object(server_module, "_BoundServerSocket", return_value=actions.transport),
        patch.object(server_module, "Thread"),
    ):
        server_module.AnkamaLauncherServer(Mock()).start()
    process.assert_called_once_with(123)
    assert actions.mock_calls == [call.process.kill(), call.process.wait(timeout=5), call.transport.listen()]


def test_launcher_bind_failure_propagates_without_starting_thread() -> None:
    with (
        patch.object(server_module, "net_connections", return_value=[]),
        patch.object(server_module, "_BoundServerSocket") as transport,
        patch.object(server_module, "Thread") as thread,
    ):
        transport.return_value.listen.side_effect = OSError("bind failed")
        with pytest.raises(OSError, match="bind failed"):
            server_module.AnkamaLauncherServer(Mock()).start()
    thread.assert_not_called()


def test_scheduler_configuration_failure_is_visible_and_stops() -> None:
    scheduler = AccountScheduler(Mock(), Mock())
    with (
        patch.object(scheduler, "_next_operation", side_effect=OSError("private-secret")),
        patch("src.core.bot.lifecycle.account_scheduler.UserActivityService") as activity,
    ):
        scheduler._loop()
    assert scheduler._stop_event.is_set()
    activity.return_value.record.assert_called_once()
    assert "Automation stopped" in activity.return_value.record.call_args.args[1]
    assert "private-secret" not in activity.return_value.record.call_args.args[1]


def test_unexpected_scheduler_error_propagates() -> None:
    scheduler = AccountScheduler(Mock(), Mock())
    with (
        patch.object(scheduler, "_next_operation", side_effect=AssertionError("broken invariant")),
        patch("src.core.bot.lifecycle.account_scheduler.UserActivityService") as activity,
        pytest.raises(AssertionError, match="broken invariant"),
    ):
        scheduler._loop()
    activity.assert_not_called()
    assert not scheduler._stop_event.is_set()


def test_dofus_missing_or_corrupt_metadata_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "release.json"
    monkeypatch.setattr(environment, "RELEASE_JSON_PATH", str(path))
    with pytest.raises(runtime_support.RuntimeSetupError, match="install"):
        environment.resolve_dofus_path()
    path.write_text("private-secret")
    with pytest.raises(runtime_support.RuntimeSetupError, match="invalid"):
        environment.resolve_dofus_path()


def test_frida_failure_kills_spawned_process() -> None:
    device = Mock()
    device.spawn.return_value = 123
    with (
        patch.object(launch, "resolve_dofus_path", return_value="Dofus.exe"),
        patch.object(launch.frida, "get_local_device", return_value=device),
        patch.object(launch, "load_frida_script", side_effect=RuntimeError("injection failed")),
        patch.object(launch, "check_resource"),
    ):
        with pytest.raises(RuntimeError, match="injection failed"):
            launch.launch_dofus_exe(1, "synthetic", 9999)
    device.kill.assert_called_once_with(123)


def test_missing_hook_does_not_spawn_game() -> None:
    with (
        patch.object(launch, "resolve_dofus_path", return_value="Dofus.exe"),
        patch.object(launch, "check_resource", side_effect=runtime_support.RuntimeSetupError("hook absent")),
        patch.object(launch.frida, "get_local_device") as device,
    ):
        with pytest.raises(runtime_support.RuntimeSetupError):
            launch.launch_dofus_exe(1, "synthetic", 9999)
    device.assert_not_called()


def test_launch_reads_hook_from_bundle_with_frozen_module_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook = tmp_path / "AnkamaLauncherEmulator/ankama_launcher_emulator/server/dofus3/script.js"
    hook.parent.mkdir(parents=True)
    hook.write_text("// bundled hook", encoding="utf-8")
    monkeypatch.setattr(launch, "FRIDA_SCRIPT_PATH", hook)
    monkeypatch.setattr(
        launch, "__file__", str(tmp_path / "ankama_launcher_emulator/server/dofus3/launch.py")
    )
    device = Mock()
    device.spawn.return_value = 123
    with (
        patch.object(launch, "resolve_dofus_path", return_value="Dofus.exe"),
        patch.object(launch.frida, "get_local_device", return_value=device),
    ):
        assert launch.launch_dofus_exe(1, "synthetic", 9999) == 123
    device.attach.return_value.create_script.assert_called_once_with("// bundled hook")
    device.attach.return_value.create_script.return_value.load.assert_called_once()
    device.resume.assert_called_once_with(123)
    device.kill.assert_not_called()


def test_resource_validation_identifies_missing_and_lfs(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    with pytest.raises(runtime_support.RuntimeSetupError, match="absente"):
        install_validation.check_resource(path)
    path.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:synthetic")
    with pytest.raises(runtime_support.RuntimeSetupError, match="Git LFS"):
        install_validation.check_resource(path)


def test_json_and_map_archive_are_validated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_validation, "BUNDLE_ROOT", tmp_path)
    monkeypatch.setattr(install_validation, "USER_DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        install_validation,
        "FRIDA_SCRIPT_PATH",
        tmp_path / "AnkamaLauncherEmulator/ankama_launcher_emulator/server/dofus3/script.js",
    )
    bundles = tmp_path / "DBDofusUnity/datas/bundles"
    files = [
        "resources/icons/logo.png",
        "DBDofusUnity/datas/bundles/data/SubAreasDataRoot.json",
        "DBDofusUnity/datas/bundles/i18n.json",
        "DBDofusUnity/datas/bundles/standalone/world-graph.json",
        "DBDofusUnity/datas/protos/game_mappings.json",
        "AnkamaLauncherEmulator/ankama_launcher_emulator/server/dofus3/script.js",
    ]
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    with ZipFile(bundles / "maps.zip", "w") as archive:
        archive.writestr("map/map_1.json", "{}")
    install_validation.validate_resources()
    (bundles / "i18n.json").write_text("broken")
    with pytest.raises(runtime_support.RuntimeSetupError, match="i18n.json"):
        install_validation.validate_resources()
    (bundles / "i18n.json").write_text("{}")
    (bundles / "maps.zip").write_text("broken")
    with pytest.raises(runtime_support.RuntimeSetupError, match="maps.zip"):
        install_validation.validate_resources()

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING
from collections.abc import Callable

if (
    __name__ == "__main__"
    and getattr(sys, "frozen", False)
    and os.environ.pop("THISISBILLBOT_INSTALL_UPDATE", None)
):
    from scripts.packaging.install_update import main as install_update

    install_update()
    raise SystemExit(0)

from src.utils.project_paths import BUNDLE_ROOT

if TYPE_CHECKING:
    from src.gui.application import Application

if not getattr(sys, "frozen", False):
    for import_root in (BUNDLE_ROOT, BUNDLE_ROOT / "DBDofusUnity", BUNDLE_ROOT / "AnkamaLauncherEmulator"):
        sys.path.insert(0, str(import_root))

from src.utils.project_paths import ENV_PATH, ensure_packaged_runtime_data
from src.utils.runtime_support import (
    RuntimeSetupError,
    check_platform,
    configure_browser_path,
    configure_diagnostics,
    report_fatal,
)

if __name__ == "__main__":
    try:
        check_platform()
        configure_diagnostics()
    except (RuntimeSetupError, OSError) as error:
        report_fatal(error)
        raise SystemExit(1)


configure_browser_path()

from dotenv import load_dotenv

load_dotenv(ENV_PATH)

def run_gui(
    application_argv: list[str],
    enable_automatic_schedules: bool,
    application: Application | None = None,
) -> int:
    from src.runtime import run_gui as start_gui

    return start_gui(application_argv, enable_automatic_schedules, application)


@dataclass(frozen=True)
class RuntimeArgs:
    use_bot_config_json: bool
    enable_automatic_schedules: bool
    application_argv: list[str]


def parse_runtime_args(argv: list[str]) -> RuntimeArgs:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--auto",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use resources/bots.json to configure bots at runtime.",
    )
    runtime_args, remaining_args = parser.parse_known_args(argv[1:])
    return RuntimeArgs(
        use_bot_config_json=runtime_args.auto,
        enable_automatic_schedules=runtime_args.auto,
        application_argv=[argv[0], *remaining_args],
    )


def validate_startup(progress: Callable[[str], None]) -> None:
    from src.services.game_version import validate_game_version
    from src.services.install_validation import validate_browser, validate_resources
    from src.services.protocol_mapping import initialize_mapping

    progress("Checking Dofus version...")
    validate_game_version()
    progress("Checking resources...")
    validate_resources()
    progress("Checking browser...")
    validate_browser()
    ensure_packaged_runtime_data()
    initialize_mapping(progress)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    application = None
    startup_error: RuntimeSetupError | OSError | None = None
    try:
        check_platform()
        runtime_args = parse_runtime_args(argv)
        if getattr(sys, "frozen", False):
            from src.gui.application import Application

            application = Application(runtime_args.application_argv)
            application.show_startup_status("Checking for updates...")
            from src.gui.update_dialog import check_release_update, run_startup_task

            if check_release_update(application):
                return 0
            run_startup_task(application, validate_startup)
            application.show_startup_status("Loading interface...")
        else:
            validate_startup(lambda status: None)
        from src.controller.bot_config import BotConfigService

        BotConfigService.use_bot_config_json = runtime_args.use_bot_config_json
        return run_gui(runtime_args.application_argv, runtime_args.enable_automatic_schedules, application)
    except (RuntimeSetupError, OSError) as error:
        startup_error = error
    finally:
        if application is not None:
            application.startup_window.close()
    if startup_error is not None:
        report_fatal(startup_error)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

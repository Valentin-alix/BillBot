from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

if (
    __name__ == "__main__"
    and getattr(sys, "frozen", False)
    and os.environ.pop("THISISASTRABOT_INSTALL_UPDATE", None)
):
    from scripts.packaging.install_update import main as install_update

    install_update()
    raise SystemExit(0)

from src.utils.project_paths import BUNDLE_ROOT
lazy import src.services.game_version as game_version
lazy import src.services.install_validation as install_validation
lazy from src.controller.bot_config import BotConfigService
lazy from src.runtime import run_gui as start_gui

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
    progress("Checking Dofus version...")
    game_version.validate_game_version()
    progress("Checking resources...")
    install_validation.validate_resources()
    progress("Checking browser...")
    install_validation.validate_browser()
    ensure_packaged_runtime_data()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    try:
        check_platform()
        runtime_args = parse_runtime_args(argv)
        BotConfigService.use_bot_config_json = runtime_args.use_bot_config_json
        return start_gui(
            runtime_args.application_argv, runtime_args.enable_automatic_schedules, validate_startup
        )
    except (RuntimeSetupError, OSError) as error:
        report_fatal(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

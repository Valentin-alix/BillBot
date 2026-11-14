import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from DBDofusUnity.consts import (
    IDA_EXE,
    NON_OBF_GAME_ASSEMBLY_DLL_I64,
    NON_OBFUSCATED_DATA_DIR,
    OBF_GAME_ASSEMBLY_DLL_I64,
    OBFUSCATED_DATA_DIR,
    require_game_toolchain,
)
from scripts.mapping.ida_tracer.progress.reporter import start_progress_watcher

IDA_SCRIPT = Path(__file__).parent / "trace_proto_fields.py"


def _select_ida_database(i64_path: Path) -> Path:
    if i64_path.exists():
        return i64_path
    dll_path = i64_path.with_name("GameAssembly.dll")
    if dll_path.exists():
        return dll_path
    error_message = f"{i64_path} ida base not found and {dll_path} dll not found"
    raise FileNotFoundError(error_message)


def _build_argument_parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--non-obf", action="store_true")
    argument_parser.add_argument("--ida-exe", type=Path, default=IDA_EXE)
    argument_parser.add_argument(
        "--obf-dir",
        type=Path,
        help="Use this obfuscated build folder for GameAssembly.dll, global-metadata.dat, and generated outputs.",
    )
    return argument_parser


def run_ida_script(
    *,
    use_non_obf: bool,
    ida_exe: Path = IDA_EXE,
    script_path: Path = IDA_SCRIPT,
    base_dir: Path | None = None,
    database_path: Path | None = None,
) -> None:
    environment = dict(os.environ)
    with tempfile.NamedTemporaryFile(delete=False) as progress_temp_file:
        progress_path = Path(progress_temp_file.name)
    environment["PROTO_TRACER_PROGRESS_PATH"] = str(progress_path)
    if use_non_obf:
        selected_base_dir = base_dir or NON_OBFUSCATED_DATA_DIR
        selected_database = _select_ida_database(database_path or NON_OBF_GAME_ASSEMBLY_DLL_I64)
    else:
        selected_base_dir = base_dir or OBFUSCATED_DATA_DIR
        selected_database = _select_ida_database(database_path or OBF_GAME_ASSEMBLY_DLL_I64)

    environment["PROTO_TRACER_BASE_DIR"] = str(selected_base_dir)
    database = str(selected_database)

    stop_progress_event, progress_thread = start_progress_watcher(progress_path)
    try:
        process = subprocess.Popen(
            [str(ida_exe), "-A", f"-S{script_path!s}", database],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="locale",
            env=environment,
        )
        if process.stdout:
            for line in process.stdout:
                print(line, end="")

        return_code = process.wait()
        if return_code != 0:
            raise SystemExit(return_code)
    finally:
        stop_progress_event.set()
        progress_thread.join()
        progress_path.unlink(missing_ok=True)


def main() -> None:
    parser = _build_argument_parser()
    arguments = parser.parse_args()
    if arguments.obf_dir is not None and arguments.non_obf:
        parser.error("--obf-dir requires obfuscated dump mode")
    require_game_toolchain()
    if arguments.obf_dir is not None:
        run_ida_script(
            use_non_obf=arguments.non_obf,
            ida_exe=arguments.ida_exe,
            base_dir=arguments.obf_dir,
            database_path=arguments.obf_dir / "GameAssembly.dll.i64",
        )
    else:
        run_ida_script(use_non_obf=arguments.non_obf, ida_exe=arguments.ida_exe)


if __name__ == "__main__":
    main()

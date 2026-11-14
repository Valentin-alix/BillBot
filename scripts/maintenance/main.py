import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "AnkamaLauncherEmulator"))

from ankama_launcher_emulator.interfaces.ankama_release import ReleaseJson
from ankama_launcher_emulator.utils.environment import RELEASE_JSON_PATH
from filelock import FileLock
from pydantic import TypeAdapter

from DBDofusUnity.consts import OBF_GAME_DIR
from scripts.mapping.dump import (
    _build_proto_dump_target,
    gen_python_from_protoc,
    update_protos,
)
from scripts.mapping.export_signature_overrides import (
    run_export_signature_overrides,
)
from scripts.mapping.ida_tracer.main import run_ida_script
from scripts.mapping.synchronize_protos import (
    synchronize_non_obf_mapping_artifacts,
)
from src.services.game_version import VERSION_PATH, GameVersion


def gen_python(_):
    target = _build_proto_dump_target(use_obf=False)
    if target.proto_output.is_file():
        python_output_folder = target.proto_output.parent
    else:
        python_output_folder = target.proto_output
    gen_python_from_protoc(target.proto_output, python_output_folder)


def _synchronize_protos(arguments: argparse.Namespace) -> None:
    # Import pipeline after gen_python: stale protobuf descriptors would reject regenerated modules.
    gen_python(arguments)
    synchronize_non_obf_mapping_artifacts()
    run_export_signature_overrides()

    from DBDofusUnity.proto_mapper_assembly.pipeline import run_pipeline

    run_pipeline(do_load_pinned_pair=True)


def _read_update_game_version() -> str:
    release = ReleaseJson.model_validate_json(Path(RELEASE_JSON_PATH).read_bytes())
    if Path(release.location).resolve() != OBF_GAME_DIR.resolve():
        raise RuntimeError("The launcher release.json location does not match OBF_GAME_DIR.")
    return TypeAdapter[str](GameVersion).validate_python(release.version)


def new_maj_update_command(_: argparse.Namespace) -> None:
    from DBDofusUnity.dofus_unity_reader.get_datas import update_all_datas

    with FileLock(ROOT / ".update-maj.lock"):
        version = _read_update_game_version()
        update_all_datas()
        update_protos(use_obf=True)
        if _read_update_game_version() != version:
            raise RuntimeError("The installed Dofus version changed during update-maj. Run update-maj again.")
        VERSION_PATH.write_text(f"{version}\n", encoding="utf-8")
        print(f"Bot-compatible VERSION updated to {version}")


def run_ida_script_command(arguments: argparse.Namespace):
    target = _build_proto_dump_target(use_obf=not arguments.non_obf)
    run_ida_script(
        use_non_obf=arguments.non_obf, base_dir=target.output_folder, database_path=target.ida_database
    )


def _run_pipeline_command(arguments: argparse.Namespace) -> None:
    from DBDofusUnity.proto_mapper_assembly.pipeline import run_pipeline

    run_pipeline(do_load_pinned_pair=not arguments.no_pinned, obf_dir=arguments.obf_dir)


def build_argument_parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description="DBDofusUnity maintenance commands.")
    subparsers = argument_parser.add_subparsers(dest="command", required=False)

    pipeline_parser = subparsers.add_parser("run-pipeline", help="Run the proto mapping pipeline.")
    pipeline_parser.add_argument(
        "--no-pinned", action="store_true", default=False, help="Update obfuscated protocol artifacts."
    )
    pipeline_parser.add_argument(
        "--obf-dir",
        type=Path,
        help="Use this obfuscated dump folder for obf inputs, pinned data, overrides, and game mappings.",
    )
    pipeline_parser.set_defaults(handler=_run_pipeline_command)

    synchronize_protos_parser = subparsers.add_parser(
        "synchronize-protos",
        help="Regenerate every non-obfuscated protobuf artifact and run the mapping pipeline.",
    )
    synchronize_protos_parser.set_defaults(handler=_synchronize_protos)

    on_new_maj_parser = subparsers.add_parser(
        "update-maj", help="Execute at a new dofus maj <!> it reset pinned_pairs & instancied_msg_info.json"
    )
    on_new_maj_parser.set_defaults(handler=new_maj_update_command)

    ida_parser = subparsers.add_parser("ida", help="Run ida pipeline")
    ida_parser.add_argument("--non-obf", action="store_true", default=False)
    ida_parser.set_defaults(handler=run_ida_script_command)

    return argument_parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_argument_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        arguments = parser.parse_args(["run-pipeline"])
    arguments.handler(arguments)


if __name__ == "__main__":
    main()

import argparse
import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from DBDofusUnity.consts import (
    DUMP_CS_MARKER_NAME,
    GAME_ASSEMBLY_MARKER_NAME,
    GAME_MAPPINGS_JSON_FILE,
    IL2CPP_INSPECTOR_EXECUTABLE,
    NON_OBF_GAME_ASSEMBLY_DLL,
    NON_OBF_IL2CPP_METADATA_FILE,
    NON_OBF_PROTO_OUTPUT,
    NON_OBFUSCATED_DATA_DIR,
    OBF_GAME_ASSEMBLY_DLL,
    OBF_GAME_SNAPSHOTS_DIR,
    OBF_IL2CPP_METADATA_FILE,
    OBF_PROTO_OUTPUT,
    OBFUSCATED_DATA_DIR,
    PINNED_PAIRS_FILE,
    PROTOC_PATH,
    PROTOCOL_GAME_DUMP_CS_RELATIVE_PATH,
    PROTODEC_EXECUTABLE,
    RUNTIME_DATA_FILE,
    require_game_toolchain,
)
from DBDofusUnity.proto_mapper_assembly.controllers.pinned_pairs import write_pinned_pairs
from DBDofusUnity.proto_mapper_assembly.helpers.obf_game_snapshot import (
    find_snapshot_dir_by_game_assembly_mtime_ns,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.pinned_pairs import PinnedPairsConfig
from DBDofusUnity.proto_mapper_assembly.pipeline import run_pipeline
from scripts.mapping.ida_tracer.main import run_ida_script


@dataclass(frozen=True)
class ProtoDumpTarget:
    output_folder: Path
    game_assembly: Path
    metadata: Path
    proto_output: Path
    dump_cs: Path
    include_ida_trace: bool = True
    ida_database: Path | None = None


def main() -> None:
    argument_parser = build_argument_parser()
    arguments = argument_parser.parse_args()
    if arguments.non_obf and arguments.obf_dir is not None:
        argument_parser.error("--obf-dir cannot be used with --non-obf")

    use_obf = not arguments.non_obf

    require_game_toolchain()
    update_protos(use_obf=use_obf, obf_dir=arguments.obf_dir, force=arguments.force)


def build_argument_parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description="Refresh dump/proto artifacts for one game build.")
    group = argument_parser.add_mutually_exclusive_group()
    group.add_argument("--obf", action="store_true", help="Process the obfuscated build.")
    group.add_argument("--non-obf", action="store_true", help="Process the non-obfuscated build.")
    argument_parser.add_argument(
        "--obf-dir",
        type=Path,
        help="Use this obfuscated build folder for GameAssembly.dll, global-metadata.dat, and generated outputs.",
    )
    argument_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help=(
            "Re-dump even when the build was already dumped. Needed to refresh an archived build "
            "whose artifacts predate a tracer change, since its marker still matches its assembly."
        ),
    )
    return argument_parser


def check_updated_mapping_resources():
    require_game_toolchain()
    target = _build_proto_dump_target(use_obf=True)
    if does_dump_cs_changed_since_last_run(target):
        raise RuntimeError(
            "Proto resources are not updated, pls launch uv run python -m scripts.maintenance.main update-maj"
        )


def update_protos(*, use_obf: bool, obf_dir: Path | None = None, force: bool = False) -> None:
    target = _build_proto_dump_target(use_obf=use_obf, obf_dir=obf_dir)
    if force or is_game_assembly_changed_since_last_run(target):
        if use_obf and obf_dir is None:
            _archive_previous_obf_dump(target)
        run_il2cpp_inspector(target.game_assembly, target.metadata, target.output_folder)
        run_protodec(target.output_folder / "dll" / "Ankama.Dofus.Protocol.Game.dll", target.proto_output)

        if use_obf:
            if not force and not does_dump_cs_changed_since_last_run(target):
                _record_game_assembly_mtime(target)
                return

        if target.proto_output.is_file():
            python_output_folder = target.proto_output.parent
        else:
            python_output_folder = target.proto_output

        gen_python_from_protoc(target.proto_output, python_output_folder)

        if target.include_ida_trace:
            print("running ida script...")
            run_ida_script(
                use_non_obf=not use_obf, base_dir=target.output_folder, database_path=target.ida_database
            )

        if use_obf and obf_dir is not None:
            _record_dump_cs_hash(target)
            _record_game_assembly_mtime(target)
            return

        if use_obf:
            _clear_mapping_inputs_before_pipeline()

            run_pipeline(do_load_pinned_pair=True)
            _record_dump_cs_hash(target)
            _record_game_assembly_mtime(target)
    else:
        print(f"{target.game_assembly} unchanged since last dump; skipping mapping-input reset")


def _archive_previous_obf_dump(target: ProtoDumpTarget) -> None:
    marker_path = target.output_folder / GAME_ASSEMBLY_MARKER_NAME
    if not marker_path.exists():
        return
    try:
        previous_mtime_ns = int(marker_path.read_text(encoding="utf-8").strip())
    except ValueError:
        return

    snapshot_dir = find_snapshot_dir_by_game_assembly_mtime_ns(OBF_GAME_SNAPSHOTS_DIR, previous_mtime_ns)
    if snapshot_dir is None:
        print(
            f"no matching D3 snapshot for previous build (mtime_ns={previous_mtime_ns}); skipping obf archive"
        )
        return

    shutil.copytree(target.output_folder, snapshot_dir, dirs_exist_ok=True)
    shutil.copytree(NON_OBFUSCATED_DATA_DIR, snapshot_dir / "non_obf", dirs_exist_ok=True)
    shutil.copy2(GAME_MAPPINGS_JSON_FILE, snapshot_dir / GAME_MAPPINGS_JSON_FILE.name)


def _clear_mapping_inputs_before_pipeline() -> None:
    if RUNTIME_DATA_FILE.exists():
        RUNTIME_DATA_FILE.unlink()

    write_pinned_pairs(PINNED_PAIRS_FILE, PinnedPairsConfig(pairs=[]))


def is_game_assembly_changed_since_last_run(target: ProtoDumpTarget) -> bool:
    marker_path = target.output_folder / GAME_ASSEMBLY_MARKER_NAME
    if not marker_path.exists():
        return True
    try:
        last_mtime_ns = int(marker_path.read_text(encoding="utf-8").strip())
    except ValueError:
        return True
    return target.game_assembly.stat().st_mtime_ns != last_mtime_ns


def _record_game_assembly_mtime(target: ProtoDumpTarget) -> None:
    marker_path = target.output_folder / GAME_ASSEMBLY_MARKER_NAME
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(str(target.game_assembly.stat().st_mtime_ns), encoding="utf-8")


def _hash_dump_cs(dump_cs: Path) -> str:
    return hashlib.sha256(dump_cs.read_bytes()).hexdigest()


def does_dump_cs_changed_since_last_run(target: ProtoDumpTarget) -> bool:
    marker_path = target.output_folder / DUMP_CS_MARKER_NAME
    if not marker_path.exists() or not target.dump_cs.exists():
        return True
    return marker_path.read_text(encoding="utf-8").strip() != _hash_dump_cs(target.dump_cs)


def _record_dump_cs_hash(target: ProtoDumpTarget) -> None:
    marker_path = target.output_folder / DUMP_CS_MARKER_NAME
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(_hash_dump_cs(target.dump_cs), encoding="utf-8")


def _build_proto_dump_target(*, use_obf: bool, obf_dir: Path | None = None) -> ProtoDumpTarget:
    if obf_dir is not None:
        assert use_obf, "--obf-dir requires obfuscated dump mode"
        return ProtoDumpTarget(
            output_folder=obf_dir,
            game_assembly=obf_dir / "GameAssembly.dll",
            metadata=obf_dir / "global-metadata.dat",
            proto_output=obf_dir / "game_messages.proto",
            dump_cs=obf_dir / PROTOCOL_GAME_DUMP_CS_RELATIVE_PATH,
            ida_database=obf_dir / "GameAssembly.dll.i64",
        )

    if use_obf:
        return ProtoDumpTarget(
            output_folder=OBFUSCATED_DATA_DIR,
            game_assembly=OBF_GAME_ASSEMBLY_DLL,
            metadata=OBF_IL2CPP_METADATA_FILE,
            proto_output=OBF_PROTO_OUTPUT / "game_messages.proto",
            dump_cs=OBFUSCATED_DATA_DIR / PROTOCOL_GAME_DUMP_CS_RELATIVE_PATH,
        )

    return ProtoDumpTarget(
        output_folder=NON_OBFUSCATED_DATA_DIR,
        game_assembly=NON_OBF_GAME_ASSEMBLY_DLL,
        metadata=NON_OBF_IL2CPP_METADATA_FILE,
        proto_output=NON_OBF_PROTO_OUTPUT,
        dump_cs=NON_OBFUSCATED_DATA_DIR / PROTOCOL_GAME_DUMP_CS_RELATIVE_PATH,
    )


def run_il2cpp_inspector(
    game_assembly_path: Path,
    metadata_path: Path,
    output_folder: Path,
) -> None:
    output_folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(IL2CPP_INSPECTOR_EXECUTABLE),
            "-i",
            str(game_assembly_path),
            "-m",
            str(metadata_path),
            "--select-outputs",
            "-c",
            str(output_folder / "cs"),
            "-o",
            str(output_folder / "il2cpp.json"),
            "-d",
            str(output_folder / "dll"),
            "--layout",
            "assembly",
            "--unity-version",
            "6000.3.0b1",
        ],
        check=True,
    )


def run_protodec(assembly_path: Path, proto_output: Path) -> None:
    subprocess.run([str(PROTODEC_EXECUTABLE), str(assembly_path), str(proto_output)], check=True)


def gen_python_from_protoc(proto_input: Path, output_folder: Path) -> None:
    if proto_input.is_dir():
        _remove_orphaned_generated_bindings(proto_input)
        for proto_path in proto_input.iterdir():
            if proto_path.suffix != ".proto":
                continue

            subprocess.run(
                [
                    str(PROTOC_PATH),
                    f"--proto_path={proto_input}",
                    f"--python_out={output_folder}",
                    str(proto_path),
                    f"--pyi_out={output_folder}",
                ],
                check=True,
            )
        return

    parent = proto_input.parent
    subprocess.run(
        [
            str(PROTOC_PATH),
            f"--proto_path={parent}",
            f"--python_out={parent}",
            str(proto_input),
            f"--pyi_out={parent}",
        ],
        check=True,
    )


def _remove_orphaned_generated_bindings(proto_directory: Path) -> None:
    source_stems = {proto_path.stem for proto_path in proto_directory.glob("*.proto")}
    for generated_path in proto_directory.glob("*_pb2.*"):
        generated_stem = generated_path.name.removesuffix("_pb2.py").removesuffix("_pb2.pyi")
        if generated_stem not in source_stems:
            generated_path.unlink()


if __name__ == "__main__":
    main()

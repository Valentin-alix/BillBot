import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import msgspec
from tqdm import tqdm

from DBDofusUnity.consts import (
    BUNDLES_ROOT,
    DATA_BUNDLES_ROOT,
    I18N_OUTPUT_PATH,
    I18N_PATH,
    MAP_BUNDLES_ROOT,
    MAPS_ARCHIVE_PATH,
    PATH_DATAS,
    PATH_MAPS,
    PATH_STANDALONE_BUNDLES,
    STANDALONE_BUNDLES_ROOT,
    UABEA_PATH_EXE,
)
from DBDofusUnity.dofus_unity_reader.data_center.data_reader import (
    FILEPATH_BY_MODEL,
    REQUIRED_DATA_FILENAMES,
)
from DBDofusUnity.dofus_unity_reader.extraction_manifest import ExtractionManifest
from DBDofusUnity.dofus_unity_reader.generator.data_cleaning import clean_data_to_output
from DBDofusUnity.dofus_unity_reader.generator.i18n import I18NReader
from DBDofusUnity.dofus_unity_reader.generator.maps import decode_map_export
from DBDofusUnity.dofus_unity_reader.models.world_graph import WorldGraphData
from src.utils.project_paths import BUNDLE_ROOT

MANIFEST_PATH = BUNDLES_ROOT / ".extraction_manifest.json"
DATA_MODEL_BY_FILENAME = {filename: model for model, filename in FILEPATH_BY_MODEL.items()}


def _run_uabea_batch_export(bundle_path: str, output_dir: str) -> None:
    subprocess.run(
        [str(UABEA_PATH_EXE), "batchexportbundle", bundle_path, "-out", output_dir],
        check=True,
    )


def _ensure_output_dirs() -> None:
    DATA_BUNDLES_ROOT.mkdir(parents=True, exist_ok=True)
    MAP_BUNDLES_ROOT.mkdir(parents=True, exist_ok=True)
    STANDALONE_BUNDLES_ROOT.mkdir(parents=True, exist_ok=True)


def _json_file_state(output_dir: Path) -> dict[str, int]:
    if not output_dir.exists():
        return {}
    return {
        path.name: path.stat().st_mtime_ns
        for path in output_dir.iterdir()
        if path.is_file() and path.suffix == ".json"
    }


def _changed_json_outputs(output_dir: Path, before_state: dict[str, int]) -> list[Path]:
    outputs: list[Path] = []
    for path in output_dir.iterdir():
        if not path.is_file() or path.suffix != ".json":
            continue
        if before_state.get(path.name) != path.stat().st_mtime_ns:
            outputs.append(path)
    return outputs


def _iter_json_files(output_dir: Path) -> Iterable[Path]:
    if not output_dir.exists():
        return ()
    return (path for path in output_dir.iterdir() if path.is_file() and path.suffix == ".json")


def _clean_stale_outputs(manifest: ExtractionManifest, output_dirs: Iterable[Path]) -> None:
    referenced_outputs = {path.resolve() for path in manifest.referenced_outputs()}
    for output_dir in output_dirs:
        for path in _iter_json_files(output_dir):
            if path.resolve() not in referenced_outputs:
                path.unlink()


def _add_cleaned_map_exports(archive: ZipFile, temp_output_dir: Path) -> None:
    for exported_path in sorted(temp_output_dir.iterdir()):
        if not exported_path.is_file() or exported_path.suffix != ".json":
            continue

        content = decode_map_export(exported_path.read_bytes())
        archive.writestr(f"map/{exported_path.name}", msgspec.json.encode(content))


def _move_required_data_exports(temp_output_dir: Path) -> list[Path]:
    exports = [
        path
        for path in sorted(temp_output_dir.iterdir())
        if path.is_file() and path.name in REQUIRED_DATA_FILENAMES
    ]
    for exported_path in exports:
        msgspec.json.decode(exported_path.read_bytes(), type=DATA_MODEL_BY_FILENAME[exported_path.name])
    output_paths: list[Path] = []
    for exported_path in exports:
        output_path = DATA_BUNDLES_ROOT / exported_path.name
        exported_path.replace(output_path)
        output_paths.append(output_path)
    return output_paths


def _remove_unneeded_data_exports() -> None:
    for path in _iter_json_files(DATA_BUNDLES_ROOT):
        if path.name not in REQUIRED_DATA_FILENAMES:
            path.unlink()


def _raw_map_exports() -> list[Path]:
    return sorted(_iter_json_files(MAP_BUNDLES_ROOT))


def _create_maps_archive(raw_map_exports: Iterable[Path]) -> None:
    temporary_archive_path = MAPS_ARCHIVE_PATH.with_suffix(".zip.tmp")
    temporary_archive_path.unlink(missing_ok=True)
    try:
        with ZipFile(
            temporary_archive_path,
            "w",
            compression=ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            for map_path in raw_map_exports:
                archive.write(map_path, arcname=f"map/{map_path.name}")
        temporary_archive_path.replace(MAPS_ARCHIVE_PATH)
    finally:
        temporary_archive_path.unlink(missing_ok=True)


def _archive_existing_map_exports(manifest: ExtractionManifest, map_bundles: list[Path]) -> bool:
    if MAPS_ARCHIVE_PATH.exists():
        return False

    raw_map_exports = _raw_map_exports()
    if not raw_map_exports:
        return False

    expected_exports = {
        output_path.resolve()
        for output_path in manifest.referenced_outputs()
        if output_path.parent == MAP_BUNDLES_ROOT
    }
    if {path.resolve() for path in raw_map_exports} != expected_exports:
        return False

    _create_maps_archive(raw_map_exports)
    if not all(manifest.is_up_to_date(path, output_paths=[MAPS_ARCHIVE_PATH]) for path in map_bundles):
        MAPS_ARCHIVE_PATH.unlink()
        return False

    for path in raw_map_exports:
        path.unlink()
    return True


def get_world_graph_datas(*, manifest: ExtractionManifest) -> None:
    print("get world graph")
    bundle_filename = next(
        path.name for path in PATH_STANDALONE_BUNDLES.iterdir() if "worldassets_assets_all" in path.name
    )
    bundle_path = PATH_STANDALONE_BUNDLES / bundle_filename
    output_path = STANDALONE_BUNDLES_ROOT / WorldGraphData.FILE_PATH
    if manifest.is_up_to_date(bundle_path, output_paths=[output_path]):
        print("world graph is up to date")
        return

    before_state = _json_file_state(STANDALONE_BUNDLES_ROOT)
    _run_uabea_batch_export(
        bundle_path=str(bundle_path),
        output_dir=str(STANDALONE_BUNDLES_ROOT),
    )

    print("cleaning worldgraph")
    for path in STANDALONE_BUNDLES_ROOT.iterdir():
        if path.name == "world-graph.json":
            clean_data_to_output(WorldGraphData, path)
            continue
        path.unlink()
    outputs = _changed_json_outputs(STANDALONE_BUNDLES_ROOT, before_state)
    manifest.mark_success(bundle_path, output_paths=outputs or [output_path])
    manifest.save()


def get_map_datas(*, manifest: ExtractionManifest) -> None:
    print("get maps")

    map_bundles = sorted(
        path
        for path in PATH_MAPS.iterdir()
        if "mapdata_assets_world" in path.name and path.name.endswith(".bundle")
    )
    if not map_bundles:
        raise FileNotFoundError(f"No map bundles found in {PATH_MAPS}")
    if _archive_existing_map_exports(manifest, map_bundles):
        return
    if all(manifest.is_up_to_date(path, output_paths=[MAPS_ARCHIVE_PATH]) for path in map_bundles):
        return

    temporary_archive_path = MAPS_ARCHIVE_PATH.with_suffix(".zip.tmp")
    temporary_archive_path.unlink(missing_ok=True)
    try:
        with ZipFile(
            temporary_archive_path,
            "w",
            compression=ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            for bundle_path in tqdm(map_bundles):
                with tempfile.TemporaryDirectory(
                    prefix=f"{bundle_path.stem}-", dir=MAP_BUNDLES_ROOT
                ) as temp_dir:
                    temp_output_dir = Path(temp_dir)
                    _run_uabea_batch_export(bundle_path=str(bundle_path), output_dir=str(temp_output_dir))
                    if not any(temp_output_dir.glob("map_*.json")):
                        raise ValueError(f"No maps exported from {bundle_path}")
                    _add_cleaned_map_exports(archive, temp_output_dir)
        temporary_archive_path.replace(MAPS_ARCHIVE_PATH)
    finally:
        temporary_archive_path.unlink(missing_ok=True)

    for path in map_bundles:
        manifest.mark_success(path, output_paths=[MAPS_ARCHIVE_PATH])
    manifest.save()
    for path in _raw_map_exports():
        path.unlink()


def get_datas(*, manifest: ExtractionManifest) -> None:
    print("get content data")
    for path in tqdm(sorted(PATH_DATAS.iterdir())):
        if not path.is_file():
            continue
        if manifest.is_up_to_date(path):
            continue
        with tempfile.TemporaryDirectory(prefix=f"{path.stem}-", dir=DATA_BUNDLES_ROOT) as temp_dir:
            _run_uabea_batch_export(bundle_path=str(path), output_dir=temp_dir)
            output_paths = _move_required_data_exports(Path(temp_dir))
        manifest.mark_success(path, output_paths=output_paths)
        manifest.save()


def get_i18n_datas(*, manifest: ExtractionManifest) -> None:
    print("get i18n")
    source_path = Path(I18N_PATH)
    if manifest.is_up_to_date(source_path, output_paths=[I18N_OUTPUT_PATH]):
        print("i18n is up to date")
        return
    I18NReader.get_datas()
    manifest.mark_success(source_path, output_paths=[I18N_OUTPUT_PATH])
    manifest.save()


def update_all_datas() -> None:
    _ensure_output_dirs()
    manifest = ExtractionManifest.load(MANIFEST_PATH)
    _remove_unneeded_data_exports()
    get_datas(manifest=manifest)
    get_world_graph_datas(manifest=manifest)
    get_i18n_datas(manifest=manifest)
    get_map_datas(manifest=manifest)
    manifest.save()

    _clean_stale_outputs(manifest, [DATA_BUNDLES_ROOT, MAP_BUNDLES_ROOT, STANDALONE_BUNDLES_ROOT])
    subprocess.run([sys.executable, "-m", "scripts.data.display_catalog"], cwd=BUNDLE_ROOT, check=True)

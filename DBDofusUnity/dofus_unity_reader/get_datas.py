import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import msgspec
from tqdm import tqdm

sys.path.append(str(Path(__file__).parent.parent.parent))

from DBDofusUnity.consts import (
    BUNDLES_ROOT,
    DATA_BUNDLES_ROOT,
    I18N_OUTPUT_PATH,
    I18N_PATH,
    MAPS_ARCHIVE_PATH,
    PATH_DATAS,
    PATH_MAPS,
    PATH_STANDALONE_BUNDLES,
    STANDALONE_BUNDLES_ROOT,
)
from DBDofusUnity.dofus_unity_reader.bundle_export import iter_bundle_data
from DBDofusUnity.dofus_unity_reader.data_center.data_reader import (
    FILEPATH_BY_MODEL,
    REQUIRED_DATA_FILENAMES,
)
from DBDofusUnity.dofus_unity_reader.extraction_manifest import ExtractionManifest
from DBDofusUnity.dofus_unity_reader.generator.i18n import I18NReader
from DBDofusUnity.dofus_unity_reader.generator.maps import iter_bundle_maps
from DBDofusUnity.dofus_unity_reader.models.datas.map_positions_root import MapInformationsRoot
from DBDofusUnity.dofus_unity_reader.models.world_graph import WorldGraphData
from src.utils.project_paths import BUNDLE_ROOT

MANIFEST_PATH = BUNDLES_ROOT / ".extraction_manifest.json"
DATA_MODEL_BY_FILENAME = {filename: model for model, filename in FILEPATH_BY_MODEL.items()}


def _write_json(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def get_world_graph_datas(*, manifest: ExtractionManifest) -> None:
    print("get world graph")
    bundle_path = next(PATH_STANDALONE_BUNDLES.glob("*worldassets_assets_all*.bundle"))
    output_path = STANDALONE_BUNDLES_ROOT / WorldGraphData.FILE_PATH
    if manifest.is_up_to_date(bundle_path, output_paths=[output_path]):
        return
    exports = list(iter_bundle_data(bundle_path, names={"world-graph"}))
    if len(exports) != 1:
        raise ValueError(f"Expected one world graph in {bundle_path}, found {len(exports)}")
    content = msgspec.json.decode(msgspec.json.encode(exports[0][1]), type=WorldGraphData)
    _write_json(output_path, msgspec.json.encode(content))
    manifest.mark_success(bundle_path, output_paths=[output_path])
    manifest.save()


def get_map_datas(*, manifest: ExtractionManifest) -> None:
    print("get maps")
    catalog_path = DATA_BUNDLES_ROOT / FILEPATH_BY_MODEL[MapInformationsRoot]
    catalog = msgspec.json.decode(catalog_path.read_bytes(), type=MapInformationsRoot)
    names = frozenset(f"map_{entry.id}" for entry in catalog)

    map_bundles = sorted(
        path
        for path in PATH_MAPS.iterdir()
        if "mapdata_assets_world" in path.name and path.name.endswith(".bundle")
    )
    if not map_bundles:
        raise FileNotFoundError(f"No map bundles found in {PATH_MAPS}")
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
            exported_names: set[str] = set()
            for bundle_path in tqdm(map_bundles):
                for name, content in iter_bundle_maps(bundle_path, names=names):
                    archive.writestr(f"map/{name}.json", msgspec.json.encode(content))
                    exported_names.add(name)
        temporary_archive_path.replace(MAPS_ARCHIVE_PATH)
    finally:
        temporary_archive_path.unlink(missing_ok=True)

    for path in map_bundles:
        manifest.mark_success(path, output_paths=[MAPS_ARCHIVE_PATH])
    manifest.save()


def get_datas(*, manifest: ExtractionManifest) -> None:
    print("get content data")
    for path in tqdm(sorted(PATH_DATAS.iterdir())):
        if not path.is_file() or path.suffix != ".bundle":
            continue
        if manifest.is_up_to_date(path):
            continue
        output_paths: list[Path] = []
        required_names = {Path(filename).stem for filename in REQUIRED_DATA_FILENAMES}
        for name, data in iter_bundle_data(path, names=required_names):
            filename = f"{name}.json"
            content = msgspec.json.encode(data)
            msgspec.json.decode(content, type=DATA_MODEL_BY_FILENAME[filename])
            output_path = DATA_BUNDLES_ROOT / filename
            _write_json(output_path, content)
            output_paths.append(output_path)
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


def _clean_runtime_outputs() -> None:
    for directory, required in (
        (DATA_BUNDLES_ROOT, REQUIRED_DATA_FILENAMES),
        (STANDALONE_BUNDLES_ROOT, {WorldGraphData.FILE_PATH}),
    ):
        for path in directory.rglob("*"):
            if path.is_file() and (path.parent != directory or path.name not in required):
                path.unlink()


def update_all_datas() -> None:
    BUNDLES_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = ExtractionManifest.load(MANIFEST_PATH)
    get_datas(manifest=manifest)
    get_world_graph_datas(manifest=manifest)
    get_i18n_datas(manifest=manifest)
    get_map_datas(manifest=manifest)
    manifest.save()

    subprocess.run([sys.executable, "-m", "scripts.data.display_catalog"], cwd=BUNDLE_ROOT, check=True)
    _clean_runtime_outputs()


if __name__ == "__main__":
    update_all_datas()

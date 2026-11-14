import os
from pathlib import Path

from dotenv import load_dotenv

from DBDofusUnity.proto_mapper_assembly.helpers.archived_builds import PROTOCOL_GAME_DUMP_CS_RELATIVE_PATH
from DBDofusUnity.proto_mapper_assembly.helpers.obf_game_snapshot import resolve_obf_game_snapshot
from src.utils.project_paths import BUNDLE_ROOT, ENV_PATH, IS_PACKAGED
from utils.env_config import get_path_from_env, get_required_path

PROJECT_ROOT: Path = BUNDLE_ROOT / "DBDofusUnity"
BOT_SRC_ROOT: Path = PROJECT_ROOT.parent / "src"

if not IS_PACKAGED:
    load_dotenv(ENV_PATH)


_REQUIRED_GAME_TOOLCHAIN_VARIABLES = ("OBF_GAME_DIR", "NON_OBF_GAME_DIR", "PROTOC_PATH", "IDA_EXE")
_MISSING_GAME_TOOLCHAIN_PATH = PROJECT_ROOT / ".missing-game-toolchain"


def require_game_toolchain() -> None:
    if IS_PACKAGED:
        raise RuntimeError("The Dofus mapping toolchain is unavailable in the packaged application.")
    for env_name in _REQUIRED_GAME_TOOLCHAIN_VARIABLES:
        get_required_path(env_name)


OBF_GAME_DIR = get_path_from_env("OBF_GAME_DIR", _MISSING_GAME_TOOLCHAIN_PATH / "obf")
NON_OBF_GAME_DIR = get_path_from_env("NON_OBF_GAME_DIR", _MISSING_GAME_TOOLCHAIN_PATH / "non-obf")
OBF_GAME_SNAPSHOTS_DIR = get_path_from_env("OBF_GAME_SNAPSHOTS_DIR", Path.home() / "Documents" / "D3")
PROTOC_PATH = get_path_from_env("PROTOC_PATH", _MISSING_GAME_TOOLCHAIN_PATH / "protoc")
IDA_EXE = get_path_from_env("IDA_EXE", _MISSING_GAME_TOOLCHAIN_PATH / "ida")
DATA_ROOT: Path = PROJECT_ROOT / "datas"
BUNDLES_ROOT: Path = DATA_ROOT / "bundles"
DATA_BUNDLES_ROOT: Path = BUNDLES_ROOT / "data"
MAPS_ARCHIVE_PATH: Path = BUNDLES_ROOT / "maps.zip"
STANDALONE_BUNDLES_ROOT: Path = BUNDLES_ROOT / "standalone"
I18N_OUTPUT_PATH: Path = BUNDLES_ROOT / "i18n.json"
PROTOS_ROOT: Path = DATA_ROOT / "protos"
GAME_MAPPINGS_JSON_FILE: Path = PROTOS_ROOT / "game_mappings.json"
GAME_MAPPINGS_DETAILED_JSON_FILE: Path = PROTOS_ROOT / "game_mappings_detailed.json"

DOFUS_ASSETS_FOLDER: Path = OBF_GAME_DIR / "Dofus_Data" / "StreamingAssets"
DOFUS_CONTENT_FOLDER: Path = DOFUS_ASSETS_FOLDER / "Content"
I18N_PATH: Path = DOFUS_CONTENT_FOLDER / "I18n" / "fr.bin"
PATH_STANDALONE_BUNDLES: Path = get_path_from_env(
    "PATH_STANDALONE_BUNDLES",
    DOFUS_ASSETS_FOLDER / "aa" / "StandaloneWindows64",
)
PATH_MAPS: Path = DOFUS_CONTENT_FOLDER / "Map" / "Data"
PATH_DATAS: Path = DOFUS_CONTENT_FOLDER / "Data"

IL2CPP_INSPECTOR_EXECUTABLE: Path = get_path_from_env(
    "IL2CPP_INSPECTOR_EXECUTABLE",
    PROJECT_ROOT
    / "Il2CppInspectorRedux"
    / "Il2CppInspector.CLI"
    / "bin"
    / "Debug"
    / "net10.0"
    / "win-x64"
    / "Il2CppInspector.exe",
)
PROTODEC_EXECUTABLE: Path = get_path_from_env(
    "PROTODEC_EXECUTABLE",
    PROJECT_ROOT / "protodec" / "bin" / "protodec" / "Debug" / "net10.0" / "protodec.exe",
)

if IS_PACKAGED or not all(os.environ.get(env_name) for env_name in _REQUIRED_GAME_TOOLCHAIN_VARIABLES):
    OBF_GAME_ASSEMBLY_DLL = Path()
    OBF_GAME_ASSEMBLY_DLL_I64 = Path()
    OBF_IL2CPP_METADATA_FILE = Path()
else:
    _OBF_GAME_SNAPSHOT = resolve_obf_game_snapshot(
        real_game_dir=OBF_GAME_DIR,
        snapshots_root=OBF_GAME_SNAPSHOTS_DIR,
        non_obf_game_dir=NON_OBF_GAME_DIR,
    )
    OBF_GAME_ASSEMBLY_DLL = _OBF_GAME_SNAPSHOT.game_assembly
    OBF_GAME_ASSEMBLY_DLL_I64 = OBF_GAME_ASSEMBLY_DLL.with_suffix(".dll.i64")
    OBF_IL2CPP_METADATA_FILE = _OBF_GAME_SNAPSHOT.metadata

NON_OBF_GAME_ASSEMBLY_DLL: Path = NON_OBF_GAME_DIR / "GameAssembly.dll"
NON_OBF_GAME_ASSEMBLY_DLL_I64: Path = NON_OBF_GAME_DIR / "GameAssembly.dll.i64"
NON_OBF_IL2CPP_METADATA_FILE: Path = NON_OBF_GAME_DIR / "global-metadata.dat"

PROTO_MAPPER_DATA_ROOT: Path = DATA_ROOT / "proto_mapper"
AUTO_MODE_MAPPING_CONTRACT_FILE: Path = PROTO_MAPPER_DATA_ROOT / "auto_mode_mapping_contract.json"
PINNED_PAIRS_FILE: Path = PROTO_MAPPER_DATA_ROOT / "pinned_pairs.json"
CAPTURE_SEQUENCE_HINTS_FILE: Path = PROTO_MAPPER_DATA_ROOT / "capture_sequence_hints.json"
EXCLUDED_NON_OBF_FILE: Path = PROTO_MAPPER_DATA_ROOT / "excluded_non_obf.json"
OBFUSCATED_DATA_DIR: Path = PROTO_MAPPER_DATA_ROOT / "obf"
NON_OBFUSCATED_DATA_DIR: Path = PROTO_MAPPER_DATA_ROOT / "non_obf"
OBF_PROTO_OUTPUT: Path = PROTOS_ROOT / "obf" / "game"
NON_OBF_PROTO_OUTPUT: Path = PROTOS_ROOT / "non_obf" / "game"
NON_OBF_NEW_DUMP_CS_FILE: Path = NON_OBFUSCATED_DATA_DIR / "new_dump_cs.json"

RUNTIME_DATA_FILE: Path = PROTO_MAPPER_DATA_ROOT / "instancied_msg_infos.json"

OBF_PROTO_ACCESSES_FILE: Path = OBFUSCATED_DATA_DIR / "proto_accesses.json"
NON_OBF_PROTO_ACCESSES_FILE: Path = NON_OBFUSCATED_DATA_DIR / "proto_accesses.json"
NON_OBF_SIGNATURE_OVERRIDES_FILE: Path = NON_OBFUSCATED_DATA_DIR / "messages_access_signature_override.json"
OBF_PROTOCOL_GAME_DUMP_CS_FILE: Path = OBFUSCATED_DATA_DIR / PROTOCOL_GAME_DUMP_CS_RELATIVE_PATH
NON_OBF_PROTOCOL_GAME_DUMP_CS_FILE: Path = NON_OBFUSCATED_DATA_DIR / PROTOCOL_GAME_DUMP_CS_RELATIVE_PATH

GAME_ASSEMBLY_MARKER_NAME = ".last_dumped_game_assembly_mtime"
DUMP_CS_MARKER_NAME = ".last_dumped_protocol_cs_hash"

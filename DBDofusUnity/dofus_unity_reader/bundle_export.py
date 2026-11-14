from collections.abc import Iterator, Set
from functools import cache
from pathlib import Path
from typing import Any, Protocol, cast

import msgspec
import UnityPy
import UnityPy.config
from UnityPy.files.ObjectReader import ObjectReader
from UnityPy.files.SerializedFile import SerializedFile

from DBDofusUnity.consts import OBF_GAME_DIR


class AssetEnvironment(Protocol):
    @property
    def assets(self) -> list[SerializedFile]: ...

    @property
    def objects(self) -> list[ObjectReader[Any]]: ...


class ManagedReference(msgspec.Struct):
    rid: int
    data: dict[str, Any] | msgspec.UnsetType = msgspec.UNSET


class ManagedReferences(msgspec.Struct):
    version: int
    RefIds: list[ManagedReference]


class SerializedDictionary(msgspec.Struct):
    m_keys: list[int | str]
    m_values: list[Any]


@cache
def _unity_version(game_dir: Path) -> str:
    environment = cast(AssetEnvironment, UnityPy.load(str(game_dir / "Dofus_Data" / "globalgamemanagers")))
    versions = {asset.unity_version for asset in environment.assets}
    if len(versions) != 1 or not (version := versions.pop()) or version == "0.0.0":
        raise ValueError(f"Cannot determine the Unity version of {game_dir}")
    return version


def _normalize_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_value(item) for item in cast(list[Any], value)]
    if not isinstance(value, dict):
        return value
    value = cast(dict[str, Any], value)
    if "RefIds" in value and "version" in value:
        registry = msgspec.convert(value, type=ManagedReferences)
        if registry.version != 2:
            raise ValueError(f"Unsupported managed reference registry version: {registry.version}")
        references: list[dict[str, Any]] = []
        for ref in registry.RefIds:
            if ref.data is msgspec.UNSET:
                if ref.rid != -2:
                    raise ValueError(f"Managed reference {ref.rid} has no data")
                references.append({"rid": ref.rid})
            else:
                references.append({"rid": ref.rid, **_normalize_value(ref.data)})
        return references
    if "m_keys" in value and "m_values" in value:
        dictionary = msgspec.convert(value, type=SerializedDictionary)
        return {
            str(key): _normalize_value(item)
            for key, item in zip(dictionary.m_keys, dictionary.m_values, strict=True)
        }
    return {key: _normalize_value(item) for key, item in value.items()}


def normalize_monobehaviour(data: dict[str, Any]) -> dict[str, Any] | list[Any]:
    excluded = {"m_GameObject", "m_Enabled", "m_Script", "m_Name", "objectsById"}
    content = {key: _normalize_value(value) for key, value in data.items() if key not in excluded}
    return content["references"] if content.keys() == {"references"} else content


def load_bundle(bundle_path: Path, *, game_dir: Path = OBF_GAME_DIR) -> AssetEnvironment:
    setattr(UnityPy.config, "FALLBACK_UNITY_VERSION", _unity_version(game_dir))
    return cast(AssetEnvironment, UnityPy.load(str(bundle_path)))


def iter_bundle_data(
    bundle_path: Path, *, names: Set[str] | None = None
) -> Iterator[tuple[str, dict[str, Any] | list[Any]]]:
    environment = load_bundle(bundle_path)
    for obj in environment.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        if names is not None and obj.peek_name() not in names:
            continue
        data = obj.parse_as_dict()
        name = data.get("m_Name")
        if not isinstance(name, str) or not name:
            continue
        yield name, normalize_monobehaviour(data)

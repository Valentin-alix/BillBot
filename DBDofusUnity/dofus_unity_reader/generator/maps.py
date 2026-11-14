from collections.abc import Iterator, Set
from pathlib import Path
from typing import Any, Literal

import msgspec

from DBDofusUnity.dofus_unity_reader.bundle_export import load_bundle
from DBDofusUnity.dofus_unity_reader.models.maps import (
    CellData,
    MapData,
    MapDataRoot,
    MapPosition,
    MapReference,
)


class ClientCellData(msgspec.Struct, frozen=True, kw_only=True):
    cellNumber: int
    speed: int
    mapChangeData: int
    moveZone: int
    linkedZone: int
    mov: int
    los: int
    farmCell: int
    visible: int
    havenbagCell: int
    floor: int = msgspec.field(name="altitude")
    red: int
    blue: int
    arrow: int


def _normalize_cell(cell: ClientCellData) -> CellData:
    return msgspec.convert(
        {
            **msgspec.structs.asdict(cell),
            "movDuringFight": bool(cell.mov),
            "movDuringRP": bool(cell.mov),
        },
        type=CellData,
    )


class ElementReference(msgspec.Struct, frozen=True):
    rid: int


class PlacedMapElements(msgspec.Struct, frozen=True):
    mapElements: list[ElementReference]
    cellId: int
    displayOrder: int


class LayerMapElements(msgspec.Struct, frozen=True):
    mapElements: list[ElementReference]


class ClientReferenceData(msgspec.Struct, frozen=True, kw_only=True):
    gfxId: int | None = None
    cellId: int | None = None
    displayOrder: int | None = None
    position: MapPosition | None = None
    interactiveId: int | None = msgspec.field(default=None, name="<interactiveId>k__BackingField")


class ClientMapReference(ClientReferenceData, frozen=True, kw_only=True):
    rid: int


class UnityMapReference(msgspec.Struct, frozen=True):
    rid: int
    data: ClientReferenceData | msgspec.UnsetType = msgspec.UNSET


class UnityMapReferences(msgspec.Struct, frozen=True):
    version: Literal[2]
    RefIds: list[UnityMapReference]


class ClientMapGeometry(msgspec.Struct, frozen=True):
    cellsData: list[ClientCellData]
    middlegroundMapElements: list[PlacedMapElements]
    backgroundMapElements: list[LayerMapElements]
    foregroundMapElements: list[LayerMapElements]


class ClientMapData(ClientMapGeometry, frozen=True):
    references: list[ClientMapReference]


_EMPTY_REFERENCE = ClientReferenceData()


def decode_map_export(raw: bytes) -> MapDataRoot:
    return decode_map_data(msgspec.json.decode(raw, type=dict[str, Any]))


def decode_map_data(root: dict[str, Any]) -> MapDataRoot:
    data = msgspec.convert(root, type=ClientMapData)
    return _decode_map(data, ((ref.rid, ref) for ref in data.references))


def _decode_map(
    data: ClientMapGeometry, source_references: Iterator[tuple[int, ClientReferenceData]]
) -> MapDataRoot:
    placements = {
        element.rid: (group.cellId, group.displayOrder)
        for group in data.middlegroundMapElements
        for element in group.mapElements
    }
    unplaced_layer_rids = {
        element.rid
        for group in data.backgroundMapElements + data.foregroundMapElements
        for element in group.mapElements
    }
    references: list[MapReference] = []
    for rid, ref in source_references:
        cell_id, order = placements.get(rid, (ref.cellId, ref.displayOrder))
        if ref.interactiveId is not None and cell_id is None and rid not in unplaced_layer_rids:
            raise ValueError(f"Interactive {ref.interactiveId} has no cell placement")
        if (
            ref.gfxId is None
            and cell_id is None
            and ref.interactiveId is None
            and order is None
            and ref.position is None
        ):
            continue
        references.append(
            MapReference(
                gfxId=ref.gfxId,
                cellId=cell_id,
                m_interactionId=ref.interactiveId,
                innerCellRenderOrder=order,
                position=ref.position,
            )
        )
    result = MapDataRoot(
        references=references,
        mapData=MapData(cellsData=[_normalize_cell(cell) for cell in data.cellsData]),
    )
    if [cell.cellNumber for cell in result.mapData.cellsData] != list(range(560)):
        raise ValueError("Map export must contain the 560 cells in cell number order")
    return result


def _unity_references(registry: UnityMapReferences) -> Iterator[tuple[int, ClientReferenceData]]:
    for ref in registry.RefIds:
        if ref.data is msgspec.UNSET:
            if ref.rid != -2:
                raise ValueError(f"Managed reference {ref.rid} has no data")
            yield ref.rid, _EMPTY_REFERENCE
        else:
            yield ref.rid, ref.data


def decode_unity_map(raw: dict[str, Any]) -> MapDataRoot:
    geometry = msgspec.convert(raw, type=ClientMapGeometry)
    registry = msgspec.convert(raw["references"], type=UnityMapReferences)
    return _decode_map(geometry, _unity_references(registry))


def iter_bundle_maps(bundle_path: Path, *, names: Set[str]) -> Iterator[tuple[str, MapDataRoot]]:
    environment = load_bundle(bundle_path)
    for obj in environment.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        name = obj.peek_name()
        if name is not None and name in names:
            yield name, decode_unity_map(obj.parse_as_dict())


def export_map_bundle(bundle_path: Path, names: frozenset[str]) -> list[tuple[str, bytes]]:
    return [
        (name, msgspec.json.encode(content)) for name, content in iter_bundle_maps(bundle_path, names=names)
    ]

from collections.abc import Iterator, Set
from pathlib import Path
from typing import Any

import msgspec

from DBDofusUnity.dofus_unity_reader.bundle_export import load_bundle, read_managed_references
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


class ClientMapReference(msgspec.Struct, frozen=True, kw_only=True):
    rid: int
    gfxId: int | None = None
    cellId: int | None = None
    displayOrder: int | None = None
    position: MapPosition | None = None
    interactiveId: int | None = msgspec.field(default=None, name="<interactiveId>k__BackingField")


class ClientMapData(msgspec.Struct, frozen=True):
    cellsData: list[ClientCellData]
    references: list[ClientMapReference]
    middlegroundMapElements: list[PlacedMapElements]
    backgroundMapElements: list[LayerMapElements]
    foregroundMapElements: list[LayerMapElements]


def decode_map_export(raw: bytes) -> MapDataRoot:
    return decode_map_data(msgspec.json.decode(raw, type=dict[str, Any]))


def decode_map_data(root: dict[str, Any]) -> MapDataRoot:
    data = msgspec.convert(root, type=ClientMapData)
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
    for ref in data.references:
        cell_id, order = placements.get(ref.rid, (ref.cellId, ref.displayOrder))
        if ref.interactiveId is not None and cell_id is None and ref.rid not in unplaced_layer_rids:
            raise ValueError(f"Interactive {ref.interactiveId} has no cell placement")
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


_REFERENCE_FIELDS = frozenset(field.encode_name for field in msgspec.structs.fields(ClientMapReference))


def decode_unity_map(raw: dict[str, Any]) -> MapDataRoot:
    fields = ("cellsData", "middlegroundMapElements", "backgroundMapElements", "foregroundMapElements")
    root = {key: raw[key] for key in fields}
    references: list[dict[str, Any]] = []
    for ref in read_managed_references(raw["references"]):
        entry: dict[str, Any] = {"rid": ref.rid}
        if ref.data is not msgspec.UNSET:
            entry.update((key, value) for key, value in ref.data.items() if key in _REFERENCE_FIELDS)
        references.append(entry)
    root["references"] = references
    return decode_map_data(root)


def iter_bundle_maps(bundle_path: Path, *, names: Set[str]) -> Iterator[tuple[str, MapDataRoot]]:
    environment = load_bundle(bundle_path)
    for obj in environment.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        name = obj.peek_name()
        if name is not None and name in names:
            yield name, decode_unity_map(obj.parse_as_dict())

from typing import Any

import pytest

from DBDofusUnity.dofus_unity_reader.bundle_export import normalize_monobehaviour


def test_data_root_exports_flat_records_with_managed_reference_ids() -> None:
    root: dict[str, Any] = {
        "m_Name": "AreasDataRoot",
        "m_GameObject": {"m_FileID": 0, "m_PathID": 0},
        "m_Enabled": 1,
        "m_Script": {"m_FileID": 1, "m_PathID": 42},
        "objectsById": {"m_keys": [7], "m_values": [{"rid": 100}]},
        "references": {
            "version": 2,
            "RefIds": [{"rid": 100, "type": {"class": "AreaData"}, "data": {"id": 7}}],
        },
    }

    assert normalize_monobehaviour(root) == [{"rid": 100, "id": 7}]


def test_map_keeps_placements_and_flattens_reference_data_including_null_reference() -> None:
    placement: list[dict[str, Any]] = [{"cellId": 42, "displayOrder": 3, "mapElements": [{"rid": 100}]}]
    root: dict[str, Any] = {
        "m_Name": "map_123",
        "cellsData": [],
        "middlegroundMapElements": placement,
        "references": {
            "version": 2,
            "RefIds": [
                {"rid": -2, "type": {"class": ""}},
                {"rid": 100, "data": {"gfxId": 7, "<interactiveId>k__BackingField": 99}},
            ],
        },
    }

    assert normalize_monobehaviour(root) == {
        "cellsData": [],
        "middlegroundMapElements": placement,
        "references": [{"rid": -2}, {"rid": 100, "gfxId": 7, "<interactiveId>k__BackingField": 99}],
    }


def test_world_graph_converts_nested_serialized_dictionaries() -> None:
    root: dict[str, Any] = {
        "m_Name": "world-graph",
        "m_vertices": {
            "m_keys": [123],
            "m_values": [{"m_keys": [1], "m_values": [{"m_mapId": 123, "m_zoneId": 1, "m_uid": 5}]}],
        },
        "m_edges": {"m_keys": [], "m_values": []},
    }

    assert normalize_monobehaviour(root) == {
        "m_vertices": {"123": {"1": {"m_mapId": 123, "m_zoneId": 1, "m_uid": 5}}},
        "m_edges": {},
    }


@pytest.mark.parametrize(
    "content",
    [
        {"m_keys": [1], "m_values": []},
        {"version": 99, "RefIds": []},
        {"version": 2, "RefIds": [{"rid": 100}]},
    ],
)
def test_invalid_serialized_content_is_rejected(content: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        normalize_monobehaviour({"content": content})

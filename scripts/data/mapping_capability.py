import argparse
import json
from collections import defaultdict
from functools import reduce

from icecream import ic

from DBDofusUnity.consts import PROJECT_ROOT
from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.game_constants.map_capability import (
    MapCapabilityFlag,
    does_allow_capability,
)

_MIN_THRESHOLD = 10


def map_capability(capability_name: str) -> None:
    try:
        capability = MapCapabilityFlag[capability_name]
    except KeyError as error:
        raise ValueError(f"Unknown capability: {capability_name}") from error

    resources = PROJECT_ROOT / "dofus_unity_reader/tools/mapping_flags/resources/MapPositions.json"
    with resources.open(encoding="utf-8") as file:
        old_datas = json.load(file)
    old_capability_by_map_id = {data["id"]: data["capabilities"] for data in old_datas}

    allow_capability: dict[int, int] = defaultdict(int)
    not_allow_capability: dict[int, int] = defaultdict(int)
    for map_id, map_pos in DataReader().map_info_by_map_id.items():
        old_capability = old_capability_by_map_id.get(map_id)
        if old_capability is None:
            continue
        if does_allow_capability(old_capability, capability):
            allow_capability[map_pos.m_flags] += 1
        else:
            not_allow_capability[map_pos.m_flags] += 1

    valuable_allow_capability = {key for key, value in allow_capability.items() if value > _MIN_THRESHOLD}
    valuable_not_allow_capability = {
        key for key, value in not_allow_capability.items() if value > _MIN_THRESHOLD
    }

    common_bits = reduce(lambda previous, flag: previous & flag, valuable_allow_capability)

    for value in valuable_not_allow_capability:
        if value & common_bits != 0:
            # Bits also present in valuable_not_allow_capability are not characteristic.
            common_bits &= ~(value & common_bits)

    ic(capability_name, common_bits)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("capability", choices=MapCapabilityFlag.__members__)
    map_capability(parser.parse_args().capability)

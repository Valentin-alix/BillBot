from functools import cache

from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import ObjectItemInventory
from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.data_center.i18n import I18N
from DBDofusUnity.dofus_unity_reader.game_constants.inventory_position import (
    CharacterInventoryPositionEnum,
)
from DBDofusUnity.dofus_unity_reader.game_constants.item import ItemEnum, ItemTypeEnum
from DBDofusUnity.dofus_unity_reader.game_constants.monster import PROTECTOR_RACES
from DBDofusUnity.dofus_unity_reader.models.datas.items_root import ItemsRootItem


def is_exchangeable_item(item: ItemsRootItem):
    return (item.m_flags & 8) != 0


def get_equipment_on_position(
    objects_by_uid: dict[int, ObjectItemInventory],
    position: CharacterInventoryPositionEnum,
) -> ObjectItemInventory | None:
    return next(
        (object for object in objects_by_uid.values() if object.position == position),
        None,
    )


@cache
def get_gatherer_item_gids() -> set[int]:
    return {
        harvestable
        for sub_area in DataReader().sub_area_by_id.values()
        for harvestable in sub_area.harvestables
        if harvestable in DataReader().item_by_id
    } | {ItemEnum.WATER}


@cache
def get_gathered_item_id_by_name() -> dict[str, int]:
    return {
        I18N()
        .name_by_id[DataReader().item_by_id[item_id].nameId]
        .lower()
        .replace("s", "")
        .replace(" ", ""): item_id
        for item_id in get_gatherer_item_gids()
    }


@cache
def get_protector_drop_item_ids() -> set[int]:
    return {
        drop.objectId
        for race in PROTECTOR_RACES
        for monster in DataReader().monsters_by_race[race]
        for drop in monster.drops
        if DataReader().item_by_id[drop.objectId].typeId
        not in [ItemTypeEnum.EKLEME, ItemTypeEnum.PIERRE_BRUTE]
    }

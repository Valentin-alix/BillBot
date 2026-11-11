from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.data_center.i18n import I18N
from DBDofusUnity.dofus_unity_reader.game_constants.item import CUSTOM_GATHERER_BY_SAC
from DBDofusUnity.dofus_unity_reader.models.datas.monsters_root import MonsterDrop

from src.controller.game_data import GameDataController
from src.core.engine.items.item import get_gathered_item_id_by_name


def get_rare_gid_with_weight_from_protector_drop(
    drops: list[MonsterDrop],
    server_id: int = 1,
) -> tuple[int | None, float]:
    avg_price_by_gid = GameDataController().get_avg_price_by_gid(server_id)

    res_object_id: int | None = None
    weight: float = 0

    for drop in drops:
        if not (item_description := DataReader().item_by_id[drop.objectId].descriptionId):
            continue
        if item_description not in I18N().name_by_id:
            continue
        description = I18N().name_by_id[item_description].lower().replace("s", "")
        if drop.objectId in CUSTOM_GATHERER_BY_SAC:
            res_object_id = CUSTOM_GATHERER_BY_SAC[drop.objectId]
        elif description.startswith("cet énorme"):
            cleaned_desc = "".join(description.replace(".", "").split(" ")[-4:])
            for item_name, item_gid in get_gathered_item_id_by_name().items():
                if item_name in cleaned_desc:
                    res_object_id = item_gid
                    break
            else:
                continue

        weight += drop.percentDropForGrade1 / 100 * avg_price_by_gid.get(drop.objectId, 1)
    return res_object_id, weight

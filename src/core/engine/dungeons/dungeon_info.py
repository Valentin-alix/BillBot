from dataclasses import dataclass
from functools import cached_property

from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.data_center.i18n import I18N
from DBDofusUnity.dofus_unity_reader.game_constants.npc import NpcDialogInfo, NpcInfo
from DBDofusUnity.dofus_unity_reader.models.datas.dungeons_root import DungeonsRootItem

from src.core.engine.npcs.dialog_turn import DialogTurn
from src.core.engine.npcs.reply_selector import ByText


@dataclass
class DungeonInfo:
    key_name: str
    entrance_npc_info: NpcInfo
    entrance_turns: list[DialogTurn]
    exit_npc_info: NpcDialogInfo
    exit_turns: list[DialogTurn]

    @cached_property
    def dungeon(self) -> DungeonsRootItem:
        return DataReader().dungeon_by_entrance_map_id[self.entrance_npc_info.npc_map_id]

    @cached_property
    def exit_dialog_map_id(self) -> int:
        data_reader = DataReader()
        exit_map_name = f"{self.name} - Sortie"
        dungeon_world_maps = {
            data_reader.map_info_by_map_id[map_id].worldMap for map_id in self.dungeon.mapIds
        }
        assert len(dungeon_world_maps) == 1, (
            f"Dungeon {self.name!r} spans multiple worlds: {sorted(dungeon_world_maps)}"
        )
        exit_maps = [
            map_info
            for map_info in data_reader.map_info_by_map_id.values()
            if I18N().name_by_id.get(map_info.nameId) == exit_map_name
            and map_info.worldMap in dungeon_world_maps
        ]
        assert len(exit_maps) == 1, f"Expected one exit map named {exit_map_name!r}, got {exit_maps}"
        return exit_maps[0].id

    @cached_property
    def name(self) -> str:
        return I18N().name_by_id[self.dungeon.nameId]


BOUFTOU_ROYAL_DUNGEON = DungeonInfo(
    key_name="Clef de la Cour du Bouftou Royal",
    entrance_npc_info=NpcInfo(npc_map_id=120063489, npc_name="Rotabla le berger"),
    entrance_turns=[
        DialogTurn(reply=ByText(pattern=r"utiliser le trousseau de clefs")),
        DialogTurn(reply=ByText(pattern=r"^oui\.$")),
    ],
    exit_npc_info=NpcDialogInfo(npc_name="Rotabla le berger"),
    exit_turns=[DialogTurn(reply=ByText(pattern=r"^quitter la cour du bouftou royal\.$"))],
)

GRANGE_TOURNESOL_DUNGEON = DungeonInfo(
    key_name="Clef des Champs",
    entrance_npc_info=NpcInfo(npc_map_id=192937992, npc_name="Mawy Ingalsse"),
    entrance_turns=[
        DialogTurn(reply=ByText(pattern=r"utiliser le trousseau de clefs")),
        DialogTurn(reply=ByText(pattern=r"^oui\.$")),
    ],
    exit_npc_info=NpcDialogInfo(npc_name="Mawy Ingalsse"),
    exit_turns=[DialogTurn(reply=ByText(pattern=r"^sortir\.$"))],
)

PLAYABLE_DUNGEONS = [GRANGE_TOURNESOL_DUNGEON, BOUFTOU_ROYAL_DUNGEON]


def get_dungeon_info_for_map_id(map_id: int) -> DungeonInfo | None:
    return next(
        (
            dungeon_info
            for dungeon_info in PLAYABLE_DUNGEONS
            if map_id in dungeon_info.dungeon.mapIds or map_id == dungeon_info.exit_dialog_map_id
        ),
        None,
    )

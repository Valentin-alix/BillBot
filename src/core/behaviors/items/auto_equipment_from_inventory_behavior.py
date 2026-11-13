from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import ObjectItemInventory
from DBDofusUnity.datas.protos.non_obf.game.inventory_pb2 import (
    ObjectAddedEvent,
    ObjectMovementEvent,
    ObjectSetPositionRequest,
)
from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.game_constants.inventory_position import (
    CharacterInventoryPositionEnum,
)
from google.protobuf.message import Message
from src.core.behaviors.behavior import Behavior
from src.core.engine.items.equipment import equipment_score, get_equipment_positions, roll_score
from src.core.engine.items.item import get_equipment_on_position
from src.core.engine.movements.world.criterions.group_item_criterion import GroupItemCriterion
from src.services.human_timings import HumanTimingsService


def _equipment_signature(item: ObjectItemInventory) -> tuple[int, tuple[bytes, ...]]:
    effects = tuple(sorted(effect.SerializeToString(deterministic=True) for effect in item.item.effects))
    return item.item.gid, effects


@dataclass
class AutoEquipmentFromInventoryBehavior(Behavior):
    _positions_by_uid: dict[int, CharacterInventoryPositionEnum] = field(
        init=False, default_factory=lambda: dict[int, CharacterInventoryPositionEnum]()
    )
    _pending_position: CharacterInventoryPositionEnum | None = field(init=False, default=None)

    def run(self) -> None:
        self._positions_by_uid = {}
        self._pending_position = None
        self.choose_loadout()

    def choose_loadout(self) -> None:
        inventory_items = [
            item
            for item in self.game_state.inventory.objects_by_uid.values()
            if item.position == CharacterInventoryPositionEnum.InventoryPositionNotEquiped
            and self._is_equipable(item)
        ]
        if not inventory_items:
            return self.finish()

        equipped_by_pos = self._get_equipped_by_position()
        best_equipped_by_pos = self._best_loadout(equipped_by_pos, inventory_items)
        current_score = equipment_score(
            equipped_by_pos.values(), self.game_state.fight.primary_and_second_elem[0]
        )
        best_score = equipment_score(
            best_equipped_by_pos.values(), self.game_state.fight.primary_and_second_elem[0]
        )
        if best_score <= current_score:
            self.logger.info("No inventory equipment improves the current loadout")
            return self.finish()

        self._positions_by_uid = {}
        for position, item in best_equipped_by_pos.items():
            equipped = equipped_by_pos.get(position)
            if equipped is item:
                continue
            if equipped is not None and _equipment_signature(equipped) == _equipment_signature(item):
                continue
            self._positions_by_uid[item.item.uid] = position
        self.logger.info(
            "Equipping %s inventory item(s), score %.1f -> %.1f",
            len(self._positions_by_uid),
            current_score,
            best_score,
        )
        self._equip_next()

    def _get_equipped_by_position(self) -> dict[CharacterInventoryPositionEnum, ObjectItemInventory]:
        return {
            position: item
            for position in CharacterInventoryPositionEnum
            if (item := get_equipment_on_position(self.game_state.inventory.objects_by_uid, position))
            is not None
        }

    def _is_equipable(self, item: ObjectItemInventory) -> bool:
        item_data = DataReader().item_by_id.get(item.item.gid)
        if item_data is None or item_data.level is None or item_data.level > self.game_state.player.level:
            return False
        if not get_equipment_positions(item.item.gid):
            return False
        if not item_data.criterions:
            return True
        try:
            return GroupItemCriterion(item_data.criterions).is_respected(
                self.game_state.get_criterion_context()
            )
        except (ValueError, KeyError):
            self.logger.warning("Cannot evaluate equipment criterion for gid=%s", item.item.gid)
            return False

    def _best_loadout(
        self,
        current: dict[CharacterInventoryPositionEnum, ObjectItemInventory],
        candidates: Iterable[ObjectItemInventory],
    ) -> dict[CharacterInventoryPositionEnum, ObjectItemInventory]:
        """Greedy pruning may miss set bonuses; final loadout scoring prevents equipping worse gear."""
        primary_elem = self.game_state.fight.primary_and_second_elem[0]
        candidates_by_positions: dict[
            tuple[CharacterInventoryPositionEnum, ...], list[ObjectItemInventory]
        ] = defaultdict(list)
        for item in candidates:
            positions = get_equipment_positions(item.item.gid)
            if positions:
                candidates_by_positions[positions].append(item)

        best = dict(current)
        for positions, pool in candidates_by_positions.items():
            currently_equipped = [current[position] for position in positions if position in current]
            canonical_by_gid: dict[int, ObjectItemInventory] = {}
            for item in currently_equipped:
                canonical_by_gid[item.item.gid] = item
            for item in pool:
                incumbent = canonical_by_gid.get(item.item.gid)
                if incumbent is None or roll_score(item, primary_elem) > roll_score(incumbent, primary_elem):
                    canonical_by_gid[item.item.gid] = item

            seen_gids: set[int] = set()
            options: list[ObjectItemInventory] = []
            for item in pool + currently_equipped:
                gid = item.item.gid
                if gid in seen_gids:
                    continue
                seen_gids.add(gid)
                options.append(canonical_by_gid[gid])
            chosen = sorted(options, key=lambda item: roll_score(item, primary_elem), reverse=True)[
                : len(positions)
            ]

            chosen_ids = {item.item.uid for item in chosen}
            retained = {
                position: current[position]
                for position in positions
                if position in current and current[position].item.uid in chosen_ids
            }
            retained_uids = {item.item.uid for item in retained.values()}
            remaining_items = [item for item in chosen if item.item.uid not in retained_uids]
            remaining_positions = [position for position in positions if position not in retained]

            for position in positions:
                best.pop(position, None)
            best.update(retained)
            for position, item in zip(remaining_positions, remaining_items, strict=False):
                best[position] = item

        return best

    def _equip_next(self) -> None:
        if not self._positions_by_uid:
            return self.finish()
        uid, position = self._positions_by_uid.popitem()
        if (
            uid not in self.game_state.inventory.objects_by_uid
            or self.game_state.inventory.objects_by_uid[uid].position
            != CharacterInventoryPositionEnum.InventoryPositionNotEquiped
        ):
            return self._equip_next()

        self._pending_position = position
        self.event_manager.on(
            [ObjectMovementEvent, ObjectAddedEvent],
            self._on_equip_message,
            originator=self,
            override_on_self=True,
            timeout=15,
            on_timeout=lambda: self.on_timeout_inventory_weight_on_equipped(uid, position),
        )
        self.send_message_delayed(
            ObjectSetPositionRequest(object_uid=uid, quantity=1, position=position),
            HumanTimingsService().get_timing_equipment_choice(),
        )

    def _on_equip_message(self, msg: Message) -> None:
        if isinstance(msg, (ObjectMovementEvent, ObjectAddedEvent)):
            self._on_equip_confirmed(msg)

    def on_timeout_inventory_weight_on_equipped(self, uid: int, position: int):
        self.event_manager.clear_listener_by_origin_and_type(ObjectMovementEvent, self)
        self.event_manager.clear_listener_by_origin_and_type(ObjectAddedEvent, self)
        self.logger.error(
            f"Timeout inventory weight after equipped for item {uid} to position {position}, removing it from inventory"
        )
        self.game_state.inventory.remove_object(uid)
        self._equip_next()

    def _on_equip_confirmed(self, msg: ObjectMovementEvent | ObjectAddedEvent) -> None:
        position = msg.position if isinstance(msg, ObjectMovementEvent) else msg.object.position
        if position != self._pending_position:
            return
        self.event_manager.clear_listener_by_origin(self)
        self._pending_position = None
        self._equip_next()

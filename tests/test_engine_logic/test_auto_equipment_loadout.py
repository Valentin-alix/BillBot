import time
from collections.abc import Callable
from threading import RLock
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import ObjectEffect, ObjectItem, ObjectItemInventory
from DBDofusUnity.datas.protos.non_obf.game.inventory_pb2 import (
    ObjectAddedEvent,
    ObjectMovementEvent,
)
from DBDofusUnity.dofus_unity_reader.game_constants.characteristic import (
    CharacteristicEnum,
    EffectElement,
)
from DBDofusUnity.dofus_unity_reader.game_constants.inventory_position import (
    CharacterInventoryPositionEnum,
)
from src.core.behaviors.items.auto_equipment_from_inventory_behavior import (
    AutoEquipmentFromInventoryBehavior,
)
from src.core.engine.items import equipment as equipment_module
from src.core.states.game_state import GameState
from src.services.logging_utils.loggers import BotLogger

_RING_TYPE_ID = 9
_DOFUS_TYPE_ID = 23
_POWER_ACTION = 1
_VITALITY_ACTION = 2
_EQUIPMENT_POSITION_CASES = (
    (1, (CharacterInventoryPositionEnum.AccessoryPositionAmulet,)),
    (2, (CharacterInventoryPositionEnum.AccessoryPositionWeapon,)),
    (311, (CharacterInventoryPositionEnum.InventoryPositionMount,)),
)


def _make_item(*, uid: int, gid: int, action: int, value_int: int) -> ObjectItemInventory:
    return ObjectItemInventory(
        item=ObjectItem(uid=uid, gid=gid, effects=[ObjectEffect(action=action, value_int=value_int)])
    )


@pytest.fixture(autouse=True)
def patch_data_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    item_type_by_gid = {gid: _RING_TYPE_ID for gid in range(900, 910)}
    item_type_by_gid.update({gid: _DOFUS_TYPE_ID for gid in range(920, 940)})
    item_type_by_gid.update({item_type_id: item_type_id for item_type_id, _ in _EQUIPMENT_POSITION_CASES})
    item_type_by_gid[15] = 15
    fake_data_reader = SimpleNamespace(
        item_by_id={gid: SimpleNamespace(typeId=type_id) for gid, type_id in item_type_by_gid.items()},
        effect_by_id={
            _POWER_ACTION: SimpleNamespace(characteristic=CharacteristicEnum.POWER),
            _VITALITY_ACTION: SimpleNamespace(characteristic=CharacteristicEnum.VITALITY),
        },
    )
    monkeypatch.setattr(equipment_module, "DataReader", lambda: fake_data_reader)


def _make_behavior() -> AutoEquipmentFromInventoryBehavior:
    instance = object.__new__(AutoEquipmentFromInventoryBehavior)
    instance._state_lock = RLock()
    instance._logger = cast(BotLogger, Mock())
    instance.game_state = cast(
        GameState,
        SimpleNamespace(fight=SimpleNamespace(primary_and_second_elem=(EffectElement.STRENGTH, None))),
    )
    return instance


class TestBestLoadout:
    def test_keeps_equivalent_equipped_ring_when_another_ring_improves(self) -> None:
        behavior = _make_behavior()
        equipped_ring = _make_item(uid=1, gid=900, action=_POWER_ACTION, value_int=10)
        weak_ring = _make_item(uid=2, gid=901, action=_VITALITY_ACTION, value_int=1)
        equivalent_copy = _make_item(uid=3, gid=900, action=_POWER_ACTION, value_int=10)
        stronger_ring = _make_item(uid=4, gid=902, action=_POWER_ACTION, value_int=20)
        current = {
            CharacterInventoryPositionEnum.InventoryPositionRingLeft: equipped_ring,
            CharacterInventoryPositionEnum.InventoryPositionRingRight: weak_ring,
        }

        best = behavior._best_loadout(current, [equivalent_copy, stronger_ring])

        assert best[CharacterInventoryPositionEnum.InventoryPositionRingLeft].item.uid == 1
        assert best[CharacterInventoryPositionEnum.InventoryPositionRingRight].item.uid == 4

    def test_duplicate_gid_copy_does_not_fill_an_empty_second_slot(self) -> None:
        behavior = _make_behavior()
        equipped_ring = _make_item(uid=1, gid=900, action=_POWER_ACTION, value_int=10)
        duplicate_copy = _make_item(uid=2, gid=900, action=_POWER_ACTION, value_int=10)
        current = {CharacterInventoryPositionEnum.InventoryPositionRingLeft: equipped_ring}

        best = behavior._best_loadout(current, [duplicate_copy])

        assert best[CharacterInventoryPositionEnum.InventoryPositionRingLeft].item.gid == 900
        assert CharacterInventoryPositionEnum.InventoryPositionRingRight not in best

    def test_better_rolled_duplicate_gid_replaces_equipped_copy(self) -> None:
        behavior = _make_behavior()
        equipped_ring = _make_item(uid=1, gid=900, action=_POWER_ACTION, value_int=10)
        better_copy = _make_item(uid=2, gid=900, action=_POWER_ACTION, value_int=20)
        current = {CharacterInventoryPositionEnum.InventoryPositionRingLeft: equipped_ring}

        best = behavior._best_loadout(current, [better_copy])

        assert best[CharacterInventoryPositionEnum.InventoryPositionRingLeft].item.uid == 2
        assert CharacterInventoryPositionEnum.InventoryPositionRingRight not in best

    def test_picks_top_two_of_three_ring_candidates(self) -> None:
        behavior = _make_behavior()
        strong_ring = _make_item(uid=1, gid=900, action=_POWER_ACTION, value_int=10)
        medium_ring = _make_item(uid=2, gid=901, action=_VITALITY_ACTION, value_int=10)
        weak_ring = _make_item(uid=3, gid=902, action=_VITALITY_ACTION, value_int=1)

        best = behavior._best_loadout({}, [strong_ring, medium_ring, weak_ring])

        chosen_uids = {item.item.uid for item in best.values()}
        assert chosen_uids == {1, 2}
        assert (
            best[CharacterInventoryPositionEnum.InventoryPositionRingLeft].item.uid,
            best[CharacterInventoryPositionEnum.InventoryPositionRingRight].item.uid,
        ) != (3, 3)

    def test_picks_top_six_of_seven_dofus_candidates(self) -> None:
        behavior = _make_behavior()
        candidates = [
            _make_item(uid=100 + index, gid=920 + index, action=_POWER_ACTION, value_int=index)
            for index in range(7)
        ]

        best = behavior._best_loadout({}, candidates)

        chosen_uids = {item.item.uid for item in best.values()}
        assert len(chosen_uids) == 6
        assert 100 not in chosen_uids

    def test_currently_equipped_item_competes_with_candidates(self) -> None:
        behavior = _make_behavior()
        equipped_ring = _make_item(uid=1, gid=900, action=_POWER_ACTION, value_int=100)
        weak_candidate = _make_item(uid=2, gid=901, action=_VITALITY_ACTION, value_int=1)
        current = {CharacterInventoryPositionEnum.InventoryPositionRingLeft: equipped_ring}

        best = behavior._best_loadout(current, [weak_candidate])

        assert best[CharacterInventoryPositionEnum.InventoryPositionRingLeft].item.uid == 1

    def test_scales_to_many_dofus_candidates_without_exponential_blowup(self) -> None:
        behavior = _make_behavior()
        candidates = [
            _make_item(uid=200 + index, gid=920 + (index % 20), action=_POWER_ACTION, value_int=index)
            for index in range(20)
        ]

        started_at = time.monotonic()
        best = behavior._best_loadout({}, candidates)
        elapsed_seconds = time.monotonic() - started_at

        assert elapsed_seconds < 1.0
        assert len(best) == 6


class _FakeEventManager:
    def __init__(self) -> None:
        self.confirm_callback: Callable[[Any], None] | None = None
        self.cleared_origins: list[object] = []

    def on(self, msg_types: object, callback: Callable[[Any], None], **_kwargs: object) -> None:
        self.confirm_callback = callback

    def clear_listener_by_origin(self, originator: object) -> None:
        self.cleared_origins.append(originator)

    def clear_listener_by_origin_and_type(self, msg_type: Any, originator: object) -> None:
        self.cleared_origins.append((msg_type, originator))


class _FakeInventory:
    def __init__(self, objects_by_uid: dict[int, object]) -> None:
        self.objects_by_uid = objects_by_uid

    def remove_object(self, uid: int) -> None:
        self.objects_by_uid.pop(uid, None)


def _make_equip_behavior(
    *, pending_uid: int, position: CharacterInventoryPositionEnum
) -> tuple[AutoEquipmentFromInventoryBehavior, _FakeEventManager, list[object]]:
    behavior = _make_behavior()
    behavior._positions_by_uid = {pending_uid: position}
    behavior._pending_position = None
    pending_item = SimpleNamespace(position=CharacterInventoryPositionEnum.InventoryPositionNotEquiped)
    cast(Any, behavior.game_state).inventory = _FakeInventory({pending_uid: pending_item})
    fake_event_manager = _FakeEventManager()
    behavior.event_manager = cast(Any, fake_event_manager)
    behavior.send_message_delayed = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    finished_with: list[object] = []
    behavior.finish = lambda *args, **kwargs: finished_with.append((args, kwargs))  # type: ignore[method-assign]
    return behavior, fake_event_manager, finished_with


class TestEquipConfirmation:
    def test_object_movement_event_with_matching_position_confirms(self) -> None:
        position = CharacterInventoryPositionEnum.AccessoryPositionBoots
        behavior, event_manager, finished_with = _make_equip_behavior(pending_uid=1, position=position)

        behavior._equip_next()
        assert event_manager.confirm_callback is not None

        event_manager.confirm_callback(ObjectMovementEvent(object_uid=1, position=position))

        assert finished_with
        assert event_manager.cleared_origins == [behavior]
        assert behavior._pending_position is None

    def test_object_added_event_with_matching_position_confirms(self) -> None:
        position = CharacterInventoryPositionEnum.AccessoryPositionBoots
        behavior, event_manager, finished_with = _make_equip_behavior(pending_uid=1, position=position)

        behavior._equip_next()
        assert event_manager.confirm_callback is not None

        event_manager.confirm_callback(ObjectAddedEvent(object=ObjectItemInventory(position=position)))

        assert finished_with
        assert event_manager.cleared_origins == [behavior]
        assert behavior._pending_position is None

    def test_timeout_removes_inventory_item_and_finishes(self) -> None:
        position = CharacterInventoryPositionEnum.AccessoryPositionAmulet
        behavior, event_manager, finished_with = _make_equip_behavior(pending_uid=1, position=position)

        behavior._equip_next()
        behavior.on_timeout_inventory_weight_on_equipped(1, position)

        assert 1 not in behavior.game_state.inventory.objects_by_uid
        assert event_manager.cleared_origins == [
            (ObjectMovementEvent, behavior),
            (ObjectAddedEvent, behavior),
        ]
        assert len(finished_with) == 1

from collections.abc import Callable
from typing import cast
from unittest.mock import MagicMock

import pytest
from pytest import MonkeyPatch

from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import ObjectItem, ObjectItemInventory
from DBDofusUnity.datas.protos.non_obf.game.exchange_pb2 import (
    ExchangeObjectTransferAllFromInventoryRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.inventory_pb2 import InventoryWeightEvent
from DBDofusUnity.dofus_unity_reader.game_constants.inventory_position import (
    CharacterInventoryPositionEnum,
)
from DBDofusUnity.dofus_unity_reader.game_constants.item import ItemEnum
from google.protobuf.message import Message

from src.core.behaviors.storage.unloads.unload_in_bank_behavior import (
    UnloadInBankBehavior,
)
from src.core.events_manager.event_manager import EventManager
from tests.fixtures.bot_runtime import make_blocking_state_recovery
from tests.fixtures.game_state import GameStateContext

GRAISSE_GELATINEUSE_GID = int(ItemEnum.GRAISSE_GELATINEUSE)
POILS_DE_KERUBIM_GID = int(ItemEnum.POILS_DE_KERUBIM)


@pytest.fixture(autouse=True)
def _disable_listener_timers(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("src.core.events_manager.listener.Timer", MagicMock())


def _make_behavior(
    game_state_ctx: GameStateContext,
) -> tuple[UnloadInBankBehavior, list[Message]]:
    game_state = game_state_ctx.game_state
    game_state.inventory.weight_max = 1_000
    game_state.inventory.inventory_weight = 900
    event_manager = EventManager(_logger=game_state_ctx.logger)
    sent_messages: list[Message] = []
    event_manager.on_send_game_callback = sent_messages.append
    behavior = UnloadInBankBehavior(
        recovery=make_blocking_state_recovery(),
        event_manager=event_manager,
        game_state=game_state,
        _logger=game_state_ctx.logger,
        auto_trip_world_behavior=MagicMock(),
        enter_bank_chest_behavior=MagicMock(),
    )

    def run_timer_inline(range_time: tuple[float, float] | float, func: Callable[[], None]) -> None:
        del range_time
        func()

    behavior.run_timer = run_timer_inline
    return behavior, sent_messages


def _mock_of(child_behavior: object) -> MagicMock:
    return cast(MagicMock, child_behavior)


def _child_callback(child_behavior: object) -> Callable[..., None]:
    return cast(Callable[..., None], _mock_of(child_behavior).start.call_args.kwargs["callback"])


def _put_in_inventory(behavior: UnloadInBankBehavior, item_gid: int) -> None:
    behavior.game_state.inventory.objects_by_uid[item_gid] = ObjectItemInventory(
        item=ObjectItem(uid=item_gid, gid=item_gid, quantity=10),
        position=CharacterInventoryPositionEnum.InventoryPositionNotEquiped.value,
    )


def _enter_bank(behavior: UnloadInBankBehavior, callback: Callable[..., None] | None = None) -> None:
    behavior.start(callback=callback, parent=None)
    _child_callback(behavior.enter_bank_chest_behavior)(None)


def test_a_full_bag_is_transferred_with_a_single_request(
    game_state_ctx: GameStateContext,
) -> None:
    behavior, sent_messages = _make_behavior(game_state_ctx)
    _put_in_inventory(behavior, GRAISSE_GELATINEUSE_GID)
    _put_in_inventory(behavior, POILS_DE_KERUBIM_GID)

    _enter_bank(behavior)

    assert [type(message) for message in sent_messages] == [ExchangeObjectTransferAllFromInventoryRequest]


def test_the_chest_is_left_open_once_the_bag_is_empty(game_state_ctx: GameStateContext) -> None:
    behavior, sent_messages = _make_behavior(game_state_ctx)
    _put_in_inventory(behavior, GRAISSE_GELATINEUSE_GID)
    finished: list[str | None] = []

    _enter_bank(behavior, callback=finished.append)
    behavior.event_manager.process_msg(InventoryWeightEvent(inventory_weight=0, weight_max=1_000))

    assert [type(message) for message in sent_messages] == [ExchangeObjectTransferAllFromInventoryRequest]
    assert finished == [None]


def test_a_silent_server_still_finishes_the_unload(game_state_ctx: GameStateContext) -> None:
    behavior, sent_messages = _make_behavior(game_state_ctx)
    _put_in_inventory(behavior, GRAISSE_GELATINEUSE_GID)
    finished: list[str | None] = []

    _enter_bank(behavior, callback=finished.append)
    behavior.on_transfer_all_timeout()

    assert [type(message) for message in sent_messages] == [ExchangeObjectTransferAllFromInventoryRequest]
    assert finished == [None]


def test_a_bag_holding_nothing_transferable_sends_nothing(
    game_state_ctx: GameStateContext,
) -> None:
    behavior, sent_messages = _make_behavior(game_state_ctx)
    finished: list[str | None] = []

    _enter_bank(behavior, callback=finished.append)

    assert sent_messages == []
    assert finished == [None]

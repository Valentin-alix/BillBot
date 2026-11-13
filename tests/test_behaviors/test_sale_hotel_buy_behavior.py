from typing import cast
from unittest.mock import MagicMock

from exchange_pb2 import (
    ExchangeBidHouseBuyResultEvent,
    ExchangeTypesExchangerDescriptionForUserEvent,
    ExchangeTypesItemsExchangerDescriptionForUserEvent,
)

from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import ObjectItem, ObjectItemInventory
from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.game_constants.item import CategoryItemEnum, ItemEnum
from pytest import MonkeyPatch

from src.core.behaviors.sale_hotel.sale_hotel_buy_behavior import SaleHotelBuyBehavior
from src.core.engine.economy.sale_hotel import ItemToBuyInfo
from src.core.events_manager.event_manager import EventManager
from tests.fixtures.bot_runtime import make_blocking_state_recovery
from tests.fixtures.game_state import GameStateContext

AMULETTE_AKWADALA_GID = int(ItemEnum.AMULETTE_AKWADALA)
LISTING_UID = 987_654
BOUGHT_OBJECT_UID = 111_222


def _make_behavior(game_state_ctx: GameStateContext) -> SaleHotelBuyBehavior:
    game_state = game_state_ctx.game_state
    game_state.inventory.kamas = 1_000_000
    event_manager = EventManager(_logger=game_state_ctx.logger)
    event_manager.on_send_game_callback = lambda msg: None
    behavior = SaleHotelBuyBehavior(
        recovery=make_blocking_state_recovery(),
        event_manager=event_manager,
        game_state=game_state,
        _logger=game_state_ctx.logger,
        enter_sale_hotel_behavior=MagicMock(),
    )
    behavior.run_timer = lambda range_time, func: func()
    return behavior


def _mock_of(child_behavior: object) -> MagicMock:
    return cast(MagicMock, child_behavior)


def _start_and_search(
    behavior: SaleHotelBuyBehavior,
) -> list[tuple[str | None, list[ObjectItemInventory]]]:
    finished: list[tuple[str | None, list[ObjectItemInventory]]] = []

    def on_finished(error_code: str | None, bought_item: list[ObjectItemInventory]) -> None:
        finished.append((error_code, bought_item))

    behavior.start(
        item_infos_to_buy=[ItemToBuyInfo(item_gid=AMULETTE_AKWADALA_GID, max_kamas=1_000_000)],
        category=CategoryItemEnum.EQUIPMENT,
        callback=on_finished,
        parent=None,
    )
    enter_callback = _mock_of(behavior.enter_sale_hotel_behavior).start.call_args.kwargs["callback"]
    enter_callback(None, npc_info=MagicMock())

    type_id = DataReader().item_by_id[AMULETTE_AKWADALA_GID].typeId
    behavior.event_manager.process_msg(
        ExchangeTypesExchangerDescriptionForUserEvent(object_type=type_id, type_description=[])
    )

    behavior.event_manager.process_msg(
        ExchangeTypesItemsExchangerDescriptionForUserEvent(
            object_gid=AMULETTE_AKWADALA_GID,
            object_type=type_id,
            item_descriptions=[
                ExchangeTypesItemsExchangerDescriptionForUserEvent.BidExchangerObject(
                    uid=LISTING_UID,
                    gid=AMULETTE_AKWADALA_GID,
                    type=type_id,
                    effects=[],
                    prices=[100, 0, 0, 0],
                )
            ],
        )
    )
    return finished


def test_a_buy_result_with_no_matching_inventory_item_does_not_hang_the_behavior(
    game_state_ctx: GameStateContext, monkeypatch: MonkeyPatch
) -> None:
    del monkeypatch
    behavior = _make_behavior(game_state_ctx)
    finished = _start_and_search(behavior)

    behavior.event_manager.process_msg(
        ExchangeBidHouseBuyResultEvent(bid_item_uid=LISTING_UID, bought=True)
    )

    assert finished
    error_code, bought_item = finished[0]
    assert error_code is None
    assert bought_item == []


def test_a_buy_result_with_the_item_already_in_inventory_reports_it_bought(
    game_state_ctx: GameStateContext, monkeypatch: MonkeyPatch
) -> None:
    del monkeypatch
    behavior = _make_behavior(game_state_ctx)
    finished = _start_and_search(behavior)

    behavior.game_state.inventory.add_object(
        ObjectItemInventory(item=ObjectItem(uid=BOUGHT_OBJECT_UID, gid=AMULETTE_AKWADALA_GID, quantity=1))
    )
    behavior.event_manager.process_msg(
        ExchangeBidHouseBuyResultEvent(bid_item_uid=LISTING_UID, bought=True)
    )

    assert finished
    error_code, bought_item = finished[0]
    assert error_code is None
    assert [item.item.uid for item in bought_item] == [BOUGHT_OBJECT_UID]


def test_a_second_item_search_does_not_leave_a_stale_listener_behind(
    game_state_ctx: GameStateContext, monkeypatch: MonkeyPatch
) -> None:
    del monkeypatch
    behavior = _make_behavior(game_state_ctx)

    behavior._current_item_infos = [ItemToBuyInfo(item_gid=AMULETTE_AKWADALA_GID, max_kamas=1_000_000)]
    behavior.buy_next_item()
    behavior._current_item_infos = [ItemToBuyInfo(item_gid=AMULETTE_AKWADALA_GID, max_kamas=1_000_000)]
    behavior.buy_next_item()

    listeners = behavior.event_manager.listeners_by_type_msg[ExchangeTypesItemsExchangerDescriptionForUserEvent]
    assert len(listeners) == 1

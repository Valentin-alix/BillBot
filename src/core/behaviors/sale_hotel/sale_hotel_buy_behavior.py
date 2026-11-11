from collections import defaultdict
from dataclasses import dataclass, field
from functools import partial

from DBDofusUnity.datas.protos.non_obf.game.exchange_pb2 import (
    ExchangeBidHouseBuyRequest,
    ExchangeBidHouseBuyResultEvent,
    ExchangeBidHouseSearchRequest,
    ExchangeBidHouseTypeRequest,
    ExchangeTypesExchangerDescriptionForUserEvent,
    ExchangeTypesItemsExchangerDescriptionForUserEvent,
)

from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import ObjectItemInventory
from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.game_constants.item import CategoryItemEnum
from DBDofusUnity.dofus_unity_reader.game_constants.npc import NpcInfo
from src.core.behaviors.recovery_behavior import RecoverableBehavior
from src.core.behaviors.sale_hotel.enter_sale_hotel_behavior import (
    EnterSaleHotelBehavior,
)
from src.core.engine.economy.sale_hotel import ItemToBuyInfo
from src.services.human_timings import HumanTimingsService


@dataclass
class SaleHotelBuyBehavior(RecoverableBehavior):
    enter_sale_hotel_behavior: EnterSaleHotelBehavior

    _current_grouped_item_infos_by_type: dict[int, list[ItemToBuyInfo]] = field(
        init=False, default_factory=dict[int, list[ItemToBuyInfo]]
    )
    _current_item_infos: list[ItemToBuyInfo] = field(init=False, default_factory=list[ItemToBuyInfo])
    _bought_item: list[ObjectItemInventory] = field(init=False, default_factory=list[ObjectItemInventory])

    def run(self, item_infos_to_buy: list[ItemToBuyInfo], category: CategoryItemEnum) -> None:
        self.init_recovery_listeners()
        self.ensure_free_to_act(
            lambda: self.start_buying(item_infos_to_buy=item_infos_to_buy, category=category)
        )

    def start_buying(self, item_infos_to_buy: list[ItemToBuyInfo], category: CategoryItemEnum):
        self._bought_item.clear()
        self._current_item_infos.clear()
        self._current_grouped_item_infos_by_type = defaultdict(list)
        for item_info in item_infos_to_buy:
            self._current_grouped_item_infos_by_type[
                DataReader().item_by_id[item_info.item_gid].typeId
            ].append(item_info)

        self.enter_sale_hotel_behavior.start(
            callback=self.on_enter_sale_hotel_behavior_finished,
            parent=self,
            category=category,
        )

    def on_enter_sale_hotel_behavior_finished(self, error_code: str | None, npc_info: NpcInfo):
        self.raise_if_error(error_code)
        self.buy_next_type_item()

    def buy_next_type_item(self):
        if len(self._current_grouped_item_infos_by_type) == 0:
            return self.exit_and_finish()

        type_id, gids = self._current_grouped_item_infos_by_type.popitem()
        self._current_item_infos = gids
        self.event_manager.on(
            ExchangeTypesExchangerDescriptionForUserEvent,
            self.on_exchange_types_exchanger_description_for_user_event,
            originator=self,
            once=True,
            override_on_self=True,
        )
        if self.game_state.sale_hotel.current_search_type_id is not None:
            req = ExchangeBidHouseTypeRequest(
                type_id=self.game_state.sale_hotel.current_search_type_id, follow=False
            )
            self.event_manager.send(req)
        req = ExchangeBidHouseTypeRequest(type_id=type_id, follow=True)
        self.send_message_delayed(
            req,
            HumanTimingsService().get_timing_sale_hotel_review(),
        )

    def on_exchange_types_exchanger_description_for_user_event(
        self, msg: ExchangeTypesExchangerDescriptionForUserEvent
    ):
        self.buy_next_item()

    def buy_next_item(self):
        if len(self._current_item_infos) == 0:
            return self.buy_next_type_item()

        item_info = self._current_item_infos.pop()

        self.event_manager.on(
            ExchangeTypesItemsExchangerDescriptionForUserEvent,
            callback=partial(
                self.on_exchange_types_items_exchanger_description_for_user_event,
                item_info=item_info,
            ),
            originator=self,
            once=True,
            override_on_self=True,
        )
        if self.game_state.sale_hotel.current_search_item_gid is not None:
            req = ExchangeBidHouseSearchRequest(
                object_gid=self.game_state.sale_hotel.current_search_item_gid,
                follow=False,
            )
            self.event_manager.send(req)
        req = ExchangeBidHouseSearchRequest(object_gid=item_info.item_gid, follow=True)
        self.run_timer(
            HumanTimingsService().get_timing_sale_hotel_review(),
            lambda: self.event_manager.send(req),
        )

    def on_exchange_types_items_exchanger_description_for_user_event(
        self,
        msg: ExchangeTypesItemsExchangerDescriptionForUserEvent,
        item_info: ItemToBuyInfo,
    ):
        if len(msg.item_descriptions) == 0:
            return self.buy_next_item()

        cheaper_item = min(msg.item_descriptions, key=lambda elem: elem.prices[0])

        if not item_info.is_valid_item_to_buy(self.game_state.inventory.kamas, cheaper_item):
            self.logger.info(
                f"Skipping item_gid={item_info.item_gid}: cheapest listing "
                f"{cheaper_item.prices[0]} kamas exceeds available "
                f"{self.game_state.inventory.kamas} kamas or the {item_info.max_kamas} cap, "
                "or its roll is too low"
            )
            return self.buy_next_item()

        self.event_manager.on(
            ExchangeBidHouseBuyResultEvent,
            callback=partial(self.exchange_bid_house_buy_result_event, gid=item_info.item_gid),
            originator=self,
            once=True,
            override_on_self=True,
        )

        req = ExchangeBidHouseBuyRequest(
            bid_item_uid=cheaper_item.uid, quantity=1, price=cheaper_item.prices[0]
        )
        self.run_timer(
            HumanTimingsService().get_timing_sale_hotel_price_change(),
            lambda: self.event_manager.send(req),
        )

    def exchange_bid_house_buy_result_event(self, msg: ExchangeBidHouseBuyResultEvent, gid: int):
        if msg.bought:
            related_item_inv = self.game_state.inventory.get_object_item_by_gid(gid)
            if related_item_inv is None:
                self.logger.error(
                    f"Server confirmed purchase of item_gid={gid} (bid_item_uid={msg.bid_item_uid}) "
                    "but it is not in inventory; kamas were spent but the item could not be tracked"
                )
            else:
                self.logger.info(f"SUCESS, item bought: item_gid={gid}")
                self._bought_item.append(related_item_inv)
        self.buy_next_item()

    def exit_and_finish(self):
        self.run_timer(
            HumanTimingsService().get_timing_base_action(),
            lambda: self.finish(bought_item=self._bought_item),
        )

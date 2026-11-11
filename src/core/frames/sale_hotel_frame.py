from functools import cache

import datetime
from dataclasses import dataclass

from DBDofusUnity.datas.protos.non_obf.game.exchange_pb2 import (
    ExchangeBidHouseItemAddedEvent,
    ExchangeBidHouseItemRemovedEvent,
    ExchangeBidHouseSearchRequest,
    ExchangeBidHouseTypeRequest,
    ExchangeBidPriceEvent,
    ExchangeBidSellerStartedEvent,
    ObjectAveragePricesEvent,
)
from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.game_constants.item import (
    CategoryItemEnum,
    ItemTypeEnum,
)
from DBDofusUnity.dofus_unity_reader.game_constants.npc import (
    ASTRUB_SALE_HOTEL_COM_SELL_NPC,
    ASTRUB_SALE_HOTEL_EQUIPMENT_BUY_NPC,
    ASTRUB_SALE_HOTEL_RES_SELL_NPC,
    BONTA_SALE_HOTEL_COM_SELL_NPC,
    BONTA_SALE_HOTEL_EQUIP_SELL_NPC,
    BONTA_SALE_HOTEL_RES_SELL_NPC,
    NpcInfo,
)

from src.controller.game_data import GameDataController
from src.core.config import get_time_beween_sale_hotel_prices
from src.core.engine.items.item import get_gatherer_item_gids
from src.core.frames.frame import Frame


@cache
def get_sellable_items() -> set[int]:
    return (
        get_gatherer_item_gids()
        | DataReader().item_ids_by_type_id[ItemTypeEnum.SUBSTRAT]
        | DataReader().item_ids_by_type_id[ItemTypeEnum.ALLIAGE]
    )


SALE_HOTELS_BY_CATEGORY: dict[CategoryItemEnum, list[NpcInfo]] = {
    CategoryItemEnum.RESOURCES: [
        BONTA_SALE_HOTEL_RES_SELL_NPC,
        ASTRUB_SALE_HOTEL_RES_SELL_NPC,
    ],
    CategoryItemEnum.CONSUMABLES: [
        BONTA_SALE_HOTEL_COM_SELL_NPC,
        ASTRUB_SALE_HOTEL_COM_SELL_NPC,
    ],
    CategoryItemEnum.EQUIPMENT: [
        ASTRUB_SALE_HOTEL_EQUIPMENT_BUY_NPC,
        BONTA_SALE_HOTEL_EQUIP_SELL_NPC,
    ],
}


@dataclass
class SaleHotelFrame(Frame):
    def __post_init__(self):
        self.game_info_signals.disconnected.connect(self.game_state.sale_hotel.clear_state)
        self.event_manager.on(
            ExchangeBidSellerStartedEvent,
            self.on_exchange_bid_seller_started_event,
            originator=self,
            priority=self.priority,
        )
        self.event_manager.on(
            ExchangeBidHouseItemAddedEvent,
            self.on_exchange_bid_house_item_added_event,
            originator=self,
            priority=self.priority,
        )
        self.event_manager.on(
            ExchangeBidHouseItemRemovedEvent,
            self.on_exchange_bid_house_item_removed_event,
            originator=self,
            priority=self.priority,
        )
        self.event_manager.on(
            ExchangeBidHouseSearchRequest,
            self.on_exchange_bid_house_search_request,
            originator=self,
            priority=self.priority,
        )
        self.event_manager.on(
            ObjectAveragePricesEvent,
            self.on_object_average_prices_event,
            originator=self,
            priority=self.priority,
        )
        self.event_manager.on(
            ExchangeBidPriceEvent,
            self.on_exchange_bid_price_event,
            originator=self,
            priority=self.priority,
        )
        self.event_manager.on(
            ExchangeBidHouseTypeRequest,
            self.on_exchange_bid_house_type_request,
            originator=self,
            priority=self.priority,
        )

    def on_exchange_bid_seller_started_event(self, msg: ExchangeBidSellerStartedEvent):
        self.game_state.sale_hotel.timedelta_for_next_sale_hotel_prices = get_time_beween_sale_hotel_prices()
        self.game_state.sale_hotel.last_time_updated_prices = datetime.datetime.now()
        self.game_state.sale_hotel.bid_seller_condition = msg.selling_conditions
        self.game_state.sale_hotel.items_in_sale = list(msg.items)
        GameDataController().update_hdv(
            self.game_state.player.server_id,
            self.game_state.player.character_id,
            {item.item.uid: (item.item.gid, item.item.quantity, item.price) for item in msg.items},
        )

    def on_exchange_bid_house_item_added_event(self, msg: ExchangeBidHouseItemAddedEvent):
        GameDataController().add_gid_quantity_by_uid_by_player_id(
            self.game_state.player.server_id,
            self.game_state.player.character_id,
            msg.item.gid,
            msg.item.quantity,
            msg.price,
            msg.item.uid,
        )

    def on_exchange_bid_house_item_removed_event(self, msg: ExchangeBidHouseItemRemovedEvent):
        GameDataController().remove_uid_for_player_id(
            self.game_state.player.server_id,
            self.game_state.player.character_id,
            msg.sell_id,
        )

    def on_exchange_bid_house_type_request(self, msg: ExchangeBidHouseTypeRequest):
        if msg.follow:
            self.game_state.sale_hotel.current_search_type_id = msg.type_id
        else:
            self.game_state.sale_hotel.current_search_type_id = None

    def on_exchange_bid_house_search_request(self, msg: ExchangeBidHouseSearchRequest):
        if msg.follow:
            self.game_state.sale_hotel.current_search_item_gid = msg.object_gid
        else:
            self.game_state.sale_hotel.current_search_item_gid = None

    def on_object_average_prices_event(self, msg: ObjectAveragePricesEvent):
        GameDataController().add_multiple_avg_price_by_gid(
            self.game_state.player.server_id,
            [
                (
                    object_average_price.average_price,
                    object_average_price.object_gid,
                )
                for object_average_price in msg.objects_average_prices
            ],
        )

    def on_exchange_bid_price_event(self, msg: ExchangeBidPriceEvent):
        GameDataController().add_multiple_avg_price_by_gid(
            self.game_state.player.server_id,
            [(msg.average_price, msg.object_gid)],
        )

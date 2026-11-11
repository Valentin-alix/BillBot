import random
from collections import defaultdict
from statistics import median
from typing import cast

from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import (
    ObjectEffect,
    ObjectItem,
    ObjectItemInventory,
)
from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.game_constants.item import CategoryItemEnum
from DBDofusUnity.dofus_unity_reader.game_constants.sale_hotel import (
    QUANTITY_INDEX_BY_QUANTITY,
    QuantityEnum,
    QuantityIndex,
)
from DBDofusUnity.datas.protos.non_obf.game.exchange_pb2 import ExchangeTypesItemsExchangerDescriptionForUserEvent
from pydantic import BaseModel

from src.core.config import MAX_QUANTITY_ON_SELL
from src.core.engine.items.item import get_protector_drop_item_ids
from src.core.engine.weights.harvester.weight_collectable import (
    get_weight_collectable_for_sale_hotel,
)
from src.core.frames.sale_hotel_frame import get_sellable_items
from src.services.logging_utils.loggers import BotLogger


def get_item_gids_to_sell(
    can_access_guild_chest: bool,
    is_sub: bool,
    bank_object_by_gid: dict[int, ObjectItemInventory],
    item_sell_quantity_by_gid: defaultdict[int, int],
    category: CategoryItemEnum,
    logger: BotLogger,
    avg_price_by_gid: dict[int, float],
    guild_chest_items_by_gid: dict[int, ObjectItemInventory] | None = None,
):
    if can_access_guild_chest:
        if guild_chest_items_by_gid is None:
            raise ValueError("guild_chest_items_by_gid required when can_access_guild_chest=True")
        item_by_gid_in_storage = guild_chest_items_by_gid
    else:
        item_by_gid_in_storage = bank_object_by_gid

    logger.info(f"count item in storage : {len(item_by_gid_in_storage)}")

    def is_valid_item_to_sell(gid: int, object_item: ObjectItemInventory, is_bank_item: bool):
        item_data = DataReader().item_by_id[gid]
        if not item_data.typeId:
            return False
        if not (is_sub or (item_data.level or 0) <= 60):
            return False
        if not is_bank_item and gid not in get_sellable_items():
            return False
        if not is_interesting_item_to_sell(object_item.item, avg_price_by_gid):
            return False
        return DataReader().item_type_by_id[item_data.typeId].categoryId == category

    item_gids_to_sell = [
        item_gid
        for item_gid, item in item_by_gid_in_storage.items()
        if is_valid_item_to_sell(item_gid, item, not can_access_guild_chest)
    ]

    logger.info(f"count item to sell : {len(item_gids_to_sell)}")

    item_gids_to_sell.sort(
        key=lambda item_gid: (
            random.uniform(0.95, 1.05)
            * get_weight_collectable_for_sale_hotel(
                item_gid,
                avg_price_by_gid,
                item_by_gid_in_storage,
                item_sell_quantity_by_gid,
            )
        ),
        reverse=True,
    )

    return item_gids_to_sell


def is_interesting_item_to_sell(
    object_item: ObjectItem,
    avg_price_by_gid: dict[int, float],
    available_quantity: int | None = None,
):
    qty = available_quantity if available_quantity is not None else object_item.quantity
    if qty <= 0:
        return False
    avg_price = avg_price_by_gid.get(object_item.gid, 1)
    if avg_price < 500 and qty < 100 or avg_price < 5_000 and qty < 10:
        return False
    return True


def choose_quantity_to_sell(
    item: ObjectItem,
    avg_price_by_gid: dict[int, float],
    available_quantity: int | None = None,
):
    qty = available_quantity if available_quantity is not None else item.quantity
    if item.gid in get_protector_drop_item_ids():
        return QuantityEnum.VALUE_1, QuantityIndex.ONE

    avg_price = avg_price_by_gid.get(item.gid, 1)
    if qty >= 1000 and avg_price < 1_000:
        quantity_to_sell = QuantityEnum.VALUE_1000
        quantity_index = QuantityIndex.THOUSAND
    elif qty >= 100 and avg_price < 10_000:
        quantity_to_sell = QuantityEnum.VALUE_100
        quantity_index = QuantityIndex.HUNDRED
    elif qty >= 10 and avg_price < 100_000:
        quantity_to_sell = QuantityEnum.VALUE_10
        quantity_index = QuantityIndex.TEN
    else:
        quantity_to_sell = QuantityEnum.VALUE_1
        quantity_index = QuantityIndex.ONE

    return quantity_to_sell, quantity_index


def get_max_quantity_sell(gid: int) -> int:
    if gid in get_protector_drop_item_ids():
        return 1
    return MAX_QUANTITY_ON_SELL


PRICE_LOW_RATIO = 0.5
PRICE_HIGH_RATIO = 2.0


def get_price_for_sale_hotel(
    min_prices: list[int],
    target_lot: QuantityEnum,
) -> int:
    unit_prices: list[float | None] = []
    valid_indexes: list[int] = []

    for index, (current_price, current_lot) in enumerate(zip(min_prices, QuantityEnum)):
        if current_price > 0:
            unit_prices.append(current_price / current_lot)
            valid_indexes.append(index)
        else:
            unit_prices.append(None)

    if not valid_indexes:
        return 0

    filtered_unit_prices: list[float] = [cast(float, unit_prices[index]) for index in valid_indexes]
    median_raw = median(filtered_unit_prices)

    accepted_indexes: list[int] = []

    for index in valid_indexes:
        unit_price = cast(float, unit_prices[index])
        ratio = unit_price / median_raw
        if PRICE_LOW_RATIO <= ratio <= PRICE_HIGH_RATIO:
            accepted_indexes.append(index)

    if not accepted_indexes:
        closest_index = min(
            valid_indexes,
            key=lambda index: abs(cast(float, unit_prices[index]) - median_raw),
        )
        accepted_indexes = [closest_index]

    cleaned_unit_price = median([cast(float, unit_prices[index]) for index in accepted_indexes])

    lot_index_by_quantity = QUANTITY_INDEX_BY_QUANTITY[target_lot]
    curr_min_price_lot = (
        min_prices[lot_index_by_quantity] if min_prices[lot_index_by_quantity] > 0 else float("inf")
    )

    return int(min(cleaned_unit_price * target_lot, curr_min_price_lot))


class ItemToBuyInfo(BaseModel):
    item_gid: int
    max_kamas: int

    def is_valid_item_to_buy(
        self,
        kamas: int,
        bid_object: ExchangeTypesItemsExchangerDescriptionForUserEvent.BidExchangerObject,
    ) -> bool:
        if bid_object.prices[0] == 0 or bid_object.prices[0] > kamas or bid_object.prices[0] > self.max_kamas:
            return False
        bid_object_effect_by_id: dict[int, ObjectEffect] = {
            object_effect.action: object_effect for object_effect in bid_object.effects
        }
        for item_effect in DataReader().get_item_effects_by_gid(self.item_gid):
            object_effect = bid_object_effect_by_id.get(item_effect.effectId)
            if (
                object_effect
                and object_effect.HasField("value_int")
                and object_effect.value_int < item_effect.diceNum
            ):
                return False

        return True

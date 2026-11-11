from dataclasses import dataclass, field

from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import ObjectItemInventory
from DBDofusUnity.datas.protos.non_obf.game.exchange_pb2 import (
    ExchangeObjectMoveRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.guild_chest_pb2 import (
    GuildChestCurrentListenersAddEvent,
    GuildChestTabSelectRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.inventory_pb2 import (
    InventoryWeightEvent,
)
from DBDofusUnity.dofus_unity_reader.models.datas.recipe_root import RecipeItem

from src.core.behaviors.storage.enter_chests.enter_guild_chest_behavior import (
    EnterGuildChestBehavior,
)
from src.core.behaviors.storage.loads.recipe_loader_behavior import (
    RecipeLoaderBehavior,
)
from src.services.human_timings import HumanTimingsService
from src.core.states.guild_chest_state import get_gids_by_tab


@dataclass
class IngredientsInfo:
    gid: int
    quantity: int
    tab: int


@dataclass(frozen=True)
class ReservedIngredient:
    gid: int
    quantity: int
    tab: int


@dataclass
class CurrentRecipeLoad:
    recipe: RecipeItem
    quantity: int
    reservations: tuple[ReservedIngredient, ...]


@dataclass
class LoadRecipeFromGuildChestBehavior(RecipeLoaderBehavior):
    enter_guild_chest_behavior: EnterGuildChestBehavior
    _current_recipe_load: CurrentRecipeLoad | None = field(init=False, default=None)

    def enter_storage(self) -> None:
        self.enter_guild_chest_behavior.start(
            callback=self.on_entered_storage,
            parent=self,
        )

    def on_entered_storage(self, error_code: str | None) -> None:
        if error_code is not None:
            self.logger.error(error_code)
            return self.finish(
                error_code=error_code,
                loaded_recipes_infos=self._loaded_recipes_infos,
                remaining_recipes=self._remaining_recipes,
            )

        all_ingredient_ids: set[int] = {
            ingredient_id for recipe in self._remaining_recipes for ingredient_id in recipe.ingredientIds
        }
        storage = self.game_state.guild_chest.storage
        tabs_to_discover = tuple(
            tab
            for tab, item_gids in get_gids_by_tab().items()
            if any(
                ingredient_id in item_gids and not storage.tab_exists(tab)
                for ingredient_id in all_ingredient_ids
            )
        )
        self.logger.info(f"Tabs to discover : {set(tabs_to_discover)}")
        self.discover_tabs(tabs_to_discover, 0)

    def discover_tabs(self, tabs: tuple[int, ...], index: int) -> None:
        if index >= len(tabs):
            return self.load_recipe()

        self.event_manager.on(
            GuildChestCurrentListenersAddEvent,
            lambda _: self.discover_tabs(tabs, index + 1),
            once=True,
            originator=self,
        )
        tab = tabs[index]
        self.logger.info(f"Gonna discover tab {tab}")
        return self.send_message_delayed(
            GuildChestTabSelectRequest(tab_number=tab),
            HumanTimingsService().get_timing_short_action(),
        )

    def get_storage_objects_by_gid(self) -> dict[int, ObjectItemInventory]:
        return self.game_state.guild_chest.storage.get_storage_objects_by_gid()

    def reserve_ingredients_for_recipe(self, recipe: RecipeItem, max_possible_result_quantity: int) -> None:
        storage = self.game_state.guild_chest.storage
        reservations: list[ReservedIngredient] = []
        for ingredient_id, quantity in zip(recipe.ingredientIds, recipe.quantities):
            tab = storage.get_tab_for_gid(ingredient_id)
            if tab is None:
                continue
            total_quantity = quantity * max_possible_result_quantity
            storage.reserve_quantity(
                tab,
                ingredient_id,
                total_quantity,
                self.game_state.player.character_name,
            )
            reservations.append(
                ReservedIngredient(
                    gid=ingredient_id,
                    quantity=total_quantity,
                    tab=tab,
                )
            )
        self._current_recipe_load = CurrentRecipeLoad(
            recipe=recipe,
            quantity=max_possible_result_quantity,
            reservations=tuple(reservations),
        )

    def load_ingredients_for_recipe(self, recipe: RecipeItem, max_possible_result_quantity: int) -> None:
        storage = self.game_state.guild_chest.storage
        ingredients_infos: list[IngredientsInfo] = []
        for ingredient_id, quantity in zip(recipe.ingredientIds, recipe.quantities):
            tab = storage.get_tab_for_gid(ingredient_id)
            if tab is not None:
                ingredients_infos.append(
                    IngredientsInfo(
                        gid=ingredient_id,
                        quantity=quantity,
                        tab=tab,
                    )
                )

        ingredients_infos.sort(
            key=lambda ingredient_info: (
                ingredient_info.tab == self.game_state.guild_chest.tab_number,
                ingredient_info.tab,
            )
        )
        self.load_ingredient(tuple(ingredients_infos), 0, max_possible_result_quantity)

    def load_ingredient(
        self,
        ingredients_infos: tuple[IngredientsInfo, ...],
        ingredient_index: int,
        max_possible_result_quantity: int,
    ) -> None:
        if ingredient_index >= len(ingredients_infos):
            return self.load_recipe()

        ingredient_info = ingredients_infos[ingredient_index]
        if self.game_state.guild_chest.tab_number != ingredient_info.tab:
            self.event_manager.on(
                GuildChestCurrentListenersAddEvent,
                lambda _: self.on_tab_of_item_to_load(
                    ingredient_info,
                    ingredients_infos,
                    ingredient_index,
                    max_possible_result_quantity,
                ),
                once=True,
                originator=self,
            )
            return self.run_timer(
                HumanTimingsService().get_timing_short_action(),
                lambda: self.event_manager.send(GuildChestTabSelectRequest(tab_number=ingredient_info.tab)),
            )
        self.on_tab_of_item_to_load(
            ingredient_info,
            ingredients_infos,
            ingredient_index,
            max_possible_result_quantity,
        )

    def on_tab_of_item_to_load(
        self,
        ingredient_info: IngredientsInfo,
        ingredients_infos: tuple[IngredientsInfo, ...],
        ingredient_index: int,
        max_possible_result_quantity: int,
    ) -> None:
        self.event_manager.on(
            InventoryWeightEvent,
            callback=lambda _: self.on_ingredient_loaded(
                ingredient_info,
                ingredients_infos,
                ingredient_index,
                max_possible_result_quantity,
            ),
            originator=self,
            once=True,
        )
        self.logger.info(f"Current tab : {self.game_state.guild_chest.tab_number}")
        self.logger.info(f"Tab of item {ingredient_info.tab}")

        item = self.game_state.guild_chest.storage.get_item_by_gid(
            self.game_state.guild_chest.tab_number,
            ingredient_info.gid,
        )
        if item is None:
            self.logger.error(
                f"Item {ingredient_info.gid} not found in tab {self.game_state.guild_chest.tab_number}. Aborting recipe {self._current_recipe_load.recipe.resultId if self._current_recipe_load else 'unknown'}."
            )
            return self.abort_current_recipe()

        req = ExchangeObjectMoveRequest(
            object_uid=item.item.uid,
            quantity=-ingredient_info.quantity * max_possible_result_quantity,
        )
        self.send_message_delayed(req, HumanTimingsService().get_timing_short_action())

    def on_ingredient_loaded(
        self,
        ingredient_info: IngredientsInfo,
        ingredients_infos: tuple[IngredientsInfo, ...],
        ingredient_index: int,
        max_possible_result_quantity: int,
    ) -> None:
        total_quantity = ingredient_info.quantity * max_possible_result_quantity
        self.game_state.guild_chest.storage.release_reservation(
            ingredient_info.tab,
            ingredient_info.gid,
            total_quantity,
            self.game_state.player.character_name,
        )
        self.load_ingredient(
            ingredients_infos,
            ingredient_index + 1,
            max_possible_result_quantity,
        )

    def abort_current_recipe(self) -> None:
        if self._current_recipe_load is None:
            return self.load_recipe()

        self.logger.warning(
            f"Aborting recipe {self._current_recipe_load.recipe.resultId} - missing ingredients"
        )

        for reservation in self._current_recipe_load.reservations:
            self.game_state.guild_chest.storage.release_reservation(
                reservation.tab,
                reservation.gid,
                reservation.quantity,
                self.game_state.player.character_name,
            )

        if self._current_recipe_load.recipe in self._remaining_recipes:
            self._remaining_recipes.remove(self._current_recipe_load.recipe)

        if self._current_recipe_load.recipe in [recipe for recipe, _ in self._loaded_recipes_infos]:
            self._loaded_recipes_infos = [
                (recipe, qty)
                for recipe, qty in self._loaded_recipes_infos
                if recipe != self._current_recipe_load.recipe
            ]

        self._current_recipe_load = None

        self.load_recipe()

    def clear_behavior(self) -> None:
        self.game_state.guild_chest.storage.clear_all_reservations_for_bot(
            self.game_state.player.character_name,
        )
        super().clear_behavior()

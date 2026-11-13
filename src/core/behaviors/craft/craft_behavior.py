from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial

from DBDofusUnity.datas.protos.non_obf.game.exchange_pb2 import (
    ExchangeCraftCountModifiedEvent,
    ExchangeCraftCountRequest,
    ExchangeCraftStartedEvent,
    ExchangeReadyRequest,
    ExchangeSetCraftRecipeRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.inventory_pb2 import (
    InventoryWeightEvent,
)
from DBDofusUnity.dofus_unity_reader.data_center.i18n import I18N
from DBDofusUnity.dofus_unity_reader.data_center.map_reader import MapReader
from DBDofusUnity.dofus_unity_reader.game_constants.skill import MAP_IDS_BY_SKILL
from DBDofusUnity.dofus_unity_reader.grid.map_point import MapPoint
from DBDofusUnity.dofus_unity_reader.models.datas.recipe_root import RecipeItem
from src.core.behaviors.interactives.interactive_behavior import InteractiveBehavior
from src.core.behaviors.movements.auto_trip.auto_trip_smart_behavior import (
    AutoTripSmartBehavior,
)
from src.core.behaviors.recovery_behavior import RecoverableBehavior
from src.core.behaviors.storage.loads.load_recipe_behavior import LoadRecipeBehavior
from src.core.engine.crafts.recipes import get_max_possible_result_quantity, get_max_result_quantity
from src.core.engine.movements.map.map_tools import MapTools
from src.core.engine.movements.map.path_finding.path_finding import Pathfinding
from src.core.states.dialog_state import OpenDialogKind
from src.services.human_timings import HumanTimingsService


@dataclass(frozen=True)
class LoadedRecipeInfo:
    recipe: RecipeItem
    quantity: int


@dataclass(frozen=True)
class CraftRequest:
    recipe: RecipeItem
    stop_condition: Callable[[RecipeItem], bool] | int | None = None


@dataclass
class CraftBehavior(RecoverableBehavior):
    auto_trip_smart_behavior: AutoTripSmartBehavior
    load_recipe_behavior: LoadRecipeBehavior
    interactive_behavior: InteractiveBehavior
    pathfinding: Pathfinding

    _stop_craft_recipe_condition: Callable[[RecipeItem], bool] | None = field(init=False, default=None)
    _remaining_recipes: list[RecipeItem] = field(init=False, default_factory=list[RecipeItem])
    _loaded_recipes_infos: list[LoadedRecipeInfo] = field(init=False, default_factory=lambda: [])
    _activity_performed: bool = field(init=False, default=False)

    @property
    def activity_performed(self) -> bool:
        return self._activity_performed

    def run(self, craft_requests: list[CraftRequest]) -> None:
        self.init_recovery_listeners()
        self.ensure_free_to_act(lambda: self.start_crafting(craft_requests=craft_requests))

    def start_crafting(self, craft_requests: list[CraftRequest]) -> None:
        """Int rule: quantity without bank; callable: filter; None: exhaust ingredients."""
        self._activity_performed = False
        valid_recipe_ids = {
            recipe.resultId
            for recipe in self.game_state.craft.get_valid_recipes([req.recipe for req in craft_requests])
        }
        valid_requests = [req for req in craft_requests if req.recipe.resultId in valid_recipe_ids]

        condition_by_result_id: dict[int, Callable[[RecipeItem], bool]] = {}
        for request in valid_requests:
            stop_condition = request.stop_condition
            if stop_condition is None or isinstance(stop_condition, int):
                continue
            condition_by_result_id[request.recipe.resultId] = stop_condition
        self._stop_craft_recipe_condition = lambda recipe: condition_by_result_id.get(
            recipe.resultId, lambda _: False
        )(recipe)

        if not self.game_state.inventory.can_use_bank:
            self._remaining_recipes = []
            return self.craft_from_inventory(valid_requests)

        inventory_requests = [request for request in valid_requests if isinstance(request.stop_condition, int)]
        self._remaining_recipes = [
            req.recipe for req in valid_requests if not isinstance(req.stop_condition, int)
        ]

        if inventory_requests:
            return self.craft_from_inventory(inventory_requests)
        self.process_remaining_recipes()

    def craft_from_inventory(self, requests: list[CraftRequest]) -> None:
        quantity_by_gid: dict[int, int] = {}
        for object_item in self.game_state.inventory.objects_by_uid.values():
            gid = object_item.item.gid
            quantity_by_gid[gid] = quantity_by_gid.get(gid, 0) + object_item.item.quantity

        self._loaded_recipes_infos = []
        for request in requests:
            if isinstance(request.stop_condition, int):
                self._loaded_recipes_infos.append(LoadedRecipeInfo(recipe=request.recipe, quantity=request.stop_condition))
                continue

            max_result_quantity, weight_for_one_result = get_max_result_quantity(
                self.logger, quantity_by_gid, request.recipe
            )
            if max_result_quantity == 0:
                continue
            quantity = get_max_possible_result_quantity(
                self.game_state.inventory.weight_max,
                self.game_state.inventory.inventory_weight,
                weight_for_one_result,
                max_result_quantity,
            )
            if quantity > 0:
                self._loaded_recipes_infos.append(LoadedRecipeInfo(recipe=request.recipe, quantity=quantity))
                for ingredient_id, ingredient_quantity in zip(request.recipe.ingredientIds, request.recipe.quantities):
                    quantity_by_gid[ingredient_id] -= ingredient_quantity * quantity

        if not self._loaded_recipes_infos:
            self.logger.warning("No craftable recipe left, nothing to craft from the inventory")
            return self.process_remaining_recipes()
        self.process_next_loaded_recipe_skill()

    def process_remaining_recipes(self):
        initial_count = len(self._remaining_recipes)
        self._remaining_recipes = [
            recipe
            for recipe in self._remaining_recipes
            if (not self._stop_craft_recipe_condition or not self._stop_craft_recipe_condition(recipe))
        ]

        if len(self._remaining_recipes) == 0:
            filtered_count = initial_count - len(self._remaining_recipes)
            self.logger.info(f"No more recipe to process, filtered out {filtered_count} recipes by condition")
            return self.finish()

        self.logger.info(f"Processing {len(self._remaining_recipes)} recipes")

        self.load_recipe_behavior.start(
            callback=self.on_load_recipe_behavior_finished,
            parent=self,
            recipes=self._remaining_recipes,
        )

    def on_load_recipe_behavior_finished(
        self,
        error_code: str | None,
        loaded_recipes_infos: list[tuple[RecipeItem, int]],
        remaining_recipes: list[RecipeItem],
    ) -> None:
        if error_code is not None:
            return self.finish(error_code)

        if len(loaded_recipes_infos) == 0:
            return self.finish()

        self._remaining_recipes = remaining_recipes
        self._loaded_recipes_infos = [
            LoadedRecipeInfo(recipe=recipe, quantity=quantity) for recipe, quantity in loaded_recipes_infos
        ]

        self.process_next_loaded_recipe_skill()

    def process_next_loaded_recipe_skill(self) -> None:
        if len(self._loaded_recipes_infos) == 0:
            return self.process_remaining_recipes()

        target_skill_id = self._loaded_recipes_infos[0].recipe.skillId
        target_recipes_infos = [
            recipe_info
            for recipe_info in self._loaded_recipes_infos
            if recipe_info.recipe.skillId == target_skill_id
        ]
        self._loaded_recipes_infos = [
            recipe_info
            for recipe_info in self._loaded_recipes_infos
            if recipe_info.recipe.skillId != target_skill_id
        ]
        self.go_and_craft_on_skill(target_recipes_infos, target_skill_id)

    def go_and_craft_on_skill(self, recipes_infos: list[LoadedRecipeInfo], skill_id: int) -> None:
        if self.game_state.dialog.is_open(OpenDialogKind.CRAFT, context_id=skill_id):
            self.logger.info(f"Workshop for skill {skill_id} already open, reusing it")
            return self.run_timer(
                HumanTimingsService().get_timing_base_action(),
                lambda: self.craft_recipe_in_same_skill(tuple(recipes_infos), 0),
            )

        related_map_ids = MAP_IDS_BY_SKILL[skill_id]
        if not self.game_state.player.is_sub:
            related_map_ids = {
                map_id for map_id in related_map_ids if MapTools.is_map_allowed_for_unsub(map_id)
            }
        if len(related_map_ids) == 0:
            self.logger.warning(f"Did not found any related map for the skill {skill_id}")
            skipped_recipes = [recipe_info.recipe for recipe_info in recipes_infos]
            self._remaining_recipes = [
                recipe for recipe in self._remaining_recipes if recipe not in skipped_recipes
            ]
            return self.process_remaining_recipes()

        self.run_timer(
            HumanTimingsService().get_timing_base_action(),
            lambda: self.auto_trip_smart_behavior.start(
                map_ids=related_map_ids,
                callback=partial(
                    self.on_auto_trip_smart_behavior_to_workshop_finished,
                    recipes_infos=recipes_infos,
                    skill_id=skill_id,
                ),
                parent=self,
            ),
        )

    def on_auto_trip_smart_behavior_to_workshop_finished(
        self,
        error_code: str | None,
        recipes_infos: list[LoadedRecipeInfo],
        skill_id: int,
    ) -> None:
        self.raise_if_error(error_code)

        related_element = self.game_state.interactive.get_element_by_skill_id(skill_id)
        ref_data = MapReader().get_ref_data_by_element_id_by_map_id(self.game_state.map.map_id)[
            related_element.element_id
        ]
        if ref_data.cellId is None:
            raise ValueError(f"Missing cell id for interactive element {related_element.element_id}")
        element_mp = MapPoint.from_cell_id(ref_data.cellId)

        self.run_timer(
            HumanTimingsService().get_timing_base_action(),
            lambda: self.interactive_behavior.start(
                element_mp=element_mp,
                element_id=related_element.element_id,
                skill_id=skill_id,
                pre_interaction_delay=HumanTimingsService().get_timing_before_workshop_interaction(),
                callback=partial(
                    self.on_interactive_behavior_finished,
                    recipes_infos=recipes_infos,
                ),
                parent=self,
            ),
        )

    def on_interactive_behavior_finished(
        self, error_code: str | None, recipes_infos: list[LoadedRecipeInfo]
    ) -> None:
        self.raise_if_error(error_code)

        self.event_manager.on(
            ExchangeCraftStartedEvent,
            partial(self.on_exchange_craft_started_event, recipes_infos=recipes_infos),
            originator=self,
            once=True,
            override_on_self=True,
        )

    def on_exchange_craft_started_event(
        self,
        msg: ExchangeCraftStartedEvent,
        recipes_infos: list[LoadedRecipeInfo],
    ) -> None:
        self.craft_recipe_in_same_skill(tuple(recipes_infos), 0)

    def craft_recipe_in_same_skill(
        self,
        recipes_infos: tuple[LoadedRecipeInfo, ...],
        recipe_index: int,
    ) -> None:
        if recipe_index >= len(recipes_infos):
            return self.on_all_crafted_for_skill_in_inventory()

        recipe_info = recipes_infos[recipe_index]
        item_name = I18N().name_by_id[int(recipe_info.recipe.resultNameId)]
        self.logger.info(
            f"Crafting {item_name} x{recipe_info.quantity} ({len(recipes_infos) - recipe_index - 1} recipes remaining in queue)"
        )
        self.event_manager.on(
            ExchangeSetCraftRecipeRequest,
            partial(
                self.on_exchange_set_craft_recipe_request,
                recipes_infos=recipes_infos,
                next_recipe_index=recipe_index + 1,
                max_possible_result_quantity=recipe_info.quantity,
                gid=recipe_info.recipe.resultId,
            ),
            once=True,
            originator=self,
            override_on_self=True,
        )

        req = ExchangeSetCraftRecipeRequest(object_uid=recipe_info.recipe.resultId)
        self.send_message_delayed(req, HumanTimingsService().get_timing_base_action())

    def on_exchange_set_craft_recipe_request(
        self,
        msg: ExchangeSetCraftRecipeRequest,
        recipes_infos: tuple[LoadedRecipeInfo, ...],
        next_recipe_index: int,
        max_possible_result_quantity: int,
        gid: int,
    ) -> None:
        self.event_manager.on(
            ExchangeCraftCountModifiedEvent,
            partial(
                self.on_exchange_craft_count_modified_event,
                recipes_infos=recipes_infos,
                next_recipe_index=next_recipe_index,
                gid=gid,
            ),
            originator=self,
            once=True,
            override_on_self=True,
        )
        self.send_message_delayed(
            ExchangeCraftCountRequest(count=max_possible_result_quantity),
            HumanTimingsService().get_timing_base_action(),
        )

    def on_exchange_craft_count_modified_event(
        self,
        msg: ExchangeCraftCountModifiedEvent,
        recipes_infos: tuple[LoadedRecipeInfo, ...],
        next_recipe_index: int,
        gid: int,
    ) -> None:
        self.event_manager.on(
            InventoryWeightEvent,
            lambda _: self.on_inventory_weight_event_after_craft(recipes_infos, next_recipe_index),
            originator=self,
            once=True,
            override_on_self=True,
            timeout=15,
            on_timeout=lambda: self.on_timeout_exchange_ready(gid),
        )
        req = ExchangeReadyRequest(ready=True, step=6)
        self.send_message_delayed(req, HumanTimingsService().get_timing_short_action())

    def on_inventory_weight_event_after_craft(
        self,
        recipes_infos: tuple[LoadedRecipeInfo, ...],
        next_recipe_index: int,
    ) -> None:
        self._activity_performed = True
        self.craft_recipe_in_same_skill(recipes_infos, next_recipe_index)

    def on_all_crafted_for_skill_in_inventory(self) -> None:
        self.process_next_loaded_recipe_skill()

    def on_timeout_exchange_ready(self, gid: int) -> None:
        self.game_state.craft.forbidden_craft_ids.add(gid)
        self.finish()

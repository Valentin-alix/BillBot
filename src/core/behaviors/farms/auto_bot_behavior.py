from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from threading import RLock

from DBDofusUnity.dofus_unity_reader.data_center.area_info import AreaInfo
from src.controller.settings import SettingsService
from src.core.behaviors.craft.craft_behavior import CraftBehavior, CraftRequest
from src.core.behaviors.farms.base_farm_behavior import BaseFarmingErrorCode
from src.core.behaviors.farms.fight.fighter_behavior import FighterBehavior
from src.core.behaviors.farms.harvest.harvester_behavior import HarvesterBehavior
from src.core.behaviors.farms.harvest.multi_farming_behavior import MultiFarmingBehavior
from src.core.behaviors.idle_behavior import IdleBehavior
from src.core.behaviors.items.auto_equipment_behavior import AutoEquipmentBehavior
from src.core.behaviors.quests.dungeon_behavior import DungeonBehavior
from src.core.behaviors.recovery_behavior import RecoverableBehavior
from src.core.behaviors.sale_hotel.sale_hotel_sell_behavior import SaleHotelErrorCode, SaleHotelSellBehavior
from src.core.behaviors.storage.enter_chests.enter_bank_chest_behavior import (
    EnterBankChestErrorCode,
)
from src.core.bot.session_activity_plan import SessionActivity, SessionActivityPlan
from src.core.config import get_time_beween_areas
from src.core.engine.contexts import HarvesterAreaContext
from src.core.engine.crafts.recipes import (
    get_recipes_for_job_lvl_up_or_benefice,
    is_not_valid_recipe_for_lvl_up_job_or_benefice,
)
from src.core.engine.weights.fighter.set_drop import (
    choose_set_drop_area_info,
    get_missing_set_drop_sub_area_ids,
)
from src.core.engine.weights.weight_areas import (
    get_random_best_area_info,
)
from src.core.states.area_state import (
    CURRENT_AREAS_PLAYING_INFOS_BY_SERVER_AND_CHARACTER,
)
from src.services.human_timings import HumanTimingsService

AREA_CHOICE_LOCK = RLock()


@dataclass
class AutoBotBehavior(RecoverableBehavior):
    auto_equipment_behavior: AutoEquipmentBehavior
    fighter_behavior: FighterBehavior
    harvester_behavior: HarvesterBehavior
    multi_farming_behavior: MultiFarmingBehavior
    dungeon_behavior: DungeonBehavior
    idle_behavior: IdleBehavior
    craft_behavior: CraftBehavior
    sale_hotel_sell_behavior: SaleHotelSellBehavior
    report_status: Callable[[str], None]

    _area_id: int | None = field(init=False, default=None)
    _sub_area_id: int | None = field(init=False, default=None)
    _previous_area_info_played: list[AreaInfo] = field(init=False, default_factory=list[AreaInfo])
    _session_activity_plan: SessionActivityPlan | None = field(init=False, default=None)

    def start_planned_session(self, session_start: datetime, session_end: datetime) -> None:
        self.game_state.apply_settings(SettingsService().get().behaviors)
        activities: list[SessionActivity] = []
        if self.game_state.settings.do_idle:
            activities.append(SessionActivity.IDLE)
        if self.game_state.settings.do_dungeon:
            activities.append(SessionActivity.DUNGEON)
        if self.game_state.settings.do_craft:
            activities.append(SessionActivity.CRAFT)
        if self.game_state.settings.do_sale_hotel:
            activities.append(SessionActivity.SALE_HOTEL)
        self._session_activity_plan = SessionActivityPlan.create(
            session_start=session_start,
            session_end=session_end,
            activities=activities,
        )
        planned_slots = [
            f"{slot.activity} at {slot.starts_at:%H:%M}" for slot in self._session_activity_plan.slots
        ]
        self.logger.info("Planned session activities: %s", planned_slots)

    def clear_planned_session(self) -> None:
        self._session_activity_plan = None

    def get_harvester_area_context(self) -> HarvesterAreaContext:
        return HarvesterAreaContext(
            player_level=self.game_state.player.level,
            player_waypoint_map_ids=frozenset(self.game_state.player.waypoint_map_ids),
            player_is_sub=self.game_state.player.is_sub,
            player_server_id=self.game_state.player.server_id,
            player_jobs_lvl_by_id=self.game_state.player.jobs_lvl_by_id,
            bank_storage_by_gid=self.game_state.inventory.get_bank_objects_by_gid(),
            current_area_infos_by_server_and_character=(CURRENT_AREAS_PLAYING_INFOS_BY_SERVER_AND_CHARACTER),
        )

    def run(
        self,
        area_id: int | None = None,
        sub_area_id: int | None = None,
    ) -> None:
        self.ensure_free_to_act(lambda: self.start_playing(area_id=area_id, sub_area_id=sub_area_id))

    def start_playing(
        self,
        area_id: int | None = None,
        sub_area_id: int | None = None,
    ) -> None:
        self._area_id = area_id
        self._sub_area_id = sub_area_id
        if self.game_state.inventory.is_full_pods:
            return self.play()
        self.auto_equipment_behavior.start(callback=self.on_initial_auto_equipment_finished, parent=self)

    def on_initial_auto_equipment_finished(self, error_code: str | None) -> None:
        self.raise_if_error(error_code)
        self.play()

    def play(self) -> None:
        if not self.game_state.inventory.is_full_pods or self.game_state.inventory.can_use_bank:
            return self.play_multi_farming()
        if self.game_state.settings.do_fighter:
            return self.play_fighter()
        self.finish(EnterBankChestErrorCode.NOT_ENOUGH_KAMAS)

    def play_multi_farming(self) -> None:
        datetime_start_played = datetime.now()

        def stop_multi_farming_condition():
            return (
                self._get_due_activity() is not None
                or datetime_start_played + get_time_beween_areas() < datetime.now()
            )

        self.report_status("Analyzing farming areas…")
        with AREA_CHOICE_LOCK:
            area_info = get_random_best_area_info(
                self._area_id,
                self._sub_area_id,
                self.get_harvester_area_context(),
                self._previous_area_info_played,
                self.logger,
            )
            key = (
                self.game_state.player.server_id,
                self.game_state.player.character_id,
            )
            CURRENT_AREAS_PLAYING_INFOS_BY_SERVER_AND_CHARACTER[key] = area_info
            self._previous_area_info_played.append(area_info)

        self.report_status("Preparing harvesting route…")
        self.multi_farming_behavior.start(
            area_id=area_info.area_id,
            sub_area_id=area_info.sub_area_id,
            is_stopped_at_new_map_condition=stop_multi_farming_condition,
            callback=self.on_multi_farming_behavior_finished,
            parent=self,
        )

    def on_multi_farming_behavior_finished(self, error_code: str | None) -> None:
        if error_code is BaseFarmingErrorCode.STOP_CONDITION_TRIGGERED:
            due_activity = self._get_due_activity()
            if due_activity is not None:
                return self._start_session_activity(due_activity)
            return self.run_timer(HumanTimingsService().get_timing_base_action(), self.play_multi_farming)
        if error_code in {
            EnterBankChestErrorCode.NOT_ENOUGH_KAMAS,
            SaleHotelErrorCode.NOT_ENOUGH_KAMAS,
        }:
            self.logger.info("Not enough kamas for bank: switching to fighter")
            return self.run_timer(HumanTimingsService().get_timing_base_action(), self.play_fighter)
        self.finish(error_code)

    def _get_due_activity(self) -> SessionActivity | None:
        if self._session_activity_plan is None:
            return None
        return self._session_activity_plan.get_due_activity(datetime.now())

    def _start_session_activity(self, activity: SessionActivity) -> None:
        self.logger.info("Starting planned session activity: %s", activity)
        if activity is SessionActivity.IDLE:
            idle_duration = HumanTimingsService().get_timing_session_idle_duration()
            self.logger.info("Taking planned idle break: %.1fmin", idle_duration / 60)
            return self.idle_behavior.start(
                duration=idle_duration,
                callback=partial(self._on_session_activity_finished, activity),
                parent=self,
            )

        if activity is SessionActivity.DUNGEON:
            return self.dungeon_behavior.start(
                callback=partial(self._on_session_activity_finished, activity),
                parent=self,
            )

        if activity is SessionActivity.CRAFT:
            recipes = get_recipes_for_job_lvl_up_or_benefice(
                self.game_state.player.is_sub, self.game_state.player.jobs_lvl_by_id
            )
            return self.craft_behavior.start(
                craft_requests=[
                    CraftRequest(
                        recipe=recipe,
                        stop_condition=partial(
                            is_not_valid_recipe_for_lvl_up_job_or_benefice,
                            is_sub=self.game_state.player.is_sub,
                            jobs_lvl_by_id=self.game_state.player.jobs_lvl_by_id,
                        ),
                    )
                    for recipe in recipes
                ],
                callback=partial(self._on_session_activity_finished, activity),
                parent=self,
            )

        assert activity is SessionActivity.SALE_HOTEL, f"Unsupported session activity: {activity}"
        self.sale_hotel_sell_behavior.start(
            callback=partial(self._on_session_activity_finished, activity), parent=self
        )

    def _on_session_activity_finished(self, activity: SessionActivity, error_code: str | None) -> None:
        if error_code is not None:
            self.logger.warning("Planned session activity %s finished with %s", activity, error_code)
        assert self._session_activity_plan is not None, "A planned activity requires an active session plan"
        if error_code is None and not self._activity_was_performed(activity):
            self._session_activity_plan.discard_empty_activity(activity, datetime.now())
            self.logger.info("Removed empty planned session activity: %s", activity)
        else:
            self._session_activity_plan.mark_activity_completed(activity)
        self.run_timer(HumanTimingsService().get_timing_base_action(), self.play_multi_farming)

    def _activity_was_performed(self, activity: SessionActivity) -> bool:
        if activity is SessionActivity.IDLE:
            return True
        if activity is SessionActivity.DUNGEON:
            return self.dungeon_behavior.activity_performed
        if activity is SessionActivity.CRAFT:
            return self.craft_behavior.activity_performed
        assert activity is SessionActivity.SALE_HOTEL, f"Unsupported planned activity: {activity}"
        return self.sale_hotel_sell_behavior.activity_performed

    def play_fighter(self) -> None:
        datetime_start_played = datetime.now()

        def stop_condition_fighter() -> bool:
            time_to_rotate_area = datetime_start_played + get_time_beween_areas() < datetime.now()
            return (
                not self.game_state.inventory.is_full_pods
                or self.game_state.inventory.can_use_bank
                or time_to_rotate_area
            )

        area_info = self._get_fighter_area_info()
        self._previous_area_info_played.append(area_info)

        self.fighter_behavior.start(
            area_id=area_info.area_id,
            sub_area_id=area_info.sub_area_id,
            is_stopped_at_new_map_condition=stop_condition_fighter,
            callback=self.on_fighter_behavior_finished,
            parent=self,
        )

    def _get_fighter_area_info(self) -> AreaInfo:
        if self._area_id is None:
            player = self.game_state.player
            set_drop_area_info = choose_set_drop_area_info(
                get_missing_set_drop_sub_area_ids(
                    self.game_state.fight.primary_and_second_elem[0],
                    player.level,
                    player.is_sub,
                    self.game_state.inventory.objects_by_uid,
                ),
                player.level,
                player.is_sub,
                frozenset(player.waypoint_map_ids),
                self._previous_area_info_played,
            )
            if set_drop_area_info is not None:
                self.logger.info(f"Gearing: farming {set_drop_area_info} for set drops")
                return set_drop_area_info

        return get_random_best_area_info(
            self._area_id,
            self._sub_area_id,
            self.get_harvester_area_context(),
            self._previous_area_info_played,
            self.logger,
        )

    def on_fighter_behavior_finished(self, error_code: str | None) -> None:
        if error_code is BaseFarmingErrorCode.STOP_CONDITION_TRIGGERED:
            return self.run_timer(HumanTimingsService().get_timing_base_action(), self.play)
        self.finish(error_code)

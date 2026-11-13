from collections.abc import Callable
from dataclasses import dataclass, field, fields
from threading import Event

from ankama_launcher_emulator.interfaces.credentials import (
    StoredApiKey,
)

from DBDofusUnity.datas.protos.non_obf.game.character_pb2 import PlayerStatusUpdateRequest
from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import CharacterStatus
from DBDofusUnity.dofus_unity_reader.models.datas.recipe_root import RecipeItem
from src.controller.bot_config import BotConfig
from src.controller.settings import SettingsService
from src.core.behaviors.behavior import Behavior, BehaviorState
from src.core.behaviors.craft.craft_behavior import CraftBehavior, CraftRequest
from src.core.behaviors.farms.auto_bot_behavior import AutoBotBehavior
from src.core.behaviors.farms.fight.fight_behavior import FightBehavior
from src.core.behaviors.farms.fight.fighter_behavior import FighterBehavior
from src.core.behaviors.farms.harvest.harvester_behavior import HarvesterBehavior
from src.core.events_manager.event_manager import EventManager
from src.core.signals.bot_signals import BotSignals
from src.core.signals.shared_farm_signals import SharedSignals
from src.core.states.player_state import PlayerState
from src.services.background import run_in_background
from src.services.logging_utils.contextual_logger import ContextualLogger
from src.services.user_activity import UserActivityService


@dataclass
class BehaviorCoordinator(ContextualLogger):
    is_connected_event: Event
    is_ready_to_play_event: Event
    is_playing_event: Event
    from_manual_play: Event

    event_manager: EventManager

    fight_behavior: FightBehavior
    harvester_behavior: HarvesterBehavior
    fighter_behavior: FighterBehavior
    craft_behavior: CraftBehavior
    auto_bot_behavior: AutoBotBehavior
    usable_behaviors: list[Behavior]

    bot_signals: BotSignals
    shared_signals: SharedSignals
    player_state: PlayerState

    account: StoredApiKey
    get_bot_config: Callable[[], BotConfig | None]

    _current_bot_action_func: Callable[[Callable[[str], None]], None] | None = field(init=False, default=None)
    action_name: str = field(init=False, default="auto")
    area_id: int | None = field(init=False, default=None)
    sub_area_id: int | None = field(init=False, default=None)

    def _select_action(self, name: str, area_id: int | None = None, sub_area_id: int | None = None) -> None:
        self.action_name = name
        self.area_id = area_id
        self.sub_area_id = sub_area_id

    def on_play(self, from_manual_play: bool):
        if from_manual_play:
            self.auto_bot_behavior.game_state.apply_settings(SettingsService().get().behaviors)
            self.from_manual_play.set()
            UserActivityService().record("info", "Manual startup requested.", login=self.account.apikey.login)
        else:
            self.from_manual_play.clear()
        self.is_playing_event.set()
        if self.is_ready_to_play_event.is_set():
            self.event_manager.send(
                PlayerStatusUpdateRequest(status=CharacterStatus(status=CharacterStatus.STATUS_SOLO))
            )

    def on_stop(self):
        self.is_playing_event.clear()
        self._current_bot_action_func = None
        if self.action_name == "craft":
            self._select_action("auto")
        self.bot_signals.automation_status_changed.emit("")
        run_in_background(lambda _progress_callback: self.stop_behaviors())

    def on_play_usable_behavior(self, behavior_class_name: str):
        self._select_action(behavior_class_name)
        related_behavior = next(
            behavior
            for behavior in self.usable_behaviors
            if behavior.__class__.__name__ == behavior_class_name
        )
        self.play_action(
            lambda _: related_behavior.start(  # type: ignore
                callback=lambda *_args: self.bot_signals.stop.emit(),  # type: ignore
                parent=None,
            )
        )

    def on_play_harvester(self, area_id: int | None, sub_area_id: int | None):
        self._select_action("harvest", area_id, sub_area_id)
        self.play_action(
            lambda _: self.harvester_behavior.start(
                callback=lambda *_args: self.bot_signals.stop.emit(),
                parent=None,
                area_id=area_id,
                sub_area_id=sub_area_id,
            )
        )

    def on_play_fighter(
        self,
        area_id: int | None,
        sub_area_id: int | None,
    ):
        self._select_action("fight", area_id, sub_area_id)
        self.play_action(
            lambda _: self.fighter_behavior.start(
                callback=lambda *_args: self.bot_signals.stop.emit(),
                parent=None,
                area_id=area_id,
                sub_area_id=sub_area_id,
            )
        )

    def on_play_auto_bot(self):
        self._select_action("auto")
        self.bot_signals.automation_status_changed.emit("Preparing automatic mode…")
        self.play_action(
            lambda _: self.auto_bot_behavior.start(
                callback=lambda *_args: self.bot_signals.stop.emit(),
                parent=None,
                area_id=None,
                sub_area_id=None,
            )
        )

    def on_play_crafter(self, recipes: list[RecipeItem]):
        self._select_action("craft")
        self.play_action(
            lambda _: self.craft_behavior.start(
                callback=lambda *_args: self.bot_signals.stop.emit(),
                parent=None,
                craft_requests=[CraftRequest(recipe=recipe) for recipe in recipes],
            )
        )

    def play_action(self, func: Callable[[Callable[[str], None]], None]) -> None:
        self.stop_behaviors()
        self._current_bot_action_func = func
        if not self.is_connected_event.is_set():
            self.shared_signals.launch_account.emit(self.account.apikey.login)
        elif self.is_ready_to_play_event.is_set():
            run_in_background(self._current_bot_action_func)
        else:
            self.logger.info("character is probably connecting, waiting...")

    def run_current_bot_action(self) -> None:
        if self._current_bot_action_func is not None:
            run_in_background(self._current_bot_action_func)
        else:
            self.guess_bot_action()

    def guess_bot_action(self) -> None:
        config = self.get_bot_config()
        if config is not None and config.schedule_profile is not None:
            self.bot_signals.play_auto_bot.emit()

    def stop_behaviors(self) -> None:
        with self.event_manager.lock:
            for behavior in self.get_running_top_level_behaviors():
                behavior.stop()

    def get_running_top_level_behaviors(self) -> list[Behavior]:
        running: list[Behavior] = []
        for field_info in fields(self):
            field_value = getattr(self, field_info.name)
            if isinstance(field_value, Behavior) and field_value.state == BehaviorState.RUNNING:
                running.append(field_value)

        for usable_behavior in self.usable_behaviors:
            if usable_behavior.state == BehaviorState.RUNNING:
                running.append(usable_behavior)
        return running

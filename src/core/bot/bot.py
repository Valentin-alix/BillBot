from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from threading import Event

lazy from ankama_launcher_emulator.interfaces.credentials import (
    StoredApiKey,
)

from src.services.logging_utils.contextual_logger import ContextualLogger
lazy from src.controller.bot_config import BotConfigService
lazy from src.core.behaviors.account.character_creation_behavior import (
    CharacterCreationBehavior,
)
lazy from src.core.behaviors.account.ogrine_subscription import OgrineSubscriptionBehavior
lazy from src.core.behaviors.account.paysafecard_subscription import (
    PaysafecardSubscriptionBehavior,
)
lazy from src.core.behaviors.behavior import Behavior
lazy from src.core.behaviors.craft.craft_behavior import CraftBehavior
lazy from src.core.behaviors.farms.auto_bot_behavior import AutoBotBehavior
lazy from src.core.behaviors.farms.fight.fight_behavior import FightBehavior
lazy from src.core.behaviors.farms.fight.fighter_behavior import FighterBehavior
lazy from src.core.behaviors.farms.harvest.harvester_behavior import HarvesterBehavior
lazy from src.core.behaviors.quests.dungeon_behavior import DungeonBehavior
lazy from src.core.behaviors.quests.tutorial_behavior import TutorialBehavior
lazy from src.core.behaviors.socket.connection_behavior import ConnectionBehavior
lazy from src.core.behaviors.socket.game_session_behavior import GameSessionBehavior
lazy from src.core.behaviors.socket.handshake_behavior import HandshakeBehavior
lazy from src.core.behaviors.socket.heartbeat_behavior import HearthBeatBehavior
lazy from src.core.bot.execution.behavior_coordinator import BehaviorCoordinator
lazy from src.core.bot.execution.process_manager import ProcessManager
lazy from src.core.bot.execution.watchdog import StuckWatchdog
lazy from src.core.bot.lifecycle.connection_handler import ConnectionHandler
lazy from src.core.bot.lifecycle.scheduler import BotScheduler
lazy from src.core.events_manager.event_manager import EventManager
lazy from src.core.frames.frame import Frame
lazy from src.core.signals.bot_signals import BotSignals
lazy from src.core.signals.grid_signals import GridSignals
lazy from src.core.signals.log_signals import LogSignals
lazy from src.core.signals.message_signals import MessageInfoSignals
lazy from src.core.signals.player_signals import GameInfoSignals, InventorySignals
lazy from src.core.signals.shared_farm_signals import SharedSignals
lazy from src.core.signals.world_signals import WorldSignals
lazy from src.core.states.game_state import GameState
lazy from src.services.background import run_in_background
lazy from src.services.debug_recorder import DebugRecorder


@dataclass
class Bot(ContextualLogger):
    account: StoredApiKey

    event_manager: EventManager

    game_state: GameState

    is_fake: bool

    grid_signals: GridSignals
    bot_signals: BotSignals
    inventory_signals: InventorySignals
    game_info_signals: GameInfoSignals
    msg_info_signals: MessageInfoSignals
    world_signals: WorldSignals
    log_signals: LogSignals

    debug_recorder: DebugRecorder | None

    frames: list[Frame]

    harvester_behavior: HarvesterBehavior
    fighter_behavior: FighterBehavior
    craft_behavior: CraftBehavior
    fight_behavior: FightBehavior
    auto_bot_behavior: AutoBotBehavior
    dungeon_behavior: DungeonBehavior
    connection_behavior: ConnectionBehavior
    game_session_behavior: GameSessionBehavior
    handshake_behavior: HandshakeBehavior
    hearthbeat_behavior: HearthBeatBehavior
    character_creation_behavior: CharacterCreationBehavior
    tutorial_behavior: TutorialBehavior
    ogrine_subscription_behavior: OgrineSubscriptionBehavior
    paysafecard_subscription_behavior: PaysafecardSubscriptionBehavior

    usable_behaviors: list[Behavior]

    is_connected_event: Event
    is_ready_to_play_event: Event
    is_playing_event: Event
    shared_signals: SharedSignals
    from_manual_play: Event = field(init=False, default_factory=Event)

    connection_handler: ConnectionHandler = field(init=False)
    behavior_coordinator: BehaviorCoordinator = field(init=False)
    process_manager: ProcessManager = field(init=False)
    scheduler: BotScheduler = field(init=False)
    watchdog: StuckWatchdog = field(init=False)

    def get_bot_config(self):
        return BotConfigService().get_bot_config(self.account.apikey.login)

    def __str__(self):
        return self.account.apikey.login

    def __repr__(self):
        return self.__str__()

    def __post_init__(self):
        self.behavior_coordinator = BehaviorCoordinator(
            is_ready_to_play_event=self.is_ready_to_play_event,
            is_playing_event=self.is_playing_event,
            _logger=self.logger,
            account=self.account,
            shared_signals=self.shared_signals,
            player_state=self.game_state.player,
            event_manager=self.event_manager,
            get_bot_config=self.get_bot_config,
            is_connected_event=self.is_connected_event,
            from_manual_play=self.from_manual_play,
            fight_behavior=self.fight_behavior,
            harvester_behavior=self.harvester_behavior,
            fighter_behavior=self.fighter_behavior,
            craft_behavior=self.craft_behavior,
            auto_bot_behavior=self.auto_bot_behavior,
            usable_behaviors=self.usable_behaviors,
            bot_signals=self.bot_signals,
        )

        self.connection_handler = ConnectionHandler(
            is_ready_to_play_event=self.is_ready_to_play_event,
            is_playing_event=self.is_playing_event,
            game_state=self.game_state,
            _logger=self.logger,
            account=self.account,
            shared_signals=self.shared_signals,
            get_bot_config=self.get_bot_config,
            event_manager=self.event_manager,
            behavior_coordinator=self.behavior_coordinator,
            is_connected_event=self.is_connected_event,
            ogrine_subscription_behavior=self.ogrine_subscription_behavior,
            paysafecard_subscription_behavior=self.paysafecard_subscription_behavior,
            character_creation_behavior=self.character_creation_behavior,
            bot_signals=self.bot_signals,
        )

        self.watchdog = StuckWatchdog(
            _logger=self.logger,
            event_manager=self.event_manager,
            game_state=self.game_state,
            is_playing_event=self.is_playing_event,
            get_running_top_level_behaviors=(self.behavior_coordinator.get_running_top_level_behaviors),
        )

        self.process_manager = ProcessManager(_logger=self.logger)
        self.scheduler = BotScheduler(
            is_playing_event=self.is_playing_event,
            _logger=self.logger,
            account=self.account,
            shared_signals=self.shared_signals,
            get_bot_config=self.get_bot_config,
            behavior_coordinator=self.behavior_coordinator,
            bot_signals=self.bot_signals,
            log_signals=self.log_signals,
            msg_info_signals=self.msg_info_signals,
            process_manager=self.process_manager,
            event_manager=self.event_manager,
            on_session_started=self.auto_bot_behavior.start_planned_session,
            on_session_finished=self.auto_bot_behavior.clear_planned_session,
        )

        self.game_info_signals.connected.connect(self.connection_handler.on_connected)
        self.game_info_signals.disconnected.connect(self.connection_handler.on_disconnected)
        self.bot_signals.play.connect(self.behavior_coordinator.on_play)
        self.bot_signals.stop.connect(self.behavior_coordinator.on_stop)
        self.bot_signals.stop.connect(self.connection_handler.cleanup)
        self.bot_signals.stop.connect(self._apply_pending_schedule)
        self.bot_signals.disconnect_runtime.connect(self.scheduler.disconnect_now)
        self.game_info_signals.is_ready_to_play.connect(self.connection_handler.on_ready_to_play)
        self.bot_signals.play_harvester.connect(self.behavior_coordinator.on_play_harvester)
        self.bot_signals.play_fighter.connect(self.behavior_coordinator.on_play_fighter)
        self.bot_signals.play_crafter.connect(self.behavior_coordinator.on_play_crafter)
        self.bot_signals.play_auto_bot.connect(self.behavior_coordinator.on_play_auto_bot)
        self.bot_signals.play_usable_behavior.connect(self.behavior_coordinator.on_play_usable_behavior)

    def start(self):
        self.scheduler.start()

    def _apply_pending_schedule(self) -> None:
        run_in_background(lambda _: self.scheduler.apply_pending_configuration())

    def cancel_frame_timers(self) -> None:
        for frame in self.frames:
            frame.cancel_timers()

    def bot_should_not_play(self, now: datetime.datetime):
        if self.from_manual_play.is_set():
            return False
        in_playtime = self.scheduler.is_in_randomized_playtime(now)
        if in_playtime is None:
            return False
        return not in_playtime

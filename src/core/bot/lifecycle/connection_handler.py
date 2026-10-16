from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from threading import Event, Timer

from ankama_launcher_emulator.controller.paysafecard_pool import (
    PaysafecardPoolController,
)
from ankama_launcher_emulator.controller.paysafecard_purchase import (
    PaysafecardPurchaseController,
)
from ankama_launcher_emulator.controller.subscription_expiration import (
    SubscriptionExpirationStorage,
)
from ankama_launcher_emulator.interfaces.credentials import (
    StoredApiKey,
)

from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import Character
from src.controller.bot_config import BotConfig
from src.core.behaviors.account.character_creation_behavior import (
    CharacterCreationBehavior,
)
from src.core.behaviors.account.ogrine_subscription import (
    OgrineSubscriptionBehavior,
    OgrineSubscriptionErrorCode,
)
from src.core.behaviors.account.paysafecard_subscription import (
    PaysafecardSubscriptionBehavior,
)
from src.core.bot.execution.behavior_coordinator import BehaviorCoordinator
from src.core.bot.lifecycle.profile_subscription_eligibility import (
    ProfileSubscriptionEligibility,
)
from src.core.engine.movements.world.edge import (
    remove_banned_transitions_by_map_id,
)
from src.core.events_manager.event_manager import (
    EventManager,
)
from src.core.signals.bot_signals import BotSignals
from src.core.signals.shared_farm_signals import SharedSignals
from src.core.states.game_state import GameState
from src.exceptions import UnhandledErrorCodeException
from src.services.logging_utils.contextual_logger import ContextualLogger
from src.services.user_activity import UserActivityService

_OGRINE_SUBSCRIPTION_MIN_KAMAS = 2_500_000


@dataclass
class ConnectionHandler(ContextualLogger):
    is_connected_event: Event
    bot_signals: BotSignals
    is_ready_to_play_event: Event
    is_playing_event: Event
    game_state: GameState
    character_creation_behavior: CharacterCreationBehavior
    ogrine_subscription_behavior: OgrineSubscriptionBehavior
    paysafecard_subscription_behavior: PaysafecardSubscriptionBehavior
    account: StoredApiKey
    shared_signals: SharedSignals
    get_bot_config: Callable[[], BotConfig | None]
    event_manager: EventManager

    behavior_coordinator: BehaviorCoordinator
    subscription_storage: SubscriptionExpirationStorage = field(default_factory=SubscriptionExpirationStorage)
    paysafecard_pool: PaysafecardPoolController = field(default_factory=PaysafecardPoolController)
    paysafecard_purchase_storage: PaysafecardPurchaseController = field(
        default_factory=PaysafecardPurchaseController
    )
    profile_subscription_eligibility: ProfileSubscriptionEligibility = field(
        default_factory=ProfileSubscriptionEligibility
    )

    _timer: Timer | None = field(init=False, default=None)
    _reconnect_attempts: int = field(init=False, default=0)
    _planned_disconnect_event: Event = field(init=False, default_factory=Event)

    def record_planned_disconnect(self) -> None:
        self._planned_disconnect_event.set()

    def cleanup(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def on_connected(self, characters: list[Character]) -> None:
        self.is_connected_event.set()
        if not self.event_manager.is_socket_mode and len(characters) == 0 and self.is_playing_event.is_set():
            self.character_creation_behavior.start(
                callback=self.on_character_creation_behavior_finished, parent=None
            )

    def on_character_creation_behavior_finished(self, error_code: str | None) -> None:
        if error_code is not None:
            raise UnhandledErrorCodeException(error_code)

    def on_disconnected(self) -> None:
        planned_disconnect = self._planned_disconnect_event.is_set()
        self._planned_disconnect_event.clear()
        self.is_connected_event.clear()
        self.is_ready_to_play_event.clear()
        self.game_state.clear_connection_scoped_state()
        if not self.is_playing_event.is_set():
            self._reconnect_attempts = 0
            return

        if planned_disconnect:
            self.logger.info("Planned disconnect completed without reconnect scheduling")
            self._reconnect_attempts = 0
            self.behavior_coordinator.stop_behaviors()
            self.cleanup()
            return

        self.logger.info(f"Bot unexpected disconnect, reconnect attemp : {self._reconnect_attempts}")

        self._reconnect_attempts += 1

        if self._reconnect_attempts > 3:
            self.logger.warning("Max reconnected attempt reached, stopping bot.")
            self.bot_signals.disconnect_runtime.emit()
            self._reconnect_attempts = 0
            return

        self.behavior_coordinator.stop_behaviors()
        self.cleanup()

        delay = 10 + (self._reconnect_attempts * 10)
        UserActivityService().record(
            "warning",
            f"Unexpected disconnection; retry {self._reconnect_attempts}/3 in {delay}s.",
            login=self.account.apikey.login,
        )
        self._timer = Timer(delay, self._emit_relaunch)
        self._timer.start()

    def _emit_relaunch(self):
        self.logger.info("Relaunching bot")
        UserActivityService().record(
            "info", "Automatic restart in progress.", login=self.account.apikey.login
        )
        if self.is_playing_event.is_set():
            self.shared_signals.launch_account.emit(self.account.apikey.login)

    def on_ready_to_play(self):
        self.logger.info("On ready to play")
        self.is_ready_to_play_event.set()
        self._reconnect_attempts = 0
        if not self.is_playing_event.is_set():
            return

        if self.behavior_coordinator._current_bot_action_func is None and self.get_bot_config() is None:
            raise ValueError("An action should be provided if is_playing_event is set")

        remove_banned_transitions_by_map_id(
            self.game_state.map.map_id,
            self.game_state.map.banned_edge_transitions,
        )

        self._continue_after_required_behavior()

    def _continue_after_required_behavior(self) -> None:
        if not self.behavior_coordinator.is_playing_event.is_set():
            return
        if not self._is_eligible_for_subscription():
            self.behavior_coordinator.run_current_bot_action()
            return
        if self._should_subscribe_with_paysafe_card():
            if not self.game_state.settings.enable_auto_paysafecard_subscriptions:
                self.logger.info("Automatic Paysafecard subscription is disabled")
                UserActivityService().record(
                    "info",
                    "Automatic Paysafecard subscription skipped: permission disabled.",
                    login=self.game_state.player.login,
                )
                self.behavior_coordinator.run_current_bot_action()
                return
            self.logger.info("Starting automatic in-game Paysafecard subscription")
            UserActivityService().record(
                "warning",
                "Starting an automatic Paysafecard subscription.",
                login=self.game_state.player.login,
            )
            self.paysafecard_subscription_behavior.start(
                callback=self._on_paysafecard_subscription_finished,
                parent=None,
            )
            return
        if self._should_renew_subscription_with_ogrines():
            if not self.game_state.settings.enable_auto_ogrine_subscriptions:
                self.logger.info("Automatic ogrine subscription is disabled")
                UserActivityService().record(
                    "info", "Ogrine renewal skipped: permission disabled.", login=self.game_state.player.login
                )
                self.behavior_coordinator.run_current_bot_action()
                return
            self.logger.info(
                f"Starting automatic ogrine subscription renewal: kamas={self.game_state.inventory.kamas}"
            )
            UserActivityService().record(
                "warning", "Starting an automatic Ogrine renewal.", login=self.game_state.player.login
            )
            self.ogrine_subscription_behavior.start(
                callback=self._on_ogrine_subscription_finished,
                parent=None,
            )
            return
        self.behavior_coordinator.run_current_bot_action()

    def _is_eligible_for_subscription(self) -> bool:
        return ProfileSubscriptionEligibility._meets_progression_requirements(
            self.game_state.player.level,
            self.game_state.inventory.kamas,
        )

    def _should_subscribe_with_paysafe_card(self) -> bool:
        login = self.game_state.player.login
        bot_config = self.get_bot_config()
        if bot_config is None or bot_config.schedule_profile is None:
            return False
        subscribe_info = self.subscription_storage.get_subscribe_info(login)
        if subscribe_info is None:
            return False
        pending_purchase = self.paysafecard_purchase_storage.load_purchase()
        if subscribe_info.is_subscribe and pending_purchase is not None and pending_purchase.login == login:
            self.paysafecard_purchase_storage.clear_purchase()
            self.logger.info("Cleared confirmed pending Paysafecard purchase")
            pending_purchase = None
        is_never_subscribed = not subscribe_info.is_subscribe and not subscribe_info.is_former_subscribe
        has_pending_purchase = pending_purchase is not None and pending_purchase.login == login
        has_available_pin = bool(self.paysafecard_pool.load())
        is_profile_eligible = self.profile_subscription_eligibility.is_eligible(
            bot_config.schedule_profile,
            login,
            self.game_state.player.level,
            self.game_state.inventory.kamas,
        )
        self.logger.info(f"Is never subscribed : {is_never_subscribed}")
        self.logger.info(f"Has Paysafecard PIN available : {has_available_pin}")
        self.logger.info(f"Has pending Paysafecard purchase : {has_pending_purchase}")
        self.logger.info(f"Is schedule profile eligible for Paysafecard : {is_profile_eligible}")
        return is_never_subscribed and (has_available_pin or has_pending_purchase) and is_profile_eligible

    def _on_paysafecard_subscription_finished(
        self,
        error_code: str | None,
        _new_expiration: datetime | None,
    ) -> None:
        if error_code is not None:
            self.logger.error("Automatic Paysafecard subscription failed: %s", error_code)
            self.bot_signals.disconnect_runtime.emit()
            return
        if self.behavior_coordinator.is_playing_event.is_set():
            self.behavior_coordinator.run_current_bot_action()

    def _should_renew_subscription_with_ogrines(self) -> bool:
        subscribe_info = self.subscription_storage.get_subscribe_info(self.game_state.player.login)
        if subscribe_info is None:
            return False
        is_current_or_former_subscriber = bool(
            subscribe_info.is_subscribe or subscribe_info.is_former_subscribe
        )
        self.logger.info(f"Is current or former sub : {is_current_or_former_subscriber}")
        self.logger.info(f"Kamas available : {self.game_state.inventory.kamas}")
        self.logger.info(f"Is active beyond threshold : {subscribe_info.is_active_beyond_threshold()}")
        return (
            is_current_or_former_subscriber
            and not subscribe_info.is_active_beyond_threshold()
            and self.game_state.inventory.kamas > _OGRINE_SUBSCRIPTION_MIN_KAMAS
        )

    def _on_ogrine_subscription_finished(
        self, error_code: str | None, _new_expiration: datetime | None
    ) -> None:
        if error_code is not None and error_code is not OgrineSubscriptionErrorCode.NOT_ENOUGH_KAMAS:
            self.logger.error(f"Automatic ogrine subscription failed: {error_code}")
            self.bot_signals.disconnect_runtime.emit()
            return
        if self.behavior_coordinator.is_playing_event.is_set():
            self.behavior_coordinator.run_current_bot_action()

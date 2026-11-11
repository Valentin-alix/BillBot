from abc import ABC
from collections.abc import Callable
from dataclasses import dataclass

from DBDofusUnity.datas.protos.non_obf.game.context_pb2 import ContextCreationEvent
from DBDofusUnity.datas.protos.non_obf.game.gamemap_pb2 import MapComplementaryInformationEvent
from DBDofusUnity.datas.protos.non_obf.game.haven_bag_pb2 import HavenBagEnterRequest
from DBDofusUnity.dofus_unity_reader.game_constants.map_id import MapIdEnum

from src.core.behaviors.behavior import Behavior
from src.core.behaviors.farms.fight.fight_behavior import FightBehavior
from src.core.behaviors.quests.dungeon_behavior import DungeonBehavior
from src.core.behaviors.quests.tutorial_behavior import TutorialBehavior
from src.core.engine.dungeons.dungeon_info import get_dungeon_info_for_map_id

MAX_RECOVERY_ATTEMPTS = 5
HAVEN_BAG_EXIT_TIMEOUT_SECONDS = 10.0


@dataclass
class BlockingStateRecovery:
    fight_behavior: FightBehavior
    dungeon_behavior: DungeonBehavior
    tutorial_behavior: TutorialBehavior


@dataclass
class RecoverableBehavior(Behavior, ABC):
    recovery: BlockingStateRecovery

    def ensure_free_to_act(
        self,
        then: Callable[[], None],
        remaining_attempts: int = MAX_RECOVERY_ATTEMPTS,
    ) -> None:
        if remaining_attempts <= 0:
            self.logger.warning("Still blocked after recovery attempts, going on anyway")
            return then()

        def on_recovery_finished(error_code: str | None) -> None:
            self.raise_if_error(error_code)
            self.ensure_free_to_act(then, remaining_attempts - 1)

        if self.game_state.fight.in_fight:
            self.logger.info("A fight is running, playing it before going on")
            return self.recovery.fight_behavior.start(callback=on_recovery_finished, parent=self)

        if self.game_state.map.is_in_haven_bag:
            self.logger.info("Still in the haven bag, leaving it before going on")
            return self.leave_haven_bag(lambda: self.ensure_free_to_act(then, remaining_attempts - 1))

        if self.game_state.map.map_id == MapIdEnum.TUTORIAL_STARTING:
            self.logger.info("Still on the tutorial map, going through it before going on")
            return self.recovery.tutorial_behavior.start(callback=on_recovery_finished, parent=self)

        dungeon_info = get_dungeon_info_for_map_id(self.game_state.map.map_id)
        if dungeon_info is not None:
            self.logger.info(f"Still inside {dungeon_info.name}, leaving it before going on")
            return self.recovery.dungeon_behavior.start(
                dungeon_info=dungeon_info,
                callback=on_recovery_finished,
                parent=self,
            )

        then()

    def leave_haven_bag(self, then: Callable[[], None]) -> None:
        self.event_manager.on(
            MapComplementaryInformationEvent,
            lambda _: then(),
            originator=self,
            once=True,
            override_on_self=True,
            timeout=HAVEN_BAG_EXIT_TIMEOUT_SECONDS,
            on_timeout=then,
        )
        self.event_manager.send(HavenBagEnterRequest(owner=self.game_state.player.character_id))

    def init_recovery_listeners(self) -> None:
        self.event_manager.on(
            ContextCreationEvent,
            self.on_context_creation_event,
            originator=self,
        )

    def on_context_creation_event(self, msg: ContextCreationEvent) -> None:
        if msg.context != ContextCreationEvent.GameContext.FIGHT:
            return
        self.logger.info("Fight started, playing it before resuming")
        with self.event_manager.lock:
            self.clear_behavior()
            self.init_recovery_listeners()
            self.recovery.fight_behavior.start(callback=self.on_recovered, parent=self)

    def on_recovered(self, error_code: str | None) -> None:
        self.raise_if_error(error_code)
        self.resume_after_interruption()

    def resume_after_interruption(self) -> None:
        self.replay_run()

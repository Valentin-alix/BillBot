from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from DBDofusUnity.datas.protos.non_obf.game.context_pb2 import ContextCreationEvent
from DBDofusUnity.datas.protos.non_obf.game.gamemap_pb2 import (
    FightMapInformationEvent,
)

from src.core.behaviors.farms.fight.attacker_behavior import AttackerBehavior
from src.core.behaviors.farms.harvest.harvester_behavior import HarvesterBehavior
from src.core.config import get_time_between_attacker


@dataclass
class MultiFarmingBehavior(HarvesterBehavior):
    attacker_behavior: AttackerBehavior

    _next_time_attacker: datetime = field(init=False, default_factory=datetime.now)

    def run(
        self,
        area_id: int | None,
        sub_area_id: int | None,
        is_stopped_at_new_map_condition: Callable[[], bool] | None = None,
        target_resource_item_ids: set[int] | None = None,
    ) -> None:
        self._next_time_attacker = datetime.now() + get_time_between_attacker(self.game_state.settings.do_fighter)
        return super().run(
            area_id, sub_area_id, is_stopped_at_new_map_condition, target_resource_item_ids
        )

    def on_new_map(self):
        if self.check_stop_condition():
            return

        self._continue_on_new_map()

    def _continue_on_new_map(self):
        if self.check_stop_condition():
            return

        def on_random_action_done():
            HarvesterBehavior.on_new_map(self)

        if self._next_time_attacker <= datetime.now():
            self.logger.info("Time to attack !")
            return self.fight_on_map()

        on_random_action_done()

    def fight_on_map(self):
        with self.event_manager.lock:
            self.unregister_listener(FightMapInformationEvent)
            self.attacker_behavior.start(
                count_fight_limit=1,
                parent=self,
                callback=self.on_attacker_behavior_finished,
            )

    def on_attacker_behavior_finished(self, error_code: str | None, count_fighted_on_map: int):
        self.raise_if_error(error_code)
        self.init_listeners()
        HarvesterBehavior.on_new_map(self)

    def on_context_creation_event(self, msg: ContextCreationEvent):
        self._next_time_attacker = datetime.now() + get_time_between_attacker(self.game_state.settings.do_fighter)
        return super().on_context_creation_event(msg)

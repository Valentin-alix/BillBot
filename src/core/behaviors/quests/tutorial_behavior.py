from dataclasses import dataclass

from DBDofusUnity.dofus_unity_reader.game_constants.map_id import MapIdEnum
from DBDofusUnity.datas.protos.non_obf.game.gamemap_pb2 import MapComplementaryInformationEvent
from DBDofusUnity.datas.protos.non_obf.game.quest_pb2 import GuideModQuitRequest

from src.core.behaviors.behavior import Behavior
from src.services.human_timings import HumanTimingsService


@dataclass
class TutorialBehavior(Behavior):
    def run(self):
        assert self.game_state.map.map_id == MapIdEnum.TUTORIAL_STARTING
        self.event_manager.on(
            MapComplementaryInformationEvent,
            self.on_map_complementary_information_event,
            originator=self,
            once=True,
        )
        req = GuideModQuitRequest()
        self.run_timer(HumanTimingsService().get_timing_long_action(), lambda: self.event_manager.send(req))

    def on_map_complementary_information_event(self, msg: MapComplementaryInformationEvent):
        self.finish()

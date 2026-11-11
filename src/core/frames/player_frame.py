from dataclasses import dataclass, field
from threading import Timer

from DBDofusUnity.datas.protos.non_obf.game.bak_pb2 import BakApiKeyEvent
from DBDofusUnity.datas.protos.non_obf.game.character_management_pb2 import (
    CharacterListEvent,
    CharacterSelectionEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.character_pb2 import (
    CharacterLevelUpEvent,
    PlayerStatusUpdateRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import CharacterStatus
from DBDofusUnity.datas.protos.non_obf.game.dialog_pb2 import DialogLeaveRequest
from DBDofusUnity.datas.protos.non_obf.game.gamemap_pb2 import (
    FightMapInformationEvent,
    MapComplementaryInformationEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.job_pb2 import (
    JobExperiencesUpdateEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.teleportation_pb2 import (
    ZaapKnownListEvent,
)
from src.core.engine.fights.stats.characteristic import (
    build_characteristic_upgrade_request,
    get_max_characteristic_per_point,
)
from src.core.events_manager.priority import PriorityEnum
from src.core.frames.frame import Frame
from src.services.human_timings import HumanTimingsService


@dataclass
class PlayerFrame(Frame):
    _timer: Timer | None = field(init=False, default=None)

    def __post_init__(self):
        self.event_manager.on(
            JobExperiencesUpdateEvent,
            self.on_job_experiences_update_event,
            originator=self,
            priority=self.priority,
        )
        self.event_manager.on(
            CharacterSelectionEvent,
            self.on_character_selection_event,
            originator=self,
            priority=self.priority,
        )
        self.event_manager.on(
            ZaapKnownListEvent,
            self.on_zaap_known_list_event,
            originator=self,
            priority=self.priority,
        )
        self.event_manager.on(CharacterLevelUpEvent, self.on_character_level_up_event, originator=self)
        self.event_manager.before(DialogLeaveRequest, self.before_dialog_leave_request, originator=self)
        self.event_manager.on(
            CharacterListEvent, self.on_character_list_event, originator=self, priority=self.priority
        )
        self.event_manager.on(
            BakApiKeyEvent, self.on_bak_api_event, originator=self, priority=PriorityEnum.MAX
        )

        self.game_info_signals.disconnected.connect(self.on_disconnected)
        self.game_info_signals.is_ready_to_play.connect(self.game_state.player.is_ready_to_play_event.set)
        self.game_info_signals.is_ready_to_play.connect(self.on_ready_to_play)

    def on_ready_to_play(self) -> None:
        if self.is_playing_event.is_set():
            self.event_manager.send(
                PlayerStatusUpdateRequest(status=CharacterStatus(status=CharacterStatus.STATUS_SOLO))
            )

    def _register_ready_to_play_trigger(self):
        def on_map_init_after_connected():
            self.run_timer(3, self.game_info_signals.is_ready_to_play.emit)
            self.unregister_listener(MapComplementaryInformationEvent)
            self.unregister_listener(FightMapInformationEvent)

        self.event_manager.on(
            MapComplementaryInformationEvent,
            lambda _: on_map_init_after_connected(),
            originator=self,
            once=True,
            priority=PriorityEnum.MAX,
        )
        self.event_manager.on(
            FightMapInformationEvent,
            lambda _: on_map_init_after_connected(),
            originator=self,
            once=True,
            priority=PriorityEnum.MAX,
        )

    def on_disconnected(self):
        self.game_state.player.is_ready_to_play_event.clear()

    def on_bak_api_event(self, msg: BakApiKeyEvent):
        self.game_state.player.bak_token = msg.token

    def on_character_level_up_event(self, msg: CharacterLevelUpEvent):
        self.game_state.player.level = msg.new_level
        if self.is_playing_event.is_set():
            self.game_state.player.is_characteristic_upgrade_complete_event.clear()
            self.run_timer(
                HumanTimingsService().get_timing_after_level_up(),
                lambda: self._upgrade_characteristic_after_level_up(msg.new_level),
            )

    def _upgrade_characteristic_after_level_up(self, level: int) -> None:
        if not self.is_playing_event.is_set():
            self.game_state.player.is_characteristic_upgrade_complete_event.set()
            return
        characteristic_points = get_max_characteristic_per_point(level)
        self.logger.info(f"New amount of base char : {characteristic_points}")
        primary_element = self.game_state.fight.primary_and_second_elem[0]
        request = build_characteristic_upgrade_request(primary_element, characteristic_points)
        self.event_manager.send(request)
        self.game_state.player.is_characteristic_upgrade_complete_event.set()

    def on_job_experiences_update_event(self, message: JobExperiencesUpdateEvent) -> None:
        for job_xp in message.experiences:
            self.game_state.player.job_levels_by_id[job_xp.job_id] = job_xp.job_level
            self.game_state.player.jobs_lvl_by_id[job_xp.job_id] = max((job_xp.job_level // 10) * 10, 1)
            self.game_info_signals.job_level_changed.emit(job_xp.job_id, job_xp.job_level)

    def on_character_list_event(self, msg: CharacterListEvent):
        self._register_ready_to_play_trigger()
        self.game_info_signals.connected.emit(msg.characters)

    def on_character_selection_event(self, message: CharacterSelectionEvent):
        if message.HasField("success"):
            self.game_state.player.character_id = message.success.character.id
            if message.success.character.HasField("character_basic_information"):
                self.game_state.player.level = message.success.character.character_basic_information.level
                self.game_state.player.character_name = (
                    message.success.character.character_basic_information.name
                )
                if message.success.character.character_basic_information.HasField("character_look"):
                    self.game_state.fight.breed_id = (
                        message.success.character.character_basic_information.character_look.breed_id
                    )
            elif message.success.character.HasField("character_remodeling_information"):
                self.game_state.fight.breed_id = (
                    message.success.character.character_remodeling_information.breed_id
                )

    def on_zaap_known_list_event(self, msg: ZaapKnownListEvent):
        self.game_state.player.waypoint_map_ids = list(msg.destinations)

    def before_dialog_leave_request(self, msg: DialogLeaveRequest):
        if self.is_playing_event.is_set():
            self.logger.info("Cancel dialog leave request from client")
            return None
        return msg

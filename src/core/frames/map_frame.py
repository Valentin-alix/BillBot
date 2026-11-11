from dataclasses import dataclass

from DBDofusUnity.datas.protos.non_obf.game.fight_preparation_pb2 import FightPreparationEnterRequest

from DBDofusUnity.datas.protos.non_obf.game.anomaly_pb2 import AnomalySubareaInformationRequest
from DBDofusUnity.datas.protos.non_obf.game.context_pb2 import (
    ContextCreationEvent,
    ContextReadyRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.dialog_pb2 import DialogLeaveEvent, DialogLeaveRequest
from DBDofusUnity.datas.protos.non_obf.game.gamemap_pb2 import (
    FightMapInformationEvent,
    MapChangeRequest,
    MapComplementaryInformationEvent,
    MapCurrentEvent,
    MapInformationRequest,
    MapMovementConfirmRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.npc_pb2 import NpcDialogQuestionEvent
from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.game_constants.map_id import MAP_IDS_THAT_POP_DIALOG
from src.core import config
from src.core.frames.frame import Frame
from src.core.signals.world_signals import WorldSignals
from src.services.human_timings import HumanTimingsService

_WAIT_FOR_ANAL_MOLY_INFO_RANGE = (1.3, 1.6)
_WAIT_ON_NEW_MAP = (0.4, 0.7)


@dataclass
class MapFrame(Frame):
    world_signals: WorldSignals

    def __post_init__(self):
        self.event_manager.on(
            ContextCreationEvent,
            self.on_context_creation_event,
            originator=self,
            priority=self.priority,
        )
        self.event_manager.on(
            MapComplementaryInformationEvent,
            self.on_map_complementary_information_event,
            originator=self,
            priority=self.priority,
        )
        self.event_manager.on(
            MapCurrentEvent,
            self.on_map_current_event,
            originator=self,
            priority=self.priority,
        )
        self.event_manager.on(
            FightMapInformationEvent,
            self.on_fight_map_information_event,
            originator=self,
            priority=self.priority,
        )
        self.event_manager.before(
            MapMovementConfirmRequest,
            self.before_map_movement_confirm_request,
            originator=self,
        )
        self.event_manager.before(
            MapChangeRequest,
            self.before_map_change_request,
            originator=self,
        )

    def on_map_complementary_information_event(self, message: MapComplementaryInformationEvent):
        self.logger.debug(f"New map : {message.map_id}")
        assert self.game_state.map.map_id == message.map_id
        self.game_state.map.is_in_haven_bag = message.HasField("haven_bag_information")
        if config.DEBUG:
            self.world_signals.curr_map_pos.emit(DataReader().map_info_by_map_id[message.map_id])
        self.game_state.map.is_in_map_transition = False
        self.game_state.map.discard_map_stay_banned_transitions()
        self.register_map_popup_dialog_leave(message.map_id)

    def register_map_popup_dialog_leave(self, map_id: int) -> None:
        if map_id not in MAP_IDS_THAT_POP_DIALOG:
            self.game_state.map.is_waiting_for_map_popup_dialog_leave = False
            self.unregister_listener(NpcDialogQuestionEvent)
            return
        self.game_state.map.is_waiting_for_map_popup_dialog_leave = True
        self.event_manager.on(
            NpcDialogQuestionEvent,
            self.leave_map_popup_dialog_on_npc_question,
            originator=self,
            once=True,
            override_on_self=True,
            priority=self.priority,
            timeout=2,
            on_timeout=self.on_timeout_npc_dialog_question,
        )

    def on_timeout_npc_dialog_question(self):
        self.game_state.map.is_waiting_for_map_popup_dialog_leave = False

    def leave_map_popup_dialog_on_npc_question(self, message: NpcDialogQuestionEvent) -> None:
        self.event_manager.on(
            DialogLeaveEvent,
            self.on_map_popup_dialog_left,
            originator=self,
            once=True,
            override_on_self=True,
            priority=self.priority,
        )
        self.run_timer(
            HumanTimingsService().get_timing_base_action(),
            lambda: self.event_manager.send(DialogLeaveRequest()),
        )

    def on_map_popup_dialog_left(self, message: DialogLeaveEvent) -> None:
        self.game_state.map.is_waiting_for_map_popup_dialog_leave = False

    def on_map_current_event(self, msg: MapCurrentEvent):
        self.game_state.map.is_waiting_for_map_popup_dialog_leave = False
        self.game_state.map.map_id = msg.map_id
        self.game_state.entity.clear_actors()
        self.game_state.entity.clear_obstacles()
        self.game_state.interactive.clear_stated_elements()
        self.game_state.map.is_in_map_transition = True

        if self.event_manager.is_socket_mode:
            if not self.game_state.map._anomaly_info_requested:
                self.game_state.map._anomaly_info_requested = True
                self.run_timer(
                    _WAIT_FOR_ANAL_MOLY_INFO_RANGE,
                    lambda: self.event_manager.send(AnomalySubareaInformationRequest()),
                )
            self.run_timer(
                _WAIT_ON_NEW_MAP,
                lambda: self._send_on_ready_on_new_map(msg.map_id),
            )

    def _send_on_ready_on_new_map(self, map_id: int):
        if self.game_state.fight.in_fight:
            self.event_manager.send(FightPreparationEnterRequest())
        self.event_manager.send(ContextReadyRequest(map_id=map_id))
        if not self.game_state.fight.in_fight:
            self.event_manager.send(MapInformationRequest(map_id=map_id))

    def on_context_creation_event(self, msg: ContextCreationEvent):
        self.game_state.map.is_in_map_transition = True

    def on_fight_map_information_event(self, msg: FightMapInformationEvent):
        self.game_state.map.is_in_haven_bag = False
        self.game_state.map.is_in_map_transition = False

    def before_map_movement_confirm_request(self, msg: MapMovementConfirmRequest):
        if self.is_playing_event.is_set():
            self.logger.debug("Canceling client map movement confirm response to avoid duplicate")
            return None
        return msg

    def before_map_change_request(self, msg: MapChangeRequest):
        if not self.game_state.player.is_sub:
            self.logger.debug("Altering map change request auto_pilot set to False")
            msg.auto_pilot = False
        return msg

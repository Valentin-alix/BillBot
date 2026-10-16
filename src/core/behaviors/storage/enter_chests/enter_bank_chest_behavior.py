from dataclasses import dataclass
from enum import StrEnum, auto

from DBDofusUnity.datas.protos.non_obf.game.dialog_pb2 import (
    DialogLeaveEvent,
    DialogLeaveRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.exchange_pb2 import (
    ExchangeMoveKamaRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.inventory_pb2 import (
    StorageInventoryContentEvent,
    StorageKamasUpdateEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.npc_pb2 import (
    NpcDialogQuestionEvent,
)
from DBDofusUnity.dofus_unity_reader.game_constants.npc import (
    BANK_NPCS,
    NpcAskMessageIdEnum,
)
from src.core.behaviors.movements.auto_trip.auto_trip_smart_behavior import (
    AutoTripSmartBehavior,
)
from src.core.behaviors.npcs.npc_dialog_behavior import (
    NpcDialogBehavior,
    NpcDialogErrorCode,
)
from src.core.behaviors.recovery_behavior import RecoverableBehavior
from src.core.engine.npcs.dialog_turn import DialogTurn
from src.core.engine.npcs.reply_selector import ByText
from src.core.engine.storage.unload import get_bank_npc_info
from src.core.states.dialog_state import OpenDialogKind
from src.services.human_timings import HumanTimingsService


class EnterBankChestErrorCode(StrEnum):
    NOT_ENOUGH_KAMAS = auto()
    NOT_ENOUGH_LVL = auto()
    KAMAS_MOVE_NOT_CONFIRMED = auto()


@dataclass
class EnterBankChestBehavior(RecoverableBehavior):
    npc_dialog_behavior: NpcDialogBehavior
    auto_trip_world_behavior: AutoTripSmartBehavior

    def run(self) -> None:
        self.init_recovery_listeners()
        self.ensure_free_to_act(lambda: self.start_entering_bank())

    def start_entering_bank(self):
        if self.game_state.dialog.is_open(OpenDialogKind.BANK_STORAGE):
            self.logger.info("Bank chest already open, reusing it")
            return self.finish()

        if self.game_state.player.level < 10:
            return self.finish(EnterBankChestErrorCode.NOT_ENOUGH_LVL)

        bank_npc_infos = get_bank_npc_info(self.game_state.player.is_sub)

        self.auto_trip_world_behavior.start(
            callback=self.on_bank_map,
            parent=self,
            map_ids={bank.npc_map_id for bank in bank_npc_infos},
        )

    OPEN_CHEST_TURNS = [
        DialogTurn(reply=ByText(pattern=r"consulter son coffre personnel"), finish_after=True)
    ]

    def on_bank_map(self, error_code: str | None):
        def is_forbidden_msg_callback(msg: NpcDialogQuestionEvent):
            return (
                msg.message_id == NpcAskMessageIdEnum.ASTRUB_BANK_NPC_ASK_OPEN_CHEST
                and int(msg.dialog_params[0]) > self.game_state.inventory.kamas
            )

        if error_code is not None:
            return self.finish(error_code)

        self.npc_dialog_behavior.start(
            callback=self.on_npc_dialog_behavior_finished,
            parent=self,
            npc_dialog_info=next(bank for bank in BANK_NPCS if bank.npc_map_id == self.game_state.map.map_id),
            turn_variants=[self.OPEN_CHEST_TURNS],
            is_forbidden_msg_callback=is_forbidden_msg_callback,
        )

    def on_npc_dialog_behavior_finished(self, error_code: str | None):
        if error_code is NpcDialogErrorCode.FORBIDDEN_CONDITION:
            return self.close_dialog_for_not_enough_kamas()
        self.event_manager.on(
            StorageInventoryContentEvent,
            self.on_storage_inventory_content_event,
            originator=self,
            once=True,
        )

    def close_dialog_for_not_enough_kamas(self) -> None:
        self.event_manager.on(
            DialogLeaveEvent,
            self.on_dialog_leave_after_not_enough_kamas,
            originator=self,
            once=True,
        )
        self.event_manager.send(DialogLeaveRequest())

    def on_dialog_leave_after_not_enough_kamas(self, _: DialogLeaveEvent) -> None:
        self.finish(EnterBankChestErrorCode.NOT_ENOUGH_KAMAS)

    def on_storage_inventory_content_event(self, msg: StorageInventoryContentEvent):
        if msg.kamas > 0:

            def on_storage_kamas_update_event(
                update_event: StorageKamasUpdateEvent,
            ) -> None:
                if update_event.kamas != 0:
                    return self.finish(EnterBankChestErrorCode.KAMAS_MOVE_NOT_CONFIRMED)
                self.finish()

            def move_kama():
                req = ExchangeMoveKamaRequest(quantity=-msg.kamas)
                self.event_manager.send(req)

            self.event_manager.on(
                StorageKamasUpdateEvent,
                on_storage_kamas_update_event,
                originator=self,
                once=True,
                timeout=10,
                on_timeout=lambda: self.finish(EnterBankChestErrorCode.KAMAS_MOVE_NOT_CONFIRMED),
            )

            self.run_timer(HumanTimingsService().get_timing_base_action(), move_kama)
        else:
            self.finish()

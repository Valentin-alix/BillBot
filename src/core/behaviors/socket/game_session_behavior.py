import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from math import floor

from DBDofusUnity.datas.protos.non_obf.game.connection_pb2 import PongEvent
from DBDofusUnity.datas.protos.non_obf.game.basic_pb2 import (
    BasicLatencyStatsEvent,
    BasicLatencyStatsRequest,
    SequenceNumberEvent,
    SequenceNumberRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.challenge_pb2 import (
    ChallengeBonusChoiceRequest,
    ChallengeModSelectRequest,
    ChallengeProposalEvent,
    ChallengeReadyRequest,
    ChallengeSelectionRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.client_verification_pb2 import (
    ClientChallengeInitRequest,
    ClientChallengeProofRequest,
    ClientIdRequest,
    ServerChallengeEvent,
    ServerSessionReadyEvent,
    ServerVerificationEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import (
    ChallengeBonus,
    ChallengeMod,
)
from DBDofusUnity.datas.protos.non_obf.game.fight_pb2 import (
    FightIsTurnReadyEvent,
    FightTurnReadyRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.game_action_pb2 import (
    GameActionAcknowledgementRequest,
    SequenceEndEvent,
    SequenceStartEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.gamemap_pb2 import FightMapInformationEvent

from src.controller.bot_config import BotConfigService
from src.core.behaviors.behavior import Behavior
from src.services.human_timings import HumanTimingsService

# Schnorr group: BouncyCastle rfc2409_768 with g=2 and q=(p-1)/2.
_DH_P = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A63A3620FFFFFFFFFFFFFFFF",
    16,
)
_DH_G = 2
_DH_Q = (_DH_P - 1) // 2

# Client RNG byte counts before reduction modulo p.
_SECRET_RANDOM_BYTES = 1024
_NONCE_RANDOM_BYTES = 50


@dataclass
class GameSessionBehavior(Behavior):
    _verification_secret: int = field(init=False, default=0)
    _verification_nonce: int = field(init=False, default=0)
    _sequence_number: int = field(init=False, default=1)

    _fight_sequence_depth: int = field(init=False, default=0)
    _turn_ready_pending: bool = field(init=False, default=False)

    def run(self) -> None:
        self._verification_secret = (
            int.from_bytes(secrets.token_bytes(_SECRET_RANDOM_BYTES), "little") % _DH_P
        )
        self._verification_nonce = 0
        self._sequence_number = 1
        self._fight_sequence_depth = 0
        self._turn_ready_pending = False

        self.event_manager.on(ServerVerificationEvent, self._on_server_verification, originator=self)
        self.event_manager.on(ServerChallengeEvent, self._on_server_challenge, originator=self)
        self.event_manager.on(ServerSessionReadyEvent, self._on_server_session_ready, originator=self)
        self.event_manager.on(SequenceNumberEvent, self._on_sequence_number, originator=self)
        self.event_manager.on(BasicLatencyStatsEvent, self._on_basic_latency, originator=self)
        self.event_manager.on(SequenceStartEvent, self._on_sequence_start, originator=self)
        self.event_manager.on(SequenceEndEvent, self._on_sequence_end, originator=self)
        self.event_manager.on(FightIsTurnReadyEvent, self._on_fight_is_turn_ready, originator=self)
        self.event_manager.on(FightMapInformationEvent, self._on_fight_map_information, originator=self)
        self.event_manager.on(ChallengeProposalEvent, self._on_challenge_proposal, originator=self)
        self.event_manager.on(PongEvent, self.on_pong_event, originator=self)

    def _on_server_verification(self, _msg: ServerVerificationEvent) -> None:
        self.event_manager.send(
            ClientChallengeInitRequest(challenge_key=str(self._verification_public_key()))
        )

    def _on_server_session_ready(self, _msg: ServerSessionReadyEvent) -> None:
        self._verification_nonce = int.from_bytes(secrets.token_bytes(_NONCE_RANDOM_BYTES), "little")
        self.event_manager.send(ClientIdRequest(id=str(self._verification_commitment())))

    def _on_server_challenge(self, msg: ServerChallengeEvent) -> None:
        if msg.HasField("value") and msg.value:
            challenge = int(msg.value)
        else:
            challenge = self._derive_local_challenge()
        proof = (self._verification_nonce + challenge * self._verification_secret) % _DH_Q
        self.event_manager.send(ClientChallengeProofRequest(proof=str(proof)))

    def _verification_public_key(self) -> int:
        return pow(_DH_G, self._verification_secret, _DH_P)

    def _verification_commitment(self) -> int:
        return pow(_DH_G, self._verification_nonce, _DH_P)

    def _derive_local_challenge(self) -> int:
        """Rebuild an omitted challenge from the client's Fiat-Shamir transcript and login device identifier."""

        device_identifier = BotConfigService().get_bot_config(self.game_state.player.login).hardware_id
        transcript = (
            f"{_DH_G}{self._verification_public_key()}{self._verification_commitment()}{device_identifier}"
        )
        return int.from_bytes(hashlib.sha256(transcript.encode("utf-8")).digest(), "big")

    def _on_sequence_number(self, msg: SequenceNumberEvent) -> None:
        self.event_manager.send(SequenceNumberRequest(number=self._sequence_number))
        self._sequence_number += 1

    def _on_basic_latency(self, msg: BasicLatencyStatsEvent) -> None:
        if self.game_state.server.latency is None:
            self.logger.error(
                "BasicLatencyStatsEvent received before latency was measured: "
                f"sent_datetime_ping_request={self.game_state.server.sent_datetime_ping_request}"
            )
        assert self.game_state.server.latency is not None
        self.event_manager.send(BasicLatencyStatsRequest(latency=self.game_state.server.latency))

    def _on_sequence_start(self, msg: SequenceStartEvent) -> None:
        self._fight_sequence_depth += 1

    def _on_sequence_end(self, msg: SequenceEndEvent) -> None:
        if self._fight_sequence_depth <= 0:
            self.logger.warning(
                "SequenceEndEvent received without matching SequenceStartEvent: "
                f"author_id={msg.author_id}, action_id={msg.action_id}"
            )
            self._fight_sequence_depth = 0
        else:
            self._fight_sequence_depth -= 1
        if self._fight_sequence_depth > 0:
            return
        self._fight_sequence_depth = 0
        if self._turn_ready_pending:
            self._turn_ready_pending = False
            self._schedule_turn_ready()
        if msg.author_id != self.game_state.player.character_id:
            return
        self.run_timer(
            HumanTimingsService().get_timing_fight_acknowledgement(),
            lambda: self._send_action_ack(msg.action_id),
        )

    def _send_action_ack(self, action_id: int) -> None:
        ack = GameActionAcknowledgementRequest(valid=True, action_id=action_id)
        self.event_manager.send(ack)

    def _on_fight_is_turn_ready(self, msg: FightIsTurnReadyEvent) -> None:
        if self._fight_sequence_depth > 0:
            self._turn_ready_pending = True
            return
        self._schedule_turn_ready()

    def _schedule_turn_ready(self) -> None:
        self.run_timer(
            HumanTimingsService().get_timing_fight_turn_ready(),
            self._send_turn_ready,
        )

    def _send_turn_ready(self) -> None:
        self.event_manager.send(FightTurnReadyRequest(is_ready=True))

    def _on_fight_map_information(self, _msg: FightMapInformationEvent) -> None:
        self.event_manager.send(ChallengeModSelectRequest(challenge_mod=ChallengeMod.CHALLENGE_CHOICE))
        self.event_manager.send(
            ChallengeBonusChoiceRequest(challenge_bonus=ChallengeBonus.CHALLENGE_DROP_BONUS)
        )
        self.run_timer(
            HumanTimingsService().get_timing_fight_challenge_ready(),
            lambda: self.event_manager.send(
                ChallengeReadyRequest(challenge_mod=ChallengeMod.CHALLENGE_CHOICE)
            ),
        )

    def _on_challenge_proposal(self, msg: ChallengeProposalEvent) -> None:
        if not msg.challenge_proposals:
            self.logger.warning(
                "ChallengeProposalEvent received without proposals; leaving challenge selection to the server"
            )
            return
        challenge_id = msg.challenge_proposals[0].challenge_id
        self.run_timer(
            HumanTimingsService().get_timing_fight_challenge_selection(),
            lambda: self.event_manager.send(ChallengeSelectionRequest(challenge_id=challenge_id)),
        )

    def on_pong_event(self, msg: PongEvent):
        sent_datetime_ping_request = self.game_state.server.sent_datetime_ping_request
        if sent_datetime_ping_request is None:
            self.logger.error("PongEvent received without pending PingRequest")
            raise ValueError("latency should not be None :")
        self.game_state.server.latency = floor(
            (datetime.now() - sent_datetime_ping_request).total_seconds() * 1_000
        )
        self.game_state.server.sent_datetime_ping_request = None

from dataclasses import dataclass

from DBDofusUnity.datas.protos.non_obf.game.exchange_pb2 import ObjectAveragePricesRequest
from DBDofusUnity.datas.protos.non_obf.game.bak_pb2 import BakApiTokenRequest
from DBDofusUnity.datas.protos.non_obf.game.character_management_pb2 import (
    CharacterForceSelectionEvent,
    CharacterForceSelectionReadyRequest,
    CharacterListEvent,
    CharacterListRequest,
    CharacterSelectionEvent,
    CharacterSelectionRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.chat_pb2 import Channel, SubscribeMultipleChannelRequest
from DBDofusUnity.datas.protos.non_obf.game.connection_pb2 import (
    AuthenticationTicketAcceptedEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.connection_pb2 import (
    IdentificationRequest as GameIdentificationRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.contact_pb2 import (
    AcquaintanceListRequest,
    ContactLookRequest,
    ContactWarnOnAchievementCompleteSetRequest,
    ContactWarnOnPermanentDeathSetRequest,
    FriendListRequest,
    FriendSetStatusShareRequest,
    FriendSetWarnOnLevelGainRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.context_pb2 import ContextCreationRequest
from DBDofusUnity.datas.protos.non_obf.game.guild_information_pb2 import GuildInformationRequest
from DBDofusUnity.datas.protos.non_obf.game.guild_member_pb2 import (
    GuildMemberWarnOnConnectionStartRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.guild_mission_pb2 import ServerMaintenanceInformationRequest
from DBDofusUnity.datas.protos.non_obf.game.social_pb2 import (
    ChatCommunityChannelSetCommunityRequest,
    SpouseInformationRequest,
)
from src.core.behaviors.account.character_creation_behavior import (
    CharacterCreationBehavior,
)
from src.core.behaviors.behavior import Behavior
from src.services.human_timings import get_random_range

_POST_SELECTION_DELAY: tuple[float, float] = (1.00, 1.10)
_CONTEXT_CREATION_DELAY: tuple[float, float] = (0.25, 0.30)
_POST_CONTEXT_CREATION_DELAY: tuple[float, float] = (0.12, 0.15)
_FINAL_SERVICE_ACTIVATION_DELAY: tuple[float, float] = (0.08, 0.13)
_AUTHENTICATION_TICKET_ACCEPTED_TIMEOUT_SECONDS = 15.0

_CHANNELS_ENABLED: list[Channel] = [
    Channel.GLOBAL,
    Channel.TEAM,
    Channel.PRIVATE,
    Channel.INFO,
    Channel.FIGHT_LOG,
]
_CHANNELS_DISABLED: list[Channel] = [
    Channel.GUILD,
    Channel.ALLIANCE,
    Channel.PARTY,
    Channel.SALES,
    Channel.SEEK,
    Channel.ADMIN,
    Channel.ARENA,
    Channel.COMMUNAUTY,
    Channel.EVENT,
    Channel.EXCHANGE,
    Channel.TERRITORY,
    Channel.GUILD_RAID,
]


@dataclass
class HandshakeBehavior(Behavior):
    character_creation_behavior: CharacterCreationBehavior

    def run(self, ticket: str) -> None:
        self.event_manager.on(
            AuthenticationTicketAcceptedEvent,
            self.on_authentication_ticket_accepted_event,
            originator=self,
            once=True,
            timeout=_AUTHENTICATION_TICKET_ACCEPTED_TIMEOUT_SECONDS,
            on_timeout=self.on_authentication_ticket_accepted_timeout,
        )
        self.event_manager.on(
            CharacterListEvent,
            self.on_character_list_event,
            originator=self,
            once=True,
        )
        self.event_manager.on(
            CharacterForceSelectionEvent, self.on_character_force_selection_event, originator=self, once=True
        )
        self.event_manager.on(CharacterSelectionEvent, self.on_character_selection_event, originator=self)
        self.event_manager.send(GameIdentificationRequest(ticket_key=ticket, language_code="fr"))

    def on_authentication_ticket_accepted_timeout(self) -> None:
        request_disconnect = self.event_manager.request_disconnect_callback
        assert request_disconnect is not None, (
            "Socket handshake timeout requires a request_disconnect_callback"
        )
        request_disconnect()

    def on_authentication_ticket_accepted_event(self, _msg: AuthenticationTicketAcceptedEvent) -> None:
        self.event_manager.send(CharacterListRequest())

    def on_character_list_event(self, msg: CharacterListEvent) -> None:
        character_count = len(msg.characters)

        if character_count == 0:
            self.character_creation_behavior.start(parent=self, callback=None)
        else:
            character = msg.characters[0]
            self.event_manager.send(CharacterSelectionRequest(character_id=character.id))
            self.event_manager.send(CharacterSelectionRequest(character_id=character.id))

    def on_character_force_selection_event(self, _msg: CharacterForceSelectionEvent) -> None:
        self.event_manager.send(CharacterForceSelectionReadyRequest())

    def on_character_selection_event(self, message: CharacterSelectionEvent) -> None:
        if not message.HasField("success"):
            return

        self.event_manager.clear_listener_by_origin_and_type(CharacterSelectionEvent, self)
        self.run_timer(
            get_random_range(_POST_SELECTION_DELAY, is_weighted=False), self._send_pre_context_creation_batch
        )

    def _send_pre_context_creation_batch(self) -> None:
        self.event_manager.send(ContactWarnOnAchievementCompleteSetRequest(enable=False))
        self.event_manager.send(FriendSetStatusShareRequest(share=False))
        self.event_manager.send(FriendSetWarnOnLevelGainRequest(enable=False))
        self.event_manager.send(
            ContactLookRequest(contact_type=ContactLookRequest.SocialContactCategory.FRIEND)
        )
        self.event_manager.send(ContactWarnOnPermanentDeathSetRequest(enable=False))
        info_type = GuildInformationRequest.InformationType
        self.event_manager.send(GuildInformationRequest(information_type=info_type.INFO_PADDOCKS))
        self.event_manager.send(GuildInformationRequest(information_type=info_type.INFO_GENERAL))
        self.event_manager.send(ServerMaintenanceInformationRequest())

        self.run_timer(
            get_random_range(_CONTEXT_CREATION_DELAY, is_weighted=False),
            self._send_context_creation,
        )

    def _send_context_creation(self) -> None:
        self.event_manager.send(ContextCreationRequest())
        self.run_timer(
            get_random_range(_POST_CONTEXT_CREATION_DELAY, is_weighted=False),
            self._send_post_context_creation_batch,
        )

    def _send_post_context_creation_batch(self) -> None:
        self.event_manager.send(BakApiTokenRequest())
        self.event_manager.send(GuildMemberWarnOnConnectionStartRequest())
        self.event_manager.send(ChatCommunityChannelSetCommunityRequest(community=0))
        self.event_manager.send(
            SubscribeMultipleChannelRequest(
                channel_enabled=_CHANNELS_ENABLED,
                channel_disabled=_CHANNELS_DISABLED,
            )
        )
        self.run_timer(
            get_random_range(_FINAL_SERVICE_ACTIVATION_DELAY, is_weighted=False),
            self._send_final_service_activation_batch,
        )

    def _send_final_service_activation_batch(self) -> None:
        self.event_manager.send(ObjectAveragePricesRequest())
        self.event_manager.send(AcquaintanceListRequest())
        self.event_manager.send(FriendListRequest())
        self.event_manager.send(SpouseInformationRequest())
        self.event_manager.send(
            GuildInformationRequest(information_type=GuildInformationRequest.InformationType.INFO_PADDOCKS)
        )
        self.finish(None)

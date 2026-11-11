from dataclasses import dataclass

from DBDofusUnity.datas.protos.non_obf.game.character_management_pb2 import (
    CharacterCreationRequest,
    CharacterFirstSelectionRequest,
    CharacterListEvent,
    CharacterNameSuggestionEvent,
    CharacterNameSuggestionRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import CharacterRemodelingInformation, Gender
from DBDofusUnity.dofus_unity_reader.game_constants.breed import DEFAULT_SACRIEUR_COSMETIC_ID, BreedEnum

from src.core.behaviors.behavior import Behavior
from src.services.human_timings import HumanTimingsService


@dataclass
class CharacterCreationBehavior(Behavior):
    def run(self):
        self.event_manager.on(
            CharacterNameSuggestionEvent,
            self.on_character_name_suggestion_event,
            originator=self,
            once=True,
        )
        self.send_message_delayed(
            CharacterNameSuggestionRequest(), HumanTimingsService().get_timing_long_action()
        )

    def on_character_name_suggestion_event(self, msg: CharacterNameSuggestionEvent):
        self.event_manager.on(CharacterListEvent, self.on_character_list_event, originator=self, once=True)

        req = CharacterCreationRequest(
            modeling_information=CharacterRemodelingInformation(
                name=msg.suggestion,
                breed_id=BreedEnum.SACRIER,
                gender=Gender.FEMALE,
                colors=[-1, -1, -1, -1, -1, -1],
                cosmetic_id=DEFAULT_SACRIEUR_COSMETIC_ID,
                unknown=27,
            )
        )
        self.send_message_delayed(req, HumanTimingsService().get_timing_very_long_action())

    def on_character_list_event(self, msg: CharacterListEvent):
        assert len(msg.characters) > 0
        req = CharacterFirstSelectionRequest(character_id=msg.characters[0].id)
        self.event_manager.send(req)
        self.finish()

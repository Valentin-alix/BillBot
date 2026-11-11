from dataclasses import dataclass

from src.core.behaviors.recovery_behavior import RecoverableBehavior
from src.core.behaviors.storage.enter_chests.enter_guild_chest_behavior import (
    EnterGuildChestError,
)
from src.core.behaviors.storage.unloads.unload_in_bank_behavior import (
    UnloadInBankBehavior,
)
from src.core.behaviors.storage.unloads.unload_in_guild_chest_behavior import (
    UnloadInGuildChestBehavior,
)
from src.core.states.guild_chest_state import get_gids_by_tab
from src.exceptions import UnhandledErrorCodeException


@dataclass
class UnloadBehavior(RecoverableBehavior):
    unload_in_bank_behavior: UnloadInBankBehavior
    unload_in_guild_chest_behavior: UnloadInGuildChestBehavior

    def run(self) -> None:
        self.init_recovery_listeners()
        self.ensure_free_to_act(lambda: self.start_unloading())

    def start_unloading(self) -> None:
        self.unload_in_guild_chest_behavior.start(
            unload_item_id_by_tab=get_gids_by_tab(),
            parent=self,
            callback=self.on_unload_in_guild_chest_behavior_finished,
        )

    def on_unload_in_guild_chest_behavior_finished(self, error_code: str | None):
        if error_code is not None and error_code is not EnterGuildChestError.CANT_ACCESS_GUILD_CHEST:
            raise UnhandledErrorCodeException(error_code)
        self.unload_in_bank_behavior.start(callback=self.finish, parent=self)

import logging
from src.utils.runtime_support import RuntimeSetupError
from threading import RLock
from typing import ClassVar, Literal

from ankama_launcher_emulator.controller.bot_storage import (
    BotStorageController,
)
from ankama_launcher_emulator.controller.proxy import ProxyController
from ankama_launcher_emulator.controller.schedule_profile import (
    ScheduleProfileController,
)
from ankama_launcher_emulator.interfaces.local_storage import BotRecord
from ankama_launcher_emulator.interfaces.schedule_profile import (
    ProxyConfig,
)
from ankama_launcher_emulator.utils.proxy import (
    build_http_proxy_url,
    build_socks_proxy_url,
)
from utils.singleton import Singleton
from pydantic import BaseModel


class BotConfig(BaseModel):
    schedule_profile: str | None = None
    connection_mode: Literal["mitm", "socket"] = "socket"
    hardware_id: str | None = None


logger = logging.getLogger()


class BotConfigService(metaclass=Singleton):
    use_bot_config_json: ClassVar[bool] = True
    _BOT_CONFIG_LOCK = RLock()
    _all_configs: dict[str, BotConfig] = {}

    def _read_bot_config_json(self) -> dict[str, BotConfig]:
        return self._configs_from_records(BotStorageController().get_all_records())

    def prepare_configs(self, records: dict[str, BotRecord]) -> dict[str, BotConfig]:
        if not self.use_bot_config_json:
            with self._BOT_CONFIG_LOCK:
                for login, record in records.items():
                    self._all_configs.setdefault(
                        login, BotConfig(schedule_profile=record.schedule_profile, connection_mode="mitm")
                    )
                return dict(self._all_configs)
        return self._configs_from_records(records)

    @staticmethod
    def _configs_from_records(records: dict[str, BotRecord]) -> dict[str, BotConfig]:
        return {
            login: BotConfig(
                schedule_profile=record.schedule_profile,
                connection_mode=record.connection_mode,
                hardware_id=record.hardware_id,
            )
            for login, record in records.items()
        }

    def get_bot_config_by_login(self) -> dict[str, BotConfig]:
        if not self.use_bot_config_json:
            return self._all_configs
        return self._read_bot_config_json()

    def _write_all_configs(self, bot_configs: dict[str, BotConfig]) -> None:
        if not self.use_bot_config_json:
            self._all_configs = bot_configs
            return
        with self._BOT_CONFIG_LOCK:

            def update_configs(records: dict[str, BotRecord]) -> None:
                for login, bot_config in bot_configs.items():
                    record = records.get(login)
                    if record is None:
                        logger.warning("No BotRecord for %s; skipping bot config write", login)
                        continue
                    record.schedule_profile = bot_config.schedule_profile
                    record.connection_mode = bot_config.connection_mode

            BotStorageController().update_records(update_configs)

    def get_bot_config(self, login: str) -> BotConfig:
        with self._BOT_CONFIG_LOCK:
            bot_config_by_login = self.get_bot_config_by_login()
            bot_config = bot_config_by_login.get(login)
            if bot_config:
                return bot_config
            if self.use_bot_config_json:
                bot_config_by_login[login] = BotConfig()
            else:
                persisted_config = self._read_bot_config_json().get(login)
                schedule_profile = persisted_config.schedule_profile if persisted_config else None
                bot_config_by_login[login] = BotConfig(
                    schedule_profile=schedule_profile,
                    connection_mode="mitm",
                )
            self._write_all_configs(bot_config_by_login)
            return bot_config_by_login[login]

    def assign_mode(self, login: str, mode: Literal["socket", "mitm"]):
        with self._BOT_CONFIG_LOCK:
            all_configs = self.get_bot_config_by_login()
            config = all_configs.get(login, BotConfig())
            config.connection_mode = mode
            all_configs[login] = config
            self._write_all_configs(all_configs)

    def assign_schedule_profile(self, login: str, profile_id: str | None) -> None:
        if profile_id is not None and ScheduleProfileController().get_profile(profile_id) is None:
            raise ValueError("Unknown schedule profile.")

        def assign(record: BotRecord) -> None:
            if record.quarantine_reason is not None:
                raise ValueError("Restore the account from Activity before changing its schedule.")
            record.schedule_profile = profile_id

        try:
            BotStorageController().update_record(login, assign)
        except LookupError as error:
            raise ValueError("This account no longer exists. Refresh the list.") from error

    def resolve_bot_proxy(self, config: BotConfig) -> ProxyConfig:
        if config.schedule_profile is None:
            raise ValueError("Bot config must have a schedule profile")
        profile = ScheduleProfileController().get_profile(config.schedule_profile)
        if profile is None:
            raise RuntimeSetupError(
                f"Unknown schedule profile: {config.schedule_profile}. Check schedule_profiles.json."
            )
        return ProxyController().get_proxy(profile.proxy_id)

    def resolve_bot_http_proxy_url(self, config: BotConfig) -> str:
        return build_http_proxy_url(self.resolve_bot_proxy(config))

    def get_bot_http_proxy_url(self, login: str) -> str | None:
        config = self.get_bot_config(login)
        if config.schedule_profile is None:
            return None
        return self.resolve_bot_http_proxy_url(config)

    def resolve_bot_socks_proxy_url(self, config: BotConfig) -> str:
        return build_socks_proxy_url(self.resolve_bot_proxy(config))

    def remove_bot_config(self, login: str):
        def clear_config(record: BotRecord) -> None:
            record.schedule_profile = None
            record.connection_mode = "socket"

        BotStorageController().update_record(login, clear_config)

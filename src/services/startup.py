from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from threading import Event

lazy from ankama_launcher_emulator.controller.bot_storage import BotStorageController
lazy from ankama_launcher_emulator.controller.zaap_import import import_zaap_accounts
lazy from ankama_launcher_emulator.decrypter.crypto_helper import CryptoHelper
lazy from ankama_launcher_emulator.haapi.haapi import get_game_sub_info
lazy from ankama_launcher_emulator.interfaces.credentials import StoredApiKey
lazy from ankama_launcher_emulator.interfaces.local_storage import BotRecord

from src.services.display_catalog import load_display_catalog
from src.utils.project_paths import IS_PACKAGED
from src.utils.runtime_support import RuntimeSetupError
lazy from src.controller.bot_config import BotConfig, BotConfigService
lazy from src.controller.player_info_storage import PlayerInfoSnapshot, PlayerInfoStorage
lazy from src.core.bot.bot_manager import BotManager
lazy from src.services.release_update import (
    SKIP_UPDATE_ENV,
    clean_update_staging,
    consume_update_status,
    prepare_update,
)

logger = logging.getLogger()


@dataclass(frozen=True)
class PreparedAccounts:
    accounts: list[StoredApiKey]
    records: dict[str, BotRecord]
    configs: dict[str, BotConfig]
    snapshots: dict[str, PlayerInfoSnapshot]
    subscription_ends: dict[str, datetime | None]
    manager_type: type[BotManager] | None
    update_started: bool = False


def subscription_end_dates(records: dict[str, BotRecord]) -> dict[str, datetime | None]:
    return {
        login: get_game_sub_info(record.account_info).end_of_subscribe for login, record in records.items()
    }


def prepare_startup(
    validate: Callable[[Callable[[str], None]], None],
    progress: Callable[[str], None],
    cancelled: Event,
) -> PreparedAccounts | None:
    if IS_PACKAGED:
        clean_update_staging()
        previous_error = consume_update_status()
        if previous_error:
            logger.warning("Previous update failed: %s", previous_error)
        if os.environ.pop(SKIP_UPDATE_ENV, None) != "1":
            try:
                if prepare_update(progress):
                    return PreparedAccounts([], {}, {}, {}, {}, None, update_started=True)
            except RuntimeSetupError, OSError:
                logger.warning("Update check failed; continuing with the current installation", exc_info=True)
    if cancelled.is_set():
        return None
    validate(progress)
    load_display_catalog()
    if cancelled.is_set():
        return None
    progress("Reading accounts…")
    import_zaap_accounts()
    records = BotStorageController().get_all_records()
    accounts = CryptoHelper.getStoredApiKeys(records)
    configs = BotConfigService().prepare_configs(records)
    snapshots = PlayerInfoStorage().get_all_snapshots()
    subscription_ends = subscription_end_dates(records)
    progress("Preparing the account runtime…")
    return PreparedAccounts(accounts, records, configs, snapshots, subscription_ends, BotManager)

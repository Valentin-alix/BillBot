from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from src.controller import settings as settings_module
from src.controller.settings import SettingsService
from src.core.config import BehaviorSettings, GlobalSettings
from src.core.bot.bot import Bot
from src.utils.runtime_support import RuntimeSetupError


def test_settings_roundtrip_and_failed_write_preserve_previous_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SettingsService()
    assert service.get() == GlobalSettings()
    service.update_behaviors(BehaviorSettings(do_craft=True, do_fighter=False), True)
    service.update_sonji_key("local-key")
    persisted = GlobalSettings.model_validate_json(settings_module.SETTINGS_PATH.read_text())
    assert persisted.behaviors.do_craft
    assert not persisted.behaviors.do_fighter
    assert persisted.enable_account_automation
    assert persisted.sonji_api_key == "local-key"
    monkeypatch.setattr(service, "_settings", None)
    assert service.get() == persisted

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("test")

    monkeypatch.setattr(settings_module, "atomic_write_text", fail)
    with pytest.raises(OSError):
        service.update_sonji_key("replacement")
    assert service.get() == persisted
    assert GlobalSettings.model_validate_json(settings_module.SETTINGS_PATH.read_text()) == persisted


def test_invalid_settings_are_reported_without_resetting_file() -> None:
    settings_module.SETTINGS_PATH.write_text('{"behaviors":{"do_craft":"invalid"}}')
    with pytest.raises(RuntimeSetupError):
        SettingsService().get()
    assert "invalid" in settings_module.SETTINGS_PATH.read_text()


def test_legacy_quest_setting_is_removed_and_other_settings_are_preserved() -> None:
    settings_module.SETTINGS_PATH.write_text(
        '{"behaviors":{"do_quest":true,"do_craft":true},"sonji_api_key":"keep"}',
        encoding="utf-8",
    )

    settings = SettingsService().get()

    assert settings.behaviors.do_craft
    assert settings.sonji_api_key == "keep"
    persisted = settings_module.SETTINGS_PATH.read_text(encoding="utf-8")
    assert "do_quest" not in persisted
    assert GlobalSettings.model_validate_json(persisted) == settings


def test_service_key_precedence_and_concurrent_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SONJI_API_KEY", "environment-key")
    service = SettingsService()
    assert service.sonji_api_key() == "environment-key"
    with ThreadPoolExecutor(max_workers=2) as pool:
        key_write = pool.submit(service.update_sonji_key, "gui-key")
        behavior_write = pool.submit(service.update_behaviors, BehaviorSettings(do_craft=True), True)
        key_write.result()
        behavior_write.result()
    assert service.sonji_api_key() == "gui-key"
    assert service.get().behaviors.do_craft
    service.update_sonji_key("")
    assert service.sonji_api_key() is None
    service.update_sonji_key(None)
    assert service.sonji_api_key() == "environment-key"


def test_running_bot_keeps_settings_until_next_manual_or_planned_start(runtime_bot: Bot) -> None:
    bot = runtime_bot
    service = SettingsService()
    original = bot.game_state.settings
    changed = BehaviorSettings(do_craft=True, do_fighter=False, do_use_guild_chest=True, do_sale_hotel=False)
    service.update_behaviors(changed, False)
    assert bot.game_state.settings == original
    bot.behavior_coordinator.on_play(True)
    assert bot.game_state.settings == changed
    assert bot.game_state.guild_chest.settings == changed
    next_settings = BehaviorSettings(do_dungeon=True)
    service.update_behaviors(next_settings, False)
    assert bot.game_state.settings == changed
    start = datetime.now()
    bot.auto_bot_behavior.start_planned_session(start, start + timedelta(hours=2))
    assert bot.game_state.settings == next_settings
    bot.is_playing_event.clear()

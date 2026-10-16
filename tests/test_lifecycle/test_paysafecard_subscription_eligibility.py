from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from ankama_launcher_emulator.controller.subscription_expiration import (
    SubscribeInfo,
)
from ankama_launcher_emulator.interfaces.local_storage import (
    BotRecord,
)

import src.core.bot.lifecycle.profile_subscription_eligibility as profile_subscription_eligibility
from src.controller.bot_config import BotConfig
from src.controller.player_info_storage import PlayerInfoSnapshot
from src.core.bot.bot import Bot
from src.core.bot.lifecycle.profile_subscription_eligibility import (
    ProfileSubscriptionEligibility,
)


def _set_paysafecard_requirements(runtime_bot: Bot) -> None:
    runtime_bot.game_state.player.level = 31
    runtime_bot.game_state.inventory.kamas = 15_000


def test_paysafecard_subscription_requires_level_above_thirty(runtime_bot: Bot) -> None:
    _set_paysafecard_requirements(runtime_bot)
    handler = runtime_bot.connection_handler

    assert handler._is_eligible_for_subscription()

    runtime_bot.game_state.player.level = 30

    assert not handler._is_eligible_for_subscription()


def test_paysafecard_subscription_requires_fifteen_thousand_kamas(runtime_bot: Bot) -> None:
    _set_paysafecard_requirements(runtime_bot)
    runtime_bot.game_state.inventory.kamas = 14_999

    assert not runtime_bot.connection_handler._is_eligible_for_subscription()


def _record(login: str, profile_id: str) -> BotRecord:
    return BotRecord(email=login, hardware_id="hardware", schedule_profile=profile_id)


def _snapshot(level: int = 31, kamas: int = 15_000) -> PlayerInfoSnapshot:
    return PlayerInfoSnapshot(
        updated_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        character_id=1,
        character_name="character",
        breed_id=1,
        level=level,
        kamas=kamas,
        server_id=1,
        job_levels_by_id={},
        map_id=1,
        has_guild=False,
        guild_chest_tab_number=0,
    )


def _profile_eligibility(
    *,
    monkeypatch: pytest.MonkeyPatch,
    snapshots: dict[str, PlayerInfoSnapshot],
    logins: list[str] | None = None,
) -> ProfileSubscriptionEligibility:
    profile_logins = logins or ["buyer", "one", "two", "three", "four", "five"]
    bot_storage = Mock()
    bot_storage.get_all_records.return_value = {login: _record(login, "A") for login in profile_logins}
    player_info_storage = Mock()
    player_info_storage.get_snapshot.side_effect = snapshots.get
    monkeypatch.setattr(
        profile_subscription_eligibility,
        "BotStorageController",
        Mock(return_value=bot_storage),
    )
    monkeypatch.setattr(
        profile_subscription_eligibility,
        "PlayerInfoStorage",
        Mock(return_value=player_info_storage),
    )
    return ProfileSubscriptionEligibility()


def test_paysafecard_profile_requires_six_eligible_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    eligibility = _profile_eligibility(
        monkeypatch=monkeypatch,
        snapshots={login: _snapshot() for login in ["one", "two", "three", "four", "five"]},
    )

    assert eligibility.is_eligible("A", "buyer", 31, 15_000)


def test_paysafecard_profile_blocks_an_incomplete_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    eligibility = _profile_eligibility(
        monkeypatch=monkeypatch,
        logins=["buyer", "one", "two", "three", "four"],
        snapshots={login: _snapshot() for login in ["one", "two", "three", "four"]},
    )

    assert not eligibility.is_eligible("A", "buyer", 31, 15_000)


def test_paysafecard_profile_blocks_a_missing_or_ineligible_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = {login: _snapshot() for login in ["one", "two", "three", "four"]}
    snapshots["five"] = _snapshot(kamas=14_999)
    eligibility = _profile_eligibility(monkeypatch=monkeypatch, snapshots=snapshots)

    assert not eligibility.is_eligible("A", "buyer", 31, 15_000)

    snapshots.pop("five")

    assert not eligibility.is_eligible("A", "buyer", 31, 15_000)


def test_paysafecard_subscription_requires_an_eligible_complete_profile(
    runtime_bot: Bot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_paysafecard_requirements(runtime_bot)
    handler = runtime_bot.connection_handler
    handler.get_bot_config = Mock(return_value=BotConfig(schedule_profile="A"))
    monkeypatch.setattr(
        handler.subscription_storage,
        "get_subscribe_info",
        Mock(return_value=SubscribeInfo(is_subscribe=False, end_of_subscribe=None)),
    )
    monkeypatch.setattr(handler.paysafecard_purchase_storage, "load_purchase", Mock(return_value=None))
    monkeypatch.setattr(handler.paysafecard_pool, "load", Mock(return_value=["pin"]))
    handler.profile_subscription_eligibility.is_eligible = Mock(return_value=False)

    assert not handler._should_subscribe_with_paysafe_card()

    handler.profile_subscription_eligibility.is_eligible.assert_called_once_with("A", "TEST", 31, 15_000)

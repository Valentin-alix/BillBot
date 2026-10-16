import asyncio
import threading
from typing import cast
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from ankama_launcher_emulator.interfaces.local_storage import BotRecord
from ankama_launcher_emulator.interfaces.schedule_profile import (
    ProxyConfig,
    ScheduleProfile,
)

from src.controller.settings import SettingsService
from src.core.bot.lifecycle.account_scheduler import AccountScheduler, _AuthOp, _RegisterOp
from src.core.config import GlobalSettings


def _profile(proxy_id: str) -> ScheduleProfile:
    return ScheduleProfile(name_fr=proxy_id, proxy_id=proxy_id, slots_by_day={})


def _bot_record(email: str, schedule_profile: str | None) -> BotRecord:
    return BotRecord(
        email=email,
        password="password",
        hardware_id=f"hw-{email}",
        schedule_profile=schedule_profile,
    )


def _mock(value: object) -> Mock:
    return cast(Mock, value)


def _scheduler(
    profiles: dict[str, ScheduleProfile],
    *,
    accounts_needing_auth: list[BotRecord] | None = None,
) -> AccountScheduler:
    profile_controller = Mock()
    profile_controller.get_all_profiles.return_value = profiles
    proxy_controller = Mock()
    proxy_controller.get_proxy.return_value = ProxyConfig(
        host="localhost",
        http_port=8080,
        socks_port=1080,
        username="user",
        password="password",
    )
    bot_storage_controller = Mock()
    bot_storage_controller.get_accounts_needing_auth.return_value = accounts_needing_auth or []
    bot_storage_controller.get_generated_account_records.return_value = []
    scheduler = AccountScheduler(
        on_accounts_synchronized=Mock(),
        on_banned_callback=Mock(),
        schedule_profile_controller=profile_controller,
        proxy_controller=proxy_controller,
        bot_storage_controller=bot_storage_controller,
    )
    scheduler._pool = Mock()
    scheduler._pool.has_quota.return_value = True
    scheduler._pool.has_quota_for.return_value = True
    scheduler._pool.is_register_cooled_down.return_value = False
    scheduler._pool.count_since_hour.return_value = 0
    return scheduler


def test_account_automation_setting_stops_authentication_and_registration() -> None:
    scheduler = _scheduler({"B": _profile("B")}, accounts_needing_auth=[_bot_record("a@example.com", "B")])

    with patch.object(SettingsService, "get", return_value=GlobalSettings(enable_account_automation=False)):
        assert scheduler._next_operation(0) is None

    _mock(scheduler.bot_storage_controller.get_accounts_needing_auth).assert_not_called()


def test_missing_sonji_key_does_not_schedule_registration_without_email() -> None:
    scheduler = _scheduler({"B": _profile("B")})
    mail_accounts = MagicMock()
    mail_accounts.peek_next_available_email.return_value = None
    mail_accounts.load_bad_state_emails.return_value = set()

    with (
        patch("src.core.bot.lifecycle.account_scheduler.MailAccountController", return_value=mail_accounts),
        patch.object(SettingsService, "sonji_api_key", return_value=None),
        patch.object(SettingsService, "get", return_value=GlobalSettings(enable_account_automation=True)),
    ):
        assert scheduler._next_operation(0) is None


def test_stored_email_schedules_registration_without_sonji_key() -> None:
    scheduler = _scheduler({"B": _profile("B")})
    mail_accounts = MagicMock()
    mail_accounts.peek_next_available_email.return_value = "stored@example.com"
    mail_accounts.load_bad_state_emails.return_value = set()

    with (
        patch("src.core.bot.lifecycle.account_scheduler.MailAccountController", return_value=mail_accounts),
        patch.object(SettingsService, "sonji_api_key", return_value=None),
        patch.object(SettingsService, "get", return_value=GlobalSettings(enable_account_automation=True)),
    ):
        assert scheduler._next_operation(0) == _RegisterOp("B", "B")


def test_pending_account_is_authenticated_before_registration() -> None:
    scheduler = _scheduler(
        {"B": _profile("B")},
        accounts_needing_auth=[_bot_record("pending@example.com", "B")],
    )
    mail_accounts = MagicMock()
    mail_accounts.load_bad_state_emails.return_value = set()

    with (
        patch("src.core.bot.lifecycle.account_scheduler.MailAccountController", return_value=mail_accounts),
        patch.object(SettingsService, "get", return_value=GlobalSettings(enable_account_automation=True)),
    ):
        assert scheduler._next_operation(0) == _AuthOp("pending@example.com", "B", "B")


def test_register_operation_is_cancelled_during_shutdown() -> None:
    scheduler = _scheduler({"B": _profile("B")})
    operation_started = threading.Event()
    operation_cancelled = threading.Event()

    async def active_operation(_schedule_profile: str) -> None:
        operation_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            operation_cancelled.set()
            raise

    with (
        patch.object(scheduler, "_next_operation", return_value=_RegisterOp("B", "B")),
        patch(
            "src.core.bot.lifecycle.account_scheduler.register_next_available_email",
            new=AsyncMock(side_effect=active_operation),
        ),
    ):
        scheduler.start()
        assert operation_started.wait(timeout=1)
        scheduler.stop()

    assert operation_cancelled.is_set()
    assert scheduler._thread is None

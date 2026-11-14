from datetime import UTC, datetime
from pathlib import Path

from ankama_launcher_emulator.controller import bot_storage
from ankama_launcher_emulator.haapi.haapi import (
    get_account_info_by_login,
    get_game_sub_info_by_login,
    upsert_settings_account,
)
from ankama_launcher_emulator.interfaces.game import GameIdEnum
from ankama_launcher_emulator.interfaces.local_storage import BotRecord
from ankama_launcher_emulator.interfaces.zaap_files import (
    GameSubscription,
    UserAccount,
)

from tests.fixtures.launcher import IsolatedLauncherStorageTestCase


def _make_account(login: str) -> UserAccount:
    return UserAccount(
        login=login,
        nickname="nick",
        id=42,
        type="ANKAMA",
        firstname="first",
        lastname="last",
        tag="#0001",
        security=[],
        locked="",
        gameList=[
            GameSubscription(
                isFreeToPlay=False,
                isFormerSubscriber=False,
                isSubscribed=True,
                totalPlayTime=0,
                endOfSubscribe=datetime(2030, 1, 1, tzinfo=UTC),
                id=GameIdEnum.DOFUS,
            )
        ],
    )


def _update_password_repeatedly(
    storage_path: str,
    login: str,
    password_prefix: str,
) -> None:
    bot_storage.BOTS_STORAGE_PATH = Path(storage_path)
    storage_controller = bot_storage.BotStorageController()
    for update_index in range(10):
        password = f"{password_prefix}-{update_index}"
        storage_controller.upsert_record(
            login,
            create=lambda: BotRecord(email=login, password=password, hardware_id="hw-1"),
            update=lambda record: setattr(record, "password", password),
        )


class TestAccountInfoRoundTrip(IsolatedLauncherStorageTestCase):
    def test_upsert_then_load_recovers_account_and_subscription(self) -> None:
        bot_storage.BotStorageController().upsert_record(
            "user@example.com",
            create=lambda: BotRecord(email="user@example.com", password="secret", hardware_id="hw-1"),
            update=lambda record: None,
        )
        upsert_settings_account("user@example.com", _make_account("user@example.com"))
        loaded = get_account_info_by_login("user@example.com")
        subscription = get_game_sub_info_by_login("user@example.com")

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.login, "user@example.com")
        self.assertEqual(subscription.id, GameIdEnum.DOFUS)
        self.assertTrue(subscription.is_subscribed)

    def test_upsert_preserves_other_bot_fields(self) -> None:
        bot_storage.BotStorageController().upsert_record(
            "user@example.com",
            create=lambda: BotRecord(email="user@example.com", password="secret", hardware_id="hw-1"),
            update=lambda record: setattr(record, "password", "secret"),
        )
        upsert_settings_account("user@example.com", _make_account("user@example.com"))
        record = bot_storage.BotStorageController().get_record("user@example.com")

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.password, "secret")

from pathlib import Path

from pytest import MonkeyPatch

from ankama_launcher_emulator.controller.bot_storage import (
    BotStorageController,
)
from ankama_launcher_emulator.controller.mail_account import (
    MailAccountController,
)
from ankama_launcher_emulator.interfaces.local_storage import BotRecord


def test_bot_quarantine_preserves_record_and_blocks_authentication(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "ankama_launcher_emulator.controller.bot_storage.BOTS_STORAGE_PATH",
        tmp_path / "bots.json",
    )
    storage = BotStorageController()
    storage.upsert_record(
        "bot@example.com",
        create=lambda: BotRecord(email="bot@example.com", password="secret", hardware_id="hw"),
        update=lambda _: None,
    )

    storage.quarantine("bot@example.com", "Invalid authentication")

    record = storage.get_record("bot@example.com")
    assert record is not None
    assert record.password == "secret"
    assert record.quarantine_reason == "Invalid authentication"
    assert storage.get_accounts_needing_auth() == []

    storage.restore_from_quarantine("bot@example.com")

    assert storage.get_accounts_needing_auth()[0].email == "bot@example.com"


def test_mailbox_quarantine_keeps_the_entry_until_user_deletes_it(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "ankama_launcher_emulator.controller.mail_account.MAIL_ACCOUNTS_STORAGE_PATH",
        tmp_path / "mail_accounts.json",
    )
    controller = MailAccountController()
    controller.record_bad_state("mail@example.com")

    assert controller.quarantine("mail@example.com", "Confirmed WAF/CloudFront block") is True

    entry = controller.get_all_entries()["mail@example.com"]
    assert entry.bad_state is True
    assert entry.quarantine_reason == "Confirmed WAF/CloudFront block"

    controller.restore_from_quarantine("mail@example.com")

    restored = controller.get_all_entries()["mail@example.com"]
    assert restored.bad_state is False
    assert restored.quarantine_reason is None


def test_unknown_mailbox_is_not_created_by_quarantine(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "ankama_launcher_emulator.controller.mail_account.MAIL_ACCOUNTS_STORAGE_PATH",
        tmp_path / "mail_accounts.json",
    )

    assert MailAccountController().quarantine("unknown@example.com", "Error") is False
    assert MailAccountController().get_all_entries() == {}

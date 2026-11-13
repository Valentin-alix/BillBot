import pytest

from ankama_launcher_emulator.controller.mail_account import MailAccountController
from ankama_launcher_emulator.interfaces.mail_account import ManualAccountConfig, SmailProAccountConfig


def test_mail_edits_preserve_usage_quarantine_and_consumed_messages() -> None:
    controller = MailAccountController()
    email = "test@example.com"
    config = SmailProAccountConfig(api_key="key", email=email, timestamp=123)
    controller.save_config(email, config, create=True)
    controller.mark_used(email)
    controller.quarantine(email, "test reason")
    controller.record_smailpro_message_consumed(email, "message-1")
    controller.save_config(email, config.model_copy(update={"api_key": "new-key"}))
    entry = controller.get_all_entries()[email]
    assert entry.is_used and entry.bad_state
    assert entry.quarantine_reason == "test reason"
    assert isinstance(entry.config, SmailProAccountConfig)
    assert entry.config.consumed_message_ids == ["message-1"]
    with pytest.raises(ValueError):
        controller.save_config(email, ManualAccountConfig(), create=True)

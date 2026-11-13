import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from ankama_launcher_emulator.controller import bot_storage, zaap_import
from ankama_launcher_emulator.decrypter import crypto_helper as crypto_module
from ankama_launcher_emulator.decrypter.crypto_helper import CryptoHelper
from ankama_launcher_emulator.interfaces.credentials import DecipheredApiKey

_UUID = "uuid"


def _write_zaap_files(zaap_path: Path, login: str, account_id: int) -> None:
    settings: dict[str, list[dict[str, Any]]] = {
        "USER_ACCOUNTS": [
            {
                "login": login,
                "nickname": "nick",
                "id": account_id,
                "type": "ANKAMA",
                "firstname": "f",
                "lastname": "l",
                "tag": "0001",
                "security": [],
                "locked": "0",
                "gameList": [],
            }
        ]
    }
    zaap_path.mkdir()
    (zaap_path / "Settings").write_text(json.dumps(settings), encoding="utf-8")

    keydata_dir = zaap_path / "keydata"
    keydata_dir.mkdir()
    api_key = DecipheredApiKey(
        key="secured-key",
        provider="ankama",
        refreshToken="refresh-token",
        isStayLoggedIn=True,
        accountId=account_id,
        login=login,
        certificate=None,
        refreshDate=0,
    )
    encrypted = CryptoHelper.encrypt(api_key, _UUID)
    (keydata_dir / f".keydata{account_id}").write_text(encrypted, encoding="utf-8")


def test_imports_account_with_matching_user_account_and_api_key(tmp_path: Path) -> None:
    zaap_path = tmp_path / "zaap"
    with patch.object(crypto_module.Device, "getUUID", return_value=_UUID):
        _write_zaap_files(zaap_path, "user@example.com", 42)
        with patch.object(zaap_import, "ZAAP_PATH", zaap_path):
            zaap_import.import_zaap_accounts()

        record = bot_storage.BotStorageController().get_record("user@example.com")

    assert record is not None
    assert record.password is None
    assert record.account_info is not None
    assert record.account_info.nickname == "nick"
    assert record.encrypted_api_key is not None
    decrypted = CryptoHelper.decrypt(record.encrypted_api_key, _UUID)
    assert DecipheredApiKey.model_validate_json(decrypted).login == "user@example.com"


def test_skips_keydata_without_matching_user_account(tmp_path: Path) -> None:
    zaap_path = tmp_path / "zaap"
    with patch.object(crypto_module.Device, "getUUID", return_value=_UUID):
        _write_zaap_files(zaap_path, "user@example.com", 42)
        (zaap_path / "Settings").write_text(json.dumps({"USER_ACCOUNTS": []}), encoding="utf-8")
        with patch.object(zaap_import, "ZAAP_PATH", zaap_path):
            zaap_import.import_zaap_accounts()

        record = bot_storage.BotStorageController().get_record("user@example.com")

    assert record is None


def test_invalid_account_entry_does_not_block_valid_account(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    zaap_path = tmp_path / "zaap"
    _write_zaap_files(zaap_path, "user@example.com", 42)
    settings_path = zaap_path / "Settings"
    settings: dict[str, list[dict[str, Any]]] = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["USER_ACCOUNTS"].insert(0, {"id": "invalid"})
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    with (
        patch.object(zaap_import, "ZAAP_PATH", zaap_path),
        patch.object(crypto_module.Device, "getUUID", return_value=_UUID),
        caplog.at_level(logging.WARNING, logger=zaap_import.__name__),
    ):
        zaap_import.import_zaap_accounts()

    assert bot_storage.BotStorageController().get_record("user@example.com") is not None
    assert "Skipping unparsable zaap USER_ACCOUNTS entry" in caplog.text


@pytest.mark.parametrize(
    "keydata",
    [
        "missing-separator",
        "invalid|hex",
        "00|00",
        "00" * 16 + "|" + "00" * 16,
        pytest.param(None, id="invalid-api-key-schema"),
    ],
)
def test_invalid_keydata_is_skipped(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    keydata: str | None,
) -> None:
    zaap_path = tmp_path / "zaap"
    _write_zaap_files(zaap_path, "user@example.com", 42)
    if keydata is None:
        keydata = CryptoHelper.encrypt("invalid api key schema", _UUID)
    (zaap_path / "keydata" / ".keydata42").write_text(keydata, encoding="utf-8")

    with (
        patch.object(zaap_import, "ZAAP_PATH", zaap_path),
        patch.object(crypto_module.Device, "getUUID", return_value=_UUID),
        caplog.at_level(logging.WARNING, logger=zaap_import.__name__),
    ):
        zaap_import.import_zaap_accounts()

    assert bot_storage.BotStorageController().get_record("user@example.com") is None
    assert "Failed to decrypt zaap keydata file .keydata42" in caplog.text


def test_internal_account_validation_error_propagates(tmp_path: Path) -> None:
    zaap_path = tmp_path / "zaap"
    _write_zaap_files(zaap_path, "user@example.com", 42)

    with (
        patch.object(zaap_import, "ZAAP_PATH", zaap_path),
        patch.object(zaap_import.UserAccount, "model_validate", side_effect=TypeError("validation bug")),
        pytest.raises(TypeError, match="validation bug"),
    ):
        zaap_import.import_zaap_accounts()


def test_internal_decryption_error_propagates(tmp_path: Path) -> None:
    zaap_path = tmp_path / "zaap"
    _write_zaap_files(zaap_path, "user@example.com", 42)

    with (
        patch.object(zaap_import, "ZAAP_PATH", zaap_path),
        patch.object(crypto_module.Device, "getUUID", return_value=_UUID),
        patch.object(CryptoHelper, "decrypt", side_effect=RuntimeError("decryption bug")),
        pytest.raises(RuntimeError, match="decryption bug"),
    ):
        zaap_import.import_zaap_accounts()

from typing import cast
from unittest.mock import MagicMock

import pytest

from DBDofusUnity.datas.protos.non_obf.connection.login_message_pb2 import (
    IdentificationResponse,
    LoginMessage,
    Response,
)
from src.core.bot.bot import Bot
from src.core.mitm.connection_proxy import ConnectionProxy
from src.protocol.protocol import encode_msg


def test_identification_reason_14_removes_mitm_bot(
    runtime_bot: Bot, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_banned_callback = MagicMock()
    kill_process = MagicMock()
    monkeypatch.setattr(runtime_bot.process_manager, "kill_process", kill_process)
    connection_proxy = object.__new__(ConnectionProxy)
    connection_proxy.bot = runtime_bot
    connection_proxy.bot_by_id = {}
    connection_proxy.on_banned_callback = on_banned_callback
    message = LoginMessage(
        response=Response(
            identification=IdentificationResponse(
                error=IdentificationResponse.Error(
                    reason=cast(IdentificationResponse.Error.Reason, 14)
                )
            )
        )
    )
    message_datas = encode_msg(message)

    result = connection_proxy.alter_msg_datas(message.SerializeToString(), message_datas)

    kill_process.assert_called_once_with()
    on_banned_callback.assert_called_once_with(runtime_bot.account.apikey.login)
    assert result == message_datas


def test_non_ban_identification_error_keeps_mitm_bot(
    runtime_bot: Bot, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_banned_callback = MagicMock()
    kill_process = MagicMock()
    monkeypatch.setattr(runtime_bot.process_manager, "kill_process", kill_process)
    connection_proxy = object.__new__(ConnectionProxy)
    connection_proxy.bot = runtime_bot
    connection_proxy.bot_by_id = {}
    connection_proxy.on_banned_callback = on_banned_callback
    message = LoginMessage(
        response=Response(
            identification=IdentificationResponse(
                error=IdentificationResponse.Error(
                    reason=IdentificationResponse.Error.Reason.WRONG_CREDENTIALS
                )
            )
        )
    )
    message_datas = encode_msg(message)

    connection_proxy.alter_msg_datas(message.SerializeToString(), message_datas)

    kill_process.assert_called_once_with()
    on_banned_callback.assert_not_called()

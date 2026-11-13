from typing import cast
from unittest.mock import MagicMock

import pytest
from google.protobuf.message import Message

from src.core.mitm import game_proxy as game_proxy_module
from src.core.mitm.game_proxy import GameProxy
from DBDofusUnity.datas.protos.non_obf.game.spell_pb2 import SpellsEvent


def test_alter_message_decoding_does_not_capture_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_message = SpellsEvent()
    proxy = object.__new__(GameProxy)
    proxy.uid = 1
    proxy.bot = MagicMock()
    proxy.bot.event_manager.alter_msg.return_value = (clear_message, False)
    get_message = MagicMock(return_value=("root", clear_message, cast(Message, MagicMock()), -1))

    def decode_size(_: bytes) -> tuple[int, int]:
        return 1, 0

    monkeypatch.setattr(game_proxy_module, "get_game_msg", get_message)
    monkeypatch.setattr(game_proxy_module, "decode_varint_size", decode_size)

    original_packet = b"x"
    assert proxy.alter_msg_datas(original_packet, original_packet) == original_packet

    get_message.assert_called_once_with(original_packet, False, from_server=False)


@pytest.mark.parametrize(
    ("was_send_from_proxy", "expected_capture"),
    [(False, True), (True, False)],
)
@pytest.mark.parametrize("from_server", [True, False])
def test_proxy_captures_only_messages_not_injected_by_framework(
    monkeypatch: pytest.MonkeyPatch,
    was_send_from_proxy: bool,
    expected_capture: bool,
    from_server: bool,
) -> None:
    clear_message = SpellsEvent()
    proxy = object.__new__(GameProxy)
    proxy.uid = 1
    proxy.bot = MagicMock()
    proxy.bot.debug_recorder = None
    proxy.bot.msg_info_signals.capture_enabled = False
    get_message = MagicMock(return_value=("root", clear_message, cast(Message, MagicMock()), -1))

    def decode_size(_: bytes) -> tuple[int, int]:
        return 1, 0

    monkeypatch.setattr(game_proxy_module, "get_game_msg", get_message)
    monkeypatch.setattr(game_proxy_module, "decode_varint_size", decode_size)
    monkeypatch.setattr(game_proxy_module.config, "DEBUG", True)

    proxy.on_sent_msg_datas(b"x", was_send_from_proxy, from_server=from_server)

    get_message.assert_called_once_with(b"x", expected_capture, from_server=from_server)

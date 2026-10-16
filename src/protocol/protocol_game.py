import atexit
import datetime
import logging
import signal
import sys
from types import TracebackType

from google.protobuf import descriptor_pool
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message
from google.protobuf.message_factory import GetMessageClass

from DBDofusUnity.proto_mapper_assembly.runtime.runtime_store import RuntimeDataStore
from src.protocol.message import MessageInfo
from src.services.protocol_mapping import get_translator

logger = logging.getLogger(__name__)
POOL = descriptor_pool.Default()


def _on_exit(*_: object) -> None:
    RuntimeDataStore().write_captured_content()


_default_excepthook = sys.excepthook


def _on_except_hook(exc_type: type[BaseException], value: BaseException, tb: TracebackType | None) -> None:
    _on_exit()
    _default_excepthook(exc_type, value, tb)


sys.excepthook = _on_except_hook
signal.signal(signal.SIGTERM, _on_exit)
signal.signal(signal.SIGINT, _on_exit)
atexit.register(_on_exit)


def _message(name: str, payload: bytes) -> Message:
    return GetMessageClass(POOL.FindMessageTypeByName(name)).FromString(payload)


def get_game_msg(
    content: bytes, do_dump_values: bool, *, from_server: bool
) -> tuple[str, Message | None, Message, int]:
    module = get_translator()
    info = module.inspect_envelope(content)
    obfuscated = _message(info["message_type"], info["payload"])
    if do_dump_values:
        store = RuntimeDataStore()
        store.add_msg(_message(info["envelope_type"], content), from_server=None, is_game_msg=True)
        store.add_msg(obfuscated, from_server=from_server, is_game_msg=False)
    try:
        name, payload, root_type, uid = module.decode(content)
        clear = _message(name, payload)
    except ValueError as error:
        if str(error) not in ("missing_mapping", "unknown_type"):
            raise
        logger.warning("No native mapping found for %s", info["message_type"])
        return info["root_type"], None, obfuscated, info["uid"]
    return root_type, clear, obfuscated, uid


def get_obf_game_message_from_msg(
    root_msg_namespace: str, clear_sub_msg: Message, uid: int | None = None
) -> tuple[Message, Message] | None:
    module = get_translator()
    try:
        payload = module.encode(
            clear_sub_msg.DESCRIPTOR.full_name,
            clear_sub_msg.SerializeToString(),
            root_msg_namespace,
            uid or -1,
        )
    except ValueError as error:
        if str(error) not in ("missing_mapping", "unknown_type"):
            raise
        logger.warning("No native mapping found for %s", clear_sub_msg.DESCRIPTOR.full_name)
        return None
    info = module.inspect_envelope(payload)
    return _message(info["envelope_type"], payload), _message(info["inner_type"], info["inner_payload"])


def get_game_msg_info(
    clear_sub_msg: Message | None,
    obf_sub_msg: Message,
    uid_value: int | None,
    from_server: bool,
) -> MessageInfo:
    received_msg_time = datetime.datetime.now()

    if clear_sub_msg is not None:
        msg_json = MessageToDict(
            clear_sub_msg,
            always_print_fields_with_no_presence=True,
            preserving_proto_field_name=True,
        )
        sub_msg_info = (clear_sub_msg, clear_sub_msg.DESCRIPTOR.full_name)
    else:
        msg_json = None
        sub_msg_info = (obf_sub_msg, obf_sub_msg.DESCRIPTOR.full_name)

    if msg_json and uid_value is not None:
        msg_json["uid"] = uid_value

    return MessageInfo(
        received_time=received_msg_time,
        from_server=from_server,
        msg_json=msg_json,
        sub_msg_name=f"{obf_sub_msg.DESCRIPTOR.full_name} -> {sub_msg_info[0].__class__.__name__}",
        obf_msg_json=MessageToDict(
            obf_sub_msg, always_print_fields_with_no_presence=True, preserving_proto_field_name=True
        ),
    )

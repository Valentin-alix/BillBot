import atexit
import datetime
import json
import logging
import signal
import sys
from collections.abc import Callable, Mapping
from functools import cache
from pathlib import Path
from types import TracebackType
from typing import Any

from google.protobuf import descriptor_pool
from google.protobuf.any_pb2 import Any as protoAny
from google.protobuf.descriptor import Descriptor, FieldDescriptor
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message
from google.protobuf.message_factory import GetMessageClass

from DBDofusUnity.consts import GAME_MAPPINGS_JSON_FILE
from DBDofusUnity.datas.protos.non_obf.game.game_message_pb2 import GameMessage
from DBDofusUnity.proto_mapper_assembly.interfaces.game_mappings import (
    SimpleGameMappingsDocument,
)
from DBDofusUnity.proto_mapper_assembly.runtime.runtime_store import RuntimeDataStore
from src.protocol.message import MessageInfo
from src.protocol import initialize_protocol
from utils.protobuf import is_repeated_field


logger = logging.getLogger(__name__)

TYPE_URL_PREFIX = "type.ankama.com/"
_GAME_MAPPINGS_PATH = Path(GAME_MAPPINGS_JSON_FILE)


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

POOL: descriptor_pool.DescriptorPool = descriptor_pool.Default()

FieldMappingToReal = Mapping[str, str | None]
FieldMappingToObf = Mapping[str, str]
ProtoToRealMapping = Mapping[str, tuple[str, FieldMappingToReal]]
ProtoToObfMapping = Mapping[str, tuple[str, FieldMappingToObf]]
MessageTransformer = Callable[[Message], Message]

_cached_game_mappings: SimpleGameMappingsDocument | None = None
_cached_game_mappings_mtime_ns: int | None = None


def _load_game_mappings() -> SimpleGameMappingsDocument:
    global _cached_game_mappings, _cached_game_mappings_mtime_ns

    initialize_protocol()
    current_mtime_ns = _GAME_MAPPINGS_PATH.stat().st_mtime_ns
    if _cached_game_mappings is not None and _cached_game_mappings_mtime_ns == current_mtime_ns:
        return _cached_game_mappings

    with open(_GAME_MAPPINGS_PATH, encoding="utf-8") as file:
        game_mappings = SimpleGameMappingsDocument.model_validate(json.load(file))

    _cached_game_mappings = game_mappings
    _cached_game_mappings_mtime_ns = current_mtime_ns
    return game_mappings


@cache
def get_mapping_proto_to_real() -> ProtoToRealMapping:
    return {
        mapping_info.obf_msg_namespace: (
            clear_namespace[1:],
            mapping_info.field_mapping,
        )
        for clear_namespace, mapping_info in _load_game_mappings().root.items()
    }


@cache
def get_mapping_proto_to_obf() -> ProtoToObfMapping:
    return {
        clear_namespace[1:]: (
            mapping_info.obf_msg_namespace,
            {clear_field: obf_field for obf_field, clear_field in mapping_info.field_mapping.items()},
        )
        for clear_namespace, mapping_info in _load_game_mappings().root.items()
    }


def is_usable_msg(msg_name: str) -> bool:
    return msg_name in get_mapping_proto_to_obf()


def get_game_msg(
    content: bytes, do_dump_values: bool, *, from_server: bool
) -> tuple[str, Message | None, Message, int]:
    obf_game_type_url, obf_game_field_mapping = get_mapping_proto_to_obf()[GameMessage.DESCRIPTOR.full_name]
    uid_value = -1
    msg_descriptor: Descriptor = POOL.FindMessageTypeByName(obf_game_type_url)
    msg_type = GetMessageClass(msg_descriptor)

    msg = msg_type()
    msg.ParseFromString(content)

    if do_dump_values:
        RuntimeDataStore().add_msg(msg, from_server=None, is_game_msg=True)

    msg_one_of = next(obf_field for obf_field in obf_game_field_mapping.values() if msg.HasField(obf_field))
    root_msg: Message = getattr(msg, msg_one_of)
    root_msg_any_field: protoAny | None = None

    root_msg_namespace = get_mapping_proto_to_real()[root_msg.DESCRIPTOR.full_name][0]

    for _, field_value in root_msg.ListFields():
        if field_value.__class__ == protoAny:
            root_msg_any_field = field_value
        else:
            uid_value = field_value

    if root_msg_any_field is None:
        raise ValueError("Did not found any in root msg")

    type_url = root_msg_any_field.type_url.split("/")[-1]
    sub_msg_descriptor: Descriptor = POOL.FindMessageTypeByName(type_url)
    sub_msg_type = GetMessageClass(sub_msg_descriptor)

    sub_msg_content_unpacked: Message = sub_msg_type()
    root_msg_any_field.Unpack(sub_msg_content_unpacked)

    if do_dump_values:
        RuntimeDataStore().add_msg(
            msg=sub_msg_content_unpacked,
            from_server=from_server,
            is_game_msg=False,
        )

    try:
        clear_sub_msg = get_clear_msg_from_obf(sub_msg_content_unpacked)
    except Exception:
        logger.exception("Failed to decode game message into its clear form")
        clear_sub_msg = None

    return root_msg_namespace, clear_sub_msg, sub_msg_content_unpacked, uid_value


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


def get_obf_game_message_from_msg(
    root_msg_namespace: str, clear_sub_msg: Message, uid: int | None = None
) -> tuple[Message, Message] | None:
    obf_any_msg = protoAny()
    obf_msg = get_obf_msg_from_clear(clear_sub_msg)
    if obf_msg is None:
        logger.warning("No mapping found for %s", clear_sub_msg.DESCRIPTOR.full_name)
        return None

    obf_any_msg.Pack(obf_msg, type_url_prefix=TYPE_URL_PREFIX)
    obf_any_msg.type_url = TYPE_URL_PREFIX + get_mapping_proto_to_obf()[clear_sub_msg.DESCRIPTOR.full_name][0]

    obf_sub_type_url, obf_sub_field_mapping = get_mapping_proto_to_obf()[root_msg_namespace]
    obf_sub_msg_type = GetMessageClass(POOL.FindMessageTypeByName(obf_sub_type_url))
    sub_msg_values: dict[str, Message | int] = {obf_sub_field_mapping["content"]: obf_any_msg}
    if "uid" in obf_sub_field_mapping:
        sub_msg_values[obf_sub_field_mapping["uid"]] = uid or -1
    obf_sub_msg = obf_sub_msg_type(**sub_msg_values)

    obf_game_type_url, obf_game_field_mapping = get_mapping_proto_to_obf()[GameMessage.DESCRIPTOR.full_name]
    game_content_field_name = root_msg_namespace.split(".")[-1].lower()
    obf_game_msg_type = GetMessageClass(POOL.FindMessageTypeByName(obf_game_type_url))
    obf_game_msg = obf_game_msg_type(**{obf_game_field_mapping[game_content_field_name]: obf_sub_msg})

    return obf_game_msg, obf_sub_msg


def get_clear_msg_from_obf(
    obf_msg: Message,
) -> Message | None:
    transformer = get_msg_transformer_to_clear(obf_msg.DESCRIPTOR.full_name)
    return transformer(obf_msg) if transformer else None


def get_obf_msg_from_clear(
    clear_msg: Message,
) -> Message | None:
    transformer = get_msg_transformer_to_obf(clear_msg.DESCRIPTOR.full_name)
    return transformer(clear_msg) if transformer else None


def get_msg_transformer_to_obf(msg_full_name: str):
    return get_msg_transformer(msg_full_name, get_mapping_proto_to_obf())


def get_msg_transformer_to_clear(msg_full_name: str):
    return get_msg_transformer(msg_full_name, get_mapping_proto_to_real())


def get_msg_transformer(
    msg_full_name: str,
    msg_mappings: ProtoToRealMapping | ProtoToObfMapping,
) -> MessageTransformer | None:
    initialize_protocol()
    related_mapping = msg_mappings.get(msg_full_name)
    if not related_mapping:
        return None

    output_msg_name, field_mapping = related_mapping

    try:
        output_msg_descriptor: Descriptor = POOL.FindMessageTypeByName(output_msg_name)
    except KeyError:
        return None
    output_msg_type = GetMessageClass(output_msg_descriptor)

    def transformer(msg: Message) -> Message:
        output_msg = output_msg_type()
        for msg_field, msg_field_value in msg.ListFields():
            output_msg_field_name = field_mapping.get(msg_field.name)
            if output_msg_field_name is None:
                continue
            try:
                set_field_from_mapping_field(
                    msg_field,
                    msg_field_value,
                    output_msg,
                    output_msg_field_name,
                    msg_mappings,
                )
            except (AttributeError, ValueError, OverflowError, TypeError):
                logger.debug(
                    "Failed to translate field %s -> %s", msg_field.name, output_msg_field_name, exc_info=True
                )
        return output_msg

    return transformer


def set_field_from_mapping_field(
    msg_field: FieldDescriptor,
    msg_field_value: Any,
    output_msg: Message,
    output_msg_field_name: str,
    msg_mappings: ProtoToRealMapping | ProtoToObfMapping,
) -> None:
    if is_repeated_field(msg_field):
        output_field_value = getattr(output_msg, output_msg_field_name)
        map_entry = msg_field.message_type
        is_map_field = map_entry is not None and map_entry.GetOptions().map_entry
        if is_map_field:
            assert map_entry is not None, f"Map field {msg_field.full_name} has no map-entry descriptor"
            map_value_field = map_entry.fields_by_name["value"]
            if map_value_field.type == FieldDescriptor.TYPE_MESSAGE:
                for map_key, sub_msg_value in msg_field_value.items():
                    assert isinstance(sub_msg_value, Message), (
                        f"Map field {msg_field.full_name} declares message values but contains "
                        f"{type(sub_msg_value).__name__}"
                    )
                    sub_transformer = get_msg_transformer(
                        sub_msg_value.DESCRIPTOR.full_name,
                        msg_mappings,
                    )
                    if sub_transformer is None:
                        continue
                    output_field_value[map_key].CopyFrom(sub_transformer(sub_msg_value))
            else:
                output_field_value.MergeFrom(msg_field_value)
        elif msg_field.type == FieldDescriptor.TYPE_MESSAGE:
            for sub_msg_value in msg_field_value:
                sub_msg_value: Message
                sub_transformer = get_msg_transformer(sub_msg_value.DESCRIPTOR.full_name, msg_mappings)
                if not sub_transformer:
                    continue
                sub_output_msg = sub_transformer(sub_msg_value)
                output_field_value.append(sub_output_msg)
        else:
            output_field_value.extend(msg_field_value)

    elif msg_field.type == FieldDescriptor.TYPE_MESSAGE:
        if not isinstance(msg_field_value, Message):
            return
        output_field_value = getattr(output_msg, output_msg_field_name)

        sub_transformer = get_msg_transformer(msg_field_value.DESCRIPTOR.full_name, msg_mappings)
        if sub_transformer is not None:
            try:
                output_field_value.CopyFrom(sub_transformer(msg_field_value))
            except TypeError:
                pass

    else:
        setattr(output_msg, output_msg_field_name, msg_field_value)

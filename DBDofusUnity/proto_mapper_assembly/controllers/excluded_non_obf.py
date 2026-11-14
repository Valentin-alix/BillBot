from collections.abc import Sequence
from pathlib import Path

from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage
from DBDofusUnity.consts import PROTOS_ROOT
from DBDofusUnity.proto_mapper_assembly.parsers.protobuf_dump_cs import build_dump_cs_messages_from_pb2
from DBDofusUnity.proto_mapper_assembly.interfaces.signature_overrides import SignatureOverrideEntry


def compute_excluded_non_obf_names(
    messages: Sequence[DumpCSMessage],
    *,
    protos_dir: Path = PROTOS_ROOT / "non_obf" / "game",
) -> frozenset[str]:
    valid_class_names = build_dump_cs_messages_from_pb2(protos_dir).keys()
    return frozenset(message.composed_name for message in messages if message.composed_name not in valid_class_names)


def split_excluded_non_obf_messages(
    *,
    messages: Sequence[DumpCSMessage],
    excluded_message_names: frozenset[str],
) -> tuple[list[DumpCSMessage], frozenset[str]]:
    """Return retained messages and excluded names so stale overrides cannot reintroduce exclusions."""
    kept: list[DumpCSMessage] = []
    dropped: set[str] = set()
    for message in messages:
        if message.composed_name in excluded_message_names:
            dropped.add(message.composed_name)
        else:
            kept.append(message)
    return kept, frozenset(dropped)


def drop_excluded_signature_overrides(
    *,
    overrides: dict[str, SignatureOverrideEntry],
    dropped_message_names: frozenset[str],
) -> dict[str, SignatureOverrideEntry]:
    return {name: entry for name, entry in overrides.items() if name not in dropped_message_names}

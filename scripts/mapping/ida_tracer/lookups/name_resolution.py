import re

from DBDofusUnity.proto_mapper_assembly.interfaces.il2cpp_json import MethodDefinition

_ASYNC_STATE_MACHINE_SEGMENT_PATTERN = re.compile(r"^<([^>]+)>d__(\d+)$")


def resolve_owner_class_name(
    method: MethodDefinition,
    tracking_type_lookup: dict[str, str],
) -> str | None:
    owner_type_name = method.group.rsplit("/", 1)[-1]
    return resolve_message_type_name(owner_type_name, tracking_type_lookup)


def resolve_message_type_name(
    type_name: str,
    message_type_lookup: dict[str, str],
) -> str | None:
    normalized_type_name = normalize_message_type_name(type_name)
    return message_type_lookup.get(normalized_type_name)


def normalize_message_type_name(type_name: str) -> str:
    normalized_segments = [
        _normalize_compiler_generated_segment(segment)
        for segment in type_name.strip().replace("+", ".").split(".")
        if segment
    ]
    return ".".join(normalized_segments)


def _normalize_compiler_generated_segment(segment: str) -> str:
    async_state_machine_match = _ASYNC_STATE_MACHINE_SEGMENT_PATTERN.fullmatch(segment)
    if async_state_machine_match is None:
        return segment
    generated_method_name = async_state_machine_match.group(1)
    generated_method_index = async_state_machine_match.group(2)
    return f"_{generated_method_name}_d__{generated_method_index}"

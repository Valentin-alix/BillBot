import re
from dataclasses import dataclass

from DBDofusUnity.proto_mapper_assembly.interfaces.il2cpp_json import MethodInfoPointer, TypeInfoPointer
from DBDofusUnity.proto_mapper_assembly.parsers._clr_type_utils import split_top_level_tokens
from scripts.mapping.ida_tracer.lookups.name_resolution import resolve_message_type_name
from scripts.mapping.ida_tracer.signatures.type_utils import extract_generic_inner_type
from scripts.mapping.ida_tracer.simulation.constants import (
    IENUMERABLE_TYPEINFO_PREFIX,
    KVP_VALUE_METHODINFO_PREFIX,
)

_DOT_NET_SIG_RE = re.compile(r"^(?:(\S+)\s+)?(\S+)\(([^)]*)\)$")
_IENUMERATOR_BACKTICK_PREFIX = "IEnumerator`1["
_IENUMERATOR_ANGLE_PREFIX = "IEnumerator<"
_KVP_ANGLE_PREFIX = "KeyValuePair<"
_KVP_BACKTICK_PREFIX = "KeyValuePair`2["
_KVP_TYPE_ARGUMENT_COUNT = 2
_FILTER_ANGLE_TOKEN = "filter<"
_FILTER_BACKTICK_TOKEN = "filter`1["


@dataclass(frozen=True)
class HandlerMethodInfo:
    cls: str
    method_signature: str
    method_address: int | None


def build_methodinfo_get_enumerator_lookup(
    method_info_pointers: list[MethodInfoPointer],
    message_type_lookup: dict[str, str],
) -> dict[int, str]:
    result: dict[int, str] = {}
    for method_info_pointer in method_info_pointers:
        parsed_signature = _parse_method_signature(method_info_pointer.dot_net_signature)
        if parsed_signature is None:
            continue
        return_type, method_name = parsed_signature
        if method_name == "get_Value":
            resolved = resolve_message_type_name(return_type, message_type_lookup)
            if resolved is not None and "KeyValuePair" in method_info_pointer.name:
                result[int(method_info_pointer.virtual_address, 16)] = KVP_VALUE_METHODINFO_PREFIX + resolved
            continue
        if method_name != "GetEnumerator":
            continue
        element_type = _extract_ienumerator_inner_type(return_type)
        if element_type is None:
            continue
        resolved = _resolve_ienumerator_message_type(element_type, message_type_lookup)
        if resolved is not None:
            result[int(method_info_pointer.virtual_address, 16)] = resolved
    return result


def build_ienumerator_typeinfo_lookup(
    type_info_pointers: list[TypeInfoPointer],
    message_type_lookup: dict[str, str],
) -> dict[int, str]:
    result: dict[int, str] = {}
    for type_info_pointer in type_info_pointers:
        enumerable_type = (
            extract_generic_inner_type(type_info_pointer.dot_net_type, prefix="IEnumerable<", suffix=">")
            or extract_generic_inner_type(type_info_pointer.dot_net_type, prefix="IEnumerable`1[", suffix="]")
        )
        if enumerable_type is not None:
            resolved = resolve_message_type_name(enumerable_type, message_type_lookup)
            if resolved is not None:
                result[int(type_info_pointer.virtual_address, 16)] = IENUMERABLE_TYPEINFO_PREFIX + resolved
            continue
        element_type = _extract_ienumerator_inner_type(type_info_pointer.dot_net_type)
        if element_type is None:
            continue
        resolved = _resolve_ienumerator_message_type(
            element_type, message_type_lookup, kvp_prefix="kvp_value:"
        )
        if resolved is not None:
            result[int(type_info_pointer.virtual_address, 16)] = resolved
    return result


def build_filter_typeinfo_lookup(
    type_info_pointers: list[TypeInfoPointer], message_type_lookup: dict[str, str]
) -> dict[int, str]:
    result: dict[int, str] = {}
    for type_info_pointer in type_info_pointers:
        inner_type = _extract_filter_inner_type(type_info_pointer.dot_net_type)
        if inner_type is None:
            continue
        resolved = resolve_message_type_name(inner_type, message_type_lookup)
        if resolved is not None:
            result[int(type_info_pointer.virtual_address, 16)] = resolved
    return result


def build_handler_methodinfo_lookup(
    method_info_pointers: list[MethodInfoPointer],
    message_type_lookup: dict[str, str],
) -> dict[int, HandlerMethodInfo]:
    result: dict[int, HandlerMethodInfo] = {}
    for method_info_pointer in method_info_pointers:
        parameters = parse_method_signature_parts(method_info_pointer.dot_net_signature)[2]
        if len(parameters) != 1:
            continue
        resolved = resolve_message_type_name(parameters[0], message_type_lookup)
        if resolved is None:
            continue
        result[int(method_info_pointer.virtual_address, 16)] = HandlerMethodInfo(
            cls=resolved,
            method_signature=method_info_pointer.dot_net_signature,
            method_address=(
                int(method_info_pointer.method_address, 16)
                if method_info_pointer.method_address is not None
                else None
            ),
        )
    return result


def _resolve_ienumerator_message_type(
    element_type: str,
    message_type_lookup: dict[str, str],
    kvp_prefix: str = "",
) -> str | None:
    resolved = resolve_message_type_name(element_type, message_type_lookup)
    if resolved is not None:
        return resolved
    kvp_value_type = _extract_kvp_value_type(element_type)
    if kvp_value_type is None:
        return None
    resolved_value_type = resolve_message_type_name(kvp_value_type, message_type_lookup)
    if resolved_value_type is None:
        return None
    return f"{kvp_prefix}{resolved_value_type}"


def _parse_method_signature(sig: str) -> tuple[str, str] | None:
    matched = _DOT_NET_SIG_RE.match(sig)
    if matched is None:
        return None
    return matched.group(1) or "Void", matched.group(2)


def parse_method_signature_parts(sig: str) -> tuple[str, str, list[str]]:
    matched = _DOT_NET_SIG_RE.match(sig)
    if matched is None:
        return "Void", "", []
    parameters_raw = matched.group(3).strip()
    parameters = split_top_level_tokens(parameters_raw) if parameters_raw else []
    return matched.group(1) or "Void", matched.group(2), parameters


def _extract_ienumerator_inner_type(type_name: str) -> str | None:
    normalized_type = type_name.strip()
    if normalized_type.startswith(_IENUMERATOR_BACKTICK_PREFIX) and normalized_type.endswith("]"):
        return extract_generic_inner_type(
            normalized_type,
            prefix=_IENUMERATOR_BACKTICK_PREFIX,
            suffix="]",
        )
    if normalized_type.startswith(_IENUMERATOR_ANGLE_PREFIX) and normalized_type.endswith(">"):
        return extract_generic_inner_type(
            normalized_type,
            prefix=_IENUMERATOR_ANGLE_PREFIX,
            suffix=">",
        )
    return None


def _extract_filter_inner_type(type_name: str) -> str | None:
    normalized_type = type_name.strip()
    angle_index = normalized_type.rfind(_FILTER_ANGLE_TOKEN)
    if angle_index >= 0 and normalized_type.endswith(">"):
        prefix = normalized_type[: angle_index + len(_FILTER_ANGLE_TOKEN)]
        return extract_generic_inner_type(normalized_type, prefix=prefix, suffix=">")
    backtick_index = normalized_type.rfind(_FILTER_BACKTICK_TOKEN)
    if backtick_index >= 0 and normalized_type.endswith("]"):
        prefix = normalized_type[: backtick_index + len(_FILTER_BACKTICK_TOKEN)]
        return extract_generic_inner_type(normalized_type, prefix=prefix, suffix="]")
    return None


def _extract_kvp_value_type(kvp_type: str) -> str | None:
    if kvp_type.startswith(_KVP_ANGLE_PREFIX) and kvp_type.endswith(">"):
        inner = kvp_type[len(_KVP_ANGLE_PREFIX) : -1]
    elif kvp_type.startswith(_KVP_BACKTICK_PREFIX) and kvp_type.endswith("]"):
        inner = kvp_type[len(_KVP_BACKTICK_PREFIX) : -1]
    else:
        return None
    tokens = split_top_level_tokens(inner)
    if len(tokens) != _KVP_TYPE_ARGUMENT_COUNT:
        return None
    return tokens[1]

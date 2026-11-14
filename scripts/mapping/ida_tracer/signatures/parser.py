import re

from DBDofusUnity.proto_mapper_assembly.interfaces.il2cpp_json import MethodDefinition
from DBDofusUnity.proto_mapper_assembly.parsers._clr_type_utils import (
    extract_map_inner_types,
    extract_repeated_inner_type,
    split_top_level_tokens,
)
from scripts.mapping.ida_tracer.lookups.name_resolution import (
    normalize_message_type_name,
    resolve_message_type_name,
)
from scripts.mapping.ida_tracer.signatures.type_utils import extract_generic_inner_type
from scripts.mapping.ida_tracer.signatures.types import ProtoParameterSeed

_DOT_NET_SIG_RE = re.compile(r"^(?:(\S+)\s+)?(\w+)\(([^)]*)\)$")
_NATIVE_SIG_RE = re.compile(r"^(.*?)\s+([^\s(]+)\((.*)\)$")
_NATIVE_PARAMETER_NAME_RE = re.compile(r"\s+[A-Za-z_][A-Za-z0-9_]*$")
_WHITESPACE_RE = re.compile(r"\s+")
_NATIVE_TYPE_ALIASES: dict[str, str] = {"void": "Void"}
_REPEATED_BACKTICK_PREFIX = "RepeatedField`1["
_REPEATED_ANGLE_PREFIX = "RepeatedField<"


def get_preferred_signature(method: MethodDefinition) -> str | None:
    for candidate_signature in (method.dot_net_signature, method.signature):
        if candidate_signature is None:
            continue
        if candidate_signature.strip():
            return candidate_signature
    return None


def select_signature_for_proto_tracking(
    method: MethodDefinition,
    message_type_lookup: dict[str, str],
    fallback_signature: str | None = None,
) -> str | None:
    preferred_signature = get_preferred_signature(method)
    if fallback_signature is None or not fallback_signature.strip():
        return preferred_signature
    if preferred_signature is None or not preferred_signature.strip():
        return fallback_signature
    preferred_seed_count = len(
        _extract_proto_parameter_seeds_from_signature(preferred_signature, message_type_lookup)
    )
    fallback_seed_count = len(
        _extract_proto_parameter_seeds_from_signature(fallback_signature, message_type_lookup)
    )
    if fallback_seed_count > preferred_seed_count:
        return fallback_signature
    return preferred_signature


def parse_dot_net_signature(sig: str | None) -> tuple[str, list[str]]:
    if sig is None or not sig.strip():
        return "Void", []
    if _looks_like_native_signature(sig):
        native_signature = _parse_native_signature(sig)
        if native_signature is not None:
            return native_signature
    dot_net_signature = _parse_dot_net_style_signature(sig)
    if dot_net_signature is not None:
        return dot_net_signature
    native_signature = _parse_native_signature(sig)
    if native_signature is not None:
        return native_signature
    return "Void", []


def canonicalize_function_signature_types(
    method: MethodDefinition,
    signature: str | None,
    proto_long_name_by_alias: dict[str, frozenset[str]],
    *,
    do_canonicalize_to_long_name: bool,
) -> tuple[str, list[str]]:
    return_type, parameters = parse_dot_net_signature(signature)
    if not do_canonicalize_to_long_name:
        return return_type, parameters
    return (
        _canonicalize_signature_type(return_type, method, proto_long_name_by_alias),
        [
            _canonicalize_signature_type(parameter, method, proto_long_name_by_alias)
            for parameter in parameters
        ],
    )


def _canonicalize_signature_type(
    type_name: str, method: MethodDefinition, proto_long_name_by_alias: dict[str, frozenset[str]]
) -> str:
    resolved_long_name = _resolve_long_name_from_alias(type_name, proto_long_name_by_alias)
    if resolved_long_name is not None and _is_already_long_name(type_name):
        return resolved_long_name

    normalized_type_name = normalize_message_type_name(type_name)
    candidate_types = proto_long_name_by_alias.get(normalized_type_name, frozenset())
    if not candidate_types:
        return type_name

    mangled_matches = [
        candidate_type
        for candidate_type in candidate_types
        if any(marker in method.name for marker in _iter_mangled_type_markers(candidate_type))
    ]
    if len(mangled_matches) == 1:
        return mangled_matches[0]

    if len(mangled_matches) > 1:
        error = (
            f"Ambiguous mangled parameter type for {method.name}: {type_name} -> {sorted(mangled_matches)}"
        )
        raise ValueError(error)

    if _mangled_name_contains_short_type(method.name, type_name):
        return type_name

    if len(candidate_types) != 1:
        error = f"Ambiguous parameter type for {method.name}: {type_name} -> {sorted(candidate_types)}"
        raise ValueError(error)

    return next(iter(candidate_types))


def _resolve_long_name_from_alias(
    type_name: str,
    proto_long_name_by_alias: dict[str, frozenset[str]],
) -> str | None:
    normalized_type_name = normalize_message_type_name(type_name)
    candidate_types = proto_long_name_by_alias.get(normalized_type_name)
    if candidate_types is None or len(candidate_types) != 1:
        return None
    return next(iter(candidate_types))


def _is_already_long_name(type_name: str) -> bool:
    return "." in type_name or "+" in type_name


def _mangled_name_contains_short_type(mangled_name: str, type_name: str) -> bool:
    normalized_segments = normalize_message_type_name(type_name).split(".")
    if not normalized_segments:
        return False
    leaf_segment = normalized_segments[-1]
    return f"{len(leaf_segment)}{leaf_segment}" in mangled_name


def _iter_mangled_type_markers(canonical_type: str) -> list[str]:
    normalized_segments = normalize_message_type_name(canonical_type).split(".")
    collapsed_segments = _collapse_nested_type_segments(normalized_segments)
    markers: list[str] = []
    for suffix_length in range(2, len(collapsed_segments) + 1):
        suffix_segments = collapsed_segments[-suffix_length:]
        markers.append("".join(f"{len(segment)}{segment}" for segment in suffix_segments))
    return markers


def _collapse_nested_type_segments(type_segments: list[str]) -> list[str]:
    collapsed_segments: list[str] = []
    for type_segment in type_segments:
        if type_segment == "Types" and collapsed_segments:
            collapsed_segments[-1] = f"{collapsed_segments[-1]}_Types"
            continue
        collapsed_segments.append(type_segment)
    return collapsed_segments


def extract_proto_parameter_seeds(
    method: MethodDefinition,
    message_type_lookup: dict[str, str],
    preferred_signature: str | None = None,
) -> list[ProtoParameterSeed]:
    return _extract_proto_parameter_seeds_from_signature(
        _resolve_effective_signature(method, preferred_signature),
        message_type_lookup,
    )


def method_has_this_parameter(method: MethodDefinition) -> bool:
    matched = _NATIVE_SIG_RE.match(method.signature)
    if matched is None:
        return False
    parameter_entries = _split_native_signature_parameters(matched.group(3))
    if not parameter_entries:
        return False
    return is_native_this_parameter(parameter_entries[0])


def build_function_key(method: MethodDefinition, preferred_signature: str | None = None) -> str:
    class_name = method.group.rsplit("/", 1)[-1]
    sig = preferred_signature or get_preferred_signature(method) or method.name
    return f"{class_name}::{sig}"


def is_native_this_parameter(param: str) -> bool:
    return param.strip().endswith(" this")


def is_method_info_parameter(param: str) -> bool:
    return _extract_native_parameter_type(param) == "MethodInfo"


def _looks_like_native_signature(sig: str) -> bool:
    return "MethodInfo" in sig


def _resolve_effective_signature(
    method: MethodDefinition,
    preferred_signature: str | None,
) -> str | None:
    return preferred_signature or get_preferred_signature(method)


def _parse_dot_net_style_signature(sig: str) -> tuple[str, list[str]] | None:
    matched = _DOT_NET_SIG_RE.match(sig)
    if matched is None:
        return None
    return_type = matched.group(1) or "Void"
    params_raw = matched.group(3)
    parameters = split_top_level_tokens(params_raw)
    return return_type, parameters


def _split_native_signature_parameters(params_raw: str) -> list[str]:
    return [parameter.strip() for parameter in params_raw.split(",") if parameter.strip()]


def _normalize_native_type(type_name: str) -> str:
    without_pointers = type_name.replace("*", " ")
    normalized_type = _WHITESPACE_RE.sub(" ", without_pointers).strip()
    return _NATIVE_TYPE_ALIASES.get(normalized_type, normalized_type)


def _strip_native_parameter_name(param: str) -> str:
    return _NATIVE_PARAMETER_NAME_RE.sub("", param.strip()).strip()


def _extract_native_parameter_type(param: str) -> str:
    return _normalize_native_type(_strip_native_parameter_name(param))


def _extract_explicit_native_parameter_types(sig: str) -> list[str]:
    matched = _NATIVE_SIG_RE.match(sig)
    if matched is None:
        return []
    parameter_entries = _split_native_signature_parameters(matched.group(3))
    if parameter_entries and is_native_this_parameter(parameter_entries[0]):
        parameter_entries = parameter_entries[1:]
    if parameter_entries and is_method_info_parameter(parameter_entries[-1]):
        parameter_entries = parameter_entries[:-1]
    return [_extract_native_parameter_type(parameter_entry) for parameter_entry in parameter_entries]


def _parse_native_signature(sig: str) -> tuple[str, list[str]] | None:
    matched = _NATIVE_SIG_RE.match(sig)
    if matched is None:
        return None
    return_type = _normalize_native_type(matched.group(1))
    return return_type, _extract_explicit_native_parameter_types(sig)


def _extract_proto_parameter_seeds_from_signature(
    signature: str | None,
    message_type_lookup: dict[str, str],
) -> list[ProtoParameterSeed]:
    _, param_types = parse_dot_net_signature(signature)
    resolved_parameter_seeds: list[ProtoParameterSeed] = []
    for index, param_type in enumerate(param_types):
        if param_type == "IMessage":
            resolved_parameter_seeds.append((index, "untyped_object", param_type))
            continue
        repeated_inner_type = _extract_repeated_parameter_inner_type(param_type)
        if repeated_inner_type is not None:
            resolved_repeated_type = resolve_message_type_name(repeated_inner_type, message_type_lookup)
            if resolved_repeated_type is not None:
                resolved_parameter_seeds.append((index, "repeated_container", resolved_repeated_type))
            continue
        resolved_param_type = resolve_message_type_name(param_type, message_type_lookup)
        if resolved_param_type is None:
            continue
        resolved_parameter_seeds.append((index, "object", resolved_param_type))
    return resolved_parameter_seeds


def _extract_repeated_parameter_inner_type(type_name: str) -> str | None:
    map_types = extract_map_inner_types(type_name)
    if map_types is not None:
        return map_types[1]
    if type_name.startswith("MapField`2[") and type_name.endswith("]"):
        arguments = split_top_level_tokens(type_name[len("MapField`2[") : -1])
        return arguments[1] if len(arguments) == 2 else None
    for prefix, suffix in (("IEnumerable`1[", "]"), ("IEnumerable<", ">")):
        inner = extract_generic_inner_type(type_name.strip(), prefix=prefix, suffix=suffix)
        if inner is not None:
            return inner
    repeated_inner_type = extract_repeated_inner_type(type_name)
    if repeated_inner_type is not None:
        return repeated_inner_type
    return extract_generic_inner_type(
        type_name.strip(),
        prefix=_REPEATED_BACKTICK_PREFIX,
        suffix="]",
    ) or extract_generic_inner_type(
        type_name.strip(),
        prefix=_REPEATED_ANGLE_PREFIX,
        suffix=">",
    )

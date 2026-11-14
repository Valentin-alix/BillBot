import re
from collections import defaultdict

from DBDofusUnity.proto_mapper_assembly.interfaces.il2cpp_json import MethodDefinition
from scripts.mapping.ida_tracer.lookups.il2cpp import parse_method_signature_parts

_OBFUSCATED_IDENTIFIER_RE = re.compile(r"^[a-z]{1,5}$")
_TYPE_PATH_SEPARATORS_RE = re.compile(r"[/+]")


def build_callee_identity_lookup(method_definitions: list[MethodDefinition]) -> dict[int, str]:
    """Keep assembly/namespace/type/method/arity; signature types may be obfuscated and unstable."""
    definitions_by_address: dict[int, list[MethodDefinition]] = defaultdict(list)
    for method_definition in method_definitions:
        definitions_by_address[int(method_definition.virtual_address, 16)].append(method_definition)

    callee_identity_by_address: dict[int, str] = {}
    for address, definitions in definitions_by_address.items():
        # Identical code folding makes shared native addresses ambiguous.
        if len(definitions) != 1:
            continue
        identity = _build_callee_identity(definitions[0])
        if identity is not None:
            callee_identity_by_address[address] = identity
    return callee_identity_by_address


def _build_callee_identity(method_definition: MethodDefinition) -> str | None:
    if method_definition.dot_net_signature is None:
        return None
    _, method_name, parameters = parse_method_signature_parts(method_definition.dot_net_signature)
    if not method_name or not _is_stable_path(method_definition.group) or _is_obfuscated(method_name):
        return None
    return f"{method_definition.group}::{method_name}/{len(parameters)}"


def _is_stable_path(type_path: str) -> bool:
    return all(not _is_obfuscated(segment) for segment in _TYPE_PATH_SEPARATORS_RE.split(type_path))


def _is_obfuscated(identifier: str) -> bool:
    return _OBFUSCATED_IDENTIFIER_RE.match(identifier) is not None

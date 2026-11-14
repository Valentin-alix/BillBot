from collections import defaultdict

from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage
from scripts.mapping.ida_tracer.lookups.name_resolution import normalize_message_type_name


def build_long_name_by_unique_alias(classes: list[DumpCSMessage]) -> dict[str, str]:
    aliases_by_composed_name = build_long_name_by_alias(classes)
    return {
        alias: next(iter(canonical_names))
        for alias, canonical_names in aliases_by_composed_name.items()
        if len(canonical_names) == 1
    }


def build_long_name_by_alias(classes: list[DumpCSMessage]) -> dict[str, frozenset[str]]:
    aliases_by_composed_name: dict[str, set[str]] = defaultdict(set)
    for current_class in classes:
        canonical_name = current_class.composed_name
        candidate_names: set[str] = {canonical_name}
        if current_class.namespace is not None and current_class.parent_name is not None:
            candidate_names.add(f"{current_class.namespace}.{canonical_name}")
        for candidate_name in candidate_names:
            for suffix in _iter_type_name_suffixes(candidate_name):
                aliases_by_composed_name[suffix].add(canonical_name)
    return {alias: frozenset(canonical_names) for alias, canonical_names in aliases_by_composed_name.items()}


def _iter_type_name_suffixes(type_name: str) -> list[str]:
    normalized_name = normalize_message_type_name(type_name)
    name_segments = [segment for segment in normalized_name.split(".") if segment]
    return [".".join(name_segments[index:]) for index in range(len(name_segments))]

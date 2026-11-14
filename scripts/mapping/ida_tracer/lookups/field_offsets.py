from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage, DumpCSMessageField
from DBDofusUnity.proto_mapper_assembly.interfaces.field_category import FieldCategoryEnum
from scripts.mapping.ida_tracer.lookups.name_resolution import resolve_message_type_name
from scripts.mapping.ida_tracer.lookups.type_lookup import build_long_name_by_unique_alias


def build_enum_field_by_class_offset(msgs: list[DumpCSMessage]) -> dict[str, dict[int, str]]:
    result: dict[str, dict[int, str]] = {}
    for msg in msgs:
        offset_to_enum: dict[int, str] = {}
        for field in msg.fields:
            if field.category != FieldCategoryEnum.ENUM:
                continue
            if field.enum_value_type is None:
                continue
            offset_to_enum[field.memory_offset] = field.enum_value_type
        if offset_to_enum:
            result[msg.composed_name] = offset_to_enum
    return result


def build_field_offset_lookup(
    msgs: list[DumpCSMessage],
) -> dict[str, dict[int, DumpCSMessageField]]:
    result: dict[str, dict[int, DumpCSMessageField]] = {}
    for msg in msgs:
        offset_map = {
            field.memory_offset: field for field in msg.fields if field.is_instance_backed_proto_field
        }
        if len(offset_map) == 0:
            continue
        result[msg.composed_name] = offset_map
    return result


def build_tracking_field_offset_lookup(
    classes: list[DumpCSMessage],
) -> dict[str, dict[int, DumpCSMessageField]]:
    direct_fields_by_class: dict[str, dict[int, DumpCSMessageField]] = {}
    for current_class in classes:
        offset_map = {
            field.memory_offset: field for field in current_class.fields if _is_trackable_object_field(field)
        }
        if len(offset_map) == 0:
            continue
        direct_fields_by_class[current_class.composed_name] = offset_map
    type_lookup = build_long_name_by_unique_alias(classes)
    classes_by_name = {current_class.composed_name: current_class for current_class in classes}
    inherited_fields_cache: dict[str, dict[int, DumpCSMessageField]] = {}
    result: dict[str, dict[int, DumpCSMessageField]] = {}
    for current_class in classes:
        inherited_offset_map = _collect_trackable_fields_with_bases(
            current_class,
            classes_by_name,
            direct_fields_by_class,
            type_lookup,
            inherited_fields_cache,
            visiting=frozenset(),
        )
        if len(inherited_offset_map) > 0:
            result[current_class.composed_name] = inherited_offset_map
    return result


def _collect_trackable_fields_with_bases(
    current_class: DumpCSMessage,
    classes_by_name: dict[str, DumpCSMessage],
    direct_fields_by_class: dict[str, dict[int, DumpCSMessageField]],
    type_lookup: dict[str, str],
    inherited_fields_cache: dict[str, dict[int, DumpCSMessageField]],
    *,
    visiting: frozenset[str],
) -> dict[int, DumpCSMessageField]:
    current_class_name = current_class.composed_name
    cached = inherited_fields_cache.get(current_class_name)
    if cached is not None and current_class_name not in visiting:
        return cached
    if current_class_name in visiting:
        return direct_fields_by_class.get(current_class_name, {})
    result: dict[int, DumpCSMessageField] = {}
    base_class_name = _resolve_base_class_name(current_class, type_lookup)
    if base_class_name is not None:
        base_class = classes_by_name.get(base_class_name)
        if base_class is not None:
            result.update(
                _collect_trackable_fields_with_bases(
                    base_class,
                    classes_by_name,
                    direct_fields_by_class,
                    type_lookup,
                    inherited_fields_cache,
                    visiting=visiting | frozenset({current_class_name}),
                )
            )
    result.update(direct_fields_by_class.get(current_class_name, {}))
    if current_class_name not in visiting:
        inherited_fields_cache[current_class_name] = result
    return result


def _resolve_base_class_name(
    current_class: DumpCSMessage,
    type_lookup: dict[str, str],
) -> str | None:
    if current_class.base_class_name is None:
        return None
    return resolve_message_type_name(current_class.base_class_name, type_lookup)


def _is_trackable_object_field(field: DumpCSMessageField) -> bool:
    if field.is_synthetic_oneof_variant:
        return False
    if field.clr_type.startswith("static"):
        return False
    return field.clr_type != "UnknownFieldSet"

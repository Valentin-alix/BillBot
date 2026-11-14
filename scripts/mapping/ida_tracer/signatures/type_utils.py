from DBDofusUnity.proto_mapper_assembly.interfaces.field_category import FieldTypeLeafKind
from DBDofusUnity.proto_mapper_assembly.parsers._clr_type_utils import split_top_level_tokens
from DBDofusUnity.proto_mapper_assembly.parsers.clr_types import resolve_non_container_field_kind


def is_non_message_type(
    type_name: str,
    enum_key_type: str | None,
    enum_value_type: str | None,
) -> bool:
    field_category = resolve_non_container_field_kind(type_name, enum_key_type, enum_value_type)
    return field_category != FieldTypeLeafKind.MESSAGE


def extract_generic_inner_type(
    type_name: str,
    *,
    prefix: str,
    suffix: str,
) -> str | None:
    if not type_name.startswith(prefix) or not type_name.endswith(suffix):
        return None
    inner_types = type_name[len(prefix) : -len(suffix)].strip()
    tokenized_inner_types = split_top_level_tokens(inner_types)
    if len(tokenized_inner_types) != 1:
        return None
    return tokenized_inner_types[0]

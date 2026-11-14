from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property
from typing import Literal

from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import AccessKind, FieldAccessEntry
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage, DumpCSMessageField
from DBDofusUnity.proto_mapper_assembly.interfaces.field_category import FieldCategoryEnum
from DBDofusUnity.proto_mapper_assembly.parsers._clr_type_utils import (
    extract_map_inner_types,
    extract_repeated_inner_type,
)
from scripts.mapping.ida_tracer.core.function_inspector import (
    get_operation_index_inside_function,
)
from scripts.mapping.ida_tracer.lookups.name_resolution import resolve_message_type_name
from scripts.mapping.ida_tracer.lookups.type_lookup import build_long_name_by_unique_alias
from scripts.mapping.ida_tracer.signatures.type_utils import is_non_message_type


@dataclass(frozen=True)
class AccessorCandidate:
    owner_cls: str
    access_kind: AccessKind
    field: str
    property_name: str | None
    field_offset: int
    clr_type: str | None
    normalized_type: str | None
    returned_cls: str | None
    _returned_domain: Literal["object", "repeated_container"] | None

    @cached_property
    def returned_domain(self) -> Literal["object", "repeated_container"] | None:
        if self._returned_domain:
            return self._returned_domain
        if self.returned_cls is None:
            return None
        normalized_type = self.normalized_type or self.clr_type or ""
        return (
            "repeated_container" if normalized_type.startswith(("RepeatedField<", "MapField<")) else "object"
        )

    def to_field_access_entry(self, instruction_address: int) -> FieldAccessEntry:
        assert self.property_name is not None, "accessor candidate requires property_name"
        return FieldAccessEntry(
            type="field",
            access_kind=self.access_kind,
            cls=self.owner_cls,
            field_name=self.field,
            property_name=self.property_name,
            instruction_address=instruction_address,
            field_offset=None,
            index_in_function=get_operation_index_inside_function(instruction_address),
        )


def build_getter_setter_lookup(msgs: list[DumpCSMessage]) -> dict[int, list[AccessorCandidate]]:
    result: dict[int, list[AccessorCandidate]] = defaultdict(list)
    message_type_lookup = build_long_name_by_unique_alias(msgs)
    for msg in msgs:
        fields_by_property_name: dict[str, DumpCSMessageField] = {
            field.property_name: field
            for field in msg.fields
            if field.property_name is not None and field.is_property_traceable_proto_field
        }
        for prop in msg.properties:
            if prop.getter_address is None and prop.setter_address is None:
                continue
            field = fields_by_property_name.get(prop.property_name)
            if field is None:
                continue
            if prop.getter_address is not None:
                result[prop.getter_address].append(
                    _build_accessor_candidate(
                        msg.composed_name,
                        field,
                        prop.clr_type,
                        prop.normalized_type,
                        "getter",
                        message_type_lookup,
                    )
                )
            if prop.setter_address is not None:
                result[prop.setter_address].append(
                    _build_accessor_candidate(
                        msg.composed_name,
                        field,
                        prop.clr_type,
                        prop.normalized_type,
                        "setter",
                        message_type_lookup,
                    )
                )
    return dict(result)


def _build_accessor_candidate(
    owner_cls: str,
    field: DumpCSMessageField,
    property_clr_type: str | None,
    property_normalized_type: str | None,
    access_kind: AccessKind,
    message_type_lookup: dict[str, str],
) -> AccessorCandidate:
    return AccessorCandidate(
        owner_cls=owner_cls,
        access_kind=access_kind,
        field=field.field_name,
        property_name=field.property_name,
        field_offset=field.memory_offset,
        clr_type=property_clr_type,
        normalized_type=property_normalized_type,
        returned_cls=_resolve_accessor_returned_message_cls(
            field,
            property_normalized_type,
            message_type_lookup,
        ),
        _returned_domain=_resolve_accessor_returned_domain(
            field,
            property_normalized_type,
            message_type_lookup,
        ),
    )


def _resolve_accessor_returned_message_cls(
    field: DumpCSMessageField,
    property_normalized_type: str | None,
    message_type_lookup: dict[str, str],
) -> str | None:
    normalized_type = property_normalized_type or field.normalized_type
    if field.category == FieldCategoryEnum.MESSAGE:
        return resolve_message_type_name(normalized_type, message_type_lookup) or normalized_type
    if field.category == FieldCategoryEnum.ENUM:
        return None
    if field.category == FieldCategoryEnum.REPEATED:
        repeated_inner_type = extract_repeated_inner_type(normalized_type)
        if repeated_inner_type is None:
            return None
        if is_non_message_type(repeated_inner_type, field.enum_key_type, field.enum_value_type):
            return None
        return resolve_message_type_name(repeated_inner_type, message_type_lookup) or repeated_inner_type
    if field.category != FieldCategoryEnum.MAP:
        return None
    map_inner_types = extract_map_inner_types(normalized_type)
    if map_inner_types is None:
        return None
    _, value_type = map_inner_types
    if is_non_message_type(value_type, field.enum_key_type, field.enum_value_type):
        return None
    return resolve_message_type_name(value_type, message_type_lookup) or value_type


def _resolve_accessor_returned_domain(
    field: DumpCSMessageField,
    property_normalized_type: str | None,
    message_type_lookup: dict[str, str],
) -> Literal["object", "repeated_container"] | None:
    returned_cls = _resolve_accessor_returned_message_cls(
        field,
        property_normalized_type,
        message_type_lookup,
    )
    if returned_cls is None:
        return None
    if field.category in {FieldCategoryEnum.REPEATED, FieldCategoryEnum.MAP}:
        return "repeated_container"
    return "object"

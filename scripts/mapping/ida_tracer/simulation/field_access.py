import idaapi

from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import (
    AccessEntry,
    AccessKind,
    FieldAccessEntry,
    HandlerRegistrationAccessEntry,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessageField
from scripts.mapping.ida_tracer.core.function_inspector import (
    get_operation_index_inside_function,
)
from scripts.mapping.ida_tracer.core.mnemonics import (
    CMOV_MNEMONICS,
    READ_WRITE_DEST_MNEMONICS,
    VECTOR_MOVE_MNEMONICS,
)
from scripts.mapping.ida_tracer.core.operands import (
    get_displacement_value,
    get_operand_access_size,
)
from scripts.mapping.ida_tracer.state.encoding import (
    decode_field_address,
    decode_object_offset,
)
from scripts.mapping.ida_tracer.state.types import (
    RegisterState,
    decode_object_union,
)

_IDA_OPERAND_COUNT: int = 8
_IDA_OPERAND_USE_FLAGS: tuple[int, ...] = (
    idaapi.CF_USE1,
    idaapi.CF_USE2,
    idaapi.CF_USE3,
    idaapi.CF_USE4,
    idaapi.CF_USE5,
    idaapi.CF_USE6,
    idaapi.CF_USE7,
    idaapi.CF_USE8,
)
_IDA_OPERAND_CHANGE_FLAGS: tuple[int, ...] = (
    idaapi.CF_CHG1,
    idaapi.CF_CHG2,
    idaapi.CF_CHG3,
    idaapi.CF_CHG4,
    idaapi.CF_CHG5,
    idaapi.CF_CHG6,
    idaapi.CF_CHG7,
    idaapi.CF_CHG8,
)


def collect_instruction_field_accesses(
    insn: idaapi.insn_t,
    ea: int,
    reg_state: RegisterState,
    proto_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
) -> list[FieldAccessEntry]:
    entries: list[FieldAccessEntry] = []
    for operand_index, access_kind in _iter_operand_accesses(insn):
        if operand_index >= len(insn.ops):
            continue
        entries.extend(
            _get_field_accesses_for_operand(
                insn.ops[operand_index],
                access_kind,
                ea,
                reg_state,
                proto_fields_by_class_and_offset,
            )
        )
    return entries


def dedupe_and_sort_access_entries(entries: list[AccessEntry]) -> list[AccessEntry]:
    unique_entries: dict[tuple[object, ...], AccessEntry] = {}
    for entry in entries:
        identity = _access_entry_identity(entry)
        previous = unique_entries.get(identity)
        if isinstance(entry, FieldAccessEntry) and isinstance(previous, FieldAccessEntry):
            comparisons = {
                (comparison.similarity_key, comparison.instruction_address): comparison
                for comparison in [*previous.comparisons, *entry.comparisons]
            }
            entry = entry.model_copy(update={"comparisons": sorted(
                comparisons.values(), key=lambda comparison: (comparison.similarity_key, comparison.instruction_address)
            )})
        unique_entries[identity] = entry
    return sorted(unique_entries.values(), key=_access_entry_sort_key)


def _iter_operand_accesses(insn: idaapi.insn_t) -> list[tuple[int, AccessKind]]:
    feature = _get_instruction_feature(insn)
    if feature is not None:
        accesses: list[tuple[int, AccessKind]] = []
        for operand_index in range(_IDA_OPERAND_COUNT):
            if feature & _IDA_OPERAND_USE_FLAGS[operand_index]:
                accesses.append((operand_index, "read"))
            if feature & _IDA_OPERAND_CHANGE_FLAGS[operand_index]:
                accesses.append((operand_index, "write"))
        if accesses:
            return accesses

    mnemonic = insn.get_canon_mnem()
    if mnemonic in {"mov", "movzx", "movsxd"}:
        accesses = []
        if mnemonic == "mov":
            accesses.append((0, "write"))
        accesses.append((1, "read"))
        return accesses
    if mnemonic in {"cmp", "test"}:
        return [(0, "read"), (1, "read")]
    if mnemonic in CMOV_MNEMONICS:
        return [(1, "read")]
    if mnemonic in READ_WRITE_DEST_MNEMONICS:
        return [(0, "read"), (0, "write"), (1, "read")]
    if mnemonic in VECTOR_MOVE_MNEMONICS:
        return [(0, "write"), (1, "read")]
    return []


def _get_instruction_feature(insn: idaapi.insn_t) -> int | None:
    try:
        feature = insn.get_canon_feature()
    except (AttributeError, TypeError, RuntimeError):
        return None
    return feature if isinstance(feature, int) else None


def _access_entry_identity(entry: AccessEntry) -> tuple[object, ...]:
    if isinstance(entry, FieldAccessEntry):
        return (
            entry.type,
            entry.access_kind,
            entry.cls,
            entry.property_name,
            entry.instruction_address,
            entry.field_offset,
        )
    if isinstance(entry, HandlerRegistrationAccessEntry):
        return (
            entry.type,
            entry.access_kind,
            entry.cls,
            entry.handler_method,
            entry.method_info_address,
            entry.filter_typeinfo_address,
        )
    return (
        entry.type,
        entry.access_kind,
        entry.cls,
        entry.instruction_address,
        entry.target_address,
    )


def _access_entry_sort_key(entry: AccessEntry) -> tuple[object, ...]:
    if isinstance(entry, FieldAccessEntry):
        return (
            entry.instruction_address,
            0,
            entry.cls,
            entry.access_kind,
            entry.field_offset,
            entry.field_name or "",
            entry.property_name or "",
        )
    if isinstance(entry, HandlerRegistrationAccessEntry):
        return (
            entry.instruction_address,
            2,
            entry.cls,
            entry.access_kind,
            entry.handler_method,
            entry.method_info_address,
        )
    return (
        entry.instruction_address,
        1,
        entry.cls,
        entry.access_kind,
        entry.target_address,
    )


def _get_field_accesses_for_operand(
    operand: idaapi.op_t,
    access_kind: AccessKind,
    ea: int,
    reg_state: RegisterState,
    proto_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
) -> list[FieldAccessEntry]:
    if operand.type not in {idaapi.o_phrase, idaapi.o_displ}:
        return []
    reg_info = reg_state.get(operand.reg)
    if reg_info is None:
        return []
    domain, class_name = reg_info
    displacement = 0 if operand.type == idaapi.o_phrase else get_displacement_value(operand)
    if domain == "field_address":
        field_cls, field_offset = decode_field_address(class_name)
        class_name = field_cls
        effective_offset = field_offset + displacement
    elif domain == "object_offset":
        class_name, base_offset = decode_object_offset(class_name)
        effective_offset = base_offset + displacement
    elif domain in {"candidate_object", "object", "object_union"}:
        effective_offset = displacement
    else:
        return []
    fields_by_class = _collect_overlapping_fields_by_class(
        class_name=class_name,
        domain=domain,
        effective_offset=effective_offset,
        access_size=get_operand_access_size(operand),
        proto_fields_by_class_and_offset=proto_fields_by_class_and_offset,
    )
    if not fields_by_class:
        return []
    if domain == "candidate_object":
        reg_state[operand.reg] = ("object", class_name)
    return [
        FieldAccessEntry(
            type="field",
            access_kind=access_kind,
            cls=field_class_name,
            field_name=field.field_name,
            property_name=field.property_name,
            instruction_address=ea,
            field_offset=field.memory_offset,
            index_in_function=get_operation_index_inside_function(ea),
        )
        for field_class_name, fields in fields_by_class
        for field in fields
    ]


def _collect_overlapping_fields_by_class(
    *,
    class_name: str,
    domain: str,
    effective_offset: int,
    access_size: int | None,
    proto_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
) -> list[tuple[str, list[DumpCSMessageField]]]:
    if domain == "object_union":
        candidate_class_names = sorted(decode_object_union(class_name))
    else:
        candidate_class_names = [class_name]
    fields_by_class: list[tuple[str, list[DumpCSMessageField]]] = []
    for candidate_class_name in candidate_class_names:
        offset_map = proto_fields_by_class_and_offset.get(candidate_class_name)
        if offset_map is None:
            continue
        fields = _fields_overlapping_access(offset_map, effective_offset, access_size)
        if fields:
            fields_by_class.append((candidate_class_name, fields))
    return fields_by_class


def _fields_overlapping_access(
    offset_map: dict[int, DumpCSMessageField],
    access_offset: int,
    access_size: int | None,
) -> list[DumpCSMessageField]:
    if access_size is None:
        field = offset_map.get(access_offset)
        return [] if field is None else [field]
    access_end = access_offset + access_size
    return [
        field
        for field_offset, field in sorted(offset_map.items())
        if access_offset <= field_offset < access_end
    ]

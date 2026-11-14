from collections.abc import Sequence
from dataclasses import replace
from typing import Literal

import idaapi

from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import FieldAccessEntry
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessageField
from DBDofusUnity.proto_mapper_assembly.interfaces.field_category import FieldCategoryEnum, NumericKind
from DBDofusUnity.proto_mapper_assembly.interfaces.field_comparison import (
    ComparisonPredicate,
    FieldComparison,
)
from scripts.mapping.ida_tracer.core.operands import (
    Operand,
    get_operand_access_size,
    read_immediate_operand_value,
)
from scripts.mapping.ida_tracer.lookups.accessor_candidate import (
    AccessorCandidate,
)
from scripts.mapping.ida_tracer.simulation.field_access import (
    _get_field_accesses_for_operand,
)
from scripts.mapping.ida_tracer.simulation.interproc_calls import (
    resolve_accessor_candidate_for_call,
)
from scripts.mapping.ida_tracer.state.analysis_state import AnalysisState
from scripts.mapping.ida_tracer.state.basic_block import DecodedInstruction
from scripts.mapping.ida_tracer.state.numeric_provenance import (
    NumericComparison,
    NumericFieldValue,
)
from scripts.mapping.ida_tracer.state.types import (
    WINDOWS_X64_VOLATILE_REGISTERS,
)
from scripts.mapping.ida_tracer.state.value_resolution import get_stack_slot

_BRANCH_PREDICATES: dict[str, tuple[ComparisonPredicate, bool | None]] = {
    # Equality and its negation test the same constant partition, regardless of branch polarity.
    "jz": ("eq", None),
    "je": ("eq", None),
    "jnz": ("eq", None),
    "jne": ("eq", None),
    "jl": ("lt", True),
    "jle": ("le", True),
    "jg": ("gt", True),
    "jge": ("ge", True),
    "jb": ("lt", False),
    "jbe": ("le", False),
    "ja": ("gt", False),
    "jae": ("ge", False),
}
_REVERSED_PREDICATES: dict[ComparisonPredicate, ComparisonPredicate] = {
    "eq": "eq",
    "lt": "gt",
    "le": "ge",
    "gt": "lt",
    "ge": "le",
}


def _numeric_field(field: DumpCSMessageField) -> bool:
    return (
        field.is_declared_proto_shape_field
        and field.numeric_kind
        not in {
            NumericKind.FLOAT,
            NumericKind.DOUBLE,
        }
        and field.category
        in {
            FieldCategoryEnum.NUMBER,
            FieldCategoryEnum.ENUM,
            FieldCategoryEnum.BOOLEAN,
        }
    )


def _origin(entry: FieldAccessEntry, width: Literal[8, 16, 32, 64]) -> NumericFieldValue:
    assert entry.access_kind == "read" or entry.access_kind == "getter"
    return NumericFieldValue(
        cls=entry.cls,
        field_name=entry.field_name,
        property_name=entry.property_name,
        field_offset=entry.field_offset,
        access_kind=entry.access_kind,
        instruction_address=entry.instruction_address,
        index_in_function=entry.index_in_function,
        width=width,
    )


def _source_value(
    operand: Operand,
    state: AnalysisState,
    fields: dict[str, dict[int, DumpCSMessageField]],
    ea: int,
) -> NumericFieldValue | None:
    if operand.type == idaapi.o_reg:
        value = state.numeric_registers.get(operand.reg)
        if value is None:
            return None
        size = get_operand_access_size(operand)
        return value if size is not None and size * 8 >= value.width else None
    if operand.type not in {idaapi.o_displ, idaapi.o_phrase}:
        return None
    slot = get_stack_slot(operand, state.frame_state)
    if slot is not None:
        value = state.numeric_stack.get(slot)
        if value is None:
            return None
        size = get_operand_access_size(operand)
        return value if size is not None and size * 8 >= value.width else None
    entries = _get_field_accesses_for_operand(operand, "read", ea, state.reg_state, fields)
    if len(entries) != 1:
        return None
    entry = entries[0]
    assert entry.field_offset is not None
    field = fields[entry.cls][entry.field_offset]
    size = get_operand_access_size(operand)
    if not _numeric_field(field) or size not in {1, 2, 4, 8}:
        return None
    width = size * 8
    if width == 8:
        return _origin(entry, 8)
    if width == 16:
        return _origin(entry, 16)
    if width == 32:
        return _origin(entry, 32)
    return _origin(entry, 64)


def _branch_comparison(mnemonic: str, state: AnalysisState) -> list[FieldAccessEntry]:
    pending = state.numeric_comparison
    branch = _BRANCH_PREDICATES.get(mnemonic)
    if pending is None or branch is None:
        return []
    predicate, signed = branch
    value = pending.value
    if predicate != "eq" and value.adjustment:
        return []  # Offset arithmetic can wrap; only equality is invariant under modular subtraction.
    if pending.reversed_operands:
        predicate = _REVERSED_PREDICATES[predicate]
    mask = (1 << value.width) - 1
    constant = (pending.constant - value.adjustment) & mask
    if signed and constant & (1 << (value.width - 1)):
        constant -= 1 << value.width
    entry = value.access_entry()
    entry.comparisons = [
        FieldComparison(
            predicate=predicate,
            constant=constant,
            width=value.width,
            signed=signed,
            instruction_address=pending.instruction_address,
        )
    ]
    return [entry]


def track_field_comparisons(
    insn: DecodedInstruction,
    ea: int,
    mnemonic: str,
    state: AnalysisState,
    fields: dict[str, dict[int, DumpCSMessageField]],
    accessors: dict[int, list[AccessorCandidate]],
    *,
    collect_entries: bool = True,
) -> Sequence[FieldAccessEntry]:
    if mnemonic.startswith("j"):
        return _branch_comparison(mnemonic, state) if collect_entries else ()
    destination, source = insn.ops[0], insn.ops[1]
    if mnemonic == "cmp":
        value = _source_value(destination, state, fields, ea)
        constant = read_immediate_operand_value(source)
        reversed_operands = False
        if value is None or constant is None:
            value = _source_value(source, state, fields, ea)
            constant = read_immediate_operand_value(destination)
            reversed_operands = True
        state.numeric_comparison = (
            NumericComparison(value, constant, ea, reversed_operands)
            if value is not None and constant is not None
            else None
        )
        return ()
    if mnemonic == "test":
        value = _source_value(destination, state, fields, ea)
        state.numeric_comparison = (
            NumericComparison(value, 0, ea)
            if value is not None and source.type == idaapi.o_reg and source.reg == destination.reg
            else None
        )
        return ()
    if mnemonic in {"mov", "movzx", "movsxd"}:
        value = _source_value(source, state, fields, ea)
        size = get_operand_access_size(destination)
        if value is not None and (size is None or size * 8 < value.width):
            value = None
        slot = get_stack_slot(destination, state.frame_state)
        target = state.numeric_stack if slot is not None else state.numeric_registers
        key = slot if slot is not None else destination.reg
        if slot is not None or destination.type == idaapi.o_reg:
            if slot is not None:
                # A partial or aliased stack write can invalidate a wider spilled value.
                state.numeric_stack.clear()
            target.pop(key, None)
            if value is not None:
                target[key] = value
        return ()
    if mnemonic == "call":
        candidate = resolve_accessor_candidate_for_call(insn, accessors, state.reg_state)
        state.numeric_comparison = None
        state.numeric_stack.clear()
        for reg in WINDOWS_X64_VOLATILE_REGISTERS:
            state.numeric_registers.pop(reg, None)
        if candidate is not None and candidate.access_kind == "getter":
            field = fields.get(candidate.owner_cls, {}).get(candidate.field_offset)
            if field is not None and _numeric_field(field):
                width = (
                    64
                    if field.numeric_kind in {NumericKind.LONG, NumericKind.ULONG}
                    else 8
                    if field.category == FieldCategoryEnum.BOOLEAN
                    else 32
                )
                state.numeric_registers[0] = _origin(candidate.to_field_access_entry(ea), width)
        return ()
    if not state.numeric_registers and not state.numeric_stack and state.numeric_comparison is None:
        return ()
    value = state.numeric_registers.get(destination.reg) if destination.type == idaapi.o_reg else None
    immediate = (
        read_immediate_operand_value(source) if mnemonic in {"add", "sub"} and value is not None else None
    )
    if immediate is not None and value is not None:
        size = get_operand_access_size(destination)
        if size is not None and size * 8 == value.width:
            adjustment = (value.adjustment + (immediate if mnemonic == "add" else -immediate)) % (
                1 << value.width
            )
            updated = replace(value, adjustment=adjustment)
            state.numeric_registers[destination.reg] = updated
            state.numeric_comparison = NumericComparison(updated, 0, ea)
            return ()
    feature = insn.feature
    if feature is None:
        state.numeric_registers.clear()
        state.numeric_stack.clear()
        state.numeric_comparison = None
        return ()
    for index in insn.changed_operand_indices:
        operand = insn.ops[index]
        if operand.type == idaapi.o_reg:
            state.numeric_registers.pop(operand.reg, None)
        slot = get_stack_slot(operand, state.frame_state)
        if slot is not None:
            state.numeric_stack.pop(slot, None)
    if mnemonic not in {"lea", "push", "pop", "nop", "xchg", "not"}:
        state.numeric_comparison = None
    return ()

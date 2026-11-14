from collections.abc import Sequence

import idaapi

from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import AccessEntry, FieldAccessEntry
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessageField
from scripts.mapping.ida_tracer.core.function_inspector import (
    get_operation_index_inside_function,
)
from scripts.mapping.ida_tracer.core.operands import (
    get_displacement_value,
    read_immediate_operand_value,
)
from scripts.mapping.ida_tracer.lookups.accessor_candidate import AccessorCandidate
from scripts.mapping.ida_tracer.simulation.constants import (
    IENUMERATOR_INTERFACE_SLOT_DISPLACEMENT,
    KVP_VALUE_POINTER_OFFSET,
)
from scripts.mapping.ida_tracer.simulation.interproc_calls import (
    resolve_accessor_candidate_for_call,
    resolve_inline_get_enumerator_cls,
    resolve_inline_ienumerator_current_cls,
    resolve_inline_kvp_current_cls,
)
from scripts.mapping.ida_tracer.simulation.static_loads import (
    handle_static_tracker_load,
    handle_typeinfo_load,
    static_lookup_contains_operand,
)
from scripts.mapping.ida_tracer.state.basic_block import DecodedInstruction
from scripts.mapping.ida_tracer.state.encoding import (
    decode_field_address,
    decode_object_offset,
    encode_field_address,
    encode_object_offset,
)
from scripts.mapping.ida_tracer.state.invalidation import invalidate_heap_slots_for_register
from scripts.mapping.ida_tracer.state.types import (
    HeapState,
    RegisterState,
    StackFrameState,
    StackState,
    TrackedValue,
)
from scripts.mapping.ida_tracer.state.value_resolution import get_stack_slot


def _resolve_xmm0_register_number() -> int:
    try:
        return list(idaapi.ph.regnames).index("xmm0")
    except AttributeError, ValueError:
        return 33


XMM0_REG: int = _resolve_xmm0_register_number()


def handle_call_instruction(
    insn: DecodedInstruction,
    getter_setter_lookup: dict[int, list[AccessorCandidate]],
    reg_state: RegisterState,
    ea: int,
) -> list[FieldAccessEntry]:
    candidate = resolve_accessor_candidate_for_call(insn, getter_setter_lookup, reg_state)
    if candidate is None:
        return []
    return [candidate.to_field_access_entry(instruction_address=ea)]


def handle_lea_instruction(
    insn: DecodedInstruction,
    typeinfo_lookup: dict[int, str],
    reg_state: RegisterState,
    proto_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    ea: int,
    methodinfo_get_enumerator_lookup: dict[int, str] | None = None,
    ienumerator_typeinfo_lookup: dict[int, str] | None = None,
    frame_state: StackFrameState | None = None,
    *,
    collect_entries: bool = True,
) -> Sequence[AccessEntry]:
    destination_operand = insn.ops[0]
    source_operand = insn.ops[1]
    if destination_operand.type == idaapi.o_reg and frame_state is not None:
        stack_slot = get_stack_slot(source_operand, frame_state)
        if stack_slot is not None:
            reg_state[destination_operand.reg] = ("stack_address", str(stack_slot))
            return ()
    resolved_methodinfo_lookup = methodinfo_get_enumerator_lookup or {}
    resolved_ienumerator_typeinfo_lookup = ienumerator_typeinfo_lookup or {}
    inline_kvp_current_cls = resolve_inline_kvp_current_cls(reg_state)
    inline_current_cls = resolve_inline_ienumerator_current_cls(reg_state)
    inline_enumerable_cls = resolve_inline_get_enumerator_cls(reg_state)
    if (
        (
            inline_kvp_current_cls is not None
            or inline_current_cls is not None
            or inline_enumerable_cls is not None
        )
        and destination_operand.type == idaapi.o_reg
        and source_operand.type == idaapi.o_displ
        and int(source_operand.addr) == IENUMERATOR_INTERFACE_SLOT_DISPLACEMENT
    ):
        if inline_kvp_current_cls is not None:
            reg_state[destination_operand.reg] = (
                "pending_indirect_kvp_current_base",
                inline_kvp_current_cls,
            )
        elif inline_current_cls is not None:
            reg_state[destination_operand.reg] = ("pending_indirect_current_base", inline_current_cls)
        elif inline_enumerable_cls is not None:
            reg_state[destination_operand.reg] = (
                "pending_indirect_get_enumerator_base",
                inline_enumerable_cls,
            )
        return ()

    if destination_operand.type == idaapi.o_reg and source_operand.type == idaapi.o_displ:
        base_info = reg_state.get(source_operand.reg)
        if base_info is not None and base_info[0] in {"object", "candidate_object", "object_offset"}:
            base_domain, base_class_name = base_info
            base_offset = 0
            if base_domain == "object_offset":
                base_class_name, base_offset = decode_object_offset(base_class_name)
            field_offset = base_offset + get_displacement_value(source_operand)
            offset_map = proto_fields_by_class_and_offset.get(base_class_name)
            if offset_map is not None and field_offset in offset_map:
                field = offset_map[field_offset]
                reg_state[destination_operand.reg] = (
                    "field_address",
                    encode_field_address(base_class_name, field_offset),
                )
                if not collect_entries:
                    return ()
                return [
                    FieldAccessEntry(
                        type="field",
                        access_kind="address",
                        cls=base_class_name,
                        field_name=field.field_name,
                        property_name=field.property_name,
                        instruction_address=ea,
                        field_offset=field.memory_offset,
                        index_in_function=get_operation_index_inside_function(ea),
                    )
                ]
            reg_state[destination_operand.reg] = (
                "object_offset",
                encode_object_offset(base_class_name, field_offset),
            )
            return ()

    if static_lookup_contains_operand(source_operand, resolved_methodinfo_lookup):
        handle_static_tracker_load(
            destination_operand,
            source_operand,
            resolved_methodinfo_lookup,
            reg_state,
            tracked_domain="methodinfo_get_enumerator",
            clear_destination_on_failure=False,
        )
        return ()
    return handle_typeinfo_load(
        destination_operand,
        source_operand,
        typeinfo_lookup,
        reg_state,
        ea=ea,
        clear_destination_on_failure=True,
        silent_typeinfo_lookup=resolved_ienumerator_typeinfo_lookup,
        collect_entries=collect_entries,
    )


def handle_vector_move_instruction(
    insn: DecodedInstruction,
    reg_state: RegisterState,
    frame_state: StackFrameState,
    stack_state: StackState,
) -> None:
    destination_operand = insn.ops[0]
    source_operand = insn.ops[1]

    if destination_operand.type == idaapi.o_reg and destination_operand.reg == XMM0_REG:
        if source_operand.type in {idaapi.o_displ, idaapi.o_phrase}:
            source_value = reg_state.get(source_operand.reg)
            if source_value is not None and source_value[0] == "map_kvp_output":
                reg_state[XMM0_REG] = source_value
                return
        reg_state.pop(XMM0_REG, None)
        return

    stack_slot = get_stack_slot(destination_operand, frame_state)
    if stack_slot is not None and source_operand.type == idaapi.o_reg and source_operand.reg == XMM0_REG:
        xmm0_value = reg_state.get(XMM0_REG)
        if xmm0_value is not None and xmm0_value[0] == "map_kvp_output":
            stack_state[stack_slot + KVP_VALUE_POINTER_OFFSET] = ("object", xmm0_value[1])


def update_register_state_for_arithmetic(
    insn: DecodedInstruction,
    reg_state: RegisterState,
    heap_state: HeapState | None = None,
) -> None:
    destination_operand = insn.ops[0]
    mnemonic = insn.mnemonic
    if mnemonic == "add" and destination_operand.type == idaapi.o_reg:
        source_operand = insn.ops[1]
        destination_value = reg_state.get(destination_operand.reg)
        if destination_value is None:
            inline_kvp_current_cls = resolve_inline_kvp_current_cls(reg_state)
            inline_current_cls = resolve_inline_ienumerator_current_cls(reg_state)
            if read_immediate_operand_value(source_operand) == IENUMERATOR_INTERFACE_SLOT_DISPLACEMENT:
                if inline_kvp_current_cls is not None:
                    reg_state[destination_operand.reg] = (
                        "pending_indirect_kvp_current_base",
                        inline_kvp_current_cls,
                    )
                    if heap_state is not None:
                        invalidate_heap_slots_for_register(heap_state, destination_operand.reg)
                    return
                if inline_current_cls is not None:
                    reg_state[destination_operand.reg] = (
                        "pending_indirect_current_base",
                        inline_current_cls,
                    )
                    if heap_state is not None:
                        invalidate_heap_slots_for_register(heap_state, destination_operand.reg)
                    return
                inline_enumerable_cls = resolve_inline_get_enumerator_cls(reg_state)
                if inline_enumerable_cls is not None:
                    reg_state[destination_operand.reg] = (
                        "pending_indirect_get_enumerator_base",
                        inline_enumerable_cls,
                    )
                    if heap_state is not None:
                        invalidate_heap_slots_for_register(heap_state, destination_operand.reg)
                    return

        if destination_value is not None and destination_value[0] == "pending_indirect_current_base":
            reg_state[destination_operand.reg] = ("pending_indirect_current", destination_value[1])
            if heap_state is not None:
                invalidate_heap_slots_for_register(heap_state, destination_operand.reg)
            return
        if destination_value is not None and destination_value[0] == "pending_indirect_kvp_current_base":
            reg_state[destination_operand.reg] = ("pending_indirect_kvp_current", destination_value[1])
            if heap_state is not None:
                invalidate_heap_slots_for_register(heap_state, destination_operand.reg)
            return
        if destination_value is not None and destination_value[0] == "pending_indirect_get_enumerator_base":
            reg_state[destination_operand.reg] = ("pending_indirect_get_enumerator", destination_value[1])
            if heap_state is not None:
                invalidate_heap_slots_for_register(heap_state, destination_operand.reg)
            return
    if mnemonic in {"add", "sub"} and destination_operand.type == idaapi.o_reg:
        source_operand = insn.ops[1]
        immediate = read_immediate_operand_value(source_operand)
        destination_value = reg_state.get(destination_operand.reg)
        if immediate is not None and destination_value is not None:
            adjustment = immediate if mnemonic == "add" else -immediate
            adjusted_value = _adjust_tracked_pointer_offset(destination_value, adjustment)
            if adjusted_value is not None:
                reg_state[destination_operand.reg] = adjusted_value
                if heap_state is not None:
                    invalidate_heap_slots_for_register(heap_state, destination_operand.reg)
                return
    _invalidate_arithmetic_destination(insn, reg_state, heap_state)


def _invalidate_arithmetic_destination(
    insn: DecodedInstruction,
    reg_state: RegisterState,
    heap_state: HeapState | None = None,
) -> None:
    destination_operand = insn.ops[0]
    if destination_operand.type == idaapi.o_reg:
        reg_state.pop(destination_operand.reg, None)
        if heap_state is not None:
            invalidate_heap_slots_for_register(heap_state, destination_operand.reg)


def _adjust_tracked_pointer_offset(value: TrackedValue, adjustment: int) -> TrackedValue | None:
    domain, payload = value
    if domain in {"object", "candidate_object"}:
        return "object_offset", encode_object_offset(payload, adjustment)
    if domain == "object_offset":
        class_name, offset = decode_object_offset(payload)
        return "object_offset", encode_object_offset(class_name, offset + adjustment)
    if domain == "field_address":
        class_name, offset = decode_field_address(payload)
        return "field_address", encode_field_address(class_name, offset + adjustment)
    return None

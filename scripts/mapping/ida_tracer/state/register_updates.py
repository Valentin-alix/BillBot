import idaapi

from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessageField
from scripts.mapping.ida_tracer.core.operands import (
    get_displacement_value,
    is_register_operand,
    read_immediate_operand_value,
)
from scripts.mapping.ida_tracer.state.invalidation import invalidate_heap_slots_for_register
from scripts.mapping.ida_tracer.state.types import (
    RBP_REG,
    RSP_REG,
    HeapState,
    RegisterState,
    StackFrameState,
    StackState,
)
from scripts.mapping.ida_tracer.state.value_resolution import (
    get_stack_slot,
    is_heap_tracking_destination,
    resolve_move_source,
)


def copy_frame_state(frame_state: StackFrameState) -> StackFrameState:
    return StackFrameState(
        rsp_entry_offset=frame_state.rsp_entry_offset,
        rbp_entry_offset=frame_state.rbp_entry_offset,
    )


def update_stack_frame_for_instruction(
    insn: idaapi.insn_t,
    frame_state: StackFrameState,
) -> None:
    mnemonic = insn.get_canon_mnem()
    destination_operand = insn.ops[0]
    source_operand = insn.ops[1]

    if mnemonic == "push":
        if frame_state.rsp_entry_offset is not None:
            frame_state.rsp_entry_offset -= 8
        return

    if mnemonic == "pop":
        if is_register_operand(destination_operand, RBP_REG):
            frame_state.rbp_entry_offset = None
        if frame_state.rsp_entry_offset is not None:
            frame_state.rsp_entry_offset += 8
        return

    if mnemonic == "leave":
        if frame_state.rbp_entry_offset is not None:
            frame_state.rsp_entry_offset = frame_state.rbp_entry_offset + 8
        else:
            frame_state.rsp_entry_offset = None
        frame_state.rbp_entry_offset = None
        return

    if mnemonic == "sub" and is_register_operand(destination_operand, RSP_REG):
        stack_adjustment = read_immediate_operand_value(source_operand)
        if stack_adjustment is not None and frame_state.rsp_entry_offset is not None:
            frame_state.rsp_entry_offset -= stack_adjustment
        return

    if mnemonic == "add" and is_register_operand(destination_operand, RSP_REG):
        stack_adjustment = read_immediate_operand_value(source_operand)
        if stack_adjustment is not None and frame_state.rsp_entry_offset is not None:
            frame_state.rsp_entry_offset += stack_adjustment
        return

    if mnemonic != "mov":
        if mnemonic == "lea":
            if is_register_operand(destination_operand, RSP_REG):
                frame_state.rsp_entry_offset = get_stack_slot(source_operand, frame_state)
            elif is_register_operand(destination_operand, RBP_REG):
                frame_state.rbp_entry_offset = get_stack_slot(source_operand, frame_state)
        return

    if is_register_operand(destination_operand, RBP_REG):
        if is_register_operand(source_operand, RSP_REG):
            frame_state.rbp_entry_offset = frame_state.rsp_entry_offset
        else:
            frame_state.rbp_entry_offset = None
        return

    if is_register_operand(destination_operand, RSP_REG):
        if is_register_operand(source_operand, RBP_REG) and frame_state.rbp_entry_offset is not None:
            frame_state.rsp_entry_offset = frame_state.rbp_entry_offset
        else:
            frame_state.rsp_entry_offset = None


def update_register_state_for_mov(
    insn: idaapi.insn_t,
    reg_state: RegisterState,
    frame_state: StackFrameState,
    stack_state: StackState,
    heap_state: HeapState,
    tracking_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    tracking_type_lookup: dict[str, str],
) -> None:
    destination_operand = insn.ops[0]
    source_operand = insn.ops[1]

    stack_slot = get_stack_slot(destination_operand, frame_state)
    if stack_slot is not None:
        if source_operand.type == idaapi.o_reg:
            source_value = reg_state.get(source_operand.reg)
            if source_value is not None:
                stack_state[stack_slot] = source_value
            else:
                stack_state.pop(stack_slot, None)
        else:
            stack_state.pop(stack_slot, None)
        return

    if is_heap_tracking_destination(destination_operand, frame_state, reg_state):
        heap_slot = (
            destination_operand.reg,
            0 if destination_operand.type == idaapi.o_phrase else get_displacement_value(destination_operand),
        )
        if source_operand.type == idaapi.o_reg:
            source_value = reg_state.get(source_operand.reg)
            if source_value is not None:
                heap_state[heap_slot] = source_value
            else:
                heap_state.pop(heap_slot, None)
        else:
            heap_state.pop(heap_slot, None)
        return

    if destination_operand.type != idaapi.o_reg:
        return

    source_value = resolve_move_source(
        source_operand,
        reg_state,
        frame_state,
        stack_state,
        heap_state,
        tracking_fields_by_class_and_offset,
        tracking_type_lookup,
    )
    invalidate_heap_slots_for_register(heap_state, destination_operand.reg)
    if source_value is not None:
        reg_state[destination_operand.reg] = source_value
        return
    reg_state.pop(destination_operand.reg, None)


def update_register_state_for_cmov(
    insn: idaapi.insn_t,
    reg_state: RegisterState,
    frame_state: StackFrameState,
    stack_state: StackState,
    heap_state: HeapState,
    tracking_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    tracking_type_lookup: dict[str, str],
) -> None:
    """An unresolved conditional move retains the destination because the move may not execute."""
    destination_operand = insn.ops[0]
    if destination_operand.type != idaapi.o_reg:
        return
    source_value = resolve_move_source(
        insn.ops[1],
        reg_state,
        frame_state,
        stack_state,
        heap_state,
        tracking_fields_by_class_and_offset,
        tracking_type_lookup,
    )
    if source_value is not None:
        reg_state[destination_operand.reg] = source_value

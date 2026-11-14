import idaapi

from scripts.mapping.ida_tracer.simulation.static_loads import (
    get_static_lookup_operand_addr,
)
from scripts.mapping.ida_tracer.state.analysis_state import TypeGuard
from scripts.mapping.ida_tracer.state.types import RegisterState


def handle_mov_type_guard_instruction(insn: idaapi.insn_t, reg_state: RegisterState) -> None:
    destination_operand = insn.ops[0]
    source_operand = insn.ops[1]
    if destination_operand.type != idaapi.o_reg:
        return
    if source_operand.type not in {idaapi.o_phrase, idaapi.o_displ}:
        return
    if source_operand.type == idaapi.o_displ and int(source_operand.addr) != 0:
        return
    source_value = reg_state.get(source_operand.reg)
    if source_value is None or source_value[0] != "untyped_object":
        return
    reg_state[destination_operand.reg] = ("object_vtable", str(source_operand.reg))


def resolve_type_guard_compare(
    insn: idaapi.insn_t,
    reg_state: RegisterState,
    typeinfo_lookup: dict[int, str],
) -> TypeGuard | None:
    left_operand = insn.ops[0]
    right_operand = insn.ops[1]
    return _resolve_type_guard_compare_operands(
        left_operand,
        right_operand,
        reg_state,
        typeinfo_lookup,
    ) or _resolve_type_guard_compare_operands(
        right_operand,
        left_operand,
        reg_state,
        typeinfo_lookup,
    )


def apply_type_guarded_cmov(
    insn: idaapi.insn_t,
    reg_state: RegisterState,
    type_guard: TypeGuard | None,
) -> None:
    if type_guard is None:
        return
    if insn.get_canon_mnem() not in {"cmove", "cmovz"}:
        return
    destination_operand = insn.ops[0]
    source_operand = insn.ops[1]
    if destination_operand.type != idaapi.o_reg or source_operand.type != idaapi.o_reg:
        return
    if source_operand.reg != type_guard.object_reg:
        return
    source_value = reg_state.get(source_operand.reg)
    if source_value is None or source_value[0] != "untyped_object":
        return
    reg_state[destination_operand.reg] = ("object", type_guard.cls)


def _resolve_type_guard_compare_operands(
    vtable_operand: idaapi.op_t,
    typeinfo_operand: idaapi.op_t,
    reg_state: RegisterState,
    typeinfo_lookup: dict[int, str],
) -> TypeGuard | None:
    if vtable_operand.type != idaapi.o_reg:
        return None
    vtable_value = reg_state.get(vtable_operand.reg)
    if vtable_value is None or vtable_value[0] != "object_vtable":
        return None
    typeinfo_addr = get_static_lookup_operand_addr(typeinfo_operand, typeinfo_lookup, None)
    if typeinfo_addr is None:
        return None
    class_name = typeinfo_lookup.get(typeinfo_addr)
    if class_name is None:
        return None
    return TypeGuard(object_reg=int(vtable_value[1]), cls=class_name)

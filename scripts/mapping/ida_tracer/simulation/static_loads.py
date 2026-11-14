from collections.abc import Sequence
from typing import Literal

import idaapi

from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import TypeInfoAccessEntry
from scripts.mapping.ida_tracer.core.function_inspector import (
    get_operation_index_inside_function,
)
from scripts.mapping.ida_tracer.core.operands import Operand
from scripts.mapping.ida_tracer.simulation.constants import (
    IENUMERABLE_TYPEINFO_PREFIX,
    IENUMERATOR_TYPEINFO_PREFIX,
    KVP_VALUE_TYPEINFO_PREFIX,
)
from scripts.mapping.ida_tracer.state.basic_block import DecodedInstruction
from scripts.mapping.ida_tracer.state.types import RegisterState


def handle_mov_typeinfo_instruction(
    insn: DecodedInstruction,
    typeinfo_lookup: dict[int, str],
    reg_state: RegisterState,
    ea: int,
    ienumerator_typeinfo_lookup: dict[int, str] | None = None,
    *,
    collect_entries: bool = True,
) -> Sequence[TypeInfoAccessEntry]:
    return handle_typeinfo_load(
        insn.ops[0],
        insn.ops[1],
        typeinfo_lookup,
        reg_state,
        ea=ea,
        clear_destination_on_failure=False,
        silent_typeinfo_lookup=ienumerator_typeinfo_lookup or {},
        collect_entries=collect_entries,
    )


def handle_mov_methodinfo_instruction(
    insn: DecodedInstruction,
    methodinfo_get_enumerator_lookup: dict[int, str],
    reg_state: RegisterState,
) -> None:
    handle_static_tracker_load(
        insn.ops[0],
        insn.ops[1],
        methodinfo_get_enumerator_lookup,
        reg_state,
        tracked_domain="methodinfo_get_enumerator",
        clear_destination_on_failure=False,
    )


def handle_typeinfo_load(
    destination_operand: Operand,
    source_operand: Operand,
    typeinfo_lookup: dict[int, str],
    reg_state: RegisterState,
    *,
    ea: int,
    clear_destination_on_failure: bool,
    silent_typeinfo_lookup: dict[int, str] | None = None,
    collect_entries: bool = True,
) -> Sequence[TypeInfoAccessEntry]:
    if destination_operand.type != idaapi.o_reg:
        return ()
    source_addr = get_static_lookup_operand_addr(source_operand, typeinfo_lookup, silent_typeinfo_lookup)
    if source_addr is None:
        if clear_destination_on_failure:
            reg_state.pop(destination_operand.reg, None)
        return ()
    class_name = typeinfo_lookup.get(source_addr)
    if class_name is None:
        if silent_typeinfo_lookup is not None:
            silent_typeinfo_class_name = silent_typeinfo_lookup.get(source_addr)
            if silent_typeinfo_class_name is not None:
                if silent_typeinfo_class_name.startswith(
                    (KVP_VALUE_TYPEINFO_PREFIX, IENUMERABLE_TYPEINFO_PREFIX)
                ):
                    reg_state[destination_operand.reg] = ("typeinfo", silent_typeinfo_class_name)
                else:
                    reg_state[destination_operand.reg] = (
                        "typeinfo",
                        f"{IENUMERATOR_TYPEINFO_PREFIX}{silent_typeinfo_class_name}",
                    )
                return ()
        if clear_destination_on_failure:
            reg_state.pop(destination_operand.reg, None)
        return ()
    reg_state[destination_operand.reg] = ("typeinfo", class_name)
    if not collect_entries:
        return ()
    return [
        TypeInfoAccessEntry(
            type="typeinfo",
            access_kind="load",
            cls=class_name,
            instruction_address=ea,
            target_address=source_addr,
            index_in_function=get_operation_index_inside_function(ea),
        )
    ]


def handle_static_tracker_load(
    destination_operand: Operand,
    source_operand: Operand,
    lookup: dict[int, str],
    reg_state: RegisterState,
    *,
    tracked_domain: Literal["methodinfo_get_enumerator"],
    clear_destination_on_failure: bool,
) -> None:
    if destination_operand.type != idaapi.o_reg:
        return
    source_addr = get_static_lookup_operand_addr(source_operand, lookup, None)
    if source_addr is None:
        if clear_destination_on_failure:
            reg_state.pop(destination_operand.reg, None)
        return
    class_name = lookup.get(source_addr)
    if class_name is None:
        if clear_destination_on_failure:
            reg_state.pop(destination_operand.reg, None)
        return
    reg_state[destination_operand.reg] = (tracked_domain, class_name)


def get_static_lookup_operand_addr(
    operand: Operand,
    primary_lookup: dict[int, str],
    secondary_lookup: dict[int, str] | None,
) -> int | None:
    if operand.type == idaapi.o_mem:
        return int(operand.addr)
    if operand.type != idaapi.o_displ:
        return None
    operand_addr = int(operand.addr)
    if operand_addr in primary_lookup:
        return operand_addr
    if secondary_lookup is not None and operand_addr in secondary_lookup:
        return operand_addr
    return None


def static_lookup_contains_operand(operand: Operand, lookup: dict[int, str]) -> bool:
    if operand.type not in {idaapi.o_mem, idaapi.o_displ}:
        return False
    return int(operand.addr) in lookup

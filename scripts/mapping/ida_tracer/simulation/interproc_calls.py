from functools import cache

import idaapi
import idautils

from scripts.mapping.ida_tracer.lookups.accessor_candidate import AccessorCandidate
from scripts.mapping.ida_tracer.simulation.constants import (
    IENUMERABLE_TYPEINFO_PREFIX,
    IENUMERATOR_TYPEINFO_PREFIX,
    IL2CPP_TYPEINFO_CAST_HELPERS,
    KVP_VALUE_METHODINFO_PREFIX,
    KVP_VALUE_POINTER_OFFSET,
    KVP_VALUE_TYPEINFO_PREFIX,
    R8_REG,
    R9_REG,
    RAX_REG,
    RCX_REG,
    RDX_REG,
)
from scripts.mapping.ida_tracer.state.basic_block import DecodedInstruction
from scripts.mapping.ida_tracer.state.invalidation import (
    invalidate_heap_slots_for_volatile_bases,
    invalidate_volatile_registers,
)
from scripts.mapping.ida_tracer.state.types import (
    HeapState,
    RegisterState,
    StackState,
    TrackedValue,
)


def update_register_state_for_call(
    insn: DecodedInstruction,
    reg_state: RegisterState,
    getter_setter_lookup: dict[int, list[AccessorCandidate]],
    ienumerator_typeinfo_lookup: dict[int, str] | None = None,
    *,
    heap_state: HeapState | None = None,
    stack_state: StackState | None = None,
) -> None:
    _update_kvp_value_output(reg_state, stack_state)
    target_operand = insn.ops[0]
    if target_operand.type == idaapi.o_reg:
        target_value = reg_state.get(target_operand.reg)
        if target_value is not None and target_value[0] == "pending_indirect_get_enumerator":
            invalidate_volatile_registers(reg_state)
            if heap_state is not None:
                invalidate_heap_slots_for_volatile_bases(heap_state)
            reg_state[RAX_REG] = ("repeated_enumerator", target_value[1])
            return
    indirect_result = resolve_pending_indirect_call_target(insn, reg_state)
    if indirect_result is not None:
        indirect_cls, is_kvp = indirect_result
        invalidate_volatile_registers(reg_state)
        if heap_state is not None:
            invalidate_heap_slots_for_volatile_bases(heap_state)
        if is_kvp:
            reg_state[RAX_REG] = ("map_kvp_output", indirect_cls)
        else:
            reg_state[RAX_REG] = ("object", indirect_cls)
        return
    direct_call_result: TrackedValue | None = None
    direct_call_target_addr = insn.direct_call_target_addr
    if direct_call_target_addr is not None:
        direct_call_result = _resolve_direct_call_result(
            insn,
            direct_call_target_addr,
            reg_state,
            getter_setter_lookup,
            ienumerator_typeinfo_lookup or {},
        )
    invalidate_volatile_registers(reg_state)
    if heap_state is not None:
        invalidate_heap_slots_for_volatile_bases(heap_state)
    if direct_call_result is not None:
        reg_state[RAX_REG] = direct_call_result


def resolve_accessor_candidate_for_call(
    insn: DecodedInstruction,
    getter_setter_lookup: dict[int, list[AccessorCandidate]],
    reg_state: RegisterState,
) -> AccessorCandidate | None:
    target_addr = insn.direct_call_target_addr
    if target_addr is None:
        return None
    candidates = getter_setter_lookup.get(target_addr)
    if candidates is None:
        return None
    receiver_info = reg_state.get(RCX_REG)
    if receiver_info is None:
        return None
    receiver_domain, receiver_cls = receiver_info
    if receiver_domain not in {"object", "candidate_object"}:
        return None
    matching_candidates = [candidate for candidate in candidates if candidate.owner_cls == receiver_cls]
    if len(matching_candidates) != 1:
        return None
    return matching_candidates[0]


def resolve_pending_indirect_call_target(
    insn: DecodedInstruction,
    reg_state: RegisterState,
) -> tuple[str, bool] | None:
    target_operand = insn.ops[0]
    if target_operand.type != idaapi.o_reg:
        return None
    tracked_value = reg_state.get(target_operand.reg)
    if tracked_value is None:
        return None
    if tracked_value[0] == "pending_indirect_current":
        return tracked_value[1], False
    if tracked_value[0] == "pending_indirect_kvp_current":
        return tracked_value[1], True
    return None


def resolve_inline_ienumerator_current_cls(reg_state: RegisterState) -> str | None:
    repeated_enumerator_classes = {
        class_name for domain, class_name in reg_state.values() if domain == "repeated_enumerator"
    }
    if not repeated_enumerator_classes:
        return None
    matching_typeinfo_classes = {
        class_name.removeprefix(IENUMERATOR_TYPEINFO_PREFIX)
        for domain, class_name in reg_state.values()
        if domain == "typeinfo" and class_name.startswith(IENUMERATOR_TYPEINFO_PREFIX)
    }
    common_classes = repeated_enumerator_classes & matching_typeinfo_classes
    if len(common_classes) != 1:
        return None
    return next(iter(common_classes))


def resolve_inline_get_enumerator_cls(reg_state: RegisterState) -> str | None:
    containers = {cls for domain, cls in reg_state.values() if domain == "repeated_container"}
    interfaces = {
        cls.removeprefix(IENUMERABLE_TYPEINFO_PREFIX)
        for domain, cls in reg_state.values()
        if domain == "typeinfo" and cls.startswith(IENUMERABLE_TYPEINFO_PREFIX)
    }
    common = containers & interfaces
    return next(iter(common)) if len(common) == 1 else None


def _update_kvp_value_output(reg_state: RegisterState, stack_state: StackState | None) -> None:
    if stack_state is None:
        return
    receiver = reg_state.get(RCX_REG)
    output = reg_state.get(RDX_REG)
    metadata = reg_state.get(R8_REG)
    if receiver is None or output is None or metadata is None:
        return
    if receiver[0] != "stack_address" or output[0] != "stack_address":
        return
    if metadata[0] != "methodinfo_get_enumerator" or not metadata[1].startswith(KVP_VALUE_METHODINFO_PREFIX):
        return
    cls = metadata[1].removeprefix(KVP_VALUE_METHODINFO_PREFIX)
    if stack_state.get(int(receiver[1]) + KVP_VALUE_POINTER_OFFSET) == ("object", cls):
        stack_state[int(output[1])] = ("object", cls)


@cache
def _invokes_interface_slot(target_addr: int) -> bool:
    # IL2CPP dispatch wrappers invoke the resolved slot; resolution helpers only return it.
    for ea in idautils.FuncItems(target_addr):
        insn = idaapi.insn_t()
        if idaapi.decode_insn(insn, ea) and insn.get_canon_mnem() in {"call", "jmp"}:
            if insn.ops[0].type in {idaapi.o_reg, idaapi.o_phrase, idaapi.o_displ}:
                return True
    return False


def resolve_inline_kvp_current_cls(reg_state: RegisterState) -> str | None:
    repeated_enumerator_classes = {
        class_name for domain, class_name in reg_state.values() if domain == "repeated_enumerator"
    }
    if not repeated_enumerator_classes:
        return None
    matching_kvp_typeinfo_classes = {
        class_name.removeprefix(KVP_VALUE_TYPEINFO_PREFIX)
        for domain, class_name in reg_state.values()
        if domain == "typeinfo" and class_name.startswith(KVP_VALUE_TYPEINFO_PREFIX)
    }
    common_classes = repeated_enumerator_classes & matching_kvp_typeinfo_classes
    if len(common_classes) != 1:
        return None
    return next(iter(common_classes))


def _resolve_direct_call_result(
    insn: DecodedInstruction,
    target_addr: int,
    reg_state: RegisterState,
    getter_setter_lookup: dict[int, list[AccessorCandidate]],
    ienumerator_typeinfo_lookup: dict[int, str],
) -> TrackedValue | None:
    tracked_value = reg_state.get(RCX_REG)
    if tracked_value is not None and tracked_value[0] == "typeinfo":
        return "candidate_object", tracked_value[1]

    helper_cast_result = _resolve_typeinfo_helper_cast_return(target_addr, reg_state)
    if helper_cast_result is not None:
        return helper_cast_result

    repeated_enumerator_cls = _resolve_get_enumerator_return(reg_state)
    if repeated_enumerator_cls is not None:
        return "repeated_enumerator", repeated_enumerator_cls

    pending_indirect_result = _resolve_pending_indirect_current_return(
        reg_state,
        ienumerator_typeinfo_lookup,
    )
    if pending_indirect_result is not None:
        pending_indirect_cls, is_kvp = pending_indirect_result
        if _invokes_interface_slot(target_addr):
            return ("map_kvp_output" if is_kvp else "object"), pending_indirect_cls
        if is_kvp:
            return "pending_indirect_kvp_current", pending_indirect_cls
        return "pending_indirect_current", pending_indirect_cls

    enumerable_cls = resolve_inline_get_enumerator_cls(reg_state)
    if enumerable_cls is not None and reg_state.get(RDX_REG) == (
        "typeinfo",
        IENUMERABLE_TYPEINFO_PREFIX + enumerable_cls,
    ):
        if _invokes_interface_slot(target_addr):
            return "repeated_enumerator", enumerable_cls
        return "pending_indirect_get_enumerator", enumerable_cls

    receiver_info = reg_state.get(RCX_REG)
    if receiver_info is not None and receiver_info[0] == "repeated_enumerator":
        return "pending_indirect_current", receiver_info[1]

    accessor_candidate = resolve_accessor_candidate_for_call(
        insn,
        getter_setter_lookup,
        reg_state,
    )
    if accessor_candidate is None or accessor_candidate.access_kind != "getter":
        return None
    if accessor_candidate.returned_cls is None:
        return None
    if accessor_candidate.returned_domain == "repeated_container":
        return "repeated_container", accessor_candidate.returned_cls
    return "object", accessor_candidate.returned_cls


def _resolve_typeinfo_helper_cast_return(target_addr: int, reg_state: RegisterState) -> TrackedValue | None:
    typeinfo_value = reg_state.get(RDX_REG)
    if typeinfo_value is None:
        return None
    typeinfo_domain, typeinfo_cls = typeinfo_value
    if typeinfo_domain != "typeinfo":
        return None
    if typeinfo_cls.startswith((KVP_VALUE_TYPEINFO_PREFIX, IENUMERABLE_TYPEINFO_PREFIX)):
        return None
    if typeinfo_cls.startswith(IENUMERATOR_TYPEINFO_PREFIX):
        if target_addr not in IL2CPP_TYPEINFO_CAST_HELPERS:
            return None
        return "candidate_object", typeinfo_cls.removeprefix(IENUMERATOR_TYPEINFO_PREFIX)
    if target_addr in IL2CPP_TYPEINFO_CAST_HELPERS:
        return "candidate_object", typeinfo_cls
    for receiver_register in (R8_REG, RCX_REG, R9_REG):
        receiver_value = reg_state.get(receiver_register)
        if receiver_value is None:
            continue
        receiver_domain, _ = receiver_value
        if receiver_domain in {"object", "candidate_object", "untyped_object"}:
            return "candidate_object", typeinfo_cls
    return None


def _resolve_get_enumerator_return(reg_state: RegisterState) -> str | None:
    receiver_info = reg_state.get(RCX_REG)
    methodinfo_info = reg_state.get(RDX_REG)
    if receiver_info is None or methodinfo_info is None:
        return None
    receiver_domain, receiver_cls = receiver_info
    methodinfo_domain, methodinfo_cls = methodinfo_info
    if receiver_domain != "repeated_container":
        return None
    if methodinfo_domain != "methodinfo_get_enumerator":
        return None
    if receiver_cls != methodinfo_cls:
        return None
    return receiver_cls


def _resolve_pending_indirect_current_return(
    reg_state: RegisterState,
    ienumerator_typeinfo_lookup: dict[int, str],
) -> tuple[str, bool] | None:
    typeinfo_info = reg_state.get(RDX_REG)
    if typeinfo_info is None:
        return None
    typeinfo_domain, typeinfo_cls = typeinfo_info
    if typeinfo_domain != "typeinfo":
        return None
    if typeinfo_cls.startswith(KVP_VALUE_TYPEINFO_PREFIX):
        enumerator_cls = typeinfo_cls.removeprefix(KVP_VALUE_TYPEINFO_PREFIX)
        is_kvp = True
    elif typeinfo_cls.startswith(IENUMERATOR_TYPEINFO_PREFIX):
        enumerator_cls = typeinfo_cls.removeprefix(IENUMERATOR_TYPEINFO_PREFIX)
        is_kvp = False
    else:
        return None
    for receiver_reg in (RCX_REG, R8_REG):
        receiver_info = reg_state.get(receiver_reg)
        if receiver_info is None:
            continue
        receiver_domain, receiver_cls = receiver_info
        if receiver_domain != "repeated_enumerator":
            continue
        if ienumerator_typeinfo_lookup and enumerator_cls != receiver_cls:
            continue
        return enumerator_cls, is_kvp
    return None

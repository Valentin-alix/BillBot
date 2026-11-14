from collections import defaultdict
from dataclasses import dataclass

import idaapi

from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import HandlerRegistrationAccessEntry
from scripts.mapping.ida_tracer.core.function_inspector import (
    get_function_operation_indices,
    get_operation_index_inside_function,
)
from scripts.mapping.ida_tracer.lookups.il2cpp import HandlerMethodInfo
from scripts.mapping.ida_tracer.simulation.scan_engine import build_function_scan_plan
from scripts.mapping.ida_tracer.simulation.static_loads import (
    get_static_lookup_operand_addr,
)
from scripts.mapping.ida_tracer.state.basic_block import FunctionScanCache


@dataclass(frozen=True)
class _ResolvedRegistration:
    message_cls: str
    method_info: HandlerMethodInfo
    method_info_address: int
    filter_typeinfo_address: int
    instruction_address: int


def collect_handler_registration_accesses(
    func: idaapi.func_t,
    filter_typeinfo_lookup: dict[int, str],
    handler_methodinfo_lookup: dict[int, HandlerMethodInfo],
    method_address_lookup: dict[int, str],
    function_scan_cache: FunctionScanCache,
) -> list[HandlerRegistrationAccessEntry]:
    filter_refs_by_cls: dict[str, list[tuple[int, int]]] = defaultdict(list)
    method_refs_by_cls: dict[str, list[tuple[int, int, HandlerMethodInfo]]] = defaultdict(list)
    operation_indices = get_function_operation_indices(func)
    scan_plan = build_function_scan_plan(func, function_scan_cache)
    instructions_by_address = {
        decoded.ea: decoded
        for instructions in scan_plan.instructions_by_block.values()
        for decoded in instructions
    }

    for instruction_address in operation_indices:
        decoded = instructions_by_address.get(instruction_address)
        if decoded is None:
            insn = idaapi.insn_t()
            if idaapi.decode_insn(insn, instruction_address) <= 0:
                continue
            operands = tuple(insn.ops)
        else:
            operands = decoded.ops
        for operand in operands:
            if operand.type == idaapi.o_void:
                break
            filter_address = get_static_lookup_operand_addr(operand, filter_typeinfo_lookup, None)
            if filter_address is not None and filter_address in filter_typeinfo_lookup:
                filter_refs_by_cls[filter_typeinfo_lookup[filter_address]].append(
                    (instruction_address, filter_address)
                )
                continue
            method_info_address = get_static_lookup_operand_addr(operand, method_address_lookup, None)
            if method_info_address is None or method_info_address not in handler_methodinfo_lookup:
                continue
            method_info = handler_methodinfo_lookup[method_info_address]
            method_refs_by_cls[method_info.cls].append(
                (instruction_address, method_info_address, method_info)
            )

    resolved_registrations: list[_ResolvedRegistration] = []
    for message_cls, method_refs in sorted(method_refs_by_cls.items()):
        filter_refs = filter_refs_by_cls.get(message_cls)
        if not filter_refs:
            continue
        _, filter_typeinfo_address = max(filter_refs, key=lambda item: item[0])
        latest_method_ref_by_address: dict[int, tuple[int, int, HandlerMethodInfo]] = {}
        for method_ref in method_refs:
            instruction_address, method_info_address, _ = method_ref
            existing_method_ref = latest_method_ref_by_address.get(method_info_address)
            if existing_method_ref is None or instruction_address > existing_method_ref[0]:
                latest_method_ref_by_address[method_info_address] = method_ref
        for instruction_address, method_info_address, method_info in sorted(
            latest_method_ref_by_address.values()
        ):
            resolved_registrations.append(
                _ResolvedRegistration(
                    message_cls=message_cls,
                    method_info=method_info,
                    method_info_address=method_info_address,
                    filter_typeinfo_address=filter_typeinfo_address,
                    instruction_address=instruction_address,
                )
            )
    # Restore instruction order before assigning registration ordinals.
    resolved_registrations.sort(key=lambda registration: registration.instruction_address)
    return [
        HandlerRegistrationAccessEntry(
            type="handler_registration",
            access_kind="register",
            cls=registration.message_cls,
            handler_method=registration.method_info.method_signature,
            method_info_address=registration.method_info_address,
            filter_typeinfo_address=registration.filter_typeinfo_address,
            instruction_address=registration.instruction_address,
            index_in_function=get_operation_index_inside_function(registration.instruction_address),
            handler_function_address=registration.method_info.method_address,
            registration_ordinal=ordinal,
        )
        for ordinal, registration in enumerate(resolved_registrations)
    ]

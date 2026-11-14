from collections.abc import Mapping

import idaapi

from scripts.mapping.ida_tracer.simulation.interproc_calls import (
    get_direct_call_target_addr,
)
from scripts.mapping.ida_tracer.simulation.scan_engine import build_function_scan_plan
from scripts.mapping.ida_tracer.state.basic_block import FunctionScanCache


def collect_stable_callees(
    func: idaapi.func_t,
    callee_identity_by_address: Mapping[int, str],
    function_scan_cache: FunctionScanCache | None = None,
) -> list[str]:
    """Deduplicate call sites because inlining changes call counts across builds."""
    identities: set[str] = set()
    for instructions in build_function_scan_plan(func, function_scan_cache).instructions_by_block.values():
        for decoded in instructions:
            if decoded.mnemonic not in {"call", "jmp"}:
                continue
            if decoded.mnemonic == "jmp" and decoded.insn.ops[0].type not in {idaapi.o_near, idaapi.o_far}:
                continue
            target_address = get_direct_call_target_addr(decoded.insn)
            if target_address is None:
                continue
            if decoded.mnemonic == "jmp":
                target_function = idaapi.get_func(target_address)
                if (
                    target_function is None
                    or target_function.start_ea != target_address
                    or target_function.start_ea == func.start_ea
                ):
                    continue
            identity = callee_identity_by_address.get(target_address)
            if identity is not None:
                identities.add(identity)
    return sorted(identities)

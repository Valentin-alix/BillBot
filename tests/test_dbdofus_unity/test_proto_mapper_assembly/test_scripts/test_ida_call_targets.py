from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import idaapi
import pytest

from scripts.mapping.ida_tracer.core.function_inspector import cache_function_inspection
from scripts.mapping.ida_tracer.state.basic_block import DecodedInstruction


def _instruction(kind: int, target: int = 404, mnemonic: str = "call") -> DecodedInstruction:
    native = cast(
        idaapi.insn_t,
        SimpleNamespace(
            ops=[SimpleNamespace(dtype=None, reg=0, type=kind, addr=target)],
            get_canon_feature=lambda: 0,
        ),
    )
    return DecodedInstruction(100, native, mnemonic)


@pytest.mark.parametrize("kind", [idaapi.o_near, idaapi.o_far, idaapi.o_mem])
def test_calls_and_jumps_share_normalized_target_resolution(kind: int) -> None:
    function = cast(idaapi.func_t, SimpleNamespace(start_ea=400))
    with patch.object(idaapi, "get_func", return_value=function) as get_func, cache_function_inspection():
        first = _instruction(kind)
        second = _instruction(kind, mnemonic="jmp")
        assert first.direct_call_target_addr == second.direct_call_target_addr == 400
        assert first.direct_call_target_addr == 400
        get_func.assert_called_once_with(404)


@pytest.mark.parametrize("fail", [False, True])
def test_unresolved_targets_are_cached_and_rechecked_between_runs(fail: bool) -> None:
    with patch.object(idaapi, "get_func", return_value=None) as get_func:

        def run() -> None:
            with cache_function_inspection():
                assert _instruction(idaapi.o_near).direct_call_target_addr == 404
                assert _instruction(idaapi.o_near).direct_call_target_addr == 404
                if fail:
                    raise RuntimeError("scan failed")

        if fail:
            with pytest.raises(RuntimeError, match="scan failed"):
                run()
        else:
            run()
        get_func.assert_called_once_with(404)

        get_func.return_value = cast(idaapi.func_t, SimpleNamespace(start_ea=400))
        with cache_function_inspection():
            assert _instruction(idaapi.o_near).direct_call_target_addr == 400
        assert get_func.call_count == 2

        get_func.return_value = cast(idaapi.func_t, SimpleNamespace(start_ea=300))
        assert _instruction(idaapi.o_near).direct_call_target_addr == 300
        assert get_func.call_count == 3


def test_indirect_calls_and_non_call_memory_operands_do_not_resolve_functions() -> None:
    with patch.object(idaapi, "get_func") as get_func, cache_function_inspection():
        for kind in (idaapi.o_reg, idaapi.o_displ, idaapi.o_phrase):
            assert _instruction(kind).direct_call_target_addr is None
        assert _instruction(idaapi.o_mem, mnemonic="mov").direct_call_target_addr is None
        get_func.assert_not_called()

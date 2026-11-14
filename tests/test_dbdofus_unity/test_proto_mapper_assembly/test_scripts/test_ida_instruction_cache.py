from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import idaapi
import idautils
import pytest

from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import HandlerRegistrationAccessEntry
from scripts.mapping.ida_tracer.core.function_inspector import (
    cache_function_inspection,
    get_operation_index_inside_function,
)
from scripts.mapping.ida_tracer.lookups.il2cpp import HandlerMethodInfo
from scripts.mapping.ida_tracer.simulation.scan_engine import (
    _get_linear_function_instructions,
)
from scripts.mapping.ida_tracer.state.basic_block import DecodedInstruction, FunctionScanPlan
from scripts.mapping.ida_tracer.tracing.handler_registration import collect_handler_registration_accesses


def test_operation_indices_preserve_func_items_order_and_reuse_one_traversal() -> None:
    function = cast(idaapi.func_t, SimpleNamespace(start_ea=100, end_ea=120))
    items = (100, 104, 108, 300, 304)
    with (
        patch.object(idaapi, "get_func", return_value=function),
        patch.object(idautils, "FuncItems", return_value=items) as func_items,
        cache_function_inspection(),
    ):
        assert [get_operation_index_inside_function(ea) for ea in items] == list(range(len(items)))
        assert get_operation_index_inside_function(100) == 0
        assert get_operation_index_inside_function(304) == 4
        func_items.assert_called_once_with(100)


@pytest.mark.parametrize("fail", [False, True])
def test_operation_indices_are_released_between_runs_even_on_failure(fail: bool) -> None:
    function = cast(idaapi.func_t, SimpleNamespace(start_ea=100, end_ea=120))

    @cache_function_inspection()
    def run() -> None:
        assert get_operation_index_inside_function(104) == 1
        assert get_operation_index_inside_function(100) == 0
        if fail:
            raise RuntimeError("scan failed")

    with (
        patch.object(idaapi, "get_func", return_value=function),
        patch.object(idautils, "FuncItems", return_value=(100, 104)) as func_items,
    ):
        if fail:
            with pytest.raises(RuntimeError, match="scan failed"):
                run()
        else:
            run()
        func_items.assert_called_once_with(100)

        func_items.return_value = (104, 100)
        with cache_function_inspection():
            assert get_operation_index_inside_function(104) == 0
            assert get_operation_index_inside_function(100) == 1
        assert func_items.call_count == 2

        func_items.return_value = (100, 104)
        assert get_operation_index_inside_function(104) == 1
        assert func_items.call_count == 3


def test_operation_indices_keep_missing_function_and_item_errors() -> None:
    function = cast(idaapi.func_t, SimpleNamespace(start_ea=100, end_ea=120))
    with (
        patch.object(idaapi, "get_func", return_value=function) as get_func,
        patch.object(idautils, "FuncItems", return_value=(100,)),
        cache_function_inspection(),
    ):
        with pytest.raises(ValueError, match="Did not found operation at 104"):
            get_operation_index_inside_function(104)
        get_func.return_value = None
        with pytest.raises(ValueError, match="func at 104 not found"):
            get_operation_index_inside_function(104)


def test_linear_fallback_reuses_cfg_instructions_and_keeps_missing_heads() -> None:
    def native_instruction() -> idaapi.insn_t:
        return cast(
            idaapi.insn_t,
            SimpleNamespace(ops=[], get_canon_mnem=lambda: "nop", get_canon_feature=lambda: 0),
        )

    function = cast(idaapi.func_t, SimpleNamespace(start_ea=100, end_ea=110))
    cached = tuple(DecodedInstruction(ea, native_instruction(), "nop") for ea in (100, 108, 200))
    plan = FunctionScanPlan(
        blocks={}, predecessors={}, entry_block_start=100, instructions_by_block={100: cached}
    )

    def decode(_insn: idaapi.insn_t, ea: int) -> int:
        return 1 if ea == 104 else 0

    with (
        patch.object(idautils, "Heads", return_value=(100, 104, 106, 108)) as heads,
        patch.object(idaapi, "insn_t", side_effect=native_instruction),
        patch.object(idaapi, "decode_insn", side_effect=decode, create=True) as decode_insn,
    ):
        first = _get_linear_function_instructions(function, plan)
        second = _get_linear_function_instructions(function, plan)

    assert [decoded.ea for decoded in first] == [100, 104, 108]
    assert first[0] is cached[0] and first[2] is cached[1]
    assert first is second
    heads.assert_called_once_with(100, 110)
    assert [call.args[1] for call in decode_insn.call_args_list] == [104, 106]


@pytest.mark.parametrize("warm_cache", [False, True])
@pytest.mark.parametrize("shared_tail", [False, True])
def test_handler_registrations_keep_indices_and_ordinals_with_partial_decode_cache(
    warm_cache: bool, shared_tail: bool
) -> None:
    function = cast(idaapi.func_t, SimpleNamespace(start_ea=100, end_ea=132))
    tail_owner = cast(idaapi.func_t, SimpleNamespace(start_ea=200, end_ea=220))
    references = {
        100: 2000,
        104: 1000,
        112: 2001,
        116: 1001,
        120: 1000,
        124: 2002,
        128: 1002,
        300: 1001,
        304: 2003,
    }
    items = (100, 104, 108, 112, 116, 120, 124, 128, 300, 304)

    def get_func(ea: int) -> idaapi.func_t:
        return tail_owner if shared_tail and ea >= 300 else function

    def function_items(start_ea: int) -> tuple[int, ...]:
        return items if start_ea == 100 else (200, 204, 208, 300, 304)

    def operands(ea: int) -> list[idaapi.op_t]:
        return [
            cast(idaapi.op_t, SimpleNamespace(dtype=None, reg=0, type=idaapi.o_mem, addr=references[ea])),
            cast(idaapi.op_t, SimpleNamespace(dtype=None, reg=0, type=idaapi.o_void)),
        ]

    decoded_instructions = tuple(
        DecodedInstruction(ea, cast(idaapi.insn_t, SimpleNamespace(ops=operands(ea))), "mov")
        for ea in references
        if warm_cache and ea < 132
    )
    plan = FunctionScanPlan(
        blocks={},
        predecessors={},
        entry_block_start=100,
        instructions_by_block={100: decoded_instructions},
    )
    filters = {2000: "A", 2001: "B", 2002: "A", 2003: "B"}
    handlers = {
        1000: HandlerMethodInfo("A", "Void HandleA(A)", 400),
        1001: HandlerMethodInfo("B", "Void HandleB(B)", None),
        1002: HandlerMethodInfo("Orphan", "Void HandleOrphan(Orphan)", 500),
    }
    method_addresses = {address: info.cls for address, info in handlers.items()}

    def decode(insn: idaapi.insn_t, ea: int) -> int:
        if ea not in references:
            return 0
        insn.ops = operands(ea)
        return 1

    with (
        patch.object(idaapi, "get_func", side_effect=get_func),
        patch.object(idautils, "FuncItems", side_effect=function_items) as func_items,
        patch.object(idaapi, "insn_t", side_effect=SimpleNamespace),
        patch.object(idaapi, "decode_insn", side_effect=decode, create=True) as decode_insn,
        cache_function_inspection(),
    ):
        result = collect_handler_registration_accesses(
            function, filters, handlers, method_addresses, {(100, 132): plan}
        )
        assert [call.args for call in func_items.call_args_list] == (
            [(100,), (200,)] if shared_tail else [(100,)]
        )

    assert result == [
        HandlerRegistrationAccessEntry(
            type="handler_registration",
            access_kind="register",
            cls="A",
            handler_method="Void HandleA(A)",
            method_info_address=1000,
            filter_typeinfo_address=2002,
            index_in_function=5,
            instruction_address=120,
            handler_function_address=400,
            registration_ordinal=0,
        ),
        HandlerRegistrationAccessEntry(
            type="handler_registration",
            access_kind="register",
            cls="B",
            handler_method="Void HandleB(B)",
            method_info_address=1001,
            filter_typeinfo_address=2003,
            index_in_function=3 if shared_tail else 8,
            instruction_address=300,
            handler_function_address=None,
            registration_ordinal=1,
        ),
    ]
    assert [call.args[1] for call in decode_insn.call_args_list] == (
        [108, 300, 304] if warm_cache else list(items)
    )


def test_decoded_operands_are_independent_of_native_instruction() -> None:
    source = SimpleNamespace(type=idaapi.o_displ, reg=3, addr=32, dtype=None)
    native = SimpleNamespace(ops=[source, SimpleNamespace(type=idaapi.o_void)])
    decoded = DecodedInstruction(100, cast(idaapi.insn_t, native), "mov")
    source.reg = 7
    source.addr = 64
    native.ops.clear()
    assert decoded.ops[0].reg == 3
    assert decoded.ops[0].addr == 32
    assert decoded.ops[7].type == idaapi.o_void

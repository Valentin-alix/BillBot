from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import ida_nalt
import idaapi
import pytest

from DBDofusUnity.proto_mapper_assembly.interfaces.il2cpp_json import MethodDefinition
from scripts.mapping.ida_tracer.state.basic_block import DecodedInstruction, FunctionScanPlan
from scripts.mapping.ida_tracer.tracing.enum_tracer import find_enum_switch_func_addrs


@pytest.mark.parametrize(
    ("field_indices", "switch_index", "queried_indices", "matched"),
    [
        ((), 8, (), False),
        ((1,), 1, tuple(range(2, 8)), False),
        ((1,), 2, (2,), True),
        ((1,), 7, tuple(range(2, 8)), True),
        ((1,), 8, tuple(range(2, 8)), False),
        ((1, 4), 10, tuple(range(2, 11)), True),
    ],
)
def test_switch_queries_require_a_field_in_the_previous_window(
    field_indices: tuple[int, ...], switch_index: int, queried_indices: tuple[int, ...], matched: bool
) -> None:
    function = cast(idaapi.func_t, SimpleNamespace(start_ea=100, end_ea=112))
    method = MethodDefinition(
        virtualAddress="0x64", name="Handle", signature="Void Handle()", group="Core.dll/Handler"
    )
    instructions = tuple(
        DecodedInstruction(
            100 + index,
            cast(
                idaapi.insn_t,
                SimpleNamespace(
                    ops=[
                        SimpleNamespace(
                            dtype=None,
                            reg=0,
                            type=idaapi.o_displ if index in field_indices else idaapi.o_reg,
                            addr=32,
                        ),
                        SimpleNamespace(dtype=None, reg=0, type=idaapi.o_displ, addr=99),
                    ]
                ),
            ),
            "mov",
        )
        for index in range(12)
    )
    plan = FunctionScanPlan(
        blocks={},
        predecessors={},
        entry_block_start=100,
        instructions_by_block={106: instructions[6:], 100: instructions[:6]},
    )

    def switch_info(ea: int) -> object | None:
        return object() if ea == 100 + switch_index else None

    with (
        patch.object(idaapi, "get_func", return_value=function),
        patch.object(ida_nalt, "get_switch_info", side_effect=switch_info) as get_switch_info,
    ):
        result = find_enum_switch_func_addrs([method], {32}, {(100, 112): plan})

    assert result == ({100} if matched else set())
    assert [call.args[0] for call in get_switch_info.call_args_list] == [
        100 + index for index in queried_indices
    ]

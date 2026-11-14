from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import idaapi

from DBDofusUnity.proto_mapper_assembly.controllers.access_signatures import (
    build_message_access_signatures_from_trace,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import AccessTraceDocument
from scripts.mapping.ida_tracer.state.basic_block import (
    DecodedInstruction,
    FunctionScanPlan,
)
from scripts.mapping.ida_tracer.tracing import stable_callees
from scripts.mapping.ida_tracer.tracing.trace_export import (
    build_traced_functions_from_proto_infos,
)
from tests.fixtures.proto_mapper.signatures import (
    builder_field_access_entry,
    function_access_info,
    stub_message,
)


def test_shared_native_function_retains_accesses_for_each_alias() -> None:
    functions = [
        function_access_info(
            name=f"Mapper::Void Handle({cls})",
            parameters=(cls,),
            start_address=100,
            access_infos=(builder_field_access_entry(cls=cls),),
        )
        for cls in ("ApiKeyEvent", "ShopTokenEvent")
    ]
    trace = AccessTraceDocument(
        functions_by_address=build_traced_functions_from_proto_infos(
            {function.name: function for function in functions}
        )
    )
    trace = AccessTraceDocument.model_validate_json(trace.model_dump_json())

    assert len(trace.functions_by_address["0x64"].access_infos) == 2
    for function in trace.to_proto_accesses().root.values():
        assert {entry.cls for entry in function.access_infos} == set(function.parameters)
    signatures = build_message_access_signatures_from_trace(
        access_trace=trace,
        messages=[stub_message(cls, field_offsets=(24,)) for cls in ("ApiKeyEvent", "ShopTokenEvent")],
    )
    for signature in signatures.values():
        assert len(signature.field_signatures[0].accesses_key) == 1
        assert signature.function_signatures[0].foreign_access_summary == []


def test_shared_aliases_do_not_duplicate_accesses_but_distinct_functions_do() -> None:
    functions = [
        function_access_info(
            name=f"Mapper::Void {name}(Message)",
            start_address=address,
            access_infos=(builder_field_access_entry(cls="Message"),),
        )
        for name, address in (("First", 100), ("Alias", 100), ("Second", 200))
    ]
    trace = AccessTraceDocument(
        functions_by_address=build_traced_functions_from_proto_infos(
            {function.name: function for function in functions}
        )
    )
    signature = build_message_access_signatures_from_trace(
        access_trace=trace,
        messages=[stub_message("Message", field_offsets=(24,))],
    )["Message"]

    assert len(signature.field_signatures[0].accesses) == 2
    assert len(signature.function_signatures) == 2


def test_stable_callees_include_external_tail_calls_only() -> None:
    function = cast(idaapi.func_t, SimpleNamespace(start_ea=100, end_ea=200))
    operations = [
        ("call", idaapi.o_near, 300),
        ("jmp", idaapi.o_near, 400),
        ("jmp", idaapi.o_near, 120),
        ("jmp", idaapi.o_reg, 500),
        ("jmp", idaapi.o_near, 600),
    ]
    instructions = tuple(
        DecodedInstruction(
            ea=100 + index,
            insn=cast(
                idaapi.insn_t,
                SimpleNamespace(ops=[SimpleNamespace(dtype=None, reg=0, type=operand_type, addr=target)]),
            ),
            mnemonic=mnemonic,
        )
        for index, (mnemonic, operand_type, target) in enumerate(operations)
    )
    plan = FunctionScanPlan(
        blocks={}, predecessors={}, entry_block_start=100, instructions_by_block={100: instructions}
    )

    def get_func(address: int) -> idaapi.func_t | None:
        functions: dict[int, idaapi.func_t] = {
            400: cast(idaapi.func_t, SimpleNamespace(start_ea=400)),
            120: function,
        }
        return functions.get(address)

    with (
        patch.object(stable_callees, "build_function_scan_plan", return_value=plan),
        patch.object(
            stable_callees.idaapi,
            "get_func",
            side_effect=get_func,
        ),
    ):
        result = stable_callees.collect_stable_callees(
            function,
            {300: "NormalCall", 400: "TailCall", 120: "Internal", 500: "Indirect", 600: "Unresolved"},
        )

    assert result == ["NormalCall", "TailCall"]

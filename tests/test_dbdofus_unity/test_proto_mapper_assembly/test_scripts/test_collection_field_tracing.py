from collections.abc import Callable, Iterable

import idaapi
import pytest

from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import AccessEntry, FieldAccessEntry
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessageField
from DBDofusUnity.proto_mapper_assembly.interfaces.field_category import FieldCategoryEnum
from scripts.mapping.ida_tracer.lookups.accessor_candidate import (
    AccessorCandidate,
)
from scripts.mapping.ida_tracer.simulation.scan_engine import (
    scan_function_instructions,
)
from scripts.mapping.ida_tracer.state.basic_block import (
    BasicBlock,
    DecodedInstruction,
    FunctionScanPlan,
)
from scripts.mapping.ida_tracer.state.types import (
    RegisterState,
    StackFrameState,
)
from tests.fixtures.proto_mapper.field_builders import dump_field, typed_dump_field


class Operand(idaapi.op_t):
    def __init__(self, kind: int = 0, *, reg: int = 0, addr: int = 0, value: int = 0, size: int = 4) -> None:
        self.type, self.reg, self.addr, self.value, self.dtype = kind, reg, addr, value, size


class Instruction(idaapi.insn_t):
    def __init__(self, mnemonic: str = "nop", *ops: idaapi.op_t) -> None:
        self.mnemonic = mnemonic
        self.ops = [*ops, *(Operand() for _ in range(8 - len(ops)))]

    def get_canon_mnem(self) -> str:
        return self.mnemonic

    def get_canon_feature(self) -> int:
        feature = idaapi.CF_USE1 | idaapi.CF_USE2
        if self.mnemonic in {"mov", "lea", "sub", "xor", "movups", "add"}:
            feature |= idaapi.CF_CHG1
        return feature


class Function(idaapi.func_t):
    def __init__(self, start: int, end: int) -> None:
        self.start_ea, self.end_ea = start, end


def reg(number: int, size: int = 8) -> Operand:
    return Operand(idaapi.o_reg, reg=number, size=size)


def mem(number: int, offset: int, size: int = 4) -> Operand:
    return Operand(idaapi.o_displ, reg=number, addr=offset, size=size)


def imm(value: int) -> Operand:
    return Operand(idaapi.o_imm, value=value)


def global_address(address: int) -> Operand:
    return Operand(idaapi.o_mem, addr=address, size=8)


def call(address: int) -> Instruction:
    return Instruction("call", Operand(idaapi.o_near, addr=address))


@pytest.fixture
def scan(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[AccessEntry]]:
    import idautils

    from scripts.mapping.ida_tracer.simulation.interproc_calls import (
        _invokes_interface_slot,
    )

    _invokes_interface_slot.cache_clear()

    def dtype_size(dtype: int) -> int:
        return dtype

    monkeypatch.setattr(idaapi, "get_dtype_size", dtype_size, raising=False)
    monkeypatch.setattr(idaapi, "insn_t", Instruction)

    def run(
        instructions: list[Instruction],
        registers: RegisterState,
        *,
        methodinfo: dict[int, str] | None = None,
        interfaces: dict[int, str] | None = None,
        accessors: dict[int, list[AccessorCandidate]] | None = None,
        successors_by_index: dict[int, tuple[int, ...]] | None = None,
        fields_by_cls: dict[str, dict[int, DumpCSMessageField]] | None = None,
    ) -> list[AccessEntry]:
        start, end = 100, 100 + len(instructions)
        function = Function(start, end)

        def get_func(ea: int) -> Function | None:
            return function if start <= ea < end else None

        def func_items(ea: int) -> Iterable[int]:
            return range(start, end) if ea == start else (ea,)

        monkeypatch.setattr(idaapi, "get_func", get_func)
        monkeypatch.setattr(idautils, "FuncItems", func_items)

        def decode(insn: Instruction, ea: int) -> int:
            insn.mnemonic = "jmp" if ea == 1000 else "ret"
            insn.ops = [Operand(idaapi.o_phrase, reg=0), *(Operand() for _ in range(7))]
            return 1

        monkeypatch.setattr(idaapi, "decode_insn", decode, raising=False)
        fields = fields_by_cls or {
            "Msg": {
                offset: dump_field(f"field_{offset}", f"property_{offset}", offset, FieldCategoryEnum.NUMBER)
                for offset in (32, 36, 40, 44)
            }
        }
        successors = successors_by_index or {0: ()}
        starts = sorted(successors)
        blocks = {
            start + index: BasicBlock(
                start + index,
                start + starts[position + 1] if position + 1 < len(starts) else end,
                tuple(start + successor for successor in successors[index]),
            )
            for position, index in enumerate(starts)
        }
        plan = FunctionScanPlan(
            blocks=blocks,
            predecessors={
                address: tuple(source for source, block in blocks.items() if address in block.successors)
                for address in blocks
            },
            entry_block_start=start,
            instructions_by_block={
                address: tuple(
                    DecodedInstruction(start + i, instructions[i], instructions[i].mnemonic)
                    for i in range(block.start_ea - start, block.end_ea - start)
                )
                for address, block in blocks.items()
            },
        )
        return scan_function_instructions(
            function,
            registers,
            StackFrameState(),
            {},
            fields,
            fields,
            {cls: cls for cls in fields},
            accessors or {},
            {},
            methodinfo,
            interfaces,
            function_scan_cache={(start, end): plan},
        )

    return run


def test_shared_current_returns_message_with_comparison_context(
    scan: Callable[..., list[AccessEntry]],
) -> None:
    entries = scan(
        [
            Instruction("mov", reg(8), reg(7)),
            Instruction("mov", reg(2), global_address(2000)),
            call(1000),
            Instruction("mov", reg(2, 4), mem(0, 32)),
            Instruction("sub", reg(2, 4), imm(1)),
            Instruction("jz"),
            Instruction("sub", reg(2, 4), imm(1)),
            Instruction("jz"),
            Instruction("cmp", reg(2, 4), imm(1)),
            Instruction("jnz"),
            Instruction("mov", reg(0, 4), mem(0, 36)),
        ],
        {7: ("repeated_enumerator", "Msg")},
        interfaces={2000: "Msg"},
    )
    fields = [entry for entry in entries if isinstance(entry, FieldAccessEntry)]
    assert {entry.field_offset for entry in fields} == {32, 36}
    discriminator = next(entry for entry in fields if entry.field_offset == 32)
    assert {comparison.constant for comparison in discriminator.comparisons} == {1, 2, 3}


@pytest.mark.parametrize("interruption", [Instruction("xor", reg(2), reg(2)), call(1002)])
def test_rewritten_value_has_no_comparison_context(
    scan: Callable[..., list[AccessEntry]],
    interruption: Instruction,
) -> None:
    entries = scan(
        [
            Instruction("mov", reg(2, 4), mem(7, 32)),
            interruption,
            Instruction("cmp", reg(2, 4), imm(42)),
            Instruction("jz"),
        ],
        {7: ("object", "Msg")},
    )
    assert all(not entry.comparisons for entry in entries if isinstance(entry, FieldAccessEntry))


def test_shared_getter_requires_matching_receiver(scan: Callable[..., list[AccessEntry]]) -> None:
    candidate = AccessorCandidate("Other", "getter", "field", "property", 32, "int", "int", None, None)
    entries = scan([call(1002)], {1: ("object", "Msg")}, accessors={1002: [candidate]})
    assert entries == []


def test_ienumerable_dispatch_tracks_enumerator_then_current(scan: Callable[..., list[AccessEntry]]) -> None:
    entries = scan(
        [
            Instruction("mov", reg(1), reg(7)),
            Instruction("mov", reg(2), global_address(2000)),
            call(1001),
            Instruction("mov", reg(0), mem(0, 0, 8)),
            Instruction("call", reg(0)),
            Instruction("mov", reg(7), reg(0)),
            Instruction("mov", reg(1), reg(7)),
            Instruction("mov", reg(2), global_address(2001)),
            call(1001),
            Instruction("mov", reg(0), mem(0, 0, 8)),
            Instruction("call", reg(0)),
            Instruction("mov", reg(0, 4), mem(0, 32)),
        ],
        {7: ("repeated_container", "Msg")},
        interfaces={2000: "ienumerable:Msg", 2001: "Msg"},
    )
    assert [(entry.cls, entry.field_offset) for entry in entries if isinstance(entry, FieldAccessEntry)] == [
        ("Msg", 32)
    ]


def test_different_field_origins_are_discarded_at_branch_join(scan: Callable[..., list[AccessEntry]]) -> None:
    entries = scan(
        [
            Instruction("jz"),
            Instruction("mov", reg(2, 4), mem(7, 32)),
            Instruction("jmp"),
            Instruction("mov", reg(2, 4), mem(7, 36)),
            Instruction("jmp"),
            Instruction("cmp", reg(2, 4), imm(42)),
            Instruction("jz"),
        ],
        {7: ("object", "Msg")},
        successors_by_index={0: (1, 3), 1: (5,), 3: (5,), 5: ()},
    )
    assert {entry.field_offset for entry in entries if isinstance(entry, FieldAccessEntry)} == {32, 36}
    assert all(not entry.comparisons for entry in entries if isinstance(entry, FieldAccessEntry))


def test_loop_revisits_processed_blocks_when_input_changes(scan: Callable[..., list[AccessEntry]]) -> None:
    entries = scan(
        [
            Instruction("nop"),
            Instruction("mov", reg(0, 4), mem(7, 32)),
            Instruction("mov", reg(7), imm(0)),
            Instruction("jmp"),
            Instruction("mov", reg(0, 4), mem(8, 36)),
        ],
        {7: ("object", "Msg"), 8: ("object", "Msg")},
        successors_by_index={0: (1,), 1: (2, 4), 2: (1,), 4: ()},
    )
    assert [(entry.cls, entry.field_offset) for entry in entries if isinstance(entry, FieldAccessEntry)] == [
        ("Msg", 36)
    ]


def test_cached_features_preserve_accesses_on_later_operands(
    scan: Callable[..., list[AccessEntry]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def feature(_insn: Instruction) -> int:
        return idaapi.CF_USE3 | idaapi.CF_CHG4

    monkeypatch.setattr(Instruction, "get_canon_feature", feature)
    entries = scan(
        [Instruction("custom", reg(0), reg(1), mem(7, 32), mem(7, 36))],
        {7: ("object", "Msg")},
    )
    assert [
        (entry.access_kind, entry.field_offset) for entry in entries if isinstance(entry, FieldAccessEntry)
    ] == [("read", 32), ("write", 36)]


@pytest.mark.parametrize("unavailable", [False, True])
def test_cached_features_preserve_mnemonic_fallback(
    scan: Callable[..., list[AccessEntry]], monkeypatch: pytest.MonkeyPatch, unavailable: bool
) -> None:
    def feature(_insn: Instruction) -> int:
        if unavailable:
            raise RuntimeError("feature unavailable")
        return 0

    monkeypatch.setattr(Instruction, "get_canon_feature", feature)
    entries = scan([Instruction("mov", reg(0), mem(7, 32))], {7: ("object", "Msg")})
    assert [
        (entry.access_kind, entry.field_offset) for entry in entries if isinstance(entry, FieldAccessEntry)
    ] == [("read", 32)]


def test_narrow_register_copy_does_not_compare_the_whole_field(
    scan: Callable[..., list[AccessEntry]],
) -> None:
    entries = scan(
        [
            Instruction("mov", reg(2, 4), mem(7, 32)),
            Instruction("movzx", reg(0, 4), reg(2, 1)),
            Instruction("cmp", reg(0, 4), imm(42)),
            Instruction("jz"),
        ],
        {7: ("object", "Msg")},
    )
    assert all(not entry.comparisons for entry in entries if isinstance(entry, FieldAccessEntry))


@pytest.mark.parametrize(("branch", "constant", "signed"), [("jl", -1, True), ("jb", 0xFFFFFFFF, False)])
def test_order_comparison_preserves_signedness(
    scan: Callable[..., list[AccessEntry]],
    branch: str,
    constant: int,
    signed: bool,
) -> None:
    entries = scan(
        [
            Instruction("mov", reg(2, 4), mem(7, 32)),
            Instruction("cmp", reg(2, 4), imm(0xFFFFFFFF)),
            Instruction(branch),
        ],
        {7: ("object", "Msg")},
    )
    comparisons = [
        comparison
        for entry in entries
        if isinstance(entry, FieldAccessEntry)
        for comparison in entry.comparisons
    ]
    assert [(comparison.predicate, comparison.constant, comparison.signed) for comparison in comparisons] == [
        ("lt", constant, signed)
    ]


def test_nested_collection_keeps_child_message_identity(scan: Callable[..., list[AccessEntry]]) -> None:
    fields = {
        "Msg": {
            32: typed_dump_field(
                field_name="children",
                property_name="Children",
                normalized_type="RepeatedField<Child>",
                category=FieldCategoryEnum.REPEATED,
                offset=32,
            )
        },
        "Child": {40: dump_field("value", "Value", 40, FieldCategoryEnum.NUMBER)},
    }
    entries = scan(
        [
            Instruction("mov", reg(1), mem(7, 32, 8)),
            Instruction("mov", reg(2), global_address(2000)),
            call(1002),
            Instruction("mov", reg(7), reg(0)),
            Instruction("mov", reg(8), reg(7)),
            Instruction("mov", reg(2), global_address(2001)),
            call(1000),
            Instruction("mov", reg(0, 4), mem(0, 40)),
        ],
        {7: ("object", "Msg")},
        methodinfo={2000: "Child"},
        interfaces={2001: "Child"},
        fields_by_cls=fields,
    )
    assert [(entry.cls, entry.field_offset) for entry in entries if isinstance(entry, FieldAccessEntry)] == [
        ("Msg", 32),
        ("Child", 40),
    ]


def test_float_bits_do_not_become_integer_comparison_context(scan: Callable[..., list[AccessEntry]]) -> None:
    fields = {
        "Msg": {
            32: typed_dump_field(
                field_name="value",
                property_name="Value",
                normalized_type="float",
                category=FieldCategoryEnum.NUMBER,
                offset=32,
            )
        }
    }
    entries = scan(
        [
            Instruction("mov", reg(2, 4), mem(7, 32)),
            Instruction("cmp", reg(2, 4), imm(42)),
            Instruction("jz"),
        ],
        {7: ("object", "Msg")},
        fields_by_cls=fields,
    )
    assert all(not entry.comparisons for entry in entries if isinstance(entry, FieldAccessEntry))

from dataclasses import InitVar, dataclass, field

import idaapi

from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import AccessKind
from scripts.mapping.ida_tracer.core.function_inspector import resolve_call_target_start
from scripts.mapping.ida_tracer.core.instruction_features import (
    changed_operand_indices,
    get_instruction_feature,
    operand_accesses,
)
from scripts.mapping.ida_tracer.core.operands import DecodedOperand, decode_operands


@dataclass(frozen=True, slots=True)
class BasicBlock:
    start_ea: int
    end_ea: int
    successors: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DecodedInstruction:
    ea: int
    insn: InitVar[idaapi.insn_t]
    mnemonic: str
    ops: tuple[DecodedOperand, ...] = field(init=False, repr=False)
    feature: int | None = field(init=False)
    operand_accesses: tuple[tuple[int, AccessKind], ...] = field(init=False, repr=False)
    changed_operand_indices: tuple[int, ...] = field(init=False, repr=False)
    direct_call_target_addr: int | None = field(init=False)

    def __post_init__(self, insn: idaapi.insn_t) -> None:
        object.__setattr__(self, "ops", decode_operands(insn))
        feature = get_instruction_feature(insn)
        object.__setattr__(self, "feature", feature)
        object.__setattr__(self, "operand_accesses", operand_accesses(self.mnemonic, feature))
        object.__setattr__(self, "changed_operand_indices", changed_operand_indices(feature))
        target = None
        if self.mnemonic in {"call", "jmp"} and self.ops:
            operand = self.ops[0]
            if operand.type in (idaapi.o_near, idaapi.o_far, idaapi.o_mem):
                target = resolve_call_target_start(int(operand.addr))
        object.__setattr__(self, "direct_call_target_addr", target)


@dataclass(slots=True)
class FunctionScanPlan:
    blocks: dict[int, BasicBlock]
    predecessors: dict[int, tuple[int, ...]]
    entry_block_start: int
    instructions_by_block: dict[int, tuple[DecodedInstruction, ...]]
    linear_instructions: tuple[DecodedInstruction, ...] | None = field(default=None, repr=False)


type FunctionScanCache = dict[tuple[int, int], FunctionScanPlan]

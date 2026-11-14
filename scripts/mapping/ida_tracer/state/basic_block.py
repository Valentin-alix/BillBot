from dataclasses import dataclass

import idaapi


@dataclass(frozen=True, slots=True)
class BasicBlock:
    start_ea: int
    end_ea: int
    successors: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DecodedInstruction:
    ea: int
    insn: idaapi.insn_t
    mnemonic: str


@dataclass(frozen=True, slots=True)
class FunctionScanPlan:
    blocks: dict[int, BasicBlock]
    predecessors: dict[int, tuple[int, ...]]
    entry_block_start: int
    instructions_by_block: dict[int, tuple[DecodedInstruction, ...]]


type FunctionScanCache = dict[tuple[int, int], FunctionScanPlan]

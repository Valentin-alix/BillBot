from collections import Counter, deque

import idaapi

from DBDofusUnity.proto_mapper_assembly.interfaces.function_access_signature import CfgStats
from scripts.mapping.ida_tracer.simulation.scan_engine import build_function_scan_plan
from scripts.mapping.ida_tracer.state.basic_block import FunctionScanCache, FunctionScanPlan


def opcode_histogram_for_func(
    func: idaapi.func_t,
    function_scan_cache: FunctionScanCache | None = None,
) -> Counter[str]:
    histo: Counter[str] = Counter()
    for instructions in build_function_scan_plan(func, function_scan_cache).instructions_by_block.values():
        for decoded in instructions:
            histo[decoded.mnemonic] += 1
    return histo


def cfg_stats_for_func(
    func: idaapi.func_t,
    function_scan_cache: FunctionScanCache | None = None,
) -> CfgStats:
    return build_cfg_stats(build_function_scan_plan(func, function_scan_cache))


def build_cfg_stats(scan_plan: FunctionScanPlan) -> CfgStats:
    return CfgStats(
        basic_block_count=len(scan_plan.blocks),
        edge_count=sum(len(_known_successors(scan_plan, block_start)) for block_start in scan_plan.blocks),
        back_edge_count=_count_back_edges(scan_plan),
        max_block_depth=_max_block_depth(scan_plan),
    )


def _known_successors(scan_plan: FunctionScanPlan, block_start: int) -> tuple[int, ...]:
    return tuple(
        successor for successor in scan_plan.blocks[block_start].successors if successor in scan_plan.blocks
    )


def _count_back_edges(scan_plan: FunctionScanPlan) -> int:
    open_blocks: set[int] = set()
    closed_blocks: set[int] = set()
    back_edge_count = 0
    for root_block_start in sorted(scan_plan.blocks):
        if root_block_start in closed_blocks:
            continue
        pending: list[tuple[int, int]] = [(root_block_start, 0)]
        while pending:
            block_start, successor_index = pending.pop()
            if successor_index == 0:
                if block_start in closed_blocks:
                    continue
                open_blocks.add(block_start)
            successors = _known_successors(scan_plan, block_start)
            if successor_index >= len(successors):
                open_blocks.discard(block_start)
                closed_blocks.add(block_start)
                continue
            pending.append((block_start, successor_index + 1))
            successor = successors[successor_index]
            if successor in open_blocks:
                back_edge_count += 1
            elif successor not in closed_blocks:
                pending.append((successor, 0))
    return back_edge_count


def _max_block_depth(scan_plan: FunctionScanPlan) -> int:
    depth_by_block: dict[int, int] = {scan_plan.entry_block_start: 0}
    pending: deque[int] = deque([scan_plan.entry_block_start])
    while pending:
        block_start = pending.popleft()
        for successor in _known_successors(scan_plan, block_start):
            if successor in depth_by_block:
                continue
            depth_by_block[successor] = depth_by_block[block_start] + 1
            pending.append(successor)
    return max(depth_by_block.values())


def opcode_histogram(func_ea: int) -> Counter[str]:
    func = idaapi.get_func(func_ea)
    if not func:
        err = f"func at {func_ea} not found"
        raise ValueError(err)
    return opcode_histogram_for_func(func)

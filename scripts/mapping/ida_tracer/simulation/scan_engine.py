from collections import deque
from collections.abc import Sequence

import ida_gdl
import idaapi
import idautils

from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import AccessEntry
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessageField
from scripts.mapping.ida_tracer.core.function_inspector import (
    get_operation_index_inside_function,
)
from scripts.mapping.ida_tracer.core.mnemonics import (
    ARITHMETIC_MNEMONICS,
    CMOV_MNEMONICS,
    VECTOR_MOVE_MNEMONICS,
)
from scripts.mapping.ida_tracer.lookups.accessor_candidate import AccessorCandidate
from scripts.mapping.ida_tracer.simulation.constants import (
    CALL_ARGUMENT_REGISTERS,
    MAX_INTERPROCEDURAL_DEPTH,
)
from scripts.mapping.ida_tracer.simulation.field_access import (
    collect_instruction_field_accesses,
    dedupe_and_sort_access_entries,
)
from scripts.mapping.ida_tracer.simulation.field_comparisons import track_field_comparisons
from scripts.mapping.ida_tracer.simulation.handlers import (
    handle_call_instruction,
    handle_lea_instruction,
    handle_vector_move_instruction,
    update_register_state_for_arithmetic,
)
from scripts.mapping.ida_tracer.simulation.interproc_calls import (
    get_direct_call_target_addr,
    update_register_state_for_call,
)
from scripts.mapping.ida_tracer.simulation.static_loads import (
    handle_mov_methodinfo_instruction,
    handle_mov_typeinfo_instruction,
)
from scripts.mapping.ida_tracer.simulation.type_guards import (
    apply_type_guarded_cmov,
    handle_mov_type_guard_instruction,
    resolve_type_guard_compare,
)
from scripts.mapping.ida_tracer.state.analysis_state import (
    AnalysisState,
    analysis_state_equals,
    copy_analysis_state,
    merge_analysis_states,
)
from scripts.mapping.ida_tracer.state.basic_block import (
    BasicBlock,
    DecodedInstruction,
    FunctionScanCache,
    FunctionScanPlan,
)
from scripts.mapping.ida_tracer.state.builders import build_initial_frame_state
from scripts.mapping.ida_tracer.state.interproc import (
    InterproceduralCacheKey,
    InterproceduralContext,
)
from scripts.mapping.ida_tracer.state.register_updates import (
    copy_frame_state,
    update_register_state_for_cmov,
    update_register_state_for_mov,
    update_stack_frame_for_instruction,
)
from scripts.mapping.ida_tracer.state.types import (
    HeapState,
    RegisterState,
    StackFrameState,
    StackState,
)


def scan_function_instructions(
    func: idaapi.func_t,
    initial_reg_state: RegisterState,
    initial_frame_state: StackFrameState,
    initial_stack_state: StackState,
    proto_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    tracking_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    tracking_type_lookup: dict[str, str],
    getter_setter_lookup: dict[int, list[AccessorCandidate]],
    typeinfo_lookup: dict[int, str],
    methodinfo_get_enumerator_lookup: dict[int, str] | None = None,
    ienumerator_typeinfo_lookup: dict[int, str] | None = None,
    initial_heap_state: HeapState | None = None,
    interprocedural_context: InterproceduralContext | None = None,
    function_scan_cache: FunctionScanCache | None = None,
) -> list[AccessEntry]:
    initial_state = AnalysisState(
        reg_state=dict(initial_reg_state),
        frame_state=copy_frame_state(initial_frame_state),
        stack_state=dict(initial_stack_state),
        heap_state=dict(initial_heap_state) if initial_heap_state is not None else {},
    )
    resolved_methodinfo_lookup = methodinfo_get_enumerator_lookup or {}
    resolved_ienumerator_typeinfo_lookup = ienumerator_typeinfo_lookup or {}
    resolved_interprocedural_context = interprocedural_context
    if resolved_interprocedural_context is None:
        resolved_interprocedural_context = InterproceduralContext(
            depth=0,
            max_depth=MAX_INTERPROCEDURAL_DEPTH,
            cache={},
            active_keys=set(),
        )
    scan_plan = build_function_scan_plan(func, function_scan_cache)
    input_states = _compute_block_input_states(
        scan_plan,
        initial_state,
        proto_fields_by_class_and_offset,
        tracking_fields_by_class_and_offset,
        tracking_type_lookup,
        getter_setter_lookup,
        typeinfo_lookup,
        resolved_methodinfo_lookup,
        resolved_ienumerator_typeinfo_lookup,
    )

    collected: list[AccessEntry] = []
    for block_start in sorted(scan_plan.blocks):
        input_state = input_states.get(block_start)
        if input_state is None:
            continue
        block_entries, _ = _scan_basic_block(
            scan_plan.instructions_by_block[block_start],
            input_state,
            proto_fields_by_class_and_offset,
            tracking_fields_by_class_and_offset,
            tracking_type_lookup,
            getter_setter_lookup,
            typeinfo_lookup,
            resolved_methodinfo_lookup,
            resolved_ienumerator_typeinfo_lookup,
            interprocedural_context=resolved_interprocedural_context,
            function_scan_cache=function_scan_cache,
        )
        collected.extend(block_entries)
    deduped_entries = dedupe_and_sort_access_entries(collected)
    if deduped_entries or len(scan_plan.blocks) <= 1:
        return deduped_entries
    linear_entries, _ = _scan_basic_block(
        _decode_instruction_range(func.start_ea, func.end_ea),
        initial_state,
        proto_fields_by_class_and_offset,
        tracking_fields_by_class_and_offset,
        tracking_type_lookup,
        getter_setter_lookup,
        typeinfo_lookup,
        resolved_methodinfo_lookup,
        resolved_ienumerator_typeinfo_lookup,
        interprocedural_context=resolved_interprocedural_context,
        function_scan_cache=function_scan_cache,
    )
    return dedupe_and_sort_access_entries(linear_entries)


def build_function_scan_plan(
    func: idaapi.func_t,
    function_scan_cache: FunctionScanCache | None = None,
) -> FunctionScanPlan:
    cache_key = (func.start_ea, func.end_ea)
    if function_scan_cache is not None:
        cached_plan = function_scan_cache.get(cache_key)
        if cached_plan is not None:
            return cached_plan
    blocks, predecessors, entry_block_start = _build_basic_blocks(func)
    plan = FunctionScanPlan(
        blocks=blocks,
        predecessors=predecessors,
        entry_block_start=entry_block_start,
        instructions_by_block={
            block_start: _decode_instruction_range(block.start_ea, block.end_ea)
            for block_start, block in blocks.items()
        },
    )
    if function_scan_cache is not None:
        function_scan_cache[cache_key] = plan
    return plan


def _build_basic_blocks(
    func: idaapi.func_t,
) -> tuple[dict[int, BasicBlock], dict[int, tuple[int, ...]], int]:
    flow_chart = list(ida_gdl.FlowChart(func))
    if not flow_chart:
        return _build_single_block_cfg(func)

    blocks: dict[int, BasicBlock] = {}
    predecessor_sets: dict[int, set[int]] = {}
    entry_block_start = func.start_ea
    for raw_block in flow_chart:
        block_start = int(raw_block.start_ea)
        block_end = int(raw_block.end_ea)
        if block_start <= func.start_ea < block_end:
            entry_block_start = block_start
        successor_starts = tuple(int(successor.start_ea) for successor in raw_block.succs())
        blocks[block_start] = BasicBlock(
            start_ea=block_start,
            end_ea=block_end,
            successors=successor_starts,
        )
        predecessor_sets.setdefault(block_start, set())
        for successor_start in successor_starts:
            predecessor_sets.setdefault(successor_start, set()).add(block_start)

    predecessors = {
        block_start: tuple(sorted(predecessor_sets.get(block_start, set()))) for block_start in blocks
    }
    return blocks, predecessors, entry_block_start


def _build_single_block_cfg(
    func: idaapi.func_t,
) -> tuple[dict[int, BasicBlock], dict[int, tuple[int, ...]], int]:
    block = BasicBlock(
        start_ea=func.start_ea,
        end_ea=func.end_ea,
        successors=(),
    )
    return {block.start_ea: block}, {block.start_ea: ()}, block.start_ea


def _decode_instruction_range(start_ea: int, end_ea: int) -> tuple[DecodedInstruction, ...]:
    decoded: list[DecodedInstruction] = []
    for ea in idautils.Heads(start_ea, end_ea):
        insn = idaapi.insn_t()
        if idaapi.decode_insn(insn, ea) == 0:
            continue
        decoded.append(DecodedInstruction(ea=ea, insn=insn, mnemonic=insn.get_canon_mnem()))
    return tuple(decoded)


def _compute_block_input_states(
    scan_plan: FunctionScanPlan,
    initial_state: AnalysisState,
    proto_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    tracking_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    tracking_type_lookup: dict[str, str],
    getter_setter_lookup: dict[int, list[AccessorCandidate]],
    typeinfo_lookup: dict[int, str],
    methodinfo_get_enumerator_lookup: dict[int, str],
    ienumerator_typeinfo_lookup: dict[int, str],
) -> dict[int, AnalysisState]:
    input_states: dict[int, AnalysisState] = {scan_plan.entry_block_start: copy_analysis_state(initial_state)}
    output_states: dict[int, AnalysisState] = {}
    pending_blocks: deque[int] = deque([scan_plan.entry_block_start])

    while pending_blocks:
        block_start = pending_blocks.popleft()
        input_state = input_states.get(block_start)
        if input_state is None:
            continue
        _, output_state = _scan_basic_block(
            scan_plan.instructions_by_block[block_start],
            input_state,
            proto_fields_by_class_and_offset,
            tracking_fields_by_class_and_offset,
            tracking_type_lookup,
            getter_setter_lookup,
            typeinfo_lookup,
            methodinfo_get_enumerator_lookup,
            ienumerator_typeinfo_lookup,
            collect_entries=False,
            function_scan_cache=None,
        )
        previous_output_state = output_states.get(block_start)
        if analysis_state_equals(previous_output_state, output_state):
            continue
        output_states[block_start] = output_state
        for successor_start in scan_plan.blocks[block_start].successors:
            if successor_start == scan_plan.entry_block_start:
                continue
            predecessor_states = [
                output_states[pred]
                for pred in scan_plan.predecessors.get(successor_start, ())
                if pred in output_states
            ]
            if not predecessor_states:
                continue
            merged_input_state = merge_analysis_states(predecessor_states)
            previous_input_state = input_states.get(successor_start)
            if analysis_state_equals(previous_input_state, merged_input_state):
                continue
            input_states[successor_start] = merged_input_state
            pending_blocks.append(successor_start)
    return input_states


def _scan_basic_block(
    instructions: Sequence[DecodedInstruction],
    input_state: AnalysisState,
    proto_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    tracking_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    tracking_type_lookup: dict[str, str],
    getter_setter_lookup: dict[int, list[AccessorCandidate]],
    typeinfo_lookup: dict[int, str],
    methodinfo_get_enumerator_lookup: dict[int, str],
    ienumerator_typeinfo_lookup: dict[int, str],
    interprocedural_context: InterproceduralContext | None = None,
    collect_entries: bool = True,
    function_scan_cache: FunctionScanCache | None = None,
) -> tuple[list[AccessEntry], AnalysisState]:
    state = copy_analysis_state(input_state)
    collected: list[AccessEntry] = []
    for decoded in instructions:
        collected.extend(
            _process_instruction(
                decoded.insn,
                decoded.ea,
                decoded.mnemonic,
                state,
                proto_fields_by_class_and_offset,
                tracking_fields_by_class_and_offset,
                tracking_type_lookup,
                getter_setter_lookup,
                typeinfo_lookup,
                methodinfo_get_enumerator_lookup,
                ienumerator_typeinfo_lookup,
                interprocedural_context,
                collect_entries=collect_entries,
                function_scan_cache=function_scan_cache,
            )
        )
    return collected, state


def _process_instruction(
    insn: idaapi.insn_t,
    ea: int,
    mnemonic: str,
    state: AnalysisState,
    proto_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    tracking_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    tracking_type_lookup: dict[str, str],
    getter_setter_lookup: dict[int, list[AccessorCandidate]],
    typeinfo_lookup: dict[int, str],
    methodinfo_get_enumerator_lookup: dict[int, str],
    ienumerator_typeinfo_lookup: dict[int, str],
    interprocedural_context: InterproceduralContext | None = None,
    *,
    collect_entries: bool = True,
    function_scan_cache: FunctionScanCache | None = None,
) -> list[AccessEntry]:
    collected: list[AccessEntry] = []
    comparison_entries = track_field_comparisons(
        insn, ea, mnemonic, state, proto_fields_by_class_and_offset, getter_setter_lookup
    )
    if collect_entries:
        collected.extend(comparison_entries)
    if collect_entries:
        collected.extend(
            collect_instruction_field_accesses(
                insn,
                ea,
                state.reg_state,
                proto_fields_by_class_and_offset,
            )
        )
    if mnemonic in {"mov", "movzx", "movsxd"}:
        update_register_state_for_mov(
            insn,
            state.reg_state,
            state.frame_state,
            state.stack_state,
            state.heap_state,
            tracking_fields_by_class_and_offset,
            tracking_type_lookup,
        )
        if mnemonic == "mov":
            handle_mov_type_guard_instruction(insn, state.reg_state)
            typeinfo_entries = handle_mov_typeinfo_instruction(
                insn,
                typeinfo_lookup,
                state.reg_state,
                ienumerator_typeinfo_lookup=ienumerator_typeinfo_lookup,
                ea=ea,
            )
            if collect_entries:
                collected.extend(typeinfo_entries)
            handle_mov_methodinfo_instruction(
                insn,
                methodinfo_get_enumerator_lookup,
                state.reg_state,
            )
    elif mnemonic in CMOV_MNEMONICS:
        update_register_state_for_cmov(
            insn,
            state.reg_state,
            state.frame_state,
            state.stack_state,
            state.heap_state,
            tracking_fields_by_class_and_offset,
            tracking_type_lookup,
        )
        apply_type_guarded_cmov(insn, state.reg_state, state.type_guard)
        state.type_guard = None
    elif mnemonic == "cmp":
        state.type_guard = resolve_type_guard_compare(insn, state.reg_state, typeinfo_lookup)
    elif mnemonic == "test":
        state.type_guard = None
    elif mnemonic in ARITHMETIC_MNEMONICS:
        state.type_guard = None
        update_register_state_for_arithmetic(insn, state.reg_state, state.heap_state)
    elif mnemonic == "call":
        state.type_guard = None
        direct_call_entries = (
            handle_call_instruction(insn, getter_setter_lookup, state.reg_state, ea)
            if collect_entries
            else []
        )
        collected.extend(direct_call_entries)
        if not direct_call_entries and interprocedural_context is not None:
            collected.extend(
                _collect_interprocedural_call_accesses(
                    insn,
                    state,
                    proto_fields_by_class_and_offset,
                    tracking_fields_by_class_and_offset,
                    tracking_type_lookup,
                    getter_setter_lookup,
                    typeinfo_lookup,
                    methodinfo_get_enumerator_lookup,
                    ienumerator_typeinfo_lookup,
                    interprocedural_context,
                    function_scan_cache,
                    call_ea=ea,
                )
            )
        update_register_state_for_call(
            insn,
            state.reg_state,
            getter_setter_lookup,
            ienumerator_typeinfo_lookup,
            heap_state=state.heap_state,
            stack_state=state.stack_state,
        )
    elif mnemonic == "lea":
        state.type_guard = None
        lea_entries = handle_lea_instruction(
            insn,
            typeinfo_lookup,
            state.reg_state,
            proto_fields_by_class_and_offset,
            methodinfo_get_enumerator_lookup=methodinfo_get_enumerator_lookup,
            ienumerator_typeinfo_lookup=ienumerator_typeinfo_lookup,
            ea=ea,
            frame_state=state.frame_state,
        )
        if collect_entries:
            collected.extend(lea_entries)
    elif mnemonic in VECTOR_MOVE_MNEMONICS:
        state.type_guard = None
        handle_vector_move_instruction(
            insn,
            state.reg_state,
            state.frame_state,
            state.stack_state,
        )
    update_stack_frame_for_instruction(insn, state.frame_state)
    return collected


def _collect_interprocedural_call_accesses(
    insn: idaapi.insn_t,
    state: AnalysisState,
    proto_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    tracking_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    tracking_type_lookup: dict[str, str],
    getter_setter_lookup: dict[int, list[AccessorCandidate]],
    typeinfo_lookup: dict[int, str],
    methodinfo_get_enumerator_lookup: dict[int, str],
    ienumerator_typeinfo_lookup: dict[int, str],
    context: InterproceduralContext,
    function_scan_cache: FunctionScanCache | None,
    *,
    call_ea: int,
) -> list[AccessEntry]:
    if context.depth >= context.max_depth:
        return []
    target_addr = get_direct_call_target_addr(insn)
    if target_addr is None:
        return []
    call_arg_state = _build_call_argument_register_state(state.reg_state)
    if not call_arg_state:
        return []
    cache_key = InterproceduralCacheKey(target_addr, tuple(sorted(call_arg_state.items())))
    cached_entries = context.cache.get(cache_key)
    if cached_entries is not None:
        return _rebase_interprocedural_entries_to_callsite(cached_entries, call_ea=call_ea)
    if cache_key in context.active_keys:
        return []
    target_func = idaapi.get_func(target_addr)
    if target_func is None:
        return []

    context.active_keys.add(cache_key)
    nested_context = InterproceduralContext(
        depth=context.depth + 1,
        max_depth=context.max_depth,
        cache=context.cache,
        active_keys=context.active_keys,
    )
    try:
        entries = scan_function_instructions(
            target_func,
            call_arg_state,
            build_initial_frame_state(),
            {},
            proto_fields_by_class_and_offset,
            tracking_fields_by_class_and_offset,
            tracking_type_lookup,
            getter_setter_lookup,
            typeinfo_lookup,
            methodinfo_get_enumerator_lookup,
            ienumerator_typeinfo_lookup,
            interprocedural_context=nested_context,
            function_scan_cache=function_scan_cache,
        )
    finally:
        context.active_keys.discard(cache_key)
    context.cache[cache_key] = list(entries)
    return _rebase_interprocedural_entries_to_callsite(entries, call_ea=call_ea)


def _rebase_interprocedural_entries_to_callsite(
    entries: list[AccessEntry], *, call_ea: int
) -> list[AccessEntry]:
    callsite_index = get_operation_index_inside_function(call_ea)
    return [
        entry.model_copy(update={"instruction_address": call_ea, "index_in_function": callsite_index})
        for entry in entries
    ]


def _build_call_argument_register_state(reg_state: RegisterState) -> RegisterState:
    supported_domains = {
        "object",
        "object_union",
        "candidate_object",
        "object_offset",
        "untyped_object",
        "repeated_container",
    }
    return {
        register_number: tracked_value
        for register_number in CALL_ARGUMENT_REGISTERS
        if (tracked_value := reg_state.get(register_number)) is not None
        and tracked_value[0] in supported_domains
    }

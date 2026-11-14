from dataclasses import dataclass, field
from scripts.mapping.ida_tracer.state.numeric_provenance import (
    NumericFieldValue, NumericComparison,
)

from scripts.mapping.ida_tracer.state.register_updates import copy_frame_state
from scripts.mapping.ida_tracer.state.types import (
    HeapState,
    RegisterState,
    StackFrameState,
    StackState,
    TrackedValue,
    build_tracked_object_value,
    get_tracked_object_classes,
)


@dataclass(frozen=True, slots=True)
class TypeGuard:
    object_reg: int
    cls: str


@dataclass(slots=True)
class AnalysisState:
    reg_state: RegisterState
    frame_state: StackFrameState
    stack_state: StackState
    heap_state: HeapState
    type_guard: TypeGuard | None = None
    numeric_registers: dict[int, NumericFieldValue] = field(default_factory=dict[int, NumericFieldValue])
    numeric_stack: dict[int, NumericFieldValue] = field(default_factory=dict[int, NumericFieldValue])
    numeric_comparison: NumericComparison | None = None


def copy_analysis_state(state: AnalysisState) -> AnalysisState:
    return AnalysisState(
        reg_state=dict(state.reg_state),
        frame_state=copy_frame_state(state.frame_state),
        stack_state=dict(state.stack_state),
        heap_state=dict(state.heap_state),
        type_guard=state.type_guard,
        numeric_registers=dict(state.numeric_registers),
        numeric_stack=dict(state.numeric_stack),
        numeric_comparison=state.numeric_comparison,
    )


def analysis_state_equals(
    left: AnalysisState | None,
    right: AnalysisState | None,
) -> bool:
    if left is None or right is None:
        return left is right
    return (
        left.reg_state == right.reg_state
        and left.frame_state == right.frame_state
        and left.stack_state == right.stack_state
        and left.heap_state == right.heap_state
        and left.type_guard == right.type_guard
        and left.numeric_registers == right.numeric_registers
        and left.numeric_stack == right.numeric_stack
        and left.numeric_comparison == right.numeric_comparison
    )


def merge_analysis_states(states: list[AnalysisState]) -> AnalysisState:
    merged_frames = [state.frame_state for state in states]
    merged_frame_state = _merge_frame_states(merged_frames)
    merged_stack_state = (
        _merge_tracked_maps([state.stack_state for state in states])
        if _all_frame_states_equal(merged_frames)
        else {}
    )
    merged_heap_state = _merge_heap_maps([state.heap_state for state in states])
    return AnalysisState(
        reg_state=_merge_tracked_maps([state.reg_state for state in states]),
        frame_state=merged_frame_state,
        stack_state=merged_stack_state,
        heap_state=merged_heap_state,
        type_guard=_merge_type_guards([state.type_guard for state in states]),
        numeric_registers={
            key: value for key, value in states[0].numeric_registers.items()
            if all(state.numeric_registers.get(key) == value for state in states[1:])
        },
        numeric_stack={
            key: value for key, value in states[0].numeric_stack.items()
            if _all_frame_states_equal(merged_frames)
            and all(state.numeric_stack.get(key) == value for state in states[1:])
        },
        numeric_comparison=(
            states[0].numeric_comparison
            if all(state.numeric_comparison == states[0].numeric_comparison for state in states[1:])
            else None
        ),
    )


def _all_frame_states_equal(frame_states: list[StackFrameState]) -> bool:
    return all(frame_state == frame_states[0] for frame_state in frame_states[1:])


def _merge_frame_states(frame_states: list[StackFrameState]) -> StackFrameState:
    if _all_frame_states_equal(frame_states):
        return copy_frame_state(frame_states[0])
    return StackFrameState(rsp_entry_offset=None, rbp_entry_offset=None)


def _merge_tracked_maps(
    tracked_maps: list[dict[int, TrackedValue]],
) -> dict[int, TrackedValue]:
    common_values = dict(tracked_maps[0])
    for current_map in tracked_maps[1:]:
        for key in list(common_values):
            current_value = current_map.get(key)
            if current_value is None:
                common_values.pop(key)
                continue
            merged_value = _merge_tracked_value(common_values[key], current_value)
            if merged_value is None:
                common_values.pop(key)
                continue
            common_values[key] = merged_value
    return common_values


def _merge_heap_maps(tracked_maps: list[HeapState]) -> HeapState:
    common_values: HeapState = dict(tracked_maps[0])
    for current_map in tracked_maps[1:]:
        for key in list(common_values):
            current_value = current_map.get(key)
            if current_value is None:
                common_values.pop(key)
                continue
            merged_value = _merge_tracked_value(common_values[key], current_value)
            if merged_value is None:
                common_values.pop(key)
                continue
            common_values[key] = merged_value
    return common_values


def _merge_type_guards(type_guards: list[TypeGuard | None]) -> TypeGuard | None:
    first_guard = type_guards[0]
    if first_guard is None:
        return None
    if all(type_guard == first_guard for type_guard in type_guards[1:]):
        return first_guard
    return None


def _merge_tracked_value(
    left: TrackedValue,
    right: TrackedValue,
) -> TrackedValue | None:
    if left == right:
        return left
    left_domain, left_class_name = left
    right_domain, right_class_name = right
    if left_class_name != right_class_name:
        left_object_classes = get_tracked_object_classes(left)
        right_object_classes = get_tracked_object_classes(right)
        if left_object_classes is not None and right_object_classes is not None:
            return build_tracked_object_value(left_object_classes | right_object_classes)
        return None
    if {left_domain, right_domain} == {"candidate_object", "object"}:
        return "candidate_object", left_class_name
    if left_domain == "object_union" and right_domain in {"object", "candidate_object", "object_union"}:
        left_object_classes = get_tracked_object_classes(left)
        right_object_classes = get_tracked_object_classes(right)
        if left_object_classes is not None and right_object_classes is not None:
            return build_tracked_object_value(left_object_classes | right_object_classes)
    return None

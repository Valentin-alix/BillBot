import pytest

from scripts.mapping.ida_tracer.state.analysis_state import AnalysisState, TypeGuard, merge_analysis_states
from scripts.mapping.ida_tracer.state.numeric_provenance import NumericComparison, NumericFieldValue
from scripts.mapping.ida_tracer.state.types import StackFrameState


def _state() -> AnalysisState:
    value = NumericFieldValue(
        cls="Msg",
        field_name="value",
        property_name="Value",
        field_offset=32,
        access_kind="read",
        instruction_address=100,
        index_in_function=0,
        width=32,
    )
    return AnalysisState(
        reg_state={1: ("object", "Msg")},
        frame_state=StackFrameState(rsp_entry_offset=0, rbp_entry_offset=8),
        stack_state={0: ("object", "Msg")},
        heap_state={(1, 32): ("object", "Msg")},
        type_guard=TypeGuard(1, "Msg"),
        numeric_registers={0: value},
        numeric_stack={0: value},
        numeric_comparison=NumericComparison(value, 42, 101),
    )


def test_single_predecessor_merge_copies_all_mutable_state() -> None:
    predecessor = _state()
    merged = merge_analysis_states([predecessor])
    assert merged == predecessor
    assert merged is not predecessor
    assert merged.frame_state is not predecessor.frame_state

    merged.reg_state.clear()
    merged.stack_state.clear()
    merged.heap_state.clear()
    merged.numeric_registers.clear()
    merged.numeric_stack.clear()
    merged.frame_state.rsp_entry_offset = -8
    merged.frame_state.rbp_entry_offset = None
    merged.type_guard = None
    merged.numeric_comparison = None
    assert predecessor == _state()


@pytest.mark.parametrize("frames_equal", [False, True])
def test_join_keeps_stack_values_only_when_frames_agree(frames_equal: bool) -> None:
    left, right = _state(), _state()
    if not frames_equal:
        right.frame_state.rsp_entry_offset = -8
    right.reg_state[2] = ("object", "Other")
    right.numeric_registers[2] = right.numeric_registers[0]

    merged = merge_analysis_states([left, right])
    assert merged.reg_state == left.reg_state
    assert merged.heap_state == left.heap_state
    assert merged.numeric_registers == left.numeric_registers
    assert merged.numeric_comparison == left.numeric_comparison
    assert merged.type_guard == left.type_guard
    assert merged.stack_state == (left.stack_state if frames_equal else {})
    assert merged.numeric_stack == (left.numeric_stack if frames_equal else {})
    assert merged.frame_state == (
        left.frame_state if frames_equal else StackFrameState(rsp_entry_offset=None, rbp_entry_offset=None)
    )
    assert merged.frame_state is not left.frame_state

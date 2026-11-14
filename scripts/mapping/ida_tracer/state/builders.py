from scripts.mapping.ida_tracer.signatures.types import ProtoParameterSeed
from scripts.mapping.ida_tracer.state.types import (
    FIRST_STACK_PARAM_OFFSET,
    WINDOWS_X64_PARAM_REGISTERS,
    HeapState,
    RegisterState,
    StackFrameState,
    StackState,
)


def build_initial_register_state(
    proto_parameter_seeds: list[ProtoParameterSeed],
    has_this: bool,
    this_class_name: str | None = None,
) -> RegisterState:
    """Instance methods reserve rcx for this; static methods use it for the first parameter."""
    reg_state: RegisterState = {}
    if has_this and this_class_name is not None:
        reg_state[WINDOWS_X64_PARAM_REGISTERS[0]] = ("object", this_class_name)
    first_explicit_param_register_index = 1 if has_this else 0
    for param_index, tracked_domain, class_name in proto_parameter_seeds:
        register_list_index = param_index + first_explicit_param_register_index
        if register_list_index >= len(WINDOWS_X64_PARAM_REGISTERS):
            break
        reg_num = WINDOWS_X64_PARAM_REGISTERS[register_list_index]
        reg_state[reg_num] = (tracked_domain, class_name)
    return reg_state


def build_initial_stack_state(
    proto_parameter_seeds: list[ProtoParameterSeed],
    has_this: bool,
) -> StackState:
    """Windows x64 stack arguments start at rsp+0x28, after the register parameters."""
    stack_state: StackState = {}
    first_stack_param_index = len(WINDOWS_X64_PARAM_REGISTERS) - (1 if has_this else 0)
    for param_index, tracked_domain, class_name in proto_parameter_seeds:
        if param_index < first_stack_param_index:
            continue
        stack_offset = FIRST_STACK_PARAM_OFFSET + ((param_index - first_stack_param_index) * 8)
        stack_state[stack_offset] = (tracked_domain, class_name)
    return stack_state


def build_initial_frame_state() -> StackFrameState:
    return StackFrameState()


def build_initial_heap_state() -> HeapState:
    return {}

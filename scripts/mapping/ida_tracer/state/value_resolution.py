import idaapi

from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessageField
from DBDofusUnity.proto_mapper_assembly.interfaces.field_category import FieldCategoryEnum
from DBDofusUnity.proto_mapper_assembly.parsers._clr_type_utils import (
    extract_map_inner_types,
    extract_repeated_inner_type,
)
from scripts.mapping.ida_tracer.core.operands import Operand, get_displacement_value
from scripts.mapping.ida_tracer.lookups.name_resolution import resolve_message_type_name
from scripts.mapping.ida_tracer.signatures.type_utils import is_non_message_type
from scripts.mapping.ida_tracer.state.encoding import decode_object_offset
from scripts.mapping.ida_tracer.state.types import (
    MAP_KVP_VALUE_OFFSET,
    RBP_REG,
    RSP_REG,
    STACK_BASE_REGISTERS,
    HeapState,
    RegisterState,
    StackFrameState,
    StackSlot,
    StackState,
    TrackedValue,
    build_tracked_object_value,
    get_tracked_object_classes,
)


def resolve_move_source(
    source_operand: Operand,
    reg_state: RegisterState,
    frame_state: StackFrameState,
    stack_state: StackState,
    heap_state: HeapState,
    tracking_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    tracking_type_lookup: dict[str, str],
) -> TrackedValue | None:
    if source_operand.type == idaapi.o_reg:
        return reg_state.get(source_operand.reg)
    if source_operand.type == idaapi.o_phrase:
        stack_slot = get_stack_slot(source_operand, frame_state)
        if stack_slot is not None:
            return stack_state.get(stack_slot)
        tracked_value = reg_state.get(source_operand.reg)
        if tracked_value is not None and tracked_value[0] in {
            "pending_indirect_current",
            "pending_indirect_kvp_current",
            "pending_indirect_get_enumerator",
        }:
            return tracked_value
        heap_value = heap_state.get((source_operand.reg, 0))
        if heap_value is not None:
            return heap_value
        return _resolve_nested_object_value(
            source_operand,
            reg_state,
            tracking_fields_by_class_and_offset,
            tracking_type_lookup,
        )
    stack_slot = get_stack_slot(source_operand, frame_state)
    if stack_slot is not None:
        return stack_state.get(stack_slot)
    if source_operand.type == idaapi.o_displ:
        tracked_value = reg_state.get(source_operand.reg)
        if (
            tracked_value is not None
            and tracked_value[0] in {
                "pending_indirect_current", "pending_indirect_kvp_current", "pending_indirect_get_enumerator"
            }
            and get_displacement_value(source_operand) == 0
        ):
            return tracked_value
        if (
            tracked_value is not None
            and tracked_value[0] == "map_kvp_output"
            and get_displacement_value(source_operand) == MAP_KVP_VALUE_OFFSET
        ):
            return ("object", tracked_value[1])
        heap_value = heap_state.get((source_operand.reg, get_displacement_value(source_operand)))
        if heap_value is not None:
            return heap_value
        return _resolve_nested_object_value(
            source_operand,
            reg_state,
            tracking_fields_by_class_and_offset,
            tracking_type_lookup,
        )
    return None


def get_stack_slot(
    operand: Operand,
    frame_state: StackFrameState,
) -> StackSlot | None:
    if operand.type not in {idaapi.o_displ, idaapi.o_phrase}:
        return None
    base_register = operand.reg
    if base_register not in STACK_BASE_REGISTERS:
        return None
    displacement = 0 if operand.type == idaapi.o_phrase else get_displacement_value(operand)
    if base_register == RSP_REG:
        if frame_state.rsp_entry_offset is None:
            return None
        return frame_state.rsp_entry_offset + displacement
    if frame_state.rbp_entry_offset is None:
        return None
    return frame_state.rbp_entry_offset + displacement


def is_heap_tracking_destination(
    operand: Operand,
    frame_state: StackFrameState,
    reg_state: RegisterState,
) -> bool:
    if operand.type not in {idaapi.o_displ, idaapi.o_phrase}:
        return False
    if operand.reg == RSP_REG:
        return False
    base_value = reg_state.get(operand.reg)
    if base_value is not None and base_value[0] in {"field_address", "object_offset"}:
        return False
    return operand.reg != RBP_REG or frame_state.rbp_entry_offset is None


def resolve_referenced_tracked_value(
    field: DumpCSMessageField,
    message_type_lookup: dict[str, str],
) -> TrackedValue | None:
    if field.category == FieldCategoryEnum.MESSAGE:
        resolved_type = resolve_message_type_name(field.normalized_type, message_type_lookup)
        if resolved_type is None:
            return None
        return "object", resolved_type
    if field.category == FieldCategoryEnum.ENUM:
        return None
    if field.category == FieldCategoryEnum.REPEATED:
        repeated_inner_type = extract_repeated_inner_type(field.normalized_type)
        if repeated_inner_type is None:
            return None
        if is_non_message_type(repeated_inner_type, field.enum_key_type, field.enum_value_type):
            return None
        resolved_type = resolve_message_type_name(repeated_inner_type, message_type_lookup)
        if resolved_type is None:
            return None
        return "repeated_container", resolved_type
    if field.category != FieldCategoryEnum.MAP:
        return None
    map_inner_types = extract_map_inner_types(field.normalized_type)
    if map_inner_types is None:
        return None
    _, value_type = map_inner_types
    if is_non_message_type(value_type, field.enum_key_type, field.enum_value_type):
        return None
    resolved_type = resolve_message_type_name(value_type, message_type_lookup)
    if resolved_type is None:
        return None
    return "repeated_container", resolved_type


def _resolve_nested_object_value(
    operand: Operand,
    reg_state: RegisterState,
    tracking_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    tracking_type_lookup: dict[str, str],
) -> TrackedValue | None:
    parent_info = reg_state.get(operand.reg)
    if parent_info is None:
        return None
    parent_domain, parent_class_name = parent_info
    base_offset = 0
    if parent_domain == "object_offset":
        parent_class_name, base_offset = decode_object_offset(parent_class_name)
        parent_class_names = frozenset({parent_class_name})
    else:
        parent_class_names = get_tracked_object_classes(parent_info)
    if parent_class_names is None:
        return None
    displacement = 0 if operand.type == idaapi.o_phrase else get_displacement_value(operand)
    child_values: list[TrackedValue] = []
    for candidate_parent_class_name in parent_class_names:
        offset_map = tracking_fields_by_class_and_offset.get(candidate_parent_class_name)
        if offset_map is None:
            continue
        field = offset_map.get(base_offset + displacement)
        if field is None:
            continue
        tracked_value = resolve_referenced_tracked_value(field, tracking_type_lookup)
        if tracked_value is None:
            continue
        _, child_class_name = tracked_value
        if child_class_name not in tracking_fields_by_class_and_offset:
            continue
        child_values.append(tracked_value)
    if not child_values:
        return None
    if parent_domain == "candidate_object":
        reg_state[operand.reg] = ("object", parent_class_name)
    return _merge_resolved_child_values(child_values)


def _merge_resolved_child_values(child_values: list[TrackedValue]) -> TrackedValue | None:
    merged_value = child_values[0]
    for child_value in child_values[1:]:
        if child_value == merged_value:
            continue
        merged_object_classes = get_tracked_object_classes(merged_value)
        child_object_classes = get_tracked_object_classes(child_value)
        if merged_object_classes is None or child_object_classes is None:
            return None
        built_value = build_tracked_object_value(merged_object_classes | child_object_classes)
        if built_value is None:
            return None
        merged_value = built_value
    return merged_value

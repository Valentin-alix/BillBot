from dataclasses import dataclass
from typing import Literal

_OBJECT_UNION_SEPARATOR = "|"
_MIN_OBJECT_UNION_CLASS_COUNT = 2

# Windows x64 IDA parameter registers: rcx=1, rdx=2, r8=8, r9=9.
WINDOWS_X64_PARAM_REGISTERS: list[int] = [1, 2, 8, 9]
WINDOWS_X64_VOLATILE_REGISTERS: frozenset[int] = frozenset({0, 1, 2, 8, 9, 10, 11, 33})
RSP_REG: int = 4
RBP_REG: int = 5
STACK_BASE_REGISTERS: frozenset[int] = frozenset({RSP_REG, RBP_REG})
FIRST_STACK_PARAM_OFFSET: int = 0x28
MAP_KVP_VALUE_OFFSET: int = 8


type TrackedValue = tuple[
    Literal[
        "object",
        "object_union",
        "untyped_object",
        "object_vtable",
        "typeinfo",
        "candidate_object",
        "object_offset",
        "field_address",
        "repeated_container",
        "repeated_enumerator",
        "methodinfo_get_enumerator",
        "pending_indirect_current_base",
        "pending_indirect_current",
        "pending_indirect_kvp_current_base",
        "pending_indirect_kvp_current",
        "map_kvp_output",
        "stack_address",
        "pending_indirect_get_enumerator_base",
        "pending_indirect_get_enumerator",
    ],
    str,
]
type RegisterState = dict[int, TrackedValue]
type StackSlot = int
type StackState = dict[StackSlot, TrackedValue]
type HeapSlot = tuple[int, int]
type HeapState = dict[HeapSlot, TrackedValue]


def encode_object_union(class_names: frozenset[str]) -> str:
    if len(class_names) < _MIN_OBJECT_UNION_CLASS_COUNT:
        message = f"object union requires at least two classes, got {sorted(class_names)}"
        raise ValueError(message)
    return _OBJECT_UNION_SEPARATOR.join(sorted(class_names))


def decode_object_union(encoded_class_names: str) -> frozenset[str]:
    return frozenset(encoded_class_names.split(_OBJECT_UNION_SEPARATOR))


def get_tracked_object_classes(value: TrackedValue) -> frozenset[str] | None:
    domain, class_name = value
    if domain in {"object", "candidate_object"}:
        return frozenset({class_name})
    if domain == "object_union":
        return decode_object_union(class_name)
    return None


def build_tracked_object_value(class_names: frozenset[str]) -> TrackedValue | None:
    if not class_names:
        return None
    if len(class_names) == 1:
        return "object", next(iter(class_names))
    return "object_union", encode_object_union(class_names)


@dataclass(slots=True)
class StackFrameState:
    rsp_entry_offset: int | None = 0
    rbp_entry_offset: int | None = None

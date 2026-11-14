from dataclasses import dataclass
from typing import NamedTuple

from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import AccessEntry
from scripts.mapping.ida_tracer.state.types import TrackedValue


class InterproceduralCacheKey(NamedTuple):
    target_addr: int
    call_arg_state: tuple[tuple[int, TrackedValue], ...]


@dataclass(slots=True)
class InterproceduralContext:
    depth: int
    max_depth: int
    cache: dict[InterproceduralCacheKey, list[AccessEntry]]
    active_keys: set[InterproceduralCacheKey]

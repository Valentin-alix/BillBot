from collections.abc import Generator, Mapping
from contextlib import contextmanager

import idaapi
import idautils

_operation_indices_by_function: dict[tuple[int, int], dict[int, int]] | None = None
_call_target_starts: dict[int, int] | None = None


@contextmanager
def cache_function_inspection() -> Generator[None]:
    global _operation_indices_by_function, _call_target_starts
    previous_cache = _operation_indices_by_function
    previous_targets = _call_target_starts
    _operation_indices_by_function = {}
    _call_target_starts = {}
    try:
        yield
    finally:
        _operation_indices_by_function = previous_cache
        _call_target_starts = previous_targets


def resolve_call_target_start(ea: int) -> int:
    if _call_target_starts is not None:
        cached_start = _call_target_starts.get(ea)
        if cached_start is not None:
            return cached_start
    func = idaapi.get_func(ea)
    start = func.start_ea if func is not None and isinstance(func.start_ea, int) else ea
    if _call_target_starts is not None:
        _call_target_starts[ea] = start
    return start


def get_function_operation_indices(func: idaapi.func_t) -> Mapping[int, int]:
    cache_key = (func.start_ea, func.end_ea)
    if _operation_indices_by_function is not None:
        cached_indices = _operation_indices_by_function.get(cache_key)
        if cached_indices is not None:
            return cached_indices
    indices = {item_ea: index for index, item_ea in enumerate(idautils.FuncItems(func.start_ea))}
    if _operation_indices_by_function is not None:
        _operation_indices_by_function[cache_key] = indices
    return indices


def get_operation_index_inside_function(ea: int) -> int:
    func = idaapi.get_func(ea)
    if not func:
        err = f"func at {ea} not found"
        raise ValueError(err)

    index = get_function_operation_indices(func).get(ea)
    if index is not None:
        return index

    err = f"Did not found operation at {ea} ?!"
    raise ValueError(err)

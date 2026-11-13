import functools
import threading
from collections.abc import Callable
from time import perf_counter
from typing import Final, ParamSpec, TypeVar
from src.utils.project_paths import IS_PACKAGED
from utils.env_config import get_bool_from_env

P = ParamSpec("P")
R = TypeVar("R")

PROFILING_ENABLED: Final = not IS_PACKAGED and get_bool_from_env("PROFILING_ENABLED")


def profiled_slot(func: Callable[P, R], threshold_ms: int = 1) -> Callable[P, R]:
    if not PROFILING_ENABLED:
        return func

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start = perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            dt = (perf_counter() - start) * 1000
            if dt >= threshold_ms:
                function_name = getattr(func, "__qualname__", type(func).__qualname__)
                print(f"[SLOT] {function_name} | {dt:.2f} ms | {threading.current_thread().name}")

    return wrapper

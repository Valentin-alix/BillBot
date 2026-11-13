from typing import Final

class DisplayT:
    ea: int
    type: str

def auto_wait() -> None: ...
def auto_is_ok() -> bool: ...
def auto_display_t() -> DisplayT: ...
def auto_make_step(min_ea: int, max_ea: int) -> bool: ...
def get_auto_display(display_t: DisplayT) -> bool: ...

AU_UNK: Final[int]
AU_CODE: Final[int]
AU_WEAK: Final[int]
AU_PROC: Final[int]
AU_TAIL: Final[int]
AU_FCHUNK: Final[int]
AU_USED: Final[int]
AU_USD2: Final[int]
AU_TYPE: Final[int]
AU_LIBF: Final[int]
AU_LBF2: Final[int]
AU_LBF3: Final[int]
AU_CHLB: Final[int]
AU_FINAL: Final[int]

def peek_auto_queue(low_ea: int, type: int) -> int: ...
def get_auto_state() -> str: ...

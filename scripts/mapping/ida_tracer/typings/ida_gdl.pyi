from collections.abc import Iterator, Sequence

import idaapi

class BasicBlock:
    start_ea: int
    end_ea: int

    def succs(self) -> Sequence[BasicBlock]: ...

class FlowChart:
    size: int

    def __init__(
        self,
        f: idaapi.func_t | None = None,
        bounds: object | None = None,
        flags: int = 0,
    ) -> None: ...
    def __iter__(self) -> Iterator[BasicBlock]: ...
    def refresh(self) -> None: ...

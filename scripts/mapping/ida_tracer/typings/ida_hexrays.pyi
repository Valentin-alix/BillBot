from collections.abc import Iterator, Sequence

CV_FAST: int

cot_ptr: int
cot_add: int
cot_num: int
cot_cast: int
cot_call: int
cot_obj: int
cit_switch: int

class DecompilationFailure(Exception): ...

class citem_t:
    op: int

class cexpr_t(citem_t):
    x: cexpr_t
    y: cexpr_t
    obj_ea: int

    def numval(self) -> int: ...

class cinsn_t(citem_t):
    cswitch: cswitch_t

class ccase_t(cinsn_t):
    values: Sequence[int]

class ccases_t:
    def __iter__(self) -> Iterator[ccase_t]: ...

class cswitch_t:
    expr: cexpr_t
    cases: ccases_t

class cline_t:
    line: str

class strvec_t:
    def __iter__(self) -> Iterator[cline_t]: ...
    def __len__(self) -> int: ...

class cfunc_t:
    body: cinsn_t

    def get_pseudocode(self) -> strvec_t: ...

class cfuncptr_t:
    body: cinsn_t

    def get_pseudocode(self) -> strvec_t: ...

class ctree_visitor_t:
    def __init__(self, flags: int) -> None: ...
    def visit_expr(self, expr: cexpr_t) -> int: ...
    def visit_insn(self, insn: cinsn_t) -> int: ...
    def apply_to(self, item: citem_t, parent: citem_t | None) -> int: ...

def decompile(ea: int) -> cfunc_t: ...

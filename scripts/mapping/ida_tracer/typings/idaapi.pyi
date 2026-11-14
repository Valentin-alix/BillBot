from collections.abc import Sequence

CF_USE1: int
CF_USE2: int
CF_USE3: int
CF_USE4: int
CF_USE5: int
CF_USE6: int
CF_USE7: int
CF_USE8: int
CF_CHG1: int
CF_CHG2: int
CF_CHG3: int
CF_CHG4: int
CF_CHG5: int
CF_CHG6: int
CF_CHG7: int
CF_CHG8: int

# Operand type constants
o_void: int  # 0 - no operand
o_reg: int  # 1 - register
o_mem: int  # 2 - direct memory
o_phrase: int  # 3 - [base + index]
o_displ: int  # 4 - [base + displacement]
o_imm: int  # 5 - immediate value
o_near: int  # 7 - near address (call/jump)
o_far: int  # 8 - far address (call/jump)

class op_t:
    type: int  # operand type (o_void, o_displ, etc.)
    addr: int  # displacement value for o_displ
    reg: int  # register number
    value: int  # immediate value for o_imm
    dtype: int | None

class func_t:
    start_ea: int
    end_ea: int

class processor_t:
    regnames: Sequence[str]

ph: processor_t

class insn_t:
    ops: Sequence[op_t]

    def get_canon_mnem(self) -> str: ...
    def get_canon_feature(self) -> int: ...

def get_dtype_size(dtype: int) -> int: ...
def get_func(ea: int) -> func_t | None: ...
def decode_insn(insn: insn_t, ea: int) -> int: ...

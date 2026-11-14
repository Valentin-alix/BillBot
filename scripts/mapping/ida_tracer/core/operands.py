import idaapi


def get_displacement_value(operand: idaapi.op_t) -> int:
    return int(operand.addr)


def is_register_operand(operand: idaapi.op_t, register_number: int) -> bool:
    return operand.type == idaapi.o_reg and operand.reg == register_number


def get_operand_access_size(operand: idaapi.op_t) -> int | None:
    dtype = operand.dtype
    if dtype is None:
        return None
    size = idaapi.get_dtype_size(dtype)
    return size if isinstance(size, int) and size > 0 else None


def read_immediate_operand_value(operand: idaapi.op_t) -> int | None:
    if operand.type != idaapi.o_imm:
        return None
    return int(operand.value)

from functools import cache
from typing import NamedTuple

import idaapi


class DecodedOperand(NamedTuple):
    type: int
    reg: int = 0
    addr: int = 0
    value: int = 0
    access_size: int | None = None


type Operand = idaapi.op_t | DecodedOperand

_VOID_OPERAND = DecodedOperand(idaapi.o_void)


@cache
def _dtype_size(dtype: int) -> int | None:
    size = idaapi.get_dtype_size(dtype)
    return size if isinstance(size, int) and size > 0 else None


def decode_operands(insn: idaapi.insn_t) -> tuple[DecodedOperand, ...]:
    operands: list[DecodedOperand] = []
    for operand in insn.ops:
        kind = operand.type
        if kind == idaapi.o_void:
            break
        operands.append(
            DecodedOperand(
                kind,
                operand.reg if kind in {idaapi.o_reg, idaapi.o_phrase, idaapi.o_displ} else 0,
                int(operand.addr)
                if kind in {idaapi.o_mem, idaapi.o_phrase, idaapi.o_displ, idaapi.o_near, idaapi.o_far}
                else 0,
                int(operand.value) if kind == idaapi.o_imm else 0,
                get_operand_access_size(operand),
            )
        )
    return tuple(operands) + (_VOID_OPERAND,) * (8 - len(operands))


def get_displacement_value(operand: Operand) -> int:
    return int(operand.addr)


def is_register_operand(operand: Operand, register_number: int) -> bool:
    return operand.type == idaapi.o_reg and operand.reg == register_number


def get_operand_access_size(operand: Operand) -> int | None:
    if isinstance(operand, DecodedOperand):
        return operand.access_size
    dtype = operand.dtype
    if dtype is None:
        return None
    return _dtype_size(dtype)


def read_immediate_operand_value(operand: Operand) -> int | None:
    if operand.type != idaapi.o_imm:
        return None
    return int(operand.value)

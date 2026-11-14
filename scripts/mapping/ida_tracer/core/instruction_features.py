from functools import cache

import idaapi

from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import AccessKind
from scripts.mapping.ida_tracer.core.mnemonics import (
    CMOV_MNEMONICS,
    READ_WRITE_DEST_MNEMONICS,
    VECTOR_MOVE_MNEMONICS,
)

_USE_FLAGS = (
    idaapi.CF_USE1,
    idaapi.CF_USE2,
    idaapi.CF_USE3,
    idaapi.CF_USE4,
    idaapi.CF_USE5,
    idaapi.CF_USE6,
    idaapi.CF_USE7,
    idaapi.CF_USE8,
)
_CHANGE_FLAGS = (
    idaapi.CF_CHG1,
    idaapi.CF_CHG2,
    idaapi.CF_CHG3,
    idaapi.CF_CHG4,
    idaapi.CF_CHG5,
    idaapi.CF_CHG6,
    idaapi.CF_CHG7,
    idaapi.CF_CHG8,
)


def get_instruction_feature(insn: idaapi.insn_t) -> int | None:
    try:
        feature = insn.get_canon_feature()
    except AttributeError, TypeError, RuntimeError:
        return None
    return feature if isinstance(feature, int) else None


@cache
def changed_operand_indices(feature: int | None) -> tuple[int, ...]:
    if feature is None:
        return ()
    return tuple(index for index, flag in enumerate(_CHANGE_FLAGS) if feature & flag)


@cache
def operand_accesses(mnemonic: str, feature: int | None) -> tuple[tuple[int, AccessKind], ...]:
    if feature is not None:
        accesses: list[tuple[int, AccessKind]] = []
        for index, (use_flag, change_flag) in enumerate(zip(_USE_FLAGS, _CHANGE_FLAGS, strict=True)):
            if feature & use_flag:
                accesses.append((index, "read"))
            if feature & change_flag:
                accesses.append((index, "write"))
        if accesses:
            return tuple(accesses)
    if mnemonic in {"mov", "movzx", "movsxd"}:
        return ((0, "write"), (1, "read")) if mnemonic == "mov" else ((1, "read"),)
    if mnemonic in {"cmp", "test"}:
        return ((0, "read"), (1, "read"))
    if mnemonic in CMOV_MNEMONICS:
        return ((1, "read"),)
    if mnemonic in READ_WRITE_DEST_MNEMONICS:
        return ((0, "read"), (0, "write"), (1, "read"))
    if mnemonic in VECTOR_MOVE_MNEMONICS:
        return ((0, "write"), (1, "read"))
    return ()

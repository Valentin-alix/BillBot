CMOV_MNEMONICS: frozenset[str] = frozenset(
    {
        "cmova",
        "cmovae",
        "cmovb",
        "cmovbe",
        "cmove",
        "cmovg",
        "cmovge",
        "cmovl",
        "cmovle",
        "cmovne",
        "cmovnz",
        "cmovns",
        "cmovnp",
        "cmovs",
        "cmovp",
        "cmovz",
    }
)
ARITHMETIC_MNEMONICS: frozenset[str] = frozenset(
    {
        "add",
        "sub",
        "xor",
        "and",
        "or",
        "shl",
        "shr",
        "sar",
        "rol",
        "ror",
        "not",
        "neg",
        "imul",
        "mul",
    }
)
VECTOR_MOVE_MNEMONICS: frozenset[str] = frozenset({"movaps", "movdqa", "movdqu", "movups"})
READ_WRITE_DEST_MNEMONICS: frozenset[str] = ARITHMETIC_MNEMONICS | frozenset(
    {"adc", "cmpxchg", "dec", "inc", "sbb", "xadd", "xchg"}
)

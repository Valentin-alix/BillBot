import sys
import types

IDA_MODULES = [
    "ida_gdl",
    "ida_nalt",
    "ida_typeinf",
    "idaapi",
    "idc",
    "ida_hexrays",
    "ida_auto",
    "ida_pro",
    "idautils",
    "ida_ua",
]

for module_name in IDA_MODULES:
    sys.modules.setdefault(module_name, types.ModuleType(module_name))

import ida_auto
import ida_gdl
import ida_hexrays
import ida_nalt
import ida_pro
import ida_typeinf
import ida_ua
import idaapi
import idautils
import idc

IDA_O_REG = 1
IDA_O_MEM = 2
IDA_O_DISPL = 4
IDA_O_IMM = 5
IDA_O_NEAR = 7
IDA_O_FAR = 8
IDA_O_PHRASE: int = 3


def _empty_flow_chart(_func: object) -> list[object]:
    return []


def _get_name(addr: int) -> str:
    return f"sub_{addr:X}"


def _get_switch_info(_ea: int) -> None:
    return None


def _get_tinfo(_tinfo: object, _ea: int) -> bool:
    return False


class MockFunc:
    def __init__(self, start_ea: int) -> None:
        self.start_ea = start_ea
        self.end_ea = start_ea + 1


def _get_func(ea: int) -> MockFunc:
    return MockFunc(ea)


def _print_insn_mnem(ea: int) -> str:
    return f"inst_{ea}"


def _heads(_start_ea: int, _end_ea: int) -> tuple[int, ...]:
    return ()


def _func_items(ea: int) -> tuple[int, ...]:
    return (ea,)


def _auto_wait() -> None:
    return None


def _qexit(_exit_code: int) -> None:
    return None


class MockTInfo:
    def dstr(self) -> str:
        return ""


def _install_idaapi_mock() -> None:
    attrs: dict[str, object] = {
        "o_void": 0,
        "o_reg": IDA_O_REG,
        "o_mem": IDA_O_MEM,
        "o_phrase": IDA_O_PHRASE,
        "o_displ": IDA_O_DISPL,
        "o_imm": IDA_O_IMM,
        "o_near": IDA_O_NEAR,
        "o_far": IDA_O_FAR,
        "op_t": object,
        "func_t": object,
        "insn_t": object,
        "get_func": _get_func,
    }

    for index in range(1, 9):
        attrs[f"CF_USE{index}"] = 1 << (index - 1)
        attrs[f"CF_CHG{index}"] = 1 << (index + 7)

    for name, value in attrs.items():
        setattr(idaapi, name, value)


def _install_ida_gdl_mock() -> None:
    setattr(ida_gdl, "FlowChart", _empty_flow_chart)


def _install_idc_mock() -> None:
    setattr(idc, "get_name", _get_name)


def _install_ida_typeinf_mock() -> None:
    setattr(ida_typeinf, "tinfo_t", MockTInfo)


def _install_ida_nalt_mock() -> None:
    setattr(ida_nalt, "get_switch_info", _get_switch_info)
    setattr(ida_nalt, "get_tinfo", _get_tinfo)


def _install_ida_ua_mock() -> None:
    setattr(ida_ua, "print_insn_mnem", _print_insn_mnem)


def _install_idautils_mock() -> None:
    setattr(idautils, "Heads", _heads)
    setattr(idautils, "FuncItems", _func_items)


def _install_ida_auto_mock() -> None:
    setattr(ida_auto, "auto_wait", _auto_wait)


def _install_ida_pro_mock() -> None:
    setattr(ida_pro, "qexit", _qexit)


class _MockCtreeVisitorBase:
    def __init__(self, _flags: int) -> None:
        pass

    def apply_to(self, _item: object, _parent: object) -> int:
        return 0


def _install_ida_hexrays_mock() -> None:
    constants: dict[str, int] = {
        "CV_FAST": 0,
        "cot_ptr": 1,
        "cot_add": 2,
        "cot_num": 3,
        "cot_cast": 4,
        "cot_call": 5,
        "cot_obj": 6,
        "cit_switch": 7,
    }
    for name, value in constants.items():
        setattr(ida_hexrays, name, value)
    setattr(ida_hexrays, "ctree_visitor_t", _MockCtreeVisitorBase)


def install_mock_ida_environment() -> None:
    _install_idaapi_mock()
    _install_ida_gdl_mock()
    _install_idc_mock()
    _install_ida_typeinf_mock()
    _install_ida_nalt_mock()
    _install_ida_ua_mock()
    _install_idautils_mock()
    _install_ida_auto_mock()
    _install_ida_pro_mock()
    _install_ida_hexrays_mock()

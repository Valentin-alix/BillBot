import re
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

import ida_hexrays
import ida_nalt
import ida_typeinf
import idaapi
import idc

from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import (
    EnumFunctionResolvedMetadata,
    TracedFunction,
    format_trace_address,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.enum_mapping import (
    EnumCalledFunctionRef,
    EnumMemberGroup,
    EnumResolutionKind,
    EnumSignatureEntry,
    EnumSwitchPattern,
    EnumTypeKind,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.il2cpp_json import Il2CppApiDefinition, MethodDefinition
from DBDofusUnity.proto_mapper_assembly.parsers._clr_type_utils import split_top_level_tokens
from scripts.mapping.ida_tracer.lookups.name_resolution import (
    resolve_message_type_name,
    resolve_owner_class_name,
)
from scripts.mapping.ida_tracer.progress.reporter import ProgressReporter
from scripts.mapping.ida_tracer.signatures.parser import (
    get_preferred_signature,
    is_method_info_parameter,
    is_native_this_parameter,
    parse_dot_net_signature,
)
from scripts.mapping.ida_tracer.simulation.cfg_stats import (
    opcode_histogram_for_func,
)
from scripts.mapping.ida_tracer.simulation.scan_engine import build_function_scan_plan
from scripts.mapping.ida_tracer.state.basic_block import (
    DecodedInstruction,
    FunctionScanCache,
)

_MAX_FIELD_OFFSET = 0x1000
_IDA_CALLING_CONVENTIONS: frozenset[str] = frozenset(
    {
        "__cdecl",
        "__clrcall",
        "__fastcall",
        "__stdcall",
        "__thiscall",
        "__vectorcall",
    }
)
_COMMENT_RE = re.compile(r"/\*.*?\*/")
_NAMED_SIGNATURE_RE = re.compile(r"^(?P<return>.+?)\s+(?P<name>[^\s(]+)\((?P<params>.*)\)$")
_NATIVE_PARAMETER_NAME_RE = re.compile(r"\s+[A-Za-z_][A-Za-z0-9_]*$")
_INTEGER_TYPE_TOKENS: tuple[str, ...] = (
    "byte",
    "char",
    "dword",
    "int",
    "long",
    "qword",
    "short",
    "size_t",
    "sbyte",
    "uint",
    "ulong",
    "ushort",
    "word",
)


@dataclass(frozen=True, slots=True)
class _ResolvedFunctionMetadata:
    function_addr: int
    resolution_kind: EnumResolutionKind
    function_size: int
    raw_name: str | None
    raw_signature: str | None
    return_type: str | None
    parameter_types: tuple[str, ...]
    return_kind: EnumTypeKind
    parameter_kinds: tuple[EnumTypeKind, ...]


@dataclass(frozen=True, slots=True)
class EnumTraceResult:
    enum_signatures_by_name: dict[str, EnumSignatureEntry]
    functions_by_address: dict[str, TracedFunction]


class _EnumFunctionResolver:
    def __init__(
        self,
        *,
        methods: Sequence[MethodDefinition],
        apis: Sequence[Il2CppApiDefinition],
        exports: Sequence[Il2CppApiDefinition],
    ) -> None:
        self._method_by_addr: dict[int, MethodDefinition] = {
            int(method.virtual_address, 16): method for method in methods
        }
        self._api_by_addr: dict[int, Il2CppApiDefinition] = {
            int(api.virtual_address, 16): api for api in apis
        }
        self._export_by_addr: dict[int, Il2CppApiDefinition] = {
            int(export.virtual_address, 16): export for export in exports
        }
        self._metadata_by_addr: dict[int, _ResolvedFunctionMetadata] = {}

    def describe_call_target(self, call_target_addr: int) -> EnumCalledFunctionRef:
        function_addr = _normalize_called_function_addr(call_target_addr)
        self._resolve_function_metadata(function_addr)
        return EnumCalledFunctionRef(
            function_address=format_trace_address(function_addr),
            call_target_address=format_trace_address(call_target_addr),
            occurrence_count=1,
        )

    def _resolve_function_metadata(
        self,
        function_addr: int,
        *,
        preferred_method: MethodDefinition | None = None,
        preferred_cfunc: ida_hexrays.cfunc_t | None = None,
    ) -> _ResolvedFunctionMetadata:
        cached_metadata = self._metadata_by_addr.get(function_addr)
        if cached_metadata is not None:
            return cached_metadata

        function_size = _get_function_size(function_addr)
        raw_name = _safe_get_name(function_addr)

        method = preferred_method or self._method_by_addr.get(function_addr)
        if method is not None:
            raw_signature = get_preferred_signature(method) or method.signature
            metadata = _build_metadata(
                function_addr=function_addr,
                resolution_kind="method_definition",
                function_size=function_size,
                raw_name=method.name,
                raw_signature=raw_signature,
            )
            self._metadata_by_addr[function_addr] = metadata
            return metadata

        api = self._api_by_addr.get(function_addr)
        if api is not None:
            metadata = _build_metadata(
                function_addr=function_addr,
                resolution_kind="api",
                function_size=function_size,
                raw_name=api.name,
                raw_signature=api.signature,
            )
            self._metadata_by_addr[function_addr] = metadata
            return metadata

        export = self._export_by_addr.get(function_addr)
        if export is not None:
            metadata = _build_metadata(
                function_addr=function_addr,
                resolution_kind="export",
                function_size=function_size,
                raw_name=export.name,
                raw_signature=export.signature,
            )
            self._metadata_by_addr[function_addr] = metadata
            return metadata

        ida_type_signature = _get_ida_type_signature(function_addr)
        if ida_type_signature is not None:
            metadata = _build_metadata(
                function_addr=function_addr,
                resolution_kind="ida_type",
                function_size=function_size,
                raw_name=raw_name,
                raw_signature=ida_type_signature,
            )
            self._metadata_by_addr[function_addr] = metadata
            return metadata

        decompiled_signature = _get_decompiled_signature(function_addr, preferred_cfunc=preferred_cfunc)
        if decompiled_signature is not None:
            metadata = _build_metadata(
                function_addr=function_addr,
                resolution_kind="decompiled",
                function_size=function_size,
                raw_name=raw_name,
                raw_signature=decompiled_signature,
            )
            self._metadata_by_addr[function_addr] = metadata
            return metadata

        metadata = _build_metadata(
            function_addr=function_addr,
            resolution_kind="unknown",
            function_size=function_size,
            raw_name=raw_name,
            raw_signature=None,
        )
        self._metadata_by_addr[function_addr] = metadata
        return metadata

    def build_traced_functions(
        self, function_scan_cache: FunctionScanCache | None
    ) -> dict[str, TracedFunction]:
        return {
            format_trace_address(function_addr): _build_traced_function_from_metadata(
                metadata,
                function_scan_cache=function_scan_cache,
            )
            for function_addr, metadata in self._metadata_by_addr.items()
        }


def scan_methods_for_enum_switches(
    func_addrs: set[int],
    class_candidates_by_ea: dict[int, list[str]],
    enum_value_by_offset_by_name: dict[str, dict[int, str]],
    enum_value_by_member_name_by_name: dict[str, dict[str, int]],
    methods: Sequence[MethodDefinition],
    apis: Sequence[Il2CppApiDefinition],
    exports: Sequence[Il2CppApiDefinition],
    progress_reporter: ProgressReporter,
    function_scan_cache: FunctionScanCache | None = None,
) -> EnumTraceResult:
    patterns_by_enum: dict[str, list[EnumSwitchPattern]] = {}
    resolver = _EnumFunctionResolver(methods=methods, apis=apis, exports=exports)
    total_functions = len(func_addrs)
    progress_reporter.start(phase="scan_enum_switches", total=total_functions, label="Scan enum switches")

    for index, func_ea in enumerate(func_addrs):
        current = index + 1
        if (current % 25) == 0 or current == total_functions:
            progress_reporter.update(
                phase="scan_enum_switches",
                current=current,
                total=total_functions,
                label="Scan enum switches",
            )
        candidates = class_candidates_by_ea.get(func_ea)
        if not candidates:
            continue
        try:
            cfunc = ida_hexrays.decompile(func_ea)
        except ida_hexrays.DecompilationFailure:
            continue
        for enum_name, pattern in _extract_switch_patterns(
            func_ea,
            cfunc,
            class_candidates=candidates,
            enum_value_by_offset_by_name=enum_value_by_offset_by_name,
            enum_value_by_member_name_by_name=enum_value_by_member_name_by_name,
            resolver=resolver,
        ):
            patterns_by_enum.setdefault(enum_name, []).append(pattern)

    print(f"[enum_tracer] found {len(patterns_by_enum)} enum(s) with switches")
    progress_reporter.finish(phase="scan_enum_switches", total=total_functions, label="Scan enum switches")

    result: dict[str, EnumSignatureEntry] = {}
    for enum_name, patterns in patterns_by_enum.items():
        members = enum_value_by_member_name_by_name.get(enum_name)
        if members is None:
            continue
        result[enum_name] = EnumSignatureEntry(
            member_value_to_name={str(val): name for name, val in members.items()}, switch_patterns=patterns
        )
    return EnumTraceResult(
        enum_signatures_by_name=result,
        functions_by_address=resolver.build_traced_functions(function_scan_cache),
    )


def find_enum_switch_func_addrs(
    methods: list[MethodDefinition],
    enum_field_offsets: set[int],
    function_scan_cache: FunctionScanCache | None = None,
) -> set[int]:
    def has_enum_field_access(decoded: DecodedInstruction) -> bool:
        return any(op.type == idaapi.o_displ and op.addr in enum_field_offsets for op in decoded.insn.ops)

    result: set[int] = set()
    for method in methods:
        method_va = int(method.virtual_address, 16)
        func = idaapi.get_func(method_va)
        if func is None:
            continue
        recent: deque[DecodedInstruction] = deque(maxlen=6)
        scan_plan = build_function_scan_plan(func, function_scan_cache)
        decoded_instructions = sorted(
            (
                decoded
                for instructions in scan_plan.instructions_by_block.values()
                for decoded in instructions
            ),
            key=lambda decoded: decoded.ea,
        )
        for decoded in decoded_instructions:
            if ida_nalt.get_switch_info(decoded.ea) is not None and any(
                has_enum_field_access(previous_decoded) for previous_decoded in recent
            ):
                result.add(method_va)
                break

            recent.append(decoded)
    return result


def _extract_switch_patterns(
    func_ea: int,
    cfunc: ida_hexrays.cfunc_t,
    *,
    class_candidates: list[str],
    enum_value_by_offset_by_name: dict[str, dict[int, str]],
    enum_value_by_member_name_by_name: dict[str, dict[str, int]],
    resolver: _EnumFunctionResolver,
) -> list[tuple[str, EnumSwitchPattern]]:
    results: list[tuple[str, EnumSwitchPattern]] = []

    class _SwitchFinder(ida_hexrays.ctree_visitor_t):
        def __init__(self) -> None:
            super().__init__(ida_hexrays.CV_FAST)

        def visit_insn(self, insn: ida_hexrays.cinsn_t) -> int:
            if insn.op != ida_hexrays.cit_switch:
                return 0

            field_offset = _extract_field_offset(insn.cswitch.expr)
            if field_offset is None:
                return 0

            all_case_values: set[int] = set()
            for case in insn.cswitch.cases:
                all_case_values.update(case.values)

            matching_enum_names: list[str] = []
            for class_name in class_candidates:
                enum_name = enum_value_by_offset_by_name.get(class_name, {}).get(field_offset)
                if enum_name is None:
                    continue
                declared_values = set(enum_value_by_member_name_by_name[enum_name].values())
                if not all_case_values <= declared_values:
                    continue
                matching_enum_names.append(enum_name)
            if len(matching_enum_names) != 1:
                return 0

            resolved_enum_name = matching_enum_names[0]

            member_groups: list[EnumMemberGroup] = []
            for case in insn.cswitch.cases:
                member_values = list(case.values)
                member_groups.append(
                    EnumMemberGroup(
                        member_values=member_values,
                        is_default=not member_values,
                        called_functions=_build_called_function_descriptors(
                            _collect_called_function_targets(case),
                            resolver=resolver,
                        ),
                    )
                )

            pattern = EnumSwitchPattern(
                function_addr=func_ea,
                field_offset=field_offset,
                member_groups=member_groups,
            )

            results.append((resolved_enum_name, pattern))

            return 0

    if cfunc.body is not None:
        _SwitchFinder().apply_to(cfunc.body, None)

    return results


def _extract_field_offset(expr: ida_hexrays.cexpr_t) -> int | None:
    if expr.op != ida_hexrays.cot_ptr:
        return None
    inner = expr.x
    while inner.op == ida_hexrays.cot_cast:
        inner = inner.x
    if inner.op != ida_hexrays.cot_add:
        return None
    if inner.y.op == ida_hexrays.cot_num:
        offset = inner.y.numval()
    elif inner.x.op == ida_hexrays.cot_num:
        offset = inner.x.numval()
    else:
        return None
    return int(offset) if offset < _MAX_FIELD_OFFSET else None


def build_class_candidates_by_ea(
    methods: Sequence[MethodDefinition],
    proto_message_type_lookup: dict[str, str],
) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    for method in methods:
        candidates: list[str] = []
        owner = resolve_owner_class_name(method, proto_message_type_lookup)
        if owner is not None:
            candidates.append(owner)
        _, parameter_types = parse_dot_net_signature(get_preferred_signature(method))
        for parameter_type in parameter_types:
            resolved = resolve_message_type_name(parameter_type, proto_message_type_lookup)
            if resolved is None or resolved in candidates:
                continue
            candidates.append(resolved)
        if candidates:
            result[int(method.virtual_address, 16)] = candidates
    return result


def _collect_called_function_targets(insn: ida_hexrays.cinsn_t) -> list[int]:
    call_targets: list[int] = []

    class _Collector(ida_hexrays.ctree_visitor_t):
        def __init__(self) -> None:
            super().__init__(ida_hexrays.CV_FAST)

        def visit_expr(self, expr: ida_hexrays.cexpr_t) -> int:
            if expr.op == ida_hexrays.cot_call and expr.x.op == ida_hexrays.cot_obj:
                call_targets.append(int(expr.x.obj_ea))
            return 0

    collector = _Collector()
    collector.apply_to(insn, None)
    return call_targets


def _build_called_function_descriptors(
    call_target_addrs: Sequence[int],
    *,
    resolver: _EnumFunctionResolver,
) -> list[EnumCalledFunctionRef]:
    grouped_targets: dict[int, list[int]] = {}
    for call_target_addr in call_target_addrs:
        function_addr = _normalize_called_function_addr(call_target_addr)
        grouped_targets.setdefault(function_addr, []).append(call_target_addr)

    descriptors: list[EnumCalledFunctionRef] = []
    for function_addr, grouped_call_targets in grouped_targets.items():
        descriptor = resolver.describe_call_target(grouped_call_targets[0])
        call_target_addr = grouped_call_targets[0] if len(set(grouped_call_targets)) == 1 else function_addr
        descriptors.append(
            descriptor.model_copy(
                update={
                    "call_target_address": format_trace_address(call_target_addr),
                    "function_address": format_trace_address(function_addr),
                    "occurrence_count": len(grouped_call_targets),
                }
            )
        )
    return descriptors


def _build_traced_function_from_metadata(
    metadata: _ResolvedFunctionMetadata,
    *,
    function_scan_cache: FunctionScanCache | None,
) -> TracedFunction:
    func = idaapi.get_func(metadata.function_addr)
    assert func is not None, f"Missing IDA function for enum descriptor target 0x{metadata.function_addr:X}"
    return TracedFunction(
        start_address=metadata.function_addr,
        end_address=int(func.end_ea),
        size=metadata.function_size,
        access_infos=[],
        opcode_histogram=opcode_histogram_for_func(func, function_scan_cache),
        aliases=[],
        # Enum-switch discoveries lack proto-scan aliases and must not become message signatures.
        stable_callees=[],
        cfg_stats=None,
        resolved_metadata=EnumFunctionResolvedMetadata(
            resolution_kind=metadata.resolution_kind,
            raw_name=metadata.raw_name,
            raw_signature=metadata.raw_signature,
            return_type=metadata.return_type,
            parameter_types=list(metadata.parameter_types),
            return_kind=metadata.return_kind,
            parameter_kinds=list(metadata.parameter_kinds),
        ),
    )


def _build_metadata(
    *,
    function_addr: int,
    resolution_kind: EnumResolutionKind,
    function_size: int,
    raw_name: str | None,
    raw_signature: str | None,
) -> _ResolvedFunctionMetadata:
    return_type, parameter_types = _parse_descriptor_signature(raw_signature)
    return _ResolvedFunctionMetadata(
        function_addr=function_addr,
        resolution_kind=resolution_kind,
        function_size=function_size,
        raw_name=raw_name,
        raw_signature=raw_signature,
        return_type=return_type,
        parameter_types=tuple(parameter_types),
        return_kind=_categorize_type(return_type),
        parameter_kinds=tuple(_categorize_type(parameter_type) for parameter_type in parameter_types),
    )


def _normalize_called_function_addr(call_target_addr: int) -> int:
    func = idaapi.get_func(call_target_addr)
    if func is None:
        return call_target_addr
    return int(func.start_ea)


def _get_function_size(function_addr: int) -> int:
    func = idaapi.get_func(function_addr)
    assert func is not None, f"Missing IDA function for enum descriptor target 0x{function_addr:X}"
    return int(func.end_ea - func.start_ea)


def _safe_get_name(function_addr: int) -> str | None:
    raw_name = str(idc.get_name(function_addr)).strip()
    return raw_name or None


def _get_ida_type_signature(function_addr: int) -> str | None:
    tinfo = ida_typeinf.tinfo_t()
    if not ida_nalt.get_tinfo(tinfo, function_addr):
        return None
    raw_signature = str(tinfo.dstr()).strip()
    return raw_signature or None


def _get_decompiled_signature(
    function_addr: int,
    *,
    preferred_cfunc: ida_hexrays.cfunc_t | None = None,
) -> str | None:
    cfunc = preferred_cfunc
    if cfunc is None:
        try:
            cfunc = ida_hexrays.decompile(function_addr)
        except ida_hexrays.DecompilationFailure:
            return None
    pseudocode_lines = cfunc.get_pseudocode()
    signature_parts: list[str] = []
    for line in pseudocode_lines:
        cleaned_line = _COMMENT_RE.sub("", line.line).strip()
        if not cleaned_line:
            continue
        if cleaned_line == "{":
            break
        signature_parts.append(cleaned_line.removesuffix("{").strip())
        if cleaned_line.endswith(("{", ")")):
            break
    signature = " ".join(part for part in signature_parts if part).strip()
    return signature or None


def _parse_descriptor_signature(raw_signature: str | None) -> tuple[str | None, list[str]]:
    if raw_signature is None:
        return None, []
    stripped_signature = raw_signature.strip()
    if not stripped_signature or "(" not in stripped_signature or ")" not in stripped_signature:
        return None, []
    native_signature = _parse_named_signature_preserving_pointers(stripped_signature)
    if native_signature is not None:
        return native_signature
    return_type, parameter_types = parse_dot_net_signature(stripped_signature)
    normalized_return_type = _normalize_signature_type(return_type)
    normalized_parameter_types = [
        normalized_parameter_type
        for parameter_type in parameter_types
        if (normalized_parameter_type := _normalize_signature_type(parameter_type)) is not None
    ]
    return normalized_return_type, normalized_parameter_types


def _parse_named_signature_preserving_pointers(raw_signature: str) -> tuple[str | None, list[str]] | None:
    matched_signature = _NAMED_SIGNATURE_RE.match(raw_signature)
    if matched_signature is None:
        return None
    return_type = _normalize_signature_type(matched_signature.group("return"))
    parameter_entries = [
        raw_parameter.strip()
        for raw_parameter in split_top_level_tokens(matched_signature.group("params"))
        if raw_parameter.strip()
    ]
    if parameter_entries and is_native_this_parameter(parameter_entries[0]):
        parameter_entries = parameter_entries[1:]
    if parameter_entries and is_method_info_parameter(parameter_entries[-1]):
        parameter_entries = parameter_entries[:-1]
    parameter_types: list[str] = []
    for parameter_entry in parameter_entries:
        parameter_type = _normalize_native_parameter_type(parameter_entry)
        if parameter_type is None:
            continue
        parameter_types.append(parameter_type)
    return return_type, parameter_types


def _normalize_native_parameter_type(parameter: str) -> str | None:
    parameter_without_name = _NATIVE_PARAMETER_NAME_RE.sub("", parameter.strip()).strip()
    return _normalize_signature_type(parameter_without_name)


def _normalize_signature_type(type_name: str | None) -> str | None:
    if type_name is None:
        return None
    stripped_type = type_name.strip()
    if not stripped_type:
        return None
    normalized_tokens = [
        token for token in re.split(r"\s+", stripped_type) if token and token not in _IDA_CALLING_CONVENTIONS
    ]
    if not normalized_tokens:
        return None
    normalized_type = " ".join(normalized_tokens)
    normalized_type = normalized_type.replace(" &", "&").replace("& ", "&").strip()
    return normalized_type or None


def _categorize_type(type_name: str | None) -> EnumTypeKind:
    if type_name is None:
        return "unknown"
    normalized_type = type_name.strip().lower()
    if not normalized_type:
        return "unknown"
    if normalized_type == "void":
        return "void"
    if "descriptor" in normalized_type:
        return "descriptor_like"
    if "bool" in normalized_type:
        return "bool"
    if "string" in normalized_type or "char*" in normalized_type or "char *" in normalized_type:
        return "string_like"
    if "float" in normalized_type or "double" in normalized_type or "single" in normalized_type:
        return "float_like"
    if "(*)" in normalized_type or "__fastcall *" in normalized_type or "(*" in normalized_type:
        return "function_pointer"
    if "message" in normalized_type or "imessage" in normalized_type or "parser" in normalized_type:
        return "message_like"
    if "object" in normalized_type or normalized_type == "_qword":
        return "pointer_like"
    if "*" in normalized_type or "intptr" in normalized_type or "uintptr" in normalized_type:
        return "pointer_like"
    if any(token in normalized_type for token in _INTEGER_TYPE_TOKENS):
        return "int_like"
    return "unknown"

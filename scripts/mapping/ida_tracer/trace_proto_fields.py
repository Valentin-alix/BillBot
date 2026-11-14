#!/usr/bin/env python3

import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import ida_pro
import idaapi

from DBDofusUnity.consts import NON_OBFUSCATED_DATA_DIR
from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import (
    AccessTraceDocument,
    FunctionAccessInfo,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import (
    DumpCSMessageField,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.il2cpp_json import Il2CppJson, MethodDefinition
from DBDofusUnity.proto_mapper_assembly.parsers._dump_cs_structure import parse_enum_member_values
from DBDofusUnity.proto_mapper_assembly.parsers.csharp_signature_utils import (
    CORE_METHOD_DECLARATION_RE as _CORE_METHOD_DECLARATION_RE,
)
from DBDofusUnity.proto_mapper_assembly.parsers.csharp_signature_utils import (
    canonicalize_csharp_method_declaration as _canonicalize_csharp_method_declaration,
)
from DBDofusUnity.proto_mapper_assembly.parsers.dump_cs_parser import load_tracking_types, parse_messages
from scripts.mapping.ida_tracer.analysis import auto_wait_analysis
from scripts.mapping.ida_tracer.core.function_inspector import cache_function_inspection
from scripts.mapping.ida_tracer.lookups.accessor_candidate import (
    AccessorCandidate,
    build_getter_setter_lookup,
)
from scripts.mapping.ida_tracer.lookups.field_offsets import (
    build_enum_field_by_class_offset,
    build_field_offset_lookup,
    build_tracking_field_offset_lookup,
)
from scripts.mapping.ida_tracer.lookups.il2cpp import (
    HandlerMethodInfo,
    build_filter_typeinfo_lookup,
    build_handler_methodinfo_lookup,
    build_ienumerator_typeinfo_lookup,
    build_methodinfo_get_enumerator_lookup,
)
from scripts.mapping.ida_tracer.lookups.name_resolution import (
    resolve_message_type_name,
    resolve_owner_class_name,
)
from scripts.mapping.ida_tracer.lookups.stable_symbols import (
    build_callee_identity_lookup,
)
from scripts.mapping.ida_tracer.lookups.type_lookup import (
    build_long_name_by_alias,
    build_long_name_by_unique_alias,
)
from scripts.mapping.ida_tracer.progress.reporter import ProgressReporter
from scripts.mapping.ida_tracer.signatures.parser import (
    build_function_key,
    canonicalize_function_signature_types,
    extract_proto_parameter_seeds,
    method_has_this_parameter,
    select_signature_for_proto_tracking,
)
from scripts.mapping.ida_tracer.simulation.cfg_stats import (
    cfg_stats_for_func,
    opcode_histogram_for_func,
)
from scripts.mapping.ida_tracer.simulation.constants import MAX_INTERPROCEDURAL_DEPTH
from scripts.mapping.ida_tracer.simulation.scan_engine import scan_function_instructions
from scripts.mapping.ida_tracer.state.basic_block import FunctionScanCache
from scripts.mapping.ida_tracer.state.builders import (
    build_initial_frame_state,
    build_initial_heap_state,
    build_initial_register_state,
    build_initial_stack_state,
)
from scripts.mapping.ida_tracer.state.interproc import InterproceduralContext
from scripts.mapping.ida_tracer.tracing.enum_tracer import (
    build_class_candidates_by_ea,
    find_enum_switch_func_addrs,
    scan_methods_for_enum_switches,
)
from scripts.mapping.ida_tracer.tracing.handler_registration import (
    collect_handler_registration_accesses,
)
from scripts.mapping.ida_tracer.tracing.stable_callees import collect_stable_callees
from scripts.mapping.ida_tracer.tracing.trace_export import (
    build_traced_functions_from_proto_infos,
    merge_traced_functions,
)


@dataclass(frozen=True)
class TracerConfig:
    base_dir: Path
    output_path: Path


def load_tracer_config(base_dir: Path) -> TracerConfig:
    output_path = base_dir / "proto_accesses.json"
    return TracerConfig(base_dir=base_dir, output_path=output_path)


def select_methods_for_scanning(methods: list[MethodDefinition]) -> list[MethodDefinition]:
    return [method for method in methods if method.group.startswith("Core.dll")]


def build_core_method_signature_lookup(base_dir: Path) -> dict[int, str]:
    core_cs_path = base_dir / "cs" / "Core.cs"
    if not core_cs_path.exists():
        raise FileNotFoundError(str(core_cs_path))
    result: dict[int, str] = {}
    for raw_line in core_cs_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        matched = _CORE_METHOD_DECLARATION_RE.match(raw_line)
        if matched is None:
            continue
        signature = _canonicalize_csharp_method_declaration(matched.group("declaration"))
        if signature is None:
            continue
        result[int(matched.group("start"), 16)] = signature
    return result


def scan_methods_for_accesses(
    methods: list[MethodDefinition],
    proto_message_type_lookup: dict[str, str],
    proto_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    tracking_type_lookup: dict[str, str],
    tracking_fields_by_class_and_offset: dict[str, dict[int, DumpCSMessageField]],
    getter_setter_lookup: dict[int, list[AccessorCandidate]],
    typeinfo_lookup: dict[int, str],
    filter_typeinfo_lookup: dict[int, str],
    handler_methodinfo_lookup: dict[int, HandlerMethodInfo],
    methodinfo_get_enumerator_lookup: dict[int, str],
    ienumerator_typeinfo_lookup: dict[int, str],
    callee_identity_lookup: dict[int, str],
    progress_reporter: ProgressReporter,
    proto_long_name_by_alias: dict[str, frozenset[str]],
    core_method_signature_lookup: dict[int, str],
    function_scan_cache: FunctionScanCache,
    do_canonicalize_to_long_name: bool,
) -> dict[str, FunctionAccessInfo]:
    result: dict[str, FunctionAccessInfo] = {}
    resolved_core_method_signature_lookup = core_method_signature_lookup or {}
    combined_type_lookup = {**tracking_type_lookup, **proto_message_type_lookup}
    combined_tracking_fields_by_class_and_offset = {
        **tracking_fields_by_class_and_offset,
        **proto_fields_by_class_and_offset,
    }
    resolved_function_scan_cache = function_scan_cache if function_scan_cache is not None else {}
    method_address_lookup = {
        address: method_info.cls for address, method_info in handler_methodinfo_lookup.items()
    }
    interprocedural_context = InterproceduralContext(
        depth=0,
        max_depth=MAX_INTERPROCEDURAL_DEPTH,
        cache={},
        active_keys=set(),
    )

    print("Start scan methods")
    total_methods = len(methods)
    progress_reporter.start(phase="scan_methods", total=total_methods, label="Scan methods")

    for index, method in enumerate(methods):
        current = index + 1
        if (current % 100) == 0 or current == total_methods:
            progress_reporter.update(
                phase="scan_methods",
                current=current,
                total=total_methods,
                label="Scan methods",
            )
        method_va = int(method.virtual_address, 16)
        selected_signature = select_signature_for_proto_tracking(
            method,
            proto_message_type_lookup,
            fallback_signature=resolved_core_method_signature_lookup.get(method_va),
        )
        proto_parameter_seeds = extract_proto_parameter_seeds(
            method,
            proto_message_type_lookup,
            preferred_signature=selected_signature,
        )
        has_this = method_has_this_parameter(method)
        func_key = build_function_key(method, preferred_signature=selected_signature)
        return_type, parameters = canonicalize_function_signature_types(
            method,
            selected_signature,
            proto_long_name_by_alias,
            do_canonicalize_to_long_name=do_canonicalize_to_long_name,
        )

        func = idaapi.get_func(method_va)
        if func is None:
            continue

        owner_class_name = resolve_owner_class_name(method, tracking_type_lookup) if has_this else None
        initial_reg_state = build_initial_register_state(
            proto_parameter_seeds,
            has_this=has_this,
            this_class_name=owner_class_name,
        )
        initial_frame_state = build_initial_frame_state()
        initial_stack_state = build_initial_stack_state(
            proto_parameter_seeds,
            has_this=has_this,
        )
        initial_heap_state = build_initial_heap_state()
        entries = scan_function_instructions(
            func,
            initial_reg_state,
            initial_frame_state,
            initial_stack_state,
            proto_fields_by_class_and_offset,
            combined_tracking_fields_by_class_and_offset,
            combined_type_lookup,
            getter_setter_lookup,
            typeinfo_lookup,
            methodinfo_get_enumerator_lookup,
            ienumerator_typeinfo_lookup,
            initial_heap_state=initial_heap_state,
            interprocedural_context=interprocedural_context,
            function_scan_cache=resolved_function_scan_cache,
        )
        entries.extend(
            collect_handler_registration_accesses(
                func,
                filter_typeinfo_lookup,
                handler_methodinfo_lookup,
                method_address_lookup,
                resolved_function_scan_cache,
            )
        )
        if not entries:
            continue
        result[func_key] = FunctionAccessInfo(
            name=func_key,
            parameters=parameters,
            return_type=return_type,
            access_infos=entries,
            start_address=func.start_ea,
            end_address=func.end_ea,
            size=func.end_ea - func.start_ea,
            group=method.group,
            opcode_histogram=opcode_histogram_for_func(func, resolved_function_scan_cache),
            stable_callees=collect_stable_callees(func, callee_identity_lookup, resolved_function_scan_cache),
            cfg_stats=cfg_stats_for_func(func, resolved_function_scan_cache),
        )
    progress_reporter.finish(phase="scan_methods", total=total_methods, label="Scan methods")
    return result


@cache_function_inspection()
def main() -> None:
    progress_reporter = ProgressReporter.from_environment()
    auto_wait_analysis(progress_reporter)

    configured_base_dir = os.environ.get("PROTO_TRACER_BASE_DIR", str(NON_OBFUSCATED_DATA_DIR))
    config = load_tracer_config(Path(configured_base_dir))
    base_dir = config.base_dir

    il2cpp = Il2CppJson.model_validate_json(Path(base_dir / "il2cpp.json").read_text(encoding="utf-8"))
    selected_methods = select_methods_for_scanning(il2cpp.address_map.method_definitions)

    proto_messages = parse_messages(str(base_dir / "cs" / "Ankama.Dofus.Protocol.Game.cs"))
    print("Parsed msg")
    tracking_types = load_tracking_types(base_dir)
    print("Loaded tracking type")
    proto_message_type_lookup = build_long_name_by_unique_alias(proto_messages)
    proto_long_name_by_alias = build_long_name_by_alias(proto_messages)
    tracking_type_lookup = build_long_name_by_unique_alias(tracking_types)
    proto_fields_by_class_and_offset = build_field_offset_lookup(proto_messages)
    tracking_fields_by_class_and_offset = build_tracking_field_offset_lookup(tracking_types)
    getter_setter_lookup = build_getter_setter_lookup(proto_messages)
    core_method_signature_lookup = build_core_method_signature_lookup(base_dir)
    typeinfo_lookup: dict[int, str] = {
        int(tip.virtual_address, 16): resolved_type_name
        for tip in il2cpp.address_map.type_info_pointers
        if (
            (
                resolved_type_name := resolve_message_type_name(
                    tip.dot_net_type,
                    proto_message_type_lookup,
                )
            )
            is not None
        )
    }
    methodinfo_get_enumerator_lookup = build_methodinfo_get_enumerator_lookup(
        il2cpp.address_map.method_info_pointers,
        proto_message_type_lookup,
    )
    filter_typeinfo_lookup = build_filter_typeinfo_lookup(
        il2cpp.address_map.type_info_pointers,
        proto_message_type_lookup,
    )
    handler_methodinfo_lookup = build_handler_methodinfo_lookup(
        il2cpp.address_map.method_info_pointers,
        proto_message_type_lookup,
    )
    ienumerator_typeinfo_lookup = build_ienumerator_typeinfo_lookup(
        il2cpp.address_map.type_info_pointers,
        proto_message_type_lookup,
    )
    callee_identity_lookup = build_callee_identity_lookup(il2cpp.address_map.method_definitions)
    print("Builded lookup")
    function_scan_cache: FunctionScanCache = {}

    result = scan_methods_for_accesses(
        methods=selected_methods,
        proto_message_type_lookup=proto_message_type_lookup,
        proto_fields_by_class_and_offset=proto_fields_by_class_and_offset,
        tracking_type_lookup=tracking_type_lookup,
        tracking_fields_by_class_and_offset=tracking_fields_by_class_and_offset,
        getter_setter_lookup=getter_setter_lookup,
        typeinfo_lookup=typeinfo_lookup,
        filter_typeinfo_lookup=filter_typeinfo_lookup,
        handler_methodinfo_lookup=handler_methodinfo_lookup,
        methodinfo_get_enumerator_lookup=methodinfo_get_enumerator_lookup,
        ienumerator_typeinfo_lookup=ienumerator_typeinfo_lookup,
        callee_identity_lookup=callee_identity_lookup,
        progress_reporter=progress_reporter,
        core_method_signature_lookup=core_method_signature_lookup,
        function_scan_cache=function_scan_cache,
        proto_long_name_by_alias=proto_long_name_by_alias,
        do_canonicalize_to_long_name=base_dir.name == "non_obf",
    )
    print("Building enums accesses")
    dump_cs_code = Path(base_dir / "cs" / "Ankama.Dofus.Protocol.Game.cs").read_text(
        encoding="utf-8", errors="ignore"
    )
    enum_value_by_member_name_by_name = parse_enum_member_values(dump_cs_code)
    enum_value_by_offset_by_name = build_enum_field_by_class_offset(proto_messages)
    enum_field_offsets = {
        offset for offsets_by_offset in enum_value_by_offset_by_name.values() for offset in offsets_by_offset
    }
    class_candidates_by_ea = build_class_candidates_by_ea(
        il2cpp.address_map.method_definitions, proto_message_type_lookup
    )
    enum_accessed_func_addrs = find_enum_switch_func_addrs(
        selected_methods,
        enum_field_offsets,
        function_scan_cache,
    )
    enum_result = scan_methods_for_enum_switches(
        enum_accessed_func_addrs,
        class_candidates_by_ea,
        enum_value_by_offset_by_name,
        enum_value_by_member_name_by_name,
        il2cpp.address_map.method_definitions,
        il2cpp.address_map.apis,
        il2cpp.address_map.exports,
        progress_reporter=progress_reporter,
        function_scan_cache=function_scan_cache,
    )
    config.output_path.write_text(
        AccessTraceDocument(
            functions_by_address=merge_traced_functions(
                build_traced_functions_from_proto_infos(result),
                enum_result.functions_by_address,
            ),
            enum_signatures_by_name=enum_result.enum_signatures_by_name,
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )


def _run_batch_script() -> None:
    exit_code = 0
    try:
        main()
    except Exception:  # noqa: BLE001 - batch IDA must always close with a failing exit code.
        exit_code = 1
        _write_batch_exception(traceback.format_exc())
    finally:
        ida_pro.qexit(exit_code)


def _write_batch_exception(error_output: str) -> None:
    error_path = os.environ.get("PROTO_TRACER_ERROR_PATH")
    if error_path is None:
        print(error_output, file=sys.stderr, end="", flush=True)
        return
    Path(error_path).write_text(error_output, encoding="utf-8")


if __name__ == "__main__":
    _run_batch_script()

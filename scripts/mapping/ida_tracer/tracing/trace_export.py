from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import (
    FunctionAccessInfo,
    FunctionAlias,
    TracedFunction,
    format_trace_address,
)
from scripts.mapping.ida_tracer.simulation.field_access import (
    dedupe_and_sort_access_entries,
)


def build_traced_functions_from_proto_infos(
    function_infos: dict[str, FunctionAccessInfo],
) -> dict[str, TracedFunction]:
    functions_by_address: dict[str, TracedFunction] = {}
    for function_info in function_infos.values():
        function_address = format_trace_address(function_info.start_address)
        alias = FunctionAlias(
            name=function_info.name,
            parameters=function_info.parameters,
            return_type=function_info.return_type,
            group=function_info.group,
            access_infos=function_info.access_infos,
        )
        existing_function = functions_by_address.get(function_address)
        if existing_function is None:
            functions_by_address[function_address] = TracedFunction(
                start_address=function_info.start_address,
                end_address=function_info.end_address,
                size=function_info.size,
                access_infos=function_info.access_infos,
                opcode_histogram=function_info.opcode_histogram,
                aliases=[alias],
                stable_callees=function_info.stable_callees,
                cfg_stats=function_info.cfg_stats,
            )
            continue
        functions_by_address[function_address] = existing_function.model_copy(
            update={
                "aliases": [*existing_function.aliases, alias],
                "access_infos": dedupe_and_sort_access_entries(
                    [*existing_function.access_infos, *function_info.access_infos]
                ),
            }
        )
    return functions_by_address


def merge_traced_functions(
    left_functions: dict[str, TracedFunction],
    right_functions: dict[str, TracedFunction],
) -> dict[str, TracedFunction]:
    merged_functions = dict(left_functions)
    for function_address, right_function in right_functions.items():
        left_function = merged_functions.get(function_address)
        if left_function is None:
            merged_functions[function_address] = right_function
            continue
        merged_functions[function_address] = left_function.model_copy(
            update={
                "resolved_metadata": right_function.resolved_metadata,
                "aliases": [*left_function.aliases, *right_function.aliases],
                "access_infos": dedupe_and_sort_access_entries(
                    [*left_function.access_infos, *right_function.access_infos]
                ),
            }
        )
    return merged_functions

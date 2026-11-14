import argparse
from collections.abc import Mapping


from DBDofusUnity.proto_mapper_assembly.controllers.message_lookup import (
    build_non_obf_alias_lookup,
    build_obf_alias_lookup,
    resolve_non_obf_alias,
    resolve_obf_alias,
)
from DBDofusUnity.proto_mapper_assembly.controllers.matching_inputs_loader import load_matching_inputs
from DBDofusUnity.proto_mapper_assembly.field_mapping.field_mapper import build_field_mapping
from DBDofusUnity.proto_mapper_assembly.field_mapping.field_mapping_preparation import (
    prepare_field_mapping_context,
)
from DBDofusUnity.proto_mapper_assembly.field_mapping.field_mapping_scoring import score_field_pair
from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import MessageAccessSignature
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage
from DBDofusUnity.proto_mapper_assembly.interfaces.field_mapping import FieldMappingContext
from DBDofusUnity.proto_mapper_assembly.interfaces.matching import MatchingWorkspace
from DBDofusUnity.proto_mapper_assembly.interfaces.runtime import (
    MessageRuntimeMetadata,
    RuntimeRemappingContext,
    RuntimeRemappingTraceEvent,
    RuntimeValidationCandidate,
)
from DBDofusUnity.proto_mapper_assembly.matching.score_lookup import (
    build_lazy_score_by_pair_lookup_from_signatures,
    build_message_pair_static_score,
)
from DBDofusUnity.proto_mapper_assembly.matching.workspace import build_matching_workspace
from DBDofusUnity.proto_mapper_assembly.runtime.metadata import build_message_runtime_metadata
from DBDofusUnity.proto_mapper_assembly.runtime.runtime_remapping import remap_runtime_instances
from DBDofusUnity.proto_mapper_assembly.runtime.runtime_store import RuntimeDataStore
from DBDofusUnity.proto_mapper_assembly.scoring.message_scoring import StructureSimilarityContext


def main() -> None:
    args = build_argument_parser().parse_args()

    matching_inputs = load_matching_inputs()
    obf_messages_by_cls = matching_inputs.obf_messages_by_cls
    non_obf_messages_by_cls = matching_inputs.non_obf_messages_by_cls
    obf_signatures_by_cls = matching_inputs.obf_signatures_by_cls
    non_obf_signatures_by_cls = matching_inputs.non_obf_signatures_by_cls

    obf_alias_to_cls = build_obf_alias_lookup(obf_messages_by_cls=obf_messages_by_cls)
    args.obf = resolve_obf_alias(str(args.obf), obf_alias_to_cls)

    if args.obf not in obf_signatures_by_cls:
        message = f"Unknown obfuscated message class: {args.obf}"
        raise SystemExit(message)

    normalized_non_obf = str(args.non_obf)

    alias_to_cls, short_alias_to_cls = build_non_obf_alias_lookup(
        non_obf_messages_by_cls=non_obf_messages_by_cls
    )
    args.non_obf = resolve_non_obf_alias(normalized_non_obf, alias_to_cls, short_alias_to_cls)

    if args.non_obf not in non_obf_signatures_by_cls:
        message = f"Unknown non-obfuscated message class: {args.non_obf}"
        raise SystemExit(message)

    obf_signature = obf_signatures_by_cls[args.obf]
    non_obf_signature = non_obf_signatures_by_cls[args.non_obf]
    workspace = build_matching_workspace(
        obf_signatures=list(obf_signatures_by_cls.values()),
        non_obf_signatures=list(non_obf_signatures_by_cls.values()),
        obf_messages_by_cls=obf_messages_by_cls,
        non_obf_messages_by_cls=non_obf_messages_by_cls,
    )
    structure_context = StructureSimilarityContext(
        left_signatures_by_cls=obf_signatures_by_cls,
        right_signatures_by_cls=non_obf_signatures_by_cls,
        left_enum_signatures_by_name=matching_inputs.obf_enum_signatures_by_name,
        right_enum_signatures_by_name=matching_inputs.non_obf_enum_signatures_by_name,
        left_access_trace=matching_inputs.obf_access_trace,
        right_access_trace=matching_inputs.non_obf_access_trace,
    )
    pair_static_score = build_message_pair_static_score(
        obf_signature, non_obf_signature, structure_context=structure_context
    )
    score_by_pair = build_lazy_score_by_pair_lookup_from_signatures(
        obf_signatures_by_cls=obf_signatures_by_cls,
        non_obf_signatures_by_cls=non_obf_signatures_by_cls,
        structure_context=structure_context,
    )
    runtime_data_store = RuntimeDataStore()
    field_mapping_context = FieldMappingContext(
        score_by_pair=score_by_pair,
        runtime_data_store=runtime_data_store,
        signature_overrides_by_non_obf_cls=matching_inputs.signature_overrides_by_non_obf_cls,
        obf_enum_signatures_by_name=matching_inputs.obf_enum_signatures_by_name,
        non_obf_enum_signatures_by_name=matching_inputs.non_obf_enum_signatures_by_name,
        obf_access_trace=matching_inputs.obf_access_trace,
        non_obf_access_trace=matching_inputs.non_obf_access_trace,
    )
    candidate = RuntimeValidationCandidate(
        obf_msg_sig=obf_signature,
        non_obf_msg_sig=non_obf_signature,
        obf_index=workspace.signature_indexes.obf_index_by_cls[args.obf],
        non_obf_index=workspace.signature_indexes.non_obf_index_by_cls[args.non_obf],
        field_mapping_result=build_field_mapping(
            non_obf_signature=non_obf_signature,
            obf_signature=obf_signature,
            non_obf_messages_by_cls=non_obf_messages_by_cls,
            obf_messages_by_cls=obf_messages_by_cls,
            field_mapping_context=field_mapping_context,
            obf_type_index=workspace.obf_type_index,
            non_obf_type_index=workspace.non_obf_type_index,
            pinned_pair=None,
        ),
    )
    candidates_by_non_obf: dict[str, dict[str, RuntimeValidationCandidate]] = {
        args.non_obf: {args.obf: candidate},
    }

    _debug_runtime_remapping(
        candidate=candidate,
        candidates_by_non_obf=candidates_by_non_obf,
        obf_messages_by_cls=obf_messages_by_cls,
        non_obf_messages_by_cls=non_obf_messages_by_cls,
        workspace=workspace,
        runtime_data_store=runtime_data_store,
    )
    _debug_field_mapping(
        candidate=candidate,
        obf_signature=obf_signature,
        non_obf_signature=non_obf_signature,
        obf_messages_by_cls=obf_messages_by_cls,
        non_obf_messages_by_cls=non_obf_messages_by_cls,
        workspace=workspace,
        field_mapping_context=field_mapping_context,
    )

    result: dict[str, object] = {
        "obf_message_cls": args.obf,
        "non_obf_message_cls": args.non_obf,
        "access_score": str(pair_static_score.assembly_sim_data),
        "structure_score": pair_static_score.structure_similarity,
        "field_mapping": candidate.field_mapping_result.field_mapping,
        "field_mapping_infos": candidate.field_mapping_result.field_mapping_infos,
        "field_mapping_rejected_infos": candidate.field_mapping_result.field_mapping_rejected_infos,
        "total_score": pair_static_score.static_similarity,
    }
    print(result)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute pair-level static/runtime scores for one explicit message pairing."
    )
    parser.add_argument("--obf", help="Obfuscated message class name.", required=True)
    parser.add_argument(
        "--non-obf",
        help="Non-obfuscated message class name. E.G : GameActionFightEvent",
        required=True,
    )
    return parser


def _debug_field_mapping(
    *,
    candidate: RuntimeValidationCandidate,
    obf_signature: MessageAccessSignature,
    non_obf_signature: MessageAccessSignature,
    obf_messages_by_cls: Mapping[str, DumpCSMessage],
    non_obf_messages_by_cls: Mapping[str, DumpCSMessage],
    workspace: MatchingWorkspace,
    field_mapping_context: FieldMappingContext,
) -> None:
    context = prepare_field_mapping_context(
        non_obf_signature=non_obf_signature,
        obf_signature=obf_signature,
        non_obf_messages_by_cls=dict(non_obf_messages_by_cls),
        obf_messages_by_cls=dict(obf_messages_by_cls),
        matching_store=None,
        field_mapping_context=field_mapping_context,
        obf_type_index=workspace.obf_type_index,
        non_obf_type_index=workspace.non_obf_type_index,
        pinned_pair=None,
    )

    mapped_non_obf_names = set(candidate.field_mapping_result.field_mapping.values())
    has_vf = candidate.field_mapping_result.has_validation_failure
    print(f"field_mapping_debug: has_validation_failure = {has_vf}")

    for non_obf_field in context.non_obf.fields:
        if non_obf_field.property_name is None:
            continue
        non_obf_name = non_obf_field.clean_field_name
        if non_obf_name in mapped_non_obf_names:
            continue

        scored_pairs: list[tuple[float, str, bool]] = []
        for obf_field in context.obf.fields:
            non_obf_sig = context.non_obf.field_signature_by_field_key.get(non_obf_field.field_key)
            obf_sig = context.obf.field_signature_by_field_key.get(obf_field.field_key)
            if non_obf_sig is None or obf_sig is None:
                continue
            score, pair_metadata = score_field_pair(
                non_obf_access_signature=non_obf_sig,
                obf_access_signature=obf_sig,
                non_obf_field=non_obf_field,
                obf_field=obf_field,
                non_obf_message=context.non_obf_signature.dump_cs_msg,
                obf_message=context.obf_signature.dump_cs_msg,
                non_obf_messages_by_cls=context.non_obf.messages_by_cls,
                obf_messages_by_cls=context.obf.messages_by_cls,
                non_obf_type_index=context.non_obf.type_index,
                obf_type_index=context.obf.type_index,
                non_obf_child_cls_by_field_key=context.non_obf.child_cls_by_field_key,
                obf_child_cls_by_field_key=context.obf.child_cls_by_field_key,
                matching_store=context.matching_store,
                field_mapping_context=context.field_mapping_context,
                pinned_pair=None,
            )
            if score > 0.0 or pair_metadata.did_validation_failure:
                obf_name = obf_field.clean_field_name
                scored_pairs.append((score, obf_name, pair_metadata.did_validation_failure))

        if not scored_pairs:
            print(
                f"field_mapping_debug: {non_obf_name!r} -> "
                "no obf candidate (all pairs score=0, validator not triggered)"
            )
        else:
            scored_pairs.sort(key=lambda pair: pair[0], reverse=True)
            for score, obf_name, did_vf in scored_pairs[:3]:
                print(
                    f"field_mapping_debug: {non_obf_name!r} -> "
                    f"obf={obf_name!r}, score={score:.3f}, did_validation_failure={did_vf}"
                )


def _debug_runtime_remapping(
    *,
    candidate: RuntimeValidationCandidate,
    candidates_by_non_obf: dict[str, dict[str, RuntimeValidationCandidate]],
    obf_messages_by_cls: Mapping[str, DumpCSMessage],
    non_obf_messages_by_cls: Mapping[str, DumpCSMessage],
    workspace: MatchingWorkspace,
    runtime_data_store: RuntimeDataStore,
) -> None:
    normalized_instances = runtime_data_store.get_normalized_content_for_obf_message(
        message=candidate.obf_msg_sig.dump_cs_msg,
        obf_messages_by_cls=obf_messages_by_cls,
    )
    if not normalized_instances:
        print("runtime_debug: no normalized runtime samples available")
        return

    remapping_context = _build_remapping_context(
        candidates_by_non_obf=candidates_by_non_obf,
        obf_messages_by_cls=obf_messages_by_cls,
        non_obf_messages_by_cls=non_obf_messages_by_cls,
        workspace=workspace,
    )
    trace_events: list[RuntimeRemappingTraceEvent] = []

    print("runtime_debug: samples =", len(normalized_instances))

    remap_result = remap_runtime_instances(
        normalized_instances=normalized_instances,
        candidate=candidate,
        obf_message=candidate.obf_msg_sig.dump_cs_msg,
        non_obf_message=candidate.non_obf_msg_sig.dump_cs_msg,
        remapping_context=remapping_context,
        trace_handler=trace_events.append,
    )
    print("runtime_debug: remap_failure =", remap_result.mapping_failure)
    print("runtime_debug: remap_failure_origin =", remap_result.mapping_failure_origin)
    if trace_events:
        first_trace_event = trace_events[0]
        print(f"runtime_debug: first_failed_path = {_trace_runtime_event_label(first_trace_event)}")
    elif remap_result.mapping_failure is None:
        print("runtime_debug: remapping succeeded (validators enforced by ILP)")

    if remap_result.mapping_failure is None:
        non_obf_message_name = candidate.non_obf_msg_sig.dump_cs_msg.name
        remapped_instances = remap_result.instances_by_type.get(non_obf_message_name, [])
        if remapped_instances:
            all_remapped_keys: set[str] = set()
            for remapped_instance in remapped_instances:
                all_remapped_keys |= remapped_instance.keys()
            expected_keys = {
                field.clean_field_name
                for field in candidate.non_obf_msg_sig.get_exportable_fields()
                if field.property_name is not None
            }
            silent_drops = expected_keys - all_remapped_keys
            if silent_drops:
                print(f"runtime_debug: silently_dropped_fields in remapping = {sorted(silent_drops)}")


def _build_remapping_context(
    *,
    candidates_by_non_obf: dict[str, dict[str, RuntimeValidationCandidate]],
    obf_messages_by_cls: Mapping[str, DumpCSMessage],
    non_obf_messages_by_cls: Mapping[str, DumpCSMessage],
    workspace: MatchingWorkspace,
) -> RuntimeRemappingContext:
    def get_obf_metadata(message: DumpCSMessage) -> MessageRuntimeMetadata:
        signature = workspace.obf_signatures_by_cls.get(message.composed_name)
        return build_message_runtime_metadata(
            message=message,
            messages_by_cls=dict(obf_messages_by_cls),
            type_index=workspace.obf_type_index,
            live_field_keys=None if signature is None else signature.live_field_keys,
        )

    def get_non_obf_metadata(message: DumpCSMessage) -> MessageRuntimeMetadata:
        signature = workspace.non_obf_signatures_by_cls.get(message.composed_name)
        return build_message_runtime_metadata(
            message=message,
            messages_by_cls=dict(non_obf_messages_by_cls),
            type_index=workspace.non_obf_type_index,
            live_field_keys=None if signature is None else signature.live_field_keys,
        )

    return RuntimeRemappingContext(
        candidates_by_non_obf=candidates_by_non_obf,
        obf_messages_by_cls=dict(obf_messages_by_cls),
        non_obf_messages_by_cls=dict(non_obf_messages_by_cls),
        get_obf_metadata=get_obf_metadata,
        get_non_obf_metadata=get_non_obf_metadata,
    )


def _trace_runtime_event_label(trace_event: RuntimeRemappingTraceEvent) -> str:
    path = ".".join(trace_event.path) if trace_event.path else "<root>"
    return f"{path} | {trace_event.message}"


if __name__ == "__main__":
    raise SystemExit(main())

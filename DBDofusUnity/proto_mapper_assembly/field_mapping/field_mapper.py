from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import numpy as np
from scipy.optimize import linear_sum_assignment

from DBDofusUnity.proto_mapper_assembly.field_mapping.field_mapping_preparation import (
    PreparedFieldMappingContext,
    prepare_field_mapping_context,
)
from DBDofusUnity.proto_mapper_assembly.field_mapping.field_mapping_scoring import (
    FieldPairMetadata,
    score_field_pair,
)
from DBDofusUnity.proto_mapper_assembly.field_mapping.pulp.constraints import has_applicable_constraints
from DBDofusUnity.proto_mapper_assembly.field_mapping.pulp.solver import solve_field_mapping_ilp
from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import MessageAccessSignature
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage, DumpCSMessageField
from DBDofusUnity.proto_mapper_assembly.interfaces.field_mapping import (
    DiscoveredMessageMatch,
    FieldMappingContext,
    FieldMappingRejectedInfos,
    FieldMappingResult,
    FieldMappingUnmappedNonObfFields,
    MatchingStoreProtocol,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.field_mapping_rejected_infos import (
    FieldMappingRejectedInfo,
    ReasonRejectedInfo,
    ScoreRejectedInfo,
    ValidationFailureRejectedInfo,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.pinned_pairs import PinnedPair


@dataclass(frozen=True)
class FieldPairScore:
    obf_field: DumpCSMessageField
    non_obf_field: DumpCSMessageField
    score: float
    metadata: FieldPairMetadata


@dataclass(frozen=True)
class UnmappedNonObfField:
    field: DumpCSMessageField
    reason: str


@dataclass(frozen=True)
class FieldMappingBuildResult:
    field_mappings: list[FieldPairScore]
    rejected_pairs: list[FieldPairScore]
    unmapped_non_obf: list[UnmappedNonObfField]


def build_field_mapping(
    non_obf_signature: MessageAccessSignature,
    obf_signature: MessageAccessSignature,
    non_obf_messages_by_cls: dict[str, DumpCSMessage],
    obf_messages_by_cls: dict[str, DumpCSMessage],
    *,
    matching_store: MatchingStoreProtocol | None = None,
    field_mapping_context: FieldMappingContext,
    obf_type_index: dict[str, tuple[DumpCSMessage, ...]],
    non_obf_type_index: dict[str, tuple[DumpCSMessage, ...]],
    pinned_pair: PinnedPair | None = None,
) -> FieldMappingResult:
    context = prepare_field_mapping_context(
        non_obf_signature=non_obf_signature,
        obf_signature=obf_signature,
        non_obf_messages_by_cls=non_obf_messages_by_cls,
        obf_messages_by_cls=obf_messages_by_cls,
        matching_store=matching_store,
        field_mapping_context=field_mapping_context,
        obf_type_index=obf_type_index,
        non_obf_type_index=non_obf_type_index,
        pinned_pair=pinned_pair,
    )

    build_result = _build_field_mappings(context)
    field_mapping: dict[str, str] = {}
    discovered_message_matches: list[DiscoveredMessageMatch] = []
    field_mapping_infos: dict[str, dict[str, float]] = defaultdict(dict)
    has_validation_failure: bool = False
    for pair in build_result.field_mappings:
        if not pair.obf_field.property_name or not pair.non_obf_field.property_name:
            error_msg = (
                f"{pair.obf_field.field_name} or {pair.non_obf_field.field_name} does not have property name"
            )
            raise ValueError(error_msg)
        obf_field_name = pair.obf_field.clean_field_name
        non_obf_field_name = pair.non_obf_field.clean_field_name
        field_mapping[obf_field_name] = non_obf_field_name
        field_mapping_infos[obf_field_name][non_obf_field_name] = pair.score
        if pair.metadata.child_match is not None:
            discovered_message_matches.append(pair.metadata.child_match)
        if pair.metadata.did_validation_failure:
            has_validation_failure = True

    field_mapping_rejected_infos: FieldMappingRejectedInfos = defaultdict(dict)
    for pair in build_result.rejected_pairs:
        if not pair.obf_field.property_name or not pair.non_obf_field.property_name:
            continue
        obf_field_name = pair.obf_field.clean_field_name
        non_obf_field_name = pair.non_obf_field.clean_field_name
        field_mapping_rejected_infos[obf_field_name][non_obf_field_name] = _build_rejected_info(
            pair.metadata, pair.score
        )

    for obf_field in _get_non_exportable_obf_fields(context):
        obf_field_name = obf_field.clean_field_name
        for non_obf_field in context.non_obf.fields:
            field_mapping_rejected_infos[obf_field_name][non_obf_field.clean_field_name] = ReasonRejectedInfo(
                reason="obf_field_not_exportable"
            )

    field_mapping_unmapped_non_obf_fields: FieldMappingUnmappedNonObfFields = {}
    for unmapped_field in build_result.unmapped_non_obf:
        if not unmapped_field.field.property_name:
            continue
        field_mapping_unmapped_non_obf_fields[unmapped_field.field.clean_field_name] = unmapped_field.reason

    return FieldMappingResult(
        field_mapping=field_mapping,
        field_mapping_infos=field_mapping_infos,
        discovered_message_matches=tuple(discovered_message_matches),
        has_validation_failure=has_validation_failure,
        field_mapping_rejected_infos=dict(field_mapping_rejected_infos),
        field_mapping_unmapped_non_obf_fields=field_mapping_unmapped_non_obf_fields,
    )


def _build_field_mappings(
    context: PreparedFieldMappingContext,
) -> FieldMappingBuildResult:
    similarity_matrix = np.full((len(context.non_obf.fields), len(context.obf.fields)), 0, dtype=np.float64)
    pair_metadata_by_indexes: dict[tuple[int, int], FieldPairMetadata] = {}

    pair_data: list[FieldPairScore] = []
    for non_obf_index, non_obf_field in enumerate(context.non_obf.fields):
        for obf_index, obf_field in enumerate(context.obf.fields):
            score, pair_metadata = score_field_pair(
                non_obf_access_signature=context.non_obf.field_signature_by_field_key[
                    non_obf_field.field_key
                ],
                obf_access_signature=context.obf.field_signature_by_field_key[obf_field.field_key],
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
                pinned_pair=context.pinned_pair,
            )
            similarity_matrix[non_obf_index, obf_index] = score
            pair_metadata_by_indexes[(non_obf_index, obf_index)] = pair_metadata
            pair_data.append(FieldPairScore(obf_field, non_obf_field, score, pair_metadata))

    non_obf_indexes, obf_indexes = _solve_assignment(context, similarity_matrix)
    field_mappings: list[FieldPairScore] = []
    assigned_non_obf_indices: set[int] = {int(idx) for idx in non_obf_indexes}
    unmapped_non_obf: list[UnmappedNonObfField] = []

    selected_indexes: set[tuple[int, int]] = set()
    for non_obf_index, obf_index in zip(non_obf_indexes, obf_indexes, strict=True):
        non_obf_idx = int(non_obf_index)
        obf_idx = int(obf_index)
        if similarity_matrix[non_obf_idx, obf_idx] == 0:
            unmapped_non_obf.append(
                UnmappedNonObfField(context.non_obf.fields[non_obf_idx], "score_below_threshold")
            )
            continue
        selected_indexes.add((non_obf_idx, obf_idx))
        field_mappings.append(
            FieldPairScore(
                context.obf.fields[obf_idx],
                context.non_obf.fields[non_obf_idx],
                float(similarity_matrix[non_obf_idx, obf_idx]),
                pair_metadata_by_indexes[(non_obf_idx, obf_idx)],
            )
        )

    unmapped_non_obf.extend(
        UnmappedNonObfField(context.non_obf.fields[idx], "no_obf_candidate")
        for idx in range(len(context.non_obf.fields))
        if idx not in assigned_non_obf_indices
    )

    field_mappings.sort(key=lambda pair: pair.obf_field.memory_offset)
    rejected_pairs = _build_rejected_pairs(
        pair_data=pair_data,
        selected_indexes=selected_indexes,
        non_obf_fields=context.non_obf.fields,
        obf_fields=context.obf.fields,
    )
    return FieldMappingBuildResult(field_mappings, rejected_pairs, unmapped_non_obf)


def _build_rejected_pairs(
    *,
    pair_data: list[FieldPairScore],
    selected_indexes: set[tuple[int, int]],
    non_obf_fields: tuple[DumpCSMessageField, ...],
    obf_fields: tuple[DumpCSMessageField, ...],
) -> list[FieldPairScore]:
    selected_pair_names = {
        (obf_fields[obf_idx].clean_field_name, non_obf_fields[non_obf_idx].clean_field_name)
        for non_obf_idx, obf_idx in selected_indexes
    }

    rejected_pairs: list[FieldPairScore] = []
    for pair in pair_data:
        if (pair.obf_field.clean_field_name, pair.non_obf_field.clean_field_name) in selected_pair_names:
            continue
        rejected_pairs.append(pair)
    return rejected_pairs


def _build_rejected_info(pair_metadata: FieldPairMetadata, score: float) -> FieldMappingRejectedInfo:
    reason = pair_metadata.zero_score_reason
    if reason == "validation_failure":
        return ValidationFailureRejectedInfo(
            reason=reason, value=_to_json_debug_value(pair_metadata.failed_validation_value)
        )
    if reason == "pinned_field_exclusion":
        return ReasonRejectedInfo(reason=reason)
    return ScoreRejectedInfo(reason="not_selected", score=score)


def _get_non_exportable_obf_fields(context: PreparedFieldMappingContext) -> tuple[DumpCSMessageField, ...]:
    exportable_field_keys = {field.field_key for field in context.obf.fields}
    return tuple(
        field
        for field in context.obf_signature.dump_cs_msg.fields
        if field.is_declared_proto_shape_field
        and field.property_name is not None
        and field.field_key not in exportable_field_keys
    )


def _to_json_debug_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_to_json_debug_value(item) for item in cast("list[object]", value)]
    if isinstance(value, Mapping):
        return {
            str(_to_json_debug_value(key)): _to_json_debug_value(item)
            for key, item in cast("Mapping[object, object]", value).items()
        }
    return repr(value)


def _solve_assignment(
    context: PreparedFieldMappingContext,
    similarity_matrix: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    if has_applicable_constraints(context.non_obf_signature.dump_cs_msg.name):
        runtime_instances = (
            context.field_mapping_context.runtime_data_store.get_normalized_content_for_obf_message(
                message=context.obf_signature.dump_cs_msg,
                obf_messages_by_cls=context.obf.messages_by_cls,
            )
        )
        if runtime_instances:
            ilp_result: tuple[np.ndarray, np.ndarray] | None = solve_field_mapping_ilp(
                context=context,
                similarity_matrix=similarity_matrix,
                runtime_instances=runtime_instances,
            )
            if ilp_result is not None:
                return ilp_result
    return linear_sum_assignment(similarity_matrix, maximize=True)

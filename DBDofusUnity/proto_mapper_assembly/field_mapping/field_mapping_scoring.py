import dataclasses
from dataclasses import dataclass
from typing import Literal, NamedTuple

from DBDofusUnity.proto_mapper_assembly.helpers.proto_helpers import resolve_child_message_cls
from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import FieldAccessSignatures
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import (
    DumpCSMessage,
    DumpCSMessageField,
    EnumFieldTypes,
    FieldKey,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.enum_mapping import EnumSignatureEntry
from DBDofusUnity.proto_mapper_assembly.interfaces.field_category import FieldCategoryEnum
from DBDofusUnity.proto_mapper_assembly.interfaces.field_mapping import (
    DiscoveredMessageMatch,
    FieldMappingContext,
    MatchingStoreProtocol,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.message_pair import MatchPairKey
from DBDofusUnity.proto_mapper_assembly.interfaces.pinned_pairs import PinnedPair
from DBDofusUnity.proto_mapper_assembly.runtime.runtime_field_validation import (
    get_defined_runtime_values,
    is_runtime_compatible_field_pair,
)
from DBDofusUnity.proto_mapper_assembly.scoring.enum_similarity import (
    EnumSimilarityContext,
    enum_signature_similarity,
)
from DBDofusUnity.proto_mapper_assembly.scoring.signature_scoring import (
    declared_field_similarity,
    field_evidence_similarity,
)

_CHILD_MATCH_SUPPORT_BONUS = 0.1
_CHILD_MESSAGE_SCORE_WEIGHT = 0.65
_LOCAL_FIELD_SCORE_WEIGHT = 0.35
_VALIDATION_BONUS = 0.001
_RUNTIME_ALIVE_FIELD_BONUS = 0.0005
_MIN_CHILD_MESSAGE_SCORE = 0.1
_COMPLEMENTARY_ACCESS_SCORE = 0.65

NoMatchReason = Literal[
    "enum_override_conflict",
    "validation_failure",
    "child_conflict",
    "child_score_too_low",
    "not_selected",
    "pinned_field_exclusion",
]


@dataclass(frozen=True)
class FieldPairMetadata:
    child_match: DiscoveredMessageMatch | None = None
    has_conflict: bool = False
    child_message_score: float | None = None
    did_validation_run: bool = False
    did_validation_failure: bool = False
    failed_validation_value: object | None = None
    zero_score_reason: NoMatchReason | None = None


@dataclass(frozen=True)
class EnumSimilarityResolution:
    score: float | None
    has_override_hint: bool


class EnumSignatureSlots(NamedTuple):
    key: EnumSignatureEntry | None
    value: EnumSignatureEntry | None


def score_field_pair(
    *,
    non_obf_access_signature: FieldAccessSignatures,
    obf_access_signature: FieldAccessSignatures,
    non_obf_field: DumpCSMessageField,
    obf_field: DumpCSMessageField,
    non_obf_message: DumpCSMessage,
    obf_message: DumpCSMessage,
    non_obf_messages_by_cls: dict[str, DumpCSMessage],
    obf_messages_by_cls: dict[str, DumpCSMessage],
    non_obf_type_index: dict[str, tuple[DumpCSMessage, ...]],
    field_mapping_context: FieldMappingContext,
    obf_type_index: dict[str, tuple[DumpCSMessage, ...]],
    non_obf_child_cls_by_field_key: dict[FieldKey, str | None],
    obf_child_cls_by_field_key: dict[FieldKey, str | None],
    matching_store: MatchingStoreProtocol | None,
    pinned_pair: PinnedPair | None,
) -> tuple[float, FieldPairMetadata]:
    score: float | None = None

    if pinned_pair:
        if (
            pinned_pair.complete_field_mapping
            and obf_field.clean_field_name not in pinned_pair.field_mapping_by_obf
        ):
            return 0.0, FieldPairMetadata(zero_score_reason="pinned_field_exclusion")
        if obf_field.clean_field_name in pinned_pair.field_mapping_by_obf:
            if non_obf_field.clean_field_name != pinned_pair.field_mapping_by_obf[obf_field.clean_field_name]:
                return 0.0, FieldPairMetadata(zero_score_reason="pinned_field_exclusion")
            score = 1.0
        if non_obf_field.clean_field_name in pinned_pair.field_mapping_by_non_obf:
            if obf_field.clean_field_name != pinned_pair.field_mapping_by_non_obf[non_obf_field.clean_field_name]:
                return 0.0, FieldPairMetadata(zero_score_reason="pinned_field_exclusion")
            score = 1.0

    if score is None:
        declared_score = declared_field_similarity(non_obf_field, obf_field)
        if declared_score != 0:
            score = declared_score * field_evidence_similarity(non_obf_access_signature, obf_access_signature)
            if score == 0 and _has_unique_complementary_access_match(
                non_obf_access_signature,
                obf_access_signature,
                non_obf_field,
                obf_field,
                non_obf_message,
                obf_message,
            ):
                score = _COMPLEMENTARY_ACCESS_SCORE
        else:
            score = 0
        enum_resolution = _resolve_enum_signature_similarity(
            non_obf_field=non_obf_field,
            obf_field=obf_field,
            non_obf_message=non_obf_message,
            field_mapping_context=field_mapping_context,
        )
        if enum_resolution.score is not None:
            if enum_resolution.has_override_hint and enum_resolution.score == 0.0:
                return 0.0, FieldPairMetadata(zero_score_reason="enum_override_conflict")
            score = (_LOCAL_FIELD_SCORE_WEIGHT * score) + (
                _CHILD_MESSAGE_SCORE_WEIGHT * enum_resolution.score
            )

    field_runtime_metadata = is_runtime_compatible_field_pair(
        non_obf_field=non_obf_field,
        obf_field=obf_field,
        non_obf_message=non_obf_message,
        obf_message=obf_message,
        obf_messages_by_cls=obf_messages_by_cls,
        runtime_data_store=field_mapping_context.runtime_data_store,
    )
    if field_runtime_metadata.did_validation_run:
        if field_runtime_metadata.did_validation_failure:
            return 0, FieldPairMetadata(
                did_validation_failure=field_runtime_metadata.did_validation_failure,
                did_validation_run=field_runtime_metadata.did_validation_run,
                failed_validation_value=field_runtime_metadata.failed_value,
                zero_score_reason="validation_failure",
            )
        score += _VALIDATION_BONUS

    if score > 0.0 and _has_runtime_alive_obf_value(
        obf_field=obf_field,
        obf_message=obf_message,
        obf_messages_by_cls=obf_messages_by_cls,
        field_mapping_context=field_mapping_context,
    ):
        score += _RUNTIME_ALIVE_FIELD_BONUS

    child_metadata = _build_child_match_metadata(
        non_obf_field=non_obf_field,
        obf_field=obf_field,
        non_obf_message=non_obf_message,
        obf_message=obf_message,
        non_obf_messages_by_cls=non_obf_messages_by_cls,
        obf_messages_by_cls=obf_messages_by_cls,
        non_obf_type_index=non_obf_type_index,
        obf_type_index=obf_type_index,
        non_obf_child_cls=non_obf_child_cls_by_field_key.get(non_obf_field.field_key),
        obf_child_cls=obf_child_cls_by_field_key.get(obf_field.field_key),
        matching_store=matching_store,
        field_mapping_context=field_mapping_context,
        score=score,
    )

    if child_metadata.has_conflict:
        return 0.0, dataclasses.replace(child_metadata, zero_score_reason="child_conflict")

    child_message_score = child_metadata.child_message_score
    if child_message_score is not None:
        if child_message_score < _MIN_CHILD_MESSAGE_SCORE:
            return 0.0, dataclasses.replace(child_metadata, zero_score_reason="child_score_too_low")
        score = (_LOCAL_FIELD_SCORE_WEIGHT * score) + (_CHILD_MESSAGE_SCORE_WEIGHT * child_message_score)

    child_match = child_metadata.child_match
    if (
        child_match is not None
        and matching_store is not None
        and matching_store.supports_pair(child_match.obf_message_cls, child_match.non_obf_message_cls)
    ):
        score = min(1.0, score + _CHILD_MATCH_SUPPORT_BONUS)

    return score, child_metadata


def _has_unique_complementary_access_match(
    left_access: FieldAccessSignatures,
    right_access: FieldAccessSignatures,
    left_field: DumpCSMessageField,
    right_field: DumpCSMessageField,
    left_message: DumpCSMessage,
    right_message: DumpCSMessage,
) -> bool:
    if (
        left_field.category
        not in {
            FieldCategoryEnum.NUMBER,
            FieldCategoryEnum.BOOLEAN,
            FieldCategoryEnum.STRING,
        }
        or declared_field_similarity(left_field, right_field) != 1.0
    ):
        return False
    left_kinds = {access.access_kind for access in left_access.accesses}
    right_kinds = {access.access_kind for access in right_access.accesses}
    if not left_kinds or not right_kinds:
        return False
    readers = {"read", "getter"}
    writers = {"write", "setter"}
    if not (
        (left_kinds <= readers and right_kinds <= writers)
        or (left_kinds <= writers and right_kinds <= readers)
    ):
        return False
    return all(
        sum(
            declared_field_similarity(field, candidate) > 0
            for candidate in message.fields
            if candidate.is_declared_proto_shape_field
        )
        == 1
        for field, message in ((left_field, right_message), (right_field, left_message))
    )


def _has_runtime_alive_obf_value(
    *,
    obf_field: DumpCSMessageField,
    obf_message: DumpCSMessage,
    obf_messages_by_cls: dict[str, DumpCSMessage],
    field_mapping_context: FieldMappingContext,
) -> bool:
    runtime_instances = field_mapping_context.runtime_data_store.get_normalized_content_for_obf_message(
        message=obf_message,
        obf_messages_by_cls=obf_messages_by_cls,
    )
    return bool(get_defined_runtime_values(runtime_instances, obf_field.clean_field_name))


def _resolve_enum_signature_similarity(
    *,
    non_obf_field: DumpCSMessageField,
    obf_field: DumpCSMessageField,
    non_obf_message: DumpCSMessage,
    field_mapping_context: FieldMappingContext,
) -> EnumSimilarityResolution:
    override_on_msg = field_mapping_context.signature_overrides_by_non_obf_cls.get(
        non_obf_message.composed_name
    )
    enum_override_on_field = (
        override_on_msg.enum_signature_hints_by_non_obf_prop_name.get(non_obf_field.clean_field_name)
        if override_on_msg is not None
        else None
    )

    if enum_override_on_field:
        enum_override_field_key = enum_override_on_field.get("key")
        enum_override_field_value = enum_override_on_field.get("value")
        override_scores = _resolve_override_enum_signature_scores(
            obf_field=obf_field,
            field_mapping_context=field_mapping_context,
            non_obf_enum_signature_types=EnumFieldTypes(
                key=(enum_override_field_key.non_obf_enum_type if enum_override_field_key else None),
                value=(enum_override_field_value.non_obf_enum_type if enum_override_field_value else None),
            ),
            non_obf_enum_signatures=EnumSignatureSlots(
                key=enum_override_field_key.signature if enum_override_field_key else None,
                value=enum_override_field_value.signature if enum_override_field_value else None,
            ),
        )
        if not override_scores:
            return EnumSimilarityResolution(score=None, has_override_hint=True)

        return EnumSimilarityResolution(
            score=sum(override_scores) / len(override_scores),
            has_override_hint=True,
        )

    comparable_slot_scores = _resolve_comparable_enum_signature_scores(
        non_obf_field=non_obf_field,
        obf_field=obf_field,
        field_mapping_context=field_mapping_context,
    )
    if not comparable_slot_scores:
        return EnumSimilarityResolution(score=None, has_override_hint=False)

    return EnumSimilarityResolution(
        score=sum(comparable_slot_scores) / len(comparable_slot_scores),
        has_override_hint=False,
    )


def _resolve_override_enum_signature_scores(
    *,
    obf_field: DumpCSMessageField,
    field_mapping_context: FieldMappingContext,
    non_obf_enum_signature_types: EnumFieldTypes,
    non_obf_enum_signatures: EnumSignatureSlots,
) -> list[float]:
    obf_enum_types = obf_field.enum_field_types

    scores: list[float] = []
    if obf_enum_types.key and non_obf_enum_signature_types.key:
        key_score = _resolve_enum_signature_score(
            obf_enum_type=obf_enum_types.key,
            non_obf_enum_type=non_obf_enum_signature_types.key,
            non_obf_enum_signature=non_obf_enum_signatures.key,
            field_mapping_context=field_mapping_context,
        )
        if key_score is not None:
            scores.append(key_score)

    if obf_enum_types.value and non_obf_enum_signature_types.value:
        value_score = _resolve_enum_signature_score(
            obf_enum_type=obf_enum_types.value,
            non_obf_enum_type=non_obf_enum_signature_types.value,
            non_obf_enum_signature=non_obf_enum_signatures.value,
            field_mapping_context=field_mapping_context,
        )
        if value_score is not None:
            scores.append(value_score)

    return scores


def _resolve_comparable_enum_signature_scores(
    *,
    non_obf_field: DumpCSMessageField,
    obf_field: DumpCSMessageField,
    field_mapping_context: FieldMappingContext,
) -> list[float]:
    obf_enum_types = obf_field.enum_field_types
    non_obf_enum_types = non_obf_field.enum_field_types

    scores: list[float] = []
    if obf_enum_types.key and non_obf_enum_types.key:
        key_score = _resolve_enum_signature_score(
            obf_enum_type=obf_enum_types.key,
            non_obf_enum_type=non_obf_enum_types.key,
            non_obf_enum_signature=None,
            field_mapping_context=field_mapping_context,
        )
        if key_score is not None:
            scores.append(key_score)

    if obf_enum_types.value and non_obf_enum_types.value:
        value_score = _resolve_enum_signature_score(
            obf_enum_type=obf_enum_types.value,
            non_obf_enum_type=non_obf_enum_types.value,
            non_obf_enum_signature=None,
            field_mapping_context=field_mapping_context,
        )
        if value_score is not None:
            scores.append(value_score)

    return scores


def _resolve_enum_signature_score(
    *,
    obf_enum_type: str,
    non_obf_enum_type: str,
    non_obf_enum_signature: EnumSignatureEntry | None,
    field_mapping_context: FieldMappingContext,
) -> float | None:
    obf_enum_signature = field_mapping_context.obf_enum_signatures_by_name.get(obf_enum_type)
    if non_obf_enum_signature is None:
        non_obf_enum_signature = field_mapping_context.non_obf_enum_signatures_by_name.get(non_obf_enum_type)
        right_access_trace = field_mapping_context.non_obf_access_trace
    else:
        right_access_trace = field_mapping_context.obf_access_trace
    if obf_enum_signature is None or non_obf_enum_signature is None:
        return None
    return enum_signature_similarity(
        obf_enum_signature,
        non_obf_enum_signature,
        context=EnumSimilarityContext(field_mapping_context.obf_access_trace, right_access_trace),
    )


def _build_child_match_metadata(
    *,
    non_obf_field: DumpCSMessageField,
    obf_field: DumpCSMessageField,
    non_obf_message: DumpCSMessage,
    obf_message: DumpCSMessage,
    non_obf_messages_by_cls: dict[str, DumpCSMessage],
    obf_messages_by_cls: dict[str, DumpCSMessage],
    non_obf_type_index: dict[str, tuple[DumpCSMessage, ...]],
    obf_type_index: dict[str, tuple[DumpCSMessage, ...]],
    non_obf_child_cls: str | None,
    obf_child_cls: str | None,
    matching_store: MatchingStoreProtocol | None,
    field_mapping_context: FieldMappingContext | None,
    score: float,
) -> FieldPairMetadata:
    if non_obf_child_cls is None:
        non_obf_child_cls = resolve_child_message_cls(
            field=non_obf_field,
            parent_message=non_obf_message,
            messages_by_cls=non_obf_messages_by_cls,
            type_index=non_obf_type_index,
        )
    if obf_child_cls is None:
        obf_child_cls = resolve_child_message_cls(
            field=obf_field,
            parent_message=obf_message,
            messages_by_cls=obf_messages_by_cls,
            type_index=obf_type_index,
        )
    if non_obf_child_cls is None or obf_child_cls is None:
        return FieldPairMetadata()

    child_message_score = None
    if field_mapping_context is not None and field_mapping_context.score_by_pair is not None:
        try:
            child_message_score = field_mapping_context.score_by_pair[
                MatchPairKey(obf_child_cls, non_obf_child_cls)
            ]
        except KeyError:
            child_message_score = None

    discovered_match = DiscoveredMessageMatch(
        obf_message_cls=obf_child_cls,
        non_obf_message_cls=non_obf_child_cls,
        source_field_obf=obf_field.field_name,
        source_field_non_obf=non_obf_field.field_name,
        confidence=score,
        reason="field_mapping_child",
    )
    return FieldPairMetadata(
        child_match=discovered_match,
        has_conflict=(
            matching_store.conflicts_with_pair(obf_child_cls, non_obf_child_cls)
            if matching_store is not None
            else False
        ),
        child_message_score=child_message_score,
    )

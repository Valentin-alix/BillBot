from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from DBDofusUnity.proto_mapper_assembly.controllers.game_mappings import load_game_mappings_document
from DBDofusUnity.proto_mapper_assembly.controllers.pinned_pairs import load_pinned_pairs
from DBDofusUnity.proto_mapper_assembly.interfaces.auto_mode_mapping_contract import (
    AutoModeMappingContract,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.game_mappings import GameMappingEntry
from DBDofusUnity.proto_mapper_assembly.interfaces.pinned_pairs import PinnedPair
from DBDofusUnity.proto_mapper_assembly.interfaces.runtime_data import ObservedRootObfMessage


@dataclass(frozen=True)
class AutoModeMappingAudit:
    message_count: int
    field_count: int
    pinned_message_exceptions: tuple[str, ...]
    pinned_field_exceptions: tuple[str, ...]
    messages_without_runtime_confidence: int
    messages_without_evidence: int
    unmapped_observed_messages: tuple[str, ...]
    observed_contract_messages: int
    runtime_capture_count: int
    runtime_session_count: int


@dataclass
class _AuditFailures:
    missing_messages: list[str] = field(default_factory=list[str])
    missing_fields: list[str] = field(default_factory=list[str])
    low_message_scores: list[str] = field(default_factory=list[str])
    low_match_margins: list[str] = field(default_factory=list[str])
    fieldless_low_match_margins: list[str] = field(default_factory=list[str])
    low_field_scores: list[str] = field(default_factory=list[str])
    low_confidence_messages: list[str] = field(default_factory=list[str])

    def has_failures(self) -> bool:
        return any(
            (
                self.missing_messages,
                self.missing_fields,
                self.low_message_scores,
                self.low_match_margins,
                self.fieldless_low_match_margins,
                self.low_field_scores,
                self.low_confidence_messages,
            )
        )

    def format(self) -> str:
        sections = [
            ("missing messages", self.missing_messages),
            ("missing fields", self.missing_fields),
            ("low message scores", self.low_message_scores),
            ("low match margins", self.low_match_margins),
            ("fieldless low match margins", self.fieldless_low_match_margins),
            ("low field scores", self.low_field_scores),
            ("low-confidence messages", self.low_confidence_messages),
        ]
        lines = ["Auto-mode mapping contract failed:"]
        for title, values in sections:
            if values:
                lines.append(f"- {title} ({len(values)}):")
                lines.extend(f"  - {value}" for value in values)
        return "\n".join(lines)


class AutoModeMappingContractError(ValueError):
    pass


def check_auto_mode_mappings(
    *,
    contract: AutoModeMappingContract,
    detailed_mappings_path: Path,
    pinned_pairs_path: Path,
    observed_root_obf_messages: Mapping[str, ObservedRootObfMessage],
) -> AutoModeMappingAudit:
    mappings = load_game_mappings_document(detailed_mappings_path)
    pinned_pairs = load_pinned_pairs(pinned_pairs_path)
    entries_by_name = {
        _normalize_name(entry.full_non_obf_msg_namespace): entry for entry in mappings.root.values()
    }
    pins_by_name = {_normalize_name(pair.non_obf): pair for pair in pinned_pairs.pairs}

    failures = _AuditFailures()
    pinned_message_exceptions: list[str] = []
    pinned_field_exceptions: list[str] = []
    messages_without_runtime_confidence = 0
    messages_without_evidence = 0
    field_count = 0
    for requirement in contract.messages:
        field_count += len(requirement.fields)
        normalized_name = _normalize_name(requirement.message)
        entry = entries_by_name.get(normalized_name)
        if entry is None:
            failures.missing_messages.append(requirement.message)
            continue
        if entry.runtime_confidence is None:
            messages_without_runtime_confidence += 1
        if not entry.is_runtime_observed:
            messages_without_evidence += 1

        pinned_pair = pins_by_name.get(normalized_name)
        _check_message_scores(
            requirement.message,
            entry,
            pinned_pair,
            contract,
            failures,
            pinned_message_exceptions,
        )
        _check_fields(
            requirement.message,
            requirement.fields,
            entry,
            pinned_pair,
            contract,
            failures,
            pinned_field_exceptions,
        )

    unmapped_observed_messages = _collect_unmapped_observed_messages(
        observed_root_obf_messages,
        mappings.root.values(),
    )

    if failures.has_failures():
        details = failures.format()
        details += _format_runtime_coverage(
            contract=contract,
            entries_by_name=entries_by_name,
            observed_root_obf_messages=observed_root_obf_messages,
            missing_fields=failures.missing_fields,
        )
        if failures.missing_messages:
            details += _format_unmapped_observed(unmapped_observed_messages)
        details += _format_pinned_exceptions(pinned_message_exceptions, pinned_field_exceptions)
        raise AutoModeMappingContractError(details)

    return AutoModeMappingAudit(
        message_count=len(contract.messages),
        field_count=field_count,
        pinned_message_exceptions=tuple(pinned_message_exceptions),
        pinned_field_exceptions=tuple(pinned_field_exceptions),
        messages_without_runtime_confidence=messages_without_runtime_confidence,
        messages_without_evidence=messages_without_evidence,
        unmapped_observed_messages=unmapped_observed_messages,
        observed_contract_messages=sum(
            1
            for requirement in contract.messages
            if (entry := entries_by_name.get(_normalize_name(requirement.message))) is not None
            and entry.obf_msg_namespace in observed_root_obf_messages
        ),
        runtime_capture_count=sum(
            observed.instance_count for observed in observed_root_obf_messages.values()
        ),
        runtime_session_count=len(
            {
                session_id
                for observed in observed_root_obf_messages.values()
                for session_id in observed.capture_session_ids
            }
        ),
    )


def format_auto_mode_mapping_audit(audit: AutoModeMappingAudit) -> str:
    session_label = "session" if audit.runtime_session_count == 1 else "sessions"
    return (
        "Auto-mode mapping contract passed: "
        f"{audit.message_count} messages, {audit.field_count} fields, "
        f"{len(audit.pinned_message_exceptions)} pinned message exceptions, "
        f"{len(audit.pinned_field_exceptions)} pinned field exceptions. "
        "Diagnostics only: "
        f"{audit.messages_without_runtime_confidence} messages without runtime confidence, "
        f"{audit.messages_without_evidence} messages never observed in a capture, "
        f"{len(audit.unmapped_observed_messages)} captured classes nothing claimed. "
        f"Runtime coverage: {audit.observed_contract_messages}/{audit.message_count} contract messages, "
        f"{audit.runtime_capture_count} root captures across "
        f"{audit.runtime_session_count} {session_label}."
    )


def _format_runtime_coverage(
    *,
    contract: AutoModeMappingContract,
    entries_by_name: Mapping[str, GameMappingEntry],
    observed_root_obf_messages: Mapping[str, ObservedRootObfMessage],
    missing_fields: list[str],
) -> str:
    observed_contract_messages = sum(
        1
        for requirement in contract.messages
        if (entry := entries_by_name.get(_normalize_name(requirement.message))) is not None
        and entry.obf_msg_namespace in observed_root_obf_messages
    )
    runtime_capture_count = sum(
        observed.instance_count for observed in observed_root_obf_messages.values()
    )
    runtime_session_count = len(
        {
            session_id
            for observed in observed_root_obf_messages.values()
            for session_id in observed.capture_session_ids
        }
    )
    missing_fields_with_captures = 0
    missing_fields_without_captures = 0
    for requirement in contract.messages:
        entry = entries_by_name.get(_normalize_name(requirement.message))
        if entry is None:
            continue
        has_capture = entry.obf_msg_namespace in observed_root_obf_messages
        for field_name in requirement.fields:
            failure_prefix = f"{requirement.message}.{field_name}"
            if not any(
                failure == failure_prefix or failure.startswith(failure_prefix + " ")
                for failure in missing_fields
            ):
                continue
            if has_capture:
                missing_fields_with_captures += 1
            else:
                missing_fields_without_captures += 1
    session_label = "session" if runtime_session_count == 1 else "sessions"
    return (
        "\n- runtime coverage: "
        f"{observed_contract_messages}/{len(contract.messages)} contract messages observed, "
        f"{len(observed_root_obf_messages)} obfuscated classes, {runtime_capture_count} root captures, "
        f"{runtime_session_count} {session_label} with session IDs; "
        f"required-field gaps: {missing_fields_with_captures} with captures, "
        f"{missing_fields_without_captures} without captures"
    )


def _format_unmapped_observed(unmapped_observed_messages: tuple[str, ...]) -> str:
    if not unmapped_observed_messages:
        return ""
    lines = [
        f"\n- captured classes nothing claimed ({len(unmapped_observed_messages)}):",
        *(f"  - {message}" for message in unmapped_observed_messages),
    ]
    return "\n".join(lines)


def _format_pinned_exceptions(
    pinned_message_exceptions: list[str],
    pinned_field_exceptions: list[str],
) -> str:
    lines: list[str] = []
    if pinned_message_exceptions:
        lines.append(f"\n- accepted pinned message exceptions ({len(pinned_message_exceptions)}):")
        lines.extend(f"  - {message}" for message in pinned_message_exceptions)
    if pinned_field_exceptions:
        lines.append(f"\n- accepted pinned field exceptions ({len(pinned_field_exceptions)}):")
        lines.extend(f"  - {field_name}" for field_name in pinned_field_exceptions)
    return "\n".join(lines)


def _check_message_scores(
    message: str,
    entry: GameMappingEntry,
    pinned_pair: PinnedPair | None,
    contract: AutoModeMappingContract,
    failures: _AuditFailures,
    pinned_exceptions: list[str],
) -> None:
    score_failed = entry.similarity_score < contract.thresholds.message_score
    margin_failed = entry.match_margin < contract.thresholds.match_margin
    confidence_failed = entry.is_low_confidence
    if pinned_pair is not None:
        if score_failed or margin_failed or confidence_failed:
            pinned_exceptions.append(message)
        return
    if score_failed:
        failures.low_message_scores.append(
            f"{message}: {entry.similarity_score:.3f} < {contract.thresholds.message_score:.3f}"
        )
    if margin_failed:
        margin_detail = f"{message}: {entry.match_margin:.3f} < {contract.thresholds.match_margin:.3f}"
        if entry.evidence_coverage is None:
            failures.fieldless_low_match_margins.append(f"{margin_detail} (no declared fields)")
        else:
            failures.low_match_margins.append(margin_detail)
    if confidence_failed:
        failures.low_confidence_messages.append(message)


def _collect_unmapped_observed_messages(
    observed_root_obf_messages: Mapping[str, ObservedRootObfMessage],
    entries: Iterable[GameMappingEntry],
) -> tuple[str, ...]:
    mapped_obf_namespaces = {entry.obf_msg_namespace for entry in entries}
    return tuple(
        observed.describe()
        for obf_msg_namespace, observed in sorted(observed_root_obf_messages.items())
        if obf_msg_namespace not in mapped_obf_namespaces
    )


def _check_fields(
    message: str,
    required_fields: list[str],
    entry: GameMappingEntry,
    pinned_pair: PinnedPair | None,
    contract: AutoModeMappingContract,
    failures: _AuditFailures,
    pinned_exceptions: list[str],
) -> None:
    obf_field_by_non_obf = {non_obf: obf for obf, non_obf in entry.field_mapping.items()}
    pinned_obf_by_non_obf = pinned_pair.field_mapping_by_non_obf if pinned_pair is not None else {}
    for required_field in required_fields:
        obf_field = obf_field_by_non_obf.get(required_field)
        if obf_field is None:
            failures.missing_fields.append(f"{message}.{required_field}")
            continue
        field_score = entry.field_mapping_infos.get(obf_field, {}).get(required_field)
        if field_score is None:
            failures.missing_fields.append(f"{message}.{required_field} (missing score)")
            continue
        if field_score >= contract.thresholds.field_score:
            continue
        if pinned_obf_by_non_obf.get(required_field) == obf_field:
            pinned_exceptions.append(f"{message}.{required_field}")
            continue
        failures.low_field_scores.append(
            f"{message}.{required_field}: {field_score:.3f} < {contract.thresholds.field_score:.3f}"
        )


def _normalize_name(value: str) -> str:
    return value.removeprefix(".").casefold()

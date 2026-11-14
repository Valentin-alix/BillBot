"""
Rank the obfuscated groups a non-obfuscated `.proto` could belong to, and say who holds them.

The group level is where the starved files are stuck: `breach.proto` has 22 messages and 8
mappings, spread over 8 unrelated obfuscated groups. Yet the file partition survives a rebuild --
94% of the verified pins land in the plurality obfuscated group of their `.proto` -- so a scattered
file means the mappings are wrong, not that the file moved.

What blocks those files is that every plausible group is already taken. This script measures how
much that claim is worth: a group held on 18 messages and zero pin is an algorithm's guess, one
held on 6 messages and 4 pins is ground truth. Only the first kind is worth contesting.

Anchoring two or three messages by hand is then enough: the file-descriptor affinity propagates the
rest and evicts the wrong claimant on its own.

Nothing is written.
"""

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from DBDofusUnity.consts import (
    GAME_MAPPINGS_DETAILED_JSON_FILE,
    PINNED_PAIRS_FILE,
)
from DBDofusUnity.proto_mapper_assembly.affinities._group_similarity import build_group_scores_matrix
from DBDofusUnity.proto_mapper_assembly.controllers.access_signatures import (
    count_handler_registrations_by_cls,
)
from DBDofusUnity.proto_mapper_assembly.controllers.game_mappings import load_game_mappings_document
from DBDofusUnity.proto_mapper_assembly.controllers.matching_inputs_loader import load_matching_inputs
from DBDofusUnity.proto_mapper_assembly.controllers.pinned_pairs import load_pinned_pairs
from DBDofusUnity.proto_mapper_assembly.helpers.non_obf_names import build_filtered_message_namespace
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage
from DBDofusUnity.proto_mapper_assembly.interfaces.matching import MatchingWorkspace
from DBDofusUnity.proto_mapper_assembly.interfaces.matching_inputs import MatchingInputs
from DBDofusUnity.proto_mapper_assembly.interfaces.pinned_pairs import PinnedPairsConfig
from DBDofusUnity.proto_mapper_assembly.matching.static_scores import build_static_score_data
from DBDofusUnity.proto_mapper_assembly.matching.workspace import build_matching_workspace
from DBDofusUnity.proto_mapper_assembly.runtime.runtime_store import RuntimeDataStore
from DBDofusUnity.proto_mapper_assembly.scoring.message_scoring import StructureSimilarityContext

_MIN_GROUP_COHERENCE = 0.90

_SOLID_CLAIM_PIN_COUNT = 3

_DEFAULT_CANDIDATE_COUNT = 6


@dataclass(frozen=True, slots=True)
class GroupClaim:
    non_obf_descriptor: str
    message_count: int
    pinned_count: int


@dataclass(frozen=True, slots=True)
class ObfGroupState:
    obf_descriptor: str
    size: int
    claims: tuple[GroupClaim, ...]

    @property
    def pinned_count(self) -> int:
        return sum(claim.pinned_count for claim in self.claims)

    @property
    def claimed_count(self) -> int:
        return sum(claim.message_count for claim in self.claims)

    @property
    def verdict(self) -> str:
        if self.pinned_count >= _SOLID_CLAIM_PIN_COUNT:
            return "SOLID"
        if self.pinned_count == 0:
            return "UNVERIFIED"
        return "weak"

    def describe_claims(self, limit: int = 3) -> str:
        if not self.claims:
            return "free"
        return ", ".join(
            f"{_short_descriptor(claim.non_obf_descriptor)}={claim.message_count}({claim.pinned_count} pins)"
            for claim in self.claims[:limit]
        )


@dataclass(frozen=True, slots=True)
class GroupAnalysis:
    workspace: MatchingWorkspace
    scores_matrix: np.ndarray
    group_scores_matrix: np.ndarray
    non_obf_descriptors: tuple[str, ...]
    obf_descriptors: tuple[str, ...]
    obf_group_state_by_descriptor: Mapping[str, ObfGroupState]
    obf_cls_by_non_obf_cls: Mapping[str, str]
    pinned_obf_classes: frozenset[str]
    obf_handler_count_by_cls: Mapping[str, int]
    non_obf_handler_count_by_cls: Mapping[str, int]

    def non_obf_rows(self, descriptor: str) -> list[int]:
        index_by_cls = self.workspace.signature_indexes.non_obf_index_by_cls
        return [
            index_by_cls[signature.message_cls] for signature in self.workspace.non_obf_groups[descriptor]
        ]

    def obf_cols(self, descriptor: str) -> list[int]:
        index_by_cls = self.workspace.signature_indexes.obf_index_by_cls
        return [index_by_cls[signature.message_cls] for signature in self.workspace.obf_groups[descriptor]]

    def coherence(self, descriptor: str) -> tuple[float, str, int] | None:
        landings: dict[str, int] = {}
        for signature in self.workspace.non_obf_groups[descriptor]:
            obf_cls = self.obf_cls_by_non_obf_cls.get(signature.message_cls)
            if obf_cls is None:
                continue
            obf_descriptor = self.workspace.obf_signatures_by_cls[obf_cls].file_descriptor
            landings[obf_descriptor] = landings.get(obf_descriptor, 0) + 1
        if not landings:
            return None
        dominant, dominant_count = max(landings.items(), key=lambda item: item[1])
        return dominant_count / sum(landings.values()), dominant, sum(landings.values())


def main() -> None:
    argument_parser = _build_argument_parser()
    arguments = argument_parser.parse_args()
    if not (arguments.incoherent or arguments.non_obf or arguments.obf):
        argument_parser.error("choose one of --incoherent, --non-obf or --obf")

    analysis = build_group_analysis(load_matching_inputs())

    if arguments.incoherent:
        _print_incoherent_report(analysis)
    if arguments.non_obf is not None:
        descriptor = _resolve_non_obf_descriptor(arguments.non_obf, analysis.non_obf_descriptors)
        _print_non_obf_report(
            analysis,
            descriptor=descriptor,
            candidate_count=arguments.top,
            with_members=bool(arguments.members),
        )
    if arguments.obf is not None:
        _print_obf_report(analysis, obf_descriptor=arguments.obf)


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--incoherent",
        action="store_true",
        help="List the non-obf descriptors whose mappings are split across obf groups.",
    )
    parser.add_argument(
        "--non-obf",
        dest="non_obf",
        default=None,
        help="Non-obf file descriptor to inspect, short or full form, e.g. Breach.",
    )
    parser.add_argument(
        "--obf",
        default=None,
        help="Obfuscated group to inspect, e.g. izp. Shows who claims it and on what evidence.",
    )
    parser.add_argument(
        "--members",
        action="store_true",
        help="With --non-obf, print the message-level assignment inside the best candidate group.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=_DEFAULT_CANDIDATE_COUNT,
        help=f"How many candidate groups to print (default {_DEFAULT_CANDIDATE_COUNT}).",
    )
    return parser


def build_group_analysis(inputs: MatchingInputs) -> GroupAnalysis:
    """Exclude pins from scoring; report them separately as evidence for each claim."""
    workspace = build_matching_workspace(
        obf_signatures=list(inputs.obf_signatures_by_cls.values()),
        non_obf_signatures=list(inputs.non_obf_signatures_by_cls.values()),
        obf_messages_by_cls=inputs.obf_messages_by_cls,
        non_obf_messages_by_cls=inputs.non_obf_messages_by_cls,
    )
    structure_context = StructureSimilarityContext(
        left_signatures_by_cls=inputs.obf_signatures_by_cls,
        right_signatures_by_cls=inputs.non_obf_signatures_by_cls,
        left_enum_signatures_by_name=dict(inputs.obf_enum_signatures_by_name),
        right_enum_signatures_by_name=dict(inputs.non_obf_enum_signatures_by_name),
        left_access_trace=inputs.obf_access_trace,
        right_access_trace=inputs.non_obf_access_trace,
    )
    scores_matrix = build_static_score_data(
        obf_signatures=workspace.obf_signatures,
        non_obf_signatures=workspace.non_obf_signatures,
        pinned_pairs_config=PinnedPairsConfig(pairs=[]),
        runtime_data_store=RuntimeDataStore(),
        structure_context=structure_context,
    ).static_scores_matrix

    non_obf_descriptors = workspace.non_obf_group_descriptors
    obf_descriptors = workspace.obf_group_descriptors
    index_by_cls = workspace.signature_indexes
    group_scores_matrix = build_group_scores_matrix(
        base_scores_matrix=scores_matrix,
        non_obf_groups=[
            [index_by_cls.non_obf_index_by_cls[s.message_cls] for s in workspace.non_obf_groups[descriptor]]
            for descriptor in non_obf_descriptors
        ],
        obf_groups=[
            [index_by_cls.obf_index_by_cls[s.message_cls] for s in workspace.obf_groups[descriptor]]
            for descriptor in obf_descriptors
        ],
    )

    obf_cls_by_non_obf_cls = _build_current_mappings(workspace, inputs.non_obf_messages_by_cls)
    pinned_obf_classes = frozenset(pair.obf for pair in load_pinned_pairs(PINNED_PAIRS_FILE).pairs)
    return GroupAnalysis(
        workspace=workspace,
        scores_matrix=scores_matrix,
        group_scores_matrix=group_scores_matrix,
        non_obf_descriptors=non_obf_descriptors,
        obf_descriptors=obf_descriptors,
        obf_group_state_by_descriptor=_build_obf_group_states(
            workspace=workspace,
            obf_cls_by_non_obf_cls=obf_cls_by_non_obf_cls,
            pinned_obf_classes=pinned_obf_classes,
        ),
        obf_cls_by_non_obf_cls=obf_cls_by_non_obf_cls,
        pinned_obf_classes=pinned_obf_classes,
        obf_handler_count_by_cls=count_handler_registrations_by_cls(inputs.obf_access_trace),
        non_obf_handler_count_by_cls=count_handler_registrations_by_cls(inputs.non_obf_access_trace),
    )


def _build_current_mappings(
    workspace: MatchingWorkspace, non_obf_messages_by_cls: Mapping[str, DumpCSMessage]
) -> dict[str, str]:
    """Index filtered namespaces without C# Types wrappers so nested mappings resolve."""
    cls_by_namespace: dict[str, str] = {}
    for message_cls in workspace.non_obf_signatures_by_cls:
        cls_by_namespace.setdefault(f".{message_cls}".casefold(), message_cls)
        message = non_obf_messages_by_cls.get(message_cls)
        if message is None:
            continue
        filtered = build_filtered_message_namespace(
            is_obf=False, message=message, messages_by_cls=non_obf_messages_by_cls
        )
        cls_by_namespace.setdefault(filtered.casefold(), message_cls)
    obf_cls_by_non_obf_cls: dict[str, str] = {}
    for mapping_key, entry in load_game_mappings_document(GAME_MAPPINGS_DETAILED_JSON_FILE).root.items():
        non_obf_cls = cls_by_namespace.get(mapping_key.casefold()) or cls_by_namespace.get(
            entry.full_non_obf_msg_namespace.casefold()
        )
        if non_obf_cls is not None and entry.full_obf_msg_namespace in workspace.obf_signatures_by_cls:
            obf_cls_by_non_obf_cls[non_obf_cls] = entry.full_obf_msg_namespace
    return obf_cls_by_non_obf_cls


def _build_obf_group_states(
    *,
    workspace: MatchingWorkspace,
    obf_cls_by_non_obf_cls: Mapping[str, str],
    pinned_obf_classes: frozenset[str],
) -> dict[str, ObfGroupState]:
    counters: dict[str, dict[str, list[int]]] = {descriptor: {} for descriptor in workspace.obf_groups}
    for non_obf_signature in workspace.non_obf_signatures:
        obf_cls = obf_cls_by_non_obf_cls.get(non_obf_signature.message_cls)
        if obf_cls is None:
            continue
        obf_descriptor = workspace.obf_signatures_by_cls[obf_cls].file_descriptor
        slot = counters[obf_descriptor].setdefault(non_obf_signature.file_descriptor, [0, 0])
        slot[0] += 1
        if obf_cls in pinned_obf_classes:
            slot[1] += 1

    states: dict[str, ObfGroupState] = {}
    for descriptor, group in workspace.obf_groups.items():
        claims = tuple(
            GroupClaim(non_obf_descriptor=non_obf_descriptor, message_count=counts[0], pinned_count=counts[1])
            for non_obf_descriptor, counts in sorted(
                counters[descriptor].items(), key=lambda item: -item[1][0]
            )
        )
        states[descriptor] = ObfGroupState(obf_descriptor=descriptor, size=len(group), claims=claims)
    return states


def _resolve_non_obf_descriptor(requested: str, descriptors: Sequence[str]) -> str:
    folded = requested.casefold()
    exact = [descriptor for descriptor in descriptors if descriptor.casefold() == folded]
    if exact:
        return exact[0]
    prefixed = [descriptor for descriptor in descriptors if descriptor.casefold() == f"{folded}reflection"]
    if prefixed:
        return prefixed[0]
    partial = [descriptor for descriptor in descriptors if folded in descriptor.casefold()]
    if len(partial) == 1:
        return partial[0]
    if partial:
        message = f"Ambiguous descriptor {requested!r}: {', '.join(sorted(partial)[:8])}"
        raise SystemExit(message)
    message = f"Unknown non-obf file descriptor: {requested}"
    raise SystemExit(message)


def _print_incoherent_report(analysis: GroupAnalysis) -> None:
    rows: list[tuple[float, str, str, int]] = []
    for descriptor in analysis.non_obf_descriptors:
        coherence = analysis.coherence(descriptor)
        if coherence is None:
            continue
        share, dominant, mapping_count = coherence
        if share < _MIN_GROUP_COHERENCE:
            rows.append((share, descriptor, dominant, mapping_count))

    print(f"{len(rows)} descriptor(s) below {_MIN_GROUP_COHERENCE:.2f} coherence.\n")
    print(f"{'score':>6}  {'descriptor':<28} {'plurality':<10} {'mapped':>6} {'members':>8}")
    for share, descriptor, dominant, mapping_count in sorted(rows):
        member_count = len(analysis.workspace.non_obf_groups[descriptor])
        print(
            f"{share:>6.2f}  {_short_descriptor(descriptor):<28} {dominant:<8}"
            f" {mapping_count:>7} {member_count:>8}"
        )
    print("\n'mapped' is the score denominator: at 2 or 3 it can only ever be 0.50 or 0.33.")


def _print_non_obf_report(
    analysis: GroupAnalysis,
    *,
    descriptor: str,
    candidate_count: int,
    with_members: bool,
) -> None:
    rows = analysis.non_obf_rows(descriptor)
    coherence = analysis.coherence(descriptor)
    mapped = sum(
        1
        for signature in analysis.workspace.non_obf_groups[descriptor]
        if signature.message_cls in analysis.obf_cls_by_non_obf_cls
    )
    pinned = sum(
        1
        for signature in analysis.workspace.non_obf_groups[descriptor]
        if analysis.obf_cls_by_non_obf_cls.get(signature.message_cls) in analysis.pinned_obf_classes
    )
    print(f"### {descriptor}")
    print(f"    {len(rows)} messages, {mapped} mapped, {pinned} pinned", end="")
    if coherence is not None:
        print(f", coherence {coherence[0]:.2f} towards {coherence[1]}")
    else:
        print()

    descriptor_index = analysis.non_obf_descriptors.index(descriptor)
    ranked = sorted(
        (
            (float(analysis.group_scores_matrix[descriptor_index, obf_index]), obf_descriptor)
            for obf_index, obf_descriptor in enumerate(analysis.obf_descriptors)
        ),
        reverse=True,
    )[:candidate_count]

    print("\n    candidate obf groups (hungarian alignment / max(n,m)):")
    for score, obf_descriptor in ranked:
        state = analysis.obf_group_state_by_descriptor[obf_descriptor]
        obf_index = analysis.obf_descriptors.index(obf_descriptor)
        best_index = int(np.argmax(analysis.group_scores_matrix[:, obf_index]))
        best_claimant = analysis.non_obf_descriptors[best_index]
        best_score = float(analysis.group_scores_matrix[best_index, obf_index])
        marker = "  <== this file is the best placed" if best_claimant == descriptor else ""
        print(
            f"      {score:.3f}  {obf_descriptor:<6} size={state.size:>3}"
            f" claimed={state.claimed_count:>3} [{state.verdict:<7}] {state.describe_claims()}"
        )
        print(
            f"              best global claimant: "
            f"{_short_descriptor(best_claimant)} @{best_score:.3f}{marker}"
        )

    if with_members and ranked:
        _print_member_assignment(analysis, descriptor=descriptor, obf_descriptor=ranked[0][1])


def _print_member_assignment(analysis: GroupAnalysis, *, descriptor: str, obf_descriptor: str) -> None:
    rows = analysis.non_obf_rows(descriptor)
    cols = analysis.obf_cols(obf_descriptor)
    submatrix = analysis.scores_matrix[np.ix_(rows, cols)]
    row_indexes, col_indexes = linear_sum_assignment(submatrix, maximize=True)

    non_obf_by_index = {
        analysis.workspace.signature_indexes.non_obf_index_by_cls[s.message_cls]: s
        for s in analysis.workspace.non_obf_signatures
    }
    obf_by_index = {
        analysis.workspace.signature_indexes.obf_index_by_cls[s.message_cls]: s
        for s in analysis.workspace.obf_signatures
    }

    print(f"\n    proposed assignment inside {obf_descriptor} (P = pinned, h = handlers):")
    assignments = sorted(
        zip(row_indexes, col_indexes, strict=True),
        key=lambda pair: -submatrix[pair[0], pair[1]],
    )
    for row, col in assignments:
        non_obf_signature = non_obf_by_index[rows[row]]
        obf_signature = obf_by_index[cols[col]]
        current = analysis.obf_cls_by_non_obf_cls.get(non_obf_signature.message_cls)
        current_descriptor = (
            analysis.workspace.obf_signatures_by_cls[current].file_descriptor if current else "-"
        )
        pin = "P" if current in analysis.pinned_obf_classes else " "
        change = "=" if current == obf_signature.message_cls else "CHANGE"
        handlers = (
            f"h{analysis.non_obf_handler_count_by_cls.get(non_obf_signature.message_cls, 0)}"
            f"/h{analysis.obf_handler_count_by_cls.get(obf_signature.message_cls, 0)}"
        )
        print(
            f"      [{pin}] {float(submatrix[row, col]):.3f} {_short_name(non_obf_signature.message_cls):<42}"
            f" -> {obf_signature.message_cls:<14} {handlers:<8}"
            f" current={current or '-'}@{current_descriptor} {change}"
        )
    assigned_rows = {int(row) for row in row_indexes}
    for row in range(len(rows)):
        if row in assigned_rows:
            continue
        non_obf_signature = non_obf_by_index[rows[row]]
        print(
            f"      [ ] ----- {_short_name(non_obf_signature.message_cls):<42}"
            f" -> (no room left in {obf_descriptor})"
        )


def _print_obf_report(analysis: GroupAnalysis, *, obf_descriptor: str) -> None:
    state = analysis.obf_group_state_by_descriptor.get(obf_descriptor)
    if state is None:
        message = f"Unknown obfuscated group: {obf_descriptor}"
        raise SystemExit(message)

    print(f"### {obf_descriptor}: {state.size} members, {state.claimed_count} claimed")
    print(f"    verdict: {state.verdict} ({state.pinned_count} pin(s) in total)\n")
    print(f"    {'claiming .proto':<30} {'messages':>8} {'pins':>5}")
    for claim in state.claims:
        print(
            f"    {_short_descriptor(claim.non_obf_descriptor):<30}"
            f" {claim.message_count:>8} {claim.pinned_count:>5}"
        )
    if not state.claims:
        print("    (none)")

    obf_index = analysis.obf_descriptors.index(obf_descriptor)
    ranked = sorted(
        (
            (float(analysis.group_scores_matrix[non_obf_index, obf_index]), non_obf_descriptor)
            for non_obf_index, non_obf_descriptor in enumerate(analysis.non_obf_descriptors)
        ),
        reverse=True,
    )[:_DEFAULT_CANDIDATE_COUNT]
    print("\n    best aligned claimants:")
    for score, non_obf_descriptor in ranked:
        member_count = len(analysis.workspace.non_obf_groups[non_obf_descriptor])
        print(f"      {score:.3f}  {_short_descriptor(non_obf_descriptor):<28} {member_count:>3} members")


def _short_descriptor(descriptor: str) -> str:
    return descriptor.removesuffix("Reflection")


def _short_name(message_cls: str) -> str:
    return message_cls.rsplit(".", maxsplit=1)[-1]


if __name__ == "__main__":
    main()

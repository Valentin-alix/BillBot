"""Process-local experiments; never expose ablation switches in the production matcher."""

from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import numpy as np

from DBDofusUnity.proto_mapper_assembly.field_mapping import field_mapping_scoring
from DBDofusUnity.proto_mapper_assembly.interfaces.capture_sequence_hints import CaptureSequenceHintsConfig
from DBDofusUnity.proto_mapper_assembly.interfaces.capture_sequence_order import CaptureOrderIndex
from DBDofusUnity.proto_mapper_assembly.interfaces.function_access_signature import FunctionSimilarityKey
from DBDofusUnity.proto_mapper_assembly.interfaces.matching import MatchingWorkspace
from DBDofusUnity.proto_mapper_assembly.matching import score_constraints, score_preparation
from DBDofusUnity.proto_mapper_assembly.matching.iterative_store import IterativeMatchingStore
from DBDofusUnity.proto_mapper_assembly.scoring import enum_similarity, message_scoring, signature_scoring
from DBDofusUnity.proto_mapper_assembly.scoring.primitives import (
    counter_profile_overlap_similarity,
    ratio_similarity,
)
from scripts.mapping import export_signature_overrides

ABLATION_SIGNALS = (
    "opcode_histogram",
    "foreign_access",
    "function_size",
    "handler_registration",
    "handler_cohort",
    "declaration_order",
    "callee",
    "file_descriptor",
    "validation_bonus",
    "runtime_alive_bonus",
    "oneof_partition_penalty",
    "capture_sequence_order",
)


@contextmanager
def ablate_signals(disabled: frozenset[str]) -> Generator[None, None, None]:
    """Run one variant per process so memoized scores cannot leak between experiments."""
    unknown = disabled.difference(ABLATION_SIGNALS)
    if unknown:
        raise ValueError(f"Unknown ablation signals: {sorted(unknown)}")
    with ExitStack() as stack:
        if disabled.intersection(("opcode_histogram", "foreign_access", "function_size")):

            def score_function(left: FunctionSimilarityKey, right: FunctionSimilarityKey) -> float:
                return _score_function(left, right, disabled)

            for module in (signature_scoring, message_scoring, enum_similarity, export_signature_overrides):
                stack.enter_context(patch.object(module, "function_similarity_from_keys", score_function))
        stack.enter_context(
            patch.object(
                score_preparation,
                "_MASKED_AFFINITY_SIGNALS",
                tuple(
                    signal
                    for signal in score_preparation._MASKED_AFFINITY_SIGNALS
                    if signal.name not in disabled
                ),
            )
        )
        for name, module, attribute in (
            ("handler_registration", score_preparation, "_HANDLER_REGISTRATION_SIMILARITY_WEIGHT"),
            ("file_descriptor", score_preparation, "_FILE_DESCRIPTOR_SIMILARITY_WEIGHT"),
            ("validation_bonus", field_mapping_scoring, "_VALIDATION_BONUS"),
            ("runtime_alive_bonus", field_mapping_scoring, "_RUNTIME_ALIVE_FIELD_BONUS"),
        ):
            if name in disabled:
                stack.enter_context(patch.object(module, attribute, 0.0))
        if "oneof_partition_penalty" in disabled:
            stack.enter_context(patch.object(message_scoring, "_ONEOF_PARTITION_MISMATCH_FLOOR", 1.0))
        if "capture_sequence_order" in disabled:
            stack.enter_context(
                patch.object(score_constraints, "apply_capture_sequence_order_scores", _skip_capture_order_scores)
            )
        yield


def _score_function(
    left: FunctionSimilarityKey, right: FunctionSimilarityKey, disabled: frozenset[str]
) -> float:
    if left.return_role != right.return_role or left.takes_message_parameter != right.takes_message_parameter:
        return 0.0
    score = 0.85 * signature_scoring.access_atom_sequence_similarity(left.self_accesses, right.self_accesses)
    weight = 0.85
    if "opcode_histogram" not in disabled:
        score += 0.05 * counter_profile_overlap_similarity(
            left=signature_scoring._opcode_histogram_profile(left.opcode_histogram),
            right=signature_scoring._opcode_histogram_profile(right.opcode_histogram),
        )
        weight += 0.05
    if "foreign_access" not in disabled:
        score += 0.05 * counter_profile_overlap_similarity(
            left=signature_scoring._foreign_access_profile(left.foreign_access_summary),
            right=signature_scoring._foreign_access_profile(right.foreign_access_summary),
        )
        weight += 0.05
    if "function_size" not in disabled:
        score += 0.05 * ratio_similarity(left.size, right.size, max_value=max(left.size, right.size))
        weight += 0.05
    return score / weight


def _skip_capture_order_scores(
    *,
    workspace: MatchingWorkspace,
    scores_matrix: np.ndarray,
    matching_store: IterativeMatchingStore,
    capture_order_index: CaptureOrderIndex,
    capture_sequence_hints_config: CaptureSequenceHintsConfig,
) -> None:
    del workspace, scores_matrix, matching_store, capture_order_index, capture_sequence_hints_config

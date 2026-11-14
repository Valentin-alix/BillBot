from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from DBDofusUnity.consts import (
    AUTO_MODE_MAPPING_CONTRACT_FILE,
    CAPTURE_SEQUENCE_HINTS_FILE,
    GAME_MAPPINGS_DETAILED_JSON_FILE,
    GAME_MAPPINGS_JSON_FILE,
    NON_OBF_NEW_DUMP_CS_FILE,
    NON_OBF_PROTO_ACCESSES_FILE,
    NON_OBF_PROTOCOL_GAME_DUMP_CS_FILE,
    NON_OBF_SIGNATURE_OVERRIDES_FILE,
    OBF_PROTO_ACCESSES_FILE,
    OBF_PROTOCOL_GAME_DUMP_CS_FILE,
    PINNED_PAIRS_FILE,
)
from DBDofusUnity.proto_mapper_assembly.controllers.capture_sequence_hints import (
    load_capture_sequence_hints,
    resolve_capture_sequence_hints_non_obf_targets,
)
from DBDofusUnity.proto_mapper_assembly.controllers.game_mappings import write_game_mappings
from DBDofusUnity.proto_mapper_assembly.controllers.matching_inputs_loader import load_matching_inputs
from DBDofusUnity.proto_mapper_assembly.controllers.new_dump_cs import load_new_dump_cs_messages
from DBDofusUnity.proto_mapper_assembly.controllers.pinned_pairs import (
    load_pinned_pairs,
    resolve_pinned_pairs_non_obf_targets,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage
from DBDofusUnity.proto_mapper_assembly.interfaces.matching import MatchResult
from DBDofusUnity.proto_mapper_assembly.interfaces.matching_inputs import MatchingRunConfig
from DBDofusUnity.proto_mapper_assembly.interfaces.pinned_pairs import PinnedPairsConfig
from DBDofusUnity.proto_mapper_assembly.matching.orchestrator import match_messages
from DBDofusUnity.proto_mapper_assembly.runtime.runtime_store import RuntimeDataStore
from DBDofusUnity.proto_mapper_assembly.validators.auto_mode_mapping_contract import (
    AutoModeMappingContractError,
    check_auto_mode_mappings,
    format_auto_mode_mapping_audit,
)


@dataclass(frozen=True)
class PipelinePaths:
    auto_mode_mapping_contract_path: Path
    obf_dump_cs_path: Path
    non_obf_dump_cs_path: Path
    obf_proto_accesses_path: Path
    non_obf_proto_accesses_path: Path
    bootstrap_non_obf_dump_cs_path: Path
    signature_overrides_path: Path
    pinned_pairs_path: Path
    capture_sequence_hints_path: Path
    game_mappings_path: Path
    detailed_game_mappings_path: Path


def build_pipeline_paths(*, obf_dir: Path | None = None) -> PipelinePaths:
    if obf_dir is None:
        return PipelinePaths(
            auto_mode_mapping_contract_path=AUTO_MODE_MAPPING_CONTRACT_FILE,
            obf_dump_cs_path=OBF_PROTOCOL_GAME_DUMP_CS_FILE,
            non_obf_dump_cs_path=NON_OBF_PROTOCOL_GAME_DUMP_CS_FILE,
            obf_proto_accesses_path=OBF_PROTO_ACCESSES_FILE,
            non_obf_proto_accesses_path=NON_OBF_PROTO_ACCESSES_FILE,
            bootstrap_non_obf_dump_cs_path=NON_OBF_NEW_DUMP_CS_FILE,
            signature_overrides_path=NON_OBF_SIGNATURE_OVERRIDES_FILE,
            pinned_pairs_path=PINNED_PAIRS_FILE,
            capture_sequence_hints_path=CAPTURE_SEQUENCE_HINTS_FILE,
            game_mappings_path=GAME_MAPPINGS_JSON_FILE,
            detailed_game_mappings_path=GAME_MAPPINGS_DETAILED_JSON_FILE,
        )

    return PipelinePaths(
        auto_mode_mapping_contract_path=AUTO_MODE_MAPPING_CONTRACT_FILE,
        obf_dump_cs_path=obf_dir / "cs" / "Ankama.Dofus.Protocol.Game.cs",
        non_obf_dump_cs_path=NON_OBF_PROTOCOL_GAME_DUMP_CS_FILE,
        obf_proto_accesses_path=obf_dir / "proto_accesses.json",
        non_obf_proto_accesses_path=NON_OBF_PROTO_ACCESSES_FILE,
        bootstrap_non_obf_dump_cs_path=NON_OBF_NEW_DUMP_CS_FILE,
        signature_overrides_path=obf_dir / "proto_accesses.override.json",
        pinned_pairs_path=obf_dir / "pinned_pairs.json",
        capture_sequence_hints_path=obf_dir / "capture_sequence_hints.json",
        game_mappings_path=obf_dir / "game_mappings.json",
        detailed_game_mappings_path=obf_dir / "game_mappings_detailed.json",
    )


def run_pipeline(*, do_load_pinned_pair: bool, obf_dir: Path | None = None) -> None:
    pipeline_paths = build_pipeline_paths(obf_dir=obf_dir)
    runtime_data_store = RuntimeDataStore()
    matching_inputs = load_matching_inputs(
        obf_dump_cs_path=pipeline_paths.obf_dump_cs_path,
        non_obf_dump_cs_path=pipeline_paths.non_obf_dump_cs_path,
        obf_proto_accesses_path=pipeline_paths.obf_proto_accesses_path,
        non_obf_proto_accesses_path=pipeline_paths.non_obf_proto_accesses_path,
        bootstrap_non_obf_dump_cs_path=pipeline_paths.bootstrap_non_obf_dump_cs_path,
        signature_overrides_path=pipeline_paths.signature_overrides_path,
    )
    obf_messages_by_cls = matching_inputs.obf_messages_by_cls
    non_obf_messages_by_cls = matching_inputs.non_obf_messages_by_cls
    bootstrap_messages_by_cls = load_new_dump_cs_messages(pipeline_paths.bootstrap_non_obf_dump_cs_path).root
    pinned_pairs = (
        resolve_pinned_pairs_non_obf_targets(
            pinned_pairs=load_pinned_pairs(pipeline_paths.pinned_pairs_path),
            obf_messages_by_cls=obf_messages_by_cls,
            non_obf_messages_by_cls={**non_obf_messages_by_cls, **bootstrap_messages_by_cls},
        )
        if do_load_pinned_pair
        else PinnedPairsConfig(pairs=[])
    )
    capture_sequence_hints = resolve_capture_sequence_hints_non_obf_targets(
        capture_sequence_hints=load_capture_sequence_hints(pipeline_paths.capture_sequence_hints_path),
        non_obf_messages_by_cls={**non_obf_messages_by_cls, **bootstrap_messages_by_cls},
    )
    matches = match_messages(
        inputs=matching_inputs,
        run_config=MatchingRunConfig(
            runtime_data_store=runtime_data_store,
            pinned_pairs_config=pinned_pairs,
            capture_sequence_hints_config=capture_sequence_hints,
        ),
    )

    publish_game_mappings(
        matches,
        obf_messages_by_cls=obf_messages_by_cls,
        non_obf_messages_by_cls=non_obf_messages_by_cls,
        pipeline_paths=pipeline_paths,
        runtime_data_store=runtime_data_store,
    )


def publish_game_mappings(
    matches: Sequence[MatchResult],
    *,
    obf_messages_by_cls: dict[str, DumpCSMessage],
    non_obf_messages_by_cls: dict[str, DumpCSMessage],
    pipeline_paths: PipelinePaths,
    runtime_data_store: RuntimeDataStore,
) -> None:
    write_game_mappings(
        matches,
        obf_messages_by_cls=obf_messages_by_cls,
        non_obf_messages_by_cls=non_obf_messages_by_cls,
        output_path=pipeline_paths.game_mappings_path,
        detailed_output_path=pipeline_paths.detailed_game_mappings_path,
    )
    try:
        audit = check_auto_mode_mappings(
            contract_path=pipeline_paths.auto_mode_mapping_contract_path,
            detailed_mappings_path=pipeline_paths.detailed_game_mappings_path,
            pinned_pairs_path=pipeline_paths.pinned_pairs_path,
            observed_root_obf_messages=runtime_data_store.get_observed_root_obf_messages(),
        )
    except AutoModeMappingContractError as error:
        print(str(error))
        return
    print(format_auto_mode_mapping_audit(audit))

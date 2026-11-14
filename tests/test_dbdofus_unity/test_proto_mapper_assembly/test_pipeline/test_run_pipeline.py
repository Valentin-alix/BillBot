from pathlib import Path
from unittest.mock import Mock, patch

from tests.fixtures.proto_mapper.message_builders import message_signature
from tests.fixtures.proto_mapper.pipeline_builders import (
    builder_matching_inputs,
    simple_match_result,
)

from DBDofusUnity.consts import (
    NON_OBF_NEW_DUMP_CS_FILE,
    NON_OBF_PROTO_ACCESSES_FILE,
    NON_OBF_PROTOCOL_GAME_DUMP_CS_FILE,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.capture_sequence_hints import CaptureSequenceHintsConfig
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage
from DBDofusUnity.proto_mapper_assembly.interfaces.new_dump_cs import NewDumpCSFile
from DBDofusUnity.proto_mapper_assembly.interfaces.pinned_pairs import PinnedPair, PinnedPairsConfig
from DBDofusUnity.proto_mapper_assembly.pipeline import (
    build_pipeline_paths,
    publish_game_mappings,
    run_pipeline,
)
from DBDofusUnity.proto_mapper_assembly.validators.auto_mode_mapping_contract import (
    AutoModeMappingContractError,
)
from DBDofusUnity.proto_mapper_assembly.runtime.runtime_store import RuntimeDataStore


class TestRunPipeline:
    def test_uses_obf_dir_for_inputs_pins_overrides_and_outputs(self, tmp_path: Path) -> None:
        obf_dir = tmp_path / "20_05_2026"
        obf_message = DumpCSMessage(file_descriptor="GameReflection", name="obf")
        non_obf_message = DumpCSMessage(file_descriptor="GameReflection", name="Clear")
        fake_pinned = PinnedPairsConfig(pairs=[PinnedPair(obf="obf", non_obf="Clear")])
        matches = (simple_match_result(non_obf_cls="Clear", obf_cls="obf", score=0.9),)
        fake_hints = CaptureSequenceHintsConfig(sequences=())

        with (
            patch(
                "DBDofusUnity.proto_mapper_assembly.pipeline.load_matching_inputs",
                return_value=builder_matching_inputs(
                    obf_messages_by_cls={"obf": obf_message},
                    non_obf_messages_by_cls={"Clear": non_obf_message},
                    obf_signatures_by_cls={"obf": message_signature("obf", declared_field_signatures=[])},
                    non_obf_signatures_by_cls={
                        "Clear": message_signature("Clear", declared_field_signatures=[])
                    },
                ),
            ) as mock_load_matching_inputs,
            patch(
                "DBDofusUnity.proto_mapper_assembly.pipeline.load_pinned_pairs", return_value=fake_pinned
            ) as mock_load_pinned_pairs,
            patch(
                "DBDofusUnity.proto_mapper_assembly.pipeline.resolve_pinned_pairs_non_obf_targets",
                return_value=fake_pinned,
            ),
            patch(
                "DBDofusUnity.proto_mapper_assembly.pipeline.load_new_dump_cs_messages",
                return_value=NewDumpCSFile(root={}),
            ) as mock_load_new_dump_cs_messages,
            patch(
                "DBDofusUnity.proto_mapper_assembly.pipeline.load_capture_sequence_hints",
                return_value=fake_hints,
            ),
            patch(
                "DBDofusUnity.proto_mapper_assembly.pipeline.resolve_capture_sequence_hints_non_obf_targets",
                return_value=fake_hints,
            ),
            patch("DBDofusUnity.proto_mapper_assembly.pipeline.match_messages", return_value=matches),
            patch(
                "DBDofusUnity.proto_mapper_assembly.pipeline.publish_game_mappings"
            ) as mock_publish_game_mappings,
        ):
            run_pipeline(do_load_pinned_pair=True, obf_dir=obf_dir)

        mock_load_matching_inputs.assert_called_once_with(
            obf_dump_cs_path=obf_dir / "cs" / "Ankama.Dofus.Protocol.Game.cs",
            non_obf_dump_cs_path=NON_OBF_PROTOCOL_GAME_DUMP_CS_FILE,
            obf_proto_accesses_path=obf_dir / "proto_accesses.json",
            non_obf_proto_accesses_path=NON_OBF_PROTO_ACCESSES_FILE,
            bootstrap_non_obf_dump_cs_path=NON_OBF_NEW_DUMP_CS_FILE,
            signature_overrides_path=obf_dir / "proto_accesses.override.json",
        )
        mock_load_new_dump_cs_messages.assert_called_once_with(NON_OBF_NEW_DUMP_CS_FILE)
        mock_load_pinned_pairs.assert_called_once_with(obf_dir / "pinned_pairs.json")
        mock_publish_game_mappings.assert_called_once()


class TestPublishGameMappings:
    def test_contract_success_publishes_staged_mappings(self, tmp_path: Path) -> None:
        obf_dir = tmp_path / "build"
        obf_dir.mkdir()
        paths = build_pipeline_paths(obf_dir=obf_dir)

        def write_mappings(
            matches: object,
            *,
            output_path: Path,
            detailed_output_path: Path,
            **kwargs: object,
        ) -> None:
            output_path.write_text("new-simple", encoding="utf-8")
            detailed_output_path.write_text("new-detailed", encoding="utf-8")

        with (
            patch(
                "DBDofusUnity.proto_mapper_assembly.pipeline.write_game_mappings", side_effect=write_mappings
            ),
            patch("DBDofusUnity.proto_mapper_assembly.pipeline.check_auto_mode_mappings"),
            patch(
                "DBDofusUnity.proto_mapper_assembly.pipeline.format_auto_mode_mapping_audit",
                return_value="mapping contract passed",
            ),
        ):
            publish_game_mappings(
                (),
                obf_messages_by_cls={},
                non_obf_messages_by_cls={},
                pipeline_paths=paths,
                runtime_data_store=Mock(spec=RuntimeDataStore),
            )

        assert paths.game_mappings_path.read_text(encoding="utf-8") == "new-simple"
        assert paths.detailed_game_mappings_path.read_text(encoding="utf-8") == "new-detailed"
        assert len(list(obf_dir.iterdir())) == 2

    def test_contract_failure_still_publishes_mappings(self, tmp_path: Path) -> None:
        obf_dir = tmp_path / "build"
        obf_dir.mkdir()
        paths = build_pipeline_paths(obf_dir=obf_dir)
        paths.game_mappings_path.write_text("previous-simple", encoding="utf-8")
        paths.detailed_game_mappings_path.write_text("previous-detailed", encoding="utf-8")

        def write_mappings(
            matches: object,
            *,
            output_path: Path,
            detailed_output_path: Path,
            **kwargs: object,
        ) -> None:
            output_path.write_text("published-simple", encoding="utf-8")
            detailed_output_path.write_text("published-detailed", encoding="utf-8")

        with (
            patch(
                "DBDofusUnity.proto_mapper_assembly.pipeline.write_game_mappings", side_effect=write_mappings
            ),
            patch(
                "DBDofusUnity.proto_mapper_assembly.pipeline.check_auto_mode_mappings",
                side_effect=AutoModeMappingContractError("mapping contract failed"),
            ),
            patch("builtins.print") as print_mock,
        ):
            publish_game_mappings(
                (),
                obf_messages_by_cls={},
                non_obf_messages_by_cls={},
                pipeline_paths=paths,
                runtime_data_store=Mock(spec=RuntimeDataStore),
            )

        assert paths.game_mappings_path.read_text(encoding="utf-8") == "published-simple"
        assert paths.detailed_game_mappings_path.read_text(encoding="utf-8") == "published-detailed"
        print_mock.assert_called_once_with("mapping contract failed")
        assert len(list(obf_dir.iterdir())) == 2

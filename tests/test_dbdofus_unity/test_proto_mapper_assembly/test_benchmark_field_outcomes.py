from pathlib import Path

import pytest

from DBDofusUnity.proto_mapper_assembly.interfaces.auto_mode_mapping_contract import AutoModeMappingContract

from DBDofusUnity.proto_mapper_assembly.interfaces.game_mappings import GameMappingsDocument
from DBDofusUnity.proto_mapper_assembly.interfaces.pinned_pairs import PinnedPair, PinnedPairsConfig
from scripts.mapping.debug import benchmark_cross_build as benchmark
from tests.fixtures.proto_mapper.pipeline_builders import detailed_game_mapping_entry


def test_required_fields_detect_wrong_message_and_missing_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        '{"version":3,"thresholds":{"message_score":0.5,"match_margin":0.05,"field_score":0.5},'
        '"messages":[{"message":".Clear","fields":["value","unknown"]}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        benchmark, "generate_auto_mode_mapping_contract",
        lambda: AutoModeMappingContract.model_validate_json(contract_path.read_text(encoding="utf-8")),
    )
    reference = GameMappingsDocument(root={".Clear": detailed_game_mapping_entry("old", {"x": "value"})})
    candidate = GameMappingsDocument(root={".Clear": detailed_game_mapping_entry("wrong", {"x": "value"})})
    messages, unverified, fields = benchmark._score_current_requirements(
        reference_document=reference,
        candidate_document=candidate,
        pinned_pairs=PinnedPairsConfig(pairs=[]),
    )
    assert not messages[0].is_recovered
    assert unverified == ()
    assert [(field.field, field.status) for field in fields] == [
        ("value", "wrong"),
        ("unknown", "unverified"),
    ]

    candidate = GameMappingsDocument(root={".Clear": detailed_game_mapping_entry("new", {"y": "value"})})
    messages, _, fields = benchmark._score_current_requirements(
        reference_document=reference,
        candidate_document=candidate,
        pinned_pairs=PinnedPairsConfig(
            pairs=[
                PinnedPair(
                    obf="new_full",
                    non_obf="Clear",
                    field_mapping_by_obf={"y": "value"},
                )
            ]
        ),
    )
    assert messages[0].is_recovered
    assert fields[0].status == "correct"
    assert fields[0].reference_kind == "pin"
    assert fields[1].status == "unverified"


def test_missing_message_is_not_a_correct_field_match() -> None:
    outcome = benchmark.FieldOutcome(".Clear", "value", "a", None, "x", None, "pin")
    assert outcome.status == "unmapped"


def test_archived_benchmark_uses_current_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        '{"version":3,"thresholds":{"message_score":0.5,"match_margin":0.05,"field_score":0.5},'
        '"messages":[{"message":".protocol.Required"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        benchmark, "generate_auto_mode_mapping_contract",
        lambda: AutoModeMappingContract.model_validate_json(contract_path.read_text(encoding="utf-8")),
    )
    reference_path = tmp_path / benchmark.GAME_MAPPINGS_RELATIVE_PATH
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path.write_text(
        '{".protocol.Required":{"obf_msg_namespace":"old","field_mapping":{}},'
        '".protocol.Unused":{"obf_msg_namespace":"other","field_mapping":{}}}',
        encoding="utf-8",
    )
    outcomes = benchmark._score_bot_used_messages(candidate_document_root={}, build_dir=tmp_path)

    assert [(outcome.non_obf_key, outcome.expected_obf, outcome.actual_obf) for outcome in outcomes] == [
        (".protocol.Required", "old", None)
    ]

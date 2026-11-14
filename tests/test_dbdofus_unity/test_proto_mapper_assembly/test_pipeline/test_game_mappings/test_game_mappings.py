import json
from pathlib import Path

import pytest
from tests.fixtures.proto_mapper.field_builders import dump_cs_field
from tests.fixtures.proto_mapper.message_builders import (
    build_message_lookup,
    message_signature,
)
from tests.fixtures.proto_mapper.pipeline_builders import (
    detailed_game_mapping_entry,
    simple_match_result,
)

from DBDofusUnity.proto_mapper_assembly.controllers.game_mappings import (
    build_full_message_namespace,
    build_game_mappings_document,
    validate_game_mapping_targets,
    write_game_mappings,
)
from DBDofusUnity.proto_mapper_assembly.controllers.excluded_non_obf import (
    compute_excluded_non_obf_names,
    drop_excluded_signature_overrides,
    split_excluded_non_obf_messages,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage
from DBDofusUnity.proto_mapper_assembly.interfaces.field_mapping_rejected_infos import ValidationFailureRejectedInfo
from DBDofusUnity.proto_mapper_assembly.interfaces.game_mappings import GameMappingsDocument
from DBDofusUnity.proto_mapper_assembly.interfaces.matching import MatchResult
from DBDofusUnity.proto_mapper_assembly.interfaces.pinned_pairs import PinnedPair, PinnedPairsConfig
from DBDofusUnity.proto_mapper_assembly.interfaces.runtime_data import ObservedRootObfMessage
from DBDofusUnity.proto_mapper_assembly.validators.auto_mode_mapping_contract import (
    AutoModeMappingContractError,
    check_auto_mode_mappings,
)


pytestmark = pytest.mark.usefixtures("reuse_generated_non_obf_messages")

class TestGameMappings:
    def test_computes_exclusions_from_current_descriptors_and_drops_stale_overrides(self) -> None:
        from DBDofusUnity.consts import PROTOS_ROOT
        from DBDofusUnity.proto_mapper_assembly.interfaces.signature_overrides import SignatureOverrideEntry
        from DBDofusUnity.proto_mapper_assembly.parsers.protobuf_dump_cs import build_dump_cs_messages_from_pb2

        generated_messages = build_dump_cs_messages_from_pb2(PROTOS_ROOT / "non_obf" / "game")
        root = next(message for message in generated_messages.values() if message.parent_name is None)
        nested = next(message for message in generated_messages.values() if message.parent_name is not None)
        stale = DumpCSMessage(file_descriptor="RemovedReflection", name="RemovedMessageForExclusionTest")
        messages = [root, nested, stale]

        excluded_names = compute_excluded_non_obf_names(messages)
        kept, dropped = split_excluded_non_obf_messages(
            messages=messages, excluded_message_names=excluded_names
        )
        override = SignatureOverrideEntry(function_signatures=[], field_signatures={})
        retained_overrides = drop_excluded_signature_overrides(
            overrides={message.composed_name: override for message in messages},
            dropped_message_names=dropped,
        )

        assert excluded_names == frozenset({stale.composed_name})
        assert kept == [root, nested]
        assert set(retained_overrides) == {root.composed_name, nested.composed_name}

    def test_excludes_only_the_requested_non_obf_message(self) -> None:
        excluded_message = DumpCSMessage(
            file_descriptor="GuildInformationReflection",
            name="Excluded",
            namespace="Com.Ankama.Dofus.Server.Game.Protocol.Guild.Information",
        )
        sibling_message = DumpCSMessage(
            file_descriptor="GuildInformationReflection",
            name="Sibling",
            namespace="Com.Ankama.Dofus.Server.Game.Protocol.Guild.Information",
        )

        kept_messages, dropped_names = split_excluded_non_obf_messages(
            messages=[excluded_message, sibling_message],
            excluded_message_names=frozenset({excluded_message.composed_name}),
        )

        assert kept_messages == [sibling_message]
        assert dropped_names == frozenset({excluded_message.composed_name})

    def test_rejects_mapping_target_missing_from_generated_protobuf_descriptors(self) -> None:
        document = GameMappingsDocument(
            root={
                ".com.ankama.dofus.server.game.protocol.game.action.GameActionUpdateEffectTriggerCountEvent.FightEffectTriggerCount": detailed_game_mapping_entry(
                    "jtn.jtl",
                    {"fvbr": "target_id", "fvbs": "effect_id", "fvbt": "count"},
                )
            }
        )

        with pytest.raises(ValueError, match="FightEffectTriggerCount"):
            validate_game_mapping_targets(document)

    def test_accepts_mapping_target_present_in_generated_protobuf_descriptors(self) -> None:
        document = GameMappingsDocument(
            root={
                ".com.ankama.dofus.server.game.protocol.game.action.GameActionUpdateEffectTriggerCountEvent.FightEffectTriggerCountInfo": detailed_game_mapping_entry(
                    "jtn.jtl",
                    {"fvbr": "target_id", "fvbs": "effect_id", "fvbt": "count"},
                )
            }
        )

        validate_game_mapping_targets(document)

    def test_auto_mode_contract_accepts_explicit_pinned_score_exceptions(self, tmp_path: Path) -> None:
        contract_path = tmp_path / "contract.json"
        mappings_path = tmp_path / "mappings.json"
        pins_path = tmp_path / "pins.json"
        contract_path.write_text(
            json.dumps(
                {
                    "version": 3,
                    "thresholds": {
                        "message_score": 0.7,
                        "match_margin": 0.05,
                        "field_score": 0.5,
                    },
                    "messages": [
                        {
                            "message": ".game.Required",
                            "fields": ["value"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        entry = detailed_game_mapping_entry("obf", {"obf_value": "value"}).model_copy(
            update={
                "full_non_obf_msg_namespace": ".game.Required",
                "similarity_score": 0.2,
                "match_margin": 0.0,
                "is_low_confidence": True,
                "field_mapping_infos": {"obf_value": {"value": 0.2}},
            }
        )
        mappings_path.write_text(
            GameMappingsDocument(root={".game.Required": entry}).model_dump_json(),
            encoding="utf-8",
        )
        pins_path.write_text(
            PinnedPairsConfig(
                pairs=[
                    PinnedPair(
                        obf="obf",
                        non_obf=".game.Required",
                        field_mapping_by_obf={"obf_value": "value"},
                    )
                ]
            ).model_dump_json(),
            encoding="utf-8",
        )

        audit = check_auto_mode_mappings(
            contract_path=contract_path,
            detailed_mappings_path=mappings_path,
            pinned_pairs_path=pins_path,
            observed_root_obf_messages={
                "obf_orphan": ObservedRootObfMessage(
                    obf_msg_namespace="obf_orphan",
                    from_server=True,
                    instance_count=9,
                    observed_field_names=("field_a",),
                    capture_session_ids=("session-a", "session-b"),
                )
            },
        )

        assert audit.pinned_message_exceptions == (".game.Required",)
        assert audit.pinned_field_exceptions == (".game.Required.value",)
        assert audit.unmapped_observed_messages == ("obf_orphan (server, 9 captures, 1 fields, 2 sessions)",)
        assert audit.observed_contract_messages == 0
        assert audit.runtime_capture_count == 9
        assert audit.runtime_session_count == 2

    def test_auto_mode_contract_aggregates_failures(self, tmp_path: Path) -> None:
        contract_path = tmp_path / "contract.json"
        mappings_path = tmp_path / "mappings.json"
        pins_path = tmp_path / "pins.json"
        contract_path.write_text(
            json.dumps(
                {
                    "version": 3,
                    "thresholds": {
                        "message_score": 0.7,
                        "match_margin": 0.05,
                        "field_score": 0.5,
                    },
                    "messages": [
                        {
                            "message": ".game.Required",
                            "fields": ["missing"],
                        },
                        {
                            "message": ".game.Absent",
                            "fields": [],
                        },
                        {
                            "message": ".game.Traced",
                            "fields": [],
                        },
                        {
                            "message": ".game.Fielded",
                            "fields": [],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        entry = detailed_game_mapping_entry("obf", {}).model_copy(
            update={
                "full_non_obf_msg_namespace": ".game.Required",
                "similarity_score": 0.2,
                "match_margin": 0.0,
            }
        )
        traced_entry = detailed_game_mapping_entry("obf_traced", {}).model_copy(
            update={
                "full_non_obf_msg_namespace": ".game.Traced",
                "match_margin": 0.0,
                "evidence_coverage": 1.0,
            }
        )
        fielded_entry = detailed_game_mapping_entry("obf_fielded", {}).model_copy(
            update={
                "full_non_obf_msg_namespace": ".game.Fielded",
                "match_margin": 0.5,
                "evidence_coverage": 0.5,
            }
        )
        mappings_path.write_text(
            GameMappingsDocument(
                root={
                    ".game.Required": entry,
                    ".game.Traced": traced_entry,
                    ".game.Fielded": fielded_entry,
                }
            ).model_dump_json(),
            encoding="utf-8",
        )
        pins_path.write_text(PinnedPairsConfig(pairs=[]).model_dump_json(), encoding="utf-8")

        with pytest.raises(AutoModeMappingContractError) as error_info:
            check_auto_mode_mappings(
                contract_path=contract_path,
                detailed_mappings_path=mappings_path,
                pinned_pairs_path=pins_path,
                observed_root_obf_messages={
                    "obf_traced": ObservedRootObfMessage(
                        obf_msg_namespace="obf_traced",
                        from_server=True,
                        instance_count=4,
                        observed_field_names=("field_a",),
                    ),
                    "obf_orphan": ObservedRootObfMessage(
                        obf_msg_namespace="obf_orphan",
                        from_server=False,
                        instance_count=2,
                        observed_field_names=(),
                        capture_session_ids=("session-a",),
                    ),
                },
            )

        error_message = str(error_info.value)
        assert "missing messages (1)" in error_message
        assert ".game.Absent" in error_message
        assert "missing fields (1)" in error_message
        assert ".game.Required.missing" in error_message
        assert "low message scores (1)" in error_message
        assert "- low match margins (1):" in error_message
        assert ".game.Traced: 0.000 < 0.050" in error_message
        assert "- fieldless low match margins (1):" in error_message
        assert ".game.Required: 0.000 < 0.050 (no declared fields)" in error_message
        assert "- captured classes nothing claimed (1):" in error_message
        assert "runtime coverage: 1/4 contract messages observed" in error_message
        assert "2 obfuscated classes, 6 root captures, 1 session with session IDs" in error_message
        assert "required-field gaps: 0 with captures, 1 without captures" in error_message
        assert "obf_orphan (client, 2 captures, 0 fields, 1 session)" in error_message

    @pytest.mark.parametrize(
        ("msg", "expected"),
        [
            (DumpCSMessage(file_descriptor="FD", name="SimpleMsg"), ".SimpleMsg"),
            (
                DumpCSMessage(file_descriptor="FD", name="MyMsg", namespace="Game.Messages"),
                ".game.messages.MyMsg",
            ),
            (
                DumpCSMessage(
                    file_descriptor="FD",
                    name="InnerMsg",
                    namespace="Game.Messages",
                    parent_name="OuterMsg.Types",
                ),
                ".game.messages.OuterMsg.Types.InnerMsg",
            ),
        ],
    )
    def test_builds_full_message_namespace(self, msg: DumpCSMessage, expected: str) -> None:
        assert build_full_message_namespace(msg) == expected

    def test_exports_minimal_mapping_document_with_scores_and_runtime_confidence(self) -> None:
        non_obf_message = DumpCSMessage(
            file_descriptor="InventoryReflection",
            name="InventoryEvent",
            fields=[dump_cs_field("int", field_name="value_")],
        )
        obf_message = DumpCSMessage(
            file_descriptor="InventoryReflection",
            name="obf_inventory_event",
            fields=[dump_cs_field("int", field_name="value_")],
        )

        document = build_game_mappings_document(
            [
                MatchResult(
                    non_obf_signature=message_signature("InventoryEvent", declared_field_signatures=[]),
                    obf_signature=message_signature("obf_inventory_event", declared_field_signatures=[]),
                    score=0.6175,
                    group_similarity_score=0.75,
                    assembly_similarity_score=0.81,
                    structure_similarity_score=0.93,
                    field_mapping={"obj": "object"},
                    field_mapping_infos={"obj": {"object": 0.6175}},
                    field_mapping_rejected_infos={
                        "fgvo": {
                            "map_id": ValidationFailureRejectedInfo(reason="validation_failure", value=123)
                        }
                    },
                    field_mapping_unmapped_non_obf_fields={},
                    runtime_confidence=0.5,
                    match_margin=0.0,
                    runner_up_obf=None,
                    evidence_coverage=0.8,
                    is_runtime_observed=True,
                )
            ],
            obf_messages_by_cls={"obf_inventory_event": obf_message},
            non_obf_messages_by_cls={"InventoryEvent": non_obf_message},
        )

        assert document.root[".InventoryEvent"].model_dump() == {
            "full_obf_msg_namespace": "obf_inventory_event",
            "obf_msg_namespace": "obf_inventory_event",
            "full_non_obf_msg_namespace": ".InventoryEvent",
            "field_mapping": {"obj": "object"},
            "field_mapping_infos": {"obj": {"object": 0.6175}},
            "field_mapping_rejected_infos": {
                "fgvo": {"map_id": {"reason": "validation_failure", "value": 123}}
            },
            "field_mapping_unmapped_non_obf_fields": {},
            "similarity_score": 0.6175,
            "group_similarity_score": 0.75,
            "assembly_similarity_score": 0.81,
            "structure_similarity_score": 0.93,
            "runtime_confidence": 0.5,
            "match_margin": 0.0,
            "runner_up_obf": None,
            "is_low_confidence": False,
            "evidence_coverage": 0.8,
            "is_runtime_observed": True,
        }

    def test_filters_container_only_segments_from_full_namespaces(self) -> None:
        non_obf_container = DumpCSMessage(
            file_descriptor="GameReflection",
            name="InteractiveElement",
            namespace="Com.Ankama.Dofus.Server.Game.Protocol.Common",
            fields=[dump_cs_field("int", field_name="root_value_")],
        )
        non_obf_types = DumpCSMessage(
            file_descriptor="GameReflection",
            name="Types",
            namespace="Com.Ankama.Dofus.Server.Game.Protocol.Common",
            parent_name="InteractiveElement",
        )
        non_obf_message = DumpCSMessage(
            file_descriptor="GameReflection",
            name="InteractiveElementSkill",
            namespace="Com.Ankama.Dofus.Server.Game.Protocol.Common",
            parent_name="InteractiveElement.Types",
            fields=[dump_cs_field("int", field_name="value_")],
        )
        obf_root = DumpCSMessage(file_descriptor="GameReflection", name="kmv", namespace="kmv")
        obf_container = DumpCSMessage(
            file_descriptor="GameReflection",
            name="kmu",
            namespace="kmv",
            parent_name="kmv",
        )
        obf_message = DumpCSMessage(
            file_descriptor="GameReflection",
            name="kmt",
            namespace="kmv",
            parent_name="kmv.kmu",
            fields=[dump_cs_field("int", field_name="value_")],
        )

        document = build_game_mappings_document(
            [
                simple_match_result(
                    non_obf_cls="InteractiveElement.Types.InteractiveElementSkill",
                    obf_cls="kmv.kmu.kmt",
                    score=1.0,
                )
            ],
            obf_messages_by_cls=build_message_lookup([obf_root, obf_container, obf_message]),
            non_obf_messages_by_cls=build_message_lookup([non_obf_container, non_obf_types, non_obf_message]),
        )

        assert set(document.root) == {
            ".com.ankama.dofus.server.game.protocol.common.InteractiveElement.InteractiveElementSkill"
        }
        entry = next(iter(document.root.values()))
        assert entry.full_obf_msg_namespace == "kmv.kmu.kmt"
        assert entry.obf_msg_namespace == "kmv.kmv.kmt"
        assert entry.field_mapping == {}

    @pytest.mark.parametrize(
        ("matches", "obf_messages_by_cls", "non_obf_messages_by_cls"),
        [
            (
                [simple_match_result(non_obf_cls="Foo", obf_cls="obf_foo")],
                {},
                {"Foo": DumpCSMessage(file_descriptor="FD", name="Foo")},
            ),
            (
                [simple_match_result(non_obf_cls="Foo", obf_cls="obf_foo")],
                {"obf_foo": DumpCSMessage(file_descriptor="FD", name="obf_foo")},
                {},
            ),
            (
                [
                    simple_match_result(non_obf_cls="SharedMsg", obf_cls="obf_a"),
                    simple_match_result(non_obf_cls="SharedMsg", obf_cls="obf_b", score=0.8),
                ],
                {
                    "obf_a": DumpCSMessage(file_descriptor="FD", name="obf_a"),
                    "obf_b": DumpCSMessage(file_descriptor="FD", name="obf_b"),
                },
                {"SharedMsg": DumpCSMessage(file_descriptor="FD", name="SharedMsg")},
            ),
        ],
    )
    def test_raises_for_invalid_mapping_document_inputs(
        self,
        matches: list[MatchResult],
        obf_messages_by_cls: dict[str, DumpCSMessage],
        non_obf_messages_by_cls: dict[str, DumpCSMessage],
    ) -> None:
        with pytest.raises(ValueError):
            build_game_mappings_document(
                matches,
                obf_messages_by_cls=obf_messages_by_cls,
                non_obf_messages_by_cls=non_obf_messages_by_cls,
            )

    def test_write_game_mappings_writes_simple_and_detailed_documents(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def skip_generated_target_validation(document: GameMappingsDocument) -> None:
            return None

        monkeypatch.setattr(
            "DBDofusUnity.proto_mapper_assembly.controllers.game_mappings.validate_game_mapping_targets",
            skip_generated_target_validation,
        )
        output_path = tmp_path / "game_mappings.json"
        detailed_output_path = tmp_path / "game_mappings_detailed.json"

        write_game_mappings(
            [simple_match_result(field_mapping={"fa": "field_a"})],
            obf_messages_by_cls={"obf_a": DumpCSMessage(file_descriptor="GameReflection", name="obf_a")},
            non_obf_messages_by_cls={
                "ClearA": DumpCSMessage(file_descriptor="GameReflection", name="ClearA")
            },
            output_path=output_path,
            detailed_output_path=detailed_output_path,
        )

        assert json.loads(output_path.read_text(encoding="utf-8")) == {
            ".ClearA": {"obf_msg_namespace": "obf_a", "field_mapping": {"fa": "field_a"}}
        }
        assert (
            json.loads(detailed_output_path.read_text(encoding="utf-8"))[".ClearA"]["field_mapping_infos"]
            == {}
        )

import pytest

from tests.fixtures.proto_mapper.field_builders import typed_dump_field
from tests.fixtures.proto_mapper.matching_builders import (
    child_message_mapping_signatures,
    fake_field_mapping_context,
)
from tests.fixtures.proto_mapper.message_builders import (
    build_field_mapping_for_test,
    build_message_lookup,
    make_field_mapping_context,
    message_signature,
)

from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage
from DBDofusUnity.proto_mapper_assembly.interfaces.field_category import FieldCategoryEnum
from DBDofusUnity.proto_mapper_assembly.interfaces.field_mapping_rejected_infos import ReasonRejectedInfo
from DBDofusUnity.proto_mapper_assembly.interfaces.message_pair import MatchPairKey
from DBDofusUnity.proto_mapper_assembly.interfaces.pinned_pairs import PinnedPair
from DBDofusUnity.proto_mapper_assembly.matching.iterative_store import IterativeMatchingStore
from DBDofusUnity.proto_mapper_assembly.runtime.runtime_store import RuntimeDataStore


class TestChildMessageMapping:
    @pytest.mark.parametrize("complete", [False, True])
    def test_pinned_child_field_excludes_competitors_despite_stronger_child_scores(
        self, runtime_data_store: RuntimeDataStore, complete: bool
    ) -> None:
        non_obf_message = DumpCSMessage(
            file_descriptor="GameReflection",
            name="FightEvent",
            fields=[
                typed_dump_field(
                    field_name=name,
                    property_name=name,
                    normalized_type=child,
                    category=FieldCategoryEnum.MESSAGE,
                    offset=offset,
                )
                for name, child, offset in (("teleport", "Teleport", 24), ("dodge", "Dodge", 32))
            ],
        )
        obf_message = DumpCSMessage(
            file_descriptor="GameReflection",
            name="jsq",
            fields=[
                typed_dump_field(
                    field_name=name,
                    property_name=name,
                    normalized_type=child,
                    category=FieldCategoryEnum.MESSAGE,
                    offset=offset,
                )
                for name, child, offset in (("fwvp", "jrh", 24), ("fwvc", "jre", 32))
            ],
        )
        non_obf_messages = build_message_lookup(
            [
                non_obf_message,
                *(DumpCSMessage(file_descriptor="GameReflection", name=n) for n in ("Teleport", "Dodge")),
            ]
        )
        obf_messages = build_message_lookup(
            [obf_message, *(DumpCSMessage(file_descriptor="GameReflection", name=n) for n in ("jrh", "jre"))]
        )
        context = make_field_mapping_context(
            runtime_data_store,
            score_by_pair={
                MatchPairKey("jrh", "Teleport"): 0.2,
                MatchPairKey("jrh", "Dodge"): 1.0,
                MatchPairKey("jre", "Teleport"): 1.0,
                MatchPairKey("jre", "Dodge"): 0.2,
            },
        )
        result = build_field_mapping_for_test(
            non_obf_signature=message_signature("FightEvent", [], dump_cs_msg=non_obf_message),
            obf_signature=message_signature("jsq", [], dump_cs_msg=obf_message),
            non_obf_messages_by_cls=non_obf_messages,
            obf_messages_by_cls=obf_messages,
            field_mapping_context=context,
            pinned_pair=PinnedPair(
                obf="jsq",
                non_obf="FightEvent",
                field_mapping_by_obf={"fwvp": "teleport"},
                complete_field_mapping=complete,
            ),
        )

        assert result.field_mapping == (
            {"fwvp": "teleport"} if complete else {"fwvp": "teleport", "fwvc": "dodge"}
        )
        for obf, non_obf in (("fwvp", "dodge"), ("fwvc", "teleport")):
            assert result.field_mapping_rejected_infos[obf][non_obf] == ReasonRejectedInfo(
                reason="pinned_field_exclusion"
            )
        assert any(
            child.obf_message_cls == "jrh" and child.non_obf_message_cls == "Teleport"
            for child in result.discovered_message_matches
        )

    def test_discovers_child_message_correspondence(self, runtime_data_store: RuntimeDataStore) -> None:
        non_obf_signature, obf_signature, non_obf_messages_by_cls, obf_messages_by_cls = (
            child_message_mapping_signatures()
        )

        result = build_field_mapping_for_test(
            non_obf_signature=non_obf_signature,
            obf_signature=obf_signature,
            obf_messages_by_cls=obf_messages_by_cls,
            non_obf_messages_by_cls=non_obf_messages_by_cls,
            field_mapping_context=fake_field_mapping_context(runtime_data_store),
        )

        assert result.field_mapping == {"faaa": "stated_element"}
        assert len(result.discovered_message_matches) == 1
        assert result.discovered_message_matches[0].obf_message_cls == "abc"
        assert result.discovered_message_matches[0].non_obf_message_cls == "StatedElement"

    def test_rejects_child_pair_confirmed_to_another_non_obf_message(
        self, runtime_data_store: RuntimeDataStore
    ) -> None:
        non_obf_signature, obf_signature, non_obf_messages_by_cls, obf_messages_by_cls = (
            child_message_mapping_signatures()
        )
        non_obf_messages_by_cls["OtherElement"] = DumpCSMessage(
            file_descriptor="GameReflection",
            name="OtherElement",
        )
        matching_store = IterativeMatchingStore(
            confirmed_non_obf_by_obf={"abc": "OtherElement"},
            confirmed_obf_by_non_obf={"OtherElement": "abc"},
            inferred_non_obf_by_obf={},
            source_by_obf={"abc": "group_match"},
        )

        result = build_field_mapping_for_test(
            non_obf_signature=non_obf_signature,
            obf_signature=obf_signature,
            obf_messages_by_cls=obf_messages_by_cls,
            non_obf_messages_by_cls=non_obf_messages_by_cls,
            matching_store=matching_store,
            field_mapping_context=fake_field_mapping_context(runtime_data_store),
        )

        assert result.field_mapping == {}
        assert result.discovered_message_matches == ()

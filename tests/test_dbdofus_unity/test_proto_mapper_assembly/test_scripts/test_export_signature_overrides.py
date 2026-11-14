import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from tests.fixtures.proto_mapper.field_builders import dump_field
from tests.fixtures.proto_mapper.message_builders import (
    field_signature,
    message_signature,
)
from tests.fixtures.proto_mapper.script_builders import (
    export_enum_signature_entry as _enum_signature_entry,
)
from tests.fixtures.proto_mapper.script_builders import (
    export_signature as _sig,
)
from tests.fixtures.proto_mapper.script_builders import (
    field_with_shape as _field_with_shape,
)
from tests.fixtures.proto_mapper.script_builders import (
    game_mapping_entry,
    game_mappings_doc,
    non_obf_message_with_fields,
    obf_signature_with_fields,
    pinned_config,
)
from tests.fixtures.proto_mapper.script_builders import (
    run_build_signature_overrides as _run_build_signature_overrides,
)
from tests.fixtures.proto_mapper.script_builders import (
    script_message as _msg,
)
from tests.fixtures.proto_mapper.shapes import ENUM_SHAPE
from tests.fixtures.proto_mapper.signatures import (
    builder_function_access_signature,
)

from DBDofusUnity.proto_mapper_assembly.controllers.signature_override_application import (
    apply_stored_signature_overrides,
)
from DBDofusUnity.proto_mapper_assembly.controllers.signature_overrides import load_signature_overrides
from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import (
    AccessAtomSignature,
    FieldAccessSignatures,
    ReturnRole,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import (
    DumpCSMessage,
    DumpCSMessageField,
    FieldKey,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.field_category import (
    FieldCategoryEnum,
    FieldTypeLeafKind,
    FieldTypeShape,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.game_mappings import GameMappingsDocument
from DBDofusUnity.proto_mapper_assembly.interfaces.signature_overrides import (
    EnumSignatureOverrideHint,
    FieldOverrideBinding,
    SignatureOverrideEntry,
    SignatureOverridesFile,
)
from DBDofusUnity.proto_mapper_assembly.scoring.message_scoring import compute_message_similarity
from scripts.mapping.export_signature_overrides import (
    _build_obf_field_binding_by_non_obf_property_name,
    main,
)


pytestmark = pytest.mark.usefixtures("reuse_generated_non_obf_messages")

class TestBuildSignatureOverrides:
    def test_exports_field_signatures_for_mapped_pair(self) -> None:
        pinned = pinned_config()
        obf_msg = _msg("xyz").model_copy(
            update={
                "fields": [
                    dump_field("fa", "fa", 32, FieldCategoryEnum.NUMBER),
                    dump_field("fb", "fb", 48, FieldCategoryEnum.NUMBER),
                ]
            }
        )
        obf_sigs = {
            "xyz": message_signature(
                "xyz",
                declared_field_signatures=[],
                dump_cs_msg=obf_msg,
                field_signatures=[field_signature(32, None), field_signature(48, None)],
            )
        }
        non_obf_msg = DumpCSMessage(
            file_descriptor="FD",
            name="Msg",
            namespace="Com.Ankama",
            fields=[
                dump_field("field_a", "FieldA", 16, FieldCategoryEnum.NUMBER),
                dump_field("field_b", "FieldB", 24, FieldCategoryEnum.NUMBER),
            ],
        )
        game_mappings = game_mappings_doc({"fa": "field_a", "fb": "field_b"})

        result = _run_build_signature_overrides(
            pinned=pinned,
            obf_signatures=obf_sigs,
            non_obf_messages=[non_obf_msg],
            game_mappings=game_mappings,
        )

        entry = result.root["Com.Ankama.Msg"]
        assert len(entry.field_signatures) == 2
        assert {field_sig.field_offset for field_sig in entry.field_signatures.values()} == {16, 24}

    def test_exports_function_signatures_for_direct_pinned_pair_without_detailed_mapping(self) -> None:
        pinned = pinned_config()
        obf_sigs = {"xyz": _sig("xyz", [])}

        result = _run_build_signature_overrides(
            pinned=pinned,
            obf_signatures=obf_sigs,
        )

        entry = result.root["Com.Ankama.Msg"]
        assert entry.function_signatures == obf_sigs["xyz"].function_signatures

    def test_exports_signatures_for_all_detailed_game_mappings(self) -> None:
        pinned = pinned_config(obf="xyz", non_obf="Com.Ankama.Msg")
        obf_sigs = {
            "abc": _sig("abc", [32]),
            "xyz": _sig("xyz", [48]),
        }
        other_non_obf_msg = DumpCSMessage(
            file_descriptor="FD",
            name="Other",
            namespace="Com.Ankama",
        )
        game_mappings = GameMappingsDocument(
            root={
                ".com.ankama.Msg": game_mapping_entry(field_mapping={}, obf_cls="xyz"),
                ".com.ankama.Other": game_mapping_entry(
                    field_mapping={},
                    obf_cls="abc",
                    non_obf_namespace=".com.ankama.Other",
                ),
            }
        )

        result = _run_build_signature_overrides(
            pinned=pinned,
            obf_signatures=obf_sigs,
            non_obf_messages=[other_non_obf_msg],
            game_mappings=game_mappings,
        )

        assert set(result.root) == {"Com.Ankama.Msg", "Com.Ankama.Other"}
        assert result.root["Com.Ankama.Other"].function_signatures == obf_sigs["abc"].function_signatures

    def test_raises_when_obf_sig_missing(self) -> None:
        pinned = pinned_config()

        with pytest.raises(ValueError):
            _run_build_signature_overrides(
                pinned=pinned,
                obf_signatures={},
            )

    def test_rejects_missing_manual_new_dump_cs_entry_for_new_message(self) -> None:
        pinned = pinned_config(non_obf="Com.Ankama.Dofus.Server.Game.Protocol.Fight.NewMsg")
        obf_sigs = {"xyz": _sig("xyz", [32])}
        namespace_anchor = DumpCSMessage(
            file_descriptor="fight_fd",
            name="SomeExistingMsg",
            namespace="Com.Ankama.Dofus.Server.Game.Protocol.Fight",
        )

        with pytest.raises(ValueError, match="Unknown non-obf message alias"):
            _run_build_signature_overrides(
                pinned=pinned,
                obf_signatures=obf_sigs,
                non_obf_messages=[namespace_anchor],
                bootstrap_messages={},
            )


class TestBuildFieldBinding:
    def test_skips_field_not_found_in_dump_cs(self) -> None:
        obf_sig = obf_signature_with_fields({"fhtj": 32})
        non_obf_msg = non_obf_message_with_fields({"cells": 16})
        result = _build_obf_field_binding_by_non_obf_property_name(
            obf_message=obf_sig.dump_cs_msg,
            non_obf_message=non_obf_msg,
            field_mapping={"fhtj": "cells", "missing_key": "other"},
        )

        assert result == {"cells": FieldOverrideBinding(obf_field_name="fhtj", obf_memory_offset=32)}

    def test_build_signature_overrides_populates_field_offset_remapping_from_pinned_field_mapping(
        self,
    ) -> None:
        pinned = pinned_config(field_mapping_by_obf={"fhtm": "cells"})
        fields = [
            dump_field("fhtj", "fhtj", 32, FieldCategoryEnum.NUMBER),
            dump_field("fhtm", "fhtm", 48, FieldCategoryEnum.NUMBER),
        ]
        message = _msg("xyz").model_copy(update={"fields": fields})
        obf_sigs = {
            "xyz": message_signature(
                "xyz",
                declared_field_signatures=[],
                dump_cs_msg=message,
                field_signatures=[field_signature(32, None), field_signature(48, None)],
            )
        }
        game_mappings = game_mappings_doc({"fhtj": "actorId"})
        bootstrap_msg = DumpCSMessage(
            file_descriptor="FD",
            name="Msg",
            namespace="Com.Ankama",
            fields=[
                dump_field("_actor_id", "actorId", 16, FieldCategoryEnum.NUMBER),
                dump_field("_cells", "cells", 24, FieldCategoryEnum.NUMBER),
            ],
        )

        result = _run_build_signature_overrides(
            pinned=pinned,
            obf_signatures=obf_sigs,
            bootstrap_messages={"Com.Ankama.Msg": bootstrap_msg},
            game_mappings=game_mappings,
        )

        assert result.root["Com.Ankama.Msg"].obf_field_binding_by_non_obf_property_name == {
            "actorId": FieldOverrideBinding(obf_field_name="fhtj", obf_memory_offset=32),
            "cells": FieldOverrideBinding(obf_field_name="fhtm", obf_memory_offset=48),
        }

    def test_build_signature_overrides_binds_oneof_variants_by_obf_field_name(self) -> None:
        pinned = pinned_config()
        obf_message = _msg("xyz").model_copy(
            update={
                "fields": [
                    _field_with_shape(
                        field_name="choice_message",
                        property_name="choice_message",
                        offset=32,
                        clr_type="Child",
                        category=FieldCategoryEnum.MESSAGE,
                    ),
                    _field_with_shape(
                        field_name="choice_enum",
                        property_name="choice_enum",
                        offset=32,
                        clr_type="ChoiceEnum",
                        category=FieldCategoryEnum.ENUM,
                    ),
                ]
            }
        )
        obf_sigs = {
            "xyz": message_signature(
                "xyz",
                declared_field_signatures=[],
                dump_cs_msg=obf_message,
                field_signatures=[
                    field_signature(
                        32,
                        obf_message.fields[0].field_type_shape,
                        field_key=obf_message.fields[0].field_key,
                    ),
                    field_signature(
                        32,
                        obf_message.fields[1].field_type_shape,
                        field_key=obf_message.fields[1].field_key,
                    ),
                ],
            )
        }
        bootstrap_msg = DumpCSMessage(
            file_descriptor="FD",
            name="Msg",
            namespace="Com.Ankama",
            fields=[
                _field_with_shape(
                    field_name="clear_message_",
                    property_name="ClearMessage",
                    offset=16,
                    clr_type="Child",
                    category=FieldCategoryEnum.MESSAGE,
                ),
                _field_with_shape(
                    field_name="clear_enum_",
                    property_name="ClearEnum",
                    offset=16,
                    clr_type="ChoiceEnum",
                    category=FieldCategoryEnum.ENUM,
                ),
            ],
        )
        game_mappings = game_mappings_doc({"choice_message": "clear_message", "choice_enum": "clear_enum"})

        result = _run_build_signature_overrides(
            pinned=pinned,
            obf_signatures=obf_sigs,
            bootstrap_messages={"Com.Ankama.Msg": bootstrap_msg},
            game_mappings=game_mappings,
        )

        entry = result.root["Com.Ankama.Msg"]
        assert entry.obf_field_binding_by_non_obf_property_name == {
            "ClearMessage": FieldOverrideBinding(obf_field_name="choice_message", obf_memory_offset=32),
            "ClearEnum": FieldOverrideBinding(obf_field_name="choice_enum", obf_memory_offset=32),
        }
        assert {
            property_name: field_signature.field_key.field_name
            for property_name, field_signature in entry.field_signatures.items()
        } == {"ClearMessage": "clear_message_", "ClearEnum": "clear_enum_"}

    def test_build_signature_overrides_drops_incompatible_field_remapping(self) -> None:
        pinned = pinned_config()
        obf_msg = _msg("xyz").model_copy(
            update={
                "fields": [
                    _field_with_shape(
                        field_name="character_id_",
                        property_name="fhtj",
                        offset=32,
                        clr_type="long",
                        category=FieldCategoryEnum.NUMBER,
                    ),
                    _field_with_shape(
                        field_name="cells_",
                        property_name="fhtm",
                        offset=48,
                        clr_type="RepeatedField<int>",
                        category=FieldCategoryEnum.REPEATED,
                    ),
                ]
            }
        )
        obf_sigs = {
            "xyz": message_signature(
                "xyz",
                declared_field_signatures=[],
                dump_cs_msg=obf_msg,
                field_signatures=[
                    field_signature(32, FieldTypeShape(FieldCategoryEnum.NUMBER, None, None)),
                    field_signature(
                        48, FieldTypeShape(FieldCategoryEnum.REPEATED, FieldTypeLeafKind.NUMBER, None)
                    ),
                ],
            )
        }
        bootstrap_msg = DumpCSMessage(
            file_descriptor="FD",
            name="Msg",
            namespace="Com.Ankama",
            fields=[
                _field_with_shape(
                    field_name="cells_",
                    property_name="Cells",
                    offset=16,
                    clr_type="RepeatedField<int>",
                    category=FieldCategoryEnum.REPEATED,
                ),
                _field_with_shape(
                    field_name="character_id_",
                    property_name="CharacterId",
                    offset=24,
                    clr_type="long",
                    category=FieldCategoryEnum.NUMBER,
                ),
            ],
        )
        game_mappings = game_mappings_doc({"fhtj": "cells", "fhtm": "character_id"})

        overrides = _run_build_signature_overrides(
            pinned=pinned,
            obf_signatures=obf_sigs,
            bootstrap_messages={"Com.Ankama.Msg": bootstrap_msg},
            game_mappings=game_mappings,
        )

        entry = overrides.root["Com.Ankama.Msg"]
        assert entry.obf_field_binding_by_non_obf_property_name == {}
        assert entry.field_signatures == {}


class TestRekeyedOverrideEntry:
    def test_rekeyed_override_round_trip_preserves_field_access_indexes_for_scoring(self) -> None:
        pinned = pinned_config()
        number_shape = FieldTypeShape(FieldCategoryEnum.NUMBER, None, None)
        obf_access = AccessAtomSignature(
            entry_type="field",
            access_kind="read",
            field_type_shape=number_shape,
            field_offset=56,
            index_in_function=7,
        )
        function_signatures = [
            builder_function_access_signature(
                return_role=ReturnRole.VOID,
                takes_message_parameter=False,
                size=12,
                self_accesses=[obf_access],
                foreign_access_summary=[],
            )
        ]
        field_signatures = [
            FieldAccessSignatures(
                field_key=FieldKey(56, "field_56_"),
                field_type_shape=number_shape,
                accesses=[obf_access],
            )
        ]
        obf_sig = obf_signature_with_fields(
            {"fhya": 56},
            field_signatures=field_signatures,
            function_signatures=function_signatures,
        )
        non_obf_msg = non_obf_message_with_fields({"subarea_id": 24})
        non_obf_sig = message_signature(
            "Com.Ankama.Msg",
            declared_field_signatures=[],
            dump_cs_msg=non_obf_msg,
        )
        exported = _run_build_signature_overrides(
            pinned=pinned,
            obf_signatures={"xyz": obf_sig},
            non_obf_messages=[non_obf_msg],
            game_mappings=game_mappings_doc({"fhya": "subarea_id"}),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "messages_access_signature_override.json"
            path.write_text(exported.model_dump_json(), encoding="utf-8")
            loaded = load_signature_overrides(path)

        prepared = apply_stored_signature_overrides(
            non_obf_signatures_by_cls={"Com.Ankama.Msg": non_obf_sig},
            overrides=loaded.root,
            non_obf_enum_signatures_by_name={},
        )

        prepared_non_obf = prepared["Com.Ankama.Msg"]
        payload = json.loads(exported.model_dump_json())
        access_payload = payload["Com.Ankama.Msg"]["function_signatures"][0]["self_accesses"][0]
        assert access_payload["index_in_function"] == 7
        assert prepared_non_obf.function_signatures[0].self_accesses[0].index_in_function == 7
        assert (
            compute_message_similarity(
                obf_sig, prepared_non_obf, structure_score=0.0
            ).assembly_sim_data.assembly_similarity
            == 1.0
        )

    def test_exports_one_signature_per_distinct_alias(self) -> None:
        pinned = pinned_config()
        alias_signature = builder_function_access_signature(
            return_role=ReturnRole.VOID,
            takes_message_parameter=False,
            size=12,
            self_accesses=[
                AccessAtomSignature(
                    entry_type="field",
                    access_kind="read",
                    field_offset=56,
                    index_in_function=0,
                )
            ],
            foreign_access_summary=[],
        )
        obf_sigs = {
            "xyz": obf_signature_with_fields(
                {"fhya": 56},
                function_signatures=[alias_signature, alias_signature.model_copy(deep=True)],
            )
        }
        non_obf_msg = non_obf_message_with_fields({"subarea_id": 24})
        game_mappings = game_mappings_doc({"fhya": "subarea_id"})

        result = _run_build_signature_overrides(
            pinned=pinned,
            obf_signatures=obf_sigs,
            non_obf_messages=[non_obf_msg],
            game_mappings=game_mappings,
        )

        assert len(result.root["Com.Ankama.Msg"].function_signatures) == 1

    def test_drops_field_signatures_outside_game_mapping(self) -> None:
        pinned = pinned_config()
        obf_sigs = {
            "xyz": obf_signature_with_fields(
                {"fhya": 56, "fhyc": 64},
                field_signatures=[field_signature(56, None), field_signature(64, None)],
            )
        }
        non_obf_msg = non_obf_message_with_fields({"subarea_id": 24})
        game_mappings = game_mappings_doc({"fhya": "subarea_id"})

        result = _run_build_signature_overrides(
            pinned=pinned,
            obf_signatures=obf_sigs,
            non_obf_messages=[non_obf_msg],
            game_mappings=game_mappings,
        )

        entry = result.root["Com.Ankama.Msg"]
        assert [field.field_offset for field in entry.field_signatures.values()] == [24]

    def test_exports_synthetic_signature_for_virtual_field_without_obf_accesses(self) -> None:
        pinned = pinned_config()
        obf_message = _msg("xyz").model_copy(
            update={
                "fields": [
                    dump_field("fhya", "fhya", 56, FieldCategoryEnum.NUMBER),
                    dump_field("raw_fhyc", "fhyc", 64, FieldCategoryEnum.NUMBER),
                ]
            }
        )
        obf_sigs = {
            "xyz": message_signature(
                "xyz",
                declared_field_signatures=[],
                dump_cs_msg=obf_message,
                field_signatures=[field_signature(56, None)],
            )
        }
        real_non_obf_msg = non_obf_message_with_fields({"subarea_id": 24})
        bootstrap_msg = non_obf_message_with_fields({"subarea_id": 24, "unknown": 0})
        game_mappings = game_mappings_doc({"fhya": "subarea_id", "fhyc": "unknown"})

        result = _run_build_signature_overrides(
            pinned=pinned,
            obf_signatures=obf_sigs,
            non_obf_messages=[real_non_obf_msg],
            bootstrap_messages={"Com.Ankama.Msg": bootstrap_msg},
            game_mappings=game_mappings,
        )

        entry = result.root["Com.Ankama.Msg"]
        assert entry.obf_field_binding_by_non_obf_property_name == {
            "unknown": FieldOverrideBinding(obf_field_name="raw_fhyc", obf_memory_offset=64)
        }
        unknown_signature = entry.field_signatures["unknown"]
        assert unknown_signature.field_offset == 64
        assert unknown_signature.field_key == FieldKey(memory_offset=64, field_name="unknown")
        assert unknown_signature.accesses == []

    def test_exports_canonical_enum_signature_hints_into_non_obf_field_names(self) -> None:
        pinned = pinned_config()
        enum_obf_field = DumpCSMessageField(
            clr_type="xyz_enum",
            normalized_type="xyz_enum",
            category=FieldCategoryEnum.ENUM,
            memory_offset=56,
            field_name="fhya",
            property_name="fhya",
            enum_value_type="xyz_enum",
        )
        enum_non_obf_field = DumpCSMessageField(
            clr_type="clear_enum",
            normalized_type="clear_enum",
            category=FieldCategoryEnum.ENUM,
            memory_offset=24,
            field_name="subarea_id_",
            property_name="SubareaId",
            enum_value_type="clear_enum",
        )
        obf_message = _msg("xyz").model_copy(update={"fields": [enum_obf_field]})
        non_obf_msg = DumpCSMessage(
            file_descriptor="FD",
            name="Msg",
            namespace="Com.Ankama",
            fields=[enum_non_obf_field],
        )
        obf_sigs = {
            "xyz": message_signature(
                "xyz",
                declared_field_signatures=[],
                dump_cs_msg=obf_message,
                field_signatures=[field_signature(56, None)],
            )
        }
        game_mappings = game_mappings_doc({"fhya": "subarea_id"})

        result = _run_build_signature_overrides(
            pinned=pinned,
            obf_signatures=obf_sigs,
            non_obf_messages=[non_obf_msg],
            game_mappings=game_mappings,
            obf_enum_signatures={
                "xyz_enum": _enum_signature_entry(
                    member_value_to_name={"0": "a", "1": "b", "2": "c"},
                    member_groups=[[0, 2], [1], [2]],
                )
            },
            non_obf_enum_signatures={
                "clear_enum": _enum_signature_entry(
                    member_value_to_name={"10": "Ten", "20": "Twenty"},
                    member_groups=[[10], [20]],
                )
            },
        )

        entry = result.root["Com.Ankama.Msg"]
        assert [field.field_offset for field in entry.field_signatures.values()] == [24]
        assert entry.enum_signature_hints_by_non_obf_prop_name == {
            "subarea_id": {
                "value": EnumSignatureOverrideHint(
                    non_obf_enum_type="clear_enum",
                    signature=_enum_signature_entry(
                        member_value_to_name={"10": "Ten", "20": "Twenty"},
                        member_groups=[[10], [20]],
                    ),
                )
            }
        }

    def test_exports_canonical_enum_signature_hints_for_bootstrap_override_entries(self) -> None:
        pinned = pinned_config()
        enum_obf_field = DumpCSMessageField(
            clr_type="xyz_enum",
            normalized_type="xyz_enum",
            category=FieldCategoryEnum.ENUM,
            memory_offset=56,
            field_name="fhya",
            property_name="fhya",
            enum_value_type="xyz_enum",
        )
        obf_message = _msg("xyz").model_copy(update={"fields": [enum_obf_field]})
        obf_sigs = {
            "xyz": message_signature(
                "xyz",
                declared_field_signatures=[],
                dump_cs_msg=obf_message,
                field_signatures=[field_signature(56, ENUM_SHAPE)],
            )
        }
        bootstrap_msg = DumpCSMessage(
            file_descriptor="FD",
            name="Msg",
            namespace="Com.Ankama",
            fields=[
                DumpCSMessageField(
                    clr_type="clear_enum",
                    normalized_type="clear_enum",
                    category=FieldCategoryEnum.ENUM,
                    memory_offset=24,
                    field_name="subarea_id_",
                    property_name="SubareaId",
                    enum_value_type="clear_enum",
                )
            ],
        )
        game_mappings = game_mappings_doc({"fhya": "subarea_id"})

        result = _run_build_signature_overrides(
            pinned=pinned,
            obf_signatures=obf_sigs,
            bootstrap_messages={"Com.Ankama.Msg": bootstrap_msg},
            game_mappings=game_mappings,
            obf_enum_signatures={
                "xyz_enum": _enum_signature_entry(
                    member_value_to_name={"0": "a", "1": "b"},
                    member_groups=[[0], [1]],
                )
            },
            non_obf_enum_signatures={
                "clear_enum": _enum_signature_entry(
                    member_value_to_name={"10": "Ten"},
                    member_groups=[[10]],
                )
            },
        )

        assert result.root["Com.Ankama.Msg"].enum_signature_hints_by_non_obf_prop_name == {
            "subarea_id": {
                "value": EnumSignatureOverrideHint(
                    non_obf_enum_type="clear_enum",
                    signature=_enum_signature_entry(
                        member_value_to_name={"10": "Ten"},
                        member_groups=[[10]],
                    ),
                )
            }
        }

    def test_pair_field_mapping_overrides_game_mapping_for_enum_hints(self) -> None:
        pinned = pinned_config(field_mapping_by_obf={"fhya": "correct_field"})
        enum_obf_field = DumpCSMessageField(
            clr_type="xyz_enum",
            normalized_type="xyz_enum",
            category=FieldCategoryEnum.ENUM,
            memory_offset=56,
            field_name="fhya",
            property_name="fhya",
            enum_value_type="xyz_enum",
        )
        enum_non_obf_field = DumpCSMessageField(
            clr_type="clear_enum",
            normalized_type="clear_enum",
            category=FieldCategoryEnum.ENUM,
            memory_offset=24,
            field_name="correct_field_",
            property_name="CorrectField",
            enum_value_type="clear_enum",
        )
        obf_message = _msg("xyz").model_copy(update={"fields": [enum_obf_field]})
        non_obf_message = DumpCSMessage(
            file_descriptor="FD",
            name="Msg",
            namespace="Com.Ankama",
            fields=[enum_non_obf_field],
        )
        obf_sigs = {
            "xyz": message_signature(
                "xyz",
                declared_field_signatures=[],
                dump_cs_msg=obf_message,
                field_signatures=[field_signature(56, None)],
            )
        }
        game_mappings = game_mappings_doc({"fhya": "old_field"})

        result = _run_build_signature_overrides(
            pinned=pinned,
            obf_signatures=obf_sigs,
            non_obf_messages=[non_obf_message],
            game_mappings=game_mappings,
            obf_enum_signatures={
                "xyz_enum": _enum_signature_entry(
                    member_value_to_name={"0": "a", "1": "b"},
                    member_groups=[[0], [1]],
                )
            },
            non_obf_enum_signatures={
                "clear_enum": _enum_signature_entry(
                    member_value_to_name={"10": "Ten", "20": "Twenty"},
                    member_groups=[[10], [20]],
                )
            },
        )

        hints = result.root["Com.Ankama.Msg"].enum_signature_hints_by_non_obf_prop_name
        assert "correct_field" in hints
        assert "old_field" not in hints


class TestManualNewDumpCs:
    def test_main_drops_entries_the_current_build_no_longer_generates(self) -> None:
        generated_overrides = SignatureOverridesFile(
            root={"Com.Ankama.New": SignatureOverrideEntry(function_signatures=[], field_signatures={})}
        )
        stale_overrides = SignatureOverridesFile(
            root={"Com.Ankama.Stale": SignatureOverrideEntry(function_signatures=[], field_signatures={})}
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            overrides_path = Path(temp_dir) / "messages_access_signature_override.json"
            overrides_path.write_text(stale_overrides.model_dump_json(), encoding="utf-8")
            new_dump_cs_path = Path(temp_dir) / "new_dump_cs.json"
            new_dump_cs_path.write_text("{}", encoding="utf-8")

            with (
                patch(
                    "scripts.mapping.export_signature_overrides.build_signature_overrides",
                    return_value=generated_overrides,
                ),
                patch(
                    "scripts.mapping.export_signature_overrides.NON_OBF_SIGNATURE_OVERRIDES_FILE",
                    overrides_path,
                ),
                patch(
                    "scripts.mapping.export_signature_overrides.NON_OBF_NEW_DUMP_CS_FILE",
                    new_dump_cs_path,
                ),
                patch(
                    "sys.argv",
                    ["export_signature_overrides.py"],
                ),
            ):
                main()

            assert overrides_path.exists()
            payload = json.loads(overrides_path.read_text(encoding="utf-8"))
            assert set(payload) == {"Com.Ankama.New"}
            assert json.loads(new_dump_cs_path.read_text(encoding="utf-8")) == {}

    def test_main_uses_obf_dir_for_pinned_pairs_obf_inputs_and_override_output(self) -> None:
        generated_overrides = SignatureOverridesFile(
            root={"Com.Ankama.New": SignatureOverrideEntry(function_signatures=[], field_signatures={})}
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            obf_dir = Path(temp_dir) / "20_05_2026"
            obf_dir.mkdir()
            new_dump_cs_path = Path(temp_dir) / "new_dump_cs.json"
            new_dump_cs_path.write_text("{}", encoding="utf-8")

            with (
                patch(
                    "scripts.mapping.export_signature_overrides.build_signature_overrides",
                    return_value=generated_overrides,
                ) as build_signature_overrides,
                patch(
                    "scripts.mapping.export_signature_overrides.NON_OBF_NEW_DUMP_CS_FILE",
                    new_dump_cs_path,
                ),
                patch(
                    "sys.argv",
                    ["export_signature_overrides.py", "--obf-dir", str(obf_dir)],
                ),
            ):
                main()

            expected_overrides_path = obf_dir / "messages_access_signature_override.json"
            build_signature_overrides.assert_called_once()
            pinned_pairs_path = build_signature_overrides.call_args.args[0]
            export_paths = build_signature_overrides.call_args.kwargs["export_paths"]
            assert pinned_pairs_path == obf_dir / "pinned_pairs.json"
            assert export_paths.obf_dump_cs_path == obf_dir / "cs" / "Ankama.Dofus.Protocol.Game.cs"
            assert export_paths.obf_proto_accesses_path == obf_dir / "proto_accesses.json"
            assert export_paths.game_mappings_path == obf_dir / "game_mappings_detailed.json"
            payload = json.loads(expected_overrides_path.read_text(encoding="utf-8"))
            assert set(payload) == {"Com.Ankama.New"}
            assert json.loads(new_dump_cs_path.read_text(encoding="utf-8")) == {}

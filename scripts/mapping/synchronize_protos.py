from collections.abc import Mapping
from pathlib import Path

from DBDofusUnity.consts import (
    GAME_MAPPINGS_DETAILED_JSON_FILE,
    GAME_MAPPINGS_JSON_FILE,
    NON_OBF_NEW_DUMP_CS_FILE,
    NON_OBF_PROTOCOL_GAME_DUMP_CS_FILE,
    NON_OBF_SIGNATURE_OVERRIDES_FILE,
    PINNED_PAIRS_FILE,
    PROTOS_ROOT,
)
from DBDofusUnity.proto_mapper_assembly.controllers.game_mappings import load_game_mappings_document
from DBDofusUnity.proto_mapper_assembly.controllers.message_lookup import (
    build_non_obf_alias_lookup,
    resolve_non_obf_alias,
)
from DBDofusUnity.proto_mapper_assembly.controllers.new_dump_cs import load_new_dump_cs_messages
from DBDofusUnity.proto_mapper_assembly.controllers.pinned_pairs import load_pinned_pairs, write_pinned_pairs
from DBDofusUnity.proto_mapper_assembly.controllers.signature_overrides import load_signature_overrides
from DBDofusUnity.proto_mapper_assembly.helpers.non_obf_names import build_filtered_message_namespace
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage, DumpCSMessageField
from DBDofusUnity.proto_mapper_assembly.interfaces.game_mappings import (
    GameMappingsDocument,
    SimpleGameMappingsDocument,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.new_dump_cs import NewDumpCSFile
from DBDofusUnity.proto_mapper_assembly.interfaces.pinned_pairs import PinnedPair, PinnedPairsConfig
from DBDofusUnity.proto_mapper_assembly.interfaces.signature_overrides import SignatureOverridesFile
from DBDofusUnity.proto_mapper_assembly.parsers.dump_cs_parser import parse_messages
from DBDofusUnity.proto_mapper_assembly.parsers.protobuf_dump_cs import build_dump_cs_messages_from_pb2

_NON_OBF_PROTOS_DIR: Path = PROTOS_ROOT / "non_obf" / "game"


def _preserve_existing_field_offsets(
    generated_message: DumpCSMessage,
    existing_message: DumpCSMessage | None,
) -> DumpCSMessage:
    if existing_message is None:
        return generated_message
    existing_offsets_by_field_name = _build_existing_offset_lookup(existing_message.fields)
    merged_fields = [
        _copy_existing_offset(field, existing_offsets_by_field_name) for field in generated_message.fields
    ]
    return generated_message.model_copy(update={"fields": merged_fields})


def _build_existing_offset_lookup(fields: list[DumpCSMessageField]) -> dict[str, int]:
    offsets_by_field_name: dict[str, int] = {}
    for field in fields:
        offsets_by_field_name.setdefault(field.clean_field_name, field.memory_offset)
        if field.property_name is not None:
            offsets_by_field_name.setdefault(field.property_name, field.memory_offset)
    return offsets_by_field_name


def _copy_existing_offset(
    generated_field: DumpCSMessageField,
    existing_offsets_by_field_name: dict[str, int],
) -> DumpCSMessageField:
    memory_offset = existing_offsets_by_field_name.get(generated_field.clean_field_name)
    if memory_offset is None and generated_field.property_name is not None:
        memory_offset = existing_offsets_by_field_name.get(generated_field.property_name)
    if memory_offset is None:
        return generated_field
    return generated_field.model_copy(update={"memory_offset": memory_offset})


def check_nesting_matches_obfuscated_side(
    message: DumpCSMessage,
    mappings: GameMappingsDocument,
) -> str | None:
    if message.parent_name is not None:
        return None
    for entry in mappings.root.values():
        if entry.full_non_obf_msg_namespace.split(".")[-1] != message.name:
            continue
        if "." not in entry.obf_msg_namespace:
            return None
        return (
            f"{message.name} is declared at top level in the .proto, but it is mapped to the nested "
            f"obfuscated class {entry.obf_msg_namespace!r}. build_static_score_data skips pairs whose "
            f"is_root_msg disagree, so this message would never be scored. Nest it under its parent "
            f"message in the .proto, then run synchronize-protos again."
        )
    return None


def synchronize_non_obf_mapping_artifacts(
    *,
    protos_dir: Path = _NON_OBF_PROTOS_DIR,
    non_obf_dump_cs_path: Path = NON_OBF_PROTOCOL_GAME_DUMP_CS_FILE,
    new_dump_cs_path: Path = NON_OBF_NEW_DUMP_CS_FILE,
    pinned_pairs_path: Path = PINNED_PAIRS_FILE,
    signature_overrides_path: Path = NON_OBF_SIGNATURE_OVERRIDES_FILE,
    game_mappings_path: Path = GAME_MAPPINGS_JSON_FILE,
    game_mappings_detailed_path: Path = GAME_MAPPINGS_DETAILED_JSON_FILE,
) -> None:
    proto_messages_by_name = build_dump_cs_messages_from_pb2(protos_dir)
    assembly_messages = parse_messages(str(non_obf_dump_cs_path))
    assembly_messages_by_name = {message.composed_name: message for message in assembly_messages}
    bootstrap_messages_by_name = load_new_dump_cs_messages(new_dump_cs_path).root
    detailed_mappings = (
        load_game_mappings_document(game_mappings_detailed_path)
        if game_mappings_detailed_path.exists()
        else None
    )
    rebuilt_root: dict[str, DumpCSMessage] = {}
    for class_name, generated_message in sorted(proto_messages_by_name.items()):
        if detailed_mappings is not None:
            warning = check_nesting_matches_obfuscated_side(generated_message, detailed_mappings)
            if warning is not None:
                print(f"WARNING: {warning}")
        with_bootstrap_offsets = _preserve_existing_field_offsets(
            generated_message, bootstrap_messages_by_name.get(class_name)
        )
        rebuilt_root[class_name] = _preserve_existing_field_offsets(
            with_bootstrap_offsets, assembly_messages_by_name.get(class_name)
        )
    document = NewDumpCSFile(root=rebuilt_root)
    new_dump_cs_path.write_text(f"{document.model_dump_json(indent=2)}\n", encoding="utf-8")
    print(f"Rebuilt {len(rebuilt_root)} entries in {new_dump_cs_path}.")
    valid_class_names = frozenset(proto_messages_by_name)
    _prune_pinned_pairs(messages_by_cls=proto_messages_by_name, path=pinned_pairs_path)
    _prune_signature_overrides(valid_class_names=valid_class_names, path=signature_overrides_path)
    _prune_game_mappings(
        proto_messages_by_name=proto_messages_by_name,
        mapping_path=game_mappings_path,
        detailed_mapping_path=game_mappings_detailed_path,
        detailed_document=detailed_mappings,
    )


def _prune_pinned_pairs(*, messages_by_cls: Mapping[str, DumpCSMessage], path: Path) -> None:
    if not path.exists():
        return
    document = load_pinned_pairs(path)
    aliases_by_name, aliases_by_short_name = build_non_obf_alias_lookup(
        non_obf_messages_by_cls=messages_by_cls
    )
    retained_pairs: list[PinnedPair] = []
    for pair in document.pairs:
        try:
            resolve_non_obf_alias(
                pair.non_obf,
                aliases_by_name,
                aliases_by_short_name,
            )
        except ValueError:
            continue
        retained_pairs.append(pair)
    write_pinned_pairs(path, PinnedPairsConfig(pairs=retained_pairs))


def _prune_signature_overrides(*, valid_class_names: frozenset[str], path: Path) -> None:
    if not path.exists():
        return
    document = load_signature_overrides(path)
    pruned_root = {name: entry for name, entry in document.root.items() if name in valid_class_names}
    path.write_text(
        f"{SignatureOverridesFile(root=pruned_root).model_dump_json(indent=2)}\n", encoding="utf-8"
    )


def _prune_game_mappings(
    *,
    proto_messages_by_name: dict[str, DumpCSMessage],
    mapping_path: Path,
    detailed_mapping_path: Path,
    detailed_document: GameMappingsDocument | None,
) -> None:
    valid_mapping_keys = frozenset(
        build_filtered_message_namespace(
            is_obf=False,
            message=message,
            messages_by_cls=proto_messages_by_name,
        )
        for message in proto_messages_by_name.values()
    )
    if mapping_path.exists():
        simple_document = SimpleGameMappingsDocument.model_validate_json(
            mapping_path.read_text(encoding="utf-8")
        )
        pruned_simple = {
            name: entry for name, entry in simple_document.root.items() if name in valid_mapping_keys
        }
        mapping_path.write_text(
            f"{SimpleGameMappingsDocument(root=pruned_simple).model_dump_json(indent=2)}\n",
            encoding="utf-8",
        )
    if detailed_document is not None:
        pruned_detailed = {
            name: entry for name, entry in detailed_document.root.items() if name in valid_mapping_keys
        }
        detailed_mapping_path.write_text(
            f"{GameMappingsDocument(root=pruned_detailed).model_dump_json(indent=2)}\n",
            encoding="utf-8",
        )

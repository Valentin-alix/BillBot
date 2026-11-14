import argparse
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from DBDofusUnity.consts import (
    GAME_MAPPINGS_DETAILED_JSON_FILE,
    NON_OBF_NEW_DUMP_CS_FILE,
    NON_OBF_PROTO_ACCESSES_FILE,
    NON_OBF_PROTOCOL_GAME_DUMP_CS_FILE,
    NON_OBF_SIGNATURE_OVERRIDES_FILE,
    OBF_PROTO_ACCESSES_FILE,
    OBF_PROTOCOL_GAME_DUMP_CS_FILE,
    PINNED_PAIRS_FILE,
    PROTOS_ROOT,
)
from DBDofusUnity.proto_mapper_assembly.controllers.access_signatures import (
    load_message_access_signatures_from_messages,
)
from DBDofusUnity.proto_mapper_assembly.controllers.enum_signatures import (
    build_canonical_enum_signature,
)
from DBDofusUnity.proto_mapper_assembly.controllers.excluded_non_obf import compute_excluded_non_obf_names
from DBDofusUnity.proto_mapper_assembly.controllers.game_mappings import (
    load_game_mappings_document,
    resolve_generated_message_alias,
)
from DBDofusUnity.proto_mapper_assembly.controllers.message_fields import build_dump_cs_field_lookup
from DBDofusUnity.proto_mapper_assembly.controllers.new_dump_cs import load_new_dump_cs_messages
from DBDofusUnity.proto_mapper_assembly.controllers.non_obf_bootstrap import (
    build_bootstrap_message_overlay,
    build_missing_bootstrap_message_error,
)
from DBDofusUnity.proto_mapper_assembly.controllers.pinned_pairs import (
    build_resolved_field_mapping,
    load_pinned_pairs,
    resolve_pinned_pairs_non_obf_targets,
)
from DBDofusUnity.proto_mapper_assembly.controllers.signature_override_application import (
    validate_stored_field_bindings,
)
from DBDofusUnity.proto_mapper_assembly.controllers.signature_rekeying import (
    dedupe_function_signatures,
    rekey_field_signatures,
    rekey_function_signatures,
)
from DBDofusUnity.proto_mapper_assembly.helpers.non_obf_names import build_filtered_message_namespace
from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import (
    FieldAccessSignatures,
    MessageAccessSignature,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import (
    DumpCSMessage,
    DumpCSMessageField,
    FieldKey,
)
from DBDofusUnity.proto_mapper_assembly.interfaces.enum_mapping import EnumSignatureEntry
from DBDofusUnity.proto_mapper_assembly.interfaces.function_access_signature import FunctionAccessSignature
from DBDofusUnity.proto_mapper_assembly.interfaces.game_mappings import GameMappingEntry, GameMappingsDocument
from DBDofusUnity.proto_mapper_assembly.interfaces.pinned_pairs import PinnedPair, PinnedPairsConfig
from DBDofusUnity.proto_mapper_assembly.interfaces.signature_overrides import (
    EnumHintSlot,
    EnumSignatureOverrideHint,
    FieldOverrideBinding,
    SignatureOverrideEntry,
    SignatureOverridesFile,
)
from DBDofusUnity.proto_mapper_assembly.parsers.dump_cs_parser import parse_messages
from DBDofusUnity.proto_mapper_assembly.parsers.proto_accesses_parser import parse_access_trace_document
from DBDofusUnity.proto_mapper_assembly.parsers.protobuf_dump_cs import build_dump_cs_messages_from_pb2
from DBDofusUnity.proto_mapper_assembly.scoring.enum_similarity import EnumFunctionSignatureResolver
from DBDofusUnity.proto_mapper_assembly.scoring.primitives import get_average_best_similarity_sequences
from DBDofusUnity.proto_mapper_assembly.scoring.signature_scoring import function_similarity_from_keys


class _ExportContext(BaseModel):
    pinned_pairs_config: PinnedPairsConfig
    non_obf_messages_by_cls: dict[str, DumpCSMessage]
    excluded_non_obf_names: frozenset[str]
    bootstrap_messages_by_cls: dict[str, DumpCSMessage]
    non_obf_messages_with_bootstrap_by_cls: dict[str, DumpCSMessage]
    virtual_field_clean_names_by_cls: dict[str, set[str]]
    obf_signatures_by_cls: dict[str, MessageAccessSignature]
    game_mappings: GameMappingsDocument
    game_mappings_by_obf_cls: dict[str, GameMappingEntry]
    obf_enum_signatures_by_name: dict[str, EnumSignatureEntry]
    non_obf_enum_signatures_by_name: dict[str, EnumSignatureEntry]
    obf_function_signature_by_address: dict[str, FunctionAccessSignature]


@dataclass(frozen=True)
class SignatureOverrideExportPaths:
    pinned_pairs_path: Path
    signature_overrides_path: Path
    obf_dump_cs_path: Path
    non_obf_dump_cs_path: Path
    new_dump_cs_path: Path
    obf_proto_accesses_path: Path
    non_obf_proto_accesses_path: Path
    game_mappings_path: Path


@dataclass(frozen=True)
class _ResolvedMappedField:
    obf_field: DumpCSMessageField
    non_obf_field: DumpCSMessageField
    obf_offset: int
    non_obf_offset: int
    non_obf_clean_name: str
    non_obf_property_name: str


def run_export_signature_overrides(
    *,
    obf_dir: Path | None = None,
    export_paths: SignatureOverrideExportPaths | None = None,
) -> SignatureOverridesFile:
    resolved_export_paths = export_paths if export_paths is not None else _build_export_paths(obf_dir=obf_dir)

    # Rewrite rather than merge so removed mappings cannot leave stale overrides.
    generated_overrides = build_signature_overrides(
        resolved_export_paths.pinned_pairs_path, export_paths=resolved_export_paths
    )

    print(f"Built overrides for {len(generated_overrides.root)} mapped messages.")
    print(f"Validated generated message definitions from {resolved_export_paths.new_dump_cs_path}.")

    resolved_export_paths.signature_overrides_path.write_text(
        generated_overrides.model_dump_json(indent=2, exclude_defaults=True),
        encoding="utf-8",
    )
    print(f"Written to {resolved_export_paths.signature_overrides_path}")
    return generated_overrides


def main() -> None:
    arguments = _build_argument_parser().parse_args()
    run_export_signature_overrides(obf_dir=arguments.obf_dir)


def _build_argument_parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description="Export message access signature overrides.")
    argument_parser.add_argument(
        "--obf-dir",
        type=Path,
        help="Use this obfuscated dump folder for pins, obf inputs, mappings, and generated overrides.",
    )
    return argument_parser


def _build_export_paths(*, obf_dir: Path | None) -> SignatureOverrideExportPaths:
    if obf_dir is None:
        return SignatureOverrideExportPaths(
            pinned_pairs_path=PINNED_PAIRS_FILE,
            signature_overrides_path=NON_OBF_SIGNATURE_OVERRIDES_FILE,
            obf_dump_cs_path=OBF_PROTOCOL_GAME_DUMP_CS_FILE,
            non_obf_dump_cs_path=NON_OBF_PROTOCOL_GAME_DUMP_CS_FILE,
            new_dump_cs_path=NON_OBF_NEW_DUMP_CS_FILE,
            obf_proto_accesses_path=OBF_PROTO_ACCESSES_FILE,
            non_obf_proto_accesses_path=NON_OBF_PROTO_ACCESSES_FILE,
            game_mappings_path=GAME_MAPPINGS_DETAILED_JSON_FILE,
        )

    return SignatureOverrideExportPaths(
        pinned_pairs_path=obf_dir / "pinned_pairs.json",
        signature_overrides_path=obf_dir / "proto_accesses.override.json",
        obf_dump_cs_path=obf_dir / "cs" / "Ankama.Dofus.Protocol.Game.cs",
        non_obf_dump_cs_path=NON_OBF_PROTOCOL_GAME_DUMP_CS_FILE,
        new_dump_cs_path=NON_OBF_NEW_DUMP_CS_FILE,
        obf_proto_accesses_path=obf_dir / "proto_accesses.json",
        non_obf_proto_accesses_path=NON_OBF_PROTO_ACCESSES_FILE,
        game_mappings_path=obf_dir / "game_mappings_detailed.json",
    )


def build_signature_overrides(
    pinned_pairs_path: Path,
    *,
    export_paths: SignatureOverrideExportPaths | None = None,
) -> SignatureOverridesFile:
    if export_paths is None:
        default_export_paths = _build_export_paths(obf_dir=None)
        resolved_export_paths = SignatureOverrideExportPaths(
            pinned_pairs_path=pinned_pairs_path,
            signature_overrides_path=default_export_paths.signature_overrides_path,
            obf_dump_cs_path=default_export_paths.obf_dump_cs_path,
            non_obf_dump_cs_path=default_export_paths.non_obf_dump_cs_path,
            new_dump_cs_path=default_export_paths.new_dump_cs_path,
            obf_proto_accesses_path=default_export_paths.obf_proto_accesses_path,
            non_obf_proto_accesses_path=default_export_paths.non_obf_proto_accesses_path,
            game_mappings_path=default_export_paths.game_mappings_path,
        )
    else:
        resolved_export_paths = export_paths
    export_context = _load_export_context(resolved_export_paths)

    export_pairs = _build_export_pairs(
        game_mappings=export_context.game_mappings,
        pinned_pairs=export_context.pinned_pairs_config.pairs,
        non_obf_messages_with_bootstrap_by_cls=export_context.non_obf_messages_with_bootstrap_by_cls,
    )

    entries: dict[str, SignatureOverrideEntry] = {}
    dropped_bindings_by_non_obf_cls: dict[str, list[str]] = {}
    for pair in export_pairs:
        obf_sig = export_context.obf_signatures_by_cls.get(pair.obf)
        if obf_sig is None:
            err = "Missing obf signature for %s -> %s, skipping override", pair.obf, pair.non_obf
            raise ValueError(err)

        non_obf_dump_message = export_context.non_obf_messages_by_cls.get(pair.non_obf)
        if non_obf_dump_message is not None:
            entry, dropped = _build_rekeyed_override_entry(
                pair=pair,
                obf_sig=obf_sig,
                non_obf_message=non_obf_dump_message,
                virtual_field_clean_names=export_context.virtual_field_clean_names_by_cls.get(
                    pair.non_obf, set()
                ),
                game_mappings_by_obf_cls=export_context.game_mappings_by_obf_cls,
                obf_enum_signatures_by_name=export_context.obf_enum_signatures_by_name,
                non_obf_enum_signatures_by_name=export_context.non_obf_enum_signatures_by_name,
                obf_function_signature_by_address=export_context.obf_function_signature_by_address,
            )
        else:
            entry, dropped = _build_bootstrap_override_entry(
                pair=pair,
                pair_non_obf=pair.non_obf,
                obf_sig=obf_sig,
                bootstrap_messages_by_cls=export_context.bootstrap_messages_by_cls,
                game_mappings_by_obf_cls=export_context.game_mappings_by_obf_cls,
                obf_enum_signatures_by_name=export_context.obf_enum_signatures_by_name,
                non_obf_enum_signatures_by_name=export_context.non_obf_enum_signatures_by_name,
                obf_function_signature_by_address=export_context.obf_function_signature_by_address,
            )

        entries[pair.non_obf] = entry
        for property_name in sorted(dropped):
            dropped_bindings_by_non_obf_cls.setdefault(pair.non_obf, []).append(property_name)
        print(
            "Exported override "
            f"{pair.non_obf} ({len(entry.function_signatures)} func sigs, "
            f"{len(entry.field_signatures)} field sigs)"
        )

    _report_dropped_bindings(dropped_bindings_by_non_obf_cls)
    _report_export_fidelity(entries=entries, export_pairs=export_pairs, export_context=export_context)
    _report_group_coherence(export_pairs=export_pairs, export_context=export_context)
    return SignatureOverridesFile(root=entries)


def _report_dropped_bindings(dropped_bindings_by_non_obf_cls: dict[str, list[str]]) -> None:
    if not dropped_bindings_by_non_obf_cls:
        return
    total = sum(len(names) for names in dropped_bindings_by_non_obf_cls.values())
    print(
        f"Dropped {total} field binding(s) of incompatible shape across "
        f"{len(dropped_bindings_by_non_obf_cls)} message(s); review their protobuf declaration, "
        f"pin, or mapping to recover the shape evidence:"
    )
    for non_obf_cls, property_names in sorted(dropped_bindings_by_non_obf_cls.items()):
        print(f"  {non_obf_cls}: {', '.join(property_names)}")


_MIN_GROUP_COHERENCE = 0.90
"""Do not fill scattered descriptors: doing so would freeze an incorrect group mapping."""

_REPORT_SAMPLE_SIZE = 8


def _report_export_fidelity(
    *,
    entries: dict[str, SignatureOverrideEntry],
    export_pairs: Sequence[PinnedPair],
    export_context: _ExportContext,
) -> None:
    """Rekeying must be lossless; a self-score below 1.0 indicates lost evidence."""
    scores: list[float] = []
    imperfect: list[tuple[float, str]] = []
    silent_field_losses: list[str] = []
    for pair in export_pairs:
        entry = entries.get(pair.non_obf)
        obf_sig = export_context.obf_signatures_by_cls.get(pair.obf)
        if entry is None or obf_sig is None:
            continue
        exported_keys = tuple(
            dict.fromkeys(signature.similarity_key for signature in entry.function_signatures)
        )
        source_keys = obf_sig.function_similarity_keys
        if exported_keys or source_keys:
            score = get_average_best_similarity_sequences(
                exported_keys, source_keys, function_similarity_from_keys
            )
            scores.append(score)
            if score < 1.0:
                imperfect.append((score, pair.non_obf))
        if not entry.field_signatures and obf_sig.field_signatures:
            silent_field_losses.append(pair.non_obf)

    if not scores:
        return
    perfect = sum(1 for score in scores if score >= 1.0)
    mean_score = sum(scores) / len(scores)
    print(
        f"Function-bag fidelity against the obf source: mean {mean_score:.4f}, "
        f"{perfect}/{len(scores)} exact ({perfect / len(scores):.1%})."
    )
    for score, non_obf_cls in sorted(imperfect)[:_REPORT_SAMPLE_SIZE]:
        print(f"  {score:.3f} {non_obf_cls}")
    if len(imperfect) > _REPORT_SAMPLE_SIZE:
        print(f"  ... and {len(imperfect) - _REPORT_SAMPLE_SIZE} more")

    if silent_field_losses:
        print(
            f"{len(silent_field_losses)} message(s) exported with no field signature although the obf "
            f"side had some; their field mapping resolved nothing:"
        )
        for non_obf_cls in sorted(silent_field_losses)[:_REPORT_SAMPLE_SIZE]:
            print(f"  {non_obf_cls}")
        if len(silent_field_losses) > _REPORT_SAMPLE_SIZE:
            print(f"  ... and {len(silent_field_losses) - _REPORT_SAMPLE_SIZE} more")


def _report_group_coherence(
    *,
    export_pairs: Sequence[PinnedPair],
    export_context: _ExportContext,
) -> None:
    obf_descriptors_by_non_obf_descriptor: dict[str, Counter[str]] = {}
    for pair in export_pairs:
        obf_sig = export_context.obf_signatures_by_cls.get(pair.obf)
        non_obf_message = export_context.non_obf_messages_with_bootstrap_by_cls.get(pair.non_obf)
        if obf_sig is None or non_obf_message is None:
            continue
        obf_descriptors_by_non_obf_descriptor.setdefault(non_obf_message.file_descriptor, Counter())[
            obf_sig.file_descriptor
        ] += 1

    non_obf_size_by_descriptor = Counter(
        message.file_descriptor
        for message in export_context.non_obf_messages_by_cls.values()
        if message.composed_name not in export_context.excluded_non_obf_names
    )
    obf_size_by_descriptor = Counter(
        signature.file_descriptor for signature in export_context.obf_signatures_by_cls.values()
    )

    incoherent: list[tuple[float, str, str]] = []
    overfilled: list[tuple[int, int, str]] = []
    for non_obf_descriptor, obf_counts in obf_descriptors_by_non_obf_descriptor.items():
        dominant_descriptor, dominant_count = obf_counts.most_common(1)[0]
        coherence = dominant_count / sum(obf_counts.values())
        if coherence < _MIN_GROUP_COHERENCE:
            incoherent.append((coherence, non_obf_descriptor, dominant_descriptor))
            continue
        non_obf_size = non_obf_size_by_descriptor[non_obf_descriptor]
        obf_size = obf_size_by_descriptor[dominant_descriptor]
        if non_obf_size > obf_size:
            overfilled.append((non_obf_size, obf_size, non_obf_descriptor))

    print(f"Group coherence over {len(obf_descriptors_by_non_obf_descriptor)} file descriptor(s).")
    if incoherent:
        print(
            f"  {len(incoherent)} below {_MIN_GROUP_COHERENCE:.2f}; their members are split across "
            f"obf groups, so do not fill them:"
        )
        for coherence, non_obf_descriptor, dominant_descriptor in sorted(incoherent)[:_REPORT_SAMPLE_SIZE]:
            print(f"    {coherence:.2f} {non_obf_descriptor} -> mostly {dominant_descriptor}")
        if len(incoherent) > _REPORT_SAMPLE_SIZE:
            print(f"    ... and {len(incoherent) - _REPORT_SAMPLE_SIZE} more")
    if overfilled:
        print(
            f"  {len(overfilled)} with more non-obf members than the obf side has room for, which "
            f"only inflates the group denominator:"
        )
        for non_obf_size, obf_size, non_obf_descriptor in sorted(overfilled, reverse=True)[
            :_REPORT_SAMPLE_SIZE
        ]:
            print(f"    {non_obf_descriptor}: {non_obf_size} non-obf vs {obf_size} obf")
        if len(overfilled) > _REPORT_SAMPLE_SIZE:
            print(f"    ... and {len(overfilled) - _REPORT_SAMPLE_SIZE} more")


def _build_export_pairs(
    *,
    game_mappings: GameMappingsDocument,
    pinned_pairs: Iterable[PinnedPair],
    non_obf_messages_with_bootstrap_by_cls: dict[str, DumpCSMessage],
) -> list[PinnedPair]:
    composed_name_by_full_form: dict[str, str] = {
        _message_full_form(message): message.composed_name
        for message in non_obf_messages_with_bootstrap_by_cls.values()
    }
    # Generated proto aliases may differ from dump.cs names.
    generated_messages_by_cls = build_dump_cs_messages_from_pb2(PROTOS_ROOT / "non_obf")
    for message in non_obf_messages_with_bootstrap_by_cls.values():
        exported = resolve_generated_message_alias(
            message=message, generated_messages_by_cls=generated_messages_by_cls
        )
        composed_name_by_full_form.setdefault(_message_full_form(exported), message.composed_name)
    # Exported nested namespaces omit the C# Types wrapper.
    for message in non_obf_messages_with_bootstrap_by_cls.values():
        filtered_form = build_filtered_message_namespace(
            is_obf=False,
            message=message,
            messages_by_cls=non_obf_messages_with_bootstrap_by_cls,
        ).lstrip(".")
        composed_name_by_full_form.setdefault(filtered_form, message.composed_name)
    # Protocol roots have bare dump.cs names but retain their package in mappings.
    composed_name_by_root_name: dict[str, str] = {
        message.name: message.composed_name
        for message in non_obf_messages_with_bootstrap_by_cls.values()
        if message.namespace is None and message.parent_name is None
    }

    pairs_by_obf: dict[str, PinnedPair] = {}
    for entry in game_mappings.root.values():
        non_obf_full_form = entry.full_non_obf_msg_namespace.lstrip(".")
        non_obf_composed_name = composed_name_by_full_form.get(non_obf_full_form)
        if non_obf_composed_name is None:
            non_obf_composed_name = composed_name_by_root_name.get(non_obf_full_form.rsplit(".", 1)[-1])
        if non_obf_composed_name is None:
            error = (
                "No non-obf DumpCSMessage for game mapping "
                f"{entry.full_obf_msg_namespace} -> {non_obf_full_form}"
            )
            raise ValueError(error)
        pairs_by_obf[entry.full_obf_msg_namespace] = PinnedPair(
            obf=entry.full_obf_msg_namespace,
            non_obf=non_obf_composed_name,
            field_mapping_by_obf={},
        )

    for pair in pinned_pairs:
        pairs_by_obf[pair.obf] = pair

    return list(pairs_by_obf.values())


def _message_full_form(msg: DumpCSMessage) -> str:
    parts: list[str] = []
    if msg.namespace is not None:
        parts.append(msg.namespace.lower())
    if msg.parent_name is not None:
        parts.append(msg.parent_name)
    parts.append(msg.name)
    return ".".join(parts)


def _load_export_context(export_paths: SignatureOverrideExportPaths) -> _ExportContext:
    print("Parsing obf dump.cs")
    obf_messages = parse_messages(str(export_paths.obf_dump_cs_path))

    print("Parsing non-obf dump.cs")
    non_obf_messages = parse_messages(str(export_paths.non_obf_dump_cs_path))
    excluded_non_obf_names = compute_excluded_non_obf_names(non_obf_messages)

    obf_messages_by_cls = {message.composed_name: message for message in obf_messages}
    parsed_non_obf_messages_by_cls = {message.composed_name: message for message in non_obf_messages}
    bootstrap_messages_by_cls = load_new_dump_cs_messages(export_paths.new_dump_cs_path).root

    overlay = build_bootstrap_message_overlay(
        bootstrap_messages_by_cls=bootstrap_messages_by_cls,
        non_obf_messages_by_cls=parsed_non_obf_messages_by_cls,
    )

    print("Loading obf access signatures")
    obf_signatures_by_cls = load_message_access_signatures_from_messages(
        str(export_paths.obf_proto_accesses_path), obf_messages
    )

    print("Loading game mappings")
    game_mappings = load_game_mappings_document(export_paths.game_mappings_path)

    print("Loading enum access signatures")
    obf_access_trace = parse_access_trace_document(str(export_paths.obf_proto_accesses_path))
    obf_enum_signatures_by_name = obf_access_trace.enum_signatures_by_name
    non_obf_enum_signatures_by_name = parse_access_trace_document(
        str(export_paths.non_obf_proto_accesses_path)
    ).enum_signatures_by_name
    obf_function_signature_by_address = dict(
        EnumFunctionSignatureResolver(obf_access_trace).signature_by_address
    )

    non_obf_messages_with_bootstrap_by_cls = {**overlay.messages_by_cls, **bootstrap_messages_by_cls}
    pinned_pairs = resolve_pinned_pairs_non_obf_targets(
        pinned_pairs=load_pinned_pairs(export_paths.pinned_pairs_path),
        obf_messages_by_cls=obf_messages_by_cls,
        non_obf_messages_by_cls=non_obf_messages_with_bootstrap_by_cls,
    )
    game_mappings_by_obf_cls = {entry.full_obf_msg_namespace: entry for entry in game_mappings.root.values()}

    return _ExportContext(
        pinned_pairs_config=pinned_pairs,
        non_obf_messages_by_cls=overlay.messages_by_cls,
        excluded_non_obf_names=excluded_non_obf_names,
        bootstrap_messages_by_cls=bootstrap_messages_by_cls,
        non_obf_messages_with_bootstrap_by_cls=non_obf_messages_with_bootstrap_by_cls,
        virtual_field_clean_names_by_cls=overlay.virtual_field_clean_names_by_cls,
        obf_signatures_by_cls=obf_signatures_by_cls,
        game_mappings=game_mappings,
        game_mappings_by_obf_cls=game_mappings_by_obf_cls,
        obf_enum_signatures_by_name=obf_enum_signatures_by_name,
        non_obf_enum_signatures_by_name=non_obf_enum_signatures_by_name,
        obf_function_signature_by_address=obf_function_signature_by_address,
    )


def _build_rekeyed_override_entry(
    *,
    pair: PinnedPair,
    obf_sig: MessageAccessSignature,
    non_obf_message: DumpCSMessage,
    virtual_field_clean_names: set[str],
    game_mappings_by_obf_cls: dict[str, GameMappingEntry],
    obf_enum_signatures_by_name: dict[str, EnumSignatureEntry],
    non_obf_enum_signatures_by_name: dict[str, EnumSignatureEntry],
    obf_function_signature_by_address: dict[str, FunctionAccessSignature],
) -> tuple[SignatureOverrideEntry, frozenset[str]]:
    game_mapping_entry = game_mappings_by_obf_cls.get(obf_sig.message_cls)
    field_mapping = build_resolved_field_mapping(pair=pair, game_mapping_entry=game_mapping_entry)
    mapped_fields = _resolve_mapped_fields(
        obf_message=obf_sig.dump_cs_msg,
        non_obf_message=non_obf_message,
        field_mapping=field_mapping,
    )
    mapped_real_fields = [
        mapped_field
        for mapped_field in mapped_fields
        if mapped_field.non_obf_clean_name not in virtual_field_clean_names
    ]
    obf_to_non_obf_offset = {
        mapped_field.obf_offset: mapped_field.non_obf_offset for mapped_field in mapped_real_fields
    }
    non_obf_field_by_obf_field_key: dict[FieldKey, DumpCSMessageField] = {
        mapped_field.obf_field.field_key: mapped_field.non_obf_field for mapped_field in mapped_real_fields
    }
    rekeyed_field_signatures = rekey_field_signatures(
        list(obf_sig.field_signatures),
        non_obf_field_by_obf_field_key,
        obf_to_non_obf_offset,
    )
    rekeyed_function_signatures = rekey_function_signatures(
        list(obf_sig.function_signatures),
        obf_to_non_obf_offset,
    )

    virtual_obf_field_binding_by_non_obf_property_name = {
        mapped_field.non_obf_property_name: _build_field_override_binding(mapped_field)
        for mapped_field in mapped_fields
        if mapped_field.non_obf_clean_name in virtual_field_clean_names
    }
    virtual_field_signatures = _build_field_signatures_by_non_obf_property_name(
        obf_sig=obf_sig,
        non_obf_message=non_obf_message,
        obf_field_binding_by_non_obf_property_name=virtual_obf_field_binding_by_non_obf_property_name,
    )
    merged_field_signatures: dict[str, FieldAccessSignatures] = {
        **rekeyed_field_signatures,
        **virtual_field_signatures,
    }
    incompatible_property_names = validate_stored_field_bindings(
        non_obf_cls=pair.non_obf,
        non_obf_message=non_obf_message,
        obf_field_binding_by_non_obf_property_name=virtual_obf_field_binding_by_non_obf_property_name,
        field_signatures_by_non_obf_property_name=merged_field_signatures,
    )
    for property_name in incompatible_property_names:
        virtual_obf_field_binding_by_non_obf_property_name.pop(property_name, None)
        merged_field_signatures.pop(property_name, None)

    return SignatureOverrideEntry(
        function_signatures=rekeyed_function_signatures,
        field_signatures=merged_field_signatures,
        obf_field_binding_by_non_obf_property_name=virtual_obf_field_binding_by_non_obf_property_name,
        enum_signature_hints_by_non_obf_prop_name=_build_enum_signature_hints_by_non_obf_prop_name(
            pair=pair,
            obf_message=obf_sig.dump_cs_msg,
            non_obf_message=non_obf_message,
            game_mapping_entry=game_mapping_entry,
            obf_enum_signatures_by_name=obf_enum_signatures_by_name,
            non_obf_enum_signatures_by_name=non_obf_enum_signatures_by_name,
            obf_function_signature_by_address=obf_function_signature_by_address,
        ),
    ), incompatible_property_names


def _resolve_mapped_fields(
    *,
    obf_message: DumpCSMessage,
    non_obf_message: DumpCSMessage,
    field_mapping: dict[str, str],
) -> list[_ResolvedMappedField]:
    obf_fields_by_name = build_dump_cs_field_lookup(obf_message)
    non_obf_fields_by_name = build_dump_cs_field_lookup(non_obf_message)

    mapped_fields: list[_ResolvedMappedField] = []
    for obf_name, non_obf_clean_name in field_mapping.items():
        obf_field = obf_fields_by_name.get(obf_name)
        non_obf_field = non_obf_fields_by_name.get(non_obf_clean_name)
        if obf_field is None or non_obf_field is None or non_obf_field.property_name is None:
            continue
        mapped_fields.append(
            _ResolvedMappedField(
                obf_field=obf_field,
                non_obf_field=non_obf_field,
                obf_offset=obf_field.memory_offset,
                non_obf_offset=non_obf_field.memory_offset,
                non_obf_clean_name=non_obf_field.clean_field_name,
                non_obf_property_name=non_obf_field.property_name,
            )
        )
    return mapped_fields


def _build_field_override_binding(mapped_field: _ResolvedMappedField) -> FieldOverrideBinding:
    return FieldOverrideBinding(
        obf_field_name=mapped_field.obf_field.field_name,
        obf_memory_offset=mapped_field.obf_offset,
    )


def _build_obf_field_binding_by_non_obf_property_name(
    *,
    obf_message: DumpCSMessage,
    non_obf_message: DumpCSMessage,
    field_mapping: dict[str, str],
) -> dict[str, FieldOverrideBinding]:
    return {
        mapped_field.non_obf_property_name: _build_field_override_binding(mapped_field)
        for mapped_field in _resolve_mapped_fields(
            obf_message=obf_message,
            non_obf_message=non_obf_message,
            field_mapping=field_mapping,
        )
    }


def _build_field_signatures_by_non_obf_property_name(
    *,
    obf_sig: MessageAccessSignature,
    non_obf_message: DumpCSMessage,
    obf_field_binding_by_non_obf_property_name: dict[str, FieldOverrideBinding],
) -> dict[str, FieldAccessSignatures]:
    obf_field_sigs_by_key = {
        field_signature.field_key: field_signature for field_signature in obf_sig.field_signatures
    }
    obf_fields_by_name = {
        **build_dump_cs_field_lookup(obf_sig.dump_cs_msg),
        **{field.field_name: field for field in obf_sig.dump_cs_msg.fields},
    }
    non_obf_field_by_property_name = {
        field.property_name: field for field in non_obf_message.fields if field.property_name is not None
    }
    result: dict[str, FieldAccessSignatures] = {}
    for non_obf_property_name, obf_field_binding in obf_field_binding_by_non_obf_property_name.items():
        obf_field = obf_fields_by_name.get(obf_field_binding.obf_field_name)
        non_obf_field = non_obf_field_by_property_name.get(non_obf_property_name)
        if obf_field is None or non_obf_field is None:
            continue
        obf_field_signature = obf_field_sigs_by_key.get(obf_field.field_key)
        if obf_field_signature is None:
            obf_field_signature = FieldAccessSignatures(
                field_key=obf_field.field_key,
                field_type_shape=obf_field.field_type_shape,
                accesses=[],
                is_traced=False,
            )
        remapped_non_obf_field = non_obf_field.model_copy(
            update={"memory_offset": obf_field_binding.obf_memory_offset}
        )
        result[non_obf_property_name] = obf_field_signature.model_copy(
            update={
                "field_key": remapped_non_obf_field.field_key,
            }
        )
    return result


def _build_bootstrap_override_entry(
    *,
    pair: PinnedPair,
    pair_non_obf: str,
    obf_sig: MessageAccessSignature,
    bootstrap_messages_by_cls: dict[str, DumpCSMessage],
    game_mappings_by_obf_cls: dict[str, GameMappingEntry],
    obf_enum_signatures_by_name: dict[str, EnumSignatureEntry],
    non_obf_enum_signatures_by_name: dict[str, EnumSignatureEntry],
    obf_function_signature_by_address: dict[str, FunctionAccessSignature],
) -> tuple[SignatureOverrideEntry, frozenset[str]]:
    bootstrap_message = bootstrap_messages_by_cls.get(pair_non_obf)
    if bootstrap_message is None:
        raise ValueError(build_missing_bootstrap_message_error(pair_non_obf))

    game_mapping_entry = game_mappings_by_obf_cls.get(obf_sig.message_cls)
    field_mapping = build_resolved_field_mapping(pair=pair, game_mapping_entry=game_mapping_entry)
    field_binding_by_non_obf_property_name = _build_obf_field_binding_by_non_obf_property_name(
        obf_message=obf_sig.dump_cs_msg,
        non_obf_message=bootstrap_message,
        field_mapping=field_mapping,
    )

    bootstrap_field_signatures = _build_field_signatures_by_non_obf_property_name(
        obf_sig=obf_sig,
        non_obf_message=bootstrap_message,
        obf_field_binding_by_non_obf_property_name=field_binding_by_non_obf_property_name,
    )

    incompatible_property_names = validate_stored_field_bindings(
        non_obf_cls=pair_non_obf,
        non_obf_message=bootstrap_message,
        obf_field_binding_by_non_obf_property_name=field_binding_by_non_obf_property_name,
        field_signatures_by_non_obf_property_name=bootstrap_field_signatures,
    )
    for property_name in incompatible_property_names:
        field_binding_by_non_obf_property_name.pop(property_name, None)
        bootstrap_field_signatures.pop(property_name, None)

    return SignatureOverrideEntry(
        function_signatures=dedupe_function_signatures(obf_sig.function_signatures),
        field_signatures=bootstrap_field_signatures,
        obf_field_binding_by_non_obf_property_name=field_binding_by_non_obf_property_name,
        enum_signature_hints_by_non_obf_prop_name=_build_enum_signature_hints_by_non_obf_prop_name(
            pair=pair,
            obf_message=obf_sig.dump_cs_msg,
            non_obf_message=bootstrap_message,
            game_mapping_entry=game_mapping_entry,
            obf_enum_signatures_by_name=obf_enum_signatures_by_name,
            non_obf_enum_signatures_by_name=non_obf_enum_signatures_by_name,
            obf_function_signature_by_address=obf_function_signature_by_address,
        ),
    ), incompatible_property_names


def _build_enum_signature_hints_by_non_obf_prop_name(
    *,
    pair: PinnedPair,
    obf_message: DumpCSMessage,
    non_obf_message: DumpCSMessage,
    game_mapping_entry: GameMappingEntry | None,
    obf_enum_signatures_by_name: dict[str, EnumSignatureEntry],
    non_obf_enum_signatures_by_name: dict[str, EnumSignatureEntry],
    obf_function_signature_by_address: dict[str, FunctionAccessSignature],
) -> dict[str, dict[EnumHintSlot, EnumSignatureOverrideHint]]:
    obf_fields_by_name = build_dump_cs_field_lookup(obf_message)
    non_obf_fields_by_name = build_dump_cs_field_lookup(non_obf_message)
    resolved_field_mapping = build_resolved_field_mapping(pair=pair, game_mapping_entry=game_mapping_entry)
    enum_signature_hints: dict[str, dict[EnumHintSlot, EnumSignatureOverrideHint]] = {}

    for obf_field_name, non_obf_field_name in resolved_field_mapping.items():
        obf_field = obf_fields_by_name.get(obf_field_name)
        non_obf_field = non_obf_fields_by_name.get(non_obf_field_name)
        if obf_field is None or non_obf_field is None:
            continue

        obf_enum_types = obf_field.enum_field_types
        non_obf_enum_types = non_obf_field.enum_field_types
        key_hint = _build_positional_enum_signature_hint_from_optional_types(
            obf_enum_type=obf_enum_types.key,
            non_obf_enum_type=non_obf_enum_types.key,
            obf_enum_signatures_by_name=obf_enum_signatures_by_name,
            non_obf_enum_signatures_by_name=non_obf_enum_signatures_by_name,
            obf_function_signature_by_address=obf_function_signature_by_address,
        )
        if key_hint is not None:
            enum_signature_hints.setdefault(non_obf_field_name, {})["key"] = key_hint

        value_hint = _build_positional_enum_signature_hint_from_optional_types(
            obf_enum_type=obf_enum_types.value,
            non_obf_enum_type=non_obf_enum_types.value,
            obf_enum_signatures_by_name=obf_enum_signatures_by_name,
            non_obf_enum_signatures_by_name=non_obf_enum_signatures_by_name,
            obf_function_signature_by_address=obf_function_signature_by_address,
        )
        if value_hint is not None:
            enum_signature_hints.setdefault(non_obf_field_name, {})["value"] = value_hint

    return enum_signature_hints


def _build_positional_enum_signature_hint_from_optional_types(
    *,
    obf_enum_type: str | None,
    non_obf_enum_type: str | None,
    obf_enum_signatures_by_name: dict[str, EnumSignatureEntry],
    non_obf_enum_signatures_by_name: dict[str, EnumSignatureEntry],
    obf_function_signature_by_address: dict[str, FunctionAccessSignature],
) -> EnumSignatureOverrideHint | None:
    if obf_enum_type is None or non_obf_enum_type is None:
        return None

    return _build_positional_enum_signature_hint(
        obf_enum_type=obf_enum_type,
        non_obf_enum_type=non_obf_enum_type,
        obf_enum_signatures_by_name=obf_enum_signatures_by_name,
        non_obf_enum_signatures_by_name=non_obf_enum_signatures_by_name,
        obf_function_signature_by_address=obf_function_signature_by_address,
    )


def _build_positional_enum_signature_hint(
    *,
    obf_enum_type: str,
    non_obf_enum_type: str,
    obf_enum_signatures_by_name: dict[str, EnumSignatureEntry],
    non_obf_enum_signatures_by_name: dict[str, EnumSignatureEntry],
    obf_function_signature_by_address: dict[str, FunctionAccessSignature],
) -> EnumSignatureOverrideHint | None:
    obf_enum_signature = obf_enum_signatures_by_name.get(obf_enum_type)
    non_obf_enum_signature = non_obf_enum_signatures_by_name.get(non_obf_enum_type)
    if obf_enum_signature is None or non_obf_enum_signature is None:
        return None

    sorted_obf_values = sorted(obf_enum_signature.member_value_to_name, key=int)
    sorted_non_obf_values = sorted(non_obf_enum_signature.member_value_to_name, key=int)
    value_mapping = dict(zip(sorted_obf_values, sorted_non_obf_values, strict=False))
    if not value_mapping:
        return None

    canonical_signature = build_canonical_enum_signature(
        obf_enum_signature=obf_enum_signature,
        value_mapping=value_mapping,
        non_obf_value_to_name=non_obf_enum_signature.member_value_to_name,
        obf_function_signature_by_address=obf_function_signature_by_address,
    )
    if not canonical_signature.member_value_to_name:
        return None

    return EnumSignatureOverrideHint(
        non_obf_enum_type=non_obf_enum_type,
        signature=canonical_signature,
    )


if __name__ == "__main__":
    main()

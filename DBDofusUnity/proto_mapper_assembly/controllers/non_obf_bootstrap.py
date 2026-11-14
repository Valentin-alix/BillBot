from typing import NamedTuple

from DBDofusUnity.consts import NON_OBF_NEW_DUMP_CS_FILE
from DBDofusUnity.proto_mapper_assembly.controllers.message_fields import bind_field_signatures_to_message_fields
from DBDofusUnity.proto_mapper_assembly.controllers.signature_override_application import _apply_field_binding_remapping
from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import FieldAccessSignatures, MessageAccessSignature
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage
from DBDofusUnity.proto_mapper_assembly.interfaces.signature_overrides import SignatureOverrideEntry


class BootstrapMessageOverlay(NamedTuple):
    messages_by_cls: dict[str, DumpCSMessage]
    virtual_field_clean_names_by_cls: dict[str, set[str]]


def build_missing_bootstrap_message_error(non_obf_cls: str) -> str:
    return (
        f"Missing generated message definition for override-only non-obf class {non_obf_cls!r} in "
        f"{NON_OBF_NEW_DUMP_CS_FILE}. Check that the message is defined in the non-obf .proto files, "
        "then run uv run python -m scripts.maintenance.main synchronize-protos."
    )


def build_bootstrap_message_overlay(
    *,
    bootstrap_messages_by_cls: dict[str, DumpCSMessage],
    non_obf_messages_by_cls: dict[str, DumpCSMessage],
) -> BootstrapMessageOverlay:
    """Prefer bootstrap entries only when they add virtual fields missing from the parsed dump."""
    overridden_messages = dict(non_obf_messages_by_cls)
    virtual_field_clean_names_by_cls: dict[str, set[str]] = {}
    for non_obf_cls, bootstrap_message in bootstrap_messages_by_cls.items():
        real_message = non_obf_messages_by_cls.get(non_obf_cls)
        if real_message is None:
            continue
        real_clean_names = {field.clean_field_name for field in real_message.fields}
        extra_clean_names = {
            field.clean_field_name
            for field in bootstrap_message.fields
            if field.clean_field_name not in real_clean_names
        }
        if not extra_clean_names:
            continue
        overridden_messages[non_obf_cls] = bootstrap_message
        virtual_field_clean_names_by_cls[non_obf_cls] = extra_clean_names
    return BootstrapMessageOverlay(overridden_messages, virtual_field_clean_names_by_cls)


def rewire_signatures_to_bootstrap_messages(
    *,
    messages_by_cls: dict[str, DumpCSMessage],
    non_obf_signatures_by_cls: dict[str, MessageAccessSignature],
) -> dict[str, MessageAccessSignature]:
    rewired_signatures = dict(non_obf_signatures_by_cls)
    for non_obf_cls, message in messages_by_cls.items():
        real_signature = non_obf_signatures_by_cls.get(non_obf_cls)
        if real_signature is not None:
            bootstrap_field_keys = {field.field_key for field in message.fields}
            compatible_field_signatures = [
                field_signature
                for field_signature in real_signature.field_signatures
                if field_signature.field_key in bootstrap_field_keys
            ]
            rewired_signatures[non_obf_cls] = real_signature.model_copy(
                update={"dump_cs_msg": message, "field_signatures": compatible_field_signatures}
            )
    return rewired_signatures


def inject_synthetic_non_obf_entries_for_override_only_messages(
    *,
    overrides: dict[str, SignatureOverrideEntry],
    bootstrap_messages_by_cls: dict[str, DumpCSMessage],
    non_obf_messages_by_cls: dict[str, DumpCSMessage],
    non_obf_signatures_by_cls: dict[str, MessageAccessSignature],
) -> tuple[dict[str, DumpCSMessage], dict[str, MessageAccessSignature]]:
    result_messages = dict(non_obf_messages_by_cls)
    result_signatures = dict(non_obf_signatures_by_cls)

    override_only_messages: list[tuple[str, SignatureOverrideEntry, DumpCSMessage]] = []
    for non_obf_cls, override in overrides.items():
        if non_obf_cls in result_messages:
            continue
        bootstrap_message = bootstrap_messages_by_cls.get(non_obf_cls)
        if bootstrap_message is None:
            message = build_missing_bootstrap_message_error(non_obf_cls)
            raise ValueError(message)
        remapped_bootstrap_message = _apply_field_binding_remapping(
            bootstrap_message,
            override.obf_field_binding_by_non_obf_property_name,
        )
        result_messages[non_obf_cls] = remapped_bootstrap_message
        override_only_messages.append((non_obf_cls, override, remapped_bootstrap_message))

    for non_obf_cls, override, bootstrap_message in override_only_messages:
        declared_bootstrap_field_signatures = [
            FieldAccessSignatures(
                field_key=field.field_key,
                field_type_shape=field.field_type_shape,
                accesses=[],
            )
            for field in bootstrap_message.fields
            if field.is_declared_proto_shape_field
        ]
        field_signatures = bind_field_signatures_to_message_fields(
            field_signatures=list(override.field_signatures.values()) or declared_bootstrap_field_signatures,
            message=bootstrap_message,
        )
        result_signatures[non_obf_cls] = MessageAccessSignature(
            message_cls=non_obf_cls,
            file_descriptor=bootstrap_message.file_descriptor,
            dump_cs_msg=bootstrap_message,
            function_signatures=list(override.function_signatures),
            field_signatures=field_signatures,
        )

    return result_messages, result_signatures

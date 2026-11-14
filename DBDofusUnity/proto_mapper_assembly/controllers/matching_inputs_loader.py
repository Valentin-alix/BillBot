from pathlib import Path

from DBDofusUnity.consts import (
    NON_OBF_NEW_DUMP_CS_FILE,
    NON_OBF_PROTO_ACCESSES_FILE,
    NON_OBF_PROTOCOL_GAME_DUMP_CS_FILE,
    NON_OBF_SIGNATURE_OVERRIDES_FILE,
    OBF_PROTO_ACCESSES_FILE,
    OBF_PROTOCOL_GAME_DUMP_CS_FILE,
    PROTOS_ROOT,
)
from DBDofusUnity.proto_mapper_assembly.controllers.access_signatures import (
    build_message_access_signatures_from_trace,
)
from DBDofusUnity.proto_mapper_assembly.controllers.excluded_non_obf import (
    drop_excluded_signature_overrides,
    compute_excluded_non_obf_names,
    split_excluded_non_obf_messages,
)
from DBDofusUnity.proto_mapper_assembly.controllers.new_dump_cs import load_new_dump_cs_messages
from DBDofusUnity.proto_mapper_assembly.controllers.non_obf_matching_inputs import (
    prepare_non_obf_matching_inputs,
)
from DBDofusUnity.proto_mapper_assembly.controllers.signature_overrides import load_signature_overrides
from DBDofusUnity.proto_mapper_assembly.interfaces.matching_inputs import MatchingInputs
from DBDofusUnity.proto_mapper_assembly.parsers.dump_cs_parser import parse_messages
from DBDofusUnity.proto_mapper_assembly.parsers.proto_accesses_parser import parse_access_trace_document


def load_matching_inputs(
    *,
    obf_dump_cs_path: Path = OBF_PROTOCOL_GAME_DUMP_CS_FILE,
    non_obf_dump_cs_path: Path = NON_OBF_PROTOCOL_GAME_DUMP_CS_FILE,
    obf_proto_accesses_path: Path = OBF_PROTO_ACCESSES_FILE,
    non_obf_proto_accesses_path: Path = NON_OBF_PROTO_ACCESSES_FILE,
    bootstrap_non_obf_dump_cs_path: Path = NON_OBF_NEW_DUMP_CS_FILE,
    signature_overrides_path: Path = NON_OBF_SIGNATURE_OVERRIDES_FILE,
    non_obf_protos_dir: Path = PROTOS_ROOT / "non_obf" / "game",
) -> MatchingInputs:
    obf_messages = parse_messages(str(obf_dump_cs_path))
    all_non_obf_messages = parse_messages(str(non_obf_dump_cs_path))
    non_obf_messages, excluded_non_obf_names = split_excluded_non_obf_messages(
        messages=all_non_obf_messages,
        excluded_message_names=compute_excluded_non_obf_names(
            all_non_obf_messages, protos_dir=non_obf_protos_dir
        ),
    )
    obf_messages_by_cls = {message.composed_name: message for message in obf_messages}
    non_obf_messages_by_cls = {message.composed_name: message for message in non_obf_messages}

    obf_access_trace = parse_access_trace_document(str(obf_proto_accesses_path))
    non_obf_access_trace = parse_access_trace_document(str(non_obf_proto_accesses_path))
    obf_enum_signatures_by_name = obf_access_trace.enum_signatures_by_name
    non_obf_enum_signatures_by_name = non_obf_access_trace.enum_signatures_by_name

    obf_signatures_by_cls = build_message_access_signatures_from_trace(
        access_trace=obf_access_trace,
        messages=obf_messages,
    )
    # Use all classes: trace references must resolve even outside the selected messages.
    non_obf_signatures_by_cls = {
        cls: signature
        for cls, signature in build_message_access_signatures_from_trace(
            access_trace=non_obf_access_trace,
            messages=all_non_obf_messages,
        ).items()
        if cls not in excluded_non_obf_names
    }

    bootstrap_messages_by_cls = load_new_dump_cs_messages(bootstrap_non_obf_dump_cs_path).root
    stored_overrides = drop_excluded_signature_overrides(
        overrides=load_signature_overrides(signature_overrides_path).root,
        dropped_message_names=excluded_non_obf_names,
    )
    prepared_non_obf_messages_by_cls, prepared_non_obf_signatures_by_cls = prepare_non_obf_matching_inputs(
        overrides=stored_overrides,
        bootstrap_messages_by_cls=bootstrap_messages_by_cls,
        non_obf_signatures_by_cls=non_obf_signatures_by_cls,
        non_obf_messages_by_cls=non_obf_messages_by_cls,
        non_obf_enum_signatures_by_name=non_obf_enum_signatures_by_name,
    )
    return MatchingInputs(
        obf_messages_by_cls=obf_messages_by_cls,
        non_obf_messages_by_cls=prepared_non_obf_messages_by_cls,
        obf_signatures_by_cls=obf_signatures_by_cls,
        non_obf_signatures_by_cls=prepared_non_obf_signatures_by_cls,
        signature_overrides_by_non_obf_cls=stored_overrides,
        obf_enum_signatures_by_name=obf_enum_signatures_by_name,
        non_obf_enum_signatures_by_name=non_obf_enum_signatures_by_name,
        obf_access_trace=obf_access_trace,
        non_obf_access_trace=non_obf_access_trace,
    )

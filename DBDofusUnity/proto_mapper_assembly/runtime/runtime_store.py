import json
import logging
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from threading import RLock
from time import perf_counter
from uuid import uuid4

from google.protobuf.descriptor import FieldDescriptor
from google.protobuf.message import Message

from DBDofusUnity.consts import RUNTIME_DATA_FILE
from DBDofusUnity.proto_mapper_assembly.helpers.non_obf_names import build_filtered_message_namespace
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage
from DBDofusUnity.proto_mapper_assembly.interfaces.runtime_data import (
    NormalizedRuntimeInstance,
    ObservedRootObfMessage,
    RuntimeCaptureDocument,
    RuntimeInstance,
    RuntimeRoot,
)
from DBDofusUnity.proto_mapper_assembly.runtime.proto_schema import get_obfuscated_proto_schema_fingerprint
from src.core.config import ENABLE_MSG_CAPTURE
from utils.protobuf import is_repeated_field
from utils.singleton import Singleton

MAX_COUNT_BY_NAME = 1_500
logger = logging.getLogger()


ContentByName = dict[str, list[RuntimeInstance]]
type CaptureSequencesBySession = dict[str, tuple[int, ...]]


@dataclass
class RuntimeDataStore(metaclass=Singleton):
    _lock: RLock = field(init=False, default_factory=RLock)
    _capture_sequence_index: int | None = field(init=False, default=None)
    _capture_session_id: str | None = field(init=False, default=None)
    _capture_target_path: Path | None = field(init=False, default=None)
    _has_unsaved_changes: bool = field(init=False, default=False)

    def __hash__(self) -> int:
        return 0

    def start_connection_capture_sequence(self) -> None:
        if not ENABLE_MSG_CAPTURE:
            return
        with self._lock:
            self._capture_sequence_index = 0
            self._capture_session_id = uuid4().hex

    def get_normalized_content_for_obf_message(
        self, *, message: DumpCSMessage, obf_messages_by_cls: Mapping[str, DumpCSMessage]
    ) -> tuple[NormalizedRuntimeInstance, ...]:
        """Return shallow payloads keyed by the exported filtered alias, not the dump.cs composed name."""
        runtime_key = build_filtered_message_namespace(
            is_obf=True,
            message=message,
            messages_by_cls=obf_messages_by_cls,
        )
        return self._normalized_content_by_name.get(runtime_key, ())

    @cached_property
    def schema_fingerprint(self) -> str:
        return get_obfuscated_proto_schema_fingerprint()

    def get_capture_sequences_by_session_for_obf_message(
        self, *, message: DumpCSMessage, obf_messages_by_cls: Mapping[str, DumpCSMessage]
    ) -> CaptureSequencesBySession:
        runtime_key = build_filtered_message_namespace(
            is_obf=True,
            message=message,
            messages_by_cls=obf_messages_by_cls,
        )
        return self.capture_sequences_by_session_by_name.get(runtime_key, {})

    def has_capture_for_obf_message(
        self, *, message: DumpCSMessage, obf_messages_by_cls: Mapping[str, DumpCSMessage]
    ) -> bool:
        runtime_key = build_filtered_message_namespace(
            is_obf=True,
            message=message,
            messages_by_cls=obf_messages_by_cls,
        )
        return runtime_key in self.content_by_name.root

    @cached_property
    def capture_sequences_by_session_by_name(self) -> dict[str, CaptureSequencesBySession]:
        by_name: dict[str, CaptureSequencesBySession] = {}
        for runtime_key, instances in self.content_by_name.root.items():
            sequences_by_session: dict[str, list[int]] = defaultdict(list)
            for runtime_instance in instances:
                if runtime_instance.capture_session_id is None or runtime_instance.capture_sequence is None:
                    continue
                sequences_by_session[runtime_instance.capture_session_id].append(
                    runtime_instance.capture_sequence
                )
            if sequences_by_session:
                by_name[runtime_key] = {
                    session_id: tuple(sorted(capture_sequences))
                    for session_id, capture_sequences in sequences_by_session.items()
                }
        return by_name

    def get_observed_root_obf_messages(self) -> dict[str, ObservedRootObfMessage]:
        observed: dict[str, ObservedRootObfMessage] = {}
        for obf_msg_namespace, instances in self.content_by_name.root.items():
            first_instance = next(iter(instances), None)
            if first_instance is None or not first_instance.is_root_msg or first_instance.is_game_msg:
                continue
            observed[obf_msg_namespace] = ObservedRootObfMessage(
                obf_msg_namespace=obf_msg_namespace,
                from_server=first_instance.from_server,
                instance_count=len(instances),
                observed_field_names=tuple(
                    sorted({name for instance in instances for name in (instance.model_extra or {})})
                ),
                capture_session_ids=tuple(
                    sorted(
                        {
                            instance.capture_session_id
                            for instance in instances
                            if instance.capture_session_id is not None
                        }
                    )
                ),
            )
        return observed

    @cached_property
    def _normalized_content_by_name(
        self,
    ) -> dict[str, tuple[NormalizedRuntimeInstance, ...]]:
        return {
            key: tuple(part.model_dump(exclude={"capture_sequence", "capture_session_id"}) for part in value)
            for key, value in self.content_by_name.root.items()
        }

    @cached_property
    def content_by_name(self) -> RuntimeRoot:
        path = self.path
        if not path.exists():
            return RuntimeRoot(root={})
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "schema_fingerprint" not in data:
            path.unlink(missing_ok=True)
            return RuntimeRoot(root={})
        capture_document = RuntimeCaptureDocument.model_validate(data)
        if capture_document.schema_fingerprint != self.schema_fingerprint:
            path.unlink(missing_ok=True)
            return RuntimeRoot(root={})
        return RuntimeRoot(root=capture_document.root)

    @cached_property
    def path(self) -> Path:
        return RUNTIME_DATA_FILE

    @cached_property
    def _writing_content(self) -> ContentByName:
        return {name: list(entries) for name, entries in self.content_by_name.root.items()}

    def add_msg(self, msg: Message, from_server: bool | None, is_game_msg: bool) -> None:
        if not ENABLE_MSG_CAPTURE:
            return
        with self._lock:
            if self._capture_target_path is None:
                self._capture_target_path = self.path
            capture_sequence = self._capture_sequence_index
            if capture_sequence is not None:
                self._capture_sequence_index = capture_sequence + 1
            self._update_msg_infos_content(
                msg,
                from_server,
                is_root_msg=True,
                is_game_msg=is_game_msg,
                capture_sequence=capture_sequence,
                capture_session_id=self._capture_session_id,
            )

    def _update_msg_infos_content(
        self,
        msg: Message,
        from_server: bool | None,
        is_game_msg: bool,
        is_root_msg: bool,
        capture_sequence: int | None,
        capture_session_id: str | None,
    ) -> RuntimeInstance:
        name = msg.DESCRIPTOR.full_name
        is_any_msg = name == "google.protobuf.Any"
        value_by_field = RuntimeInstance(
            from_server=from_server,
            is_game_msg=is_game_msg,
            is_root_msg=is_root_msg,
            capture_sequence=capture_sequence,
            capture_session_id=capture_session_id,
        )
        for _field in msg.DESCRIPTOR.fields:
            if is_any_msg and _field.name == "value":
                continue
            value = getattr(msg, _field.name)
            if is_repeated_field(_field):
                map_entry = _field.message_type
                is_map_field = map_entry is not None and map_entry.GetOptions().map_entry
                if len(value) == 0:
                    setattr(value_by_field, _field.name, {} if is_map_field else [])
                elif is_map_field:
                    assert map_entry is not None, f"Map field {_field.full_name} has no map-entry descriptor"
                    map_value_field = map_entry.fields_by_name["value"]
                    if map_value_field.type == FieldDescriptor.TYPE_MESSAGE:
                        message_values_by_key: dict[bool | int | str, RuntimeInstance] = {}
                        for map_key in value:
                            assert isinstance(map_key, bool | int | str), (
                                f"Map field {_field.full_name} has unsupported key type "
                                f"{type(map_key).__name__}"
                            )
                            map_value = value[map_key]
                            assert isinstance(map_value, Message), (
                                f"Map field {_field.full_name} declares message values but contains "
                                f"{type(map_value).__name__}"
                            )
                            message_values_by_key[map_key] = self._update_msg_infos_content(
                                map_value,
                                from_server=None,
                                is_game_msg=False,
                                is_root_msg=False,
                                capture_sequence=capture_sequence,
                                capture_session_id=capture_session_id,
                            )
                        setattr(value_by_field, _field.name, message_values_by_key)
                    else:
                        setattr(value_by_field, _field.name, dict(value))
                elif _field.type == FieldDescriptor.TYPE_MESSAGE:
                    sub_values: list[RuntimeInstance] = []
                    for sub_value in value:
                        _value = self._update_msg_infos_content(
                            sub_value,
                            from_server=None,
                            is_game_msg=False,
                            is_root_msg=False,
                            capture_sequence=capture_sequence,
                            capture_session_id=capture_session_id,
                        )
                        sub_values.append(_value)
                    setattr(value_by_field, _field.name, sub_values)
                else:
                    setattr(value_by_field, _field.name, list(value))
            elif _field.type == FieldDescriptor.TYPE_MESSAGE:
                if not msg.HasField(_field.name):
                    setattr(value_by_field, _field.name, None)
                else:
                    setattr(
                        value_by_field,
                        _field.name,
                        self._update_msg_infos_content(
                            value,
                            from_server=None,
                            is_game_msg=False,
                            is_root_msg=False,
                            capture_sequence=capture_sequence,
                            capture_session_id=capture_session_id,
                        ),
                    )
            else:
                setattr(value_by_field, _field.name, value)

        if name != "google.protobuf.Any":
            existing = self._writing_content.setdefault(name, [])
            if len(existing) < MAX_COUNT_BY_NAME:
                existing.append(value_by_field)
                self._has_unsaved_changes = True
        return value_by_field

    def write_captured_content(self) -> None:
        if not ENABLE_MSG_CAPTURE:
            return
        with self._lock:
            _ = self.content_by_name
            target_path = self._capture_target_path
            if target_path is None or not self._writing_content or not self._has_unsaved_changes:
                return
            if not target_path.parent.exists():
                return
            started_at = perf_counter()
            runtime_document = RuntimeCaptureDocument.model_construct(
                schema_fingerprint=self.schema_fingerprint,
                root={name: tuple(entries) for name, entries in self._writing_content.items()},
            )
            target_path.write_text(runtime_document.model_dump_json(), encoding="utf-8")
            self._has_unsaved_changes = False
            logger.info(
                "Wrote captured protocol data (%d bytes) in %.3f seconds",
                target_path.stat().st_size,
                perf_counter() - started_at,
            )

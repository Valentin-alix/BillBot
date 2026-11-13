import atexit
import gzip
import json
import queue
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TypedDict

from google.protobuf.message import Message

from src.protocol.message import MessageInfo
from src.protocol.protocol_connection import get_conn_msg_info
from src.protocol.protocol_game import get_game_msg_info

MessageSource = Literal["server", "client_forwarded", "framework_injected"]


class DebugLogEntry(TypedDict):
    category: Literal["log"]
    datetime: str
    level: str
    message: str


class DebugMessageEntry(TypedDict):
    category: Literal["message"]
    datetime: str
    origin: Literal["server", "client"]
    source: MessageSource
    unobfuscated_type: str
    obfuscated_type: str | None
    obfuscated_content: dict[str, Any] | None
    unobfuscated_content: dict[str, Any] | None


class DebugBehaviorEntry(TypedDict):
    category: Literal["behavior"]
    datetime: str
    behavior: str
    event: str
    from_state: str | None
    to_state: str | None
    error_code: str | None
    parent: str | None
    reason: str
    tree: list[str]


class DebugStateEntry(TypedDict):
    category: Literal["state"]
    datetime: str
    trigger: str
    snapshot: dict[str, Any]


class DebugStuckEntry(TypedDict):
    category: Literal["stuck"]
    datetime: str
    reason: str
    seconds_since_progress: float
    tree: list[str]
    snapshot: dict[str, Any]
    listeners: list[dict[str, Any]]
    last_message: str | None


DebugEntry = DebugLogEntry | DebugMessageEntry | DebugBehaviorEntry | DebugStateEntry | DebugStuckEntry


def _parse_sub_msg_name(sub_msg_name: str) -> tuple[str | None, str]:
    if " -> " in sub_msg_name:
        obf, decoded = sub_msg_name.split(" -> ", 1)
        return obf, decoded
    return None, sub_msg_name


@dataclass(slots=True)
class _LogRaw:
    logged_at: datetime
    level: str
    message: str


@dataclass(slots=True)
class _GameMsgRaw:
    received_at: datetime
    clear_sub_msg: Message | None
    obf_sub_msg: Message
    uid: int | None
    from_server: bool
    source: MessageSource


@dataclass(slots=True)
class _ConnMsgRaw:
    received_at: datetime
    sub_msg: Message
    from_server: bool
    source: MessageSource


@dataclass(slots=True)
class _BehaviorRaw:
    recorded_at: datetime
    behavior: str
    event: str
    from_state: str | None
    to_state: str | None
    error_code: str | None
    parent: str | None
    reason: str
    tree: list[str]


@dataclass(slots=True)
class _StateRaw:
    recorded_at: datetime
    trigger: str
    snapshot: dict[str, Any]


@dataclass(slots=True)
class _StuckRaw:
    recorded_at: datetime
    reason: str
    seconds_since_progress: float
    tree: list[str]
    snapshot: dict[str, Any]
    listeners: list[dict[str, Any]]
    last_message: str | None


_RawEntry = _LogRaw | _GameMsgRaw | _ConnMsgRaw | _BehaviorRaw | _StateRaw | _StuckRaw


_MAX_BUFFER = 100
_FLUSH_INTERVAL_S = 1.0
_BOT_LOG_RETENTION_DAYS = 7
_SESSION_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S_%f%z"
_HISTORY_MAINTENANCE_LOCK = threading.Lock()
_RECENT_GUI_MESSAGE_COUNT = 5000


@dataclass(frozen=True, slots=True)
class RecentMessageEntry:
    sequence: int
    received_time: datetime
    from_server: bool
    was_sent_from_proxy: bool
    sub_msg_name: str
    serialized: str

    def to_message_info(self) -> MessageInfo:
        body: dict[str, Any] = json.loads(self.serialized)
        msg_json: dict[str, Any] | None = body["unobfuscated_content"]
        obf_msg_json: dict[str, Any] | None = body["obfuscated_content"]
        assert msg_json is None or isinstance(msg_json, dict)
        assert obf_msg_json is None or isinstance(obf_msg_json, dict)
        return MessageInfo(
            received_time=self.received_time,
            from_server=self.from_server,
            sub_msg_name=self.sub_msg_name,
            msg_json=msg_json,
            obf_msg_json=obf_msg_json,
        )


@dataclass
class DebugRecorder:
    file_path: str
    _queue: queue.Queue[_RawEntry | None] = field(init=False, default_factory=queue.Queue[_RawEntry | None])
    _stop_event: threading.Event = field(init=False, default_factory=threading.Event)
    _recent_messages: deque[RecentMessageEntry] = field(
        init=False,
        default_factory=lambda: deque(maxlen=_RECENT_GUI_MESSAGE_COUNT),
    )
    _recent_lock: threading.Lock = field(init=False, default_factory=threading.Lock)
    _latest_message_sequence: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        Path(self.file_path).parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._drain,
            daemon=True,
            name=f"DebugRecorder-{Path(self.file_path).stem}",
        )
        self._thread.start()
        atexit.register(self.stop)

    def record_log(self, level: str, message: str, logged_at: datetime) -> None:
        self._queue.put(_LogRaw(logged_at, level, message))

    def record_game_message(
        self,
        clear_sub_msg: Message | None,
        obf_sub_msg: Message,
        uid: int | None,
        from_server: bool,
        source: MessageSource,
    ) -> None:
        self._queue.put(_GameMsgRaw(datetime.now(), clear_sub_msg, obf_sub_msg, uid, from_server, source))

    def record_conn_message(
        self,
        sub_msg: Message,
        from_server: bool,
        source: MessageSource,
    ) -> None:
        self._queue.put(_ConnMsgRaw(datetime.now(), sub_msg, from_server, source))

    @property
    def latest_message_sequence(self) -> int:
        with self._recent_lock:
            return self._latest_message_sequence

    def recent_messages_after(self, sequence: int) -> tuple[int, list[RecentMessageEntry]]:
        with self._recent_lock:
            return (
                self._latest_message_sequence,
                [entry for entry in self._recent_messages if entry.sequence > sequence],
            )

    def record_behavior(
        self,
        behavior: str,
        event: str,
        tree: list[str],
        from_state: str | None = None,
        to_state: str | None = None,
        error_code: str | None = None,
        parent: str | None = None,
        reason: str = "",
    ) -> None:
        self._queue.put(
            _BehaviorRaw(
                datetime.now(),
                behavior,
                event,
                from_state,
                to_state,
                error_code,
                parent,
                reason,
                tree,
            )
        )

    def record_state(self, trigger: str, snapshot: dict[str, Any]) -> None:
        self._queue.put(_StateRaw(datetime.now(), trigger, snapshot))

    def record_stuck(
        self,
        reason: str,
        seconds_since_progress: float,
        tree: list[str],
        snapshot: dict[str, Any],
        listeners: list[dict[str, Any]],
        last_message: str | None,
    ) -> None:
        self._queue.put(
            _StuckRaw(
                datetime.now(),
                reason,
                seconds_since_progress,
                tree,
                snapshot,
                listeners,
                last_message,
            )
        )

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        self._queue.put(None)
        self._thread.join(timeout=5)

    def _drain(self) -> None:
        with open(self.file_path, "w", encoding="utf-8") as file:
            buffer: list[str] = []
            last_flush = time.monotonic()
            while True:
                try:
                    item = self._queue.get(timeout=_FLUSH_INTERVAL_S)
                except queue.Empty:
                    item = None
                    if self._stop_event.is_set():
                        self._flush(file, buffer)
                        return

                if item is not None:
                    entry = _build_entry(item)
                    serialized = json.dumps(entry, ensure_ascii=False)
                    buffer.append(serialized)
                    if entry["category"] == "message":
                        self._record_recent_message(entry, serialized)

                now = time.monotonic()
                if buffer and (len(buffer) >= _MAX_BUFFER or now - last_flush >= _FLUSH_INTERVAL_S):
                    self._flush(file, buffer)
                    last_flush = now

                if self._stop_event.is_set() and self._queue.empty():
                    self._flush(file, buffer)
                    return

    def _record_recent_message(self, entry: DebugMessageEntry, serialized: str) -> None:
        obf_name = entry["obfuscated_type"]
        non_obf_name = entry["unobfuscated_type"]
        sub_msg_name = f"{obf_name} -> {non_obf_name}" if obf_name else non_obf_name
        with self._recent_lock:
            self._latest_message_sequence += 1
            self._recent_messages.append(
                RecentMessageEntry(
                    sequence=self._latest_message_sequence,
                    received_time=datetime.fromisoformat(entry["datetime"]),
                    from_server=entry["origin"] == "server",
                    was_sent_from_proxy=entry["source"] == "framework_injected",
                    sub_msg_name=sub_msg_name,
                    serialized=serialized,
                )
            )

    @staticmethod
    def _flush(file: Any, buffer: list[str]) -> None:
        if not buffer:
            return
        file.write("\n".join(buffer) + "\n")
        file.flush()
        buffer.clear()


def create_bot_session_debug_recorder(
    logs_directory: Path,
    login: str,
    *,
    session_started_at: datetime | None = None,
) -> DebugRecorder:
    account_logs_directory = logs_directory / login
    account_logs_directory.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_bot_debug_log(
        logs_directory,
        account_logs_directory,
        login,
    )

    started_at = session_started_at or datetime.now().astimezone()
    assert started_at.tzinfo is not None, "Bot debug session timestamp must be timezone-aware"
    session_timestamp = started_at.strftime(_SESSION_TIMESTAMP_FORMAT)
    session_path = account_logs_directory / f"{session_timestamp}.debug.jsonl"
    assert not session_path.exists(), f"Bot debug session path already exists: {session_path}"
    session_path.touch(exist_ok=False)

    recorder = DebugRecorder(file_path=str(session_path))
    threading.Thread(
        target=_maintain_bot_debug_history,
        args=(account_logs_directory, session_path),
        daemon=True,
        name=f"DebugHistory-{login}",
    ).start()
    return recorder


def _migrate_legacy_bot_debug_log(
    logs_directory: Path,
    account_logs_directory: Path,
    login: str,
) -> None:
    legacy_path = logs_directory / f"{login}.debug.jsonl"
    if not legacy_path.exists():
        return
    legacy_timestamp = (
        datetime.fromtimestamp(legacy_path.stat().st_mtime).astimezone().strftime(_SESSION_TIMESTAMP_FORMAT)
    )
    migrated_path = account_logs_directory / f"legacy-{legacy_timestamp}.debug.jsonl"
    assert not migrated_path.exists(), f"Migrated debug log already exists: {migrated_path}"
    legacy_path.replace(migrated_path)


def _maintain_bot_debug_history(
    account_logs_directory: Path,
    active_session_path: Path,
) -> None:
    with _HISTORY_MAINTENANCE_LOCK:
        for session_path in account_logs_directory.glob("*.debug.jsonl"):
            if session_path != active_session_path:
                _compress_bot_debug_session(session_path)
        _prune_bot_debug_sessions(account_logs_directory, active_session_path)


def _compress_bot_debug_session(session_path: Path) -> None:
    compressed_path = Path(f"{session_path}.gz")
    temporary_path = Path(f"{compressed_path}.tmp")
    if temporary_path.exists():
        temporary_path.unlink()
    with session_path.open("rb") as source, gzip.open(temporary_path, "wb") as destination:
        shutil.copyfileobj(source, destination)
    temporary_path.replace(compressed_path)
    session_path.unlink()


def _prune_bot_debug_sessions(
    account_logs_directory: Path,
    active_session_path: Path,
) -> None:
    expiration_timestamp = (datetime.now().astimezone() - timedelta(days=_BOT_LOG_RETENTION_DAYS)).timestamp()
    session_paths = _bot_debug_session_paths(account_logs_directory)
    for session_path in session_paths:
        if session_path != active_session_path and session_path.stat().st_mtime < expiration_timestamp:
            session_path.unlink()


def _bot_debug_session_paths(account_logs_directory: Path) -> list[Path]:
    return [
        session_path
        for session_path in account_logs_directory.iterdir()
        if session_path.name.endswith((".debug.jsonl", ".debug.jsonl.gz"))
    ]


def _build_entry(raw: _RawEntry) -> DebugEntry:
    if isinstance(raw, _LogRaw):
        return DebugLogEntry(
            category="log",
            datetime=raw.logged_at.isoformat(),
            level=raw.level,
            message=raw.message,
        )
    if isinstance(raw, _BehaviorRaw):
        return DebugBehaviorEntry(
            category="behavior",
            datetime=raw.recorded_at.isoformat(),
            behavior=raw.behavior,
            event=raw.event,
            from_state=raw.from_state,
            to_state=raw.to_state,
            error_code=raw.error_code,
            parent=raw.parent,
            reason=raw.reason,
            tree=raw.tree,
        )
    if isinstance(raw, _StateRaw):
        return DebugStateEntry(
            category="state",
            datetime=raw.recorded_at.isoformat(),
            trigger=raw.trigger,
            snapshot=raw.snapshot,
        )
    if isinstance(raw, _StuckRaw):
        return DebugStuckEntry(
            category="stuck",
            datetime=raw.recorded_at.isoformat(),
            reason=raw.reason,
            seconds_since_progress=raw.seconds_since_progress,
            tree=raw.tree,
            snapshot=raw.snapshot,
            listeners=raw.listeners,
            last_message=raw.last_message,
        )
    if isinstance(raw, _GameMsgRaw):
        msg_info = get_game_msg_info(raw.clear_sub_msg, raw.obf_sub_msg, raw.uid, raw.from_server)
        obf_type, decoded_type = _parse_sub_msg_name(msg_info.sub_msg_name)
        return DebugMessageEntry(
            category="message",
            datetime=msg_info.received_time.isoformat(),
            origin="server" if msg_info.from_server else "client",
            source=raw.source,
            unobfuscated_type=decoded_type,
            obfuscated_type=obf_type,
            obfuscated_content=msg_info.obf_msg_json,
            unobfuscated_content=msg_info.msg_json,
        )

    assert isinstance(raw, _ConnMsgRaw)
    msg_info = get_conn_msg_info(raw.sub_msg, raw.from_server)
    obf_type, decoded_type = _parse_sub_msg_name(msg_info.sub_msg_name)
    return DebugMessageEntry(
        category="message",
        datetime=msg_info.received_time.isoformat(),
        origin="server" if msg_info.from_server else "client",
        source=raw.source,
        unobfuscated_type=decoded_type,
        obfuscated_type=obf_type,
        obfuscated_content=msg_info.obf_msg_json,
        unobfuscated_content=msg_info.msg_json,
    )

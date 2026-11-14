import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, cast


@dataclass(frozen=True)
class ProgressEvent:
    phase: str
    current: int
    total: int
    label: str
    done: bool = False


class ProgressReporter:
    def __init__(self, output_path: Path | None) -> None:
        self._output_path = output_path

    @classmethod
    def from_environment(cls) -> "ProgressReporter":
        configured_path = os.environ.get("PROTO_TRACER_PROGRESS_PATH")
        if configured_path is None:
            return cls(None)
        return cls(Path(configured_path))

    def start(self, *, phase: str, total: int, label: str) -> None:
        self._emit(ProgressEvent(phase=phase, current=0, total=total, label=label))

    def update(self, *, phase: str, current: int, total: int, label: str) -> None:
        self._emit(ProgressEvent(phase=phase, current=current, total=total, label=label))

    def finish(self, *, phase: str, total: int, label: str) -> None:
        self._emit(ProgressEvent(phase=phase, current=total, total=total, label=label, done=True))

    def _emit(self, event: ProgressEvent) -> None:
        if self._output_path is None:
            return
        with self._output_path.open("a", encoding="utf-8") as progress_file:
            progress_file.write(json.dumps(event.__dict__) + "\n")
            progress_file.flush()


_PROGRESS_POLL_SECONDS = 0.1
_PROGRESS_BAR_WIDTH = 24


def start_progress_watcher(progress_path: Path) -> tuple[threading.Event, threading.Thread]:
    stop_event = threading.Event()
    thread = threading.Thread(target=_watch_progress_file, args=(progress_path, stop_event), daemon=True)
    thread.start()
    return stop_event, thread


def _watch_progress_file(
    progress_path: Path, stop_event: threading.Event, stream: TextIO = sys.stderr
) -> None:
    position = 0
    while not stop_event.is_set() or position < progress_path.stat().st_size:
        with progress_path.open(encoding="utf-8") as progress_file:
            progress_file.seek(position)
            line = progress_file.readline()
            while line:
                position = progress_file.tell()
                event = _parse_progress_event(line)
                if event is not None:
                    _render_progress_event(event, stream)
                line = progress_file.readline()
        if not stop_event.is_set():
            time.sleep(_PROGRESS_POLL_SECONDS)


def _render_progress_event(event: ProgressEvent, stream: TextIO = sys.stderr) -> None:
    total = max(event.total, 1)
    current = min(max(event.current, 0), total)
    ratio = current / total
    filled_width = int(_PROGRESS_BAR_WIDTH * ratio)
    bar = "#" * filled_width + "-" * (_PROGRESS_BAR_WIDTH - filled_width)
    progress_text = f"{event.label} [{bar}] {ratio * 100:5.1f}% {current}/{event.total}"
    if stream.isatty():
        stream.write(f"\r{progress_text}")
        if event.done:
            stream.write("\n")
    else:
        stream.write(f"{progress_text}\n")
    stream.flush()


def _parse_progress_event(line: str) -> ProgressEvent | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    progress_payload = cast("dict[str, object]", payload)
    phase = progress_payload.get("phase")
    current = progress_payload.get("current")
    total = progress_payload.get("total")
    label = progress_payload.get("label")
    done = progress_payload.get("done", False)
    if not isinstance(phase, str):
        return None
    if not isinstance(current, int) or not isinstance(total, int):
        return None
    if not isinstance(label, str) or not isinstance(done, bool):
        return None
    return ProgressEvent(phase=phase, current=current, total=total, label=label, done=done)

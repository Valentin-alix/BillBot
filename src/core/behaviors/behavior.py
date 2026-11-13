from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from threading import RLock, Timer
from typing import ParamSpec, Protocol, cast

from DBDofusUnity.datas.protos.non_obf.game.dialog_pb2 import DialogLeaveEvent, DialogLeaveRequest
from DBDofusUnity.datas.protos.non_obf.game.exchange_pb2 import ExchangeLeaveEvent
from google.protobuf.message import Message

from src.core.events_manager.event_manager import EventManager
from src.core.states.dialog_state import OpenDialogKind
from src.core.states.game_state import GameState
from src.exceptions import UnhandledErrorCodeException
from src.services.human_timings import get_random_range
from src.services.logging_utils.contextual_logger import ContextualLogger


class BehaviorLifecycleError(Exception):
    pass


class BehaviorStateError(Exception):
    pass


class BehaviorState(Enum):
    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()


RunParams = ParamSpec("RunParams")

DIALOG_LEAVE_TIMEOUT_SECONDS = 5.0
DIALOG_LEAVE_KINDS = {OpenDialogKind.NPC_DIALOG, OpenDialogKind.ZAAP_DESTINATIONS}


class RunnableBehavior(Protocol[RunParams]):
    def run(self, *args: RunParams.args, **kwargs: RunParams.kwargs) -> None: ...


@dataclass
class Behavior(ContextualLogger):
    event_manager: EventManager
    game_state: GameState
    callback: Callable[..., None] | None = field(init=False, default=None)
    parent: "Behavior|None" = field(init=False, default=None)

    _state: BehaviorState = field(init=False, default=BehaviorState.STOPPED)
    _state_lock: RLock = field(init=False, default_factory=RLock)
    children: list["Behavior"] = field(init=False, default_factory=list["Behavior"])
    timers: list[Timer] = field(init=False, default_factory=list[Timer])
    _run_args: tuple[object, ...] = field(init=False, default_factory=tuple[object, ...])
    _run_kwargs: dict[str, object] = field(init=False, default_factory=dict[str, object])

    def _transition(self, from_states: set[BehaviorState], to_state: BehaviorState, reason: str = "") -> None:
        with self._state_lock:
            if self._state not in from_states:
                error = (
                    f"{self.__class__.__name__} invalid transition: "
                    f"{self._state.name} -> {to_state.name}. "
                    f"Expected current state in {[s.name for s in from_states]}. "
                    f"Reason: {reason}"
                )
                self.logger.error(error)
                raise BehaviorStateError(error)

            old_state = self._state
            self._state = to_state
            self.logger.debug(
                f"State transition: {old_state.name} -> {to_state.name}" + (f" ({reason})" if reason else "")
            )
            self._record_behavior_event(
                "transition",
                from_state=old_state.name,
                to_state=to_state.name,
                reason=reason,
            )

    @property
    def state(self) -> BehaviorState:
        with self._state_lock:
            return self._state

    def start(
        self: RunnableBehavior[RunParams],
        callback: Callable[..., None] | None,
        parent: "Behavior|None",
        *args: RunParams.args,
        **kwargs: RunParams.kwargs,
    ) -> None:
        behavior = cast(Behavior, self)
        behavior._transition(
            {BehaviorState.STOPPED},
            BehaviorState.STARTING,
            reason=f"start() called with parent={parent.__class__.__name__ if parent else None}",
        )

        with behavior.event_manager.lock:
            if parent and parent.state != BehaviorState.RUNNING:
                error = (
                    f"Cannot start {behavior.__class__.__name__}: "
                    f"parent {parent.__class__.__name__} in state {parent.state.name}, "
                    f"expected RUNNING"
                )
                behavior.logger.error(error)
                behavior.stop()
                raise BehaviorLifecycleError(error)

            behavior.parent = parent
            if behavior.parent:
                behavior.parent.children.append(behavior)
            behavior.callback = callback
            behavior._run_args = args
            behavior._run_kwargs = cast(dict[str, object], kwargs)

            behavior._transition(
                {BehaviorState.STARTING},
                BehaviorState.RUNNING,
                reason="setup complete, entering run()",
            )

        behavior.logger.debug(f"Starting {behavior.__class__.__name__}")
        if behavior.parent is None:
            behavior._record_state_snapshot(f"{behavior.__class__.__name__}.start")
        try:
            self.run(*args, **kwargs)
        except Exception:
            behavior.stop()
            raise

    def send_message_delayed(self, message: Message, delay: tuple[float, float] | float) -> None:
        self.run_timer(delay, lambda: self.event_manager.send(message))

    def run_timer(self, range_time: tuple[float, float] | float, func: Callable[[], None]) -> None:
        if isinstance(range_time, tuple):
            wait_time = get_random_range(range_time)
        else:
            wait_time = range_time
        timer = Timer(wait_time, lambda: self.run_timed_func(func))
        timer.daemon = True
        self.timers.append(timer)
        timer.start()

    def run_timed_func(self, func: Callable[[], None]) -> None:
        with self._state_lock:
            if self._state != BehaviorState.RUNNING:
                self.logger.warning(f"Timer fired but behavior in state {self._state.name}, ignoring")
                return
        func()

    def stop(self) -> None:
        with self._state_lock:
            if self._state == BehaviorState.STOPPED:
                error = "Already stopped, ignoring"
                self.logger.debug(error)
                return

            if self._state == BehaviorState.STOPPING:
                error = "Already stopping, ignoring"
                self.logger.debug(error)
                return

            self._transition(
                {BehaviorState.RUNNING, BehaviorState.STARTING},
                BehaviorState.STOPPING,
                reason="stop() called",
            )

        with self.event_manager.lock:
            self.logger.info("Stopping")
            self.clear_behavior()
            if self.parent and self in self.parent.children:
                self.parent.children.remove(self)

        self._transition({BehaviorState.STOPPING}, BehaviorState.STOPPED, reason="cleanup complete")

    def clear_behavior(self) -> None:
        with self.event_manager.lock:
            for timer in self.timers:
                timer.cancel()
            self.timers.clear()

            self.event_manager.clear_listener_by_origin(self)
            self.event_manager.clear_modifier_by_origin(self)

            while self.children:
                child = self.children.pop()
                child.stop()

    def finish(self, error_code: str | None = None, *args: object, **kwargs: object) -> None:
        with self._state_lock:
            if self._state not in {BehaviorState.RUNNING, BehaviorState.STARTING}:
                error = f"finish() called in state {self._state.name}"
                self.logger.error(error)
                return

            callback = self.callback
            self.callback = None

        if error_code is not None:
            self.logger.warning(f"Finished with error: {error_code}")

        self._record_behavior_event("finish", error_code=error_code)
        if self.parent is None:
            self._record_state_snapshot(f"{self.__class__.__name__}.finish")

        self.stop()

        if callback:
            callback(error_code, *args, **kwargs)

    def ensure_dialog_closed(
        self,
        then: Callable[[], None],
        keep: OpenDialogKind | None = None,
    ) -> None:
        kind = self.game_state.dialog.kind
        if kind is None or kind is keep:
            return then()

        self.logger.info(f"Closing {kind} before going on")

        def on_timeout() -> None:
            self.logger.warning(f"No leave confirmation for {kind}, assuming it is closed")
            self.game_state.dialog.clear_state()
            then()

        self.event_manager.on(
            DialogLeaveEvent if kind in DIALOG_LEAVE_KINDS else ExchangeLeaveEvent,
            lambda _: then(),
            originator=self,
            once=True,
            override_on_self=True,
            timeout=DIALOG_LEAVE_TIMEOUT_SECONDS,
            on_timeout=on_timeout,
        )
        self.event_manager.send(DialogLeaveRequest())

    def leave_dialog(self, on_leave_callback: Callable[[ExchangeLeaveEvent], None] | None = None) -> None:
        if not self.game_state.dialog.is_any_open:
            self.logger.info("Nothing is open, skipping the leave request")
            if on_leave_callback:
                on_leave_callback(ExchangeLeaveEvent())
            return

        if on_leave_callback:
            self.event_manager.on(
                ExchangeLeaveEvent,
                callback=on_leave_callback,
                originator=self,
                once=True,
                override_on_self=True,
                timeout=DIALOG_LEAVE_TIMEOUT_SECONDS,
                on_timeout=lambda: on_leave_callback(ExchangeLeaveEvent()),
            )
        self.event_manager.send(DialogLeaveRequest())

    def unregister_listener(self, event_type: type[Message], reason: str = "") -> None:
        """Remove a listener during execution; behavior completion already clears all listeners."""
        if reason:
            self.logger.debug(f"Manual listener cleanup: {event_type.__name__} - {reason}")
        self.event_manager.clear_listener_by_origin_and_type(event_type, self)

    def replay_run(self) -> None:
        runnable = cast(RunnableBehavior[...], self)
        runnable.run(*self._run_args, **self._run_kwargs)

    def raise_if_error(self, error_code: str | None) -> None:
        if error_code is not None:
            raise UnhandledErrorCodeException(error_code)

    def behavior_tree_snapshot(self) -> list[str]:
        root = self
        while root.parent is not None:
            root = root.parent

        lines: list[str] = []

        def walk(behavior: "Behavior", depth: int) -> None:
            marker = " <-" if behavior is self else ""
            lines.append("  " * depth + f"{behavior.__class__.__name__}[{behavior.state.name}]" + marker)
            for child in list(behavior.children):
                walk(child, depth + 1)

        walk(root, 0)
        return lines

    def _record_behavior_event(
        self,
        event: str,
        from_state: str | None = None,
        to_state: str | None = None,
        error_code: str | None = None,
        reason: str = "",
    ) -> None:
        self.event_manager.mark_activity()
        recorder = self.event_manager.debug_recorder
        if recorder is None:
            return
        recorder.record_behavior(
            behavior=self.__class__.__name__,
            event=event,
            tree=self.behavior_tree_snapshot(),
            from_state=from_state,
            to_state=to_state,
            error_code=error_code,
            parent=self.parent.__class__.__name__ if self.parent else None,
            reason=reason,
        )

    def _record_state_snapshot(self, trigger: str) -> None:
        recorder = self.event_manager.debug_recorder
        if recorder is None:
            return
        recorder.record_state(trigger=trigger, snapshot=self.game_state.debug_snapshot())

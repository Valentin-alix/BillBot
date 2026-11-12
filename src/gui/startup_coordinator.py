from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from time import perf_counter
from typing import cast

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot

from src.consts import MIN_DATE
from src.services.background import has_pending_tasks
from src.services.startup import PreparedAccounts, prepare_startup
from src.utils.runtime_support import RuntimeSetupError, error_message
lazy from src.core.bot.bot_manager import BotManager
lazy from src.core.bot.lifecycle.scheduler import run_continuously
lazy from src.gui.main_window import MainWindow
lazy from src.protocol.protocol_game import install_capture_hooks
lazy from src.services.user_activity import UserActivityService

logger = logging.getLogger()
type StartupValidation = Callable[[Callable[[str], None]], None]


class StartupTask[ResultT](QThread):
    progress = pyqtSignal(str)

    def __init__(
        self,
        action: Callable[[Callable[[str], None]], ResultT],
        done: Callable[[ResultT], None],
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self.action = action
        self.result: ResultT
        self.done = done
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            self.result = self.action(self.progress.emit)
        except (RuntimeSetupError, OSError) as error:
            self.error = error
            logger.error("Startup task failed: %s", error_message(error), exc_info=True)


class StartupCoordinator(QObject):
    closed = pyqtSignal()
    update_started = pyqtSignal()

    def __init__(
        self,
        window: MainWindow,
        validate: StartupValidation,
        automatic: bool,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.validate = validate
        self.automatic = automatic
        self.manager: BotManager | None = None
        self.failed = False
        self._cancelled = threading.Event()
        self._cease_running = threading.Event()
        self._task: QThread | None = None
        self._prepared: PreparedAccounts | None = None
        self._account_index = 0
        self._closing = False
        self._shutdown_started = False
        self._accounts_timer = QTimer(self)
        self._accounts_timer.setSingleShot(True)
        self._accounts_timer.timeout.connect(self._add_accounts)
        window.retry_requested.connect(self.retry)

    def start(self) -> None:
        if self._closing:
            return
        self.failed = False
        self.window.set_startup_status("Checking installation…")
        self._work(
            lambda progress: prepare_startup(self.validate, progress, self._cancelled),
            self._accounts_prepared,
        )

    def _work[ResultT](
        self, action: Callable[[Callable[[str], None]], ResultT], done: Callable[[ResultT], None]
    ) -> None:
        task = StartupTask(action, done, self)
        self._task = task
        task.progress.connect(self.window.set_startup_status)
        task.finished.connect(self._task_finished)
        task.start()

    @pyqtSlot()
    def _task_finished(self) -> None:
        task = cast(StartupTask[object], self._task)
        self._task = None
        task.deleteLater()
        if self._closing:
            if self._shutdown_started:
                self.manager = None
                self._maybe_closed()
            else:
                self._start_shutdown()
            return
        if task.error is not None:
            self._fail(task.error)
            return
        task.done(task.result)

    def _accounts_prepared(self, result: PreparedAccounts | None) -> None:
        result = cast(PreparedAccounts, result)
        if result.update_started:
            self.update_started.emit()
            return
        install_capture_hooks()
        self._prepared = result
        self._account_index = 0
        manager_type = cast(type[BotManager], result.manager_type)
        self.manager = manager_type(self.window.shared_signals, self.automatic)
        self.window.bind_runtime(self.manager)
        self._accounts_timer.start(16)

    def _add_accounts(self) -> None:
        manager = cast(BotManager, self.manager)
        prepared = cast(PreparedAccounts, self._prepared)
        deadline = perf_counter() + 0.008
        while self._account_index < len(prepared.accounts) and perf_counter() < deadline:
            account = prepared.accounts[self._account_index]
            bot = manager.add_prepared_account(account)
            login = account.apikey.login
            record = prepared.records[login]
            self.window.add_account(
                bot,
                prepared.snapshots.get(login),
                record.quarantine_reason,
                prepared.configs[login],
                prepared.subscription_ends[login] or MIN_DATE,
            )
            self._account_index += 1
        self.window.set_startup_status(f"Preparing accounts {self._account_index}/{len(prepared.accounts)}…")
        if self._account_index == len(prepared.accounts):
            self._prepared = None
            self.window.set_startup_status("Starting launcher…")
            self._work(self._start_runtime, self._ready)
        else:
            self._accounts_timer.start(16)

    def _start_runtime(self, progress: Callable[[str], None]) -> None:
        manager = cast(BotManager, self.manager)
        manager.ankama_launcher.start()
        if self._cancelled.is_set():
            return
        progress("Starting schedules...")
        self._cease_running = run_continuously()
        manager.start_account_scheduler()
        if self.automatic:
            for bot in manager.bot_by_account_id.values():
                if self._cancelled.is_set():
                    return
                bot.start()

    def _ready(self, result: object) -> None:
        self.window.set_runtime_ready()
        logger.info("Application ready.")

    def _fail(self, error: Exception) -> None:
        self.failed = True
        self._accounts_timer.stop()
        self._prepared = None
        self.window.show_startup_error(error_message(error))

    def retry(self) -> None:
        if self._task is not None or self._closing:
            return
        self.window.clear_accounts()
        self._cease_running.set()
        manager = self.manager
        if manager is None:
            self.start()
        else:
            self.window.unbind_runtime(manager)
            self.manager = None
            self._work(lambda _: manager.shutdown(close_activity=False), lambda _: self.start())

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self.manager is not None:
            self.manager.disconnect_signals()
        self._cancelled.set()
        self._accounts_timer.stop()
        self._prepared = None
        if self._task is None:
            self._start_shutdown()

    def _start_shutdown(self) -> None:
        self._shutdown_started = True
        self._cease_running.set()
        manager = self.manager

        def shutdown(_: Callable[[str], None]) -> None:
            if manager is None:
                UserActivityService().close()
            else:
                manager.shutdown()

        self._work(shutdown, lambda _: None)

    def _maybe_closed(self) -> None:
        if has_pending_tasks():
            QTimer.singleShot(25, self._maybe_closed)
        else:
            self.closed.emit()

import logging
from collections.abc import Callable
from typing import TypeVar

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

from src.services.user_activity import UserActivityService
from src.utils.runtime_support import RuntimeSetupError, error_message

logger = logging.getLogger()

_running: set[object] = set()
BackgroundResultT = TypeVar("BackgroundResultT")


def has_pending_tasks() -> bool:
    return bool(_running)


def _report_task_error(error: object) -> None:
    UserActivityService().record("error", error_message(error))


class Worker(QObject):
    progress = pyqtSignal(str)
    success = pyqtSignal(object)
    error = pyqtSignal(object)
    finished = pyqtSignal()

    def __init__(self, func: Callable[[Callable[[str], None]], BackgroundResultT]) -> None:
        super().__init__()
        self.func = func

    @pyqtSlot()
    def run(self) -> None:
        try:
            self.success.emit(self.func(self.progress.emit))
        except (RuntimeSetupError, OSError) as error:
            logger.error("Task interrupted: %s", error_message(error), exc_info=True)
            self.error.emit(error)
        finally:
            self.finished.emit()


def run_in_background(
    func: Callable[[Callable[[str], None]], BackgroundResultT],
    on_success: Callable[[BackgroundResultT], None] | None = None,
    on_error: Callable[[object], None] | None = None,
    on_progress: Callable[[str], None] | None = None,
    parent: QObject | None = None,
) -> None:
    worker = Worker(func)
    thread = QThread(parent)

    _running.add(worker)
    _running.add(thread)

    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    if on_success is not None:
        worker.success.connect(on_success)
    if on_error is not None:
        worker.error.connect(on_error)
    else:
        worker.error.connect(_report_task_error)
    if on_progress is not None:
        worker.progress.connect(on_progress)

    worker.finished.connect(lambda: on_worker_finished(worker, thread))
    thread.finished.connect(lambda: on_q_thread_finished(thread))

    thread.start()


def on_worker_finished(worker: Worker, thread: QThread):
    thread.quit()
    worker.deleteLater()
    _running.discard(worker)


def on_q_thread_finished(q_thread: QThread):
    q_thread.deleteLater()
    _running.discard(q_thread)

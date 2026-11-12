from collections.abc import Callable

from PyQt6.QtCore import QTimer

from src.core.signals.shared_farm_signals import SharedSignals
from src.gui.application import Application
lazy from src.gui.main_window import MainWindow
lazy from src.gui.startup_coordinator import StartupCoordinator


def run_gui(
    application_argv: list[str],
    enable_automatic_schedules: bool,
    validate: Callable[[Callable[[str], None]], None],
) -> int:
    application = Application(application_argv)
    shared_signals = SharedSignals()
    window = MainWindow(application.TITLE, shared_signals)
    coordinator = StartupCoordinator(window, validate, enable_automatic_schedules)
    shared_signals.closed.connect(coordinator.close)
    coordinator.closed.connect(window.complete_shutdown)
    coordinator.update_started.connect(window.close)
    window.show()
    QTimer.singleShot(0, coordinator.start)
    result = application.exec()
    return 1 if coordinator.failed else result

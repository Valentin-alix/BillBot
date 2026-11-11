import logging
import threading
from collections.abc import Callable
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QMessageBox
from qfluentwidgets import Theme, setTheme, setThemeColor

from src.gui.application import Application
lazy from src.gui.main_window import MainWindow
lazy from src.core.bot.bot_manager import BotManager
from src.core.bot.lifecycle.scheduler import run_continuously
from src.core.signals.shared_farm_signals import SharedSignals
from src.services.user_activity import UserActivityService
from src.utils.runtime_support import RuntimeSetupError, error_message

logger = logging.getLogger(__name__)


def _start_bots(bot_manager: BotManager, enable_automatic_schedules: bool) -> None:
    if not enable_automatic_schedules:
        return
    for bot in bot_manager.bot_by_account_id.values():
        bot.start()


def _shutdown_runtime(
    bot_manager: BotManager,
    cease_running: threading.Event,
    on_finished: Callable[[], None],
) -> None:
    cease_running.set()

    def shutdown() -> None:
        try:
            bot_manager.shutdown()
        finally:
            on_finished()

    threading.Thread(target=shutdown, name="bot-manager-shutdown").start()


def run_gui(
    application_argv: list[str],
    enable_automatic_schedules: bool,
    application: Application | None = None,
) -> int:
    application = application or Application(application_argv)
    shared_signals = SharedSignals()
    main_window = MainWindow(title=application.TITLE, shared_signals=shared_signals)
    main_window.show()
    application.startup_window.close()
    setTheme(Theme.DARK)
    setThemeColor(Qt.GlobalColor.yellow)
    main_window.set_startup_status("Loading bots…")

    bot_manager: BotManager | None = None
    cease_running = threading.Event()
    startup_failed = False

    def on_app_close() -> None:
        if bot_manager is None:
            UserActivityService().close()
            QTimer.singleShot(0, shared_signals.shutdown_finished.emit)
        else:
            _shutdown_runtime(bot_manager, cease_running, shared_signals.shutdown_finished.emit)

    shared_signals.closed.connect(on_app_close)

    def start_runtime() -> None:
        nonlocal bot_manager, cease_running, startup_failed
        try:
            bot_manager = BotManager(
                shared_signals=shared_signals,
                enable_account_scheduler=enable_automatic_schedules,
            )
            main_window.activity_page.restore_account_requested.connect(
                bot_manager.restore_account_from_quarantine
            )
            main_window.activity_page.delete_account_requested.connect(bot_manager.delete_account)
            main_window.activity_page.restore_mailbox_requested.connect(
                bot_manager.restore_mailbox_from_quarantine
            )
            main_window.activity_page.delete_mailbox_requested.connect(bot_manager.delete_mailbox)
            main_window.settings_page.assignments.bind_deletion(
                main_window.settings_page.assignments.account,
                bot_manager.delete_account,
                main_window.activity_page.refresh,
                background=False,
            )
            main_window.settings_page.mail.bind_deletion(
                main_window.settings_page.mail.selection,
                bot_manager.delete_mailbox,
                main_window.activity_page.refresh,
                background=False,
            )
            main_window.set_startup_status("Starting launcher…")
            bot_manager.ankama_launcher.start()
            main_window.init_accounts(bot_manager.bot_by_account_id)
            cease_running = run_continuously()
            bot_manager.start_account_scheduler()
            main_window.setWindowTitle(application.TITLE)
            main_window.splashScreen.finish()
            _start_bots(bot_manager, enable_automatic_schedules)
            logger.info("Application ready.")
        except (RuntimeSetupError, OSError) as error:
            startup_failed = True
            message = error_message(error)
            logger.error("Unable to start: %s", message, exc_info=True)
            UserActivityService().record("error", message)
            main_window.splashScreen.finish()
            QMessageBox.critical(main_window, "Unable to start", message)
            main_window.close()

    QTimer.singleShot(0, start_runtime)
    result = application.exec()
    return 1 if startup_failed else result

from __future__ import annotations

lazy from src.core.bot.bot import Bot

import logging
from collections.abc import Callable
from functools import partial
from typing import Literal, cast

from ankama_launcher_emulator.controller.bot_storage import (
    BotStorageController,
)
from PyQt6.QtCore import QSize, QTimer, QUrl
from PyQt6.QtGui import QCloseEvent, QColor, QIcon, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from qfluentwidgets import (
    FluentIcon,
    NavigationItemPosition,
)

from src.consts import LOGO_FILE
from src.controller.bot_config import BotConfigService
from src.controller.player_info_storage import PlayerInfoSnapshot, PlayerInfoStorage
from src.core import config
from src.core.signals.log_signals import LogSignals
from src.core.signals.shared_farm_signals import SharedSignals
from src.gui.components.manual_confirmation import ManualConfirmationDialogs
from src.gui.consts import BASE_HEIGHT, BASE_WIDTH
from src.gui.startup_splash_screen import StartupSplashScreen
from src.gui.fragments.account_quick_info import AccountQuickInfoWidget
from src.gui.fragments.account_stacked_widget import AccountStackedWidget
from src.gui.fragments.app_fluent_window import AppFluentWindow
from src.gui.fragments.sidebar_item import SidebarItem
from src.gui.pages.activity import ActivityPage
from src.gui.pages.settings.settings_page import SettingsPage
from src.services.background import run_in_background
from src.services.logging_utils.loggers import init_root_gui_logging

logger = logging.getLogger()
_BREED_ICON_URL_TEMPLATE = "https://api.dofusdb.fr/img/breeds/symbol_{breed_id}.png"


class MainWindow(AppFluentWindow):
    account_widgets: list[AccountStackedWidget]
    bots_by_login: dict[str, Bot]
    sidebar_items_by_login: dict[str, SidebarItem]

    def __init__(self, title: str, shared_signals: SharedSignals) -> None:
        super().__init__(parent=None)

        self.global_log_signals = LogSignals()
        if config.DEBUG:
            init_root_gui_logging(self.global_log_signals)

        self.title = title
        self.shared_signals = shared_signals
        self.setWindowTitle(self.title)
        self.resize(BASE_WIDTH, BASE_HEIGHT)
        self.setWindowIcon(QIcon(LOGO_FILE))
        self.splashScreen = StartupSplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(102, 102))

        self.disconnected_icon = FluentIcon.PEOPLE.icon(color=QColor(255, 0, 0))
        self.connected_icon = FluentIcon.PEOPLE.icon(color=QColor(0, 255, 0))
        self._breed_icon_network_manager = QNetworkAccessManager(self)
        self._breed_icon_by_id: dict[int, QIcon] = {}

        self.account_widgets: list[AccountStackedWidget] = []
        self.bots_by_login: dict[str, Bot] = {}
        self.sidebar_items_by_login: dict[str, SidebarItem] = {}
        self._subscription_refresh_timer = QTimer(self)
        self._subscription_refresh_timer.setInterval(60_000)
        self._subscription_refresh_timer.timeout.connect(self._refresh_subscription_statuses)
        self._subscription_refresh_timer.start()
        self._close_requested = False
        self._shutdown_finished = False
        self.shared_signals.new_bot_added.connect(self.add_account)
        self.shared_signals.bot_removed.connect(self.remove_account)
        self.shared_signals.shutdown_finished.connect(self.complete_shutdown)
        self.activity_page = ActivityPage(parent=self)
        self.stackedWidget.addWidget(self.activity_page)
        self.navigationInterface.addItem(
            routeKey=self.activity_page.objectName(),
            icon=FluentIcon.HISTORY,  # type: ignore
            text="Activity",
            onClick=lambda: self.switchTo(self.activity_page),
            position=NavigationItemPosition.TOP,
        )
        self.navigationInterface.setCurrentItem(self.activity_page.objectName())
        self.settings_page = SettingsPage(self)
        self.stackedWidget.addWidget(self.settings_page)
        self.navigationInterface.addItem(
            routeKey="settings",
            icon=FluentIcon.SETTING,  # type: ignore
            text="Settings",
            onClick=lambda: self.switchTo(self.settings_page),
            position=NavigationItemPosition.BOTTOM,
        )
        self.settings_page.schedules_changed.connect(self._refresh_schedules)
        self.confirmation_dialogs = ManualConfirmationDialogs(self)

    def _refresh_schedules(self) -> None:
        if not BotConfigService.use_bot_config_json:
            return
        bots = list(self.bots_by_login.values())

        def refresh(_progress: Callable[[str], None]) -> None:
            for bot in bots:
                bot.scheduler.request_configuration_refresh()

        run_in_background(refresh)

    def set_startup_status(self, status: str) -> None:
        self.setWindowTitle(f"{self.title} — {status}")
        self.splashScreen.set_status(status)

    def init_accounts(self, account_by_id: dict[int, Bot]) -> None:
        snapshots = PlayerInfoStorage().get_all_snapshots()
        records = BotStorageController().get_all_records()
        for account in account_by_id.values():
            login = account.account.apikey.login
            record = records.get(login)
            self.add_account(
                account,
                snapshot=snapshots.get(login),
                quarantine_reason=record.quarantine_reason if record is not None else None,
            )
        if config.DEBUG:
            run_in_background(
                lambda _: AccountQuickInfoWidget.resolve_snapshot_sub_area_names(snapshots),
                on_success=self._set_snapshot_sub_area_names,
                parent=self,
            )

    def add_account(
        self,
        account: Bot,
        snapshot: PlayerInfoSnapshot | None = None,
        quarantine_reason: str | None = None,
    ) -> None:
        login = account.account.apikey.login
        self.bots_by_login[login] = account

        account_widget = AccountStackedWidget(
            self.global_log_signals,
            login,
            account,
            snapshot,
            quarantine_reason,
        )
        self.account_widgets.append(account_widget)
        navigation_widget = SidebarItem(account.bot_signals, self.disconnected_icon, login, True, parent=self)
        self._set_sidebar_title(navigation_widget, login, snapshot)
        self.sidebar_items_by_login[login] = navigation_widget
        navigation_widget.set_subscribed(account.game_state.player.is_sub)
        navigation_widget.set_playing(account.is_playing_event.is_set())
        is_connected = account.is_connected_event.is_set()
        navigation_widget.set_connected(is_connected)
        if is_connected:
            navigation_widget.set_left_icon(self.connected_icon)
        account_widget.setObjectName(login)
        self.addWidget(
            account_widget,
            navigation_widget,
            position=NavigationItemPosition.SCROLL,
        )
        self.navigationInterface.panel.expand()

        bot_config_controller = BotConfigService()
        config = bot_config_controller.get_bot_config(login)

        selected_mode = config.connection_mode
        navigation_widget.populate_connection_mode(selected_mode)

        def on_connection_mode_changed(mode: str) -> None:
            assert mode in ("mitm", "socket"), f"Unknown connection mode {mode}"
            self._on_connection_mode_changed(login, mode)

        navigation_widget.connection_mode_changed.connect(on_connection_mode_changed)
        navigation_widget.disconnect_clicked.connect(lambda: self._on_disconnect_clicked(login))

        def on_connected(_characters: object) -> None:
            navigation_widget.set_left_icon(self.connected_icon)
            navigation_widget.set_connected(True)

        account.game_info_signals.connected.connect(on_connected)
        account.game_info_signals.character_name.connect(
            lambda name: navigation_widget.set_title(  # type: ignore
                f"{account.account.apikey.login.split('@')[0]} : {name}"
            )  # type: ignore
        )

        account.game_info_signals.in_fight.connect(navigation_widget.show_battle_icon)
        account.game_info_signals.is_ready_to_play.connect(
            partial(self._show_breed_icon, navigation_widget, account)
        )
        account.game_info_signals.disconnected.connect(
            lambda: navigation_widget.set_left_icon(self.disconnected_icon)
        )
        account.game_info_signals.disconnected.connect(lambda: navigation_widget.set_connected(False))
        account.game_info_signals.disconnected.connect(
            partial(self._set_sidebar_title_from_storage, navigation_widget, login)
        )

        if account.is_ready_to_play_event.is_set():
            self._show_breed_icon(navigation_widget, account)

    @staticmethod
    def _set_sidebar_title(
        navigation_widget: SidebarItem,
        login: str,
        snapshot: PlayerInfoSnapshot | None,
    ) -> None:
        if snapshot is None:
            navigation_widget.set_title(login)
            return
        navigation_widget.set_title(f"{login.split('@')[0]} : {snapshot.character_name}")

    def _set_sidebar_title_from_storage(self, navigation_widget: SidebarItem, login: str) -> None:
        self._set_sidebar_title(navigation_widget, login, PlayerInfoStorage().get_snapshot(login))

    def _set_snapshot_sub_area_names(self, result: object) -> None:
        sub_area_name_by_login = cast(dict[str, str], result)
        for account_widget in self.account_widgets:
            account_widget.set_snapshot_sub_area_name(sub_area_name_by_login.get(account_widget.login))

    def _show_breed_icon(self, navigation_widget: SidebarItem, account: Bot) -> None:
        navigation_widget.set_left_icon(self.connected_icon)
        breed_id = account.game_state.fight.breed_id
        if breed_id <= 0:
            return

        cached_icon = self._breed_icon_by_id.get(breed_id)
        if cached_icon is not None:
            navigation_widget.set_left_icon(cached_icon)
            return

        request = QNetworkRequest(QUrl(_BREED_ICON_URL_TEMPLATE.format(breed_id=breed_id)))
        request.setTransferTimeout(10_000)
        reply = self._breed_icon_network_manager.get(request)
        if not reply:
            return
        reply.finished.connect(
            partial(
                self._on_breed_icon_reply_finished,
                reply,
                navigation_widget,
                account,
                breed_id,
            )
        )

    def _on_breed_icon_reply_finished(
        self,
        reply: QNetworkReply,
        navigation_widget: SidebarItem,
        account: Bot,
        breed_id: int,
    ) -> None:
        if reply.error() != QNetworkReply.NetworkError.NoError:
            logger.warning(
                "Cannot load breed icon %s for %s: %s",
                breed_id,
                account.account.apikey.login,
                reply.errorString(),
            )
            reply.deleteLater()
            return

        pixmap = QPixmap()
        if not pixmap.loadFromData(reply.readAll().data(), "PNG"):
            logger.warning(
                "Cannot load breed icon %s for %s: invalid PNG response",
                breed_id,
                account.account.apikey.login,
            )
            reply.deleteLater()
            return

        reply.deleteLater()
        breed_icon = QIcon(pixmap)
        self._breed_icon_by_id[breed_id] = breed_icon

        login = account.account.apikey.login
        if self.bots_by_login.get(login) is not account:
            return
        if not account.is_connected_event.is_set():
            return
        if account.game_state.fight.breed_id != breed_id:
            return
        navigation_widget.set_left_icon(breed_icon)

    def remove_account(self, account: Bot) -> None:
        login = account.account.apikey.login
        self.bots_by_login.pop(login, None)
        self.sidebar_items_by_login.pop(login)
        self._disconnect_game_info_signals(account)

        account_widget = next(
            (widget for widget in self.account_widgets if widget.objectName() == login),
            None,
        )
        if account_widget is None:
            return

        self.account_widgets.remove(account_widget)
        self.removeWidget(login, account_widget)
        account_widget.deleteLater()

        if self.account_widgets:
            self.switchTo(self.account_widgets[0])
            self.navigationInterface.setCurrentItem(self.account_widgets[0].objectName())

    def _refresh_subscription_statuses(self) -> None:
        for login, account in self.bots_by_login.items():
            self.sidebar_items_by_login[login].set_subscribed(account.game_state.player.is_sub)

    def _disconnect_game_info_signals(self, account: Bot) -> None:
        for signal in (
            account.game_info_signals.connected,
            account.game_info_signals.character_name,
            account.game_info_signals.in_fight,
            account.game_info_signals.is_ready_to_play,
            account.game_info_signals.disconnected,
        ):
            try:
                signal.disconnect()
            except TypeError:
                pass

    def _on_connection_mode_changed(self, login: str, mode: Literal["mitm", "socket"]) -> None:
        BotConfigService().assign_mode(login, mode)

    def _on_disconnect_clicked(self, login: str) -> None:
        bot = self.bots_by_login[login]
        if not bot.is_connected_event.is_set():
            return
        bot.scheduler.disconnect_now()

    def closeEvent(self, a0: QCloseEvent | None) -> None:
        if self._shutdown_finished:
            super().closeEvent(a0)
            return

        if a0 is not None:
            a0.ignore()
        if self._close_requested:
            return

        self._close_requested = True
        self.confirmation_dialogs.shutdown()
        self.setEnabled(False)
        self.shared_signals.closed.emit()

    def complete_shutdown(self) -> None:
        self._shutdown_finished = True
        self.close()

from collections.abc import Callable
from typing import cast

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import QScrollArea, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import SegmentedWidget, SubtitleLabel

from src.gui.pages.settings.settings_panel import SettingsPanel
lazy from src.gui.pages.settings.assignment_panel import AssignmentSettingsPanel
lazy from src.gui.pages.settings.behavior_panel import BehaviorSettingsPanel
lazy from src.gui.pages.settings.mail_panel import MailSettingsPanel
lazy from src.gui.pages.settings.payment_panel import PaymentSettingsPanel
lazy from src.gui.pages.settings.proxy_panel import ProxySettingsPanel
lazy from src.gui.pages.settings.schedule_panel import ScheduleSettingsPanel
lazy from src.gui.pages.settings.services_panel import ServicesSettingsPanel


class SettingsPage(QWidget):
    schedules_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settings")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(SubtitleLabel("Settings", self))
        self.navigation = SegmentedWidget(self)
        layout.addWidget(self.navigation)
        self.stack = QStackedWidget(self)
        layout.addWidget(self.stack)
        self._panels: dict[int, SettingsPanel] = {}
        self._factories: list[Callable[[], SettingsPanel]] = [
            lambda: BehaviorSettingsPanel(),
            lambda: MailSettingsPanel(),
            lambda: ProxySettingsPanel(),
            lambda: ScheduleSettingsPanel(),
            lambda: AssignmentSettingsPanel(),
            lambda: ServicesSettingsPanel(),
            lambda: PaymentSettingsPanel(),
        ]
        self._account_actions: (
            tuple[Callable[[str], None], Callable[[str], None], Callable[[], None]] | None
        ) = None
        for index, label in enumerate(
            ("Behaviors", "Email", "Proxies", "Schedules", "Accounts", "Services", "Payments")
        ):
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
            self.stack.addWidget(scroll)
            self.navigation.addItem(
                str(index), label, onClick=lambda _checked=False, index=index: self._select(index)
            )
            self.navigation.widget(str(index)).setFixedHeight(44)
        self.navigation.setCurrentItem("0")

    def bind_account_actions(
        self,
        delete_account: Callable[[str], None],
        delete_mailbox: Callable[[str], None],
        refresh_activity: Callable[[], None],
    ) -> None:
        self._account_actions = delete_account, delete_mailbox, refresh_activity
        for index, panel in self._panels.items():
            self._bind_deletion(index, panel)

    def _bind_deletion(self, index: int, panel: SettingsPanel) -> None:
        if self._account_actions is None:
            return
        delete_account, delete_mailbox, refresh_activity = self._account_actions
        if index == 4:
            assignment = cast(AssignmentSettingsPanel, panel)
            panel.bind_deletion(assignment.account, delete_account, refresh_activity, background=False)
        elif index == 1:
            mail = cast(MailSettingsPanel, panel)
            panel.bind_deletion(mail.selection, delete_mailbox, refresh_activity, background=False)

    def _select(self, index: int) -> None:
        if index not in self._panels:
            panel = self._factories[index]()
            self._panels[index] = panel
            panel.setAutoFillBackground(False)
            if index == 3:
                cast(ScheduleSettingsPanel, panel).changed.connect(self.schedules_changed.emit)
            elif index == 4:
                cast(AssignmentSettingsPanel, panel).changed.connect(self.schedules_changed.emit)
            self._bind_deletion(index, panel)
            scroll = cast(QScrollArea, self.stack.widget(index))
            scroll.setWidget(panel)
        self.stack.setCurrentIndex(index)

    def showEvent(self, a0: QShowEvent | None) -> None:
        super().showEvent(a0)
        self._select(self.stack.currentIndex())

    def enable_editing(self) -> None:
        self.setEnabled(True)
        panel = self._panels.get(self.stack.currentIndex())
        if self.isVisible() and panel is not None:
            panel.reload()

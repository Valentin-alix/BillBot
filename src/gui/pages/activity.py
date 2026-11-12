from typing import cast

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    SmoothMode,
    SubtitleLabel,
    TableWidget,
)
from qfluentwidgets.components.dialog_box.dialog import MessageBox
from qfluentwidgets.components.widgets.label import StrongBodyLabel

from ankama_launcher_emulator.controller.bot_storage import (
    BotStorageController,
)
from ankama_launcher_emulator.controller.mail_account import (
    MailAccountController,
)
from ankama_launcher_emulator.quarantine_signals import (
    quarantine_signals,
)
from src.services.background import run_in_background
from src.services.user_activity import UserActivityEntry, UserActivityService

QuarantineRow = tuple[str, str, str]


class ActivityPage(QWidget):
    restore_account_requested = pyqtSignal(str)
    delete_account_requested = pyqtSignal(str)
    restore_mailbox_requested = pyqtSignal(str)
    delete_mailbox_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._activity_service = UserActivityService()
        self._activity_entries: tuple[UserActivityEntry, ...] | None = None
        self._activity_logins: frozenset[str] = frozenset()
        self._quarantined: list[QuarantineRow] | None = None
        self._selected_target: tuple[str, str] | None = None
        self._actions_enabled = True
        self._quarantine_refresh_pending = False
        self.setObjectName("activity")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(SubtitleLabel("Activity", self))
        layout.addWidget(
            BodyLabel(
                "Review recent events and decide what to do with quarantined items.",
                self,
            )
        )
        layout.addWidget(self._create_activity_card(), 3)
        layout.addWidget(self._create_quarantine_card(), 2)

        self._activity_service.signals.entry_added.connect(self._on_activity_entry_added)
        quarantine_signals.changed.connect(self._on_quarantine_changed)

    def _create_activity_card(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(StrongBodyLabel("Recent history", card))
        header.addStretch()
        header.addWidget(BodyLabel("Account", card))
        self.login_filter = ComboBox(card)
        self.login_filter.setMinimumWidth(190)
        self.login_filter.currentIndexChanged.connect(self._render_activity)
        header.addWidget(self.login_filter)
        layout.addLayout(header)

        self.activity_table = self._create_table(card, ["Date", "Account", "Level", "Event"])
        self.activity_table.setMinimumHeight(220)
        layout.addWidget(self.activity_table)
        return card

    def _create_quarantine_card(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(StrongBodyLabel("Quarantined items", card))
        header.addStretch()
        header.addWidget(BodyLabel("No action is taken without confirmation.", card))
        layout.addLayout(header)

        self.quarantine_table = self._create_table(card, ["Type", "Identifier", "Reason"])
        self.quarantine_table.setMinimumHeight(160)
        self.quarantine_table.itemSelectionChanged.connect(self._select_quarantine)
        layout.addWidget(self.quarantine_table)

        actions = QHBoxLayout()
        actions.addStretch()
        self.restore_button = PrimaryPushButton(FluentIcon.SYNC, "Restore", card)
        self.restore_button.clicked.connect(self._restore_selected)
        self.delete_button = PushButton(FluentIcon.DELETE, "Delete", card)
        self.delete_button.clicked.connect(self._delete_selected)
        actions.addWidget(self.restore_button)
        actions.addWidget(self.delete_button)
        layout.addLayout(actions)
        return card

    def showEvent(self, a0: QShowEvent | None) -> None:
        self.refresh()
        super().showEvent(a0)

    @staticmethod
    def _create_table(parent: QWidget, headers: list[str]) -> TableWidget:
        table = TableWidget(parent)
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.scrollDelagate.verticalSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)
        table.scrollDelagate.horizonSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)
        table.scrollDelagate.vScrollBar.setScrollAnimation(0)
        table.scrollDelagate.hScrollBar.setScrollAnimation(0)
        vertical_header = table.verticalHeader()
        horizontal_header = table.horizontalHeader()
        assert vertical_header is not None
        assert horizontal_header is not None
        vertical_header.setVisible(False)
        horizontal_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        horizontal_header.setStretchLastSection(True)
        return table

    @staticmethod
    def _set_table_rows(table: TableWidget, rows: list[tuple[str, ...]]) -> None:
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))

    def refresh(self) -> None:
        self._refresh_activity()
        self._refresh_quarantined()

    def _on_activity_entry_added(self) -> None:
        if self.isVisible():
            self._refresh_activity()

    def _on_quarantine_changed(self) -> None:
        if self.isVisible():
            self._refresh_quarantined()

    def _refresh_activity(self) -> None:
        entries = self._activity_service.recent()
        if entries == self._activity_entries:
            return
        current_login = cast(str | None, self.login_filter.currentData())
        previous_entries = self._activity_entries
        self._activity_entries = entries
        logins = frozenset(entry.login for entry in entries if entry.login is not None)
        if logins != self._activity_logins:
            self._activity_logins = logins
            self._refresh_login_filter(current_login)
            self._render_activity()
            return
        if (
            previous_entries is not None
            and len(entries) == len(previous_entries) + 1
            and entries[:-1] == previous_entries
        ):
            self._append_activity_entry(entries[-1])
            return
        self._render_activity()

    def _refresh_login_filter(self, current_login: str | None) -> None:
        assert self._activity_entries is not None
        logins = sorted({entry.login for entry in self._activity_entries if entry.login is not None})
        self.login_filter.blockSignals(True)
        self.login_filter.clear()
        self.login_filter.addItem("Tous", userData=None)
        for login in logins:
            self.login_filter.addItem(login, userData=login)
        self.login_filter.setCurrentIndex(max(self.login_filter.findData(current_login), 0))
        self.login_filter.blockSignals(False)

    def _render_activity(self) -> None:
        if self._activity_entries is None:
            return
        selected_login = cast(str | None, self.login_filter.currentData())
        rows = [
            self._activity_row(entry)
            for entry in self._activity_entries
            if selected_login is None or entry.login == selected_login
        ]
        self.activity_table.setUpdatesEnabled(False)
        self._set_table_rows(self.activity_table, rows)
        self.activity_table.setUpdatesEnabled(True)
        QTimer.singleShot(0, self.activity_table.scrollToBottom)

    def _append_activity_entry(self, entry: UserActivityEntry) -> None:
        selected_login = cast(str | None, self.login_filter.currentData())
        if selected_login is not None and entry.login != selected_login:
            return
        row = self.activity_table.rowCount()
        self.activity_table.insertRow(row)
        for column, value in enumerate(self._activity_row(entry)):
            self.activity_table.setItem(row, column, QTableWidgetItem(value))
        QTimer.singleShot(0, self.activity_table.scrollToBottom)

    @staticmethod
    def _activity_row(entry: UserActivityEntry) -> tuple[str, str, str, str]:
        return (
            entry.happened_at.astimezone().strftime("%d/%m %H:%M:%S"),
            entry.login or "Application",
            entry.severity,
            entry.message,
        )

    def _refresh_quarantined(self) -> None:
        if self._quarantine_refresh_pending:
            return
        self._quarantine_refresh_pending = True
        run_in_background(
            lambda _: self._load_quarantined(),
            on_success=self._render_quarantined,
            on_error=self._on_quarantine_load_error,
            parent=self,
        )

    @staticmethod
    def _load_quarantined() -> list[QuarantineRow]:
        quarantined: list[QuarantineRow] = []
        for login, record in BotStorageController().get_all_records().items():
            if record.quarantine_reason is not None:
                quarantined.append(("Account", login, record.quarantine_reason))
        for email, entry in MailAccountController().get_all_entries().items():
            if entry.quarantine_reason is not None:
                quarantined.append(("Mailbox", email, entry.quarantine_reason))
        return quarantined

    def _render_quarantined(self, result: object) -> None:
        quarantined = cast(list[QuarantineRow], result)
        self._quarantine_refresh_pending = False
        if quarantined == self._quarantined:
            return
        self._quarantined = quarantined
        selected_row = next(
            (
                row
                for row, (kind, identifier, _) in enumerate(quarantined)
                if (kind, identifier) == self._selected_target
            ),
            None,
        )
        self._selected_target = (
            (quarantined[selected_row][0], quarantined[selected_row][1]) if selected_row is not None else None
        )

        self.quarantine_table.blockSignals(True)
        self._set_table_rows(self.quarantine_table, quarantined)
        if selected_row is not None:
            self.quarantine_table.selectRow(selected_row)
        self.quarantine_table.blockSignals(False)
        self._set_action_enabled()

    def _on_quarantine_load_error(self, _: object) -> None:
        self._quarantine_refresh_pending = False

    def _select_quarantine(self) -> None:
        row = self.quarantine_table.currentRow()
        if row < 0:
            self._selected_target = None
        else:
            assert self._quarantined is not None
            kind, identifier, _ = self._quarantined[row]
            self._selected_target = kind, identifier
        self._set_action_enabled()

    def _set_action_enabled(self) -> None:
        enabled = self._actions_enabled and self._selected_target is not None
        self.restore_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    def set_actions_enabled(self, enabled: bool) -> None:
        self._actions_enabled = enabled
        self._set_action_enabled()

    def _restore_selected(self) -> None:
        if self._selected_target is None:
            return
        kind, identifier = self._selected_target
        if kind == "Account":
            self.restore_account_requested.emit(identifier)
        else:
            self.restore_mailbox_requested.emit(identifier)
        self.refresh()

    def _delete_selected(self) -> None:
        if self._selected_target is None:
            return
        kind, identifier = self._selected_target
        confirmation = MessageBox(
            "Delete permanently",
            f"Permanently delete {identifier}? This action cannot be undone.",
            self,
        )
        confirmation.yesButton.setText("Delete")
        confirmation.cancelButton.setText("Cancel")
        if not confirmation.exec():
            return
        if kind == "Account":
            self.delete_account_requested.emit(identifier)
        else:
            self.delete_mailbox_requested.emit(identifier)
        self.refresh()

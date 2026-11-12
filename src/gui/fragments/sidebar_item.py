import os

from PyQt6.QtCore import QMargins, QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QIcon, QPainter, QPaintEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    TransparentToolButton,
)
from qfluentwidgets.common.config import isDarkTheme
from qfluentwidgets.common.icon import toQIcon
from qfluentwidgets.common.style_sheet import themeColor
from qfluentwidgets.components.navigation.navigation_widget import NavigationWidget
from qfluentwidgets.components.widgets.tool_tip import ToolTipFilter

from src.consts import RESOURCE_FOLDER
from src.core.signals.bot_signals import BotSignals


class SidebarItem(NavigationWidget):
    connection_mode_changed = pyqtSignal(str)
    disconnect_clicked = pyqtSignal()
    play_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()

    def __init__(
        self,
        bot_signals: BotSignals,
        left_icon: FluentIcon | QIcon,
        title: str,
        isSelectable: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            isSelectable=isSelectable,
            parent=parent,
        )
        self.bot_signals = bot_signals
        self.in_fight: bool = False
        self._is_playing: bool = False
        self._is_connected: bool = False
        self._left_icon_source = toQIcon(left_icon)
        self.main_layout = QVBoxLayout()
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.main_layout.setContentsMargins(4, 4, 0, 4)
        self.main_layout.setSpacing(4)

        self.header_layout = QHBoxLayout()
        self.header_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.header_layout.setContentsMargins(4, 0, 12, 0)

        self.controls_layout = QHBoxLayout()
        self.controls_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.controls_layout.setContentsMargins(4, 0, 12, 0)

        self.setLayout(self.main_layout)
        self.main_layout.addLayout(self.header_layout)
        self._status_label = CaptionLabel(self)
        self._status_label.setIndent(6)
        self._status_label.hide()
        self.main_layout.addWidget(self._status_label)
        self.main_layout.addLayout(self.controls_layout)

        self._left_icon = QLabel(self)
        self._sync_left_icon()
        self.header_layout.addWidget(self._left_icon)

        self._title = BodyLabel(title, self)
        self.header_layout.addWidget(self._title)

        spacer = QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.header_layout.addItem(spacer)

        self._right_icon = QLabel(self)
        battle_icon = QIcon(os.path.join(RESOURCE_FOLDER, "icons", "combat.png"))
        self._right_icon.setPixmap(battle_icon.pixmap(16))
        self._right_icon.hide()
        self.header_layout.addWidget(self._right_icon)

        self._subscription_icon = QLabel(self)
        self._subscription_icon.setFixedSize(24, 24)
        self._subscription_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subscription_icon.installEventFilter(ToolTipFilter(self._subscription_icon, 0))
        self.header_layout.addWidget(self._subscription_icon)
        self.set_subscribed(False)

        self._mode_combo = ComboBox(self)
        self._mode_combo.setFixedWidth(150)
        self._mode_combo.addItem("Mitm", userData="mitm")
        self._mode_combo.addItem("Socket", userData="socket")
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.controls_layout.addWidget(self._mode_combo)

        self._play_btn = TransparentToolButton(FluentIcon.PLAY, self)
        self._play_btn.setFixedSize(24, 24)
        self._play_btn.clicked.connect(self.on_click_play)
        self.bot_signals.play.connect(self.on_play)
        self.controls_layout.addWidget(self._play_btn)

        self._stop_btn = TransparentToolButton(FluentIcon.PAUSE, self)
        self._stop_btn.setFixedSize(24, 24)
        self._stop_btn.clicked.connect(self.on_click_stop)
        self.bot_signals.stop.connect(self.on_stop)
        self._stop_btn.hide()
        self.controls_layout.addWidget(self._stop_btn)

        self._disconnect_btn = TransparentToolButton(FluentIcon.CLOSE, self)
        self._disconnect_btn.setFixedSize(24, 24)
        self._disconnect_btn.setToolTip("Disconnect")
        self._disconnect_btn.installEventFilter(ToolTipFilter(self._disconnect_btn, 0))
        self._disconnect_btn.setEnabled(False)
        self._disconnect_btn.clicked.connect(self.disconnect_clicked.emit)
        self.controls_layout.addWidget(self._disconnect_btn)
        self.bot_signals.automation_status_changed.connect(self._on_automation_status_changed)

    def show_battle_icon(self, show: bool) -> None:
        self.in_fight = show
        if show:
            self._right_icon.show()
        else:
            self._right_icon.hide()

    def set_controls_enabled(self, enabled: bool) -> None:
        for widget in (self._mode_combo, self._play_btn, self._stop_btn, self._disconnect_btn):
            widget.setEnabled(enabled)

    def set_subscribed(self, is_subscribed: bool) -> None:
        color = QColor(0, 230, 118) if is_subscribed else QColor(244, 67, 54)
        subscription_icon = FluentIcon("Certificate")
        self._subscription_icon.setPixmap(subscription_icon.icon(color=color).pixmap(16))
        self._subscription_icon.setToolTip(
            "Subscription status: " + ("subscribed" if is_subscribed else "not subscribed")
        )

    def setCompacted(self, isCompacted: bool) -> None:
        if isCompacted == self.isCompacted:
            return

        self.isCompacted = isCompacted
        if isCompacted:
            self.header_layout.setContentsMargins(4, 0, 0, 0)
            self.setFixedSize(32, 48)
            self._title.hide()
            self._status_label.hide()
            self._right_icon.hide()
            self._subscription_icon.hide()
            self._play_btn.hide()
            self._stop_btn.hide()
            self._disconnect_btn.hide()
            self._mode_combo.hide()
        else:
            self.header_layout.setContentsMargins(4, 0, 12, 0)
            self.setFixedSize(self.EXPAND_WIDTH, 96)
            self._title.show()
            self._status_label.setVisible(bool(self._status_label.text()))
            if self.in_fight:
                self._right_icon.show()
            self._subscription_icon.show()
            self._sync_play_buttons()
            self._disconnect_btn.show()
            self._mode_combo.show()

        self.update()

    def _sync_play_buttons(self) -> None:
        if self.isCompacted:
            self._play_btn.hide()
            self._stop_btn.hide()
            return
        if self._is_playing:
            self._play_btn.hide()
            self._stop_btn.show()
        else:
            self._stop_btn.hide()
            self._play_btn.show()

    def set_playing(self, is_playing: bool) -> None:
        self._is_playing = is_playing
        self._sync_play_buttons()

    def set_connected(self, is_connected: bool) -> None:
        self._is_connected = is_connected
        self._disconnect_btn.setEnabled(is_connected)
        self._sync_left_icon()

    def _sync_left_icon(self) -> None:
        pixmap = self._left_icon_source.pixmap(16)
        if self._is_connected:
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 180))
            painter.drawEllipse(9, 9, 7, 7)
            painter.setBrush(QColor(0, 230, 118))
            painter.drawEllipse(10, 10, 5, 5)
            painter.end()
        self._left_icon.setPixmap(pixmap)

    def _margins(self) -> QMargins:
        return QMargins(0, 0, 0, 0)

    def _canDrawIndicator(self) -> bool:
        return self.isSelected

    def set_left_icon(self, icon: QIcon | FluentIcon) -> None:
        self._left_icon_source = toQIcon(icon)
        self._sync_left_icon()

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def populate_connection_mode(self, selected_mode: str) -> None:
        for mode_index in range(self._mode_combo.count()):
            if self._mode_combo.itemData(mode_index) == selected_mode:
                self._mode_combo.setCurrentIndex(mode_index)
                return

    def _on_mode_changed(self) -> None:
        mode = self._mode_combo.currentData()
        self.connection_mode_changed.emit(mode if mode else "mitm")

    def on_click_play(self) -> None:
        self.bot_signals.play.emit(True)
        self.bot_signals.play_auto_bot.emit()

    def on_click_stop(self) -> None:
        self.bot_signals.stop.emit()

    def on_play(self, _: bool) -> None:
        self.set_playing(True)

    def on_stop(self) -> None:
        self.set_playing(False)

    def _on_automation_status_changed(self, status: str) -> None:
        self._status_label.setText(status)
        if not self.isCompacted:
            self._status_label.setVisible(bool(status))

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        painter.setPen(Qt.PenStyle.NoPen)

        if self.isPressed:
            painter.setOpacity(0.7)
        if not self.isEnabled():
            painter.setOpacity(0.4)

        c = 255 if isDarkTheme() else 0
        m = self._margins()
        pl = m.left()
        globalRect = QRect(self.mapToGlobal(QPoint()), self.size())

        if self._canDrawIndicator():
            painter.setBrush(QColor(c, c, c, 6 if self.isEnter else 10))
            painter.drawRoundedRect(self.rect(), 5, 5)

            painter.setBrush(themeColor())
            indicator_height = max(16, int(self.height() * 0.65))
            indicator_top = (self.height() - indicator_height) // 2
            painter.drawRoundedRect(pl, indicator_top, 3, indicator_height, 1.5, 1.5)
        elif self.isEnter and self.isEnabled() and globalRect.contains(QCursor.pos()):
            painter.setBrush(QColor(c, c, c, 10))
            painter.drawRoundedRect(self.rect(), 5, 5)

        painter.setFont(self.font())
        painter.setPen(self.textColor())

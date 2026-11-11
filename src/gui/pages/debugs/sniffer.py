from __future__ import annotations

lazy from src.core.bot.bot import Bot

from functools import partial
from pathlib import Path
from typing import Any, cast

from google.protobuf.descriptor import Descriptor
from PyQt6.QtCore import QModelIndex, QStringListModel, Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QHideEvent, QShowEvent
from PyQt6.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    FluentIcon,
    LineEdit,
    MessageBoxBase,
    PrimaryPushButton,
    SegmentedWidget,
    SubtitleLabel,
)

from DBDofusUnity.consts import PINNED_PAIRS_FILE
from DBDofusUnity.proto_mapper_assembly.controllers.pinned_pairs import (
    upsert_pinned_field_mapping,
    upsert_pinned_pair,
)
from src.core.signals.log_signals import LogSignals
from src.services.debug_recorder import DebugRecorder
from src.gui.pages.debugs.listeners_stats import ListenersStatsWidget
from src.gui.pages.debugs.logs import LogsWidget
from src.gui.pages.debugs.message_detail import MessageDetailWidget, SelectedPinnedField
from src.gui.pages.debugs.message_table import MessageTable
from src.gui.utils.profiling import profiled_slot
from src.protocol.message import MessageInfo
from src.protocol.message_names import (
    build_non_obf_game_message_pinned_name,
    find_non_obf_game_message_descriptor,
    load_non_obf_game_message_full_names,
)
from src.protocol.protocol_game import POOL
from src.protocol import initialize_protocol


def _require_message_info(value: object) -> MessageInfo:
    if not isinstance(value, MessageInfo):
        raise TypeError("Expected a MessageInfo payload in the message table model")
    return value


def _parse_sub_msg_name(sub_msg_name: str) -> tuple[str | None, str]:
    if " -> " in sub_msg_name:
        obf, decoded = sub_msg_name.split(" -> ", 1)
        return obf, decoded
    return None, sub_msg_name


def _extract_obf_msg_name_for_pinned_pair(msg_info: MessageInfo) -> str | None:
    if msg_info.obf_msg_json is None:
        return None
    obf_msg_name, _ = _parse_sub_msg_name(msg_info.sub_msg_name)
    return obf_msg_name or msg_info.sub_msg_name


def _extract_pinned_pair_names_for_fields(
    msg_info: MessageInfo,
) -> tuple[str, str] | None:
    if msg_info.msg_json is None or msg_info.obf_msg_json is None:
        return None
    obf_msg_name, non_obf_msg_name = _parse_sub_msg_name(msg_info.sub_msg_name)
    if obf_msg_name is None:
        return None
    non_obf_descriptor = find_non_obf_game_message_descriptor(non_obf_msg_name)
    assert non_obf_descriptor is not None, (
        f"Missing non-obfuscated descriptor for pinned message {non_obf_msg_name!r}"
    )
    return obf_msg_name, build_non_obf_game_message_pinned_name(non_obf_descriptor)


def _find_obf_game_message_descriptor(name: str | None) -> Descriptor | None:
    if name is None:
        return None
    initialize_protocol()
    try:
        return POOL.FindMessageTypeByName(name)
    except KeyError:
        return None


def _resolve_pinned_field_message_pair(
    root_message_pair: tuple[str, str],
    obf_field: SelectedPinnedField,
    non_obf_field: SelectedPinnedField,
) -> tuple[str, str] | None:
    if len(obf_field.path) == 1 and len(non_obf_field.path) == 1:
        return root_message_pair
    if len(obf_field.path) == 1 or len(non_obf_field.path) == 1:
        return None
    if obf_field.container_descriptor is None or non_obf_field.container_descriptor is None:
        return None
    return (
        obf_field.container_descriptor.full_name,
        build_non_obf_game_message_pinned_name(non_obf_field.container_descriptor),
    )


def _resolve_child_pinned_message_pair(
    obf_field: SelectedPinnedField,
    non_obf_field: SelectedPinnedField,
) -> tuple[str, str] | None:
    obf_message_descriptor = obf_field.message_descriptor
    non_obf_message_descriptor = non_obf_field.message_descriptor
    if obf_message_descriptor is None or non_obf_message_descriptor is None:
        return None
    return (
        obf_message_descriptor.full_name,
        build_non_obf_game_message_pinned_name(non_obf_message_descriptor),
    )


def _upsert_pinned_field_mapping_with_child_pair(
    path: Path,
    resolved_message_pair: tuple[str, str],
    obf_field: SelectedPinnedField,
    non_obf_field: SelectedPinnedField,
) -> None:
    obf_msg_name, non_obf_msg_name = resolved_message_pair
    upsert_pinned_field_mapping(
        path,
        obf_msg_name,
        non_obf_msg_name,
        obf_field.field_name,
        non_obf_field.field_name,
    )
    child_message_pair = _resolve_child_pinned_message_pair(obf_field, non_obf_field)
    if child_message_pair is not None:
        child_obf_msg_name, child_non_obf_msg_name = child_message_pair
        upsert_pinned_pair(
            path,
            child_obf_msg_name,
            child_non_obf_msg_name,
        )


class PinnedPairMessageBox(MessageBoxBase):
    def __init__(
        self,
        obf_msg_name: str,
        non_obf_msg_names: list[str],
        parent: QWidget,
    ) -> None:
        super().__init__(parent=parent)
        self._valid_non_obf_msg_names = set(non_obf_msg_names)
        self.title_label = SubtitleLabel(f"Pinned pair for {obf_msg_name}", parent=self)
        self.message_name_edit = LineEdit(self)
        self.message_name_edit.setPlaceholderText("Unobfuscated message name")
        self.message_name_edit.setMinimumWidth(420)

        completer = QCompleter(self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setModel(QStringListModel(non_obf_msg_names, completer))
        self.message_name_edit.setCompleter(completer)

        cast(Any, self).yesButton.setText("Sauvegarder")
        cast(Any, self).cancelButton.setText("Cancel")

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.message_name_edit)

    @property
    def non_obf_msg_name(self) -> str:
        return self.message_name_edit.text().strip()

    def validate(self) -> bool:
        if self.non_obf_msg_name in self._valid_non_obf_msg_names:
            return True
        self.message_name_edit.setFocus()
        self.message_name_edit.selectAll()
        return False


class SnifferWidget(QWidget):
    msg_table: MessageTable
    msg_detail: MessageDetailWidget
    play_btn: PrimaryPushButton
    stop_btn: PrimaryPushButton

    def __init__(
        self,
        bot: Bot,
        global_log_signals: LogSignals,
        use_recorder_feed: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.bot = bot
        self.global_log_signals = global_log_signals
        self._use_recorder_feed = use_recorder_feed
        self._message_cursor = 0
        self.is_playing: bool = True
        self._current_detail_msg_info: MessageInfo | None = None
        self.v_layout = QVBoxLayout()
        self.v_layout.setContentsMargins(4, 4, 4, 4)
        self.v_layout.setSpacing(0)
        self.setLayout(self.v_layout)
        self.init_top_content()
        self.init_content()
        self._feed_timer = QTimer(self)
        self._feed_timer.setInterval(100)
        self._feed_timer.timeout.connect(self._poll_recorder_messages)
        if self._use_recorder_feed:
            self.bot.msg_info_signals.set_capture_enabled(False)
        else:
            self.bot.msg_info_signals.msg_info.connect(profiled_slot(self.on_receive_msg_info))
            self.bot.msg_info_signals.set_capture_enabled(True)

    def init_top_content(self) -> None:
        top_content = QWidget(self)

        top_content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.v_layout.addWidget(top_content)
        top_content_layout = QHBoxLayout()
        top_content.setLayout(top_content_layout)
        top_content_layout.setContentsMargins(0, 0, 0, 0)
        reset_btn = PrimaryPushButton(FluentIcon.DELETE, "Reset", top_content)
        reset_btn.clicked.connect(self.on_reset)
        top_content_layout.addWidget(reset_btn)

        self.play_btn = PrimaryPushButton(FluentIcon.PLAY, "Start sniffer", top_content)
        if self.is_playing:
            self.play_btn.hide()
        self.play_btn.clicked.connect(self.on_play)
        top_content_layout.addWidget(self.play_btn)

        self.stop_btn = PrimaryPushButton(FluentIcon.PAUSE, "Stop sniffer", top_content)
        if not self.is_playing:
            self.stop_btn.hide()
        self.stop_btn.clicked.connect(self.on_stop)
        top_content_layout.addWidget(self.stop_btn)

    def init_content(self) -> None:
        content = QWidget(self)
        content_layout = QHBoxLayout()
        content.setLayout(content_layout)
        content_layout.setSpacing(0)
        content_layout.setContentsMargins(0, 0, 0, 0)

        self.msg_table = MessageTable(parent=self)
        self.msg_table.table.clicked.connect(self.on_click_msg)
        self.msg_table.table.doubleClicked.connect(self.on_double_click_msg)

        left_widget = QWidget(self)
        left_widget_layout = QVBoxLayout()
        left_widget.setLayout(left_widget_layout)
        left_widget_layout.setContentsMargins(0, 0, 0, 0)
        left_widget_layout.setSpacing(0)

        wrapper_filter = QWidget(left_widget)
        wrapper_filter_layout = QHBoxLayout()
        wrapper_filter.setLayout(wrapper_filter_layout)

        wrapper_filter.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        wrapper_filter_layout.setContentsMargins(0, 16, 0, 0)
        custom_filter = LineEdit(wrapper_filter)
        wrapper_filter_layout.addWidget(custom_filter)
        custom_filter.setPlaceholderText("Contenu")
        custom_filter.textChanged.connect(partial(self.msg_table.table.header.on_new_filter_input, 3))

        left_widget_layout.addWidget(wrapper_filter)
        left_widget_layout.addWidget(self.msg_table)
        left_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.msg_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.detail_stack = QStackedWidget(self)
        self.detail_empty_state = self._create_detail_empty_state(self.detail_stack)
        self.detail_stack.addWidget(self.detail_empty_state)
        self.msg_detail = MessageDetailWidget(parent=self.detail_stack)
        self.detail_stack.addWidget(self.msg_detail)
        self.msg_detail.quit_btn.clicked.connect(self.on_close_detail)
        self.msg_detail.lock_pinned_fields_btn.clicked.connect(self.on_lock_pinned_fields)

        self.right_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.right_splitter.addWidget(self.detail_stack)

        if self.bot.log_signals is not None:
            debug_tabs_widget = QWidget(self)
            debug_tabs_widget_layout = QVBoxLayout()
            debug_tabs_widget.setLayout(debug_tabs_widget_layout)
            debug_tabs_widget_layout.setContentsMargins(0, 16, 0, 0)
            debug_tabs_widget_layout.setSpacing(0)

            debug_pivot = SegmentedWidget(debug_tabs_widget)
            debug_tabs_widget_layout.addWidget(debug_pivot)

            debug_stacked = QStackedWidget(debug_tabs_widget)
            debug_tabs_widget_layout.addWidget(debug_stacked)

            logs_widget = LogsWidget(
                global_signals=self.global_log_signals,
                log_signals=self.bot.log_signals,
                parent=debug_stacked,
            )
            debug_stacked.addWidget(logs_widget)

            listeners_widget = ListenersStatsWidget(
                event_manager=self.bot.event_manager, parent=debug_stacked
            )
            debug_stacked.addWidget(listeners_widget)

            debug_pivot.addItem(
                routeKey="logs",
                text="Journaux",
                onClick=lambda: debug_stacked.setCurrentWidget(logs_widget),
            )
            debug_pivot.addItem(
                routeKey="listeners",
                text="Listeners",
                onClick=lambda: self._show_listeners_tab(debug_stacked, listeners_widget),
            )
            debug_pivot.setCurrentItem("logs")

            self.right_splitter.addWidget(debug_tabs_widget)

            self.logs_widget = logs_widget
            self.listeners_widget = listeners_widget
        else:
            self.logs_widget = None
            self.listeners_widget = None

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left_widget)
        splitter.addWidget(self.right_splitter)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        splitter.setSizes([800, 800])

        if self.logs_widget is not None:
            self.right_splitter.setStretchFactor(0, 1)
            self.right_splitter.setStretchFactor(1, 2)

            self.right_splitter.setSizes([150, 300])

        content_layout.addWidget(splitter)
        self.v_layout.addWidget(content)

        self.v_layout.setStretch(2, 1)

    @staticmethod
    def _create_detail_empty_state(parent: QWidget) -> QWidget:
        empty_state = QWidget(parent)
        layout = QVBoxLayout(empty_state)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(
            SubtitleLabel("Select a message to view its content", empty_state),
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        return empty_state

    @pyqtSlot(MessageInfo, bool)
    def on_receive_msg_info(self, msg_info: MessageInfo, was_send_from_proxy: bool) -> None:
        if self.is_playing:
            self.msg_table.add_row(msg_info, was_send_from_proxy)

    @pyqtSlot()
    def on_play(self) -> None:
        self.is_playing = True
        if self._use_recorder_feed:
            self._message_cursor = self._recorder().latest_message_sequence
            if self.isVisible():
                self._feed_timer.start()
        else:
            self.bot.msg_info_signals.set_capture_enabled(True)
        if self.logs_widget is not None:
            self.logs_widget.set_capture_enabled(True)
        self.play_btn.hide()
        self.stop_btn.show()

    @pyqtSlot()
    def on_stop(self) -> None:
        self.is_playing = False
        if self._use_recorder_feed:
            self._feed_timer.stop()
            self._message_cursor = self._recorder().latest_message_sequence
        else:
            self.bot.msg_info_signals.set_capture_enabled(False)
        if self.logs_widget is not None:
            self.logs_widget.set_capture_enabled(False)
        self.stop_btn.hide()
        self.play_btn.show()

    @pyqtSlot()
    def on_reset(self) -> None:
        self.msg_table.clear()
        if self.logs_widget:
            self.logs_widget.logs_table.clear()

    def showEvent(self, a0: QShowEvent | None) -> None:
        self.msg_table.set_updates_active(True)
        if self._use_recorder_feed and self.is_playing:
            self._poll_recorder_messages()
            self._feed_timer.start()
        super().showEvent(a0)

    def hideEvent(self, a0: QHideEvent | None) -> None:
        self.msg_table.set_updates_active(False)
        if self._use_recorder_feed:
            self._feed_timer.stop()
        super().hideEvent(a0)

    def _poll_recorder_messages(self) -> None:
        cursor, entries = self._recorder().recent_messages_after(self._message_cursor)
        self._message_cursor = cursor
        if entries:
            self.msg_table.add_recent_entries(entries)

    def _recorder(self) -> DebugRecorder:
        recorder = self.bot.debug_recorder
        assert recorder is not None, "The recorder feed requires DEBUG=1"
        return recorder

    @pyqtSlot(QModelIndex)
    def on_click_msg(self, model_index: QModelIndex) -> None:
        source_index = self.msg_table.table.proxy_model.mapToSource(model_index)
        model = self.msg_table.table.item_model
        msg_infos = _require_message_info(
            model.data(model.index(source_index.row(), 3), Qt.ItemDataRole.UserRole)
        )
        self._current_detail_msg_info = msg_infos
        obf_msg_name, non_obf_msg_name = _parse_sub_msg_name(msg_infos.sub_msg_name)
        self.msg_detail.set_content(
            msg_infos.msg_json,
            msg_infos.obf_msg_json,
            find_non_obf_game_message_descriptor(non_obf_msg_name),
            _find_obf_game_message_descriptor(obf_msg_name or msg_infos.sub_msg_name),
        )
        self.detail_stack.setCurrentWidget(self.msg_detail)

    @pyqtSlot(QModelIndex)
    def on_double_click_msg(self, model_index: QModelIndex) -> None:
        source_index = self.msg_table.table.proxy_model.mapToSource(model_index)
        model = self.msg_table.table.item_model
        msg_info = _require_message_info(
            model.data(model.index(source_index.row(), 3), Qt.ItemDataRole.UserRole)
        )
        obf_msg_name = _extract_obf_msg_name_for_pinned_pair(msg_info)
        if obf_msg_name is None:
            return

        dialog = PinnedPairMessageBox(obf_msg_name, load_non_obf_game_message_full_names(), self)
        if dialog.exec():
            upsert_pinned_pair(PINNED_PAIRS_FILE, obf_msg_name, dialog.non_obf_msg_name)

    @pyqtSlot()
    def on_close_detail(self) -> None:
        self._current_detail_msg_info = None
        self.detail_stack.setCurrentWidget(self.detail_empty_state)

    @pyqtSlot()
    def on_lock_pinned_fields(self) -> None:
        if self._current_detail_msg_info is None:
            return

        message_pair = _extract_pinned_pair_names_for_fields(self._current_detail_msg_info)
        if message_pair is None:
            QMessageBox.warning(
                self,
                "Pinned fields",
                "Unable to lock fields for this message.",
            )
            return

        fields = self.msg_detail.selected_pinned_fields()
        if fields is None:
            QMessageBox.warning(
                self,
                "Pinned fields",
                "Select a root field in each tree.",
            )
            return

        obf_field, non_obf_field = fields
        resolved_message_pair = _resolve_pinned_field_message_pair(message_pair, obf_field, non_obf_field)
        if resolved_message_pair is None:
            QMessageBox.warning(
                self,
                "Pinned fields",
                "The selected fields do not target a compatible type pair.",
            )
            return

        _upsert_pinned_field_mapping_with_child_pair(
            PINNED_PAIRS_FILE,
            resolved_message_pair,
            obf_field,
            non_obf_field,
        )
        obf_field_name = obf_field.path_label
        non_obf_field_name = non_obf_field.path_label
        QMessageBox.information(
            self,
            "Pinned fields",
            f"{obf_field_name} -> {non_obf_field_name} locked.",
        )

    def _show_listeners_tab(
        self,
        debug_stacked: QStackedWidget,
        listeners_widget: ListenersStatsWidget,
    ) -> None:
        debug_stacked.setCurrentWidget(listeners_widget)
        listeners_widget.init()

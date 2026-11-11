from __future__ import annotations

lazy from src.core.bot.bot import Bot

from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel

from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.data_center.i18n import I18N
from src import consts
from src.controller.player_info_storage import PlayerInfoSnapshot

_UNKNOWN_VALUE = "—"


class AccountQuickInfoWidget(QWidget):
    def __init__(
        self,
        bot: Bot,
        snapshot: PlayerInfoSnapshot | None,
        quarantine_reason: str | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent=parent)
        self.bot = bot

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.setLayout(layout)

        self.subscription_end_column, self.subscription_end_label = self._add_info_column(
            layout, "Subscription end"
        )
        _, self.kamas_label = self._add_info_column(layout, "Kamas")
        _, self.level_label = self._add_info_column(layout, "Niveau")
        _, self.sub_area_label = self._add_info_column(layout, "Sous-zone actuelle")

        self.bot.game_info_signals.subscription_end_date.connect(self._set_subscription_end_date)
        self.bot.inventory_signals.kamas.connect(self._set_kamas)
        self.bot.game_info_signals.level.connect(self._set_level)
        self.bot.grid_signals.new_map_id.connect(self._set_sub_area)
        self.bot.game_info_signals.is_ready_to_play.connect(self._sync_from_state)

        self._set_subscription_end_date(self.bot.game_state.player.subscription_end_date)
        if self.bot.is_ready_to_play_event.is_set():
            self._sync_game_values_from_state()
        else:
            self._sync_game_values_from_snapshot(snapshot)

    @staticmethod
    def _add_info_column(layout: QHBoxLayout, title: str) -> tuple[QWidget, BodyLabel]:
        column_widget = QWidget()
        column_layout = QVBoxLayout()
        column_layout.setContentsMargins(0, 2, 0, 2)
        column_layout.setSpacing(0)
        column_widget.setLayout(column_layout)

        title_label = CaptionLabel(text=title, parent=column_widget)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column_layout.addWidget(title_label)

        value_label = BodyLabel(text=_UNKNOWN_VALUE, parent=column_widget)
        value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value_label.setWordWrap(True)
        value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column_layout.addWidget(value_label)

        layout.addWidget(column_widget, 1)
        return column_widget, value_label

    def _sync_from_state(self) -> None:
        self._set_subscription_end_date(self.bot.game_state.player.subscription_end_date)
        self._sync_game_values_from_state()

    def _sync_game_values_from_state(self) -> None:
        self._set_kamas(self.bot.game_state.inventory.kamas)
        self._set_level(self.bot.game_state.player.level)
        self._set_sub_area(self.bot.game_state.map.map_id)

    def _sync_game_values_from_snapshot(self, snapshot: PlayerInfoSnapshot | None) -> None:
        if snapshot is None:
            return
        self._set_kamas(snapshot.kamas)
        self._set_level(snapshot.level)

    def set_snapshot_sub_area_name(self, sub_area_name: str | None) -> None:
        if not self.bot.is_ready_to_play_event.is_set():
            self.sub_area_label.setText(sub_area_name or _UNKNOWN_VALUE)

    def _set_subscription_end_date(self, subscription_end_date: datetime) -> None:
        is_subscribed = subscription_end_date != consts.MIN_DATE and datetime.now(
            tz=subscription_end_date.tzinfo
        ) < subscription_end_date
        self.subscription_end_column.setVisible(is_subscribed)
        if not is_subscribed:
            return
        self.subscription_end_label.setText(subscription_end_date.strftime("%d/%m/%Y %H:%M"))

    def _set_kamas(self, kamas: int) -> None:
        self.kamas_label.setText(f"{kamas:,}".replace(",", " "))

    def _set_level(self, level: int) -> None:
        self.level_label.setText(str(level))

    def _set_sub_area(self, map_id: int) -> None:
        if map_id == 0:
            self.sub_area_label.setText(_UNKNOWN_VALUE)
            return
        map_position = DataReader().map_info_by_map_id[map_id]
        sub_area = DataReader().sub_area_by_id[map_position.subAreaId]
        self.sub_area_label.setText(I18N().name_by_id[sub_area.nameId])

    @staticmethod
    def resolve_snapshot_sub_area_names(
        snapshots: dict[str, PlayerInfoSnapshot],
    ) -> dict[str, str]:
        reader = DataReader()
        names = I18N().name_by_id
        return {
            login: names[reader.sub_area_by_id[reader.map_info_by_map_id[snapshot.map_id].subAreaId].nameId]
            for login, snapshot in snapshots.items()
            if snapshot.map_id != 0
        }

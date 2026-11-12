from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import cast

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import (
    ComboBox,
    FluentIcon,
    SegmentedWidget,
    TransparentToolButton,
)

from src.core import config
from src.core.behaviors.behavior_factory import USABLE_BEHAVIORS
from src.services.display_catalog import load_display_catalog
lazy from src.controller.player_info_storage import PlayerInfoSnapshot
lazy from src.core.bot.bot import Bot
lazy from src.gui.pages.farmer.bank_tab import BankTab
lazy from src.gui.pages.farmer.inventory_tab import InventoryTab
lazy from src.gui.pages.farmer.map_tab import MapTab
lazy from src.gui.pages.farmer.player_tab import PlayerTab
lazy from src.gui.pages.farmer.world_tab import WorldTab


class FarmActionEnum(StrEnum):
    AUTO = "Automatic"
    HARVESTER = "Harvesting"
    FIGHTER = "Combat"


class CraftActionEnum(StrEnum):
    CRAFTER = "Craft"


class FarmerWidget(QWidget):
    play_btn: TransparentToolButton
    stop_btn: TransparentToolButton
    type_action_combo: ComboBox
    area_farm_combo: ComboBox
    sub_area_farm_combo: ComboBox

    def __init__(
        self,
        login: str,
        bot: Bot,
        parent: QWidget | None = None,
        snapshot: PlayerInfoSnapshot | None = None,
    ) -> None:
        super().__init__(parent)
        self.login = login
        self.bot = bot
        self._snapshot = snapshot
        self._v_layout = QVBoxLayout()
        self._v_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setLayout(self._v_layout)

        self._storage_tab: InventoryTab | BankTab | None = None

        self._catalog = load_display_catalog()
        self.init_top_content()

        if config.DEBUG:
            self.init_content()
            self._create_debug_tabs()

        self.bot.bot_signals.play_harvester.connect(self.on_play_harvester)
        self.bot.bot_signals.play_crafter.connect(self.on_play_craft)
        self.bot.bot_signals.play_auto_bot.connect(self.on_play_auto)
        self.bot.bot_signals.play_fighter.connect(self.on_play_fighter)

    def init_top_content(self) -> None:
        top_widget = QWidget(self)
        top_widget_layout = QHBoxLayout()
        top_widget.setLayout(top_widget_layout)

        self.play_btn = TransparentToolButton(FluentIcon.PLAY, top_widget)
        self.play_btn.clicked.connect(self.on_click_play)
        self.bot.bot_signals.play.connect(self.on_play)
        top_widget_layout.addWidget(self.play_btn)

        self.stop_btn = TransparentToolButton(FluentIcon.PAUSE, top_widget)
        self.stop_btn.clicked.connect(self.on_click_stop)
        self.bot.bot_signals.stop.connect(self.on_stop)
        top_widget_layout.addWidget(self.stop_btn)
        self.stop_btn.hide()

        self.type_action_combo = ComboBox(top_widget)
        for farm_action in FarmActionEnum:
            self.type_action_combo.addItem(farm_action)

        for usable_behavior in USABLE_BEHAVIORS:
            self.type_action_combo.addItem(usable_behavior.__name__)

        self.type_action_combo.setCurrentText(FarmActionEnum.AUTO)
        self.type_action_combo.currentIndexChanged.connect(self.on_type_action_changed)
        top_widget_layout.addWidget(self.type_action_combo)

        self.sub_area_farm_combo = ComboBox(top_widget)

        self.area_farm_combo = ComboBox(top_widget)
        self.area_farm_combo.addItem("")
        for area in self._catalog.areas:
            self.area_farm_combo.addItem(area.name, userData=area.id)
        self.area_farm_combo.currentIndexChanged.connect(self.on_area_selected)

        top_widget_layout.addWidget(self.area_farm_combo)

        top_widget_layout.addWidget(self.sub_area_farm_combo)

        self._v_layout.addWidget(top_widget)
        self.on_type_action_changed()

    def init_content(self) -> None:
        self.pivot = SegmentedWidget(self)
        self._v_layout.addWidget(self.pivot)
        self.stacked_widget = QStackedWidget(self)
        self._v_layout.addWidget(self.stacked_widget)

    @pyqtSlot()
    def on_type_action_changed(self) -> None:
        current_action = self.type_action_combo.currentText()
        show_zones = current_action in (FarmActionEnum.HARVESTER, FarmActionEnum.FIGHTER)
        self.area_farm_combo.setVisible(show_zones)
        self.sub_area_farm_combo.setVisible(show_zones)
        self.play_btn.setDisabled(current_action == CraftActionEnum.CRAFTER)

    @pyqtSlot()
    def on_area_selected(self) -> None:
        current_area_id = self.area_farm_combo.currentData()
        self.sub_area_farm_combo.clear()
        if current_area_id is None:
            return
        self.sub_area_farm_combo.addItem("")
        for sub_area in self._catalog.sub_areas.get(current_area_id, []):
            self.sub_area_farm_combo.addItem(sub_area.name, userData=sub_area.id)

    @pyqtSlot()
    def on_click_play(self) -> None:
        area_id = self.area_farm_combo.currentData()
        sub_area_id = self.sub_area_farm_combo.currentData()
        self.bot.bot_signals.play.emit(True)
        if self.type_action_combo.currentText() == FarmActionEnum.HARVESTER:
            self.bot.bot_signals.play_harvester.emit(area_id, sub_area_id)
        elif self.type_action_combo.currentText() == FarmActionEnum.FIGHTER:
            self.bot.bot_signals.play_fighter.emit(area_id, sub_area_id)
        elif self.type_action_combo.currentText() == FarmActionEnum.AUTO:
            self.bot.bot_signals.play_auto_bot.emit()
        elif self.type_action_combo.currentText() not in FarmActionEnum:
            self.bot.bot_signals.play_usable_behavior.emit(self.type_action_combo.currentText())

    @pyqtSlot(bool)
    def on_play(self, _: bool) -> None:
        self.stop_btn.show()
        self.play_btn.hide()
        self.type_action_combo.setDisabled(True)
        self.area_farm_combo.setDisabled(True)
        self.sub_area_farm_combo.setDisabled(True)

    @pyqtSlot(object, object)
    def on_play_harvester(self, area_id: int | None, sub_area_id: int | None) -> None:
        self.type_action_combo.setCurrentText(FarmActionEnum.HARVESTER)
        self.on_played_zone(area_id, sub_area_id)

    @pyqtSlot(object, object)
    def on_play_fighter(self, area_id: int | None, sub_area_id: int | None) -> None:
        self.type_action_combo.setCurrentText(FarmActionEnum.FIGHTER)
        self.on_played_zone(area_id, sub_area_id)

    @pyqtSlot()
    def on_play_auto(self) -> None:
        self.type_action_combo.setCurrentText(FarmActionEnum.AUTO)
        self.on_played_zone(None, None)

    @pyqtSlot(object)
    def on_play_craft(self, _: object) -> None:
        if self.type_action_combo.findText(CraftActionEnum.CRAFTER) < 0:
            self.type_action_combo.addItem(CraftActionEnum.CRAFTER)
        self.type_action_combo.setCurrentText(CraftActionEnum.CRAFTER)

    def on_played_zone(self, area_id: int | None, sub_area_id: int | None) -> None:
        if area_id is not None:
            self.area_farm_combo.setCurrentIndex(self.area_farm_combo.findData(area_id))
        else:
            self.area_farm_combo.setCurrentText("")

        if sub_area_id is not None:
            self.sub_area_farm_combo.setCurrentIndex(self.sub_area_farm_combo.findData(sub_area_id))
        else:
            self.sub_area_farm_combo.setCurrentText("")

    @pyqtSlot()
    def on_stop(self) -> None:
        self.play_btn.show()
        self.stop_btn.hide()
        index_crafter = self.type_action_combo.findText(CraftActionEnum.CRAFTER)
        if index_crafter != -1:
            self.type_action_combo.removeItem(index_crafter)
        self.type_action_combo.setDisabled(False)
        self.area_farm_combo.setDisabled(False)
        self.sub_area_farm_combo.setDisabled(False)
        self.on_type_action_changed()

    @pyqtSlot()
    def on_click_stop(self) -> None:
        self.bot.bot_signals.stop.emit()

    def _create_debug_tabs(self) -> None:
        self._debug_factories: dict[str, Callable[[], QWidget]] = {
            "Map": lambda: MapTab(
                self.bot.grid_signals, self.bot.game_info_signals, self.bot.game_state, self.stacked_widget
            ),
            "Joueur": lambda: PlayerTab(self.bot, self.stacked_widget, snapshot=self._snapshot),
            "Monde": lambda: WorldTab(self.bot.world_signals, self.stacked_widget),
            "Inventaire": lambda: InventoryTab(self.bot, self.stacked_widget),
            "Banque": lambda: BankTab(self.bot, self.stacked_widget),
        }
        self._debug_widgets: dict[str, QWidget] = {}
        for label in self._debug_factories:
            self.pivot.addItem(
                routeKey=label,
                text=label,
                onClick=lambda _checked=False, label=label: self._select_debug_tab(label),
            )
            self.pivot.widget(label).setFixedHeight(40)
        self.pivot.setCurrentItem("Map")

    def _select_debug_tab(self, label: str) -> None:
        if self._storage_tab is not None:
            self._storage_tab.disconnect_signals()
            self._storage_tab = None
        if label not in self._debug_widgets:
            widget = self._debug_factories[label]()
            self._debug_widgets[label] = widget
            self.stacked_widget.addWidget(widget)
        widget = self._debug_widgets[label]
        self.stacked_widget.setCurrentWidget(widget)
        if label == "Inventaire":
            self._storage_tab = cast(InventoryTab, widget)
        elif label == "Banque":
            self._storage_tab = cast(BankTab, widget)
        if self._storage_tab is not None:
            self._storage_tab.connect_signals()

    def showEvent(self, a0: QShowEvent | None) -> None:
        super().showEvent(a0)
        if config.DEBUG and not self._debug_widgets:
            self._select_debug_tab("Map")
        activity = self.bot.behavior_coordinator
        labels = {
            "auto": FarmActionEnum.AUTO,
            "harvest": FarmActionEnum.HARVESTER,
            "fight": FarmActionEnum.FIGHTER,
        }
        if activity.action_name == "craft":
            self.on_play_craft(None)
        else:
            self.type_action_combo.setCurrentText(labels.get(activity.action_name, activity.action_name))
            self.on_played_zone(activity.area_id, activity.sub_area_id)
        if self.bot.is_playing_event.is_set():
            self.on_play(False)

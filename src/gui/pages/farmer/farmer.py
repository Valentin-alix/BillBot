from __future__ import annotations

lazy from src.core.bot.bot import Bot

from collections.abc import Callable
from enum import StrEnum

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import (
    ComboBox,
    FluentIcon,
    SegmentedWidget,
    TransparentToolButton,
)

from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.data_center.i18n import I18N
from src.core import config
from src.core.behaviors.behavior_factory import USABLE_BEHAVIORS
from src.gui.pages.farmer.bank_tab import BankTab
from src.gui.pages.farmer.inventory_tab import InventoryTab
from src.gui.pages.farmer.map_tab import MapTab
from src.gui.pages.farmer.player_tab import PlayerTab
from src.gui.pages.farmer.world_tab import WorldTab


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
    ) -> None:
        super().__init__(parent)
        self.login = login
        self.bot = bot
        self._v_layout = QVBoxLayout()
        self._v_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setLayout(self._v_layout)

        self.map_tab: MapTab | None = None
        self.player_tab: PlayerTab | None = None
        self.world_tab: WorldTab | None = None
        self.inventory_tab: InventoryTab | None = None
        self.bank_tab: BankTab | None = None

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
        self.area_farm_combo.currentIndexChanged.connect(self.on_area_selected)

        for area in sorted(
            DataReader().area_by_id.values(),
            key=lambda area: I18N().name_by_id.get(area.nameId, "Unknown"),
        ):
            self.area_farm_combo.addItem(I18N().name_by_id.get(area.nameId, "Unknown"), userData=area.id)

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
        if current_action not in FarmActionEnum or current_action in [
            CraftActionEnum.CRAFTER,
            FarmActionEnum.AUTO,
        ]:
            self.area_farm_combo.setHidden(True)
            self.sub_area_farm_combo.setHidden(True)
        else:
            self.area_farm_combo.setHidden(False)
            self.sub_area_farm_combo.setHidden(False)

        if current_action in [CraftActionEnum.CRAFTER]:
            self.play_btn.setDisabled(True)
        else:
            self.play_btn.setDisabled(False)

    @pyqtSlot()
    def on_area_selected(self) -> None:
        current_area_id = self.area_farm_combo.currentData()
        self.sub_area_farm_combo.clear()
        if current_area_id is None:
            return
        self.sub_area_farm_combo.addItem("")
        for sub_area in sorted(
            DataReader().sub_area_by_id.values(),
            key=lambda subarea: I18N().name_by_id.get(subarea.nameId, ""),
        ):
            if sub_area.areaId != current_area_id:
                continue
            self.sub_area_farm_combo.addItem(I18N().name_by_id[sub_area.nameId], userData=sub_area.id)

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

    @pyqtSlot()
    def on_click_stop(self) -> None:
        self.bot.bot_signals.stop.emit()

    def _add_debug_tab(
        self,
        route_key: str,
        text: str,
        widget: QWidget,
        on_click: Callable[[], None] | None = None,
    ) -> None:
        pivot_item = self.pivot.addItem(
            routeKey=route_key,
            text=text,
            onClick=on_click or (lambda: self._show_debug_widget(widget)),
        )
        if pivot_item is None:
            raise ValueError(f"Debug tab route `{route_key}` is already registered")

    def _create_debug_tabs(self) -> None:
        self.map_tab = MapTab(
            grid_signals=self.bot.grid_signals,
            game_info_signals=self.bot.game_info_signals,
            game_state=self.bot.game_state,
            parent=self.stacked_widget,
        )
        self.stacked_widget.addWidget(self.map_tab)
        self.map_route = f"{self.objectName()}_map_tab"
        self._add_debug_tab(self.map_route, "Map", self.map_tab)

        self.player_tab = PlayerTab(bot=self.bot, parent=self.stacked_widget)
        self.stacked_widget.addWidget(self.player_tab)
        player_route = f"{self.objectName()}_player_tab"
        self._add_debug_tab(player_route, "Joueur", self.player_tab)

        self.world_tab = WorldTab(world_signals=self.bot.world_signals, parent=self.stacked_widget)
        self.stacked_widget.addWidget(self.world_tab)
        world_route = f"{self.objectName()}_world_tab"
        self._add_debug_tab(world_route, "Monde", self.world_tab)

        self.inventory_tab = InventoryTab(self.bot, parent=self.stacked_widget)
        self.stacked_widget.addWidget(self.inventory_tab)
        inventory_route = f"{self.objectName()}_inventory_tab"
        self._add_debug_tab(
            inventory_route,
            "Inventaire",
            self.inventory_tab,
            self._show_inventory_tab,
        )

        self.bank_tab = BankTab(self.bot, parent=self.stacked_widget)
        self.stacked_widget.addWidget(self.bank_tab)
        bank_route = f"{self.objectName()}_bank_tab"
        self._add_debug_tab(bank_route, "Banque", self.bank_tab, self._show_bank_tab)

        for pivot_item in self.pivot.items.values():
            pivot_item.setFixedHeight(40)
        self.pivot.setCurrentItem(self.map_route)
        self.stacked_widget.setCurrentWidget(self.map_tab)

    def _show_debug_widget(self, widget: QWidget) -> None:
        self._disconnect_hidden_storage_tabs()
        self.stacked_widget.setCurrentWidget(widget)

    def _show_inventory_tab(self) -> None:
        assert self.inventory_tab is not None
        assert self.bank_tab is not None
        self.bank_tab.disconnect_signals()
        self.stacked_widget.setCurrentWidget(self.inventory_tab)
        self.inventory_tab.connect_signals()

    def _show_bank_tab(self) -> None:
        assert self.inventory_tab is not None
        assert self.bank_tab is not None
        self.inventory_tab.disconnect_signals()
        self.stacked_widget.setCurrentWidget(self.bank_tab)
        self.bank_tab.connect_signals()

    def _disconnect_hidden_storage_tabs(self) -> None:
        if self.inventory_tab is not None:
            self.inventory_tab.disconnect_signals()
        if self.bank_tab is not None:
            self.bank_tab.disconnect_signals()

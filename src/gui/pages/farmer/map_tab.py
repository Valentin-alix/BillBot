from functools import partial

from PyQt6.QtCore import pyqtSlot
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import SingleDirectionScrollArea, SmoothMode

from src.core.signals.grid_signals import GridSignals
from src.core.signals.player_signals import GameInfoSignals
from src.core.states.game_state import GameState
from src.gui.components.graphics.grid_widget import GridView
from src.gui.components.qfluent_widget.scrollable_message_box import (
    ScrollableMessageBox,
)
from src.gui.pages.farmer.player_info.property_panel_widget import PropertyPanelWidget
from src.services.display_catalog import load_display_catalog


class MapTab(QWidget):
    def __init__(
        self,
        grid_signals: GridSignals,
        game_info_signals: GameInfoSignals,
        game_state: GameState,
        parent: QWidget | None = None,
    ):
        super().__init__(parent=parent)
        self.grid_signals = grid_signals
        self.game_info_signals = game_info_signals
        self.game_state = game_state
        self._catalog = load_display_catalog()

        layout = QVBoxLayout()
        self.setLayout(layout)
        scroll_area_info = SingleDirectionScrollArea(self)

        content_widget = QWidget(scroll_area_info)
        content_widget_layout = QHBoxLayout()
        content_widget.setLayout(content_widget_layout)

        grid_view = GridView(grid_signals)
        content_widget_layout.addWidget(grid_view, 1)

        self.info_panel = PropertyPanelWidget(vertical=True, parent=content_widget)
        self.info_panel.setMinimumWidth(160)
        self.info_panel.setMaximumWidth(220)
        content_widget_layout.addWidget(self.info_panel, 0)
        self._connect_info_signals()

        scroll_area_info.setSmoothMode(SmoothMode.NO_SMOOTH)
        scroll_area_info.setWidgetResizable(True)
        scroll_area_info.setWidget(content_widget)
        scroll_area_info.enableTransparentBackground()

        layout.addWidget(scroll_area_info)

        self.grid_signals.cell_id_clicked.connect(self.on_cell_id_clicked)

        if self.game_state.map.map_id != 0:
            self.on_map_id_changed(self.game_state.map.map_id)

    def _connect_info_signals(self) -> None:
        self.grid_signals.new_map_id.connect(self.on_map_id_changed)
        self.grid_signals.is_in_map_transition.connect(
            partial(self.info_panel.on_received_property, "", "Map transition in progress")
        )
        self.game_info_signals.is_in_haven_bag.connect(
            partial(self.info_panel.on_received_property, "", "Inside the Haven Bag")
        )

        self.combat_group = self.info_panel.pre_create_group("Combat")
        self.combat_group.hide()
        self.game_info_signals.in_fight.connect(self.on_in_fight_changed)
        self.game_info_signals.is_our_turn.connect(
            partial(self.info_panel.on_received_property, "Combat", "Our turn")
        )
        self.game_info_signals.life_point.connect(
            partial(self.info_panel.on_received_property, "Combat", "Health")
        )
        self.game_info_signals.max_life_point.connect(
            partial(self.info_panel.on_received_property, "Combat", "Maximum health")
        )
        self.game_info_signals.action_points.connect(
            partial(self.info_panel.on_received_property, "Combat", "AP")
        )
        self.game_info_signals.movement_points.connect(
            partial(self.info_panel.on_received_property, "Combat", "MP")
        )

    @pyqtSlot(bool)
    def on_in_fight_changed(self, in_fight: bool) -> None:
        self.combat_group.setVisible(in_fight)

    @pyqtSlot(int)
    def on_map_id_changed(self, map_id: int) -> None:
        self.info_panel.on_received_property("", "Map id", map_id)
        position = self._catalog.map_positions[map_id]
        self.info_panel.on_received_property("", "Coordinates", str(position))

    @pyqtSlot(int)
    def on_cell_id_clicked(self, cell_id: int) -> None:
        actor = self.game_state.entity.get_first_actor_on_cell_id(cell_id)

        if actor is not None:
            if actor.actor_id in self.game_state.entity.actor_fight_by_id:
                content = str(self.game_state.entity.actor_fight_by_id[actor.actor_id])
            else:
                content = str(actor)
            ScrollableMessageBox(f"Actor on cell {cell_id}", content, self).exec()
            return

        stated_elements = self.game_state.interactive.stated_element_by_cell_id.get(cell_id)
        if stated_elements:
            stated_element, collectable = next(iter(stated_elements.values()))
            content = str(stated_element)
            if collectable is not None:
                content += f"\n\nCollectable: {collectable}"
            ScrollableMessageBox(f"Interactive on cell {cell_id}", content, self).exec()

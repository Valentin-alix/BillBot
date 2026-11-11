from __future__ import annotations

lazy from src.core.bot.bot import Bot

from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import (
    ObjectItemInventory,
)
from DBDofusUnity.dofus_unity_reader.game_constants.inventory_position import (
    CharacterInventoryPositionEnum,
)
from PyQt6.QtCore import QSize, QTimer, pyqtSlot
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QListView,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, CaptionLabel, ListWidget, SmoothMode

from src.gui.consts import CARD_WIDTH
from src.gui.pages.farmer.equipment_panel_widget import (
    EquipmentPanelWidget,
    get_item_name,
)

CARD_HEIGHT = 120
CARD_SPACING = 10
BAG_MAX_COLUMNS = 3

BAG_MAX_WIDTH = BAG_MAX_COLUMNS * (CARD_WIDTH + CARD_SPACING) + CARD_SPACING + 20

_NOT_EQUIPED = int(CharacterInventoryPositionEnum.InventoryPositionNotEquiped)


class InventoryTab(QWidget):
    def __init__(self, bot: Bot, parent: QWidget | None = None):
        super().__init__(parent=parent)
        self.bot = bot
        self.items_by_uid: dict[int, ObjectItemInventory] = {}
        self.signals_connected = False
        self.inventory_weight: int = 0
        self.weight_max: int = 0
        self._render_dirty = False
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setInterval(50)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.timeout.connect(self._rebuild_sorted_list)

        self.equipment_panel = EquipmentPanelWidget(self)

        self.list_widget = ListWidget(self)
        self.list_widget.scrollDelegate.verticalSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)
        self.list_widget.setViewMode(QListView.ViewMode.IconMode)
        self.list_widget.setResizeMode(QListView.ResizeMode.Adjust)
        self.list_widget.setMovement(QListView.Movement.Static)
        self.list_widget.setSpacing(CARD_SPACING)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setGridSize(QSize(CARD_WIDTH, CARD_HEIGHT))

        self.list_widget.setMaximumWidth(BAG_MAX_WIDTH)
        self.list_widget.setStyleSheet("QListWidget { background-color: transparent; border: none; }")

        self.weight_label = BodyLabel(text="Poids : 0/0", parent=self)
        self.loading_label = CaptionLabel(text="Loading inventory…", parent=self)
        self.loading_label.hide()
        bottom_layout = QHBoxLayout()
        bottom_layout.addWidget(self.weight_label)
        bottom_layout.addWidget(self.loading_label)
        bottom_layout.addStretch(1)

        content_layout = QHBoxLayout()
        content_layout.addWidget(self.equipment_panel, 1)
        content_layout.addWidget(self.list_widget, 1)

        layout = QVBoxLayout()
        self.setLayout(layout)
        layout.addLayout(content_layout, 1)
        layout.addLayout(bottom_layout, 0)

    @pyqtSlot(ObjectItemInventory)
    def on_added_object_item(self, object_item: ObjectItemInventory):
        self.items_by_uid[object_item.item.uid] = object_item
        self._schedule_rebuild()

    @pyqtSlot(list)
    def on_added_object_items_batch(self, objects: list[ObjectItemInventory]):
        for object_item in objects:
            self.items_by_uid[object_item.item.uid] = object_item
        self._schedule_rebuild()

    @pyqtSlot(int)
    def on_deleted_object_item_uid(self, uid: int):
        if uid not in self.items_by_uid:
            return
        del self.items_by_uid[uid]
        self._schedule_rebuild()

    @pyqtSlot(ObjectItemInventory)
    def on_updated_object_item(self, object_item: ObjectItemInventory):
        if object_item.item.uid not in self.items_by_uid:
            return
        self.items_by_uid[object_item.item.uid] = object_item
        self._schedule_rebuild()

    @pyqtSlot()
    def on_clear_inventory(self):
        self.items_by_uid.clear()
        self._rebuild_timer.stop()
        if self.isVisible():
            self.list_widget.clear()
            self.equipment_panel.set_items({})
        else:
            self._render_dirty = True

    @pyqtSlot(int)
    def on_inventory_weight_updated(self, inventory_weight: int):
        self.inventory_weight = inventory_weight
        self._refresh_weight_label()

    @pyqtSlot(int)
    def on_weight_max_updated(self, weight_max: int):
        self.weight_max = weight_max
        self._refresh_weight_label()

    def _refresh_weight_label(self) -> None:
        self.weight_label.setText(f"Poids : {self.inventory_weight}/{self.weight_max}")

    def _schedule_rebuild(self) -> None:
        self._render_dirty = True
        if self.isVisible() and not self._rebuild_timer.isActive():
            self.loading_label.show()
            self._rebuild_timer.start()

    def _rebuild_sorted_list(self) -> None:
        equipped_by_position: dict[int, ObjectItemInventory] = {}
        bag_items: list[ObjectItemInventory] = []
        for object_item in self.items_by_uid.values():
            if object_item.position == _NOT_EQUIPED:
                bag_items.append(object_item)
            else:
                equipped_by_position[object_item.position] = object_item

        self.equipment_panel.set_items(equipped_by_position)

        self.list_widget.setUpdatesEnabled(False)
        self.list_widget.clear()
        sorted_items = sorted(bag_items, key=lambda obj: obj.item.quantity, reverse=True)
        for object_item in sorted_items:
            list_item = QListWidgetItem()
            list_item.setSizeHint(QSize(CARD_WIDTH, CARD_HEIGHT))
            list_item.setText(self._get_item_text(object_item))
            self.list_widget.addItem(list_item)
        self.list_widget.setUpdatesEnabled(True)
        self._render_dirty = False
        self.loading_label.hide()

    def _get_item_text(self, object_item: ObjectItemInventory) -> str:
        item_name = get_item_name(object_item)
        return f"{item_name} \n\n {object_item.item.quantity}"

    def connect_signals(self) -> None:
        if self.signals_connected:
            return
        self.bot.inventory_signals.added_object_item.connect(self.on_added_object_item)
        self.bot.inventory_signals.added_object_items_batch.connect(self.on_added_object_items_batch)
        self.bot.inventory_signals.updated_object_item.connect(self.on_updated_object_item)
        self.bot.inventory_signals.deleted_object_item_uid.connect(self.on_deleted_object_item_uid)
        self.bot.inventory_signals.clear_inventory.connect(self.on_clear_inventory)
        self.bot.inventory_signals.inventory_weight.connect(self.on_inventory_weight_updated)
        self.bot.inventory_signals.weight_max.connect(self.on_weight_max_updated)
        self.signals_connected = True
        self._resync_inventory()

    def disconnect_signals(self) -> None:
        if not self.signals_connected:
            return
        self.bot.inventory_signals.added_object_item.disconnect(self.on_added_object_item)
        self.bot.inventory_signals.added_object_items_batch.disconnect(self.on_added_object_items_batch)
        self.bot.inventory_signals.updated_object_item.disconnect(self.on_updated_object_item)
        self.bot.inventory_signals.deleted_object_item_uid.disconnect(self.on_deleted_object_item_uid)
        self.bot.inventory_signals.clear_inventory.disconnect(self.on_clear_inventory)
        self.bot.inventory_signals.inventory_weight.disconnect(self.on_inventory_weight_updated)
        self.bot.inventory_signals.weight_max.disconnect(self.on_weight_max_updated)
        self.signals_connected = False
        self.items_by_uid.clear()
        self._rebuild_timer.stop()
        self.list_widget.clear()
        self.equipment_panel.set_items({})
        self.loading_label.hide()

    def _resync_inventory(self) -> None:
        self.items_by_uid.clear()
        self.list_widget.clear()
        inventory_items = list(self.bot.game_state.inventory.objects_by_uid.values())
        if inventory_items:
            self.on_added_object_items_batch(inventory_items)

    def showEvent(self, a0: QShowEvent | None) -> None:
        if self._render_dirty:
            self._rebuild_sorted_list()
        super().showEvent(a0)

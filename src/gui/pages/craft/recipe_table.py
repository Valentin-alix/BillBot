from collections.abc import KeysView

from PyQt6 import QtWidgets
from PyQt6.QtCore import QModelIndex, QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QStandardItem
from PyQt6.QtWidgets import QHeaderView, QWidget

from DBDofusUnity.dofus_unity_reader.models.datas.recipe_root import RecipeItem
from src.core.engine.crafts.recipes import get_benefice_on_craft_recipe
from src.gui.components.table.column_info import ColumnInfo
from src.gui.components.table.table import BaseTableWidget
from src.services.display_catalog import load_display_catalog


def _require_recipe_item(value: object) -> RecipeItem:
    if not isinstance(value, RecipeItem):
        raise TypeError("Expected a RecipeItem payload in the recipe table model")
    return value


class RecipeTableSignals(QObject):
    removed_recipe = pyqtSignal(RecipeItem)


class RecipeTable(BaseTableWidget):
    def __init__(self, recipes: list[RecipeItem], parent: QWidget | None = None) -> None:
        super().__init__(parent=parent)
        columns: list[ColumnInfo] = [
            ColumnInfo(name="Name"),
            ColumnInfo(name="Profession"),
            ColumnInfo(name="Niv."),
            ColumnInfo(name="Profit"),
            ColumnInfo(name="", filter_info=None),
        ]
        self.table.set_columns(columns)
        header = self.table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 36)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)

        self.signals = RecipeTableSignals(parent=self)
        self.widget_item_by_recipe: dict[RecipeItem, QStandardItem] = {}
        for recipe in recipes:
            self.add_recipe(recipe)

        self.table.clicked.connect(self.on_click_recipe)

    @property
    def recipes(self) -> KeysView[RecipeItem]:
        return self.widget_item_by_recipe.keys()

    def add_recipe(self, recipe: RecipeItem) -> None:
        entry = load_display_catalog().recipe_by_id[recipe.resultId]
        recipe_widget_item = QStandardItem(entry.name)
        recipe_widget_item.setData(recipe, role=Qt.ItemDataRole.UserRole)
        self.widget_item_by_recipe[recipe] = recipe_widget_item

        job_name_widget = QStandardItem(entry.job_name)

        recipe_lvl = QStandardItem(str(entry.level))

        profit_in_kamas = get_benefice_on_craft_recipe(recipe)[0]
        benefice = QStandardItem(f"{profit_in_kamas:,.0f}".replace(",", " ") + " kamas")
        remove_recipe_item = QStandardItem("×")
        remove_recipe_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        self.table.item_model.append_row(
            [recipe_widget_item, job_name_widget, recipe_lvl, benefice, remove_recipe_item]
        )

    @pyqtSlot(QModelIndex)
    def on_click_recipe(self, model_index: QModelIndex) -> None:
        if model_index.column() != 4:
            return
        source_index = self.table.proxy_model.mapToSource(model_index)
        model = self.table.item_model
        recipe = _require_recipe_item(
            model.data(model.index(source_index.row(), 0), Qt.ItemDataRole.UserRole)
        )
        model.remove_rows(source_index.row(), 1)
        self.on_remove_recipe(recipe)

    def on_remove_recipe(self, recipe: RecipeItem) -> None:
        self.signals.removed_recipe.emit(recipe)
        self.widget_item_by_recipe.pop(recipe)

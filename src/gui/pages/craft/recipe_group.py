from functools import cache

from DBDofusUnity.dofus_unity_reader.models.datas.recipe_root import RecipeItem
from PyQt6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtWidgets import QListView, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, LineEdit

from src.services.display_catalog import load_display_catalog


class RecipeGroupSignals(QObject):
    clicked_elem_queue = pyqtSignal(object)


class RecipeCatalogModel(QAbstractListModel):
    def __init__(self) -> None:
        super().__init__()
        self.entries = list(load_display_catalog().recipe_by_id.values())
        self.normalized_names = [entry.name.casefold() for entry in self.entries]

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return len(self.entries)

    def data(self, index: QModelIndex, role: int | None = None) -> object | None:
        if not index.isValid():
            return None
        if role in (None, Qt.ItemDataRole.DisplayRole):
            return self.entries[index.row()].name
        if role == Qt.ItemDataRole.UserRole:
            return self.entries[index.row()].recipe
        return None


@cache
def recipe_catalog_model() -> RecipeCatalogModel:
    return RecipeCatalogModel()


class RecipeFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.catalog = recipe_catalog_model()
        self.query = ""
        self.excluded_recipes: set[RecipeItem] = set()
        self.setSourceModel(self.catalog)

    def set_query(self, query: str) -> None:
        self.query = query.casefold()
        self.invalidateFilter()

    def set_recipe_excluded(self, recipe: RecipeItem, excluded: bool) -> None:
        if excluded:
            self.excluded_recipes.add(recipe)
        else:
            self.excluded_recipes.discard(recipe)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if len(self.query) <= 2:
            return False
        return (
            self.catalog.entries[source_row].recipe not in self.excluded_recipes
            and self.query in self.catalog.normalized_names[source_row]
        )


class RecipeGroup(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.signals = RecipeGroupSignals(parent=self)
        self.proxy_model = RecipeFilterProxyModel(self)

        layout = QVBoxLayout(self)
        self.recipe_results = QStackedWidget(self)
        self.list_view = QListView(self)
        self.list_view.setUniformItemSizes(True)
        self.list_view.setModel(self.proxy_model)
        self.list_view.clicked.connect(self._on_clicked)
        self.recipe_results.addWidget(self.list_view)

        empty_state = QWidget(self.recipe_results)
        empty_state_layout = QVBoxLayout(empty_state)
        empty_state_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state_title = BodyLabel("Search for a recipe to craft", empty_state)
        self.empty_state_description = CaptionLabel(
            "Available for: Lumberjack, Miner, Alchemist, Farmer, Fisherman, and Hunter.",
            empty_state,
        )
        empty_state_layout.addWidget(self.empty_state_title, alignment=Qt.AlignmentFlag.AlignCenter)
        empty_state_layout.addWidget(self.empty_state_description, alignment=Qt.AlignmentFlag.AlignCenter)
        self.recipe_results.addWidget(empty_state)
        layout.addWidget(self.recipe_results)

        self.search_edit = LineEdit(self)
        self.search_edit.setPlaceholderText("Search for a craftable recipe")
        self.search_edit.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search_edit)
        self._update_result_view()

    def add_item(self, recipe: RecipeItem) -> None:
        self.proxy_model.set_recipe_excluded(recipe, False)
        self._update_result_view()

    def remove_item(self, recipe: RecipeItem) -> None:
        self.proxy_model.set_recipe_excluded(recipe, True)
        self._update_result_view()

    def _on_search_changed(self, query: str) -> None:
        self.proxy_model.set_query(query)
        self._update_result_view()

    def _update_result_view(self) -> None:
        query = self.search_edit.text()
        if len(query) < 3:
            self.empty_state_title.setText("Search for a recipe to craft")
            self.empty_state_description.setText(
                "Available for: Lumberjack, Miner, Alchemist, Farmer, Fisherman, and Hunter. "
                "Enter at least 3 characters."
            )
            self.recipe_results.setCurrentIndex(1)
        elif self.proxy_model.rowCount() == 0:
            self.empty_state_title.setText("No recipe found")
            self.empty_state_description.setText("Try another name from the supported professions.")
            self.recipe_results.setCurrentIndex(1)
        else:
            self.recipe_results.setCurrentWidget(self.list_view)

    def _on_clicked(self, index: QModelIndex) -> None:
        recipe = self.proxy_model.data(index, Qt.ItemDataRole.UserRole)
        if not isinstance(recipe, RecipeItem):
            raise TypeError("Expected a RecipeItem payload in the recipe catalog")
        self.signals.clicked_elem_queue.emit(recipe)

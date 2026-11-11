from __future__ import annotations

lazy from src.core.bot.bot import Bot

from DBDofusUnity.dofus_unity_reader.models.datas.recipe_root import RecipeItem
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, TransparentToolButton

from src.gui.pages.craft.recipe_group import RecipeGroup
from src.gui.pages.craft.recipe_table import RecipeTable


class CraftPage(QWidget):
    def __init__(self, bot: Bot, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent)

        self.bot = bot
        self.main_layout = QVBoxLayout()
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignCenter)
        self.setLayout(self.main_layout)

        self.play_btn = TransparentToolButton(FluentIcon.PLAY, self)
        self.bot.bot_signals.play.connect(self.on_play)
        self.play_btn.clicked.connect(self.on_click_play)
        self.main_layout.addWidget(self.play_btn)

        self.stop_btn = TransparentToolButton(FluentIcon.PAUSE, self)
        self.bot.bot_signals.stop.connect(self.on_stop)
        self.stop_btn.clicked.connect(self.on_click_stop)
        self.main_layout.addWidget(self.stop_btn)
        self.stop_btn.hide()

        self.craft_table = RecipeTable(recipes=[], parent=self)
        self.craft_group = RecipeGroup(parent=self)

        self.craft_group.signals.clicked_elem_queue.connect(self.on_added_recipe_queue)
        self.craft_table.signals.removed_recipe.connect(self.on_removed_recipe_queue)

        self.main_layout.addWidget(self.craft_table)
        self.main_layout.addWidget(self.craft_group)
        if self.bot.is_playing_event.is_set():
            self.on_play(False)

    @pyqtSlot(object)
    def on_removed_recipe_queue(self, recipe: RecipeItem):
        self.craft_group.add_item(recipe)

    @pyqtSlot(object)
    def on_added_recipe_queue(self, recipe: RecipeItem):
        self.craft_group.remove_item(recipe)
        self.craft_table.add_recipe(recipe)
        self.bot.logger.info(f"Added recipe for result {recipe.resultId}")

    @pyqtSlot(bool)
    def on_play(self, _: bool) -> None:
        self.stop_btn.show()
        self.play_btn.hide()

    @pyqtSlot()
    def on_click_play(self):
        recipes = self.craft_table.recipes
        self.bot.bot_signals.play.emit(True)
        self.bot.bot_signals.play_crafter.emit(recipes)

    @pyqtSlot()
    def on_click_stop(self):
        self.bot.bot_signals.stop.emit()

    @pyqtSlot()
    def on_stop(self):
        self.play_btn.show()
        self.stop_btn.hide()

from __future__ import annotations

lazy from src.core.bot.bot import Bot
lazy from src.controller.player_info_storage import PlayerInfoSnapshot

from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import SingleDirectionScrollArea, SmoothMode

from src.gui.pages.farmer.player_info.player_info_widget import PlayerInfoWidget


class PlayerTab(QWidget):
    def __init__(
        self, bot: Bot, parent: QWidget | None = None, *, snapshot: PlayerInfoSnapshot | None = None
    ):
        super().__init__(parent=parent)
        layout = QVBoxLayout()
        self.setLayout(layout)
        scroll_area_info = SingleDirectionScrollArea(self)

        player_info_widget = PlayerInfoWidget(bot, parent=scroll_area_info, snapshot=snapshot)

        scroll_area_info.setSmoothMode(SmoothMode.NO_SMOOTH)
        scroll_area_info.setWidgetResizable(True)
        scroll_area_info.setWidget(player_info_widget)
        scroll_area_info.enableTransparentBackground()

        layout.addWidget(scroll_area_info)

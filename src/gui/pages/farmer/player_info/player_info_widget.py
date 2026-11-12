from __future__ import annotations

from functools import partial

from PyQt6.QtCore import pyqtSlot
from PyQt6.QtWidgets import QWidget

from DBDofusUnity.dofus_unity_reader.game_constants.job import HARVESTER_JOB_IDS, JobEnum
from src.gui.pages.farmer.player_info.property_panel_widget import PropertyPanelWidget
from src.services.display_catalog import load_display_catalog
lazy from src.controller.player_info_storage import PlayerInfoSnapshot
lazy from src.core.bot.bot import Bot

_HARVEST_JOB_IDS = HARVESTER_JOB_IDS - {JobEnum.BASE}


class PlayerInfoWidget(PropertyPanelWidget):
    def __init__(
        self, bot: Bot, parent: QWidget | None = None, *, snapshot: PlayerInfoSnapshot | None = None
    ) -> None:
        super().__init__(parent=parent)

        self.bot = bot
        self._catalog = load_display_catalog()
        self.snapshot = snapshot

        self.bot.game_info_signals.character_id.connect(
            partial(self.on_received_property, "Joueur", "Player id")
        )
        self.bot.game_info_signals.character_name.connect(
            partial(self.on_received_property, "Player", "Name")
        )
        self.bot.game_info_signals.level.connect(partial(self.on_received_property, "Joueur", "Niveau"))
        self.bot.game_info_signals.breed_id.connect(self.on_breed_id_changed)
        self.bot.game_info_signals.server_id.connect(self.on_server_id_changed)
        self.bot.game_info_signals.last_time_updated_prices.connect(
            partial(self.on_received_property, "Marketplace", "Last price update")
        )
        self.bot.game_info_signals.job_level_changed.connect(self.on_job_level_changed)
        self.bot.inventory_signals.kamas.connect(self.on_kamas_changed)

        self._sync_player_properties_from_state()
        self._sync_harvest_job_levels_from_state()

    @pyqtSlot(int)
    def on_server_id_changed(self, server_id: int) -> None:
        server_name = self._catalog.server_names.get(server_id, "—")
        self.on_received_property("Player", "Server", server_name)

    @pyqtSlot(int)
    def on_breed_id_changed(self, breed_id: int) -> None:
        if breed_id == 0:
            return
        breed_name = self._catalog.breed_names.get(breed_id, "—")
        self.on_received_property("Joueur", "Classe", breed_name)

    @pyqtSlot(int)
    def on_kamas_changed(self, kamas: int) -> None:
        self.on_received_property("Joueur", "Kamas", f"{kamas:,}".replace(",", " "))

    @pyqtSlot(int, int)
    def on_job_level_changed(self, job_id: int, job_level: int) -> None:
        if job_id not in _HARVEST_JOB_IDS:
            return
        self.on_received_property(
            "Gathering professions",
            self._get_job_name(JobEnum(job_id)),
            job_level,
        )

    def _sync_harvest_job_levels_from_state(self) -> None:
        player = self.bot.game_state.player
        job_levels_by_id = (
            player.job_levels_by_id
            if player.character_id != 0
            else self.snapshot.job_levels_by_id
            if self.snapshot is not None
            else {}
        )
        for job_id in _HARVEST_JOB_IDS:
            self.on_received_property(
                "Gathering professions",
                self._get_job_name(job_id),
                job_levels_by_id.get(job_id, "—"),
            )

    def _sync_player_properties_from_state(self) -> None:
        player = self.bot.game_state.player
        if player.character_id != 0:
            self._sync_player_properties(
                character_id=player.character_id,
                character_name=player.character_name,
                level=player.level,
                breed_id=self.bot.game_state.fight.breed_id,
                server_id=player.server_id,
                kamas=self.bot.game_state.inventory.kamas,
            )
            return
        if self.snapshot is None:
            return
        self._sync_player_properties(
            character_id=self.snapshot.character_id,
            character_name=self.snapshot.character_name,
            level=self.snapshot.level,
            breed_id=self.snapshot.breed_id,
            server_id=self.snapshot.server_id,
            kamas=self.snapshot.kamas,
        )

    def _sync_player_properties(
        self,
        *,
        character_id: int,
        character_name: str,
        level: int,
        breed_id: int,
        server_id: int,
        kamas: int,
    ) -> None:
        self.on_received_property("Joueur", "Player id", character_id)
        self.on_received_property("Player", "Name", character_name)
        self.on_received_property("Joueur", "Niveau", level)
        self.on_breed_id_changed(breed_id)
        self.on_server_id_changed(server_id)
        self.on_kamas_changed(kamas)

    def _get_job_name(self, job_id: int) -> str:
        return self._catalog.job_names.get(job_id, "—")

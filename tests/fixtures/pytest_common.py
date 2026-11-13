from collections.abc import Callable, Iterator
from pathlib import Path
from threading import Timer
from typing import Any, Protocol, cast
from unittest.mock import MagicMock

import pytest
import requests
from PyQt6.QtCore import QCoreApplication

from ankama_launcher_emulator.interfaces.credentials import StoredApiKey
from ankama_launcher_emulator.interfaces.zaap_files import (
    GameSubscription,
)
from src.consts import MIN_DATE
from src.controller.settings import SettingsService
from src.core.bot.bot import Bot
from src.core.bot.execution.behavior_coordinator import BehaviorCoordinator
from src.core.bot.lifecycle.scheduler import BotScheduler
from src.core.events_manager import listener as event_listener
from src.core.states.guild_chest_storage import GuildChestStorage
from src.services.debug_recorder import DebugRecorder
from tests.fixtures.accounts import make_account, make_runtime_bot
from tests.fixtures.bot_runtime import make_behavior_coordinator, make_bot_scheduler
from tests.fixtures.game_state import GameStateContext, make_game_state_ctx
from tests.fixtures.storage import make_guild_chest_storage


class _PreparedRequest(Protocol):
    url: str | None


@pytest.fixture(scope="session", autouse=True)
def _qt_application() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture
def behavior_coordinator() -> BehaviorCoordinator:
    return make_behavior_coordinator()


@pytest.fixture
def bot_scheduler() -> BotScheduler:
    return make_bot_scheduler()


@pytest.fixture(autouse=True)
def logger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> MagicMock:
    monkeypatch.setattr("src.consts.BOT_DEBUG_LOGS_DIR", tmp_path)
    return MagicMock()


@pytest.fixture(autouse=True)
def _isolate_resource_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("src.controller.settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(SettingsService(), "_settings", None)
    monkeypatch.setattr(
        "ankama_launcher_emulator.controller.paysafecard_pool.PAYSAFECARDS_PATH",
        tmp_path / "paysafecards.txt",
    )
    monkeypatch.setattr(
        "ankama_launcher_emulator.controller.paysafecard_purchase.PAYSAFECARD_PURCHASE_PATH",
        tmp_path / "paysafecard_purchase.local.json",
    )
    monkeypatch.setattr("src.services.user_activity.USER_ACTIVITY_PATH", tmp_path / "user_activity.json")


@pytest.fixture(autouse=True)
def _block_smailpro_network(monkeypatch: pytest.MonkeyPatch) -> None:
    original_send = cast(Callable[..., requests.Response], getattr(requests.Session, "send"))

    def send(session: requests.Session, request: _PreparedRequest, **kwargs: Any) -> requests.Response:
        if request.url is not None and request.url.startswith("https://app.sonjj.com/"):
            raise AssertionError("Tests must mock SmailPro requests; live API calls consume credits")
        return original_send(session, request, **kwargs)

    monkeypatch.setattr(requests.Session, "send", send)


@pytest.fixture(autouse=True)
def _daemonize_event_listener_timers(monkeypatch: pytest.MonkeyPatch) -> None:
    timer_type = event_listener.Timer

    def create_timer(interval: float, function: Callable[[], None]) -> Timer:
        timer = timer_type(interval=interval, function=function)
        timer.daemon = True
        return timer

    monkeypatch.setattr(event_listener, "Timer", create_timer)


@pytest.fixture(autouse=True)
def game_sub_info_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_game_sub_info(login: str) -> GameSubscription:
        return GameSubscription(
            isFreeToPlay=True,
            isFormerSubscriber=False,
            isSubscribed=False,
            totalPlayTime=0,
            endOfSubscribe=MIN_DATE,
            id=1,
        )

    monkeypatch.setattr("src.core.states.player_state.get_game_sub_info_by_login", fake_get_game_sub_info)


@pytest.fixture
def account() -> StoredApiKey:
    return make_account("TestBot", 1)


@pytest.fixture
def game_state_ctx(_shared_game_state_debug_recorder: DebugRecorder) -> GameStateContext:
    return make_game_state_ctx(_shared_game_state_debug_recorder)


@pytest.fixture(scope="session")
def _shared_game_state_debug_recorder(tmp_path_factory: pytest.TempPathFactory) -> Iterator[DebugRecorder]:
    recorder_path = tmp_path_factory.mktemp("game_state_debug") / "gamestatefixture.debug.jsonl"
    recorder = DebugRecorder(file_path=str(recorder_path))
    yield recorder
    recorder.stop()


@pytest.fixture
def runtime_bot(game_sub_info_mock: None) -> Iterator[Bot]:
    bot = make_runtime_bot("TEST", 1)
    yield bot
    bot.watchdog.stop()
    if bot.debug_recorder is not None:
        bot.debug_recorder.stop()


@pytest.fixture
def guild_chest_storage() -> GuildChestStorage:
    return make_guild_chest_storage()

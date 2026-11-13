from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BrokenBarrierError
from unittest.mock import Mock

import pytest
import schedule

from ankama_launcher_emulator.controller.schedule_profile import ScheduleProfileController
from ankama_launcher_emulator.interfaces.schedule_profile import ScheduleProfile, TimeSlot
from src.core.bot.lifecycle.scheduler import BotScheduler


def test_schedule_edit_waits_for_active_session_to_stop(
    bot_scheduler: BotScheduler, monkeypatch: pytest.MonkeyPatch
) -> None:
    rebuild = Mock()
    monkeypatch.setattr(bot_scheduler, "refresh_configuration", rebuild)
    bot_scheduler.is_playing_event.set()
    bot_scheduler.request_configuration_refresh()
    rebuild.assert_not_called()
    bot_scheduler.is_playing_event.clear()
    bot_scheduler.apply_pending_configuration()
    rebuild.assert_called_once()
    bot_scheduler.apply_pending_configuration()
    rebuild.assert_called_once()


def test_concurrent_schedule_refreshes_keep_one_set_of_jobs(
    bot_scheduler: BotScheduler, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = schedule.Scheduler()
    monkeypatch.setattr("src.core.bot.lifecycle.scheduler.schedule", clock)
    profile = ScheduleProfile(
        name_fr="Day", proxy_id="proxy", slots_by_day={"0": [TimeSlot(start="08:00", end="12:00")]}
    )
    reads = Barrier(2)

    def get_profile(_profile_id: str) -> ScheduleProfile:
        # Rendezvous after clearing jobs exposes overlapping rebuilds without the lock.
        try:
            reads.wait(timeout=0.5)
        except BrokenBarrierError:
            pass
        return profile

    monkeypatch.setattr(ScheduleProfileController(), "get_profile", get_profile)
    monkeypatch.setattr(bot_scheduler, "get_bot_config", Mock(return_value=Mock(schedule_profile="day")))
    monkeypatch.setattr(bot_scheduler, "_is_quarantined", lambda: False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(bot_scheduler.request_configuration_refresh) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)

    assert len(clock.jobs) == 3
    bot_scheduler.stop()
    assert clock.jobs == []

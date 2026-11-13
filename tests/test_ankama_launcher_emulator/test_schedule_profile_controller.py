import pytest

from ankama_launcher_emulator.controller.proxy import ProxyController
from ankama_launcher_emulator.controller.schedule_profile import ScheduleProfileController
from ankama_launcher_emulator.interfaces.schedule_profile import ProxyConfig, ScheduleProfile, TimeSlot
from src.utils.runtime_support import RuntimeSetupError


def test_profiles_validate_cross_midnight_overlap_and_missing_proxy() -> None:
    ProxyController().save_config(
        "proxy",
        ProxyConfig(host="localhost", http_port=8080, socks_port=1080, username="", password=""),
        create=True,
    )
    controller = ScheduleProfileController()
    profile = ScheduleProfile(
        name_fr="Night", proxy_id="proxy", slots_by_day={"6": [TimeSlot(start="23:00", end="02:00")]}
    )
    controller.save_profile("night", profile, create=True)
    overlap = profile.model_copy(
        update={"slots_by_day": {**profile.slots_by_day, "0": [TimeSlot(start="01:00", end="03:00")]}}
    )
    with pytest.raises(ValueError, match="overlap"):
        controller.save_profile("night", overlap)
    assert controller.get_profile("night") == profile
    short_slot = profile.model_copy(update={"slots_by_day": {"0": [TimeSlot(start="08:00", end="08:15")]}})
    with pytest.raises(ValueError, match="scheduling margins"):
        controller.save_profile("night", short_slot)
    assert controller.get_profile("night") == profile
    with pytest.raises(RuntimeSetupError):
        controller.save_profile("missing", profile.model_copy(update={"proxy_id": "missing"}), create=True)

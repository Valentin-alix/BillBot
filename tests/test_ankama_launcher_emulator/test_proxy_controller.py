import pytest

from ankama_launcher_emulator.controller.proxy import ProxyController
from ankama_launcher_emulator.interfaces.schedule_profile import ProxyConfig


def test_proxy_edits_preserve_rejection_and_runtime_quotas() -> None:
    controller = ProxyController()
    config = ProxyConfig(host="localhost", http_port=8080, socks_port=1080, username="", password="")
    controller.save_config("proxy", config, create=True)
    controller.record_rejection("proxy")
    controller.update_proxy_runtime("proxy", [1, 2], 99)
    controller.save_config("proxy", config.model_copy(update={"host": "new-host"}))
    proxy = controller.get_proxy("proxy")
    assert proxy.host == "new-host"
    assert proxy.rejected
    assert proxy.operation_timestamps == [1, 2]
    assert proxy.register_cooldown_until == 99
    with pytest.raises(ValueError):
        controller.save_config("proxy", config.model_copy(update={"http_port": 70000}))
    assert controller.get_proxy("proxy") == proxy

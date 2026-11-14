import sys
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from threading import Thread

from dotenv import load_dotenv
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme, setThemeColor
from scapy.layers.inet import IP
from scapy.layers.inet6 import IPv6
from scapy.packet import Packet, Raw
from scapy.sendrecv import sniff

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "AnkamaLauncherEmulator"))
from scripts.mapping.dump import check_updated_mapping_resources
from src.consts import ENV_PATH, FILTER_DOFUS, get_connection_servers_ips
from src.core.signals.log_signals import LogSignals
from src.services.logging_utils.loggers import (
    configure_root_logger,
    init_root_gui_logging,
)
from src.utils.runtime_paths import configure_project_import_paths

configure_project_import_paths()
load_dotenv(ENV_PATH)

from src.core.bot.bot_factory import generate_random_bot
from src.core.signals.message_signals import MessageInfoSignals
from src.gui.consts import BASE_HEIGHT, BASE_WIDTH
from src.gui.pages.debugs.sniffer import SnifferWidget
from src.protocol.protocol import decode_varint_size
from src.protocol.protocol_connection import (
    get_conn_msg,
    get_conn_msg_info,
)
from src.protocol.protocol_game import get_game_msg, get_game_msg_info
from src.utils.internet import get_local_ip


@dataclass
class Sniffer:
    buffers: dict[tuple[str, str], bytes] = field(init=False, default_factory=lambda: defaultdict(bytes))
    msg_info_signals: MessageInfoSignals

    def launch_sniffer(self) -> None:
        print("Starting sniffer")
        sniff(prn=self.on_receive, store=False, filter=FILTER_DOFUS)

    @cached_property
    def local_ip(self) -> str:
        return get_local_ip()

    def on_receive(self, packet: Packet) -> None:
        if Raw not in packet:
            return

        ip_src: str
        ip_dst: str

        if IP in packet:
            ip_src = packet[IP].src
            ip_dst = packet[IP].dst
        elif IPv6 in packet:
            ip_src = packet[IPv6].src
            ip_dst = packet[IPv6].dst
        else:
            return

        from_server = self.local_ip == ip_dst

        tunnel: tuple[str, str] = (ip_src, ip_dst)

        self.buffers[tunnel] += packet[Raw].load

        while True:
            if len(self.buffers[tunnel]) == 0:
                break

            size, pos = decode_varint_size(self.buffers[tunnel])
            if size == 0 or len(self.buffers[tunnel]) < pos + size:
                break

            msg_content_datas = self.buffers[tunnel][pos : pos + size]

            if ip_src in get_connection_servers_ips() or ip_dst in get_connection_servers_ips():
                self.handle_connection_message(msg_content_datas, from_server)
            else:
                self.handle_game_message(msg_content_datas, from_server)

            self.buffers[tunnel] = self.buffers[tunnel][pos + size :]

    def handle_connection_message(self, content: bytes, from_server: bool) -> None:
        if not self.msg_info_signals.capture_enabled:
            return
        _, sub_msg = get_conn_msg(content)
        msg_infos = get_conn_msg_info(sub_msg, from_server)
        self.msg_info_signals.msg_info.emit(msg_infos, False)

    def handle_game_message(self, content: bytes, from_server: bool) -> None:
        if not self.msg_info_signals.capture_enabled:
            return
        try:
            _, clear_sub_msg, obf_sub_msg, uid = get_game_msg(content, True, from_server=from_server)
            msg_infos = get_game_msg_info(clear_sub_msg, obf_sub_msg, uid, from_server)

            self.msg_info_signals.msg_info.emit(msg_infos, False)
        except Exception:
            print(traceback.format_exc())


def main() -> None:
    check_updated_mapping_resources()

    configure_root_logger()
    app = QApplication(sys.argv)
    bot = generate_random_bot()
    bot.is_fake = False
    sniffer = Sniffer(msg_info_signals=bot.msg_info_signals)
    Thread(target=sniffer.launch_sniffer, daemon=True).start()
    global_signals = LogSignals()
    init_root_gui_logging(global_signals)
    sniffer_widget = SnifferWidget(bot, global_signals, use_recorder_feed=False)
    sniffer_widget.resize(BASE_WIDTH, BASE_HEIGHT)
    sniffer_widget.show()
    setTheme(Theme.DARK)
    setThemeColor(Qt.GlobalColor.yellow)
    app.exec()


if __name__ == "__main__":
    main()

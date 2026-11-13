import ctypes

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme, setThemeColor

from src.utils.project_paths import BUNDLE_ROOT


class Application(QApplication):
    TITLE = "AstraBot"

    def __init__(self, argv: list[str]) -> None:
        super().__init__(argv)
        setTheme(Theme.DARK)
        setThemeColor(Qt.GlobalColor.yellow)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(self.TITLE)
        self.setWindowIcon(QIcon(str(BUNDLE_ROOT / "resources/icons/logo.png")))
        self.setApplicationName(self.TITLE)

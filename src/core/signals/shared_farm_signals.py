from PyQt6.QtCore import QObject, pyqtSignal


class SharedSignals(QObject):
    launch_account = pyqtSignal(str)
    closed = pyqtSignal()
    thread_count_update = pyqtSignal(int)
    synchronize_bots = pyqtSignal()
    new_bot_added = pyqtSignal(object)
    bot_removed = pyqtSignal(object)

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from typing import cast
from uuid import uuid4

from PyQt6.QtCore import QMetaObject, QObject, Qt, pyqtBoundSignal, pyqtSignal

from utils.singleton import Singleton

RESERVATION_TTL = timedelta(minutes=10)


class MuleExchangeSignals(QObject):
    preparation_requested = pyqtSignal(str)
    prepared = pyqtSignal(str)
    cancelled = pyqtSignal(str)

    @staticmethod
    def connect_direct(signal: pyqtBoundSignal, callback: Callable[[str], None]) -> None:
        connect = cast(
            Callable[[Callable[[str], None], Qt.ConnectionType], QMetaObject.Connection], signal.connect
        )
        connect(callback, Qt.ConnectionType.DirectConnection)


@dataclass(frozen=True)
class MuleReservation:
    token: str
    mule_login: str
    mule_character_id: int
    map_id: int
    donor_character_id: int
    expires_at: datetime


@dataclass
class _MuleEntry:
    login: str
    server_id: int | None = None
    character_id: int | None = None
    map_id: int | None = None
    accepting_new: bool = False
    reservation: MuleReservation | None = None


class KamasMuleRegistry(metaclass=Singleton):
    def __init__(self) -> None:
        self._lock = RLock()
        self._entries: dict[str, _MuleEntry] = {}
        self.exchange_signals = MuleExchangeSignals()

    def mark_ready(
        self,
        login: str,
        server_id: int,
        character_id: int,
        map_id: int,
    ) -> None:
        with self._lock:
            entry = self._entry(login)
            self._expire_reservation(entry, datetime.now())
            entry.server_id = server_id
            entry.character_id = character_id
            entry.map_id = map_id
            entry.accepting_new = True

    def mark_unavailable(self, login: str) -> None:
        with self._lock:
            entry = self._entry(login)
            entry.accepting_new = False
            entry.reservation = None

    def reserve(
        self,
        server_id: int,
        donor_character_id: int,
        now: datetime | None = None,
    ) -> MuleReservation | None:
        current = now or datetime.now()
        with self._lock:
            candidates: list[_MuleEntry] = []
            for entry in self._entries.values():
                self._expire_reservation(entry, current)
                if (
                    entry.server_id == server_id
                    and entry.character_id is not None
                    and entry.map_id is not None
                    and entry.accepting_new
                    and entry.reservation is None
                ):
                    candidates.append(entry)
            if not candidates:
                return None

            selected = min(candidates, key=lambda entry: entry.login)
            assert selected.character_id is not None
            assert selected.map_id is not None
            reservation = MuleReservation(
                token=uuid4().hex,
                mule_login=selected.login,
                mule_character_id=selected.character_id,
                map_id=selected.map_id,
                donor_character_id=donor_character_id,
                expires_at=current + RESERVATION_TTL,
            )
            selected.reservation = reservation
            return reservation

    def release(self, token: str) -> None:
        with self._lock:
            for entry in self._entries.values():
                if entry.reservation is not None and entry.reservation.token == token:
                    entry.reservation = None
                    break
        self.exchange_signals.cancelled.emit(token)

    def is_reserved_for(self, token: str, mule_login: str) -> bool:
        with self._lock:
            entry = self._entries.get(mule_login)
            if entry is None:
                return False
            self._expire_reservation(entry, datetime.now())
            return entry.reservation is not None and entry.reservation.token == token

    def is_reserved_by(self, mule_login: str, donor_character_id: int) -> bool:
        with self._lock:
            entry = self._entries.get(mule_login)
            if entry is None:
                return False
            self._expire_reservation(entry, datetime.now())
            return (
                entry.reservation is not None and entry.reservation.donor_character_id == donor_character_id
            )

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _entry(self, login: str) -> _MuleEntry:
        return self._entries.setdefault(login, _MuleEntry(login=login))

    @staticmethod
    def _expire_reservation(entry: _MuleEntry, now: datetime) -> None:
        if entry.reservation is not None and now >= entry.reservation.expires_at:
            entry.reservation = None

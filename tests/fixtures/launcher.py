from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from ankama_launcher_emulator.controller import bot_storage, mail_account


class IsolatedLauncherStorageTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        temporary_directory = TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        storage_directory = Path(temporary_directory.name)
        for patcher in (
            patch.object(bot_storage, "BOTS_STORAGE_PATH", storage_directory / "bots.json"),
            patch.object(mail_account, "MAIL_ACCOUNTS_STORAGE_PATH", storage_directory / "mail_accounts.json"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)


class FakeBrowserContext:
    def __init__(self, page: MagicMock) -> None:
        self.new_page = AsyncMock(return_value=page)

    async def __aenter__(self) -> "FakeBrowserContext":
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        return None


class FakeLocator:
    def __init__(self, count: int) -> None:
        self._count = count
        self.fill = AsyncMock()
        self.click = AsyncMock()
        self.wait_for = AsyncMock()

    @property
    def first(self) -> "FakeLocator":
        return self

    async def count(self) -> int:
        return self._count

class FakeMailProvider:
    wait_for_code: AsyncMock

    def __init__(self, code: str | None = None) -> None:
        self.wait_for_code = AsyncMock(return_value=code)

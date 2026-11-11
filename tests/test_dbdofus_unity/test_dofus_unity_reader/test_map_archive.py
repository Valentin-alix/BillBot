from pathlib import Path
from typing import Protocol, cast
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from DBDofusUnity.dofus_unity_reader.data_center.map_reader import MapReader


class CacheClearable(Protocol):
    def cache_clear(self) -> None: ...


def _clear_map_reader_caches() -> None:
    cast(CacheClearable, MapReader().map_by_id).cache_clear()
    cast(CacheClearable, MapReader.get_all_map_bundle_ids).cache_clear()


def test_map_reader_loads_a_map_from_the_compressed_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path = tmp_path / "maps.zip"
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("map/map_42.json", b'{"references":[],"mapData":{"cellsData":[]}}')

    with ZipFile(archive_path) as archive:
        monkeypatch.setitem(MapReader().__dict__, "archive", archive)
        _clear_map_reader_caches()
        try:
            assert MapReader().map_by_id(42).references == []
            assert MapReader.get_all_map_bundle_ids() == {42}
        finally:
            _clear_map_reader_caches()

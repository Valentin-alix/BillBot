from pathlib import Path

import pytest

from DBDofusUnity.dofus_unity_reader import get_datas


def test_runtime_cleanup_removes_obsolete_files_and_preserves_bot_data_and_research_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    standalone = tmp_path / "standalone"
    monkeypatch.setattr(get_datas, "DATA_BUNDLES_ROOT", data)
    monkeypatch.setattr(get_datas, "STANDALONE_BUNDLES_ROOT", standalone)
    required = [
        *(data / name for name in get_datas.REQUIRED_DATA_FILENAMES),
        standalone / "world-graph.json",
    ]
    untouched = [tmp_path / name for name in ["exports/research.json", "i18n.json", "maps.zip", "display_catalog.json"]]
    obsolete = [
        data / "UnusedDataRoot.json",
        data / "old-batch/AreasDataRoot.json",
        data / "unfinished.json.tmp",
        standalone / "UnusedComponent.json",
    ]
    for path in [*required, *untouched, *obsolete]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"preserved contents")

    get_datas._clean_runtime_outputs()
    get_datas._clean_runtime_outputs()

    assert all(path.read_bytes() == b"preserved contents" for path in [*required, *untouched])
    assert all(not path.exists() for path in obsolete)

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import msgspec
import pytest
from PIL import Image

from scripts.data import export_assets


@pytest.fixture
def text_environment(monkeypatch: pytest.MonkeyPatch) -> Mock:
    asset = SimpleNamespace(name="CAB-test")
    obj = Mock()
    obj.type.name = "TextAsset"
    obj.path_id = -42
    obj.assets_file = asset
    payload = b"\x00\xff\x80Dofus"
    obj.parse_as_dict.return_value = {"m_Name": "../../CON", "m_Script": "example"}
    obj.parse_as_object.return_value = SimpleNamespace(m_Script=payload.decode("utf-8", "surrogateescape"))
    environment = SimpleNamespace(assets=[asset], objects=[obj])
    monkeypatch.setattr(export_assets, "load_bundle", Mock(return_value=environment))
    return obj


def test_export_preserves_textasset_bytes_and_indexes_names_without_using_them_as_paths(
    tmp_path: Path, text_environment: Mock
) -> None:
    destination = tmp_path / "export"
    report = export_assets.export_asset_bundle(tmp_path / "source.bundle", destination, game_dir=tmp_path)

    assert report.issues == []
    assert report.objects[0].name == "../../CON"
    assert (destination / "asset_0/TextAsset/-42.bytes").read_bytes() == b"\x00\xff\x80Dofus"
    assert msgspec.json.decode((destination / "index.json").read_bytes(), type=export_assets.BundleExport) == report
    assert all((destination / name).is_relative_to(destination) for name in report.objects[0].files)


def test_unsupported_typetree_preserves_raw_object_and_records_the_error(
    tmp_path: Path, text_environment: Mock
) -> None:
    text_environment.type.name = "Unknown"
    text_environment.parse_as_dict.side_effect = ValueError("Unsupported typetree")
    text_environment.get_raw_data.return_value = b"raw Unity object"

    report = export_assets.export_asset_bundle(tmp_path / "source.bundle", tmp_path, game_dir=tmp_path)

    assert report.issues == [export_assets.ExportIssue("typetree", "Unsupported typetree", -42)]
    assert report.objects[0].files == ["asset_0/Unknown/-42.bin"]
    assert (tmp_path / report.objects[0].files[0]).read_bytes() == b"raw Unity object"


def test_every_run_exports_all_streamingassets_bundles_including_native_files(
    tmp_path: Path, text_environment: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    game = tmp_path / "game"
    streaming = game / "Dofus_Data/StreamingAssets"
    (streaming / "nested").mkdir(parents=True)
    (streaming / "test.bundle").write_bytes(b"Unity bundle")
    (streaming / "nested/other.bundle").write_bytes(b"Another Unity bundle")
    monkeypatch.setattr(export_assets, "OBF_GAME_DIR", game)
    monkeypatch.setattr(export_assets, "BUNDLES_ROOT", tmp_path / "bundles")
    output = tmp_path / "bundles/exports"

    assert export_assets.main() == 0
    media = output / "test.bundle/asset_0/TextAsset/-42.bytes"
    nested_media = output / "nested/other.bundle/asset_0/TextAsset/-42.bytes"
    assert media.read_bytes() == nested_media.read_bytes() == b"\x00\xff\x80Dofus"
    media.write_bytes(b"stale export")
    nested_media.unlink()

    assert export_assets.main() == 0
    assert media.read_bytes() == nested_media.read_bytes() == b"\x00\xff\x80Dofus"


def test_missing_native_resource_does_not_prevent_exporting_later_objects(
    tmp_path: Path, text_environment: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    text_environment.type.name = "Texture2D"
    text_environment.parse_as_object.side_effect = FileNotFoundError("missing.resS")
    other = Mock()
    other.type.name = "TextAsset"
    other.path_id = 43
    other.assets_file = text_environment.assets_file
    other.parse_as_dict.return_value = {"m_Name": "text"}
    other.parse_as_object.return_value = SimpleNamespace(m_Script="exported")
    environment = SimpleNamespace(assets=[other.assets_file], objects=[text_environment, other])
    monkeypatch.setattr(export_assets, "load_bundle", Mock(return_value=environment))

    report = export_assets.export_asset_bundle(tmp_path / "source.bundle", tmp_path, game_dir=tmp_path)

    assert report.issues == [export_assets.ExportIssue("native", "missing.resS", -42)]
    assert (tmp_path / "asset_0/TextAsset/43.bytes").read_bytes() == b"exported"
    assert msgspec.json.decode((tmp_path / "index.json").read_bytes(), type=export_assets.BundleExport) == report


def test_partial_texture_array_keeps_successful_layers_in_the_index(
    tmp_path: Path, text_environment: Mock
) -> None:
    text_environment.type.name = "Texture2DArray"
    broken_layer = Mock()
    broken_layer.save.side_effect = ValueError("Unsupported layer")
    text_environment.parse_as_object.return_value = SimpleNamespace(
        images=[Image.new("RGBA", (2, 2)), broken_layer]
    )

    report = export_assets.export_asset_bundle(tmp_path / "source.bundle", tmp_path, game_dir=tmp_path)

    image_path = "asset_0/Texture2DArray/-42_0.png"
    assert image_path in report.objects[0].files
    with Image.open(tmp_path / image_path) as image:
        assert image.size == (2, 2)
    assert report.issues == [export_assets.ExportIssue("native", "Unsupported layer", -42)]
    assert msgspec.json.decode((tmp_path / "index.json").read_bytes(), type=export_assets.BundleExport) == report


def test_missing_input_bundle_is_reported_and_remaining_bundles_are_exported(
    tmp_path: Path, text_environment: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    game = tmp_path / "game"
    streaming = game / "Dofus_Data/StreamingAssets"
    streaming.mkdir(parents=True)
    (streaming / "first.bundle").touch()
    (streaming / "second.bundle").touch()
    environment = SimpleNamespace(assets=[text_environment.assets_file], objects=[text_environment])
    monkeypatch.setattr(
        export_assets, "load_bundle", Mock(side_effect=[FileNotFoundError("missing bundle"), environment])
    )
    monkeypatch.setattr(export_assets, "OBF_GAME_DIR", game)
    monkeypatch.setattr(export_assets, "BUNDLES_ROOT", tmp_path / "bundles")

    assert export_assets.main() == 1

    output = tmp_path / "bundles/exports"
    assert (output / "second.bundle/asset_0/TextAsset/-42.bytes").read_bytes() == b"\x00\xff\x80Dofus"
    summary = msgspec.json.decode((output / "report.json").read_bytes())
    assert summary["counts"]["failed"] == 1
    assert summary["counts"]["bundles"] == 1
    assert summary["failed_bundles"][0]["error"] == "missing bundle"


def test_output_write_failure_stops_export(
    tmp_path: Path, text_environment: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "write_bytes", Mock(side_effect=OSError("disk full")))

    with pytest.raises(export_assets.ExportWriteError, match="disk full"):
        export_assets.export_asset_bundle(tmp_path / "source.bundle", tmp_path, game_dir=tmp_path)

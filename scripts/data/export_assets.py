import argparse
import re
import sys
from collections import Counter
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any

import msgspec
from PIL.Image import Image
from UnityPy.files.ObjectReader import ObjectReader

sys.path.append(str(Path(__file__).parent.parent.parent))

from DBDofusUnity.consts import BUNDLES_ROOT, OBF_GAME_DIR
from DBDofusUnity.dofus_unity_reader.bundle_export import load_bundle


class ExportIssue(msgspec.Struct):
    stage: str
    error: str
    path_id: int | None = None


class ObjectExport(msgspec.Struct):
    path_id: int
    asset: str
    type: str
    name: str | None
    files: list[str]


class BundleExport(msgspec.Struct):
    source: str
    objects: list[ObjectExport]
    issues: list[ExportIssue]


def _safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[:80].rstrip(" .")


class ExportWriteError(OSError):
    pass


def _write_asset(path: Path, content: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    except OSError as error:
        raise ExportWriteError(f"Cannot write {path}: {error}") from error


def _png_bytes(image: Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _native_assets(obj: ObjectReader[Any], base: Path) -> Iterator[tuple[Path, bytes]]:
    kind = obj.type.name
    if kind not in {
        "Sprite",
        "Texture2D",
        "Texture2DArray",
        "TextAsset",
        "Mesh",
        "Shader",
        "Font",
        "AudioClip",
    }:
        return
    data: Any = obj.parse_as_object()
    if kind in {"Sprite", "Texture2D"}:
        yield base.with_suffix(".png"), _png_bytes(data.image)
    elif kind == "Texture2DArray":
        for index, image in enumerate(data.images):
            yield base.with_name(f"{base.name}_{index}.png"), _png_bytes(image)
    elif kind == "TextAsset":
        yield base.with_suffix(".bytes"), data.m_Script.encode("utf-8", "surrogateescape")
    elif kind in {"Mesh", "Shader"}:
        path = base.with_suffix(".obj" if kind == "Mesh" else ".shader")
        yield path, data.export().encode("utf-8", "surrogateescape")
    elif kind == "Font" and data.m_FontData:
        font = bytes(data.m_FontData)
        yield base.with_suffix(".otf" if font.startswith(b"OTTO") else ".ttf"), font
    elif kind == "AudioClip":
        samples: dict[str, bytes] = data.samples
        if not samples:
            raise ValueError("AudioClip contains no decoded audio samples")
        for index, (name, sample) in enumerate(samples.items()):
            yield base.with_name(f"{base.name}_{index}_{_safe_name(name)}.wav"), sample


def export_asset_bundle(source: Path, destination: Path, *, game_dir: Path) -> BundleExport:
    environment = load_bundle(source, game_dir=game_dir)
    report = BundleExport(str(source), [], [])
    asset_indices = {id(asset): index for index, asset in enumerate(environment.assets)}
    for obj in environment.objects:
        name: str | None = None
        files: list[Path] = []
        base = destination / f"asset_{asset_indices[id(obj.assets_file)]}" / obj.type.name / str(obj.path_id)
        try:
            data = obj.parse_as_dict()
            candidate = data.get("m_Name")
            name = candidate if isinstance(candidate, str) else None
            encoded = msgspec.json.encode(data)
        except Exception as error:
            report.issues.append(ExportIssue("typetree", str(error), obj.path_id))
            raw_path = base.with_suffix(".bin")
            try:
                raw = obj.get_raw_data()
            except Exception as raw_error:
                report.issues.append(ExportIssue("raw", str(raw_error), obj.path_id))
            else:
                _write_asset(raw_path, raw)
                files.append(raw_path)
        else:
            json_path = base.with_suffix(".json")
            _write_asset(json_path, encoded)
            files.append(json_path)
        try:
            for path, payload in _native_assets(obj, base):
                _write_asset(path, payload)
                files.append(path)
        except ExportWriteError:
            raise
        except Exception as error:
            report.issues.append(ExportIssue("native", str(error), obj.path_id))
        report.objects.append(
            ObjectExport(
                obj.path_id,
                obj.assets_file.name,
                obj.type.name,
                name,
                [path.relative_to(destination).as_posix() for path in files],
            )
        )
    _write_asset(destination / "index.json", msgspec.json.encode(report))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export Unity assets from folders or bundle files.")
    parser.add_argument("sources", nargs="*", type=Path, help="Folders (recursive) or bundle files; defaults to StreamingAssets")
    args = parser.parse_args(argv if argv is not None else [])
    game_dir = OBF_GAME_DIR.resolve()
    streaming = game_dir / "Dofus_Data" / "StreamingAssets"
    output = (BUNDLES_ROOT / "exports").resolve()
    if output.is_relative_to(game_dir) or game_dir.is_relative_to(output):
        raise ValueError("The export directory must be separate from the game installation.")
    bundles: set[Path] = set()
    for path in args.sources or [streaming]:
        source = path.resolve()
        if source.is_dir():
            bundles.update(bundle for bundle in source.rglob("*.bundle") if bundle.is_file())
        elif source.is_file():
            bundles.add(source)
        else:
            parser.error(f"Source does not exist: {path}")
    if not bundles:
        parser.error("No bundles found in the selected sources")
    output.mkdir(parents=True, exist_ok=True)
    totals: Counter[str] = Counter()
    failures: list[ExportIssue] = []
    for index, source in enumerate(sorted(bundles), start=1):
        relative = (
            source.relative_to(streaming)
            if source.is_relative_to(streaming)
            else Path("external", _safe_name(source.anchor), *source.parts[1:])
        )
        destination = output / relative
        entry = destination / "index.json"
        print(f"[{index}] {source}", flush=True)
        try:
            report = export_asset_bundle(source, destination, game_dir=game_dir)
        except ExportWriteError:
            raise
        except Exception as error:
            failures.append(ExportIssue(str(source), str(error)))
            print(f"  Failed: {error}", flush=True)
            totals["failed"] += 1
            continue
        totals["bundles"] += 1
        totals["objects"] += len(report.objects)
        totals["issues"] += len(report.issues)
        if report.issues:
            print(f"  {len(report.issues)} issue(s); see {entry}", flush=True)
    summary = {"counts": dict(totals), "failed_bundles": msgspec.to_builtins(failures)}
    _write_asset(output / "report.json", msgspec.json.encode(summary))
    print(f"Export report: {output / 'report.json'}\n{dict(totals)}", flush=True)
    return 1 if failures or totals["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

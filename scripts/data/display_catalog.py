import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for import_root in (ROOT, ROOT / "AnkamaLauncherEmulator", ROOT / "DBDofusUnity"):
    sys.path.insert(0, str(import_root))

import msgspec

from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.data_center.i18n import I18N
from src.core.engine.crafts.recipes import is_supported_craft_recipe
from src.services.display_catalog import DISPLAY_CATALOG_PATH, DisplayCatalog, RecipeEntry, ZoneEntry


def build_display_catalog() -> DisplayCatalog:
    reader = DataReader()
    names = I18N().name_by_id
    areas = sorted(
        (ZoneEntry(area.id, names[area.nameId]) for area in reader.area_by_id.values()),
        key=lambda entry: entry.name,
    )
    sub_areas: dict[int, list[ZoneEntry]] = {}
    for area in sorted(reader.sub_area_by_id.values(), key=lambda area: names[area.nameId]):
        sub_areas.setdefault(area.areaId, []).append(ZoneEntry(area.id, names[area.nameId]))
    map_names: dict[int, str] = {}
    positions: dict[int, tuple[int, int]] = {}
    for map_id, position in reader.map_info_by_map_id.items():
        positions[map_id] = position.posX, position.posY
        sub_area = reader.sub_area_by_id.get(position.subAreaId)
        if sub_area is not None:
            map_names[map_id] = names[sub_area.nameId]
    recipes: dict[int, RecipeEntry] = {}
    for recipe in reader.recipes:
        if not is_supported_craft_recipe(recipe):
            continue
        item = reader.item_by_id[recipe.resultId]
        recipes[recipe.resultId] = RecipeEntry(
            recipe, names[item.nameId], names[reader.job_by_id[recipe.jobId].nameId], item.level or 0
        )
    return DisplayCatalog(
        areas,
        sub_areas,
        map_names,
        positions,
        {key: names[job.nameId] for key, job in reader.job_by_id.items()},
        {key: names[int(breed.shortNameId)] for key, breed in reader.breed_by_id.items()},
        {key: names[server.nameId] for key, server in reader.server_by_id.items()},
        recipes,
    )


def main() -> None:
    content = msgspec.json.encode(build_display_catalog())
    temporary = DISPLAY_CATALOG_PATH.with_suffix(".json.tmp")
    temporary.write_bytes(content)
    temporary.replace(DISPLAY_CATALOG_PATH)
    print(f"Display catalog generated: {DISPLAY_CATALOG_PATH}")


if __name__ == "__main__":
    main()

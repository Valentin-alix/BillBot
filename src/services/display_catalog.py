from functools import cache

import msgspec

from DBDofusUnity.dofus_unity_reader.models.datas.recipe_root import RecipeItem
from src.utils.project_paths import BUNDLE_ROOT

DISPLAY_CATALOG_PATH = BUNDLE_ROOT / "DBDofusUnity/datas/bundles/display_catalog.json"


class ZoneEntry(msgspec.Struct, frozen=True):
    id: int
    name: str


class RecipeEntry(msgspec.Struct, frozen=True):
    recipe: RecipeItem
    name: str
    job_name: str
    level: int


class DisplayCatalog(msgspec.Struct):
    areas: list[ZoneEntry]
    sub_areas: dict[int, list[ZoneEntry]]
    map_names: dict[int, str]
    map_positions: dict[int, tuple[int, int]]
    job_names: dict[int, str]
    breed_names: dict[int, str]
    server_names: dict[int, str]
    recipe_by_id: dict[int, RecipeEntry]


@cache
def load_display_catalog() -> DisplayCatalog:
    return msgspec.json.decode(DISPLAY_CATALOG_PATH.read_bytes(), type=DisplayCatalog)

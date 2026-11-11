from collections.abc import Mapping

from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.data_center.i18n import I18N
from DBDofusUnity.dofus_unity_reader.game_constants.item import ItemTypeEnum
from DBDofusUnity.dofus_unity_reader.game_constants.job import HARVESTER_JOB_IDS, JobEnum
from DBDofusUnity.dofus_unity_reader.game_constants.skill import MAP_IDS_BY_SKILL
from DBDofusUnity.dofus_unity_reader.models.datas.recipe_root import RecipeItem
from src.controller.game_data import GameDataController
from src.core.config import WEIGHT_BY_JOB
from src.core.engine.items.item import get_gatherer_item_gids
from src.core.states.guild_chest_state import get_gids_by_tab
from src.services.logging_utils.loggers import BotLogger


def is_supported_craft_recipe(recipe: RecipeItem) -> bool:
    return recipe.jobId in HARVESTER_JOB_IDS | {JobEnum.CHASSEUR} and recipe.skillId in MAP_IDS_BY_SKILL


def get_benefice_on_craft_recipe(recipe: RecipeItem, server_id: int = 1) -> tuple[float, float]:
    avg_price_by_gid = GameDataController().get_avg_price_by_gid(server_id)
    if recipe.resultId in avg_price_by_gid and all(
        ingredient_id in avg_price_by_gid for ingredient_id in recipe.ingredientIds
    ):
        profit = avg_price_by_gid[recipe.resultId]
        for ingredient_id in recipe.ingredientIds:
            profit -= avg_price_by_gid[ingredient_id]
        return profit, profit / avg_price_by_gid[recipe.resultId]
    return 0, 0


def is_not_valid_recipe_for_lvl_up_job_or_benefice(
    recipe: RecipeItem, is_sub: bool, jobs_lvl_by_id: dict[int, int]
) -> bool:
    max_job_lvl = 200 if is_sub else 60
    current_job_lvl = jobs_lvl_by_id.get(recipe.jobId)
    if current_job_lvl is None or current_job_lvl >= max_job_lvl:
        return True
    if not is_supported_craft_recipe(recipe):
        return True
    result_item = DataReader().item_by_id[recipe.resultId]
    if result_item.craftConditionalCriterion not in ["", None]:
        return True
    benefit_percent = get_benefice_on_craft_recipe(recipe)[1]
    if current_job_lvl - recipe.resultLevel >= 20 and not (
        benefit_percent > 0.3 and recipe.resultId in get_gids_by_tab()[2]
    ):
        return True
    if any(
        (
            ingredient_id not in get_gatherer_item_gids()
            and DataReader().item_by_id[ingredient_id].typeId != ItemTypeEnum.VIANDE
        )
        for ingredient_id in recipe.ingredientIds
    ):
        return True
    return False


def get_max_result_quantity(
    logger: BotLogger,
    quantity_by_gid: Mapping[int, int],
    recipe: RecipeItem,
) -> tuple[int, int]:
    max_result_quantity: int | None = None
    weight_for_one_result = 0

    for ingredient_id, quantity in zip(recipe.ingredientIds, recipe.quantities):
        ingredient_quantity = quantity_by_gid.get(ingredient_id)
        if ingredient_quantity is None:
            name_id = DataReader().item_by_id[ingredient_id].nameId
            logger.info(
                f"ingredient {I18N().name_by_id[name_id] if name_id else ''} is unavailable, can't craft recipe"
            )
            logger.info(f"we have {list(quantity_by_gid.keys())} gids and we need this : {ingredient_id}")
            return 0, weight_for_one_result
        if ingredient_quantity < quantity:
            name_id = DataReader().item_by_id[ingredient_id].nameId
            logger.info(
                f"ingredient {I18N().name_by_id[name_id] if name_id else ''} don't have enough "
                f"quantity, can't craft recipe {I18N().name_by_id[int(recipe.resultNameId)]}"
            )
            return 0, weight_for_one_result

        result_quantity = ingredient_quantity // quantity
        if max_result_quantity is None or result_quantity < max_result_quantity:
            max_result_quantity = result_quantity

        weight_for_one_result += quantity * (DataReader().item_by_id[ingredient_id].realWeight or 0)

    return (max_result_quantity or 0), weight_for_one_result


def get_max_possible_result_quantity(
    weight_max: int,
    inventory_weight: int,
    weight_for_one_result: int,
    max_result_quantity: int,
) -> int:
    player_weight = weight_max - inventory_weight
    max_possible_result_quantity = min(player_weight // weight_for_one_result, max_result_quantity)
    return max_possible_result_quantity


def get_valid_recipes(
    logger: BotLogger,
    jobs_lvl_by_id: dict[int, int],
    recipes: list[RecipeItem],
    forbidden_craft_ids: set[int],
) -> list[RecipeItem]:
    valid_recipes: list[RecipeItem] = []
    for recipe in recipes:
        if recipe.resultId in forbidden_craft_ids:
            continue

        skill_data = DataReader().skill_by_id[recipe.skillId]
        if jobs_lvl_by_id.get(skill_data.parentJobId, 1) < recipe.resultLevel:
            result_name = I18N().name_by_id.get(int(recipe.resultNameId), str(recipe.resultId))
            logger.warning(f"Can't craft recipe {result_name} because of job lvl")
            continue
        valid_recipes.append(recipe)
    return valid_recipes


def get_recipes_for_job_lvl_up_or_benefice(is_sub: bool, jobs_lvl_by_id: dict[int, int]) -> list[RecipeItem]:
    recipes: list[RecipeItem] = []
    for recipe in DataReader().recipes:
        if is_not_valid_recipe_for_lvl_up_job_or_benefice(recipe, is_sub, jobs_lvl_by_id):
            continue
        recipes.append(recipe)
    recipes.sort(
        key=lambda recipe: (
            WEIGHT_BY_JOB.get(JobEnum(recipe.jobId), 1),
            recipe.resultLevel - jobs_lvl_by_id[recipe.jobId],
        ),
        reverse=True,
    )
    return recipes

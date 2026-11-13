import math

from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.game_constants.characteristic import CharacteristicEnum
from DBDofusUnity.dofus_unity_reader.game_constants.description import DescriptionEnum
from DBDofusUnity.dofus_unity_reader.models.datas.spell_levels_root import (
    Effect,
    SpellLevelsRootItem,
)
from src.core.engine.contexts import AttackContext
from src.core.engine.fights.attack.spell_filter import collect_castable_spells
from src.core.engine.fights.effect import base_roll, can_self_cast, is_heal_effect, is_vitality_buff_effect
from src.core.engine.fights.spell_modifier import SpellModifiers
from src.core.engine.fights.stats.characteristic import get_stat_by_id

HEAL_HP_THRESHOLD = 0.5


def estimate_self_heal(effect: Effect, context: AttackContext) -> int:
    description_id = DataReader().effect_by_id[effect.effectId].descriptionId
    heal_bonus = get_stat_by_id(context.characteristic_by_id.get(CharacteristicEnum.HEAL_BONUS))
    base = base_roll(effect)

    if description_id == DescriptionEnum.HEAL_PERCENT_MAX_LIFE:
        return math.floor(base / 100 * context.max_life_point + heal_bonus)
    if description_id == DescriptionEnum.HEAL_FLAT_LIFE:
        return math.floor(base + heal_bonus)
    if description_id == DescriptionEnum.BUFF_VITALITY:
        return math.floor(base)

    intelligence = get_stat_by_id(context.characteristic_by_id.get(CharacteristicEnum.INTELLIGENCE))
    return math.floor(base * (100 + intelligence) / 100 + heal_bonus)


def _self_heal_effect(effect: Effect) -> bool:
    return (is_heal_effect(effect) or is_vitality_buff_effect(effect)) and can_self_cast(effect)


def get_valid_heal_spells_for_turn(
    context: AttackContext,
) -> list[tuple[SpellLevelsRootItem, Effect, SpellModifiers]]:
    return collect_castable_spells(context, _self_heal_effect)

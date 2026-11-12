import re
from functools import cache

from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.data_center.i18n import I18N
from DBDofusUnity.dofus_unity_reader.game_constants.characteristic import (
    CharacteristicEnum,
    EffectElement,
    TypeEffect,
)
from DBDofusUnity.dofus_unity_reader.grid.map_point import MapPoint
from DBDofusUnity.dofus_unity_reader.models.datas.monsters_root import MonsterGrade
from DBDofusUnity.dofus_unity_reader.models.datas.spell_levels_root import (
    Effect,
    SpellLevelsRootItem,
)
from src.core.engine.contexts import AttackContext
from src.core.engine.fights.attack.enemy_data import EnemyData
from src.core.engine.fights.attack.push import estimate_collision_damage
from src.core.engine.fights.damage_calculator import DamageCalculator
from src.core.engine.fights.effect import (
    can_target_enemy,
    get_effect_shield_level_bonus,
    get_life_point_percent_malus,
    get_type_effect,
    is_push_effect,
    resolve_effect_element,
)
from src.core.engine.fights.spell_modifier import SpellModifiers
from src.core.engine.fights.stats.characteristic import get_stat_by_id

LIFE_STEAL_PATTERN = re.compile(r"\bvol\b", re.IGNORECASE)


class AttackWeights:
    LIFE_RECOVERY_MULTIPLIER = 2
    LIFE_STEAL_RATIO = 0.5
    ENEMY_KILL_BONUS = 1.0
    SUMMONED_KILL_BONUS = 0.5
    SUMMONED_DAMAGE_DIVISOR = 2

    ALLY_HIT_PENALTY_FACTOR = 0.1
    INVISIBLE_ENEMY_WEIGHT_FACTOR = 0.3


def calculate_attack_weight(
    damage_calculator: DamageCalculator,
    context: AttackContext,
    impact_mps: set[MapPoint],
    spell_lvl: SpellLevelsRootItem,
    effect: Effect,
    target_mp: MapPoint,
    enemies_data: list[EnemyData],
    modifiers: SpellModifiers,
) -> float:
    data_effect = DataReader().effect_by_id[effect.effectId]
    description_effect = I18N().name_by_id[data_effect.descriptionId]

    dmg_weight, effective_damage = calculate_damage_weight(
        damage_calculator, context, spell_lvl, effect, target_mp, impact_mps, enemies_data
    )

    life_malus, shield_bonus = calculate_life_modifiers(context, spell_lvl)
    life_stolen = (
        effective_damage * AttackWeights.LIFE_STEAL_RATIO if is_life_steal(description_effect) else 0.0
    )

    life_recovery_weight = calculate_life_recovery_weight(context, life_stolen, life_malus, shield_bonus)

    ally_penalty = _ally_hit_penalty(context, impact_mps)

    return (dmg_weight * life_recovery_weight * ally_penalty) / modifiers.ap_cost


def _ally_hit_penalty(context: AttackContext, impact_mps: set[MapPoint]) -> float:
    enemy_ids = {actor.actor_id for actor in context.enemy_actors}
    ally_cell_ids = {
        actor.disposition.cell_id
        for actor in context.actor_by_id.values()
        if actor.actor_id != context.player_character_id
        and actor.actor_id not in enemy_ids
        and actor.disposition.cell_id != -1
    }
    allies_hit = sum(1 for mp in impact_mps if mp.cell_id in ally_cell_ids)
    return AttackWeights.ALLY_HIT_PENALTY_FACTOR**allies_hit


@cache
def is_life_steal(description_effect: str) -> bool:
    return bool(LIFE_STEAL_PATTERN.search(description_effect))


def calculate_life_modifiers(context: AttackContext, spell_lvl: SpellLevelsRootItem) -> tuple[int, int]:
    life_point_malus = 0
    shield_bonus = 0

    for effect in spell_lvl.effects:
        type_effect = get_type_effect(spell_lvl.spellId, effect)
        if type_effect is None:
            continue

        if type_effect == TypeEffect.MALUS_LIFE_PERCENT:
            life_point_malus += get_life_point_percent_malus(context.life_point, effect)
        elif type_effect == TypeEffect.SHIELD_PERCENT_LEVEL:
            shield_bonus += get_effect_shield_level_bonus(context.player_level, effect)

    return life_point_malus, shield_bonus


def calculate_life_recovery_weight(
    context: AttackContext,
    life_stolen: float,
    life_malus: int,
    shield_bonus: int,
) -> float:
    new_life = context.life_point + life_stolen - life_malus + shield_bonus
    new_life_percentage = min(new_life / context.max_life_point, 1.0)

    return 1 + ((new_life_percentage - context.life_percentage) * AttackWeights.LIFE_RECOVERY_MULTIPLIER)


def _co_zone_effects(spell_lvl: SpellLevelsRootItem, representative: Effect) -> list[Effect]:
    return [effect for effect in spell_lvl.effects if effect.zoneDescr == representative.zoneDescr]


def _spell_damage_effects(
    spell_lvl: SpellLevelsRootItem, representative: Effect, primary_elem: EffectElement
) -> list[Effect]:
    """Sum only effects sharing the representative's zone; the representative ensures a nonempty result."""
    return [
        effect
        for effect in _co_zone_effects(spell_lvl, representative)
        if can_target_enemy(effect)
        and resolve_effect_element(effect.effectElement, primary_elem) is not None
        and DataReader().effect_by_id[effect.effectId].characteristicOperator == ""
    ]


def _spell_push_distance(spell_lvl: SpellLevelsRootItem, representative: Effect) -> int | None:
    for effect in _co_zone_effects(spell_lvl, representative):
        if is_push_effect(effect):
            return effect.diceNum
    return None


def _estimate_damage_at(
    damage_calculator: DamageCalculator,
    context: AttackContext,
    spell_lvl: SpellLevelsRootItem,
    effect: Effect,
    damage_effects: list[Effect],
    target_mp: MapPoint,
    at_mp: MapPoint,
    monster_grade: MonsterGrade | None,
) -> float:
    distance = target_mp.distance_to_map_point(at_mp)
    applied_steps = min(distance, effect.zoneDescr.maxDamageDecreaseApplyCount)
    damage_decrease = effect.zoneDescr.damageDecreaseStepPercent * applied_steps / 100.0

    is_melee = context.player_map_point.distance_to_map_point(at_mp) == 1
    raw_damage = sum(
        damage_calculator.get_damage_effect(
            damage_effect,
            spell_lvl,
            monster_grade,
            context.characteristic_by_id,
            is_melee,
            context.primary_elem,
            context.modifier_by_type_and_spell_id,
        )
        for damage_effect in damage_effects
    )
    return max(raw_damage * (1 - damage_decrease), 0)


def calculate_damage_weight(
    damage_calculator: DamageCalculator,
    context: AttackContext,
    spell_lvl: SpellLevelsRootItem,
    effect: Effect,
    target_mp: MapPoint,
    impact_mps: set[MapPoint],
    enemies_data: list[EnemyData],
) -> tuple[float, float]:
    enemy_killed = 0.0
    enemy_dmg_weight = 0.0
    total_effective_damage = 0.0

    damage_effects = _spell_damage_effects(spell_lvl, effect, context.primary_elem)

    push_distance = _spell_push_distance(spell_lvl, effect)
    occupied_cell_ids: set[int] = set()
    push_damage_bonus = 0
    if push_distance is not None:
        occupied_cell_ids = {
            actor.disposition.cell_id
            for actor in context.actor_by_id.values()
            if actor.disposition.cell_id != -1
        }
        push_damage_bonus = get_stat_by_id(
            context.characteristic_by_id.get(CharacteristicEnum.PUSH_DAMAGE_BONUS)
        )

    for enemy_data in enemies_data:
        if enemy_data.map_point not in impact_mps:
            continue

        if enemy_data.is_invulnerable or enemy_data.is_hidden:
            continue

        damage = _estimate_damage_at(
            damage_calculator,
            context,
            spell_lvl,
            effect,
            damage_effects,
            target_mp,
            enemy_data.map_point,
            enemy_data.monster_grade,
        )

        if push_distance is not None:
            damage += estimate_collision_damage(
                context.player_map_point,
                enemy_data.map_point,
                push_distance,
                context.map_id,
                occupied_cell_ids - {enemy_data.map_point.cell_id},
                context.player_level,
                push_damage_bonus,
            )

        effective_damage = min(damage, enemy_data.life_point)
        total_effective_damage += effective_damage

        if damage >= enemy_data.life_point:
            enemy_killed += (
                AttackWeights.SUMMONED_KILL_BONUS
                if enemy_data.is_summoned
                else AttackWeights.ENEMY_KILL_BONUS
            )

        enemy_health_percentage = max(enemy_data.life_point / enemy_data.max_life_point, 0.1)
        damage_efficiency = effective_damage / enemy_health_percentage
        if enemy_data.is_summoned:
            damage_efficiency /= AttackWeights.SUMMONED_DAMAGE_DIVISOR

        enemy_dmg_weight += damage_efficiency

    known_enemy_mps = {enemy_data.map_point for enemy_data in enemies_data}
    for invisible_mp in impact_mps:
        if invisible_mp.cell_id not in context.invisible_enemy_cell_ids or invisible_mp in known_enemy_mps:
            continue

        damage = _estimate_damage_at(
            damage_calculator, context, spell_lvl, effect, damage_effects, target_mp, invisible_mp, None
        )
        enemy_dmg_weight += damage * AttackWeights.INVISIBLE_ENEMY_WEIGHT_FACTOR

    damage_weight = enemy_dmg_weight * (1 + enemy_killed)
    return damage_weight, total_effective_damage

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import pulp

from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessageField
from DBDofusUnity.proto_mapper_assembly.interfaces.runtime_data import NormalizedRuntimeInstance
from DBDofusUnity.proto_mapper_assembly.validators.global_validators import (
    global_validator_interactive_element,
)
from DBDofusUnity.proto_mapper_assembly.validators.set_validators import (
    validator_aggression_event,
    validator_bak_action_event,
    validator_bak_action_request,
    validator_character_characteristic_detailed_usable,
    validator_character_characteristic_upgrade_request,
    validator_character_characteristics,
    validator_exchange_first_character_pods,
    validator_exchange_positions,
    validator_exchange_requested_trade_event,
    validator_exchange_second_character_pods,
    validator_fight_request_canceled_event,
    validator_fight_slave_switch_context_event,
    validator_fight_starting_positions,
    validator_fight_turn_event,
    validator_job_experience,
    validator_map_coordinates,
    validator_monster_angry_at_player_event,
    validator_paddock_buy_result_event,
    validator_paddock_move_item_request,
    validator_paddocks_to_sell_event,
    validator_player_fight_friendly_answered_event,
    validator_player_fight_friendly_requested_event,
    validator_slide,
    validator_subscribe_multiple_channel_request,
    validator_update_life_points_event,
)

_MAX_CONSTRAINT_TUPLES: int = 50_000


class LpConstrainable(Protocol):
    def addConstraint(self, _constraint: pulp.LpAffineExpression, /, name: str | None = None) -> object: ...  # noqa: N802


@dataclass(frozen=True)
class SetValidatorFieldGroup:
    message_short_name: str
    field_names: tuple[str, ...]
    validator_fn: Callable[[dict[str, object]], bool]


@dataclass(frozen=True)
class GlobalValidatorFieldGroup:
    message_short_name: str
    field_names: tuple[str, ...]
    validator_fn: Callable[[list[dict[str, object]]], bool]


SET_VALIDATOR_FIELD_GROUPS: tuple[SetValidatorFieldGroup, ...] = (
    SetValidatorFieldGroup(
        message_short_name="BakActionRequest",
        field_names=("kamas", "ogrines", "rate", "bid_action"),
        validator_fn=validator_bak_action_request,
    ),
    SetValidatorFieldGroup(
        message_short_name="BakActionEvent",
        field_names=("kamas", "amount", "rate", "bid_action"),
        validator_fn=validator_bak_action_event,
    ),
    SetValidatorFieldGroup(
        message_short_name="UpdateLifePointsEvent",
        field_names=("max_life_points", "life_points"),
        validator_fn=validator_update_life_points_event,
    ),
    SetValidatorFieldGroup(
        message_short_name="Slide",
        field_names=("start_cell", "end_cell"),
        validator_fn=validator_slide,
    ),
    SetValidatorFieldGroup(
        message_short_name="ExchangePositions",
        field_names=("caster_cell_id", "target_cell_id"),
        validator_fn=validator_exchange_positions,
    ),
    SetValidatorFieldGroup(
        message_short_name="ExchangeStartedWithPodsEvent",
        field_names=("first_character_max_weight", "first_character_current_weight"),
        validator_fn=validator_exchange_first_character_pods,
    ),
    SetValidatorFieldGroup(
        message_short_name="ExchangeStartedWithPodsEvent",
        field_names=("second_character_max_weight", "second_character_current_weight"),
        validator_fn=validator_exchange_second_character_pods,
    ),
    SetValidatorFieldGroup(
        message_short_name="ExchangeRequestedTradeEvent",
        field_names=("source_id", "target_id"),
        validator_fn=validator_exchange_requested_trade_event,
    ),
    SetValidatorFieldGroup(
        message_short_name="CharacterCharacteristics",
        field_names=("experience_level_floor", "experience_next_level_floor", "experience"),
        validator_fn=validator_character_characteristics,
    ),
    SetValidatorFieldGroup(
        message_short_name="CharacterCharacteristicDetailedUsable",
        field_names=("used", "base", "objects_and_mount_bonus"),
        validator_fn=validator_character_characteristic_detailed_usable,
    ),
    SetValidatorFieldGroup(
        message_short_name="JobExperience",
        field_names=("job_xp_level_floor", "job_xp_next_level_floor", "job_xp"),
        validator_fn=validator_job_experience,
    ),
    SetValidatorFieldGroup(
        message_short_name="FightStartingPositions",
        field_names=("challengers_positions", "defenders_positions"),
        validator_fn=validator_fight_starting_positions,
    ),
    SetValidatorFieldGroup(
        message_short_name="MapCoordinates",
        field_names=("world_x", "world_y"),
        validator_fn=validator_map_coordinates,
    ),
    SetValidatorFieldGroup(
        message_short_name="PaddockMoveItemRequest",
        field_names=("old_cell_id", "new_cell_id"),
        validator_fn=validator_paddock_move_item_request,
    ),
    SetValidatorFieldGroup(
        message_short_name="PaddocksToSellEvent",
        field_names=("page_index", "page_total"),
        validator_fn=validator_paddocks_to_sell_event,
    ),
    SetValidatorFieldGroup(
        message_short_name="AggressionEvent",
        field_names=("attacker_id", "defender_id"),
        validator_fn=validator_aggression_event,
    ),
    SetValidatorFieldGroup(
        message_short_name="FightRequestCanceledEvent",
        field_names=("source_id", "target_id"),
        validator_fn=validator_fight_request_canceled_event,
    ),
    SetValidatorFieldGroup(
        message_short_name="PlayerFightFriendlyRequestedEvent",
        field_names=("source_id", "target_id"),
        validator_fn=validator_player_fight_friendly_requested_event,
    ),
    SetValidatorFieldGroup(
        message_short_name="PlayerFightFriendlyAnsweredEvent",
        field_names=("source_id", "target_id"),
        validator_fn=validator_player_fight_friendly_answered_event,
    ),
    SetValidatorFieldGroup(
        message_short_name="MonsterAngryAtPlayerEvent",
        field_names=("angry_start_time", "attack_time"),
        validator_fn=validator_monster_angry_at_player_event,
    ),
    SetValidatorFieldGroup(
        message_short_name="FightSlaveSwitchContextEvent",
        field_names=("master_id", "slave_id"),
        validator_fn=validator_fight_slave_switch_context_event,
    ),
    SetValidatorFieldGroup(
        message_short_name="FightTurnEvent",
        field_names=("base_time", "extra_time"),
        validator_fn=validator_fight_turn_event,
    ),
    SetValidatorFieldGroup(
        message_short_name="PaddockBuyResultEvent",
        field_names=("bought", "price"),
        validator_fn=validator_paddock_buy_result_event,
    ),
    SetValidatorFieldGroup(
        message_short_name="SubscribeMultipleChannelRequest",
        field_names=("channel_enabled", "channel_disabled"),
        validator_fn=validator_subscribe_multiple_channel_request,
    ),
    SetValidatorFieldGroup(
        message_short_name="CharacterCharacteristicUpgradeRequest",
        field_names=(
            "strength",
            "vitality",
            "wisdom",
            "chance",
            "agility",
            "intelligence",
        ),
        validator_fn=validator_character_characteristic_upgrade_request,
    ),
)

GLOBAL_VALIDATOR_FIELD_GROUPS: tuple[GlobalValidatorFieldGroup, ...] = (
    GlobalValidatorFieldGroup(
        message_short_name="InteractiveElement",
        field_names=("element_type_id", "element_id"),
        validator_fn=global_validator_interactive_element,
    ),
)


_SET_VALIDATOR_NAMES: frozenset[str] = frozenset(
    group.message_short_name for group in SET_VALIDATOR_FIELD_GROUPS
)
_GLOBAL_VALIDATOR_NAMES: frozenset[str] = frozenset(
    group.message_short_name for group in GLOBAL_VALIDATOR_FIELD_GROUPS
)


def has_applicable_constraints(non_obf_message_name: str) -> bool:
    return non_obf_message_name in _SET_VALIDATOR_NAMES or non_obf_message_name in _GLOBAL_VALIDATOR_NAMES


def build_ilp_validator_constraints(
    *,
    group: SetValidatorFieldGroup | GlobalValidatorFieldGroup,
    non_obf_fields: tuple[DumpCSMessageField, ...],
    obf_fields: tuple[DumpCSMessageField, ...],
    runtime_instances: tuple[NormalizedRuntimeInstance, ...],
    lp_variable_by_idxs: dict[tuple[int, int], pulp.LpVariable],
    problem: LpConstrainable,
) -> None:
    idx_by_non_obf_name = {field.clean_field_name: idx for idx, field in enumerate(non_obf_fields)}
    non_obf_idxs = [idx_by_non_obf_name[field_name] for field_name in group.field_names]

    k = len(group.field_names)
    m = len(obf_fields)

    if math.perm(m, k) > _MAX_CONSTRAINT_TUPLES:
        print(f"<!> Too many permutation for validate {group.message_short_name}, skipping.")
        return

    obf_field_names = [field.clean_field_name for field in obf_fields]

    for obf_jj in itertools.permutations(range(m), k):
        trial_instances = _build_trial_instances(
            runtime_instances=runtime_instances,
            field_names=group.field_names,
            obf_field_names=obf_field_names,
            obf_jj=obf_jj,
        )
        if not trial_instances:
            continue
        try:
            if isinstance(group, SetValidatorFieldGroup):
                validator_passes = all(group.validator_fn(trial) for trial in trial_instances)
            else:
                validator_passes = group.validator_fn(trial_instances)
        except (ValueError, KeyError, AssertionError):
            validator_passes = False

        if not validator_passes:
            lp_sum = pulp.lpSum(
                lp_variable_by_idxs[ii, jj] for ii, jj in zip(non_obf_idxs, obf_jj, strict=True)
            )
            problem.addConstraint(lp_sum <= k - 1)


def _build_trial_instances(
    *,
    runtime_instances: tuple[NormalizedRuntimeInstance, ...],
    field_names: tuple[str, ...],
    obf_field_names: list[str],
    obf_jj: tuple[int, ...],
) -> list[dict[str, object]]:
    trial_instances: list[dict[str, object]] = []
    for inst in runtime_instances:
        trial = {fn: inst.get(obf_field_names[j]) for fn, j in zip(field_names, obf_jj, strict=True)}
        if all(val is None for val in trial.values()):
            continue
        trial_instances.append(trial)
    return trial_instances

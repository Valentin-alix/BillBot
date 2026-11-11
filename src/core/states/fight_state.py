import dataclasses
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import (
    ActorPositionInformation,
    CharacterCharacteristic,
    SpellModifier,
    SpellModifierType,
)
from DBDofusUnity.datas.protos.non_obf.game.spell_pb2 import SpellItem
from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.game_constants.breed import BreedEnum
from DBDofusUnity.dofus_unity_reader.game_constants.characteristic import (
    CharacteristicEnum,
    EffectElement,
)
from DBDofusUnity.dofus_unity_reader.grid.map_point import MapPoint
from src.core import config
from src.core.engine.fights.attack.enemy_data import EnemyData, get_monster_max_spell_range
from src.core.engine.fights.effect import get_effect_elem_by_stat
from src.core.engine.fights.stats.characteristic import get_stat_by_id
from src.core.engine.monsters.monster_group import MonsterFighter
from src.core.signals.player_signals import GameInfoSignals
from src.core.states.entity_state import EntityState, FightActor
from src.core.states.player_state import PlayerState
from src.core.states.state import State


@dataclass
class FightState(State):
    player_state: PlayerState
    entity_state: EntityState
    game_info_signals: GameInfoSignals

    fight_placement_possible_positions: list[int] = field(init=False, default_factory=list[int])
    _is_our_turn: bool = field(init=False, default=False)
    spells: list[SpellItem] = dataclasses.field(init=False, default_factory=list[SpellItem])
    modifier_by_type_and_spell_id: dict[tuple[int, SpellModifierType], SpellModifier] = dataclasses.field(
        init=False,
        default_factory=dict[tuple[int, SpellModifierType], SpellModifier],
    )
    count_casted_by_spell_id_on_current_turn: dict[int, int] = dataclasses.field(
        init=False, default_factory=lambda: defaultdict(int)
    )

    cast_turn_by_spell_id: dict[int, int] = dataclasses.field(init=False, default_factory=dict[int, int])
    own_spell_id_by_effect_uid: dict[int, int] = dataclasses.field(init=False, default_factory=dict[int, int])
    characteristic_by_id: dict[int, CharacterCharacteristic] = dataclasses.field(
        init=False, default_factory=dict[int, CharacterCharacteristic]
    )
    _breed_id: int = dataclasses.field(init=False, default=0)
    _in_fight: bool = dataclasses.field(init=False, default=False)
    _fight_turn: int = dataclasses.field(init=False, default=0)
    _life_point: int = dataclasses.field(init=False, default=1)
    _max_life_point: int = dataclasses.field(init=False, default=1)
    invisible_enemy_cell_ids: set[int] = dataclasses.field(init=False, default_factory=set[int])

    def clear_state(self):
        self.fight_placement_possible_positions.clear()
        self.is_our_turn = False
        self.spells.clear()
        self.modifier_by_type_and_spell_id.clear()
        self.count_casted_by_spell_id_on_current_turn.clear()
        self.cast_turn_by_spell_id.clear()
        self.own_spell_id_by_effect_uid.clear()
        self.characteristic_by_id.clear()
        self.in_fight = False
        self.fight_turn = 0
        self.invisible_enemy_cell_ids.clear()

    def add_invisible_enemy_cell(self, cell_id: int) -> None:
        self.invisible_enemy_cell_ids.add(cell_id)

    def get_own_active_stack_count_by_spell_id(self) -> dict[int, int]:
        counts: dict[int, int] = defaultdict(int)
        for spell_id in self.own_spell_id_by_effect_uid.values():
            counts[spell_id] += 1
        return counts

    def get_stat_by_id(self, characteristic: int) -> int:
        value = get_stat_by_id(self.characteristic_by_id.get(characteristic))
        return value

    def can_sync_life_points_from_characteristics(self) -> bool:
        required_characteristic_ids = {
            CharacteristicEnum.LIFE_POINTS,
            CharacteristicEnum.VITALITY,
            CharacteristicEnum.CUR_LIFE,
        }
        return required_characteristic_ids.issubset(self.characteristic_by_id)

    def sync_life_points_from_characteristics(self) -> None:
        required_characteristic_ids = {
            CharacteristicEnum.LIFE_POINTS,
            CharacteristicEnum.VITALITY,
            CharacteristicEnum.CUR_LIFE,
        }
        missing_characteristic_ids = [
            characteristic_id
            for characteristic_id in required_characteristic_ids
            if characteristic_id not in self.characteristic_by_id
        ]
        if missing_characteristic_ids:
            self.logger.error(
                "Cannot sync player HP from characteristics: "
                f"missing_characteristic_ids={missing_characteristic_ids}, "
                f"available_characteristic_ids={list(self.characteristic_by_id)}"
            )
        assert CharacteristicEnum.LIFE_POINTS in self.characteristic_by_id
        assert CharacteristicEnum.VITALITY in self.characteristic_by_id
        assert CharacteristicEnum.CUR_LIFE in self.characteristic_by_id

        life_points = self.get_stat_by_id(CharacteristicEnum.LIFE_POINTS)
        vitality = self.get_stat_by_id(CharacteristicEnum.VITALITY)
        current_life_delta = self.get_stat_by_id(CharacteristicEnum.CUR_LIFE)

        max_life_point = life_points + vitality
        synced_life_point = max_life_point + current_life_delta
        if max_life_point <= 0 or synced_life_point < 0:
            self.logger.error(
                "Player HP characteristic sync would violate invariants: "
                f"life_points={life_points}, vitality={vitality}, "
                f"cur_life={current_life_delta}, max_life_point={max_life_point}, "
                f"life_point={synced_life_point}"
            )
        self.max_life_point = max_life_point
        self.life_point = synced_life_point

    def update_characteristic(self, characteristic: CharacterCharacteristic) -> None:
        self.characteristic_by_id[characteristic.characteristic_id] = characteristic
        if not config.DEBUG:
            return
        value = get_stat_by_id(characteristic)
        if characteristic.characteristic_id == CharacteristicEnum.ACTION_POINTS:
            self.game_info_signals.action_points.emit(value)
        elif characteristic.characteristic_id == CharacteristicEnum.MOVEMENT_POINTS:
            self.game_info_signals.movement_points.emit(value)

    @property
    def breed_id(self):
        return self._breed_id

    @breed_id.setter
    def breed_id(self, value: int):
        self._breed_id = value
        if config.DEBUG:
            self.game_info_signals.breed_id.emit(value)

    @property
    def life_percentage(self):
        return self.life_point / self.max_life_point

    @property
    def fight_turn(self):
        return self._fight_turn

    @fight_turn.setter
    def fight_turn(self, value: int):
        self._fight_turn = value
        if config.DEBUG:
            self.game_info_signals.fight_turn.emit(value)

    @property
    def max_life_point(self):
        return self._max_life_point

    @max_life_point.setter
    def max_life_point(self, value: int):
        assert value > 0
        self._max_life_point = value
        if config.DEBUG:
            self.game_info_signals.max_life_point.emit(value)

    @property
    def life_point(self):
        return self._life_point

    @life_point.setter
    def life_point(self, value: int):
        assert value >= 0
        self._life_point = value
        if config.DEBUG:
            self.game_info_signals.life_point.emit(self._life_point)

    @property
    def in_fight(self):
        return self._in_fight

    @in_fight.setter
    def in_fight(self, value: bool):
        self._in_fight = value
        self.game_info_signals.in_fight.emit(self._in_fight)

    @property
    def ordered_stat(self) -> list[CharacteristicEnum]:
        def _secondary_sort_stat(char_id: int) -> bool:
            if self.breed_id == BreedEnum.XELOR:
                return char_id == CharacteristicEnum.INTELLIGENCE
            elif self.breed_id in [BreedEnum.IOP, BreedEnum.SACRIER]:
                return char_id == CharacteristicEnum.CHANCE
            elif self.breed_id == BreedEnum.CRA:
                return char_id == CharacteristicEnum.AGILITY
            return False

        dmg_stats: list[CharacteristicEnum] = [
            CharacteristicEnum.CHANCE,
            CharacteristicEnum.AGILITY,
            CharacteristicEnum.STRENGTH,
            CharacteristicEnum.INTELLIGENCE,
        ]
        return list(
            sorted(
                dmg_stats,
                key=lambda char_id: (
                    self.get_stat_by_id(char_id),
                    _secondary_sort_stat(char_id),
                    (char_id),
                ),
                reverse=True,
            )
        )

    @property
    def primary_and_second_elem(self) -> tuple[EffectElement, EffectElement]:
        ordered_stats = self.ordered_stat
        return (
            get_effect_elem_by_stat(ordered_stats[0]),
            get_effect_elem_by_stat(ordered_stats[1]),
        )

    @property
    def primary_elem(self) -> EffectElement:
        return self.primary_and_second_elem[0]

    @property
    def is_our_turn(self) -> bool:
        return self._is_our_turn

    @is_our_turn.setter
    def is_our_turn(self, value: bool):
        self._is_our_turn = value
        if config.DEBUG:
            self.game_info_signals.is_our_turn.emit(value)

    def get_enemies(self, character_id: int) -> list[ActorPositionInformation]:
        return self.get_enemies_from_actor_snapshot(
            character_id,
            self.entity_state.actor_by_id,
        )

    def get_enemies_from_actor_snapshot(
        self,
        character_id: int,
        actor_by_id: Mapping[int, ActorPositionInformation],
    ) -> list[ActorPositionInformation]:
        player_actor = actor_by_id.get(character_id)
        if player_actor is None:
            self.logger.warning(f"Player {character_id} not in actors, return empty enemies")
            return []

        player_team = player_actor.actor_information.fighter.spawn_information.team
        enemies: list[ActorPositionInformation] = []
        for actor in actor_by_id.values():
            if (
                actor.disposition.cell_id == -1
                or not actor.actor_information.HasField("fighter")
                or actor.actor_information.fighter.spawn_information.team == player_team
            ):
                continue

            enemies.append(actor)
        self.logger.debug(f"Found {len(enemies)} enemies")

        return enemies

    def count_own_active_summons(
        self,
        character_id: int,
        actor_by_id: Mapping[int, ActorPositionInformation],
        actor_fight_by_id: Mapping[int, FightActor],
    ) -> int:
        player_actor = actor_by_id.get(character_id)
        if player_actor is None:
            return 0

        player_team = player_actor.actor_information.fighter.spawn_information.team
        return sum(
            1
            for actor in actor_by_id.values()
            if actor.actor_id != character_id
            and actor.actor_information.fighter.spawn_information.team == player_team
            and (actor_fight := actor_fight_by_id.get(actor.actor_id)) is not None
            and actor_fight.is_summoned
            and actor_fight.life_point > 0
        )

    def get_enemies_data(
        self,
        enemies: list[ActorPositionInformation],
        actor_fight_by_id: Mapping[int, FightActor] | None = None,
    ) -> list[EnemyData]:
        fight_actor_by_id = (
            self.entity_state.actor_fight_by_id if actor_fight_by_id is None else actor_fight_by_id
        )
        enemies_data: list[EnemyData] = []
        for enemy in enemies:
            enemy_mp = MapPoint.from_cell_id(enemy.disposition.cell_id)
            actor_fight = fight_actor_by_id.get(enemy.actor_id)
            if not actor_fight:
                self.logger.error(f"Wtf ? {enemy.actor_id} not found in actor fight by id")
                continue
            life_point = actor_fight.life_point
            is_summoned = actor_fight.is_summoned

            monster_info: MonsterFighter = (
                enemy.actor_information.fighter.ai_fighter.monster_fighter_information
            )
            monster_id = monster_info.monster_gid
            assert monster_id in DataReader().monsters_by_id, (
                "Enemy fighter has no known monster data: "
                f"actor_id={enemy.actor_id}, "
                f"team={enemy.actor_information.fighter.spawn_information.team}, "
                f"fighter_kind={_fighter_kind(enemy)}, "
                f"monster_gid={monster_id}, "
                f"creature_grade={monster_info.creature_grade}"
            )

            monster = DataReader().monsters_by_id[monster_id]
            monster_grade = monster.grades[monster_info.creature_grade - 1]
            max_life_point = monster_grade.lifePoints

            enemies_data.append(
                EnemyData(
                    actor=enemy,
                    map_point=enemy_mp,
                    life_point=life_point,
                    max_life_point=max_life_point,
                    is_summoned=is_summoned,
                    monster_grade=monster_grade,
                    invisibility=actor_fight.invisibility,
                    state_ids=actor_fight.state_ids,
                    movement_points=monster_grade.movementPoints,
                    max_spell_range=get_monster_max_spell_range(monster, monster_grade),
                )
            )
        return enemies_data


def _fighter_kind(enemy: ActorPositionInformation) -> str:
    fighter = enemy.actor_information.fighter
    if fighter.HasField("ai_fighter"):
        ai_fighter = fighter.ai_fighter
        if ai_fighter.HasField("monster_fighter_information"):
            return "ai_monster"
        return "ai_fighter"
    if fighter.HasField("named_fighter"):
        return "named_fighter"
    if fighter.HasField("entity_fighter"):
        return "entity_fighter"
    return "unknown"

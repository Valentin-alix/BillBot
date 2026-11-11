from dataclasses import dataclass, field
from typing import Any

from DBDofusUnity.dofus_unity_reader.game_constants.characteristic import CharacteristicEnum
from DBDofusUnity.dofus_unity_reader.grid.map_point import MapPoint
from src.core.config import BehaviorSettings
from src.core.engine.contexts import (
    AttackContext,
    CriterionContext,
    FightReachableContext,
    MapMovementContext,
    WorldPathContext,
    WorldTransitionContext,
)
from src.core.states.craft_state import CraftState
from src.core.states.dialog_state import DialogState
from src.core.states.entity_state import EntityState
from src.core.states.fight_state import FightState
from src.core.states.guild_chest_state import GuildChestState
from src.core.states.interactive_state import InteractiveState
from src.core.states.inventory_state import InventoryState
from src.core.states.map_state import MapState
from src.core.states.player_state import PlayerState
from src.core.states.sale_hotel_state import SaleHotelState
from src.core.states.server_state import ServerState


@dataclass
class GameState:
    entity: EntityState
    fight: FightState
    interactive: InteractiveState
    inventory: InventoryState
    map: MapState
    player: PlayerState
    guild_chest: GuildChestState
    sale_hotel: SaleHotelState
    craft: CraftState
    server: ServerState
    dialog: DialogState
    settings: BehaviorSettings = field(default_factory=BehaviorSettings)

    def apply_settings(self, settings: BehaviorSettings) -> None:
        self.settings = settings
        self.guild_chest.settings = settings

    def clear_connection_scoped_state(self) -> None:
        self.map.clear_state()
        self.fight.clear_state()
        self.entity.clear_state()

    def debug_snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "map_id": self.map.map_id,
            "in_map_transition": bool(self.map.is_in_map_transition),
            "in_haven_bag": self.map.is_in_haven_bag,
            "character_name": self.player.character_name,
            "character_id": self.player.character_id,
            "level": self.player.level,
            "server_id": self.player.server_id,
            "in_fight": self.fight.in_fight,
            "is_our_turn": self.fight.is_our_turn,
            "fight_turn": self.fight.fight_turn,
            "life_point": self.fight.life_point,
            "max_life_point": self.fight.max_life_point,
            "kamas": self.inventory.kamas,
            "inventory_weight": self.inventory.inventory_weight,
            "weight_max": self.inventory.weight_max,
            "actor_count": len(self.entity.actor_by_id),
            "open_dialog": self.dialog.kind,
            "open_dialog_context": self.dialog.context_id,
        }
        try:
            snapshot["sub_area_id"] = self.map.sub_area_id
            snapshot["cell_id"] = self.map.map_point.cell_id
        except KeyError:
            pass
        return snapshot

    def get_map_movement_context(self) -> MapMovementContext:
        return MapMovementContext(
            map_id=self.map.map_id,
            in_fight=self.fight.in_fight,
            obstacle_on_cell_id=self.entity.obstacle_on_cell_id,
            occupied_cell_ids=frozenset(
                {
                    actor.disposition.cell_id
                    for actor in self.entity.actor_by_id.values()
                    if actor.disposition.cell_id != -1
                }
                | self.fight.invisible_enemy_cell_ids
            ),
        )

    def get_criterion_context(self) -> CriterionContext:
        return CriterionContext(
            is_sub=self.player.is_sub,
            player_level=self.player.level,
            player_limited_level=self.player.limited_lvl,
            player_subscription_end_date=self.player.subscription_end_date,
            player_jobs_lvl_by_id=self.player.jobs_lvl_by_id,
            player_waypoint_map_ids=frozenset(self.player.waypoint_map_ids),
            player_server_id=self.player.server_id,
            player_character_id=self.player.character_id,
            map_id=self.map.map_id,
            sub_area_id=self.map.sub_area_id,
            fight_breed_id=self.fight.breed_id,
            fight_characteristic_by_id=self.fight.characteristic_by_id,
            inventory_objects_by_uid=self.inventory.objects_by_uid,
            positive_actor_count=sum(1 for actor_id in self.entity.actor_by_id if actor_id > 0),
        )

    def get_world_transition_context(self) -> WorldTransitionContext:
        return WorldTransitionContext(
            criterion=self.get_criterion_context(),
            forbidden_edge_transitions=frozenset(
                ban.key for ban in self.map.banned_edge_transitions
            ),
        )

    def get_world_path_context(self) -> WorldPathContext:
        return WorldPathContext(
            transition=self.get_world_transition_context(),
            current_map_pos=self.map.map_pos,
        )

    def get_fight_reachable_context(self) -> FightReachableContext:
        return FightReachableContext(
            map_id=self.map.map_id,
            player_map_point=self.map.map_point,
            movement_points=self.fight.get_stat_by_id(CharacteristicEnum.MOVEMENT_POINTS),
        )

    def get_attack_context(self) -> AttackContext:
        context = self.get_attack_context_if_available()
        assert isinstance(context, AttackContext), (
            f"Player {self.player.character_id} is missing from actor state"
        )
        return context

    def get_attack_context_if_available(self) -> AttackContext | None:
        actor_by_id = dict(self.entity.actor_by_id)
        actor_fight_by_id = dict(self.entity.actor_fight_by_id)
        player_actor = actor_by_id.get(self.player.character_id)
        if player_actor is None:
            return None
        enemies = self.fight.get_enemies_from_actor_snapshot(
            self.player.character_id,
            actor_by_id,
        )
        player_fight_actor = actor_fight_by_id.get(self.player.character_id)
        own_state_ids = (
            player_fight_actor.state_ids if player_fight_actor is not None else frozenset[int]()
        )
        own_active_stack_count_by_spell_id = self.fight.get_own_active_stack_count_by_spell_id()
        own_active_summon_count = self.fight.count_own_active_summons(
            self.player.character_id, actor_by_id, actor_fight_by_id
        )
        max_active_summon_count = self.fight.get_stat_by_id(
            CharacteristicEnum.MAX_SUMMONED_CREATURES_BOOST
        )
        return AttackContext(
            map_id=self.map.map_id,
            player_map_point=MapPoint.from_cell_id(player_actor.disposition.cell_id),
            player_character_id=self.player.character_id,
            player_level=self.player.level,
            actor_by_id=actor_by_id,
            enemy_actors=enemies,
            enemies_data=self.fight.get_enemies_data(enemies, actor_fight_by_id),
            spells=self.fight.spells,
            primary_elem=self.fight.primary_elem,
            modifier_by_type_and_spell_id=self.fight.modifier_by_type_and_spell_id,
            count_casted_by_spell_id_on_current_turn=(self.fight.count_casted_by_spell_id_on_current_turn),
            cast_turn_by_spell_id=self.fight.cast_turn_by_spell_id,
            fight_turn=self.fight.fight_turn,
            characteristic_by_id=self.fight.characteristic_by_id,
            action_points=self.fight.get_stat_by_id(CharacteristicEnum.ACTION_POINTS),
            movement_points=self.fight.get_stat_by_id(CharacteristicEnum.MOVEMENT_POINTS),
            range=self.fight.get_stat_by_id(CharacteristicEnum.RANGE),
            life_point=self.fight.life_point,
            max_life_point=self.fight.max_life_point,
            life_percentage=self.fight.life_percentage,
            invisible_enemy_cell_ids=frozenset(self.fight.invisible_enemy_cell_ids),
            breed_id=self.fight.breed_id,
            own_state_ids=own_state_ids,
            own_active_stack_count_by_spell_id=own_active_stack_count_by_spell_id,
            own_active_summon_count=own_active_summon_count,
            max_active_summon_count=max_active_summon_count,
        )

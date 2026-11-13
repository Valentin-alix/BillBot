from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import partial

from DBDofusUnity.datas.protos.non_obf.game.fight_pb2 import (
    FightTurnEvent,
    FightTurnFinishRequest,
)
from DBDofusUnity.dofus_unity_reader.grid.map_point import MapPoint
from DBDofusUnity.dofus_unity_reader.models.datas.spell_levels_root import SpellLevelsRootItem
from src.core.behaviors.farms.fight.fight_listener_behavior import FightListenerBehavior
from src.core.behaviors.farms.fight.fight_movement_behavior import FightMovementBehavior
from src.core.behaviors.farms.fight.fight_spell_behavior import FightSpellBehavior
from src.core.behaviors.movements.map_move_behavior import MapMoveError
from src.core.engine.contexts import AttackContext
from src.core.engine.fights.attack.attacker import Attacker
from src.core.engine.fights.attack.breed_abilities import BreedAbilitySelector
from src.services.human_timings import HumanTimingsService


@dataclass
class FightTurnBehavior(FightListenerBehavior):
    fight_movement_behavior: FightMovementBehavior
    fight_spell_behavior: FightSpellBehavior
    attack_selector: Attacker
    breed_ability_selector: BreedAbilitySelector

    did_attack: bool = field(init=False, default=False)
    did_cast_support_spell: bool = field(init=False, default=False)
    last_cast_spell_id: int | None = field(init=False, default=None)

    def run(self) -> None:
        self.did_attack = False
        self.did_cast_support_spell = False
        self.last_cast_spell_id = None
        self.event_manager.on(FightTurnEvent, lambda _: self.finish(), originator=self)
        self.register_fight_death_check()
        contexts = self._get_attack_context_or_finish()
        if contexts is None:
            return
        context, _ = contexts
        self.logger.info(
            f"Turn {context.fight_turn}: HP {context.life_point}/{context.max_life_point}, "
            f"AP {context.action_points}, MP {context.movement_points}, "
            f"{len(context.enemy_actors)} enemies"
        )
        self._advance_turn(0)

    def _advance_turn(self, stage: int, with_reserved_ap: bool = True) -> None:
        """Each stage retries until exhausted, then falls through without revisiting earlier stages."""
        contexts = self._get_attack_context_or_finish(with_reserved_ap)
        if contexts is None:
            return
        context, attack_context = contexts

        if stage <= 0:
            urgent_action = self.breed_ability_selector.find_urgent_support_action(attack_context)
            if urgent_action is not None:
                _, urgent_spell_lvl, _ = urgent_action
                return self._cast_self_spell(context, urgent_spell_lvl, lambda: self._advance_turn(1))
            stage = 1

        if stage <= 1:
            attack_info = self.attack_selector.find_best_attack_from_mp(attack_context)
            if attack_info is not None:
                return self._do_move_then_spell(attack_info, lambda: self._advance_turn(1))
            stage = 2

        if stage <= 2:
            heal_spell = self.attack_selector.find_best_self_heal(attack_context)
            if heal_spell is not None:
                return self._cast_self_spell(context, heal_spell, lambda: self._advance_turn(2))
            stage = 3

        if stage <= 3:
            buff_spell = self.attack_selector.find_best_self_buff(attack_context)
            if buff_spell is not None:
                return self._cast_self_spell(context, buff_spell, lambda: self._advance_turn(3))

        if stage <= 4:
            support_action = self.breed_ability_selector.find_support_action(context)
            if support_action is not None:
                return self._do_move_then_spell(
                    support_action, lambda: self._advance_turn(0, with_reserved_ap=False)
                )

        if with_reserved_ap:
            return self._advance_turn(0, with_reserved_ap=False)

        self.logger.info("No spell to launch")

        self._do_move()

    def _get_attack_context_or_finish(
        self, with_reserved_ap: bool = False
    ) -> tuple[AttackContext, AttackContext] | None:
        context = self.game_state.get_attack_context_if_available()
        if context is None:
            if self.game_state.fight.life_point <= 0:
                self.finish(MapMoveError.PLAYER_DEAD)
            else:
                self.finish()
            return None

        if not context.enemy_actors and not context.invisible_enemy_cell_ids:
            self.logger.info("No enemies left, passing turn")
            self.pass_turn()
            return None

        if not with_reserved_ap:
            return context, context
        reserved_ap = self.breed_ability_selector.get_reserved_ap(context)
        if reserved_ap <= 0:
            return context, context
        attack_context = replace(context, action_points=max(context.action_points - reserved_ap, 0))
        return context, attack_context

    def _cast_self_spell(
        self,
        context: AttackContext,
        spell_lvl: SpellLevelsRootItem,
        callback: Callable[[], None],
    ) -> None:
        def on_casted_self_spell(error_code: str | None) -> None:
            self.raise_if_error(error_code)
            self.did_cast_support_spell = True
            callback()

        self._schedule_spell_cast(spell_lvl.spellId, context.player_map_point, on_casted_self_spell)

    def _do_move_then_spell(
        self, attack_info: tuple[MapPoint, SpellLevelsRootItem, MapPoint], canceled_error: Callable[[], None]
    ) -> None:
        self.did_attack = True
        move_mp, spell_lvl, attack_mp = attack_info

        position_before_move = self.game_state.map.map_point
        move_path = self.fight_movement_behavior.find_path_to_cell(move_mp)

        def cast_attack() -> None:
            current_mp = self.game_state.map.map_point
            if current_mp != move_mp:
                if current_mp != position_before_move:
                    self.logger.info(f"Did not reach planned cast cell {move_mp} (now at {current_mp})")
                    return canceled_error()
                self.logger.error(f"Could not reach planned cast cell {move_mp}, passing turn")
                return self.pass_turn()

            self._schedule_spell_cast(spell_lvl.spellId, attack_mp, self.on_fight_spell_behavior_finished)

        self.fight_movement_behavior.start(
            callback=partial(
                self.on_fight_movement_behavior_finished,
                callback_canceled=canceled_error,
                callback=cast_attack,
            ),
            parent=self,
            move_path=move_path,
        )

    def _do_move(self) -> None:
        def on_movement_finished(error_code: str | None, cell_mp: MapPoint | None = None) -> None:
            self.on_fight_movement_behavior_finished(
                error_code,
                cell_mp=cell_mp,
                callback_canceled=self.pass_turn,
                callback=lambda: self.run_timer(
                    HumanTimingsService().get_timing_before_pass_turn(), self.pass_turn
                ),
            )

        return self.fight_movement_behavior.start(callback=on_movement_finished, parent=self)

    def _schedule_spell_cast(
        self,
        spell_id: int,
        target_mp: MapPoint,
        callback: Callable[[str | None], None],
    ) -> None:
        same_spell = spell_id == self.last_cast_spell_id

        def start_spell() -> None:
            self.last_cast_spell_id = spell_id
            self.fight_spell_behavior.start(
                spell_id=spell_id, target_mp=target_mp, parent=self, callback=callback
            )

        self.run_timer(HumanTimingsService().get_timing_fight_action(same_spell), start_spell)

    def on_fight_movement_behavior_finished(
        self,
        error_code: str | None,
        callback_canceled: Callable[[], None],
        callback: Callable[[], None],
        cell_mp: MapPoint | None = None,
    ) -> None:
        if error_code is MapMoveError.PLAYER_DEAD:
            return self.finish(error_code)
        if error_code is MapMoveError.CANCELED_MOVEMENT:
            return callback_canceled()
        elif error_code is MapMoveError.CELL_TAKEN:
            assert cell_mp
            self.logger.info(f"Cell {cell_mp.cell_id} taken (likely invisible enemy), blocking it")
            self.game_state.fight.add_invisible_enemy_cell(cell_mp.cell_id)
            return self._advance_turn(1)
        elif error_code is not None:
            self.logger.error(f"Unexpected map move error during fight turn: {error_code}")
            return self.pass_turn()
        callback()

    def on_fight_spell_behavior_finished(self, error_code: str | None) -> None:
        self.raise_if_error(error_code)
        self._advance_turn(0)

    def pass_turn(self) -> None:
        context = self.game_state.get_attack_context_if_available()
        if context is not None:
            message = (
                f"Passing turn: attacked={self.did_attack}, "
                f"support_cast={self.did_cast_support_spell}, "
                f"AP={context.action_points}, MP={context.movement_points}, "
                f"enemies={len(context.enemy_actors)}"
            )
            if (
                not self.did_attack
                and not self.did_cast_support_spell
                and context.action_points > 0
                and context.enemy_actors
            ):
                self.logger.warning(message)
            else:
                self.logger.info(message)
        req = FightTurnFinishRequest()
        self.event_manager.send(req)

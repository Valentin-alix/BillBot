from unittest.mock import MagicMock

import pytest

from DBDofusUnity.datas.protos.non_obf.game.character_pb2 import (
    CharacterCharacteristicsEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import (
    ActorPositionInformation,
    CharacterCharacteristic,
    CharacterCharacteristicDetailed,
    CharacterCharacteristics,
    CharacterCharacteristicValue,
    EntityDisposition,
    FightCharacteristics,
    FightStartingPositions,
    Team,
)
from DBDofusUnity.datas.protos.non_obf.game.fight_pb2 import FightRefreshCharacterStatsEvent
from DBDofusUnity.datas.protos.non_obf.game.fight_preparation_pb2 import (
    FightPlacementPossiblePositionsEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.game_action_pb2 import (
    GameActionFightCastRequest,
    GameActionFightEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.spell_pb2 import (
    SpellItem,
    SpellsEvent,
    SpellVariantActivationEvent,
)
from DBDofusUnity.dofus_unity_reader.game_constants.breed import BreedEnum
from DBDofusUnity.dofus_unity_reader.game_constants.characteristic import (
    CharacteristicEnum,
    EffectElement,
)
from DBDofusUnity.dofus_unity_reader.grid.map_point import MapPoint
from src.core.bot.bot import Bot
from src.core.frames.fight_frame import FightFrame
from src.core.states.entity_state import FightActor
from tests.fixtures.entities import make_fighter
from tests.fixtures.game_state import GameStateContext, set_game_state


class TestFightState:
    def test_spells_event_sets_spells(
        self,
        runtime_bot: Bot,
    ):
        runtime_bot.event_manager.process_msg(
            SpellsEvent(
                human_spells=[
                    SpellItem(spell_id=100, spell_level=1, available=True),
                    SpellItem(spell_id=200, spell_level=2, available=True),
                ]
            )
        )

        assert [spell.spell_id for spell in runtime_bot.game_state.fight.spells] == [
            100,
            200,
        ]

    def test_spell_variant_activation_event_adds_new_spell(
        self,
        runtime_bot: Bot,
    ):
        runtime_bot.event_manager.process_msg(
            SpellsEvent(human_spells=[SpellItem(spell_id=100, spell_level=1, available=True)])
        )

        runtime_bot.event_manager.process_msg(SpellVariantActivationEvent(spell_id=12744, effective=True))

        assert [spell.spell_id for spell in runtime_bot.game_state.fight.spells] == [100, 12744]

    def test_spell_variant_activation_event_ignores_non_effective(
        self,
        runtime_bot: Bot,
    ):
        runtime_bot.event_manager.process_msg(
            SpellsEvent(human_spells=[SpellItem(spell_id=100, spell_level=1, available=True)])
        )

        runtime_bot.event_manager.process_msg(SpellVariantActivationEvent(spell_id=12744, effective=False))

        assert [spell.spell_id for spell in runtime_bot.game_state.fight.spells] == [100]

    def test_spell_variant_activation_event_ignores_already_known_spell(
        self,
        runtime_bot: Bot,
    ):
        runtime_bot.event_manager.process_msg(
            SpellsEvent(human_spells=[SpellItem(spell_id=12744, spell_level=1, available=True)])
        )

        runtime_bot.event_manager.process_msg(SpellVariantActivationEvent(spell_id=12744, effective=True))

        assert [spell.spell_id for spell in runtime_bot.game_state.fight.spells] == [12744]

    def test_fight_placement_positions_for_challenger(
        self,
        runtime_bot: Bot,
    ):
        self._set_player_actor_cell(runtime_bot, cell_id=100)

        runtime_bot.event_manager.process_msg(
            FightPlacementPossiblePositionsEvent(
                starting_positions=FightStartingPositions(
                    challengers_positions=[100, 101, 102],
                    defenders_positions=[200, 201, 202],
                )
            )
        )

        assert runtime_bot.game_state.fight.fight_placement_possible_positions == [
            100,
            101,
            102,
        ]

    def test_fight_placement_positions_for_defender(
        self,
        runtime_bot: Bot,
    ):
        self._set_player_actor_cell(runtime_bot, cell_id=200)

        runtime_bot.event_manager.process_msg(
            FightPlacementPossiblePositionsEvent(
                starting_positions=FightStartingPositions(
                    challengers_positions=[100, 101, 102],
                    defenders_positions=[200, 201, 202],
                )
            )
        )

        assert runtime_bot.game_state.fight.fight_placement_possible_positions == [
            200,
            201,
            202,
        ]

    def test_ordered_stat_uses_breed_tie_breaker(
        self,
        runtime_bot: Bot,
    ):
        runtime_bot.game_state.fight.breed_id = BreedEnum.SACRIER
        runtime_bot.event_manager.process_msg(
            CharacterCharacteristicsEvent(
                stats=CharacterCharacteristics(
                    characteristics=[
                        self._detailed_characteristic(CharacteristicEnum.CHANCE, base=100),
                        self._detailed_characteristic(CharacteristicEnum.INTELLIGENCE, base=100),
                    ]
                )
            )
        )

        assert runtime_bot.game_state.fight.primary_and_second_elem == (
            EffectElement.CHANCE,
            EffectElement.INTELLIGENCE,
        )

    def test_character_characteristics_event_syncs_life_points(
        self,
        runtime_bot: Bot,
    ):
        fight_frame = self._get_fight_frame(runtime_bot)
        logger = MagicMock()
        fight_frame.logger = logger

        runtime_bot.event_manager.process_msg(
            CharacterCharacteristicsEvent(
                stats=CharacterCharacteristics(
                    characteristics=[
                        self._detailed_characteristic(CharacteristicEnum.LIFE_POINTS, base=245),
                        self._detailed_characteristic(
                            CharacteristicEnum.VITALITY,
                            objects_and_mount_bonus=55,
                        ),
                        self._detailed_characteristic(CharacteristicEnum.CUR_LIFE, base=0),
                    ]
                )
            )
        )

        assert runtime_bot.game_state.fight.life_point == 300
        assert runtime_bot.game_state.fight.max_life_point == 300
        logger.info.assert_called_with(
            "Player HP resync: source=CharacterCharacteristicsEvent, "
            "hp_before=1, life_points=245, vitality=55, cur_life=0, "
            "hp_after=300, max_hp_after=300"
        )

    def test_fight_refresh_without_life_stat_keeps_current_player_life(
        self,
        runtime_bot: Bot,
    ) -> None:
        fight_frame = self._get_fight_frame(runtime_bot)
        logger = MagicMock()
        fight_frame.logger = logger
        runtime_bot.game_state.player.character_id = 8921612638
        runtime_bot.event_manager.process_msg(
            CharacterCharacteristicsEvent(
                stats=CharacterCharacteristics(
                    characteristics=[
                        self._detailed_characteristic(CharacteristicEnum.LIFE_POINTS, base=245),
                        self._detailed_characteristic(
                            CharacteristicEnum.VITALITY,
                            objects_and_mount_bonus=55,
                        ),
                        self._detailed_characteristic(CharacteristicEnum.CUR_LIFE, base=0),
                    ]
                )
            )
        )
        runtime_bot.game_state.fight.life_point = 245
        logger.reset_mock()

        runtime_bot.event_manager.process_msg(
            FightRefreshCharacterStatsEvent(
                fighter_id=8921612638,
                stats=FightCharacteristics(
                    characteristics=[self._detailed_characteristic(CharacteristicEnum.ACTION_POINTS, base=12)]
                ),
            )
        )

        assert runtime_bot.game_state.fight.life_point == 245
        assert runtime_bot.game_state.fight.get_stat_by_id(CharacteristicEnum.ACTION_POINTS) == 12
        logger.info.assert_not_called()

    def test_fight_refresh_with_cur_life_resyncs_player_life(
        self,
        runtime_bot: Bot,
    ) -> None:
        fight_frame = self._get_fight_frame(runtime_bot)
        logger = MagicMock()
        fight_frame.logger = logger
        runtime_bot.game_state.player.character_id = 8921612638
        runtime_bot.event_manager.process_msg(
            CharacterCharacteristicsEvent(
                stats=CharacterCharacteristics(
                    characteristics=[
                        self._detailed_characteristic(CharacteristicEnum.LIFE_POINTS, base=245),
                        self._detailed_characteristic(
                            CharacteristicEnum.VITALITY,
                            objects_and_mount_bonus=55,
                        ),
                        self._detailed_characteristic(CharacteristicEnum.CUR_LIFE, base=0),
                    ]
                )
            )
        )
        runtime_bot.game_state.fight.life_point = 200
        logger.reset_mock()

        runtime_bot.event_manager.process_msg(
            FightRefreshCharacterStatsEvent(
                fighter_id=8921612638,
                stats=FightCharacteristics(
                    characteristics=[self._detailed_characteristic(CharacteristicEnum.CUR_LIFE, base=-55)]
                ),
            )
        )

        assert runtime_bot.game_state.fight.life_point == 245
        assert runtime_bot.game_state.fight.max_life_point == 300
        logger.info.assert_called_once_with(
            "Player HP resync: source=FightRefreshCharacterStatsEvent, "
            "hp_before=200, life_points=245, vitality=55, cur_life=-55, "
            "hp_after=245, max_hp_after=300"
        )

    def test_partial_life_refresh_waits_for_complete_characteristic_cache(
        self,
        runtime_bot: Bot,
    ) -> None:
        fight_frame = self._get_fight_frame(runtime_bot)
        logger = MagicMock()
        fight_frame.logger = logger
        player_id = 8921612638
        runtime_bot.game_state.player.character_id = player_id
        runtime_bot.game_state.fight.life_point = 200
        runtime_bot.game_state.fight.max_life_point = 300

        runtime_bot.event_manager.process_msg(
            FightRefreshCharacterStatsEvent(
                fighter_id=player_id,
                stats=FightCharacteristics(
                    characteristics=[self._detailed_characteristic(CharacteristicEnum.CUR_LIFE, base=-55)]
                ),
            )
        )

        assert runtime_bot.game_state.fight.life_point == 200
        assert runtime_bot.game_state.fight.max_life_point == 300
        assert runtime_bot.game_state.fight.get_stat_by_id(CharacteristicEnum.CUR_LIFE) == -55
        logger.info.assert_not_called()

    def test_fight_refresh_after_player_death_resyncs_life(
        self,
        runtime_bot: Bot,
    ) -> None:
        fight_frame = self._get_fight_frame(runtime_bot)
        logger = MagicMock()
        fight_frame.logger = logger
        player_id = 8921612638
        runtime_bot.game_state.player.character_id = player_id
        runtime_bot.event_manager.process_msg(
            CharacterCharacteristicsEvent(
                stats=CharacterCharacteristics(
                    characteristics=[
                        self._detailed_characteristic(CharacteristicEnum.LIFE_POINTS, base=285),
                        self._detailed_characteristic(
                            CharacteristicEnum.VITALITY,
                            objects_and_mount_bonus=56,
                        ),
                        self._detailed_characteristic(CharacteristicEnum.CUR_LIFE, base=-333),
                    ]
                )
            )
        )
        runtime_bot.event_manager.process_msg(
            GameActionFightEvent(death=GameActionFightEvent.Death(target_id=player_id))
        )
        logger.reset_mock()

        runtime_bot.event_manager.process_msg(
            FightRefreshCharacterStatsEvent(
                fighter_id=player_id,
                stats=FightCharacteristics(
                    characteristics=[self._detailed_characteristic(CharacteristicEnum.CUR_LIFE, base=-329)]
                ),
            )
        )

        assert runtime_bot.game_state.fight.life_point == 12
        assert runtime_bot.game_state.fight.get_stat_by_id(CharacteristicEnum.CUR_LIFE) == -329
        logger.info.assert_called_once_with(
            "Player HP resync: source=FightRefreshCharacterStatsEvent, "
            "hp_before=0, life_points=285, vitality=56, cur_life=-329, "
            "hp_after=12, max_hp_after=341"
        )

    def test_game_action_fight_cast_request_increments_count_by_spell_id(
        self,
        runtime_bot: Bot,
    ):
        runtime_bot.event_manager.process_msg(
            GameActionFightCastRequest(
                spell_id=42,
                cell=100,
            )
        )
        runtime_bot.event_manager.process_msg(
            GameActionFightCastRequest(
                spell_id=42,
                cell=100,
            )
        )

        assert runtime_bot.game_state.fight.count_casted_by_spell_id_on_current_turn[42] == 2

    def test_life_points_gain_and_loss_update_player_life(
        self,
        runtime_bot: Bot,
    ):
        runtime_bot.game_state.fight.life_point = 500
        runtime_bot.game_state.player.character_id = 123
        runtime_bot.game_state.entity.actor_fight_by_id[runtime_bot.game_state.player.character_id] = (
            FightActor(life_point=runtime_bot.game_state.fight.life_point, is_summoned=False)
        )

        runtime_bot.event_manager.process_msg(
            GameActionFightEvent(
                life_points_gain=GameActionFightEvent.LifePointsGain(
                    target_id=123,
                    delta=100,
                )
            )
        )

        runtime_bot.event_manager.process_msg(
            GameActionFightEvent(
                life_points_lost=GameActionFightEvent.LifePointsLost(
                    target_id=123,
                    loss=150,
                )
            )
        )

        assert runtime_bot.game_state.fight.life_point == 450

    def test_fatal_player_life_loss_clamps_to_zero(
        self,
        runtime_bot: Bot,
    ) -> None:
        fight_frame = self._get_fight_frame(runtime_bot)
        logger = MagicMock()
        fight_frame.logger = logger
        runtime_bot.game_state.player.character_id = 123
        runtime_bot.game_state.fight.life_point = 10

        fight_frame.on_game_action_fight_event(
            GameActionFightEvent(
                source_id=456,
                life_points_lost=GameActionFightEvent.LifePointsLost(
                    target_id=123,
                    loss=25,
                    permanent_damages=3,
                    element_id=2,
                    shield_loss=4,
                ),
            )
        )

        assert runtime_bot.game_state.fight.life_point == 0
        logger.info.assert_called_once_with(
            "Player HP loss event: hp_before=10, loss=25, shield_loss=4, "
            "permanent_damages=3, element_id=2, hp_after=0, source_id=456, "
            "target_id=123 (fatal damage clamped to 0 HP)"
        )

    def test_enemy_without_monster_data_raises_diagnostic_assertion(
        self,
        runtime_bot: Bot,
    ) -> None:
        runtime_bot.game_state.player.character_id = 123
        runtime_bot.game_state.entity.set_actor(make_fighter(123, 100, team=Team.TEAM_CHALLENGER))
        runtime_bot.game_state.entity.set_actor(make_fighter(-1, 200, team=Team.TEAM_DEFENDER))
        runtime_bot.game_state.entity.actor_fight_by_id[-1] = FightActor(
            life_point=10,
            is_summoned=False,
        )

        with pytest.raises(AssertionError) as assertion:
            runtime_bot.game_state.get_attack_context()

        message = str(assertion.value)
        assert "Enemy fighter has no known monster data" in message
        assert "actor_id=-1" in message
        assert "monster_gid=0" in message
        assert "fighter_kind=unknown" in message

    def test_zero_life_enemy_remains_in_attack_context_until_death(
        self,
        runtime_bot: Bot,
    ) -> None:
        set_game_state(
            runtime_bot.game_state, player_cell_id=100, enemy_cell_ids=[200], include_spell_ids=[]
        )
        runtime_bot.event_manager.process_msg(
            GameActionFightEvent(
                life_points_lost=GameActionFightEvent.LifePointsLost(target_id=0, loss=1000)
            )
        )

        attack_context = runtime_bot.game_state.get_attack_context()

        assert [enemy.actor_id for enemy in attack_context.enemy_actors] == [0]
        assert len(attack_context.enemies_data) == 1
        assert attack_context.enemies_data[0].life_point == 0
        assert runtime_bot.game_state.fight.get_enemies(-1) == attack_context.enemy_actors

        runtime_bot.event_manager.process_msg(
            GameActionFightEvent(death=GameActionFightEvent.Death(target_id=0))
        )

        context_after_death = runtime_bot.game_state.get_attack_context()
        assert context_after_death.enemy_actors == []
        assert context_after_death.enemies_data == []
        assert 0 not in runtime_bot.game_state.entity.actor_fight_by_id

    def test_attack_context_counts_own_active_summons(
        self,
        runtime_bot: Bot,
    ) -> None:
        runtime_bot.game_state.player.character_id = 123
        runtime_bot.game_state.entity.set_actor(make_fighter(123, 100, team=Team.TEAM_CHALLENGER))
        runtime_bot.game_state.entity.set_actor(make_fighter(456, 101, team=Team.TEAM_CHALLENGER))
        runtime_bot.game_state.entity.actor_fight_by_id[456] = FightActor(life_point=10, is_summoned=True)
        runtime_bot.game_state.entity.set_actor(make_fighter(789, 102, team=Team.TEAM_CHALLENGER))
        runtime_bot.game_state.entity.actor_fight_by_id[789] = FightActor(life_point=0, is_summoned=True)

        attack_context = runtime_bot.game_state.get_attack_context()

        assert attack_context.own_active_summon_count == 1

    def test_attack_context_keeps_actor_snapshot_after_live_state_is_cleared(
        self,
        runtime_bot: Bot,
    ) -> None:
        set_game_state(
            runtime_bot.game_state,
            player_cell_id=100,
            enemy_cell_ids=[200],
        )

        attack_context = runtime_bot.game_state.get_attack_context()
        runtime_bot.game_state.entity.clear_actors()

        assert attack_context.player_map_point == MapPoint.from_cell_id(100)
        assert set(attack_context.actor_by_id) == {-1, 0}
        assert [enemy.actor_id for enemy in attack_context.enemy_actors] == [0]

    def test_missing_life_characteristic_logs_before_assertion(
        self,
        runtime_bot: Bot,
    ) -> None:
        logger = MagicMock()
        runtime_bot.game_state.fight.logger = logger
        runtime_bot.game_state.fight.update_characteristic(
            self._detailed_characteristic(CharacteristicEnum.LIFE_POINTS, base=245)
        )
        runtime_bot.game_state.fight.update_characteristic(
            self._detailed_characteristic(
                CharacteristicEnum.VITALITY,
                objects_and_mount_bonus=55,
            )
        )

        with pytest.raises(AssertionError):
            runtime_bot.game_state.fight.sync_life_points_from_characteristics()

        log_message = logger.error.call_args.args[0]
        assert log_message.startswith("Cannot sync player HP from characteristics: ")
        assert "available_characteristic_ids=" in log_message

    def test_invalid_life_characteristic_sync_logs_before_assertion(
        self,
        runtime_bot: Bot,
    ) -> None:
        logger = MagicMock()
        runtime_bot.game_state.fight.logger = logger
        runtime_bot.game_state.fight.update_characteristic(
            self._detailed_characteristic(CharacteristicEnum.LIFE_POINTS, base=245)
        )
        runtime_bot.game_state.fight.update_characteristic(
            self._detailed_characteristic(CharacteristicEnum.VITALITY)
        )
        runtime_bot.game_state.fight.update_characteristic(
            self._detailed_characteristic(CharacteristicEnum.CUR_LIFE, base=-300)
        )

        with pytest.raises(AssertionError):
            runtime_bot.game_state.fight.sync_life_points_from_characteristics()

        logger.error.assert_called_once_with(
            "Player HP characteristic sync would violate invariants: "
            "life_points=245, vitality=0, cur_life=-300, "
            "max_life_point=245, life_point=-55"
        )

    def test_missing_non_player_fight_actor_logs_before_key_error(
        self,
        runtime_bot: Bot,
    ) -> None:
        fight_frame = self._get_fight_frame(runtime_bot)
        logger = MagicMock()
        fight_frame.logger = logger
        runtime_bot.game_state.player.character_id = 123

        with pytest.raises(KeyError):
            fight_frame.on_game_action_fight_event(
                GameActionFightEvent(
                    source_id=123,
                    life_points_lost=GameActionFightEvent.LifePointsLost(
                        target_id=-11,
                        loss=70,
                    ),
                )
            )

        logger.error.assert_called_once_with(
            "Non-player HP loss targets missing fight actor: target_id=-11, loss=70, source_id=123"
        )

    def test_life_points_lost_death_and_cur_life_sync_update_player_life(
        self,
        runtime_bot: Bot,
    ):
        runtime_bot.game_state.player.character_id = 8921612638
        runtime_bot.event_manager.process_msg(
            CharacterCharacteristicsEvent(
                stats=CharacterCharacteristics(
                    characteristics=[
                        self._detailed_characteristic(CharacteristicEnum.LIFE_POINTS, base=245),
                        self._detailed_characteristic(
                            CharacteristicEnum.VITALITY,
                            objects_and_mount_bonus=55,
                        ),
                        self._detailed_characteristic(CharacteristicEnum.CUR_LIFE, base=0),
                    ]
                )
            )
        )

        runtime_bot.event_manager.process_msg(
            GameActionFightEvent(
                life_points_lost=GameActionFightEvent.LifePointsLost(
                    target_id=8921612638,
                    loss=7,
                    permanent_damages=1,
                    element_id=0,
                )
            )
        )

        assert runtime_bot.game_state.fight.life_point == 293

        runtime_bot.event_manager.process_msg(
            GameActionFightEvent(
                death=GameActionFightEvent.Death(
                    target_id=8921612638,
                    source_id=0,
                )
            )
        )

        assert runtime_bot.game_state.fight.life_point == 0

        runtime_bot.event_manager.process_msg(
            CharacterCharacteristicsEvent(
                stats=CharacterCharacteristics(
                    characteristics=[
                        self._detailed_characteristic(CharacteristicEnum.LIFE_POINTS, base=245),
                        self._detailed_characteristic(
                            CharacteristicEnum.VITALITY,
                            objects_and_mount_bonus=55,
                        ),
                        self._detailed_characteristic(CharacteristicEnum.CUR_LIFE, base=-150),
                    ]
                )
            )
        )

        assert runtime_bot.game_state.fight.life_point == 150
        assert runtime_bot.game_state.fight.max_life_point == 300

    def _set_player_actor_cell(
        self,
        runtime_bot: Bot,
        *,
        cell_id: int,
    ) -> None:
        runtime_bot.game_state.player.character_id = 1
        runtime_bot.game_state.entity.set_actor(
            ActorPositionInformation(
                actor_id=1,
                disposition=EntityDisposition(
                    cell_id=cell_id,
                    entity_id=1,
                ),
            )
        )

    @staticmethod
    def _get_fight_frame(runtime_bot: Bot) -> FightFrame:
        fight_frame = next(frame for frame in runtime_bot.frames if isinstance(frame, FightFrame))
        return fight_frame

    def _detailed_characteristic(
        self,
        characteristic_id: CharacteristicEnum,
        *,
        base: int = 0,
        objects_and_mount_bonus: int = 0,
    ) -> CharacterCharacteristic:
        return CharacterCharacteristic(
            characteristic_id=characteristic_id,
            detailed=CharacterCharacteristicDetailed(
                base=base,
                objects_and_mount_bonus=objects_and_mount_bonus,
            ),
        )

    def test_ferveur_targets_living_enemy_with_shield_effect(self, game_state_ctx: GameStateContext) -> None:
        from src.core.engine.fights.attack.buff import get_valid_self_buff_spells_for_turn

        game_state = game_state_ctx.game_state
        set_game_state(
            game_state, player_cell_id=79, enemy_cell_ids=[93], include_spell_ids=[], movement_point=0
        )
        game_state.fight.spells = [SpellItem(spell_id=14676, spell_level=1, available=True)]
        game_state.fight.characteristic_by_id[CharacteristicEnum.ACTION_POINTS] = CharacterCharacteristic(
            characteristic_id=CharacteristicEnum.ACTION_POINTS,
            value=CharacterCharacteristicValue(total=2),
        )
        game_state.entity.actor_fight_by_id[0].life_point = 46
        context = game_state.get_attack_context()
        attack = game_state_ctx.attacker.find_best_attack_from_mp(context)

        assert attack is not None
        caster, spell, target = attack
        assert caster == context.player_map_point
        assert spell.spellId == 14676
        assert target == MapPoint.from_cell_id(93)
        assert any(
            candidate.spellId == 14676 for candidate, _, _ in get_valid_self_buff_spells_for_turn(context)
        )

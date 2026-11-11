from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from google.protobuf.message import Message

from DBDofusUnity.datas.protos.non_obf.game.account_pb2 import (
    AccountCapabilitiesEvent,
    AccountInformationUpdateEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.achievement_pb2 import (
    AchievedAchievement,
    Achievement,
    AchievementDetailsRequest,
    AchievementFinishedInformationEvent,
    AchievementRewardRequest,
    AchievementRewardResultEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.alliance_member_pb2 import (
    AllianceInvitationRequest,
    AllianceKickRequest,
    AllianceMemberLeavingEvent,
    AllianceMembershipEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.alliance_rank_pb2 import (
    AllianceRankChangeRequest,
    AllianceRankCreationRequest,
    AllianceRankDeletionRequest,
    AllianceRightsUpdateRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.bak_pb2 import (
    BakActionEvent,
    BakActionRequest,
    BakApiKeyEvent,
    BakBuyValidationEvent,
    BakShopTokenEvent,
    BakTransactionValidationEvent,
    BakTransactionValidationRequest,
    BidAction,
)
from DBDofusUnity.datas.protos.non_obf.game.basic_pb2 import (
    BasicLatencyStatsRequest,
    SequenceNumberRequest,
    TextInformationEvent,
    TimeEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.challenge_pb2 import (
    ChallengeNumberEvent,
    ChallengeProposalEvent,
    ChallengeResultEvent,
    ChallengeSelectionRequest,
    ChallengeTargetsRequest,
    ChallengeValidateRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.character_management_pb2 import (
    CharacterSelectionRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.character_pb2 import (
    CharacterCharacteristicsEvent,
    CharacterCharacteristicUpgradeRequest,
    CharacterLevelUpEvent,
    UpdateLifePointsEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.chat_pb2 import (
    ChatChannelMessageEvent,
    ChatChannelMessageRequest,
    ChatPrivateMessageRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.client_verification_pb2 import ClientChallengeInitRequest
from DBDofusUnity.datas.protos.non_obf.game.common_pb2 import (
    ActorPositionInformation,
    AlignmentInformation,
    CharacterCharacteristic,
    CharacterCharacteristicDetailedUsable,
    CharacterCharacteristics,
    EntityDisposition,
    FighterMonsterLightInformation,
    FightInvisibilityState,
    FightRemovableEffect,
    FightStartingPositions,
    InteractiveElement,
    MapCoordinates,
    MonsterInGroupInformation,
    ObjectEffect,
    ObjectItem,
    ObjectItemInventory,
    ObjectUidWithQuantity,
    SpellModifier,
    StatedElement,
)
from DBDofusUnity.datas.protos.non_obf.game.connection_pb2 import IdentificationRequest
from DBDofusUnity.datas.protos.non_obf.game.contact_pb2 import (
    AcquaintanceInformation,
    ContactLevelUpEvent,
    FriendDeleteRequest,
    FriendInformation,
    UnBlockRequest,
    UnIgnoreRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.context_pb2 import (
    ContextReadyRequest,
    ContextRemoveElementEvent,
    ContextRemoveElementsEvent,
    EntitiesDispositionEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.exchange_pb2 import (
    BidItem,
    ExchangeBidHouseInListAddedEvent,
    ExchangeBidHouseItemAddedEvent,
    ExchangeBidHouseItemRemovedEvent,
    ExchangeBidHousePriceRequest,
    ExchangeBidHouseSearchRequest,
    ExchangeBidPriceEvent,
    ExchangeCraftCountModifiedEvent,
    ExchangeCraftCountRequest,
    ExchangeCraftStartedEvent,
    ExchangeKamaModifiedEvent,
    ExchangeMoveKamaRequest,
    ExchangeObjectModifyPricedRequest,
    ExchangeObjectMovePricedRequest,
    ExchangeObjectMoveRequest,
    ExchangeObjectsAddedEvent,
    ExchangePlayerRequest,
    ExchangeReadyEvent,
    ExchangeSellRequest,
    ExchangeSetCraftRecipeRequest,
    ExchangeStartedWithMultiTabStorageEvent,
    ExchangeStartedWithStorageEvent,
    ExchangeTypesItemsExchangerDescriptionForUserEvent,
    ObjectAveragePricesEvent,
    SellingConditions,
)
from DBDofusUnity.datas.protos.non_obf.game.fight_pb2 import (
    FightEndEvent,
    FightFighterShowEvent,
    FightIsTurnReadyEvent,
    FightMapInformationResponse,
    FightNewRoundEvent,
    FightNewWaveEvent,
    FightRefreshCharacterStatsEvent,
    FightSynchronizeEvent,
    FightTurnEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.fight_preparation_pb2 import (
    FightPlacementPositionRequest,
    FightPlacementPossiblePositionsEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.game_action_pb2 import (
    EntitySpawnInformation,
    GameActionAcknowledgementRequest,
    GameActionFightCastRequest,
    GameActionFightEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.gamemap_pb2 import (
    FightMapInformationEvent,
    GameRolePlayShowActorsEvent,
    MapChangeOrientationEvent,
    MapChangeRequest,
    MapComplementaryInformationEvent,
    MapCurrentEvent,
    MapInformationRequest,
    MapMovementCancelRequest,
    MapMovementEvent,
    MapMovementRefusedEvent,
    MapMovementRequest,
    MapObstacle,
    MapTeleportOnSameEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.guild_chest_pb2 import GuildChestTabSelectRequest
from DBDofusUnity.datas.protos.non_obf.game.guild_member_pb2 import (
    GuildMemberLeaveEvent,
    GuildMemberOnlineStatusEvent,
    GuildMemberParametersChangeRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.guild_mission_pb2 import ServerMaintenanceEvent
from DBDofusUnity.datas.protos.non_obf.game.guild_rank_pb2 import (
    GuildRankCreateRequest,
    GuildRankRemoveRequest,
    GuildRightsUpdateRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.haven_bag_pb2 import Element, HavenBagEnterRequest
from DBDofusUnity.datas.protos.non_obf.game.interactive_element_pb2 import (
    InteractiveElementUpdatedEvent,
    InteractiveUsedEvent,
    InteractiveUseEndedEvent,
    InteractiveUseErrorEvent,
    InteractiveUseRequest,
    StatedElementUpdatedEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.inventory_pb2 import (
    InventoryContentEvent,
    InventoryWeightEvent,
    KamasUpdateEvent,
    ObjectAddedEvent,
    ObjectDeletedEvent,
    ObjectModifiedEvent,
    ObjectMovementEvent,
    ObjectQuantityEvent,
    ObjectsAddedEvent,
    ObjectsDeletedEvent,
    ObjectsQuantityEvent,
    ObjectUseRequest,
    StorageInventoryContentEvent,
    StorageObjectRemovedEvent,
    StorageObjectsRemovedEvent,
    StorageObjectsUpdateEvent,
    StorageObjectUpdateEvent,
    StorageTab,
)
from DBDofusUnity.datas.protos.non_obf.game.job_pb2 import JobExperience, JobExperiencesUpdateEvent
from DBDofusUnity.datas.protos.non_obf.game.npc_pb2 import (
    NpcDialogQuestionEvent,
    NpcDialogReplyRequest,
    NpcGenericActionRequest,
    NpcsMapQuestStatusUpdateEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.quest_pb2 import (
    QuestActive,
    QuestObjective,
    QuestObjectiveFollowRequest,
    QuestObjectiveUnfollowRequest,
    QuestObjectiveValidatedEvent,
    QuestObjectiveValidationRequest,
    QuestsEvent,
    QuestsFollowedOrderRefreshRequest,
    QuestStartedEvent,
    QuestStartRequest,
    QuestStepInformationRequest,
    QuestStepStartedEvent,
    QuestStepValidatedEvent,
    QuestValidatedEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.roleplay_pb2 import (
    AggressionEvent,
    AttackMonsterRequest,
    DelayedActionEvent,
    DelayedActionFinishedEvent,
    FightRequestCanceledEvent,
    MonsterAngryAtPlayerEvent,
    MonsterNotAngryAtPlayerEvent,
    PlayerFightFriendlyAnsweredEvent,
    PlayerFightFriendlyAnswerRequest,
    PlayerFightFriendlyRequestedEvent,
    PlayerFightRequest,
    RemoveChallengeEvent,
    SpellAnimEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.spell_pb2 import (
    SpellItem,
    SpellsEvent,
    SpellVariantActivationEvent,
)
from DBDofusUnity.datas.protos.non_obf.game.taxcollector_pb2 import (
    TaxCollectorAddedEvent,
    TaxCollectorAttackedEvent,
    TaxCollectorAttackResultEvent,
    TaxCollectorHarvestedEvent,
    TaxCollectorMovement,
    TaxCollectorOrderedSpellMoveRequest,
    TaxCollectorOrderedSpellRemoveRequest,
)
from DBDofusUnity.datas.protos.non_obf.game.teleportation_pb2 import (
    TeleportDestination,
    TeleportDestinationsEvent,
    TeleportRequest,
    ZaapKnownListEvent,
)
from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.data_center.i18n import I18N
from DBDofusUnity.dofus_unity_reader.game_constants.directions import DirectionsEnum
from DBDofusUnity.dofus_unity_reader.grid.map_point import MAP_POINT_BY_CELL_ID
from DBDofusUnity.proto_mapper_assembly.helpers.utils import strict_validate_call

T = TypeVar("T")

ValidatorFn = Callable[[T], bool]


MAX_LATENCY_MS = 600_000
MAX_CRAFT_COUNT = 10_000
MAX_AMOUNT_KAMAS = 1_000_000_000
MAX_ITEM_QUANTITY = 100_000_000
MAX_ITEM_MOVE_QUANTITY = 1_000_000
MAX_POSITION_INVENTORY = 63
MAX_TAB_NUMBER = 5
MAX_COOLDOWN_SPELL = 12
MAX_TYPE_ITEMS = 1_000
MIN_NICKNAME_LENGTH = 2
TAX_PERCENTAGE = 2.0
TAX_UPDATE_PERCENTAGE = 1.0
MAX_DURATION = 100
MAX_DOFUS_LEVEL = 200
MAX_MONSTER_LEVEL = 500
MAX_ACTION_ID = 500
MAX_AGE_BONUS = 100
MAX_FIGHT_REWARD_RATE = 100
MIN_FIGHT_DURATION_MS = 200
MAX_POSSIBLE_INVENTORY_MAX_WEIGHT = 50_000
MAX_GRADE = 5
MAX_ROUND_NUMBER = 200
MAX_WAVE_ID = 50
MAX_TURN_TIME_MS = 60_000
MAX_MOOD_SMILEY_ID = 50
MAX_ACHIEVEMENT_ID = 100_000


from pydantic import validate_call


@validate_call
def is_valid_tab_number(value: int) -> bool:
    return value > 0 and value <= MAX_TAB_NUMBER


@strict_validate_call
def is_valid_cell_id(value: int) -> bool:
    return value in MAP_POINT_BY_CELL_ID or value == -1


@strict_validate_call
def is_valid_cell_x(value: int) -> bool:
    return value in DataReader().POSSIBLE_COORD_X


@strict_validate_call
def is_valid_cell_y(value: int) -> bool:
    return value in DataReader().POSSIBLE_COORD_Y


@strict_validate_call
def is_valid_world_x_coordinates(value: int) -> bool:
    return value in DataReader().get_all_x_world_coodinates()


@strict_validate_call
def is_valid_world_y_coordinates(value: int) -> bool:
    return value in DataReader().get_all_y_world_coodinates()


@strict_validate_call
def is_valid_map_id(value: int) -> bool:
    return value in DataReader().get_all_map_ids()


@strict_validate_call
def is_valid_sub_area_id(value: int) -> bool:
    return value in DataReader().get_all_sub_area_ids()


@strict_validate_call
def is_valid_effect_id(value: int) -> bool:
    return value in DataReader().effect_by_id


@strict_validate_call
def is_valid_direction(value: int) -> bool:
    return value in {direction.value for direction in DirectionsEnum}


@strict_validate_call
def is_valid_runtime_direction(value: int | str) -> bool:
    if isinstance(value, int):
        return is_valid_direction(value)
    return value in {direction.name for direction in DirectionsEnum}


@strict_validate_call
def is_valid_bak_bid_action(value: int | str) -> bool:
    if isinstance(value, int):
        return value != BidAction.BID_INVALID_ACTION and value in BidAction.values()
    return value != "BID_INVALID_ACTION" and value in BidAction.keys()


@strict_validate_call
def is_valid_bak_bid_validation(value: int | str) -> bool:
    if isinstance(value, int):
        return value in BakTransactionValidationEvent.BidValidation.values()
    return value in BakTransactionValidationEvent.BidValidation.keys()


@strict_validate_call
def is_valid_actor_id(value: int) -> bool:
    return value != 0


@strict_validate_call
def is_valid_skill_id(value: int) -> bool:
    return value in DataReader().get_all_skill_ids()


@strict_validate_call
def is_valid_name_id(value: int) -> bool:
    return value in I18N().name_by_id


@strict_validate_call
def is_valid_element_state(value: int) -> bool:
    return value in DataReader().POSSIBLE_ELEMENT_STATES


@strict_validate_call
def is_valid_characteristic_id(value: int) -> bool:
    # Some characteristic IDs are absent from DataReader; accept the observed 0..1000 range.
    return value in DataReader().characteristic_by_id or 0 <= value < 1000


@strict_validate_call
def is_valid_positive(value: int | float) -> bool:
    return value >= 0


@strict_validate_call
def is_valid_strict_positive(value: int | float) -> bool:
    return value > 0


@strict_validate_call
def is_valid_cooldown_spell(value: int) -> bool:
    return value >= 0 and value <= MAX_COOLDOWN_SPELL


@strict_validate_call
def is_valid_gid(value: int) -> bool:
    return value in DataReader().get_all_item_ids()


@strict_validate_call
def is_valid_monster_gid(value: int) -> bool:
    return value in DataReader().get_all_monster_gids()


@strict_validate_call
def is_valid_job_id(value: int) -> bool:
    return value in DataReader().get_all_job_ids()


@strict_validate_call
def is_valid_key_cells(value: list[int]) -> bool:
    all_key_cells = DataReader().get_all_key_cells()
    return all(key_cell in all_key_cells for key_cell in value)


@strict_validate_call
def is_valid_amount_of_kamas(value: int) -> bool:
    return value <= MAX_AMOUNT_KAMAS and value >= 0


@strict_validate_call
def is_valid_move_of_kamas(value: int) -> bool:
    # Bank-to-inventory kama transfers can be negative.
    return -MAX_AMOUNT_KAMAS < value < MAX_AMOUNT_KAMAS


@strict_validate_call
def is_valid_craft_count(value: int) -> bool:
    return value > 0 and value <= MAX_CRAFT_COUNT


@strict_validate_call
def is_valid_latency_ms(value: int) -> bool:
    return value >= 0 and value <= MAX_LATENCY_MS


@strict_validate_call
def is_valid_sale_hotel_quantity(value: int) -> bool:
    return value in {1, 10, 100, 1000}


@strict_validate_call
def is_valid_prefix(value: str) -> bool:
    if len(value) > 5:
        return False
    return all(not c.isdigit() and c not in " ?!." for c in value)


@strict_validate_call
def is_valid_position(value: int) -> bool:
    return value <= MAX_POSITION_INVENTORY and value >= 0


@strict_validate_call
def is_valid_positive_quantity(value: int) -> bool:
    return value >= 0 and value < MAX_ITEM_QUANTITY


@strict_validate_call
def is_valid_total_quantity(value: int) -> bool:
    return value > -(MAX_ITEM_QUANTITY // 2) and value < (MAX_ITEM_QUANTITY // 2)


@strict_validate_call
def is_valid_total_move_quantity(value: int) -> bool:
    return value > -(MAX_ITEM_MOVE_QUANTITY // 2) and value < (MAX_ITEM_MOVE_QUANTITY // 2)


@strict_validate_call
def is_valid_type_item(value: int) -> bool:
    return value > 0 and value < MAX_TYPE_ITEMS


@strict_validate_call
def is_valid_spell_id(value: int) -> bool:
    # Weapon casts use zero.
    return value in DataReader().get_all_spell_ids() or value == 0


@strict_validate_call
def is_valid_spell_numero(value: int) -> bool:
    return value in DataReader().SPELL_NUMEROS


@strict_validate_call
def is_valid_nickname(value: str) -> bool:
    if len(value) <= MIN_NICKNAME_LENGTH:
        return False
    return all(not c.isdigit() and c not in " ?!.[]" for c in value)


@strict_validate_call
def is_valid_date_iso(value: str) -> bool:
    normalized = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return True


@strict_validate_call
def is_valid_tax_percentage(value: int | float) -> bool:
    return value == TAX_PERCENTAGE


@strict_validate_call
def is_valid_tax_update_percentage(value: int | float) -> bool:
    return value == TAX_UPDATE_PERCENTAGE


@strict_validate_call
def is_valid_duration(value: int | float) -> bool:
    return value >= 0 and value < MAX_DURATION


@strict_validate_call
def is_valid_fight_reward_rate(value: int) -> bool:
    return value == -1 or 0 <= value <= MAX_FIGHT_REWARD_RATE


@strict_validate_call
def is_valid_fight_duration_ms(value: int) -> bool:
    return value > MIN_FIGHT_DURATION_MS


@strict_validate_call
def is_valid_fight_loot_share_limit_malus(value: int) -> bool:
    return 0 <= value <= MAX_DOFUS_LEVEL


@strict_validate_call
def is_valid_max_item_lvl(value: int) -> bool:
    return value in {MAX_DOFUS_LEVEL, 60}


@strict_validate_call
def is_valid_max_item_per_account(value: int) -> bool:
    return value >= 1 and value <= ((MAX_DOFUS_LEVEL + 1) + MAX_DOFUS_LEVEL * 5)


@strict_validate_call
def is_valid_unsold_delay(value: int) -> bool:
    return value <= 24 * 28


@strict_validate_call
def is_valid_unsold_delay_second(value: int) -> bool:
    return value >= 0 and value <= 60 * 60 * 24 * 28


@strict_validate_call
def is_valid_age_bonus(value: int) -> bool:
    return value >= 0 and value <= MAX_AGE_BONUS


@strict_validate_call
def is_valid_action_id(value: int) -> bool:
    return value >= 0 and value <= MAX_ACTION_ID


@strict_validate_call
def is_valid_npc_id(value: int) -> bool:
    # Static NPC IDs are positive; monster NPC IDs can be negative.
    return value in DataReader().npc_by_id or value < 0


@strict_validate_call
def is_valid_npc_action_id(value: int) -> bool:
    return value in DataReader().npc_action_by_id


@strict_validate_call
def is_valid_inventory_weight(value: int) -> bool:
    return value >= 0 and value <= MAX_POSSIBLE_INVENTORY_MAX_WEIGHT


@strict_validate_call
def is_valid_grade(value: int) -> bool:
    return value >= 0 and value <= MAX_GRADE


@strict_validate_call
def is_valid_summon_grade(value: int) -> bool:
    return value > 0 and is_valid_grade(value)


@strict_validate_call
def is_valid_monster_level(value: int) -> bool:
    return value >= 1 and value <= MAX_MONSTER_LEVEL


@strict_validate_call
def is_not_empty_str(value: str) -> bool:
    return value != ""


@strict_validate_call
def is_not_empty_list(value: list[Any]) -> bool:
    return value != []


@strict_validate_call
def is_empty_list(value: list[Any]) -> bool:
    return value == []


@strict_validate_call
def is_non_empty_dict(value: dict[Any, Any]) -> bool:
    return value != {}


@strict_validate_call
def is_valid_char_usable_base(value: int) -> bool:
    return value in {3, 6, 7}


@strict_validate_call
def is_valid_char_usable_objects_bonus(value: int) -> bool:
    return value >= 0 and value < 6


@strict_validate_call
def is_valid_char_usable_context_mod(value: int) -> bool:
    return value <= 0 and value >= -12


@strict_validate_call
def is_valid_level(value: int) -> bool:
    return value >= 1 and value <= MAX_DOFUS_LEVEL


@strict_validate_call
def is_valid_cost_zaap(value: int) -> bool:
    return value >= 0 and value < 20_000


@strict_validate_call
def is_valid_ticket_key(value: str) -> bool:
    return len(value) == 32


@strict_validate_call
def is_valid_langage_code(value: str) -> bool:
    return value == "fr"


@strict_validate_call
def is_valid_timestamp(value: int) -> bool:
    try:
        datetime.fromtimestamp(value / 1000, tz=UTC)
    except ValueError, OSError:
        return False
    return value > 0


@strict_validate_call
def is_valid_timezone_offset(value: int) -> bool:
    return value % 60 == 0


@strict_validate_call
def is_valid_quest_id(value: int) -> bool:
    return value in DataReader().quest_by_id


@strict_validate_call
def is_valid_quest_objective_id(value: int) -> bool:
    return value in DataReader().quest_objective_by_id


@strict_validate_call
def is_valid_round_number(value: int) -> bool:
    return 1 <= value <= MAX_ROUND_NUMBER


@strict_validate_call
def is_valid_wave_id(value: int) -> bool:
    return 0 <= value <= MAX_WAVE_ID


@strict_validate_call
def is_valid_turn_time_ms(value: int) -> bool:
    return 0 <= value <= MAX_TURN_TIME_MS


@strict_validate_call
def is_valid_mood_smiley_id(value: int) -> bool:
    return 0 <= value <= MAX_MOOD_SMILEY_ID


@strict_validate_call
def is_valid_achievement_id(value: int) -> bool:
    return -1 <= value <= MAX_ACHIEVEMENT_ID


@strict_validate_call
def is_valid_player_id(value: int) -> bool:
    return value > 0


@strict_validate_call
def is_valid_account_id(value: int) -> bool:
    return value > 0


@strict_validate_call
def is_valid_fight_id(value: int) -> bool:
    return value > 0


@strict_validate_call
def is_valid_invisibility_state(value: int) -> bool:
    return value in FightInvisibilityState.values()


@strict_validate_call
def is_valid_dissipation_state(value: int) -> bool:
    return value in FightRemovableEffect.EffectDissipationState.values()


def is_list_of[T](validator: ValidatorFn[T]) -> Callable[[list[T]], bool]:
    @strict_validate_call
    def _list_of(value: list[T]) -> bool:
        return all(validator(part_value) for part_value in value)

    return _list_of


def is_non_empty_list_of[T](validator: ValidatorFn[T]) -> Callable[[list[T]], bool]:
    @strict_validate_call
    def _non_empty_list_of(value: list[T]) -> bool:
        return bool(value) and all(validator(part_value) for part_value in value)

    return _non_empty_list_of


_MonsterFighter = (
    ActorPositionInformation.ActorInformation.FightFighterInformation.AIFighterInformation.MonsterFighter
)
_InteractiveElementSkill = InteractiveElement.InteractiveElementSkill
_CarryCharacter = GameActionFightEvent.CarryCharacter
_ThrowCharacter = GameActionFightEvent.ThrowCharacter
_DropCharacter = GameActionFightEvent.DropCharacter
_TeleportOnSameMap = GameActionFightEvent.TeleportOnSameMap
_ExchangePositions = GameActionFightEvent.ExchangePositions
_InvisibleDetected = GameActionFightEvent.InvisibleDetected
_Invisibility = GameActionFightEvent.Invisibility
_SpellRemove = GameActionFightEvent.SpellRemove
_TargetedAbility = GameActionFightEvent.TargetedAbility
_Slide = GameActionFightEvent.Slide
_SpellCoolDownVariation = GameActionFightEvent.SpellCoolDownVariation
_SpellImmunity = GameActionFightEvent.SpellImmunity
_LifePointsGain = GameActionFightEvent.LifePointsGain
_LifePointsLost = GameActionFightEvent.LifePointsLost
_SpellCast = GameActionFightEvent.TargetedAbility.SpellCast
_EntitySpawnMonster = EntitySpawnInformation.Monster
_EntitySpawnCharacter = EntitySpawnInformation.CharacterEntity
_EntitySpawnCompanion = EntitySpawnInformation.Companion
_NpcWithQuest = NpcsMapQuestStatusUpdateEvent.MapNpcQuest.NpcWithQuest
_AchievementObjective = Achievement.AchievementObjective
_QuestActiveDetails = QuestActive.Details
_FriendOnlineInformation = FriendInformation.FriendOnlineInformation
_AcquaintanceOnlineInformation = AcquaintanceInformation.OnlineInformation
_QuestFinished = QuestsEvent.QuestFinished
_ObjectAveragePrice = ObjectAveragePricesEvent.ObjectAveragePrice


VALIDATORS_ON_FIELD: dict[type[Message], dict[str, ValidatorFn[Any]]] = {
    # account_pb2
    AccountInformationUpdateEvent: {"subscription_end_date": is_valid_timestamp},
    AccountCapabilitiesEvent: {"account_id": is_valid_account_id},
    # achievement_pb2
    AchievementDetailsRequest: {"achievement_id": is_valid_achievement_id},
    AchievementRewardRequest: {"achievement_id": is_valid_achievement_id},
    AchievementRewardResultEvent: {"achievement_id": is_valid_achievement_id},
    Achievement: {"achievement_id": is_valid_achievement_id},
    _AchievementObjective: {"objective_id": is_valid_strict_positive},
    AchievedAchievement: {
        "achievement_id": is_valid_achievement_id,
        "achieved_by": is_valid_player_id,
    },
    AchievementFinishedInformationEvent: {"player_id": is_valid_player_id},
    # alliance_member_pb2
    AllianceInvitationRequest: {"character_id": is_valid_player_id},
    AllianceKickRequest: {"kicked_id": is_valid_player_id},
    AllianceMembershipEvent: {"rank_id": is_valid_strict_positive},
    AllianceMemberLeavingEvent: {"member_id": is_valid_player_id},
    # alliance_rank_pb2
    AllianceRankCreationRequest: {"parent_rank_id": is_valid_strict_positive},
    AllianceRankDeletionRequest: {
        "rank_id": is_valid_strict_positive,
        "replacement_rank_id": is_valid_strict_positive,
    },
    AllianceRankChangeRequest: {
        "member_id": is_valid_player_id,
        "rank_id": is_valid_strict_positive,
    },
    AllianceRightsUpdateRequest: {"rank_id": is_valid_strict_positive},
    # basic_pb2
    BasicLatencyStatsRequest: {"latency": is_valid_latency_ms},
    SequenceNumberRequest: {"number": is_valid_positive},
    TextInformationEvent: {"message_id": is_valid_strict_positive},
    TimeEvent: {
        "timestamp": is_valid_timestamp,
        "timezone_offset": is_valid_timezone_offset,
    },
    # bak_pb2
    BakApiKeyEvent: {"token": is_not_empty_str},
    BakShopTokenEvent: {"token": is_not_empty_str},
    BakActionRequest: {
        "kamas": is_valid_strict_positive,
        "ogrines": is_valid_strict_positive,
        "rate": is_valid_strict_positive,
        "bid_action": is_valid_bak_bid_action,
    },
    BakActionEvent: {
        "kamas": is_valid_strict_positive,
        "amount": is_valid_strict_positive,
        "rate": is_valid_strict_positive,
        "bid_action": is_valid_bak_bid_action,
        "transaction_uuid": is_not_empty_str,
    },
    BakTransactionValidationRequest: {"transaction_uuid": is_not_empty_str},
    BakTransactionValidationEvent: {
        "bid_action": is_valid_bak_bid_action,
        "result": is_valid_bak_bid_validation,
    },
    BakBuyValidationEvent: {
        "transaction_validation": is_non_empty_dict,
        "amount": is_valid_strict_positive,
        "email": is_not_empty_str,
    },
    # character_pb2
    UpdateLifePointsEvent: {"life_points": is_valid_positive, "max_life_points": is_valid_strict_positive},
    CharacterCharacteristicUpgradeRequest: {
        "strength": is_valid_positive,
        "vitality": is_valid_positive,
        "wisdom": is_valid_positive,
        "chance": is_valid_positive,
        "agility": is_valid_positive,
        "intelligence": is_valid_positive,
    },
    CharacterCharacteristicsEvent: {"stats": is_non_empty_dict},
    CharacterLevelUpEvent: {"new_level": is_valid_level},
    # challenge_pb2
    ChallengeTargetsRequest: {"challenge_id": is_valid_strict_positive},
    ChallengeSelectionRequest: {"challenge_id": is_valid_strict_positive},
    ChallengeValidateRequest: {"challenge_id": is_valid_strict_positive},
    ChallengeResultEvent: {"challenge_id": is_valid_strict_positive},
    ChallengeNumberEvent: {"challenge_number": is_valid_strict_positive},
    ChallengeProposalEvent: {"timer": is_valid_positive},
    # character_management_pb2
    CharacterSelectionRequest: {"character_id": is_valid_strict_positive},
    # chat_pb2
    ChatChannelMessageEvent: {
        "sender_name": is_valid_nickname,
        "sender_prefix": is_valid_prefix,
        "date": is_valid_date_iso,
    },
    ChatChannelMessageRequest: {"content": is_not_empty_str},
    ChatPrivateMessageRequest: {"name": is_valid_nickname},
    # client_verification_pb2
    ClientChallengeInitRequest: {"challenge_key": is_not_empty_str},
    # common_pb2
    CharacterCharacteristics: {
        "experience": is_valid_positive,
        "experience_level_floor": is_valid_positive,
        "experience_next_level_floor": is_valid_strict_positive,
        "experience_bonus_limit": is_valid_strict_positive,
        "kamas": is_valid_amount_of_kamas,
    },
    SpellModifier: {"spell_id": is_valid_spell_id},
    FightRemovableEffect: {
        "uid": is_valid_strict_positive,
        "spell_id": is_valid_spell_id,
        "dissipation_state": is_valid_dissipation_state,
    },
    MapCoordinates: {
        "world_x": is_valid_world_x_coordinates,
        "world_y": is_valid_world_y_coordinates,
    },
    ActorPositionInformation: {"actor_id": is_valid_actor_id},
    EntityDisposition: {
        "cell_id": is_valid_cell_id,
        "direction": is_valid_runtime_direction,
    },
    StatedElement: {"cell_id": is_valid_cell_id, "state": is_valid_element_state},
    FightStartingPositions: {
        "challengers_positions": is_list_of(is_valid_cell_id),
        "defenders_positions": is_list_of(is_valid_cell_id),
    },
    ObjectItemInventory: {"position": is_valid_position},
    ObjectItem: {
        "quantity": is_valid_positive_quantity,
        "gid": is_valid_gid,
        "uid": is_valid_strict_positive,
    },
    ObjectUidWithQuantity: {
        "object_uid": is_valid_strict_positive,
        "quantity": is_valid_total_quantity,
    },
    CharacterCharacteristic: {"characteristic_id": is_valid_characteristic_id},
    MonsterInGroupInformation: {
        "gid": is_valid_monster_gid,
        "grade": is_valid_grade,
        "level": is_valid_monster_level,
    },
    ObjectEffect: {"action": is_valid_effect_id},
    CharacterCharacteristicDetailedUsable: {
        "base": is_valid_char_usable_base,
        "objects_and_mount_bonus": is_valid_char_usable_objects_bonus,
        "context_modification": is_valid_char_usable_context_mod,
        "used": is_valid_positive,
    },
    # common_pb2
    _MonsterFighter: {
        "monster_gid": is_valid_monster_gid,
        "creature_grade": is_valid_grade,
    },
    # common_pb2
    _InteractiveElementSkill: {
        "skill_id": is_valid_skill_id,
        "skill_instance_uid": is_valid_strict_positive,
        "name_id": is_valid_name_id,
    },
    FighterMonsterLightInformation: {"monster_gid": is_valid_monster_gid},
    AlignmentInformation: {"character_level": is_valid_level},
    # contact_pb2
    FriendDeleteRequest: {"account_id": is_valid_account_id},
    UnIgnoreRequest: {"account_id": is_valid_account_id},
    UnBlockRequest: {"account_id": is_valid_account_id},
    FriendInformation: {"account_id": is_valid_account_id},
    _FriendOnlineInformation: {
        "character_id": is_valid_player_id,
        "character_level": is_valid_level,
        "breed_id": is_valid_strict_positive,
        "mood_smiley_id": is_valid_mood_smiley_id,
        "achievement_points": is_valid_positive,
    },
    AcquaintanceInformation: {"account_id": is_valid_account_id},
    _AcquaintanceOnlineInformation: {
        "character_id": is_valid_player_id,
        "mood_smiley_id": is_valid_mood_smiley_id,
    },
    ContactLevelUpEvent: {
        "character_id": is_valid_player_id,
        "level": is_valid_level,
    },
    # context_pb2
    ContextReadyRequest: {"map_id": is_valid_map_id},
    ContextRemoveElementEvent: {"element_id": is_valid_actor_id},
    ContextRemoveElementsEvent: {"element_id": is_non_empty_list_of(is_valid_actor_id)},
    EntitiesDispositionEvent: {"dispositions": is_not_empty_list},
    # exchange_pb2
    BidItem: {
        "quantity": is_valid_positive_quantity,
        "gid": is_valid_gid,
        "uid": is_valid_strict_positive,
    },
    ExchangeBidHouseSearchRequest: {"object_gid": is_valid_gid},
    ExchangeBidPriceEvent: {"object_gid": is_valid_gid},
    SellingConditions: {
        "quantities": is_list_of(is_valid_sale_hotel_quantity),
        "types": is_list_of(is_valid_type_item),
        "tax_percentage": is_valid_tax_percentage,
        "tax_modification_percentage": is_valid_tax_update_percentage,
        "max_item_level": is_valid_max_item_lvl,
        "max_item_per_account": is_valid_max_item_per_account,
        "unsold_delay": is_valid_unsold_delay,
    },
    ExchangeStartedWithMultiTabStorageEvent: {"tab_number": is_valid_tab_number},
    ExchangeSellRequest: {
        "quantity": is_valid_positive_quantity,
        "object_uid": is_valid_strict_positive,
    },
    ExchangeObjectMoveRequest: {
        "quantity": is_valid_total_move_quantity,
        "object_uid": is_valid_strict_positive,
    },
    ExchangeObjectsAddedEvent: {"objects": is_not_empty_list},
    ExchangeStartedWithStorageEvent: {"storage_max_slot": is_valid_strict_positive},
    ExchangeTypesItemsExchangerDescriptionForUserEvent: {"object_gid": is_valid_gid},
    ExchangeBidHousePriceRequest: {"object_gid": is_valid_gid},
    ExchangeObjectModifyPricedRequest: {
        "object_uid": is_valid_strict_positive,
        "quantity": is_valid_sale_hotel_quantity,
        "price": is_valid_amount_of_kamas,
    },
    ExchangeCraftCountModifiedEvent: {"count": is_valid_craft_count},
    ExchangeCraftCountRequest: {"count": is_valid_craft_count},
    ExchangeCraftStartedEvent: {"skill_id": is_valid_skill_id},
    ExchangeKamaModifiedEvent: {"quantity": is_valid_amount_of_kamas},
    ExchangeMoveKamaRequest: {"quantity": is_valid_move_of_kamas},
    ExchangePlayerRequest: {"target_id": is_valid_strict_positive},
    ExchangeReadyEvent: {"character_id": is_valid_strict_positive},
    ExchangeSetCraftRecipeRequest: {"object_uid": is_valid_strict_positive},
    ObjectAveragePricesEvent: {"objects_average_prices": is_not_empty_list},
    _ObjectAveragePrice: {"object_gid": is_valid_gid},
    ExchangeObjectMovePricedRequest: {
        "object_uid": is_valid_strict_positive,
        "quantity": is_valid_sale_hotel_quantity,
        "price": is_valid_amount_of_kamas,
    },
    ExchangeBidHouseItemAddedEvent: {
        "price": is_valid_amount_of_kamas,
        "unsold_delay": is_valid_unsold_delay_second,
    },
    ExchangeBidHouseInListAddedEvent: {"object_gid": is_valid_gid},
    ExchangeBidHouseItemRemovedEvent: {"sell_id": is_valid_strict_positive},
    # fight_pb2
    FightEndEvent: {
        "duration": is_valid_fight_duration_ms,
        "reward_rate": is_valid_fight_reward_rate,
        "loot_share_limit_malus": is_valid_fight_loot_share_limit_malus,
    },
    FightNewRoundEvent: {"round_number": is_valid_round_number},
    FightFighterShowEvent: {"information": is_non_empty_dict},
    FightIsTurnReadyEvent: {"character_id": is_valid_actor_id},
    FightTurnEvent: {
        "base_time": is_valid_turn_time_ms,
        "extra_time": is_valid_turn_time_ms,
        "remaining_time": is_valid_turn_time_ms,
    },
    FightNewWaveEvent: {
        "wave_id": is_valid_wave_id,
        "turn_left_before_next_wave": is_valid_positive,
    },
    FightMapInformationResponse: {"map_id": is_valid_map_id},
    FightRefreshCharacterStatsEvent: {
        "fighter_id": is_valid_actor_id,
        "stats": is_non_empty_dict,
    },
    FightSynchronizeEvent: {"fighters": is_not_empty_list},
    # fight_preparation_pb2
    FightPlacementPositionRequest: {"cell_id": is_valid_cell_id},
    FightPlacementPossiblePositionsEvent: {"starting_positions": is_non_empty_dict},
    # game_action_pb2
    SequenceEndEvent: {"action_id": is_valid_action_id},
    SequenceStartEvent: {"author_id": is_valid_actor_id},
    GameActionFightCastRequest: {
        "spell_id": is_valid_spell_id,
        "cell": is_valid_cell_id,
    },
    _EntitySpawnMonster: {
        "monster_gid": is_valid_monster_gid,
        "grade": is_valid_summon_grade,
        "level": is_valid_monster_level,
    },
    _EntitySpawnCharacter: {
        "name": is_not_empty_str,
        "level": is_valid_level,
    },
    _EntitySpawnCompanion: {
        "model_id": is_valid_strict_positive,
        "level": is_valid_level,
        "owner_id": is_valid_actor_id,
    },
    GameActionAcknowledgementRequest: {"action_id": is_valid_action_id},
    # game_action_pb2
    _CarryCharacter: {"cell": is_valid_cell_id},
    _ThrowCharacter: {"cell": is_valid_cell_id},
    _DropCharacter: {"cell": is_valid_cell_id},
    _TeleportOnSameMap: {"cell": is_valid_cell_id},
    _ExchangePositions: {
        "caster_cell_id": is_valid_cell_id,
        "target_cell_id": is_valid_cell_id,
    },
    _InvisibleDetected: {"cell": is_valid_cell_id},
    _Invisibility: {"invisibility_state": is_valid_invisibility_state},
    _SpellRemove: {"spell_id": is_valid_spell_id},
    _TargetedAbility: {
        "target_id": is_valid_actor_id,
        "destination_cell": is_valid_cell_id,
    },
    _Slide: {"start_cell": is_valid_cell_id, "end_cell": is_valid_cell_id},
    _SpellCoolDownVariation: {
        "spell_id": is_valid_spell_id,
        "value": is_valid_cooldown_spell,
    },
    _SpellImmunity: {"spell_id": is_valid_spell_id},
    _LifePointsGain: {"delta": is_valid_strict_positive},
    _LifePointsLost: {"loss": is_valid_strict_positive},
    # game_action_pb2
    _SpellCast: {"spell_id": is_valid_spell_id},
    # gamemap_pb2
    MapMovementCancelRequest: {"cell_id": is_valid_cell_id},
    MapMovementEvent: {"cells": is_list_of(is_valid_cell_id)},
    MapChangeRequest: {"map_id": is_valid_map_id},
    MapCurrentEvent: {"map_id": is_valid_map_id},
    MapMovementRequest: {"map_id": is_valid_map_id, "key_cells": is_valid_key_cells},
    MapInformationRequest: {"map_id": is_valid_map_id},
    FightMapInformationEvent: {
        "subarea_id": is_valid_sub_area_id,
        "map_id": is_valid_map_id,
    },
    GameRolePlayShowActorsEvent: {"actors": is_not_empty_list},
    MapTeleportOnSameEvent: {
        "cell_id": is_valid_cell_id,
        "player_id": is_valid_strict_positive,
    },
    MapMovementRefusedEvent: {"cell_x": is_valid_cell_x, "cell_y": is_valid_cell_y},
    MapObstacle: {"cell_id": is_valid_cell_id},
    MapChangeOrientationEvent: {"direction": is_valid_direction},
    MapComplementaryInformationEvent: {"map_id": is_valid_map_id},
    # guild_chest_pb2
    GuildChestTabSelectRequest: {"tab_number": is_valid_tab_number},
    # guild_member_pb2
    GuildMemberParametersChangeRequest: {
        "member_id": is_valid_player_id,
        "rank_id": is_valid_strict_positive,
        "experience_given_percent": is_valid_positive,
    },
    GuildMemberOnlineStatusEvent: {"member_id": is_valid_player_id},
    GuildMemberLeaveEvent: {"player_id": is_valid_player_id},
    ServerMaintenanceEvent: {"maintenance_date": is_valid_date_iso},
    # guild_rank_pb2
    GuildRankCreateRequest: {"parent_rank_id": is_valid_strict_positive},
    GuildRankRemoveRequest: {
        "rank_id": is_valid_strict_positive,
        "new_rank_id": is_valid_strict_positive,
    },
    GuildRightsUpdateRequest: {"rank_id": is_valid_strict_positive},
    # haven_bag_pb2
    HavenBagEnterRequest: {"owner": is_valid_strict_positive},
    Element: {"cell_id": is_valid_cell_id, "orientation": is_valid_direction},
    # interactive_element_pb2
    InteractiveUsedEvent: {"skill_id": is_valid_skill_id, "duration": is_valid_duration},
    InteractiveUseEndedEvent: {"skill_id": is_valid_skill_id},
    InteractiveElementUpdatedEvent: {"interactive_element": is_non_empty_dict},
    InteractiveUseRequest: {"skill_instance_uid": is_valid_strict_positive},
    InteractiveUseErrorEvent: {
        "element_id": is_valid_strict_positive,
        "skill_instance_uid": is_valid_strict_positive,
    },
    StatedElementUpdatedEvent: {"stated_element": is_non_empty_dict},
    # inventory_pb2
    ObjectDeletedEvent: {"object_uid": is_valid_strict_positive},
    ObjectsDeletedEvent: {"objects_uid": is_list_of(is_valid_strict_positive)},
    StorageTab: {"tab_number": is_valid_tab_number},
    ObjectAddedEvent: {"object": is_non_empty_dict},
    ObjectsAddedEvent: {"objects": is_not_empty_list},
    InventoryWeightEvent: {
        "inventory_weight": is_valid_inventory_weight,
        "weight_max": is_valid_inventory_weight,
    },
    InventoryContentEvent: {"kamas": is_valid_amount_of_kamas},
    StorageInventoryContentEvent: {"kamas": is_valid_amount_of_kamas},
    ObjectQuantityEvent: {"object": is_non_empty_dict},
    ObjectsQuantityEvent: {"object": is_not_empty_list},
    ObjectUseRequest: {"object_uid": is_valid_strict_positive},
    StorageObjectRemovedEvent: {"object_uid": is_valid_strict_positive},
    StorageObjectsRemovedEvent: {"objects_uid": is_list_of(is_valid_strict_positive)},
    StorageObjectUpdateEvent: {"object": is_non_empty_dict},
    StorageObjectsUpdateEvent: {"objects": is_not_empty_list},
    KamasUpdateEvent: {"quantity": is_valid_amount_of_kamas},
    ObjectModifiedEvent: {"object": is_non_empty_dict},
    ObjectMovementEvent: {
        "object_uid": is_valid_strict_positive,
        "position": is_valid_position,
    },
    # job_pb2
    JobExperience: {
        "job_id": is_valid_job_id,
        "job_level": is_valid_level,
        "job_xp": is_valid_positive,
        "job_xp_level_floor": is_valid_positive,
        "job_xp_next_level_floor": is_valid_strict_positive,
    },
    JobExperiencesUpdateEvent: {"experiences": is_not_empty_list},
    # npc_pb2
    NpcGenericActionRequest: {
        "npc_map_id": is_valid_map_id,
        "npc_id": is_valid_npc_id,
        "npc_action_id": is_valid_npc_action_id,
    },
    _NpcWithQuest: {"npc_id": is_valid_npc_id},
    NpcDialogReplyRequest: {"reply_id": is_valid_strict_positive},
    NpcDialogQuestionEvent: {"message_id": is_valid_strict_positive},
    # quest_pb2
    QuestStartRequest: {"quest_id": is_valid_quest_id},
    QuestStepInformationRequest: {"quest_id": is_valid_quest_id},
    QuestObjectiveValidationRequest: {
        "quest_id": is_valid_quest_id,
        "objective_id": is_valid_quest_objective_id,
    },
    QuestObjectiveFollowRequest: {
        "quest_id": is_valid_quest_id,
        "objective_id": is_valid_quest_objective_id,
    },
    QuestObjectiveUnfollowRequest: {
        "quest_id": is_valid_quest_id,
        "objective_id": is_valid_quest_objective_id,
    },
    QuestsFollowedOrderRefreshRequest: {"quests": is_list_of(is_valid_quest_id)},
    QuestsEvent: {"player_id": is_valid_player_id},
    _QuestFinished: {
        "quest_id": is_valid_quest_id,
        "finished_count": is_valid_strict_positive,
    },
    QuestStartedEvent: {"quest_id": is_valid_quest_id},
    QuestValidatedEvent: {"quest_id": is_valid_quest_id},
    QuestObjectiveValidatedEvent: {
        "quest_id": is_valid_quest_id,
        "objective_id": is_valid_quest_objective_id,
    },
    QuestStepValidatedEvent: {
        "quest_id": is_valid_quest_id,
        "step_id": is_valid_strict_positive,
    },
    QuestStepStartedEvent: {
        "quest_id": is_valid_quest_id,
        "step_id": is_valid_strict_positive,
    },
    QuestActive: {"quest_id": is_valid_quest_id},
    _QuestActiveDetails: {"step_id": is_valid_strict_positive},
    QuestObjective: {"objective_id": is_valid_quest_objective_id},
    # roleplay_pb2
    PlayerFightRequest: {
        "target_id": is_valid_player_id,
        "target_cell_id": is_valid_cell_id,
    },
    AttackMonsterRequest: {"monster_group_id": is_valid_actor_id},
    PlayerFightFriendlyAnswerRequest: {"fight_id": is_valid_fight_id},
    FightRequestCanceledEvent: {
        "fight_id": is_valid_fight_id,
        "source_id": is_valid_player_id,
        "target_id": is_valid_player_id,
    },
    AggressionEvent: {
        "attacker_id": is_valid_player_id,
        "defender_id": is_valid_player_id,
    },
    PlayerFightFriendlyRequestedEvent: {
        "fight_id": is_valid_fight_id,
        "source_id": is_valid_player_id,
        "target_id": is_valid_player_id,
    },
    PlayerFightFriendlyAnsweredEvent: {
        "fight_id": is_valid_fight_id,
        "source_id": is_valid_player_id,
        "target_id": is_valid_player_id,
    },
    MonsterAngryAtPlayerEvent: {
        "character_id": is_valid_player_id,
        "monster_group_id": is_valid_strict_positive,
        "angry_start_time": is_valid_timestamp,
        "attack_time": is_valid_timestamp,
    },
    MonsterNotAngryAtPlayerEvent: {
        "character_id": is_valid_player_id,
        "monster_group_id": is_valid_strict_positive,
    },
    RemoveChallengeEvent: {"fight_id": is_valid_fight_id},
    SpellAnimEvent: {
        "caster_id": is_valid_player_id,
        "target_cell_id": is_valid_cell_id,
        "spell_id": is_valid_spell_id,
        "spell_level": is_valid_strict_positive,
    },
    DelayedActionEvent: {
        "character_id": is_valid_player_id,
        "delayed_end_time": is_valid_timestamp,
    },
    DelayedActionFinishedEvent: {"character_id": is_valid_player_id},
    # spell_pb2
    SpellItem: {"spell_id": is_valid_spell_id, "spell_level": is_valid_spell_numero},
    SpellsEvent: {"human_spells": is_not_empty_list, "mutant_spells": is_empty_list},
    SpellVariantActivationEvent: {"spell_id": is_valid_spell_id},
    # taxcollector_pb2
    TaxCollectorOrderedSpellRemoveRequest: {"slot_id": is_valid_positive},
    TaxCollectorOrderedSpellMoveRequest: {
        "from_slot_id": is_valid_positive,
        "to_slot_id": is_valid_positive,
    },
    TaxCollectorAddedEvent: {"caller_id": is_valid_player_id},
    TaxCollectorAttackedEvent: {
        "first_name_id": is_valid_name_id,
        "last_name_id": is_valid_name_id,
    },
    TaxCollectorAttackResultEvent: {
        "first_name_id": is_valid_name_id,
        "last_name_id": is_valid_name_id,
    },
    TaxCollectorHarvestedEvent: {"harvester_id": is_valid_player_id},
    TaxCollectorMovement: {
        "first_name_id": is_valid_name_id,
        "last_name_id": is_valid_name_id,
        "player_id": is_valid_player_id,
    },
    # teleportation_pb2
    TeleportRequest: {"map_id": is_valid_map_id},
    ZaapKnownListEvent: {"destinations": is_list_of(is_valid_map_id)},
    TeleportDestinationsEvent: {"spawn_map_id": is_valid_map_id},
    TeleportDestination: {
        "map_id": is_valid_map_id,
        "subarea_id": is_valid_sub_area_id,
        "level": is_valid_level,
        "cost": is_valid_cost_zaap,
    },
    IdentificationRequest: {"ticket_key": is_valid_ticket_key, "language_code": is_valid_langage_code},
}


def _assert_validators_match_proto_descriptors(
    validators: dict[type[Message], dict[str, ValidatorFn[Any]]],
) -> None:
    errors: list[str] = []
    for message_cls, field_validators in validators.items():
        known_fields = set(message_cls.DESCRIPTOR.fields_by_name)
        unknown = set(field_validators) - known_fields
        if unknown:
            errors.append(f"{message_cls.__name__}: {sorted(unknown)} (known: {sorted(known_fields)})")
    if errors:
        raise RuntimeError("unknown field(s) in VALIDATORS_ON_FIELD:\n  " + "\n  ".join(errors))


_assert_validators_match_proto_descriptors(VALIDATORS_ON_FIELD)


VALIDATORS_BY_NON_OBF_MESSAGE_NAME: dict[str, dict[str, ValidatorFn[Any]]] = {
    message_cls.DESCRIPTOR.name: field_validators
    for message_cls, field_validators in VALIDATORS_ON_FIELD.items()
}

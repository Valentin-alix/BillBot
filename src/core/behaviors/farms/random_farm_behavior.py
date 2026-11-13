import random
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from threading import RLock

from DBDofusUnity.datas.protos.non_obf.game.gamemap_pb2 import (
    MapComplementaryInformationEvent,
)
from DBDofusUnity.dofus_unity_reader.data_center.data_reader import DataReader
from DBDofusUnity.dofus_unity_reader.models.world_graph import Edge
from src.consts import MIN_DATE
from src.core.behaviors.behavior import Behavior
from src.core.behaviors.movements.auto_trip.auto_trip_behavior import (
    AutoTripErrorCode,
)
from src.core.behaviors.movements.auto_trip.auto_trip_smart_behavior import (
    AutoTripSmartBehavior,
)
from src.core.behaviors.movements.edge_behavior import EdgeBehavior
from src.core.behaviors.movements.map_change_behavior import MapChangeError
from src.core.engine.movements.world.edge import draw_edge_path
from src.core.engine.weights.weight_drawer import draw_weight_on_map
from src.core.engine.weights.weighted_path import WeightedPath
from src.core.signals.world_signals import WorldSignals

PATH_LOCK = RLock()
MAX_CONSECUTIVE_UNEXPECTED_NEW_MAPS = 3
RECENT_MAP_HISTORY_SIZE = 4
OSCILLATION_REPEAT_THRESHOLD = 2
LAST_VISITED_BY_SERVER_AND_MAP: dict[tuple[int, int], datetime] = {}
EDGE_PATH_BY_SERVER_AND_CHARACTER: dict[tuple[int, int], tuple[Edge, ...]] = {}


@dataclass
class RandomFarmBehavior(Behavior):
    auto_trip_smart_behavior: AutoTripSmartBehavior
    edge_behavior: EdgeBehavior
    weighted_path: WeightedPath
    world_signals: WorldSignals
    report_status: Callable[[str], None]
    additional_weight_by_map_id: dict[int, float] = field(default_factory=dict[int, float], init=False)
    get_additional_weight_by_map_id: Callable[[int], float] = field(init=False, default=lambda _: 0)
    map_ids: set[int] = field(default_factory=set[int], init=False)
    _edge_path: list[Edge] | None = field(default=None, init=False)
    _consecutive_unexpected_new_maps: int = field(default=0, init=False)
    _recent_map_ids: deque[int] = field(
        default_factory=lambda: deque(maxlen=RECENT_MAP_HISTORY_SIZE), init=False
    )

    @property
    def edge_path(self) -> list[Edge] | None:
        return self._edge_path

    @edge_path.setter
    def edge_path(self, value: list[Edge] | None) -> None:
        self._edge_path = None if value is None else list(value)
        path_key = (
            self.game_state.player.server_id,
            self.game_state.player.character_id,
        )
        with PATH_LOCK:
            if self._edge_path is None:
                EDGE_PATH_BY_SERVER_AND_CHARACTER.pop(path_key, None)
            else:
                EDGE_PATH_BY_SERVER_AND_CHARACTER[path_key] = tuple(self._edge_path)

    def init_random_farm(
        self,
        area_id: int | None,
        sub_area_id: int | None,
        get_additional_weight_by_map_id: Callable[[int], float],
    ) -> None:
        self.get_additional_weight_by_map_id = get_additional_weight_by_map_id
        self.additional_weight_by_map_id.clear()
        self._consecutive_unexpected_new_maps = 0
        self._recent_map_ids.clear()
        self.edge_path = None
        self.world_signals.reset_weight.emit()
        self.map_ids = self.get_map_ids(area_id, sub_area_id)

    def stop(self) -> None:
        self.edge_path = None
        return super().stop()

    def run(self) -> None:
        if self._should_go_to_area():
            self.edge_path = None
            self.logger.info("go to area for farm")
            self.report_status("Calculating route to the farming area…")
            self.auto_trip_smart_behavior.start(
                callback=self.on_auto_trip_world_behavior_finished,
                parent=self,
                map_ids=self.map_ids,
            )
            self.report_status("Traveling to the farming area…")
            return

        if self._is_edge_path_empty():
            self._recalculate_edge_path()

        if self.edge_path is not None and len(self.edge_path) > 0:
            edge = self.edge_path[0]
            self.edge_behavior.start(
                callback=partial(self.on_edge_behavior_finished, edge=edge),
                parent=self,
                edge=edge,
            )

    def _should_go_to_area(self) -> bool:
        is_outside_farm_area = self._is_edge_path_empty() and self.game_state.map.map_id not in self.map_ids
        is_path_desynchronized = (
            self.edge_path is not None
            and len(self.edge_path) > 0
            and self.edge_path[0].m_from.m_mapId != self.game_state.map.map_id
        )
        return is_outside_farm_area or is_path_desynchronized

    def _is_edge_path_empty(self) -> bool:
        return self.edge_path is None or len(self.edge_path) == 0

    def _recalculate_edge_path(self) -> None:
        self.logger.debug("Planning next farm route")
        self.report_status("Calculating harvesting route…")
        with PATH_LOCK:
            self.edge_path = None
            self.edge_path = self.get_next_weighted_path()

        blocked_map_ids: set[int] = set()
        if (
            self.edge_path is not None
            and len(self.edge_path) > 0
            and self._is_oscillating(self.edge_path[0].m_to.m_mapId)
        ):
            self.logger.warning(
                f"Farm route keeps bouncing through {list(self._recent_map_ids)}; rerouting away from it"
            )
            blocked_map_ids = set(self._recent_map_ids)
            self.edge_path = None
            self._recent_map_ids.clear()

        if self.edge_path is None:
            self.auto_trip_smart_behavior.start(
                callback=self.on_auto_trip_world_behavior_finished,
                parent=self,
                map_ids=self.map_ids - {self.game_state.map.map_id} - blocked_map_ids,
            )
        else:
            draw_edge_path(self.world_signals, self.edge_path)
            self.report_status("Following farm route…")

    def _is_oscillating(self, next_map_id: int) -> bool:
        return self._recent_map_ids.count(next_map_id) >= OSCILLATION_REPEAT_THRESHOLD

    def get_map_ids(self, area_id: int | None, sub_area_id: int | None) -> set[int]:
        self.logger.info(f"init map ids based on area {area_id} and sub area {sub_area_id}")
        if sub_area_id is not None:
            return set(DataReader().sub_area_by_id[sub_area_id].mapIds)
        elif area_id is not None:
            self.logger.info(f"setting map id based on {area_id}, ignore sub area with too high level")
            map_ids: set[int] = set()
            for sub_area_id in DataReader().sub_areas_by_area_id[area_id]:
                if DataReader().sub_area_by_id[sub_area_id].level > (self.game_state.player.level + 40):
                    continue
                map_ids |= set(DataReader().sub_area_by_id[sub_area_id].mapIds)
            return map_ids
        return set(DataReader().sub_area_by_id[self.game_state.map.sub_area_id].mapIds)

    def on_edge_behavior_finished(self, error_code: str | None, edge: Edge) -> None:
        if self.edge_path is None:
            raise ValueError("edge path should not be none")
        if error_code is not None:
            self.edge_path = None
            if error_code is MapChangeError.UNEXPECTED_NEW_MAP:
                self._consecutive_unexpected_new_maps += 1
                if self._consecutive_unexpected_new_maps >= MAX_CONSECUTIVE_UNEXPECTED_NEW_MAPS:
                    return self._force_reconnect(
                        "Repeated unexpected map changes; forcing reconnect to resync"
                    )
            return self.finish(error_code)
        self._consecutive_unexpected_new_maps = 0
        remaining_path = list(self.edge_path)
        remaining_path.remove(edge)
        self.edge_path = remaining_path
        self._recent_map_ids.append(self.game_state.map.map_id)
        with PATH_LOCK:
            LAST_VISITED_BY_SERVER_AND_MAP[(self.game_state.player.server_id, self.game_state.map.map_id)] = (
                datetime.now()
            )
        self.event_manager.on(
            MapComplementaryInformationEvent,
            partial(
                self.on_map_complementary_information_event_after_edge,
                error_code=error_code,
            ),
            originator=self,
            once=True,
            override_on_self=True,
        )

    def on_map_complementary_information_event_after_edge(
        self, msg: MapComplementaryInformationEvent, error_code: str | None
    ) -> None:
        self.finish(error_code)

    def on_auto_trip_world_behavior_finished(self, error_code: str | None) -> None:
        if error_code is not None:
            self.edge_path = None
            self.logger.error(error_code)
            if (
                error_code is AutoTripErrorCode.PATH_NOT_FOUND
                and self.game_state.map.has_session_banned_transitions
            ):
                return self._force_reconnect(
                    "Path blocked by forbidden transitions; forcing reconnect to resync"
                )
            return self.finish(error_code)
        with PATH_LOCK:
            LAST_VISITED_BY_SERVER_AND_MAP[(self.game_state.player.server_id, self.game_state.map.map_id)] = (
                datetime.now()
            )
        self.finish(error_code)

    def _force_reconnect(self, reason: str) -> None:
        self.logger.warning(reason)
        self._consecutive_unexpected_new_maps = 0
        request_disconnect = self.event_manager.request_disconnect_callback
        assert request_disconnect is not None, "Blocked path recovery requires a disconnect callback"
        request_disconnect()

    def get_next_weighted_path(self) -> list[Edge] | None:
        cached_weight_by_map_id: dict[int, float] = {}
        path = self.weighted_path.beam_search_path(
            start_vertex=self.game_state.map.curr_vertex,
            context=self.game_state.get_world_transition_context(),
            get_weight_by_edge_func=self.get_weight_edge,
            weight_by_map_id=cached_weight_by_map_id,
        )[0]
        draw_weight_on_map(cached_weight_by_map_id, self.world_signals)
        if len(path) == 0:
            self.logger.warning("Did not found any path")
            return None

        return path

    def get_weight_edge(self, edge: Edge) -> float:
        if edge.m_to.m_mapId not in self.map_ids:
            return -1

        time_weight = self._calculate_time_weight(edge.m_to.m_mapId)
        additional_weight = self._get_cached_additional_weight(edge.m_to.m_mapId)
        randomness_factor = random.uniform(0.8, 1)
        competition_penalty = self._calculate_competition_penalty(edge)

        return (time_weight * (1 + additional_weight) * randomness_factor) / (1 + competition_penalty)

    def _calculate_time_weight(self, map_id: int) -> float:
        key = (self.game_state.player.server_id, map_id)
        with PATH_LOCK:
            last_visited = LAST_VISITED_BY_SERVER_AND_MAP.get(key, MIN_DATE)
        seconds_since_visit = (datetime.now() - last_visited).total_seconds()
        return min(seconds_since_visit, 3600) ** 2

    def _get_cached_additional_weight(self, map_id: int) -> float:
        if map_id not in self.additional_weight_by_map_id:
            self.additional_weight_by_map_id[map_id] = self.get_additional_weight_by_map_id(map_id)
        return self.additional_weight_by_map_id[map_id]

    def _calculate_competition_penalty(self, edge: Edge) -> int:
        server_id = self.game_state.player.server_id
        character_id = self.game_state.player.character_id
        with PATH_LOCK:
            competing_paths = [
                edge_path
                for (
                    path_server_id,
                    path_character_id,
                ), edge_path in EDGE_PATH_BY_SERVER_AND_CHARACTER.items()
                if path_server_id == server_id and path_character_id != character_id
            ]
        return sum(
            1 for competing_path in competing_paths for other_edge in competing_path if other_edge == edge
        )

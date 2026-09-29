"""感知/世界层：维护游戏世界状态与事件总线。

设计要点
--------
- ``WorldEvent`` 不可变（frozen dataclass），保证事件可安全回放与断言。
- ``EventBus`` 同时支持按类型订阅与全量订阅，附带环形历史缓冲。
- ``World.snapshot`` 只产出"某个 NPC 视角"的局部状态，为后续视野机制留口。
"""

from __future__ import annotations

import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional


class Weather(str, Enum):
    SUNNY = "sunny"
    CLOUDY = "cloudy"
    RAIN = "rain"
    SNOW = "snow"


WEATHERS = [Weather.SUNNY, Weather.SUNNY, Weather.CLOUDY, Weather.RAIN]
MINUTES_PER_TICK = 10
WEATHER_CHANGE_PROB = 0.08


@dataclass(frozen=True)
class WorldEvent:
    """世界中发生的一次事件。"""

    tick: int
    kind: str  # player_entered / player_spoke / item_given / npc_action / ...
    actor: str
    payload: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


@dataclass
class Location:
    id: str
    name: str
    x: int = 0
    y: int = 0


@dataclass
class Entity:
    id: str
    kind: str  # player / npc
    name: str
    location_id: str
    inventory: List[str] = field(default_factory=list)


EventHandler = Callable[[WorldEvent], None]


class EventBus:
    """极简发布/订阅总线，带事件历史。"""

    def __init__(self, history_size: int = 256) -> None:
        self._by_kind: Dict[str, List[EventHandler]] = defaultdict(list)
        self._catch_all: List[EventHandler] = []
        self.history: Deque[WorldEvent] = deque(maxlen=history_size)

    def subscribe(self, handler: EventHandler, kinds: Optional[Iterable[str]] = None) -> None:
        if kinds is None:
            self._catch_all.append(handler)
        else:
            for kind in kinds:
                self._by_kind[kind].append(handler)

    def publish(self, event: WorldEvent) -> None:
        self.history.append(event)
        for handler in self._by_kind.get(event.kind, []):
            handler(event)
        for handler in self._catch_all:
            handler(event)


class EventSlot:
    """世界随机事件槽：累积→触发→归零。"""

    DEFAULT_STEP = 0.05
    DEFAULT_THRESHOLD = 1.0

    def __init__(self, step: float = DEFAULT_STEP, threshold: float = DEFAULT_THRESHOLD):
        self.step = step
        self.threshold = threshold
        self.accumulation = 0.0
        self.trigger_count = 0

    def accumulate(self, amount: float = None) -> bool:
        """累积事件槽。amount 为 None 时使用默认步长。返回是否触发。"""
        self.accumulation += amount if amount is not None else self.step
        if self.accumulation >= self.threshold:
            self.accumulation = 0.0
            self.trigger_count += 1
            return True
        return False

    @property
    def progress(self) -> float:
        return max(0.0, min(self.accumulation / self.threshold, 1.0))


class World:
    """游戏世界状态与感知入口。"""

    _DEFAULT_ENV_EVENTS = [
        {"actor": "chen", "summary": "铁匠陈想起该去收矿石了"},
        {"actor": "lily", "summary": "莉莉盘算着新货的报价"},
        {"actor": "chen", "summary": "铁匠陈觉得炉火该添炭了"},
        {"actor": "lily", "summary": "莉莉在整理货架上的商品"},
    ]

    def __init__(self, locations: Optional[List[Location]] = None,
                 env_events: Optional[List[Dict[str, Any]]] = None,
                 event_slot: Optional[EventSlot] = None) -> None:
        self.tick_count = 0
        self.game_minute = 8 * 60  # 从早上 8:00 开始
        self.weather = Weather.SUNNY
        self.bus = EventBus()
        self.locations: Dict[str, Location] = {
            loc.id: loc for loc in (locations or self._default_locations())
        }
        self.entities: Dict[str, Entity] = {}
        self.event_slot = event_slot or EventSlot()
        self._env_events = env_events if env_events is not None else list(self._DEFAULT_ENV_EVENTS)

    # ------------------------------------------------------------------ #
    # 默认地图
    # ------------------------------------------------------------------ #
    @staticmethod
    def _default_locations() -> List[Location]:
        return [
            Location("forge", "铁匠铺", 120, 80),
            Location("market", "集市", 340, 90),
            Location("plaza", "中央广场", 230, 220),
        ]

    # ------------------------------------------------------------------ #
    # 时间与天气
    # ------------------------------------------------------------------ #
    @property
    def clock(self) -> str:
        return f"{self.game_minute // 60:02d}:{self.game_minute % 60:02d}"

    @property
    def hour(self) -> int:
        return self.game_minute // 60

    def tick(self, minutes: int = MINUTES_PER_TICK) -> None:
        """推进世界时间，可能触发天气变化事件。"""
        self.tick_count += 1
        self.game_minute = (self.game_minute + minutes) % (24 * 60)
        self.bus.publish(WorldEvent(self.tick_count, "time_passed", "world",
                                    {"clock": self.clock}))
        if random.random() < WEATHER_CHANGE_PROB:
            candidates = [w for w in WEATHERS if w is not self.weather]
            if candidates:
                self.weather = random.choice(candidates)
                self.bus.publish(WorldEvent(self.tick_count, "weather_changed", "world",
                                            {"weather": self.weather.value}))

        # G3: 事件槽累积，满则触发环境事件
        if self.event_slot.accumulate():
            self._publish_env_event()

    # ------------------------------------------------------------------ #
    # 随机事件槽（G3：世界活性）
    # ------------------------------------------------------------------ #
    def accumulate_event_slot(self, amount: float = None) -> bool:
        """外部调用（如 player_says）累积事件槽，满则触发环境事件。"""
        if self.event_slot.accumulate(amount):
            self._publish_env_event()
            return True
        return False

    def _publish_env_event(self):
        """确定性选择并发布环境事件（基于 trigger_count 取模）。"""
        idx = (self.event_slot.trigger_count - 1) % len(self._env_events)
        event_def = self._env_events[idx]
        self.bus.publish(WorldEvent(
            self.tick_count, "env_event", event_def["actor"],
            {"summary": event_def["summary"]},
        ))

    # ------------------------------------------------------------------ #
    # 实体与物品
    # ------------------------------------------------------------------ #
    def add_entity(self, entity: Entity, announce: bool = True) -> None:
        self.entities[entity.id] = entity
        if announce:
            self.bus.publish(WorldEvent(self.tick_count, "player_entered", entity.id,
                                        {"name": entity.name, "location": entity.location_id}))

    def move_entity(self, entity_id: str, location_id: str) -> bool:
        entity = self.entities.get(entity_id)
        if entity is None or location_id not in self.locations:
            return False
        old = entity.location_id
        entity.location_id = location_id
        self.bus.publish(WorldEvent(self.tick_count, "entity_moved", entity_id,
                                    {"from": old, "to": location_id}))
        return True

    def transfer_item(self, from_id: str, to_id: str, item: str) -> bool:
        src = self.entities.get(from_id)
        dst = self.entities.get(to_id)
        if src is None or dst is None or item not in src.inventory:
            return False
        src.inventory.remove(item)
        dst.inventory.append(item)
        self.bus.publish(WorldEvent(self.tick_count, "item_given", from_id,
                                    {"to": to_id, "item": item}))
        return True

    def entities_at(self, location_id: str) -> List[Entity]:
        return [e for e in self.entities.values() if e.location_id == location_id]

    # ------------------------------------------------------------------ #
    # 感知快照
    # ------------------------------------------------------------------ #
    def snapshot(self, viewer_id: Optional[str] = None) -> Dict[str, Any]:
        """产出世界状态快照。viewer_id 存在时只包含同地点实体的细节。"""
        viewer = self.entities.get(viewer_id) if viewer_id else None
        visible = self.entities_at(viewer.location_id) if viewer else list(self.entities.values())
        return {
            "clock": self.clock,
            "tick": self.tick_count,
            "weather": self.weather.value,
            "my_location": self.locations[viewer.location_id].name if viewer else None,
            "nearby": [
                {"id": e.id, "kind": e.kind, "name": e.name, "inventory": list(e.inventory)}
                for e in visible if e.id != viewer_id
            ],
        }

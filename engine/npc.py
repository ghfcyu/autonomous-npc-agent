"""NPC 装配层：人格配置 + 记忆 + 状态机 + 决策引擎的聚合根。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .actions import Action, ActionExecutor
from .inner_state import InnerState, StateUpdater
from .decision import DecisionEngine
from .llm.base import BaseLLMProvider
from .memory import MemorySystem
from .relationships import Relationship, RelationshipNetwork
from .states import NPCState, StateMachine
from .world import World, WorldEvent

DEFAULT_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "configs", "npcs")


@dataclass
class Persona:
    """NPC 人格配置（对应 configs/npcs/*.json）。"""

    id: str
    name: str
    role: str
    location_id: str
    personality: str = ""
    speech_style: str = ""
    backstory: str = ""
    likes: List[str] = field(default_factory=list)
    dislikes: List[str] = field(default_factory=list)
    greeting_bank: List[str] = field(default_factory=list)
    fallback_bank: List[str] = field(default_factory=list)
    sleep_mumble: str = "（呼呼大睡）"
    schedule: Dict[str, str] = field(default_factory=dict)
    topic_responses: Dict[str, List[str]] = field(default_factory=dict)
    tags: Dict[str, float] = field(default_factory=dict)
    appearance: Dict[str, str] = field(default_factory=dict)
    residence: str = ""
    temperament: str = ""  # 脾气掩码 id（T1 粗粒度先行，T2 落地 8 掩码后替换）

    @classmethod
    def from_file(cls, path: str) -> "Persona":
        with open(path, encoding="utf-8") as fh:
            data: Dict[str, Any] = json.load(fh)
        return cls(**data)

    @classmethod
    def load_all(cls, config_dir: str = DEFAULT_CONFIG_DIR) -> List["Persona"]:
        personas: List[Persona] = []
        if os.path.isdir(config_dir):
            for fname in sorted(os.listdir(config_dir)):
                if fname.endswith(".json"):
                    personas.append(cls.from_file(os.path.join(config_dir, fname)))
        return personas

    def schedule_state(self, hour: int) -> Optional[NPCState]:
        """按作息表取该小时的状态；未命中返回 None（保持现状）。

        配置里写的是状态名（如 "SLEEPING"），也兼容直接写中文值。
        """
        state = self.schedule.get(str(hour))
        if not state:
            return None
        try:
            return NPCState[state]  # 按枚举名查找
        except KeyError:
            try:
                return NPCState(state)  # 按枚举值查找
            except ValueError:
                return None

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "role": self.role,
                "location_id": self.location_id, "personality": self.personality,
                "speech_style": self.speech_style, "tags": self.tags,
                "residence": self.residence, "temperament": self.temperament}


class NPC:
    """一个活着的 NPC：感知事件、维护记忆、做出决策。"""

    def __init__(self, persona: Persona, llm: BaseLLMProvider,
                 world: World, store_dir: Optional[str] = None,
                 relationships: Optional['RelationshipNetwork'] = None) -> None:
        self.persona = persona
        self.world = world
        self.memory = MemorySystem(persona.id, store_dir=store_dir)
        self.state_machine = StateMachine(NPCState.WORKING)
        # 关系网络：亲缘度归关系网维护（不在 8D 心智基底中）
        self.relationships = relationships or RelationshipNetwork()
        # 玩家边缺省注入：NPC 默认认识玩家（客人，好感 0.5）
        if self.relationships.get_relation_to(persona.id, "player") is None:
            self.relationships.add(Relationship(
                source_id=persona.id, target_id="player",
                relation="客人", affinity=0.5))
        self.decision = DecisionEngine(persona, llm, relationships=self.relationships)
        self.executor = ActionExecutor(world)
        # 垫话引擎：脾气掩码 + 8D 当下状态 → 0-token 本地垫话（不进 LLM prompt）
        from .filler import FillerEngine
        self.filler_engine = FillerEngine(persona.temperament)

        # T2 标签账本：默认 None。挂载是显式 API（NPCEngine.mount_tags 创建），
        # None 时决策上下文零身份标签注入，行为与接线前完全一致（向后兼容）。
        self.tag_ledger = None

        # 注册到世界
        from .world import Entity
        self.entity = Entity(id=persona.id, kind="npc", name=persona.name,
                             location_id=persona.location_id,
                             inventory=self._initial_inventory(),
                             appearance=dict(persona.appearance))
        world.add_entity(self.entity, announce=False)
        world.bus.subscribe(self._on_event, kinds=None)  # 全量感知

        # 内状态与事件驱动更新器
        self.inner_state = InnerState()
        self.state_updater = StateUpdater(self)
        world.bus.subscribe(self.state_updater.on_event, kinds=None)

    def _initial_inventory(self) -> List[str]:
        if self.persona.role == "blacksmith":
            return ["铁剑", "铁矿石", "锤子"]
        if self.persona.role == "merchant":
            return ["皮甲", "干粮", "地图"]
        return []

    # ------------------------------------------------------------------ #
    def _on_event(self, event: WorldEvent) -> None:
        """感知：直接涉及自己的事件必可闻，其余按同地点过滤。"""
        # 自己产出的动作
        if event.kind == "npc_action" and event.payload.get("npc") == self.persona.id:
            self.memory.observe(event)
            return
        # 点名事件：玩家对我说话 / 收到物品，无视距离
        if event.kind == "player_spoke" and event.payload.get("to") == self.persona.id:
            self.memory.observe(event)
            return
        if event.kind == "item_given" and event.payload.get("to") == self.persona.id:
            self.memory.observe(event)
            return
        if event.actor == self.persona.id:
            self.memory.observe(event)
            return
        # 环境事件：只感知同地点发生的
        actor_entity = self.world.entities.get(event.actor)
        if actor_entity is not None and actor_entity.location_id != self.entity.location_id:
            return  # 别处发生的事，听不见
        self.memory.observe(event)

    # ------------------------------------------------------------------ #
    def handle_player_input(self, world: World, player_input: str) -> Action:
        action = self.decision.decide(self, world, player_input)
        return action

    def apply_schedule(self, hour: int) -> Optional[NPCState]:
        state = self.persona.schedule_state(hour)
        if state is not None and state is not self.state_machine.state:
            self.state_machine.force(state)  # 作息表是外部调度，允许强制
            # G6-B: 作息驱动位置移动
            if state == NPCState.SLEEPING and self.persona.residence:
                if self.entity.location_id != self.persona.residence:
                    self.world.move_entity(self.persona.id, self.persona.residence)
            elif state == NPCState.WORKING:
                if self.entity.location_id != self.persona.location_id:
                    self.world.move_entity(self.persona.id, self.persona.location_id)
            # IDLE: 保持当前位置不动
        return state

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.persona.to_dict(),
            "state": self.state_machine.state.value,
            "inner_state": self.inner_state.to_dict(),
            "memory_size": {"short": len(self.memory.short),
                            "long": len(self.memory.long.records)},
        }

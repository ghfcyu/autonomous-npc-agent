"""背景 NPC：轻量级 NPC，不使用 LLM，纯规则反应。"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from .world import World, WorldEvent, Entity


class BackgroundNPC:
    """背景 NPC：只有身份+一句概括+关系，不跑 LLM，事件触发纯规则反应。

    设计要点：
    - 不持有任何 LLM 引用，架构上不可能调用 LLM
    - 订阅事件总线，对特定事件类型产生确定性规则反应
    - 反应通过发布 npc_action 事件回流总线，核心 NPC 可感知
    - 反应模板中 {npc_name} 替换为 NPC 名字，其余字段从事件 payload 取
    """
    def __init__(self, config: Dict[str, Any], world: World) -> None:
        self._id = config["id"]
        self._name = config["name"]
        self._role = config.get("role", "background")
        self._summary = config.get("summary", "")
        self._reactions: Dict[str, List[str]] = config.get("reactions", {})
        self._residence = config.get("residence", config["location_id"])
        self._schedule: Dict[str, str] = config.get("schedule", {})
        self._world = world
        self._reaction_counter: Dict[str, int] = {}
        self._workplace = config["location_id"]   # 工作地（初始位置），存储后不变
        self._current_state: Optional[str] = None  # 当前作息状态，初始无

        # 注册实体到世界
        self._entity = Entity(
            id=self._id, kind="npc", name=self._name,
            location_id=config["location_id"],
            inventory=config.get("inventory", [])
        )
        world.add_entity(self._entity, announce=False)
        # 订阅事件总线（全量感知，自行过滤）
        world.bus.subscribe(self._on_event, kinds=None)

    @property
    def entity(self) -> Entity:
        return self._entity

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def role(self) -> str:
        return self._role

    @property
    def summary(self) -> str:
        return self._summary

    @property
    def residence(self) -> str:
        """居所地点 id（缺省时等于初始位置）。"""
        return self._residence

    @property
    def schedule(self) -> Dict[str, str]:
        """作息表（G6-B：作息表，apply_schedule 驱动位置移动）。"""
        return self._schedule

    def apply_schedule(self, hour: int) -> Optional[str]:
        """作息驱动：按 tick 时间在居所/工作地间移动。

        返回该小时的作息状态字符串（如 "WORKING"），无匹配返回 None。
        仅当状态实际变化时才移动实体并发布 entity_moved 事件。
        - WORKING → 移动到工作地（_workplace）
        - SLEEPING → 移动到居所（_residence）
        - IDLE → 保持当前位置不动
        """
        state_str = self._schedule.get(str(hour))
        if state_str is None:
            return None
        if state_str == self._current_state:
            return state_str
        # 状态变化，执行移动
        self._current_state = state_str
        if state_str == "SLEEPING" and self._residence:
            if self._entity.location_id != self._residence:
                self._world.move_entity(self._id, self._residence)
        elif state_str == "WORKING":
            if self._entity.location_id != self._workplace:
                self._world.move_entity(self._id, self._workplace)
        # IDLE: 保持当前位置不动
        return state_str

    def _on_event(self, event: WorldEvent) -> None:
        """规则反应：按事件类型+位置过滤，确定性选择模板，发布 npc_action。"""
        # 跳过自己产出的动作，防止自激
        if event.actor == self._id:
            return
        # 只处理配置了反应模板的事件类型
        templates = self._reactions.get(event.kind)
        if not templates:
            return
        # 位置过滤
        if event.kind == "env_event":
            # 环境事件：只反应同地点的
            actor_entity = self._world.entities.get(event.actor)
            if actor_entity is not None and actor_entity.location_id != self._entity.location_id:
                return
        elif event.kind == "entity_moved":
            # 移动事件：只反应有人移动到自己的地点
            if event.payload.get("to") != self._entity.location_id:
                return
        # 确定性选择模板（计数器取模轮转）
        counter = self._reaction_counter.get(event.kind, 0)
        template = templates[counter % len(templates)]
        self._reaction_counter[event.kind] = counter + 1
        # 格式化反应文本并发布
        text = self._format_reaction(template, event)
        self._world.bus.publish(WorldEvent(
            self._world.tick_count, "npc_action", self._id,
            {"summary": text, "npc": self._id}
        ))

    def _format_reaction(self, template: str, event: WorldEvent) -> str:
        """安全格式化反应模板：{npc_name} 用 NPC 名，其余从 payload 取。"""
        fmt_kwargs = dict(event.payload)
        fmt_kwargs["npc_name"] = self._name
        try:
            return template.format(**fmt_kwargs)
        except (KeyError, IndexError, ValueError):
            return template.replace("{npc_name}", self._name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self._id, "name": self._name, "role": self._role,
            "location_id": self._entity.location_id,
            "summary": self._summary,
            "residence": self._residence,
            "schedule": self._schedule,
            "current_state": self._current_state,
            "type": "background",
        }

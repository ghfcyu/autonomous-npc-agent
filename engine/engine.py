"""引擎编排入口：世界 + NPC 集合的对外 API。

三个核心方法：
- ``player_says(text, npc_id)``  玩家对某个 NPC 说话（核心对话闭环）
- ``tick(minutes)``              推进世界时间并应用 NPC 作息
- ``status()``                   世界与全体 NPC 状态总览（供 API/Demo）
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .actions import Action
from .llm import create_provider
from .llm.base import BaseLLMProvider
from .npc import NPC, Persona
from .world import Entity, World


class NPCEngine:
    """引擎门面（Facade）。"""

    def __init__(self, llm: Optional[BaseLLMProvider] = None,
                 npc_configs: Optional[List[Persona]] = None,
                 store_dir: Optional[str] = None) -> None:
        self.world = World()
        self.llm = llm or create_provider()
        self.store_dir = store_dir
        self.npcs: Dict[str, NPC] = {}

        # 玩家实体
        self.world.add_entity(Entity(id="player", kind="player", name="旅行者",
                                     location_id="plaza", inventory=["金币", "苹果"]))

        for persona in (npc_configs if npc_configs is not None else Persona.load_all()):
            self.spawn(persona)

    # ------------------------------------------------------------------ #
    def spawn(self, persona: Persona) -> NPC:
        npc = NPC(persona, self.llm, self.world, store_dir=self.store_dir)
        self.npcs[persona.id] = npc
        return npc

    # ------------------------------------------------------------------ #
    def player_says(self, text: str, npc_id: str) -> Dict[str, Any]:
        """核心对话闭环：感知 → 记忆 → 决策 → 行动。"""
        npc = self.npcs.get(npc_id)
        if npc is None:
            return {"ok": False, "error": f"unknown npc: {npc_id}"}

        # 1) 感知：玩家说话事件入总线 → NPC 写入短期记忆
        from .world import WorldEvent
        self.world.bus.publish(WorldEvent(
            self.world.tick_count, "player_spoke", "player",
            {"to": npc_id, "text": text}))

        # 2/3) 决策（内部完成记忆检索与上下文组装）
        action: Action = npc.handle_player_input(self.world, text)

        # 4) 行动：执行并回流事件
        reply = npc.executor.execute(action, npc)

        # 记忆巩固（溢出才触发）
        npc.memory.consolidate()

        return {
            "ok": True,
            "npc": npc.persona.name,
            "npc_id": npc_id,
            "action": action.to_dict(),
            "reply": reply,
            "state": npc.state_machine.state.value,
            "clock": self.world.clock,
        }

    # ------------------------------------------------------------------ #
    def tick(self, minutes: int = 10) -> Dict[str, Any]:
        self.world.tick(minutes)
        applied = {}
        for npc_id, npc in self.npcs.items():
            state = npc.apply_schedule(self.world.hour)
            if state is not None:
                applied[npc_id] = state.value
        return {"clock": self.world.clock, "tick": self.world.tick_count,
                "schedule_applied": applied}

    # ------------------------------------------------------------------ #
    def status(self) -> Dict[str, Any]:
        snap = self.world.snapshot("player")
        return {
            "world": {
                "clock": self.world.clock,
                "tick": self.world.tick_count,
                "weather": self.world.weather.value,
                "locations": [
                    {"id": loc.id, "name": loc.name, "x": loc.x, "y": loc.y,
                     "entities": [e.name for e in self.world.entities_at(loc.id)]}
                    for loc in self.world.locations.values()
                ],
                "player": {"location": self.world.entities["player"].location_id,
                           "inventory": self.world.entities["player"].inventory},
            },
            "npcs": [npc.to_dict() for npc in self.npcs.values()],
            "recent_events": [
                {"tick": e.tick, "kind": e.kind, "actor": e.actor,
                 "summary": e.payload.get("summary") or e.payload.get("text") or e.kind}
                for e in list(self.world.bus.history)[-12:][::-1]
            ],
        }

    def move_player(self, location_id: str) -> Dict[str, Any]:
        ok = self.world.move_entity("player", location_id)
        return {"ok": ok, "location": self.world.entities["player"].location_id}

"""引擎编排入口：世界 + NPC 集合的对外 API。

三个核心方法：
- ``player_says(text, npc_id)``  玩家对某个 NPC 说话（核心对话闭环）
- ``tick(minutes)``              推进世界时间并应用 NPC 作息
- ``status()``                   世界与全体 NPC 状态总览（供 API/Demo）
"""

from __future__ import annotations

import json
import os
import random
import zlib
from typing import Any, Dict, List, Optional

from .actions import Action
from .background_npc import BackgroundNPC
from .llm import create_provider
from .llm.base import BaseLLMProvider
from .npc import NPC, Persona
from .relationships import RelationshipNetwork
from .world import Entity, World, WorldEvent

DEFAULT_BG_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                      "configs", "background_npcs")
DEFAULT_RELATIONSHIPS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                           "configs", "relationships.json")


class NPCEngine:
    """引擎门面（Facade）。"""

    def __init__(self, llm: Optional[BaseLLMProvider] = None,
                 npc_configs: Optional[List[Persona]] = None,
                 store_dir: Optional[str] = None,
                 background_configs: Optional[List[dict]] = None,
                 relationships: Optional[RelationshipNetwork] = None) -> None:
        self.world = World()
        self.llm = llm or create_provider()
        self.store_dir = store_dir
        self.npcs: Dict[str, NPC] = {}
        self.token_stats: Dict[str, Dict[str, int]] = {}

        # 关系网：显式传入 > 默认文件 > 空网络
        if relationships is not None:
            self.relationships = relationships
        elif os.path.exists(DEFAULT_RELATIONSHIPS_PATH):
            self.relationships = RelationshipNetwork.from_file(DEFAULT_RELATIONSHIPS_PATH)
        else:
            self.relationships = RelationshipNetwork()

        # 玩家实体
        self.world.add_entity(Entity(id="player", kind="player", name="旅行者",
                                     location_id="plaza", inventory=["金币", "苹果"]))

        for persona in (npc_configs if npc_configs is not None else Persona.load_all()):
            self.spawn(persona)

        # 生成背景 NPC：显式传入配置时完全按传入值；全默认构造（生产/演示路径）
        # 时自动加载 configs/background_npcs/；显式传入自定义 npc_configs（测试/
        # 嵌入场景）时不隐式注入，保证场景可控。
        self.background_npcs: Dict[str, BackgroundNPC] = {}
        if background_configs is not None:
            bg_configs = background_configs
        elif npc_configs is None:
            bg_configs = self._load_bg_configs()
        else:
            bg_configs = []
        for config in bg_configs:
            bg = BackgroundNPC(config, self.world)
            self.background_npcs[bg.id] = bg

    @staticmethod
    def _load_bg_configs(config_dir: str = DEFAULT_BG_CONFIG_DIR) -> List[dict]:
        configs = []
        if os.path.isdir(config_dir):
            for fname in sorted(os.listdir(config_dir)):
                if fname.endswith(".json"):
                    with open(os.path.join(config_dir, fname), encoding="utf-8") as fh:
                        configs.append(json.load(fh))
        return configs

    # ------------------------------------------------------------------ #
    def spawn(self, persona: Persona) -> NPC:
        npc = NPC(persona, self.llm, self.world, store_dir=self.store_dir,
                  relationships=self.relationships)
        self.npcs[persona.id] = npc
        return npc

    # ------------------------------------------------------------------ #
    def mount_tags(self, npc_id: str,
                   rng: Optional[random.Random] = None) -> Optional["TagLedger"]:
        """T2 标签挂载（显式 API，默认不启用）：为核心 NPC 创建标签账本。

        规范 5.2：非纯背景环境 NPC 强制挂载 ≥1 显性缺陷 + ≥1 绝密把柄；
        背景NPC 不在 self.npcs 登记（"非纯背景"语义），天然零挂载。
        同时把 6D 先天属性称号中的高显著度档 add_innate 进账本：仅
        attribute_label/beauty_label 的传奇档（与 10.0 端点同文案）或
        长尾/底级档（与 1.0 端点同文案）——寻常档不注入（token 纪律），
        档位判定用公开 API 端点探测，不复制阈值数字（正交纪律）。

        称号挂载先于缺陷/把柄挂载：mount_flaw_and_secret 的互斥预检
        依赖账本中已有的 morality_saint 等先天称号（圣人道德不与
        暗夜杀人并存）。

        rng=None 时用 zlib.crc32(npc_id) 做种子：跨进程确定性（内建
        hash() 受 PYTHONHASHSEED 随机化影响，不可用）。
        返回挂载好的 TagLedger；未知 npc_id 返回 None。
        """
        npc = self.npcs.get(npc_id)
        if npc is None:
            return None
        # 延迟 import（项目惯例）：tag_mount 是 T2 新模块，保持本模块可独立加载
        from .tag_genesis import (attribute_label, beauty_label,
                                  generate_innate_attributes)
        from .tag_mount import MountedTag, TagLedger, TagPhase

        if rng is None:
            rng = random.Random(zlib.crc32(npc_id.encode("utf-8")))
        innate = generate_innate_attributes(rng)
        ledger = TagLedger()

        # 6D 高显著度先天称号（beauty 走专属六档 beauty_label）
        for attr_id in ("strength", "savvy", "courage", "morality", "alcohol_tol"):
            label = attribute_label(attr_id, getattr(innate, attr_id))
            if label == attribute_label(attr_id, 10.0):  # 传奇档
                # morality 传奇档对齐互斥锁 id morality_saint（规范 6.2）
                tag_id = ("morality_saint" if attr_id == "morality"
                          else f"attr_{attr_id}_legendary")
                ledger.add_innate(MountedTag(tag_id=tag_id, label=label,
                                             phase=TagPhase.INNATE))
            elif label == attribute_label(attr_id, 1.0):  # 长尾档
                ledger.add_innate(MountedTag(tag_id=f"attr_{attr_id}_tail",
                                             label=label, phase=TagPhase.INNATE))
        beauty = beauty_label(innate.beauty)
        if beauty == beauty_label(10.0):  # 倾国倾城，对齐互斥锁 id
            ledger.add_innate(MountedTag(tag_id="beauty_stunning", label=beauty,
                                         phase=TagPhase.INNATE))
        elif beauty == beauty_label(1.0):  # 面目可怖，对齐互斥锁 id
            ledger.add_innate(MountedTag(tag_id="beauty_horrifying", label=beauty,
                                         phase=TagPhase.INNATE))

        # 强制缺陷 + 把柄（挂载结果进 INNATE 桶，把柄不进决策上下文）
        ledger.mount_flaw_and_secret(rng, innate=innate)
        npc.tag_ledger = ledger
        return ledger

    # ------------------------------------------------------------------ #
    def player_says(self, text: str, npc_id: str) -> Dict[str, Any]:
        """核心对话闭环：感知 → 记忆 → 决策 → 行动。"""
        npc = self.npcs.get(npc_id)
        if npc is None:
            return {"ok": False, "error": f"unknown npc: {npc_id}"}

        # 1) 感知：玩家说话事件入总线 → NPC 写入短期记忆
        #    （publish 同步分发，StateUpdater 已在此步更新 8D 状态）
        from .world import WorldEvent
        self.world.bus.publish(WorldEvent(
            self.world.tick_count, "player_spoke", "player",
            {"to": npc_id, "text": text}))

        # 1.5) 垫话：慢脑 LLM 调用前同步返回的反应性开场白
        #      （0-token 本地计算，不注入 LLM prompt；消费的是
        #      「听到玩家说话」后的反应性 8D 状态）
        filler = npc.filler_engine.generate(npc.inner_state)

        # 2/3) 决策（内部完成记忆检索与上下文组装）
        action: Action = npc.handle_player_input(self.world, text)

        # 4) 行动：执行并回流事件
        reply = npc.executor.execute(action, npc)

        # Token 度量：核心 NPC 对话后聚合（背景 NPC 零 LLM 不进入此路径）
        usage = getattr(self.llm, "last_usage",
                        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
        stats = self.token_stats.setdefault(
            npc_id, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0})
        stats["prompt_tokens"] += usage.get("prompt_tokens", 0)
        stats["completion_tokens"] += usage.get("completion_tokens", 0)
        stats["total_tokens"] += usage.get("total_tokens", 0)
        stats["calls"] += 1

        # 记忆巩固（溢出才触发）
        npc.memory.consolidate()

        # G3: 玩家交互累积事件槽
        self.world.accumulate_event_slot()

        return {
            "ok": True,
            "npc": npc.persona.name,
            "npc_id": npc_id,
            "action": action.to_dict(),
            "reply": reply,
            "filler": filler,
            "state": npc.state_machine.state.value,
            "clock": self.world.clock,
        }

    # ------------------------------------------------------------------ #
    def player_gives(self, npc_id: str, item: str) -> Dict[str, Any]:
        """玩家把物品送给某个 NPC（G1：事件驱动长期记忆入口）。

        无论走哪条路径，最终都会发布一条 actor="player" 的 item_given 事件，
        目标 NPC 通过既有订阅链路（``NPC._on_event`` → ``MemorySystem.observe``）
        把这次赠予同时写入短期与长期记忆（importance=0.8 >= 阈值 0.7）。
        """
        if npc_id not in self.npcs:
            return {"ok": False, "reason": "unknown npc"}

        if self.world.transfer_item("player", npc_id, item):
            # 真实库存转移，transfer_item 内部已发布 item_given 事件
            from_inventory = True
        else:
            # 宽松模式：玩家库存中没有该物品时不做严格模拟，仅手动发布事件，
            # 保证 NPC 仍能通过既有链路记住这次赠予。
            self.world.bus.publish(WorldEvent(
                self.world.tick_count, "item_given", "player",
                {"to": npc_id, "item": item}))
            from_inventory = False

        return {"ok": True, "item": item, "to": npc_id,
                "from_inventory": from_inventory}

    # ------------------------------------------------------------------ #
    def player_changes_appearance(self, changes: Dict[str, str]) -> bool:
        """玩家更换外观（穿着/姿势），同地点 NPC 将感知到。"""
        return self.world.set_appearance("player", changes)

    # ------------------------------------------------------------------ #
    def tick(self, minutes: int = 10) -> Dict[str, Any]:
        self.world.tick(minutes)
        applied = {}
        for npc_id, npc in self.npcs.items():
            state = npc.apply_schedule(self.world.hour)
            if state is not None:
                applied[npc_id] = state.value
        # G6-B: 背景 NPC 同样应用作息驱动
        for bg_id, bg in self.background_npcs.items():
            state = bg.apply_schedule(self.world.hour)
            if state is not None:
                applied[bg_id] = state
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
            "npcs": [npc.to_dict() for npc in self.npcs.values()] +
                    [bg.to_dict() for bg in self.background_npcs.values()],
            "recent_events": [
                {"tick": e.tick, "kind": e.kind, "actor": e.actor,
                 "summary": e.payload.get("summary") or e.payload.get("text") or e.kind}
                for e in list(self.world.bus.history)[-12:][::-1]
            ],
            "token_stats": {
                "by_npc": self.token_stats,
                "total_tokens": getattr(self.llm, "total_tokens_used", 0),
                "llm_calls": getattr(self.llm, "call_count", 0),
            },
        }

    def move_player(self, location_id: str) -> Dict[str, Any]:
        ok = self.world.move_entity("player", location_id)
        return {"ok": ok, "location": self.world.entities["player"].location_id}

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
from typing import Any, Dict, List, Optional

from .background_npc import BackgroundNPC
from .economics import WealthLedger
from .habit import HabitLoop
from .llm import create_provider
from .llm.base import BaseLLMProvider
from .npc import NPC, Persona
from .relationships import RelationshipNetwork
from .states import NPCState
from .village_genesis import env_mind_for, generate_village, load_newcomer_configs
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
        # T3 快脑接管率：total = player_says 有效对话次数，hits = 快脑
        # 命中次数（0 LLM 调用秒回）。供脚本度量「接管率 ≥50%」目标。
        self.fast_brain_stats: Dict[str, int] = {"hits": 0, "total": 0}

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

        # T5 第二小步：EWMA 财富基准账本（Tier 0 纯规则零 token）。全村
        # 25 人建账，初值单一来源复用创生档案 generate_village()（键=
        # npc_id，profile.innate.wealth 为对数正态创生值，与 EnvMind 初值
        # 同一 crc32 确定性口径）；ensure_account 幂等。订阅采用按类型
        # kinds=["item_given"]（EventBus 原生支持，world.py subscribe）
        # 而非 StateUpdater 的全量订阅——财富基准只消费收获事件，其余
        # 7 类事件零分发开销。无财富事件时 fortune_tag 全空、prompt
        # 逐字节不变（基线不变性，token 4519）。
        self.wealth_ledger = WealthLedger()
        for _npc_id, _profile in generate_village().items():
            self.wealth_ledger.ensure_account(_npc_id, _profile.innate.wealth)
        self.world.bus.subscribe(self.wealth_ledger.on_event,
                                 kinds=["item_given"])

        for persona in (npc_configs if npc_configs is not None else Persona.load_all()):
            self.spawn(persona)

        # 生成背景 NPC：显式传入配置时完全按传入值；全默认构造（生产/
        # 演示路径）时自动加载 configs/background_npcs/ 并接入
        # configs/village_roster/ 的 15 位新住民（T4-a 正式接入运转；
        # T4-b 收尾段 tavern_sun 升级第 3 核心 NPC 后共 22 背景 NPC =
        # 7 既有 + 15 新住民）；显式传入自定义 npc_configs（测试/嵌入场景）
        # 时不隐式注入，保证场景可控（既有语义零变化）。
        # load_newcomer_configs 复用 village_genesis 单一来源，
        # 不在本模块复制目录扫描逻辑（两套扫描漂移=共线性死穴）。
        # T4-b 第二小步：三路 bg_configs 统一经 env_mind_for 注入 4D EnvMind
        # 确定性初值（id 不在花名册 / key NPC → None，行为与旧版完全一致）。
        # T4-c 敌意边行为出口：构造时注入关系网引用（None 向后兼容），
        # 反应文案层消费敌意边（零 LLM 零 prompt）。
        self.background_npcs: Dict[str, BackgroundNPC] = {}
        if background_configs is not None:
            bg_configs = background_configs
        elif npc_configs is None:
            bg_configs = self._load_bg_configs() + load_newcomer_configs()
        else:
            bg_configs = []
        for config in bg_configs:
            bg = BackgroundNPC(config, self.world, env_mind=env_mind_for(config["id"]),
                               relationships=self.relationships)
            self.background_npcs[bg.id] = bg
        # T5 第一小步：黄昏酒馆习惯回路（杜希格习惯回路离散截断，纯规则
        # 0-token，背景 NPC 范围；relationships 空网/None 时习惯集合为空、
        # 行为与无 HabitLoop 等价，向后兼容）
        self._habit_loop = HabitLoop(self.world, self.background_npcs,
                                     self.relationships)

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
        # T2 收口先行批（常驻化）：配置声明 tag_profile="genesis" 的核心
        # NPC 引擎初始化即自动挂载标签账本——"断指的铁匠陈"是 NPC 默认
        # 状态，而非显式 API 调用结果；rng=None 走 crc32(npc_id) 确定性
        # 种子（与 mount_tags 同一确定性口径）。
        if persona.tag_profile == "genesis":
            npc.tag_ledger = self._build_tag_ledger(persona.id)
        # T5 第二小步：财富账本引用挂到 NPC（tag_ledger 同构接线模式）。
        # 决策层经 getattr 读取（兼容直接构造/未挂账本的 NPC——None 时
        # fortune 恒空串，prompt 与接线前逐字节全等）；未建账 npc_id
        # （自定义测试人格）fortune_tag 同样返回空串，天然零影响。
        npc.wealth_ledger = self.wealth_ledger
        self.npcs[persona.id] = npc
        return npc

    # ------------------------------------------------------------------ #
    def _build_tag_ledger(self, npc_id: str,
                          rng: Optional[random.Random] = None) -> "TagLedger":
        """构建一张标签账本（薄壳：全部逻辑在 tag_mount.build_genesis_ledger 单一内核）。

        mount_tags 显式 API 与 spawn 常驻化共用该内核；T4 人口学生成器
        village_genesis 亦复用同一函数（单一挂载内核纪律：两套挂载逻辑
        漂移=共线性死穴）。本方法签名与行为零变化（test_tag_residency /
        test_tag_mount 为回归保护网）。
        """
        from .tag_mount import build_genesis_ledger
        return build_genesis_ledger(npc_id, rng)

    # ------------------------------------------------------------------ #
    def mount_tags(self, npc_id: str,
                    rng: Optional[random.Random] = None) -> Optional["TagLedger"]:
        """T2 标签挂载显式 API：为核心 NPC 创建标签账本并挂到 npc.tag_ledger。

        账本构建逻辑由 _build_tag_ledger 承载（与 spawn 常驻化共用内核）；
        本方法保持既有签名与行为完全兼容：对任意已登记 NPC（含未声明
        tag_profile 的）可重复调用重建账本，未知 npc_id 返回 None。
        默认状态语义见 spawn：声明 tag_profile="genesis" 的 NPC 已自动挂载，
        无需再调本方法。

        返回挂载好的 TagLedger；未知 npc_id 返回 None。
        """
        npc = self.npcs.get(npc_id)
        if npc is None:
            return None
        ledger = self._build_tag_ledger(npc_id, rng)
        npc.tag_ledger = ledger
        return ledger

    # ------------------------------------------------------------------ #
    def player_says(self, text: str, npc_id: str) -> Dict[str, Any]:
        """核心对话闭环：感知 → 记忆 → 决策 → 行动。

        T3 快慢脑分流：感知与垫话之后先走快脑规则匹配（0 LLM 调用），
        命中直接构造 SPEAK Action 执行；未命中走慢脑（决策层 LLM），
        既有行为零变化（纯旁路向后兼容）。
        """
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

        # 1.6) 快脑：高频日常意图规则匹配命中 → 构造 SPEAK 直接执行
        #      （0 LLM 调用）。SLEEPING 的 NPC 不放行快脑——交慢脑既有
        #      路径处理（梦呓/REFUSE），睡觉 NPC 说话的拒绝语义不变。
        fast_action = None
        if npc.state_machine.state is not NPCState.SLEEPING:
            fast_action = npc.fast_brain.respond(text, npc, self.world)
        self.fast_brain_stats["total"] += 1
        if fast_action is not None:
            self.fast_brain_stats["hits"] += 1
            action = fast_action  # 快脑：0 LLM 调用
        else:
            # 2/3) 慢脑决策（内部完成记忆检索与上下文组装 + LLM）
            action = npc.handle_player_input(self.world, text)

        # 4) 行动：执行并回流事件（快慢脑共用同一执行器：SPEAK 均发布
        #    npc_action 事件回流 + enter_talking）
        reply = npc.executor.execute(action, npc)

        # Token 度量：仅慢脑路径聚合 LLM usage（快脑命中 0 LLM 调用，
        # 不得读取 llm.last_usage 的上次慢脑残留值，calls 也不 +1）
        if fast_action is None:
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
            # T3：本次对话走的脑（fast = 快脑秒回，slow = 慢脑 LLM），
            # 供测试与脚本度量接管率
            "brain": "fast" if fast_action is not None else "slow",
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
        # T5 第一小步：黄昏酒馆习惯回路（在作息状态落地后消费 IDLE 段；
        # 熄灯不归位——19/20/21 点由各 NPC SLEEPING 作息移动接管）
        self._habit_loop.apply(self.world.hour)
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
            # T3 快脑接管率（hits/total，player_says 累计）
            "fast_brain": self.fast_brain_stats,
        }

    def move_player(self, location_id: str) -> Dict[str, Any]:
        ok = self.world.move_entity("player", location_id)
        return {"ok": ok, "location": self.world.entities["player"].location_id}

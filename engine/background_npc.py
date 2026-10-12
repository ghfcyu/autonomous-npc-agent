"""背景 NPC：轻量级 NPC，不使用 LLM，纯规则反应。

T4-b 第二小步（2026-10-12）：可选持有 4D 压缩稳态 EnvMind（Tier 0/1
封印——数值与英文字段名不进任何 LLM prompt，零 LLM 零 prompt）：
反应文案按状态带拼接纯中文变体后缀，并随作息 tick 每小时离散演化
（无表项小时沿用当前作息状态，状态延续语义）。
"""
from __future__ import annotations
from dataclasses import replace
from typing import Any, Dict, List, Optional
from .village_genesis import EnvMind
from .world import World, WorldEvent, Entity

ENV_MIND_VARIANTS: tuple[tuple[str, float, str], ...] = (
    # (EnvMind 字段名, 触发阈值, 变体后缀)。顺序=优先级：生理(疲劳>饥饿)优先于
    # 压力与情绪——又累又饿的人先显累饿。每条反应至多拼接一个后缀，文案克制。
    ("p_fatigue", 0.65, "，一边打了个哈欠"),
    ("p_hunger", 0.65, "，肚子还咕咕叫着"),
    ("S_stress", 0.65, "，眉头微微拧着"),
    ("e_P", 0.65, "，嘴角带着笑意"),
)
# 每小时 4D 演化步长（作息段内每小时演化，无表项小时沿用当前作息
# 状态；元组序 = EnvMind 字段序
# p_fatigue/p_hunger/e_P/S_stress）。叙事依据：
# WORKING 劳作累积疲劳/体力消耗/无暇他顾/压力微增；
# IDLE 歇脚用餐恢复/闲适愉悦；SLEEPING 睡眠大恢复/情绪归平/压力释放。
# 日平衡为负（一觉睡回低位），白天劳作后午后疲劳抵达 0.65+ 触发变体带。
MIND_EVOLUTION_RATES: dict = {
    "WORKING":  (0.08, 0.06, 0.00, 0.01),
    "IDLE":     (-0.05, -0.12, 0.02, -0.02),
    "SLEEPING": (-0.15, -0.02, -0.03, -0.05),
}


class BackgroundNPC:
    """背景 NPC：只有身份+一句概括+关系，不跑 LLM，事件触发纯规则反应。

    设计要点：
    - 不持有任何 LLM 引用，架构上不可能调用 LLM
    - 订阅事件总线，对特定事件类型产生确定性规则反应
    - 反应通过发布 npc_action 事件回流总线，核心 NPC 可感知
    - 反应模板中 {npc_name} 替换为 NPC 名字，其余字段从事件 payload 取
    - T4-b 第二小步：可选注入 4D EnvMind（env_mind=None 时行为与旧版
      完全一致），反应文案按状态带拼接纯中文变体后缀、随作息 tick
      每小时演化（无表项小时沿用当前作息状态）——纯 Tier 0/1 规则计算，零 LLM 零 prompt（封印语义：
      EnvMind 数值与英文字段名不进任何 LLM prompt，行为出口仅为
      npc_action 文案变体）
    - 反应同 hour 判重（第十二次审查落地）：判重键 (hour, event.kind)——
      同一小时内对同一事件类型至多发布一条 npc_action（位置过滤通过
      才计配额，跨 hour 自动恢复），hour 口径与 EnvMind 演化层
      _last_evolved_hour 一致（self._world.hour）
    """
    def __init__(self, config: Dict[str, Any], world: World,
                 env_mind: Optional[EnvMind] = None) -> None:
        self._id = config["id"]
        self._name = config["name"]
        self._role = config.get("role", "background")
        self._summary = config.get("summary", "")
        self._reactions: Dict[str, List[str]] = config.get("reactions", {})
        self._residence = config.get("residence", config["location_id"])
        self._schedule: Dict[str, str] = config.get("schedule", {})
        self._world = world
        self._reaction_counter: Dict[str, int] = {}
        # 反应同 hour 判重（第十二次审查）：kind → 最近一次实际发布过
        # 反应的 hour。与演化层 _last_evolved_hour 同 hour 口径，防同
        # hour 内对同类事件复读式连发（farmer_zhao tick11 三连复读）。
        self._last_react_hour: Dict[str, int] = {}
        self._workplace = config["location_id"]   # 工作地（初始位置），存储后不变
        self._current_state: Optional[str] = None  # 当前作息状态，初始无
        # T4-b 第二小步：4D 压缩稳态（None 时不演化、变体恒为空串，行为
        # 与旧版完全一致）；_last_evolved_hour 供 apply_schedule 同 hour
        # 多次 tick 判重（10 分钟粒度一天 144 次 tick 只演化一步）。
        self._mind: Optional[EnvMind] = env_mind
        self._last_evolved_hour: Optional[int] = None

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

    @property
    def mind(self) -> Optional[EnvMind]:
        """4D 压缩稳态 EnvMind（未注入时为 None；纯数据观测出口，不进 prompt）。"""
        return self._mind

    def apply_schedule(self, hour: int) -> Optional[str]:
        """作息驱动：按 tick 时间在居所/工作地间移动。

        返回该小时的作息状态字符串（如 "WORKING"），无匹配返回 None。
        仅当状态实际变化时才移动实体并发布 entity_moved 事件。
        - WORKING → 移动到工作地（_workplace）
        - SLEEPING → 移动到居所（_residence）
        - IDLE → 保持当前位置不动
        """
        state_str = self._schedule.get(str(hour))
        # 演化用「延续状态」：无表项小时沿用 _current_state（与既有作息移动的
        # 状态延续语义一致——转移点键控作息表下 WORKING 段每小时持续累积疲劳，
        # 牧人早晨上牧场、临近中午打哈欠，午后疲劳抵达 0.65 变体带）。
        evolve_state = state_str if state_str is not None else self._current_state
        if evolve_state is not None and hour != self._last_evolved_hour and self._mind is not None:
            self._evolve_mind(hour, evolve_state)
            self._last_evolved_hour = hour
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
        # 同 hour 判重（第十二次审查）：本 hour 内该事件类型已发布过反应
        # 则直接跳过——放在位置过滤之后，别处事件不消耗配额；hour 取值
        # 与演化层同口径（engine.tick 经 apply_schedule(self._world.hour)
        # 传入，此处同取 self._world.hour）；跨 hour 不等即判新，自动恢复
        # 反应资格；通过判重后无提前返回，记录的 hour 必对应实际发布。
        hour = self._world.hour
        if self._last_react_hour.get(event.kind) == hour:
            return
        self._last_react_hour[event.kind] = hour
        # 确定性选择模板（计数器取模轮转）
        counter = self._reaction_counter.get(event.kind, 0)
        template = templates[counter % len(templates)]
        self._reaction_counter[event.kind] = counter + 1
        # 格式化反应文本并发布
        text = self._format_reaction(template, event)
        # T4-b 第二小步：按 EnvMind 状态带拼接纯中文变体后缀（零 LLM）。
        text += self._variant_suffix()
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

    def _variant_suffix(self) -> str:
        """EnvMind 状态带变体后缀（纯中文文案拼接，零 LLM 零 prompt）。

        按 ENV_MIND_VARIANTS 优先级顺序取第一条命中（维度值 ≥ 阈值）的
        后缀；至多一条（文案克制），未注入 mind 或全未命中返回空串。
        封印语义：后缀只进 npc_action 事件文本，EnvMind 数值与英文字段
        名永不进任何 LLM prompt。
        """
        if self._mind is None:
            return ""
        for dim, threshold, suffix in ENV_MIND_VARIANTS:
            if getattr(self._mind, dim) >= threshold:
                return suffix
        return ""

    def _evolve_mind(self, hour: int, state_str: str) -> None:
        """EnvMind 按作息状态演化一步（frozen dataclass 经 replace 整体替换）。

        rates 无匹配（未知作息状态）不演化；步长与叙事依据见
        MIND_EVOLUTION_RATES；每维 clamp 到 [0.0, 1.0]（与 InnerState
        apply_delta 同值域语义）。调用方 apply_schedule 负责同 hour 判重。
        """
        rates = MIND_EVOLUTION_RATES.get(state_str)
        if rates is None:
            return
        self._mind = replace(
            self._mind,
            p_fatigue=min(1.0, max(0.0, self._mind.p_fatigue + rates[0])),
            p_hunger=min(1.0, max(0.0, self._mind.p_hunger + rates[1])),
            e_P=min(1.0, max(0.0, self._mind.e_P + rates[2])),
            S_stress=min(1.0, max(0.0, self._mind.S_stress + rates[3])),
        )

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "id": self._id, "name": self._name, "role": self._role,
            "location_id": self._entity.location_id,
            "summary": self._summary,
            "residence": self._residence,
            "schedule": self._schedule,
            "current_state": self._current_state,
            "type": "background",
        }
        # T4-b 第二小步：env_mind 快照键（status 观测出口，不进 prompt；
        # 未注入时为 None）。
        data["env_mind"] = (
            None if self._mind is None else {
                "p_fatigue": round(self._mind.p_fatigue, 2),
                "p_hunger": round(self._mind.p_hunger, 2),
                "e_P": round(self._mind.e_P, 2),
                "S_stress": round(self._mind.S_stress, 2),
            })
        return data

"""背景 NPC：轻量级 NPC，不使用 LLM，纯规则反应。

T4-b 第二小步（2026-10-12）：可选持有 4D 压缩稳态 EnvMind（Tier 0/1
封印——数值与英文字段名不进任何 LLM prompt，零 LLM 零 prompt）：
反应文案按状态带拼接纯中文变体后缀，并随作息 tick 每小时离散演化
（无表项小时沿用当前作息状态，状态延续语义）。

T4-c 敌意边行为出口（2026-10-13）→ 社会关系反应出口统一收口（审查
指令 2，3/3 完整闭环）：可选注入关系网，事件 actor 命中 self→actor
关系边时按三类型分派反应文案前缀——敌意（affinity<0，优先判定）/
师徒（师父=恭敬池、徒弟=长辈慈和池）/血缘（父亲/母亲/儿子/女儿/
妻子/丈夫=亲昵池）；社会层前缀优先于 EnvMind 生理层后缀（同时命中
只取前缀，至多一条纪律——文案克制），纯文案拼接零 LLM 零 prompt。

T4 收官修正（审查指令 1，运转期分布对账）：e_P 维从「单调增量」改为
「向段内均衡目标收敛」（E_P_EQUILIBRIUM_TARGETS + E_P_CONVERGENCE）。
对账依据：原 SLEEPING 段 e_P -0.03/h 单调下移致每日净变化约 -0.16，
多日运转全村 e_P 必向 0 塌陷（17/22 低于生成带下界），与「情绪归平」
注释语义及持续运转的活村庄目标相悖；收敛语义下情绪向段内均衡位
回归而非无限漂移。三维单调步长（生理/压力）与 clamp [0,1] 不变。
"""
from __future__ import annotations
from dataclasses import replace
from typing import Any, Dict, List, Optional
from .relationships import RelationshipNetwork
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
# 每小时 4D 演化（作息段内每小时演化，无表项小时沿用当前作息状态；
# 元组序 = EnvMind 字段序 p_fatigue/p_hunger/e_P/S_stress）。
# 三维单调步长（生理/压力）叙事依据：
# WORKING 劳作累积疲劳/体力消耗/压力微增；
# IDLE 歇脚用餐恢复/压力纾解；SLEEPING 睡眠大恢复/压力释放。
# 日平衡为负（一觉睡回低位），白天劳作后午后疲劳抵达 0.65+ 触发变体带。
# e_P 位为 None 哨兵：该维不走单调步长（防误读为步长），改走均衡
# 收敛（情绪归平语义，见 E_P_EQUILIBRIUM_TARGETS）——修正依据：
# 运转期对账发现原 SLEEPING 段 -0.03/h 单调下移使每日净变化约
# -0.16、17/22 低于生成带下界，多日运转全村情绪必向 0 塌陷，
# 单调下移不可持续（T4 收官，审查指令 1）。
MIND_EVOLUTION_RATES: dict = {
    "WORKING":  (0.08, 0.06, None, 0.01),
    "IDLE":     (-0.05, -0.12, None, -0.02),
    "SLEEPING": (-0.15, -0.02, None, -0.05),
}

# T4 收官修正：e_P 段内均衡收敛目标（情绪归平语义——不同作息段的
# 情绪均衡位：劳作平淡 0.45 / 闲适带笑 0.68 / 睡眠归平 0.50）。
# 设计约束：IDLE 目标 0.68 保证 e_P 变体带（ENV_MIND_VARIANTS 阈值
# 0.65）在连续闲适约 3 小时后可达——从 WORKING 均衡位 0.45 出发
# 0.68-0.23×0.5^t ≥ 0.65 → t ≥ 2.9，玩家可感知的行为出口不因收敛
# 机制丢失（tests/test_env_mind_behavior.py TestRuntimeVisibility
# test_three_idle_hours_reach_joy_variant_band 锁定该可达性）。
E_P_EQUILIBRIUM_TARGETS: dict = {
    "WORKING": 0.45,
    "IDLE": 0.68,
    "SLEEPING": 0.50,
}

# e_P 收敛速率：每小时向段内均衡目标收敛一半差距。
E_P_CONVERGENCE = 0.5

# T4-c 社会关系反应出口（审查指令 2 收口 3/3）：事件 actor 命中
# self→actor 关系边时拼接在反应文案前的前缀池（三类型统一出口）。
# 文案克制（每条括注 ≤6 字）；确定性轮转与模板轮换同构（同一
# _reaction_counter 计数取模）。社会层前缀优先于 EnvMind 生理层
# 后缀（同时命中只取前缀不叠加，至多一条纪律）。
HOSTILE_PREFIXES: tuple[str, ...] = (
    "（冷哼一声）",
    "（把脸别过去）",
    "（头也不抬）",
)

# 师徒边：relation == "师父"（actor 是我的师父）→ 恭敬池。
MASTER_PREFIXES: tuple[str, ...] = (
    "（躬身）",
    "（拱手行礼）",
)

# 师徒边：relation == "徒弟"（actor 是我的徒弟）→ 长辈慈和池。
ELDER_PREFIXES: tuple[str, ...] = (
    "（颔首）",
    "（含笑打量）",
)

# 血缘边：relation ∈ 父亲/母亲/儿子/女儿/妻子/丈夫 → 亲昵池。
# 行为出口子集（六类直系）：比 relationships._PRIORITY_RELATION_TYPES
# 窄（不含兄长/兄弟/师娘等旁系与变体写法）——旁系亲昵度叙事上弱于
# 直系，且 configs/relationships.json 现有血缘边全部落在该六类内。
KIN_PREFIXES: tuple[str, ...] = (
    "（笑着招手）",
    "（快步迎上前）",
)

# 血缘亲昵池的 relation 类型集合（分派查表用，见 KIN_PREFIXES 注）。
_KIN_RELATION_TYPES = frozenset({
    "父亲", "母亲", "儿子", "女儿", "妻子", "丈夫",
})


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
    - T4-c 社会关系反应出口（审查指令 2 收口 3/3）：可选注入关系网
      （relationships=None 时行为与旧版完全一致），事件 actor 命中
      self→actor 关系边时按三类型分派反应文案前缀——敌意
      （affinity<0）/师父（恭敬池）/徒弟（长辈慈和池）/血缘（亲昵
      池），中性类型不前拼；社会层前缀优先于 EnvMind 生理层后缀，
      同时命中至多一条（文案克制纪律）
    - 反应同 hour 判重（第十二次审查落地）：判重键 (hour, event.kind)——
      同一小时内对同一事件类型至多发布一条 npc_action（位置过滤通过
      才计配额，跨 hour 自动恢复），hour 口径与 EnvMind 演化层
      _last_evolved_hour 一致（self._world.hour）
    """
    def __init__(self, config: Dict[str, Any], world: World,
                 env_mind: Optional[EnvMind] = None,
                 relationships: Optional[RelationshipNetwork] = None) -> None:
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
        # T4-c 社会关系反应出口：关系网引用（None 时社会前缀分派恒
        # 空串，行为与旧版完全一致）；仅文案层消费，零 LLM 零 prompt
        # 注入。
        self._relationships = relationships

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

    @property
    def state_str(self) -> Optional[str]:
        """当前作息状态字符串（WORKING/IDLE/SLEEPING；作息未应用时 None）。

        只读观测出口，供习惯回路（engine/habit.py）等纯规则层消费
        （零 LLM 零 prompt，Tier 0/1 封印不破）。
        """
        return self._current_state

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
        # T4-c 社会关系边前缀（敌意/师徒/血缘三类型统一出口，社会层）
        # 优先于 EnvMind 变体后缀（生理层）：同时命中只取社会前缀、
        # 不叠加后缀（至多一条纪律）；无社会边命中时 EnvMind 路径与
        # 旧版完全一致。文案层位于判重之后、发布之前，不改变判重行为。
        social = self._social_prefix(event.actor, counter)
        if social:
            text = social + text
        else:
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

    def _social_prefix(self, actor: str, counter: int) -> str:
        """社会关系边变体前缀（血缘/师徒/敌意三类型统一出口，纯中文
        文案拼接，零 LLM 零 prompt）。

        设计依据（T4-c 审查指令 2，3/3 完整闭环）：社会层前缀优先于
        生理层（EnvMind）后缀，同时命中至多一条（文案克制纪律）——
        由调用处保证：本方法返回非空前缀时不再拼接 EnvMind 变体后缀。

        关系网未注入（None）、actor 为空/为玩家、或 self→actor 无关系
        边（get_relation_to 未命中）或关系为中性类型（既非血缘亦非
        师徒亦非敌意）时返回空串——文案路径与旧版完全一致。命中时按
        本次反应的既有触发计数取模确定性轮转前缀（与模板轮换同构：
        同一计数器同一取模机制）。

        分派优先级（一条 get_relation_to 只返回一条边，天然至多命中
        一类；敌意判定放最前——affinity<0 优先于类型判定，防未来
        数据出现「血缘+负值」时误入亲昵池）：
        - affinity < 0          → 敌意池 HOSTILE_PREFIXES；
        - relation == "师父"     → 恭敬池 MASTER_PREFIXES；
        - relation == "徒弟"     → 长辈慈和池 ELDER_PREFIXES；
        - relation ∈ _KIN_RELATION_TYPES → 血缘亲昵池 KIN_PREFIXES；
        - 其余中性类型          → 空串（与旧版行为一致）。
        """
        if self._relationships is None or not actor or actor == "player":
            return ""
        rel = self._relationships.get_relation_to(self._id, actor)
        if rel is None:
            return ""
        if rel.affinity < 0:
            return HOSTILE_PREFIXES[counter % len(HOSTILE_PREFIXES)]
        if rel.relation == "师父":
            return MASTER_PREFIXES[counter % len(MASTER_PREFIXES)]
        if rel.relation == "徒弟":
            return ELDER_PREFIXES[counter % len(ELDER_PREFIXES)]
        if rel.relation in _KIN_RELATION_TYPES:
            return KIN_PREFIXES[counter % len(KIN_PREFIXES)]
        return ""

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

        rates 无匹配（未知作息状态）不演化（外层判重逻辑不变）；三维
        （p_fatigue/p_hunger/S_stress）按 MIND_EVOLUTION_RATES 单调步进，
        e_P 维按均衡收敛——每小时向 E_P_EQUILIBRIUM_TARGETS 段内目标
        收敛一半差距（元组 e_P 位 None 哨兵=该维走收敛，非步长；目标
        表缺该状态时 e_P 不动，防新增作息段漏配目标位静默漂移）；每维
        clamp 到 [0.0, 1.0]（与 InnerState apply_delta 同值域语义）。
        调用方 apply_schedule 负责同 hour 判重。
        """
        rates = MIND_EVOLUTION_RATES.get(state_str)
        if rates is None:
            return
        e_P = self._mind.e_P
        e_P_target = E_P_EQUILIBRIUM_TARGETS.get(state_str)
        if e_P_target is not None:
            e_P = min(1.0, max(0.0, e_P + E_P_CONVERGENCE * (e_P_target - e_P)))
        self._mind = replace(
            self._mind,
            p_fatigue=min(1.0, max(0.0, self._mind.p_fatigue + rates[0])),
            p_hunger=min(1.0, max(0.0, self._mind.p_hunger + rates[1])),
            e_P=e_P,
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

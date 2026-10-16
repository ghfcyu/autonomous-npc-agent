"""NPC 间社交回路（第十四次审查指令 2：活村庄社会生态最小闭环，0-token）。

设计（关系网驱动的确定性搭话，2026-10-16）：
- 数据流：configs/relationships.json 关系边 → SocialLoop 每 tick 扫描 →
  同地关系边发布 npc_action 事件 → EventBus 同步分发 → 同地核心 NPC
  记忆（MemorySystem.observe）与玩家事件流（status recent_events）。
- 行为出口仅为一条确定性中文文案（文案池按 relation 分派 + 触发计数
  取模轮转，与 HabitLoop HABIT_TEMPLATES / BackgroundNPC 反应模板轮换
  同构）——NPC 不位移、不改状态、不改关系。

触发闸门（apply(hour) 每 tick 一次，五层全过才互动）：
1. 亲和闸门：edge["affinity"] >= 0——敌意边（affinity<0）不进社交回路。
   共线性依据：冲突反应已有 T4-c 敌意前缀单出口
   （engine/background_npc.py HOSTILE_PREFIXES），若社交回路再对敌意边
   出第二套冲突文案，即双机制表达同一语义＝共线性死穴，故直接排除；
2. 范围闸门：source_id 与 target_id 都在背景 NPC 集合且两者不同——
   核心 NPC 不进社交回路（核心有自己的 LLM 对话链，规则搭话会与
   LLM 对话语义重叠）；
3. 判重闸门：无序对 key = tuple(sorted((source_id, target_id)))，
   该对当天（_day）已互动过则跳过——每对每天至多一次（防同地长时段
   复读式刷屏）；
4. 位置闸门：两实体 location_id 相同（world.entities[id].location_id）
   ——社交必须同地，隔空搭话不存在；
5. 状态闸门：两者 state_str ∈ {"WORKING", "IDLE"}——None＝作息未应用
   保守不动（沿 HabitLoop 纪律）；SLEEPING 深夜不社交（农家全家夜里
   同屋但不闲聊）。

day 推导口径（唯一合法日界）：World 没有 day 属性，日界由
``_last_hour`` 回绕推导——apply(hour) 开头若 ``hour < _last_hour``
（23→0 回绕）则 ``_day += 1`` 并清空当天判重集合；这是唯一合法的
日界推导路径，禁止另造第二套日期来源（两套日界漂移＝共线性死穴）。

文案池分派（模块级 TEMPLATE_POOLS 单一查表，禁 if-elif 长链）：
- "师父"/"师傅" → 徒弟视角池（speaker 是徒弟，对长辈说话）；
- "徒弟" → 师父视角池（speaker 是师父，对晚辈）；
- 血缘类（KIN_RELATION_TYPES 单一来源 import 自
  engine/background_npc.py，禁止复制第二份集合）→ 家人池
  （方向无关措辞，不得用"爹/娘/儿"称呼，直接用 {other_name}）；
- "邻居" → 邻里池；
- "恩人"/"老友"/"酒友"/"病人" → 交情池；
- "主顾"/"老主顾"/"供货商"/"同行"/"同僚"/"货源"/"东家" → 往来池；
- 未映射类型 → 交情池兜底（泛化不 crash）。

封印语义（Tier 0/1）：SocialLoop 与 HabitLoop/EnvMind 同属封印层——
不持有任何 LLM 引用、不组装任何 prompt、零 token；行为出口仅为
npc_action 确定性中文文案。``relationships=None`` 时零行为（与无
SocialLoop 等价，向后兼容），空关系网络同样安全。
"""
from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional, Tuple

from .background_npc import BackgroundNPC, KIN_RELATION_TYPES
from .relationships import RelationshipNetwork
from .world import World, WorldEvent

# 亲和闸门阈值：affinity >= 0 才进社交回路（敌意边排除，见模块 docstring）
_AFFINITY_MIN = 0.0

# 状态闸门：可社交的作息状态集合（None/SLEEPING 不社交）
_SOCIAL_STATES = frozenset({"WORKING", "IDLE"})

# 徒弟视角池（relation ∈ {"师父","师傅"}：speaker 是徒弟，对长辈说话）
DISCIPLE_TEMPLATES: tuple = (
    "（{npc_name}凑到{other_name}跟前）「师傅，这一步我总拿不准，您再讲讲？」",
    "（{npc_name}替{other_name}斟上茶）「您方才教的，我都记下了。」",
)

# 师父视角池（relation == "徒弟"：speaker 是师父，对晚辈）
MASTER_TEMPLATES: tuple = (
    "（{npc_name}指点着{other_name}的手法）「慢些，稳住腕子。」",
    "（{npc_name}看{other_name}练完一遍）「有长进，明儿接着来。」",
)

# 家人池（血缘类：方向无关措辞，不用"爹/娘/儿"称呼，直接用 {other_name}）
KIN_TEMPLATES: tuple = (
    "（{npc_name}冲{other_name}喊）「天擦黑了，先家去吃饭！」",
    "（{npc_name}把热饼塞到{other_name}手里）「趁热吃，别饿着。」",
)

# 邻里池（relation == "邻居"）
NEIGHBOR_TEMPLATES: tuple = (
    "（{npc_name}给{other_name}递了碗水）「改日来我家坐坐。」",
    "（{npc_name}隔着院墙同{other_name}搭话）「今儿日头真好。」",
)

# 交情池（恩人/老友/酒友/病人；未映射类型兜底）
FRIENDSHIP_TEMPLATES: tuple = (
    "（{npc_name}端起碗跟{other_name}碰了一个）「还是跟你说得上话！」",
    "（{npc_name}拉着{other_name}问长问短）「可算把你盼来了。」",
)

# 往来池（主顾/老主顾/供货商/同行/同僚/货源/东家）
BUSINESS_TEMPLATES: tuple = (
    "（{npc_name}跟{other_name}核对账目）「这批货按老价钱算。」",
    "（{npc_name}同{other_name}交割完货款）「下回还找你。」",
)

# relation → 文案池分派表（模块级单一查表；未映射类型走 FALLBACK 兜底）
TEMPLATE_POOLS: Dict[str, tuple] = {
    "师父": DISCIPLE_TEMPLATES,
    "师傅": DISCIPLE_TEMPLATES,
    "徒弟": MASTER_TEMPLATES,
    **{relation: KIN_TEMPLATES for relation in KIN_RELATION_TYPES},
    "邻居": NEIGHBOR_TEMPLATES,
    **{relation: FRIENDSHIP_TEMPLATES
       for relation in ("恩人", "老友", "酒友", "病人")},
    **{relation: BUSINESS_TEMPLATES
       for relation in ("主顾", "老主顾", "供货商", "同行", "同僚",
                        "货源", "东家")},
}

# 未映射 relation 类型的兜底池（泛化不 crash）
FALLBACK_TEMPLATES = FRIENDSHIP_TEMPLATES


class SocialLoop:
    """NPC 间社交回路（纯规则 0-token，背景 NPC 范围）。

    每 tick 扫描关系边一次，五层闸门（亲和/范围/判重/位置/状态）全过
    才发布一条确定性中文搭话文案（npc_action 事件，payload 含
    ``with``/``relation`` 两个数据出口键）。每对每天至多互动一次
    （_day 由 hour 回绕推导，见模块 docstring）；文案按 per-pair 触发
    计数取模轮转（跨天持续轮转，与 HabitLoop 模板轮换同构）。
    ``relationships=None`` 时零行为（与无 SocialLoop 等价，向后兼容）。
    """

    def __init__(self, world: World,
                 background_npcs: Iterable[BackgroundNPC] | Mapping[str, BackgroundNPC],
                 relationships: Optional[RelationshipNetwork]) -> None:
        self._world = world
        items = (background_npcs.values()
                 if isinstance(background_npcs, Mapping)
                 else background_npcs)
        self._npcs: Dict[str, BackgroundNPC] = {bg.id: bg for bg in items}
        self._relationships = relationships
        # 日界推导（World 无 day 属性，_last_hour 23→0 回绕是唯一合法
        # 日界）：_day 初始 0，每次 apply 开头检查回绕
        self._day: int = 0
        self._last_hour: Optional[int] = None
        # 当天已互动的无序对集合（判重闸门；日界回绕时清空）
        self._interacted: set = set()
        # per-pair 触发计数（文案确定性轮转，跨天持续）：pair → 已发布条数
        self._trigger_counts: Dict[Tuple[str, str], int] = {}

    # ------------------------------------------------------------------ #
    # 观测出口（只读，测试/调试用；不进 prompt）
    # ------------------------------------------------------------------ #
    @property
    def interacted_pairs(self) -> frozenset:
        """当天已互动的无序对集合（frozenset[tuple[str, str]]）。"""
        return frozenset(self._interacted)

    # ------------------------------------------------------------------ #
    # 触发（每 tick 一次；五层闸门：亲和→范围→判重→位置→状态）
    # ------------------------------------------------------------------ #
    def apply(self, hour: int) -> None:
        """社交扫描：遍历关系边（文件插入序=确定性），五层闸门全过才互动。

        日界推导：apply 开头若 hour < _last_hour（23→0 回绕）则 _day+1
        并清空当天判重集合（新的一天，同对可再互动）。触发时发布
        WorldEvent(tick_count, "npc_action", source_id, payload)，
        payload 的 "with"/"relation" 键是审查验收「断言互动双方与关系
        边类型」的数据出口。relationships=None 时零行为。
        """
        # day 跟踪（唯一合法日界推导，见模块 docstring）
        if self._last_hour is not None and hour < self._last_hour:
            self._day += 1
            self._interacted.clear()
        self._last_hour = hour
        if self._relationships is None:
            return
        for edge in self._relationships.to_dict():
            # 1) 亲和闸门：敌意边不进社交回路（T4-c 敌意前缀单出口已
            #    覆盖冲突语义，双机制=共线性死穴，见模块 docstring）
            if edge["affinity"] < _AFFINITY_MIN:
                continue
            source_id = edge["source_id"]
            target_id = edge["target_id"]
            # 2) 范围闸门：双方都在背景 NPC 集合且不同（核心 NPC 有
            #    LLM 对话链，不进规则社交回路）
            if source_id == target_id:
                continue
            if source_id not in self._npcs or target_id not in self._npcs:
                continue
            # 3) 判重闸门：无序对当天已互动过则跳过（每对每天一次）
            pair = tuple(sorted((source_id, target_id)))
            if pair in self._interacted:
                continue
            # 4) 位置闸门：两实体同地（社交必须面对面）
            source_entity = self._world.entities.get(source_id)
            target_entity = self._world.entities.get(target_id)
            if source_entity is None or target_entity is None:
                continue
            if source_entity.location_id != target_entity.location_id:
                continue
            # 5) 状态闸门：WORKING/IDLE 才社交（None=作息未应用保守不动；
            #    SLEEPING 深夜不社交）
            if self._npcs[source_id].state_str not in _SOCIAL_STATES:
                continue
            if self._npcs[target_id].state_str not in _SOCIAL_STATES:
                continue
            # 触发：登记判重（先记后发，发布的必已登记）
            self._interacted.add(pair)
            # 文案池分派（单一查表；未映射类型兜底交情池）+ per-pair
            # 触发计数取模确定性轮转（与 HabitLoop 模板轮换同构）
            pool = TEMPLATE_POOLS.get(edge["relation"], FALLBACK_TEMPLATES)
            counter = self._trigger_counts.get(pair, 0)
            template = pool[counter % len(pool)]
            self._trigger_counts[pair] = counter + 1
            speaker = self._npcs[source_id]
            other = self._npcs[target_id]
            text = (template
                    .replace("{npc_name}", speaker.name)
                    .replace("{other_name}", other.name))
            self._world.bus.publish(WorldEvent(
                self._world.tick_count, "npc_action", source_id,
                {"summary": text, "npc": source_id, "with": target_id,
                 "relation": edge["relation"]}))

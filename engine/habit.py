"""T5 行为经济学第一小步：杜希格习惯回路离散截断（0-token，2026-10-14）。

设计（杜希格《习惯的力量》三段回路的离散截断）：
- 线索（cue）＝ 时钟离散窗口 ``hour ∈ DUSK_HOURS = {17, 18}``（黄昏）；
- 惯例（routine）＝ 移步酒馆（``tavern``）+ 发布一条确定性中文文案
  （``HABIT_TEMPLATES`` 按触发计数取模轮转，与 BackgroundNPC 反应
  模板轮换同构）；
- 奖赏（reward）＝ 叙事层"喝一杯"（文案本身即奖赏的玩家可感知投影）。
  连续奖赏函数/渴望强度曲线按纸面哲学砍除——离散截断后三者全部
  可在纯规则层表达，无需数值化。

习惯推导口径（社会关系→习惯的数据流）：
与酒馆老板 ``tavern_sun`` 存在任一方向的"酒友"或"老主顾"边
（``PUB_RELATION_TYPES`` 精确匹配，"主顾/供货商/东家"等近义类型
不命中）的背景 NPC 获得"黄昏酒馆"习惯——遍历关系边一次构建 id
集合，tavern_sun 本身若是背景 NPC 则排除（实际它是核心 NPC 不在
背景层）。叙事依据：常客黄昏自然要去喝一杯（doc_qin 老主顾 /
guard_wang 酒友 / fisher_zhou 老主顾，17-18 点三人恰处 IDLE 段）。

触发闸门（apply(hour) 每 tick 一次，四层全过才行动）：
1. 黄昏窗口：hour ∈ {17, 18}，非黄昏不动；
2. 同 hour 判重：同一 NPC 同一 hour 至多发布一条习惯 npc_action
   （``_last_trigger_hour``，沿 BackgroundNPC._last_react_hour 模式；
   记录的 hour 必对应实际发布）；
3. 状态闸门：NPC 当前作息状态为 IDLE（读 ``BackgroundNPC.state_str``
   只读 property；None＝作息未应用，保守不行动；WORKING/SLEEPING
   打烊时段不扰）；
4. 位置闸门：当前地点 ≠ tavern（已在地不重复触发）。

熄灯语义（与作息层的交接设计）：HabitLoop 不主动把 NPC 送回居所
——19/20/21 点各 NPC apply_schedule 进入 SLEEPING 时状态变化自然
移居所（既有 G6-B 作息移动接管，doc_qin 19 点/fisher_zhou 21 点/
guard_wang 20 点各回各家），习惯层只在黄昏窗口内"拉人进酒馆"，
熄灯交给作息层。引擎 tick 中调用顺序：背景 NPC apply_schedule
循环之后（先作息状态落地、再习惯回路消费 IDLE 状态）。

封印语义（Tier 0/1）：HabitLoop 与 EnvMind 同属封印层——不持有
任何 LLM 引用、不组装任何 prompt、习惯状态不进任何 LLM 上下文；
行为出口仅为 world.move_entity 位移 + npc_action 确定性中文文案。
``relationships=None`` 时零习惯零行为（与无 HabitLoop 等价），
空关系网络同样安全（习惯集合为空集）。
"""
from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional

from .background_npc import BackgroundNPC
from .relationships import RelationshipNetwork
from .world import World, WorldEvent

# 黄昏窗口（习惯线索的离散时钟窗口）
DUSK_HOURS = frozenset({17, 18})

# 习惯锚点：酒馆（configs/locations.json 的 tavern，老板 tavern_sun 经营）
HABIT_LOCATION = "tavern"

# 酒馆老板 id（习惯锚点 NPC；核心 NPC，不在背景层，排除自身）
TAVERN_KEEPER = "tavern_sun"

# 酒馆社交边类型（与 tavern_sun 任一方向的这些关系边 → 黄昏酒馆习惯；
# 精确匹配："主顾/供货商/东家"等近义类型不命中——常客语义收紧）
PUB_RELATION_TYPES = frozenset({"酒友", "老主顾"})

# 习惯文案模板（纯中文，{npc_name} 占位；确定性轮转=触发计数取模，
# 与 BackgroundNPC 反应模板轮换同构。奖赏=叙事层"喝一杯"的玩家可感知投影）
HABIT_TEMPLATES: tuple = (
    "（收了工，{npc_name}熟门熟路拐进酒馆）",
    "（天色擦黑，{npc_name}照老规矩坐上了酒馆常客的位子）",
    "（黄昏了，{npc_name}循着熟悉的酒香踱进酒馆）",
)

# 触发状态闸门：仅收工歇脚（IDLE）时段的常客才循黄昏线索行动
_IDLE_STATE = "IDLE"


class HabitLoop:
    """杜希格习惯回路离散截断（纯规则 0-token，背景 NPC 范围）。

    黄昏（hour∈{17,18}）时把有酒馆社交关系边的 IDLE 背景 NPC 拉进
    酒馆并发一条确定性习惯文案；熄灯不归位——19/20/21 点由各 NPC
    既有 apply_schedule SLEEPING 移居所接管（见模块 docstring 交接
    设计）。relationships=None 时零习惯零行为，空网络时习惯集合为
    空集，两者均与无 HabitLoop 等价（向后兼容）。
    """

    def __init__(self, world: World,
                 background_npcs: Iterable[BackgroundNPC] | Mapping[str, BackgroundNPC],
                 relationships: Optional[RelationshipNetwork]) -> None:
        self._world = world
        items = (background_npcs.values()
                 if isinstance(background_npcs, Mapping)
                 else background_npcs)
        self._npcs: Dict[str, BackgroundNPC] = {bg.id: bg for bg in items}
        self._habit_ids = self._derive_habit_ids(relationships)
        # 触发计数（文案确定性轮转）：npc_id → 已发布习惯文案条数
        self._trigger_counts: Dict[str, int] = {}
        # 同 hour 判重：npc_id → 最近一次实际发布习惯文案的 hour
        self._last_trigger_hour: Dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # 习惯推导（离散、确定性：遍历关系边一次构建 id 集合）
    # ------------------------------------------------------------------ #
    def _derive_habit_ids(self,
                          relationships: Optional[RelationshipNetwork]
                          ) -> frozenset:
        """从关系网推导"黄昏酒馆"习惯 NPC id 集合。

        口径：与 tavern_sun 存在任一方向 PUB_RELATION_TYPES 边
        （酒友/老主顾）的背景 NPC 命中；tavern_sun 本身排除（锚点
        NPC 不自触发）；relationships=None 或无边命中时为空集。
        """
        if relationships is None:
            return frozenset()
        habit: set = set()
        for edge in relationships.to_dict():
            if edge["relation"] not in PUB_RELATION_TYPES:
                continue
            if edge["target_id"] == TAVERN_KEEPER:
                other = edge["source_id"]
            elif edge["source_id"] == TAVERN_KEEPER:
                other = edge["target_id"]
            else:
                continue
            if other != TAVERN_KEEPER and other in self._npcs:
                habit.add(other)
        return frozenset(habit)

    @property
    def habit_npc_ids(self) -> frozenset:
        """习惯 NPC id 集合（只读观测出口，测试/调试用；不进 prompt）。"""
        return self._habit_ids

    # ------------------------------------------------------------------ #
    # 触发（每 tick 一次；四层闸门：黄昏窗口→同 hour 判重→IDLE→非在地）
    # ------------------------------------------------------------------ #
    def apply(self, hour: int) -> None:
        """黄昏习惯触发：窗口内把 IDLE 常客拉进酒馆并发布一条文案。

        非黄昏 hour 不动；同 hour 判重（至多一条习惯 npc_action）；
        状态非 IDLE（含 None＝作息未应用）不扰；已在地不重复触发。
        移动失败（地点未注册等）视同未触发、不记判重 hour——记录的
        hour 必对应实际发布（与 BackgroundNPC 反应判重同纪律）。
        """
        if hour not in DUSK_HOURS:
            return
        for npc_id in sorted(self._habit_ids):
            bg = self._npcs.get(npc_id)
            if bg is None:
                continue
            # 同 hour 判重（记录仅在发布后落，见下）
            if self._last_trigger_hour.get(npc_id) == hour:
                continue
            # 状态闸门：仅收工歇脚（IDLE）时段循黄昏线索行动
            if bg.state_str != _IDLE_STATE:
                continue
            # 位置闸门：已在地不重复触发
            if bg.entity.location_id == HABIT_LOCATION:
                continue
            if not self._world.move_entity(npc_id, HABIT_LOCATION):
                continue
            self._last_trigger_hour[npc_id] = hour
            # 确定性文案轮转（触发计数取模，与反应模板轮换同构）
            counter = self._trigger_counts.get(npc_id, 0)
            template = HABIT_TEMPLATES[counter % len(HABIT_TEMPLATES)]
            self._trigger_counts[npc_id] = counter + 1
            text = template.replace("{npc_name}", bg.name)
            self._world.bus.publish(WorldEvent(
                self._world.tick_count, "npc_action", npc_id,
                {"summary": text, "npc": npc_id}))

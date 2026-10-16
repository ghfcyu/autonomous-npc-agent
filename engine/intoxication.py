"""T6 第一小步：醉酒结算最小闭环（酒量属性驱动的烂醉瞬态标签 + 失态事件）。

三条纪律（模块存在依据，tests/test_intoxication.py ast 断言守护）
----------------------------------------------------------------
1. 六情边界（共线性死穴）——饮酒的情绪语义已由 item_given 的荀子六情
   "好"矢量独占（赠酒=送礼，inner_state.StateUpdater._on_gift → e_P
   增量 + 亲缘度写入关系网）。本模块是独立规则层，严禁 import/修改
   任何 8D 心智参数——同一事件双份情绪增量 = 两套情绪系统漂移的
   共线性死穴（economics.WealthLedger 同一封印口径）。
2. 纸面哲学砍除——不做代谢率/血液酒精浓度连续曲线。玩家可感知的只有
   "几碗倒"与"多久醒"，全部机制离散为"累计阈值 + TTL"两参数：
   bowls_needed 阈值结算（ceil(4×alcohol_tol/5)，规范 4.3 锚点
   "5.0：两角微醺，四角大醉"的离散换算）+ TagLedger.add_transient
   4 小时 TTL（挂载/递减/归零注销全部复用 tag_mount 既有机制，零新
   生命周期代码）。
3. Tier 0 纯规则零 token——不组装 prompt、不持有 LLM 引用、不进快脑
   意图域。全部文案（交易拒绝句/失态事件模板/垫话醉变体/快脑酒嗝
   前缀）为确定性短句常量，由既有消费方（engine/filler/fast_brain）
   按需取用；烂醉状态对决策链的唯一出口是 TRANSIENT 桶标签经
   visible_tags() 自动进身份标签行——零新注入机制。

与 WealthLedger 的结构对齐（订阅者先例，engine/economics.py）
------------------------------------------------------------
- EventBus 订阅者：on_event 只消费 item_given（engine 按 kinds 订阅，
  守卫双保险）；
- 幂等建档：ensure_tolerance 对齐 ensure_account——档案已存在原样
  返回不覆盖（建档是创生事件）；
- 单一来源：酒量档案初值单一来源 generate_village() 的
  profile.innate.alcohol_tol（tag_genesis 既有先天 6D，零新增配置、
  零新增字段——新独立字段 = 同一现象两套变量 = 共线性死穴）；
- 失态事件发布走 world.bus.publish 既有管道，记忆写入走
  MemorySystem.observe 既有 npc_drunk 模板——零平行机制。

默认村庄零行为：无赠饮 → 酒账恒零 → 瞬态桶恒空 → prompt 逐字节
不变（token 基线 4519 不动）。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Dict

from .tag_mount import MountedTag, TagPhase
from .world import WorldEvent

if TYPE_CHECKING:  # 仅为类型注解服务，避免运行期循环导入
    from .npc import NPC
    from .world import World

DRUNK_TAG_ID = "state_drunk"
DRUNK_LABEL = "烂醉如泥"
DRUNK_TTL_HOURS = 4
DRUNK_BOWL_FACTOR = 4    # 规范 4.3 锚点"5.0：两角微醺，四角大醉"的离散换算分子
DRUNK_BOWL_DIVISOR = 5
DRUNK_REFUSAL = "（趴在桌上打鼾）……嗝……买卖……明儿再说……"    # 交易拒绝固定句（单条不轮转：无状态确定性；ARCHITECTURE T6 提案 1.2 节原文——PM 10-17 验收勘误：初版契约误重"打在桌上"一段，A/B 子代理均上报，按提案原文修正）
DRUNK_FAILURES = ("{name}喝得烂醉如泥，趴在桌上不省人事",
                  "{name}醉得东倒西歪，扶着桌子直打晃")    # 失态事件 summary 模板，按 victim 计数取模轮转，{name}=persona.name
DRUNK_FILLERS = ("（舌头打结）嗝……说、说啥？",
                 "（趴在桌上）嗝……有、有事？")           # 垫话醉变体池（filler.py 消费；每条 ≤15 字符，垫话纪律）
FAST_DRUNK_PREFIX = "（打着酒嗝）"                       # 快脑醉前缀（fast_brain.py 消费）


def bowls_needed(alcohol_tol: float) -> int:
    """烂醉所需累计碗数 = ceil(4 × alcohol_tol / 5)。

    规范 4.3 锚点"5.0：两角微醺，四角大醉"的离散换算：酒量 5.0 的常人
    四碗（角）大醉。分叉实例（generate_village 真实档案）：tavern_sun
    2.84 → 3 碗 / lily 2.96 → 3 碗 / chen 5.90 → 5 碗——酒量属性驱动的
    玩家可感知分叉，无任何手写常量。
    """
    return math.ceil(DRUNK_BOWL_FACTOR * alcohol_tol / DRUNK_BOWL_DIVISOR)


def is_dead_drunk(npc) -> bool:
    """该 NPC 当前是否烂醉：账本 all_tags() 中存在 state_drunk。

    无账本（直接构造/未挂 genesis 账本的 NPC）→ False；只用 TagLedger
    公开 API all_tags()，不触内部桶（与决策层 visible_tags 消费同一
    封印口径）。
    """
    ledger = getattr(npc, "tag_ledger", None)
    if ledger is None:
        return False
    return any(tag.tag_id == DRUNK_TAG_ID for tag in ledger.all_tags())


class IntoxicationLedger:
    """全村酒账：EventBus 订阅者 + 幂等建档（Tier 0 纯规则，零 token）。

    结构对齐 economics.WealthLedger：world 引用 + 核心 NPC 注册表引用
    （engine.npcs 的同一 dict 对象——构造先于 spawn，事件到来时注册表
    已填充）。三个内部 dict：

    - _tolerance：npc_id → alcohol_tol（创生档案，建后不改）；
    - _bowls：npc_id → 未结算累计碗数（结算清零——酒醒后须重新累计
      满轮才再醉）；
    - _fail_counts：npc_id → 失态文案轮转计数（DRUNK_FAILURES 池按
      victim 计数取模轮转，与反应模板轮换/前缀池同构）。

    纪律：严禁 import/修改任何 8D 心智参数（六情边界见模块 docstring）；
    严禁组装 prompt——烂醉对决策链的唯一影响通道是 TRANSIENT 桶标签经
    visible_tags() 自动消费（零新注入机制）。
    """

    def __init__(self, world: "World", npcs: Dict[str, "NPC"]) -> None:
        self._world = world
        self._npcs = npcs
        self._tolerance: Dict[str, float] = {}
        self._bowls: Dict[str, int] = {}
        self._fail_counts: Dict[str, int] = {}

    def ensure_tolerance(self, npc_id: str, alcohol_tol: float) -> None:
        """幂等建档：档案已存在则原样返回不覆盖（对齐 WealthLedger.ensure_account
        语义——建档是创生事件，不改档）。"""
        if npc_id not in self._tolerance:
            self._tolerance[npc_id] = alcohol_tol

    def on_event(self, event: WorldEvent) -> None:
        """EventBus 订阅入口：只消费 item_given（engine 按 kinds 订阅，守卫双保险）。

        守卫链（顺序即语义）：
        1. kind 非 item_given → return；
        2. payload["to"] 无酒量档案 → return（自定义 NPC 零行为）；
        3. payload["item"] 非 str 或不含"酒"字符 → return（酒判定：物品
           词含"酒"即计一碗，确定性——米酒/陈酿类都命中）；
        4. to_id 不在核心 NPC 注册表 → return（背景 NPC 不在 engine.npcs，
           不结算——player_gives 入口天然限定核心 NPC，本守卫直发事件
           场景双保险）；
        5. 烂醉期再赠酒：add_transient 重挂同一标签对象，TTL 覆盖重置
           4 小时（tag_mount docstring"又一轮买醉刷新醉酒时长"语义），
           return（计数不涨）；
        6. 未烂醉：_bowls += 1；达 bowls_needed(档案值) → _settle（挂
           瞬态标签 + 清零 + 发失态事件）；未达仅累计。
        """
        if event.kind != "item_given":
            return
        to_id = event.payload.get("to")
        if to_id not in self._tolerance:
            return
        item = event.payload.get("item")
        if not isinstance(item, str) or "酒" not in item:
            return
        if to_id not in self._npcs:
            return
        npc = self._npcs[to_id]
        if is_dead_drunk(npc):
            # is_dead_drunk 为 True 蕴含 tag_ledger 非 None（同标签重挂）
            npc.tag_ledger.add_transient(
                MountedTag(tag_id=DRUNK_TAG_ID, label=DRUNK_LABEL,
                           phase=TagPhase.TRANSIENT),
                DRUNK_TTL_HOURS)
            return
        self._bowls[to_id] = self._bowls.get(to_id, 0) + 1
        if self._bowls[to_id] >= bowls_needed(self._tolerance[to_id]):
            self._settle(npc)

    def _settle(self, npc: "NPC") -> None:
        """烂醉结算（Tier 0 纯规则，四步确定性）：

        1. 挂 TRANSIENT 瞬态标签 state_drunk/烂醉如泥，TTL 4 世界小时
           （phase 由 add_transient 内部 replace 为 TRANSIENT）——公开
           体态走自动管道：visible_tags() 渲染序含 TRANSIENT 桶，决策层
           身份标签行自动消费（"断指的铁匠今天烂醉如泥"）；
        2. 碗数清零（结算即清：酒醒后须重新累计满轮才再醉）；
        3. 失态文案按 victim 计数取模轮转 DRUNK_FAILURES，{name} 以
           npc.persona.name 填充，计数 +1；
        4. 发布 npc_drunk 事件（actor=victim npc_id）——victim 经
           npc._on_event 的 event.actor==self.persona.id 点名分支感知，
           同地目击者经同地过滤感知（掌柜记得谁的酒品差），均走
           MemorySystem.observe 的 npc_drunk 模板 0.75 ≥ 0.7 长期直写。
           与 item_given 不同 kind，嵌套发布不迭代同一订阅列表。
        """
        npc_id = npc.persona.id
        ledger = getattr(npc, "tag_ledger", None)
        if ledger is not None:
            ledger.add_transient(
                MountedTag(tag_id=DRUNK_TAG_ID, label=DRUNK_LABEL,
                           phase=TagPhase.TRANSIENT),
                DRUNK_TTL_HOURS)
        self._bowls[npc_id] = 0
        count = self._fail_counts.get(npc_id, 0)
        text = DRUNK_FAILURES[count % len(DRUNK_FAILURES)].format(
            name=npc.persona.name)
        self._fail_counts[npc_id] = count + 1
        self._world.bus.publish(WorldEvent(
            self._world.tick_count, "npc_drunk", npc_id,
            {"summary": text, "npc": npc_id}))

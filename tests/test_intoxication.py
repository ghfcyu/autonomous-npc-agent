"""T6 第一小步 · 醉酒结算最小闭环验收测试（engine/intoxication.py + 接线）。

被测契约（实现已冻结；与并行子代理 B 的 tests/test_drunk_behavior.py
共享同一实现契约，本文件只测不改）：
1. 酒量档案单一来源：NPCEngine.__init__ 对 generate_village() 全部
   25 人 ensure_tolerance（profile.innate.alcohol_tol，幂等不覆盖），
   订阅 world.bus.subscribe(..., kinds=["item_given"])；
2. 烂醉结算：累计碗数 >= bowls_needed(tol) = ceil(4×tol/5) →
   TagLedger.add_transient(state_drunk/烂醉如泥, ttl=4) + 碗数清零 +
   发布 npc_drunk 失态事件（DRUNK_FAILURES 池按 victim 计数取模轮转）；
3. on_event 守卫链：无档案不计、非"酒"物品不计、item 非 str 防御、
   非核心 NPC 不结算、烂醉期再赠酒只重置 TTL 不涨计数；
4. 瞬态生命周期泵（"有桶无泵"修复）：engine.tick 同 hour 判重
   （1 次 ledger.tick() = 1 世界小时），TTL 归零自动熄灭 → 身份标签行
   恢复（决策层零改动，TRANSIENT 桶经 visible_tags() 自动消费）；
5. 烂醉玩家可感知三通道：交易拒绝（DRUNK_REFUSAL 固定句零 LLM）/
   垫话醉变体（DRUNK_FILLERS 轮转，醉态覆盖脾气常态）/ 快脑酒嗝
   前缀（FAST_DRUNK_PREFIX，日常句快脑照常秒回）；
6. 记忆：victim 点名感知 + 同地目击者感知，均走 MemorySystem.observe
   的 npc_drunk 模板（importance 0.75 >= 0.7 长期直写）；
7. 架构边界：npc_drunk 事件对全村核心 NPC 8D 零增量（六情边界锁——
   饮酒的情绪语义由 item_given 荀子六情"好"矢量独占）；intoxication
   源码零 inner_state 引用（ast 断言，沿 tests/test_economics.py 先例）；
   默认场景零行为（机制惰性——token 基线 4519 不动的根）。

编写纪律：零第三方依赖（unittest 标准库）；引擎构造沿
tests/test_tavern_memory.py 的 RecordingLLM + random.seed 双保险模式；
时间推进沿同文件口径 engine.tick(minutes=60) 跨一小时界（世界 10 分钟
粒度，构造起点 8:00）；酒量阈值一律经 generate_village() 取真实档案再
经 bowls_needed() 计算（真实数据驱动纪律，严禁手写 tol 数字）。
"""

import ast
import random
import unittest

from engine.engine import NPCEngine
from engine.filler import FillerEngine
from engine.inner_state import InnerState
from engine.intoxication import (DRUNK_FAILURES, DRUNK_FILLERS, DRUNK_LABEL,
                                 DRUNK_REFUSAL, DRUNK_TAG_ID, DRUNK_TTL_HOURS,
                                 FAST_DRUNK_PREFIX, IntoxicationLedger,
                                 bowls_needed, is_dead_drunk)
from engine.llm.mock import MockLLMProvider
from engine.memory import LONG_TERM_IMPORTANCE_THRESHOLD
from engine.npc import NPC, Persona
from engine.tag_mount import TagPhase
from engine.village_genesis import generate_village
from engine.world import World, WorldEvent

# 21 字慢脑句：超 greet/presence/farewell(6)/direction(12)/price(8)/
# time(8) 全部快脑句长闸门，且不含赞美/批评关键词（沿 test_economics
# 的 SLOW_ASK 同款口径），稳定走慢脑产生 prompt。
SLOW_ASK = "陈师傅，跟您打听个事儿，您这儿最近可有什么新鲜玩意儿？"
# 19 字慢脑句（tavern_sun 场景，沿 test_tavern_memory 的 ASK_FIRST 同款）。
TAVERN_ASK = "掌柜的，最近店里生意怎么样，客人多不多？"


class RecordingLLM(MockLLMProvider):
    """记录完整 messages 的 Mock（prompt 内容级断言用，沿
    tests/test_tavern_memory.py 同款装配模式；本文件自持一份，
    不改既有测试文件）。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.captured = []

    def chat(self, messages, temperature=0.7):
        self.captured.append([dict(m) for m in messages])
        return super().chat(messages, temperature=temperature)


def _fresh_engine(seed: int = 20261016):
    """fresh 引擎 + 录制 LLM：独立世界与酒账，用例间零污染。

    random.seed 双保险（沿 tests/test_tavern_memory.py 模式）：tick 推进
    时天气变化走全局 random（world.py WEATHER_CHANGE_PROB=0.08），seed
    固定保证用例对天气触发与否完全确定（短期记忆窗口不漂移）。
    """
    random.seed(seed)
    llm = RecordingLLM()
    engine = NPCEngine(llm=llm)
    return llm, engine


def _needed(npc_id: str) -> int:
    """真实数据驱动阈值：generate_village() 创生档案 → bowls_needed()
    （严禁手写 tol 数字——酒量分叉由属性驱动，无手写名单）。"""
    return bowls_needed(generate_village()[npc_id].innate.alcohol_tol)


def _give_bowls(engine, npc_id: str, n: int) -> None:
    """宽松模式赠饮 n 碗（player_gives 玩家无该物品也发布 item_given
    事件，测试无需先放背包）。"""
    for _ in range(n):
        engine.player_gives(npc_id, "米酒")


def _drunk_events(engine):
    """事件历史中的 npc_drunk 失态事件列表。"""
    return [e for e in engine.world.bus.history if e.kind == "npc_drunk"]


# ============================================================================ #
# 1. 酒量档案：单一来源建账 + 幂等
# ============================================================================ #

class TestToleranceRegistry(unittest.TestCase):
    """引擎建账：全村 25 人档案逐人复用创生档案；ensure_tolerance 幂等。"""

    def test_registry_matches_village_genesis(self):
        """①引擎建账后 25 人酒量档案与 generate_village() 创生档案逐人
        全等（键集全等 + 逐人值全等——两套创生逻辑漂移即红）。"""
        _llm, engine = _fresh_engine()
        profiles = generate_village()
        self.assertEqual(len(engine.intoxication_ledger._tolerance), 25)
        self.assertEqual(set(engine.intoxication_ledger._tolerance),
                         set(profiles))
        for npc_id, profile in profiles.items():
            self.assertEqual(
                engine.intoxication_ledger._tolerance[npc_id],
                profile.innate.alcohol_tol,
                msg=f"{npc_id} 酒量档案与创生档案不一致")

    def test_ensure_tolerance_idempotent_no_overwrite(self):
        """②ensure_tolerance 幂等（行为口径）：以低值 1.0 重复建档后
        chen 仍在真实档案阈值（5 碗）才醉——若被覆盖则第 1 碗即结算，
        断言即红（对齐 WealthLedger.ensure_account"建账是创生事件"）。"""
        _llm, engine = _fresh_engine()
        engine.intoxication_ledger.ensure_tolerance("chen", 1.0)
        needed = _needed("chen")
        self.assertGreater(needed, 1,
                           msg="前置：chen 真实阈值须 > 1 才能区分覆盖与否")
        _give_bowls(engine, "chen", needed - 1)
        self.assertFalse(is_dead_drunk(engine.npcs["chen"]))
        _give_bowls(engine, "chen", 1)
        self.assertTrue(is_dead_drunk(engine.npcs["chen"]),
                        msg="重档未覆盖：仍在满轮第 needed 碗才醉")


# ============================================================================ #
# 2. on_event 守卫链
# ============================================================================ #

class TestEventGuards(unittest.TestCase):
    """守卫链：无档案/非酒物品/非 str item/非核心 NPC/非 item_given。"""

    def test_no_tolerance_npc_zero_behavior(self):
        """①无酒量档案 NPC（自定义人格，不在创生花名册）收到赠酒事件
        零行为：不计数、不结算、无 npc_drunk 事件（自定义 NPC 零行为）。"""
        persona = Persona(id="wanderer", name="流浪客", role="villager",
                          location_id="plaza", greeting_bank=["……"],
                          fallback_bank=["……"])
        engine = NPCEngine(llm=MockLLMProvider(), npc_configs=[persona])
        for _ in range(5):
            self.assertTrue(engine.player_gives("wanderer", "米酒")["ok"])
        self.assertFalse(is_dead_drunk(engine.npcs["wanderer"]))
        self.assertNotIn("wanderer", engine.intoxication_ledger._bowls)
        self.assertEqual(_drunk_events(engine), [])

    def test_non_alcohol_items_do_not_count(self):
        """②非"酒"物品不计碗：酱肉/铁剑/干粮赠 chen 后累计严格为零；
        补满真实阈值才醉（证明此前零计数）。"""
        _llm, engine = _fresh_engine()
        engine.player_gives("chen", "酱肉")
        engine.player_gives("chen", "铁剑")
        engine.player_gives("chen", "干粮")
        self.assertFalse(is_dead_drunk(engine.npcs["chen"]))
        self.assertNotIn("chen", engine.intoxication_ledger._bowls)
        _give_bowls(engine, "chen", _needed("chen"))
        self.assertTrue(is_dead_drunk(engine.npcs["chen"]),
                        msg="非酒物品零计数：须满真实阈值整轮才醉")

    def test_non_string_item_defensive(self):
        """③payload["item"] 非 str 防御：None/整数/列表一律跳过，不
        计数不崩溃；防御性跳过不影响后续正常计数。"""
        _llm, engine = _fresh_engine()
        for bad_item in (None, 123, ["米酒"]):
            engine.world.bus.publish(WorldEvent(
                engine.world.tick_count, "item_given", "player",
                {"to": "chen", "item": bad_item}))
        self.assertNotIn("chen", engine.intoxication_ledger._bowls)
        _give_bowls(engine, "chen", _needed("chen"))
        self.assertTrue(is_dead_drunk(engine.npcs["chen"]),
                        msg="畸形 payload 零计数：正常赠饮仍须满轮才醉")

    def test_non_item_given_kind_ignored(self):
        """④非 item_given 事件不入账（引擎按 kinds 订阅，on_event 守卫
        双保险——直发其他 kind 的"带酒 payload"事件也零计数）。"""
        ledger = IntoxicationLedger(World(), {})
        ledger.ensure_tolerance("chen", 5.9)
        for kind in ("player_spoke", "item_bought", "item_sold",
                     "npc_action", "time_passed"):
            ledger.on_event(WorldEvent(0, kind, "player",
                                       {"to": "chen", "item": "米酒"}))
        self.assertEqual(ledger._bowls, {})

    def test_background_npc_with_tolerance_not_settled(self):
        """⑤有创生档案但非核心 NPC（背景 NPC 不在 engine.npcs）不
        结算：doc_qin 直发赠酒事件 10 碗零计数零失态事件（守卫序位
        "非核心 NPC"——player_gives 入口天然限定，本用例锁直发场景）。"""
        _llm, engine = _fresh_engine()
        self.assertIn("doc_qin", generate_village())  # 有创生酒量档案
        self.assertNotIn("doc_qin", engine.npcs)        # 但非核心 NPC
        for _ in range(10):
            engine.world.bus.publish(WorldEvent(
                engine.world.tick_count, "item_given", "player",
                {"to": "doc_qin", "item": "米酒"}))
        self.assertNotIn("doc_qin", engine.intoxication_ledger._bowls)
        self.assertEqual(_drunk_events(engine), [])


# ============================================================================ #
# 3. bowls_needed 数学（真实档案驱动）
# ============================================================================ #

class TestBowlsMath(unittest.TestCase):
    """烂醉碗数 = ceil(4 × alcohol_tol / 5)：真实档案分叉 + 规范锚点。"""

    def test_real_genesis_thresholds(self):
        """①generate_village() 真实档案经 API 计算：tavern_sun 3 碗、
        lily 3 碗、chen 5 碗（酒量差的先倒——玩家可感知分叉，无手写
        名单）。"""
        self.assertEqual(_needed("tavern_sun"), 3)
        self.assertEqual(_needed("lily"), 3)
        self.assertEqual(_needed("chen"), 5)

    def test_spec_anchor_and_discrete_output(self):
        """②规范 4.3 锚点自检：tol=5.0 → ceil(4×5/5)=4 碗（"四角大
        醉"）；输出恒为 int（离散碗数——"几碗倒"玩家可感知，无浮点）。"""
        self.assertEqual(bowls_needed(5.0), 4)
        for tol in (1.0, 2.84, 5.9, 10.0):
            self.assertIsInstance(bowls_needed(tol), int)


# ============================================================================ #
# 4. 达阈值结算：TRANSIENT 桶挂载 + 碗数清零
# ============================================================================ #

class TestSettleAndTagMount(unittest.TestCase):
    """结算契约：标签挂载可见性 + 结算清零（重新满轮语义）。"""

    def test_settle_mounts_transient_visible_tag(self):
        """①第 (needed-1) 碗不结算；第 needed 碗挂 state_drunk/烂醉如泥
        进 TRANSIENT 桶：phase 为 TRANSIENT、非 secret、visible_tags()
        含它（公开体态——旁人看得见醉汉）、secrets() 不含。"""
        _llm, engine = _fresh_engine()
        chen = engine.npcs["chen"]
        needed = _needed("chen")
        _give_bowls(engine, "chen", needed - 1)
        self.assertFalse(is_dead_drunk(chen))
        _give_bowls(engine, "chen", 1)
        self.assertTrue(is_dead_drunk(chen))
        mounted = [t for t in chen.tag_ledger.all_tags()
                   if t.tag_id == DRUNK_TAG_ID]
        self.assertEqual(len(mounted), 1)
        self.assertEqual(mounted[0].label, DRUNK_LABEL)
        self.assertEqual(mounted[0].phase, TagPhase.TRANSIENT)
        self.assertFalse(mounted[0].secret)
        self.assertIn(DRUNK_LABEL,
                      [t.label for t in chen.tag_ledger.visible_tags()])
        self.assertNotIn(DRUNK_TAG_ID,
                         [t.tag_id for t in chen.tag_ledger.secrets()])

    def test_settle_resets_bowl_counter(self):
        """②结算清零：烂醉 → 推进时间至 TTL 熄灭 → 再赠 (needed-1)
        碗不醉、第 needed 碗再醉（酒醒后须重新累计满轮才再醉）。"""
        _llm, engine = _fresh_engine()
        needed = _needed("chen")
        _give_bowls(engine, "chen", needed)
        self.assertTrue(is_dead_drunk(engine.npcs["chen"]))
        self.assertEqual(engine.intoxication_ledger._bowls["chen"], 0)
        for _ in range(5):  # 跨 4 小时界熄灭（+1 小时余量）
            engine.tick(60)
        self.assertFalse(is_dead_drunk(engine.npcs["chen"]))
        _give_bowls(engine, "chen", needed - 1)
        self.assertFalse(is_dead_drunk(engine.npcs["chen"]),
                         msg="熄灭后 (needed-1) 碗不得再醉（计数已清零）")
        _give_bowls(engine, "chen", 1)
        self.assertTrue(is_dead_drunk(engine.npcs["chen"]),
                       msg="重新满轮第 needed 碗应再醉")


# ============================================================================ #
# 5. 瞬态生命周期泵（"有桶无泵"修复）
# ============================================================================ #

class TestTransientLifecycle(unittest.TestCase):
    """TTL 泵接线：覆盖重置、同 hour 判重、到期自动熄灭。"""

    def test_ttl_override_on_gift_during_drunk(self):
        """①烂醉期再赠酒 TTL 覆盖重置（行为口径）：挂载后跨 2 小时界
        → 再赠 1 碗 → 再跨 3 小时界仍醉、第 4 小时界才熄灭；且计数
        不涨（"又一轮买醉刷新醉酒时长"）。"""
        _llm, engine = _fresh_engine()
        _give_bowls(engine, "chen", _needed("chen"))
        self.assertTrue(is_dead_drunk(engine.npcs["chen"]))
        engine.tick(60)
        engine.tick(60)                       # 跨 2 小时界（TTL 4→2）
        self.assertTrue(is_dead_drunk(engine.npcs["chen"]))
        engine.player_gives("chen", "米酒")   # 烂醉期再赠酒：TTL 覆盖重置 4
        self.assertTrue(is_dead_drunk(engine.npcs["chen"]))
        self.assertEqual(engine.intoxication_ledger._bowls["chen"], 0,
                         msg="烂醉期再赠酒计数不涨（结算已清零）")
        for _ in range(3):
            engine.tick(60)                   # 再跨 3 小时界（TTL 4→1）
        self.assertTrue(is_dead_drunk(engine.npcs["chen"]),
                        msg="TTL 重置后跨 3 小时界应仍醉")
        engine.tick(60)                       # 第 4 小时界：TTL 归零熄灭
        self.assertFalse(is_dead_drunk(engine.npcs["chen"]),
                         msg="TTL 覆盖重置语义：第 4 小时界才熄灭")

    def test_same_hour_pump_dedup(self):
        """②engine.tick 同 hour 判重：挂载后同小时 tick 5 次（10 分钟
        粒度）仍醉——若泵每 tick 都递减，TTL 4-5 < 0 早已熄灭（红）；
        且同小时零偷跑不污染后续结算（跨 3 界仍醉、第 4 界才熄灭）。"""
        _llm, engine = _fresh_engine()
        _give_bowls(engine, "chen", _needed("chen"))
        for _ in range(5):
            engine.tick(10)                    # 8:00→8:50，仍 hour 8
        self.assertEqual(engine.world.hour, 8)
        self.assertTrue(is_dead_drunk(engine.npcs["chen"]),
                        msg="同小时 5 次 tick 只递减一次 TTL（本例零次）：仍烂醉")
        for _ in range(3):
            engine.tick(60)                    # 跨 3 小时界（TTL 4→1）
        self.assertTrue(is_dead_drunk(engine.npcs["chen"]))
        engine.tick(60)                        # 第 4 小时界熄灭
        self.assertFalse(is_dead_drunk(engine.npcs["chen"]),
                         msg="恰好第 4 界熄灭——证明同小时 tick 零偷跑递减")

    def test_ttl_expiry_extinguishes_and_prompt_restores(self):
        """③TTL 4 小时到期自动熄灭：跨 4 小时界后 visible_tags() 不含、
        再对话 system prompt（身份标签行所在）不含"烂醉如泥"——prompt
        恢复基线字节（标签自动管道可逆）。"""
        llm, engine = _fresh_engine()
        _give_bowls(engine, "chen", _needed("chen"))
        self.assertIn(DRUNK_LABEL,
                      [t.label for t in
                       engine.npcs["chen"].tag_ledger.visible_tags()])
        for _ in range(4):
            engine.tick(60)
        self.assertFalse(is_dead_drunk(engine.npcs["chen"]))
        self.assertNotIn(DRUNK_LABEL,
                         [t.label for t in
                          engine.npcs["chen"].tag_ledger.visible_tags()])
        result = engine.player_says(SLOW_ASK, "chen")
        self.assertEqual(result["brain"], "slow",
                         msg="慢脑句前置：须产生可断言的 prompt")
        system_prompt = llm.captured[-1][0]["content"]
        self.assertNotIn(DRUNK_LABEL, system_prompt,
                         msg="熄灭后 system prompt 不得再含烂醉如泥")


# ============================================================================ #
# 6. 失态事件 npc_drunk（payload 契约 + 文案池轮转）
# ============================================================================ #

class TestFailureEvent(unittest.TestCase):
    """失态事件：payload 契约逐字 + DRUNK_FAILURES 按 victim 轮转。"""

    def test_failure_event_payload_and_rotation(self):
        """①结算发布 npc_drunk：payload["summary"] 含 NPC 名字与
        "烂醉如泥"（池首条）、payload["npc"]==victim id、actor==victim；
        第二次结算文案为池内另一条（按 victim 计数取模轮转）。"""
        _llm, engine = _fresh_engine()
        chen_name = engine.npcs["chen"].persona.name
        needed = _needed("chen")
        _give_bowls(engine, "chen", needed)
        events = _drunk_events(engine)
        self.assertEqual(len(events), 1)
        first = events[0]
        self.assertEqual(first.kind, "npc_drunk")
        self.assertEqual(first.actor, "chen")
        self.assertEqual(first.payload["npc"], "chen")
        self.assertIn(chen_name, first.payload["summary"])
        self.assertIn("烂醉如泥", first.payload["summary"])
        self.assertEqual(first.payload["summary"],
                        DRUNK_FAILURES[0].format(name=chen_name))
        # 熄灭后重新满轮：第二次结算为池内另一条（轮转）
        for _ in range(5):
            engine.tick(60)
        _give_bowls(engine, "chen", needed)
        events = _drunk_events(engine)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1].payload["summary"],
                         DRUNK_FAILURES[1].format(name=chen_name),
                         msg="第二次结算失态文案应为池内另一条（轮转）")


# ============================================================================ #
# 7. 记忆感知：victim 点名 + 同地目击（0.75 长期直写）
# ============================================================================ #

class TestMemoryPerception(unittest.TestCase):
    """失态记忆：次晨记得自己失态 / 掌柜记得谁的酒品差。"""

    def test_victim_remembers_own_failure(self):
        """①victim（chen）长期记忆含失态记录：内容为模板产物、
        importance 0.75 >= 0.7（长期直写阈值）、短期双写。"""
        _llm, engine = _fresh_engine()
        chen_name = engine.npcs["chen"].persona.name
        _give_bowls(engine, "chen", _needed("chen"))
        expected = DRUNK_FAILURES[0].format(name=chen_name)
        matches = [r for r in engine.npcs["chen"].memory.long.records
                   if r.content == expected]
        self.assertEqual(len(matches), 1,
                         msg="失态记录应直写长期记忆（0.75 >= 0.7 阈值）")
        self.assertGreaterEqual(matches[0].importance,
                                LONG_TERM_IMPORTANCE_THRESHOLD)
        self.assertEqual(matches[0].importance, 0.75)
        self.assertTrue(any(r.content == expected
                            for r in
                            engine.npcs["chen"].memory.short.recent()),
                        msg="失态记录应同时写入短期记忆（双写）")

    def test_same_location_witness_perceives(self):
        """②同地目击：chen 移到酒馆后烂醉，掌柜 tavern_sun 记忆系统含
        chen 失态记录（同地过滤路径——"掌柜的什么都看在眼里"）；异地
        的 lily 不含。"""
        _llm, engine = _fresh_engine()
        engine.world.move_entity("chen", "tavern")
        _give_bowls(engine, "chen", _needed("chen"))
        expected = DRUNK_FAILURES[0].format(
            name=engine.npcs["chen"].persona.name)
        tavern_contents = [r.content for r in
                            engine.npcs["tavern_sun"].memory.long.records]
        self.assertIn(expected, tavern_contents,
                      msg="同地目击者 tavern_sun 应感知 chen 失态（长期直写）")
        lily_contents = [r.content for r in
                         engine.npcs["lily"].memory.long.records]
        self.assertNotIn(expected, lily_contents,
                          msg="异地 lily 不应感知 chen 失态（同地过滤）")


# ============================================================================ #
# 8. 六情边界锁（8D 零增量）
# ============================================================================ #

class TestZero8DBoundary(unittest.TestCase):
    """npc_drunk 事件 8D 零增量：饮酒情绪语义由 item_given 荀子六情
    独占，醉酒结算层严禁双写（共线性死穴）。"""

    def test_npc_drunk_event_zero_8d_delta(self):
        """①直发一条 npc_drunk 事件前后，全村核心 NPC inner_state
        .to_dict() 逐项全等（StateUpdater 只分发四类已知事件，
        npc_drunk 零分发——六情边界锁死）。"""
        _llm, engine = _fresh_engine()
        before = {nid: npc.inner_state.to_dict()
                  for nid, npc in engine.npcs.items()}
        engine.world.bus.publish(WorldEvent(
            engine.world.tick_count, "npc_drunk", "chen",
            {"summary": "铁匠陈喝得烂醉如泥，趴在桌上不省人事",
             "npc": "chen"}))
        after = {nid: npc.inner_state.to_dict()
                 for nid, npc in engine.npcs.items()}
        self.assertEqual(before, after,
                         msg="npc_drunk 不得改任何 8D 心智参数"
                             "（同一事件双份情绪增量=两套情绪系统漂移）")

    def test_drink_emotion_still_owned_by_gift_channel(self):
        """②对照：赠酒（item_given）的 e_P 增量仍恰为荀子六情"好"矢量
        首分量（六情通道独立运转），醉酒结算层零双写。"""
        from engine.inner_state import XUNZI_EMOTION_VECTORS
        _llm, engine = _fresh_engine()
        chen = engine.npcs["chen"]
        e_p_before = chen.inner_state.e_P
        engine.player_gives("chen", "米酒")
        self.assertAlmostEqual(chen.inner_state.e_P - e_p_before,
                               XUNZI_EMOTION_VECTORS["好"][0],
                               places=9,
                               msg="赠饮情绪增量须由六情通道独占承载")


# ============================================================================ #
# 9. 交易拒绝（三通道之一）
# ============================================================================ #

class TestTradeRefusal(unittest.TestCase):
    """烂醉交易拒绝：确定性文案零 LLM 零事件；未醉正常成交零回归。"""

    def test_drunk_refuses_buys_and_sells(self):
        """①烂醉 chen 的 player_buys/player_sells 均返回
        {"ok": False, "reason": "drunk", "reply": DRUNK_REFUSAL}（契约
        常量逐字）；拒绝先于库存检查（在库铁剑也拒卖）、零副作用
        （库存不动、零 LLM 调用、零新事件）。"""
        _llm, engine = _fresh_engine()
        llm_calls_before = engine.llm.call_count
        history_len = len(engine.world.bus.history)
        chen_inventory = list(engine.world.entities["chen"].inventory)
        self.assertIn("铁剑", chen_inventory)  # 前置：在库现货
        _give_bowls(engine, "chen", _needed("chen"))
        self.assertEqual(engine.player_buys("chen", "铁剑"),
                         {"ok": False, "reason": "drunk",
                          "reply": DRUNK_REFUSAL})
        self.assertEqual(engine.player_sells("chen", "铁剑"),
                         {"ok": False, "reason": "drunk",
                          "reply": DRUNK_REFUSAL})
        self.assertEqual(engine.world.entities["chen"].inventory,
                         chen_inventory, msg="烂醉拒绝不得转移库存")
        self.assertEqual(engine.llm.call_count, llm_calls_before,
                         msg="烂醉拒绝零 LLM 调用")
        self.assertEqual(len(engine.world.bus.history) - history_len,
                         5 + 1,  # 5 次 item_given + 1 次 npc_drunk
                         msg="拒绝本身零事件发布（5 赠饮 + 1 失态恰为全部增量）")

    def test_sober_trade_zero_regression(self):
        """②未醉 NPC 正常成交零回归：player_buys 禀赋溢价成交（铁剑
        10→25）+ player_sells 标准价收购（10）——T5 既有契约不受本步
        影响。"""
        _llm, engine = _fresh_engine()
        bought = engine.player_buys("chen", "铁剑")
        self.assertTrue(bought["ok"])
        self.assertEqual(bought["price"], 25)
        self.assertEqual(bought["endowment_premium"], True)
        sold = engine.player_sells("chen", "铁剑")
        self.assertTrue(sold["ok"])
        self.assertEqual(sold["price"], 10)


# ============================================================================ #
# 10. 三通道玩家可感知：身份标签行 + 垫话醉变体 + 快脑酒嗝
# ============================================================================ #

class TestPerceptibleChannels(unittest.TestCase):
    """烂醉可感知出口：自动管道 prompt + 垫话 + 快脑（决策层零改动）。"""

    def test_drunk_identity_line_in_system_prompt(self):
        """①烂醉后慢脑对话 system prompt 身份标签行自动含"烂醉如泥"
        （TRANSIENT 桶经 visible_tags() 渲染序自动消费——"断指的铁匠
        今天烂醉如泥"，决策层零改动零新注入机制）。"""
        llm, engine = _fresh_engine()
        _give_bowls(engine, "chen", _needed("chen"))
        result = engine.player_says(SLOW_ASK, "chen")
        self.assertEqual(result["brain"], "slow")
        system_prompt = llm.captured[-1][0]["content"]
        self.assertIn("身份标签：", system_prompt)
        self.assertIn(DRUNK_LABEL, system_prompt)

    def test_drunk_filler_variant_rotation(self):
        """②垫话醉变体：烂醉 chen 对话的 filler 按 DRUNK_FILLERS 池
        轮转（醉态覆盖脾气常态——烂醉的 NPC 不再分暴躁/默认带）。"""
        _llm, engine = _fresh_engine()
        _give_bowls(engine, "chen", _needed("chen"))
        first = engine.player_says(SLOW_ASK, "chen")
        second = engine.player_says(SLOW_ASK, "chen")
        self.assertEqual(first["filler"], DRUNK_FILLERS[0])
        self.assertEqual(second["filler"], DRUNK_FILLERS[1])

    def test_drunk_fast_brain_hiccup_prefix(self):
        """③快脑醉前缀：烂醉 chen 的快脑秒回（presence"在吗"）带
        "（打着酒嗝）"——日常句快脑照常秒回（醉汉也能答"在，何事？"），
        醉汉答报时只是带酒嗝；未醉对照不带前缀；全程零 LLM。"""
        _llm, engine = _fresh_engine()
        calls_before = engine.llm.call_count
        sober = engine.player_says("在吗", "chen")
        self.assertEqual(sober["brain"], "fast")
        self.assertIn("在，何事？", sober["reply"])
        self.assertFalse(sober["reply"].startswith(FAST_DRUNK_PREFIX))
        _give_bowls(engine, "chen", _needed("chen"))
        drunk = engine.player_says("在吗", "chen")
        self.assertEqual(drunk["brain"], "fast",
                         msg="醉汉日常句仍走快脑秒回")
        self.assertTrue(drunk["reply"].startswith(FAST_DRUNK_PREFIX),
                        msg=f"醉汉快脑回复应带酒嗝前缀，实际: {drunk['reply']!r}")
        self.assertIn("在，何事？", drunk["reply"])
        self.assertEqual(engine.llm.call_count, calls_before,
                         msg="快脑路径零 LLM（醉与非醉一致）")


# ============================================================================ #
# 11. 架构边界：ast 断言 + 无账本兼容
# ============================================================================ #

class TestArchitectureBoundary(unittest.TestCase):
    """intoxication 源码零 inner_state 引用；is_dead_drunk 无账本兼容。"""

    def test_source_no_inner_state_reference(self):
        """①六情边界结构断言：intoxication.py 代码不出现 "inner_state"
        引用（读源文件文本，剔除模块 docstring 后断言——沿
        tests/test_economics.py 的先例写法）。"""
        import engine.intoxication as intox_mod
        with open(intox_mod.__file__, encoding="utf-8") as fh:
            source = fh.read()
        module_doc = ast.get_docstring(ast.parse(source), clean=False) or ""
        code_text = source.replace(module_doc, "", 1)
        self.assertNotIn("inner_state", code_text,
                         msg="intoxication.py 代码层不得引用 inner_state"
                             "（同一事件双份情绪增量=两套情绪系统漂移）")

    def test_is_dead_drunk_ledgerless_npc(self):
        """②is_dead_drunk 无账本 NPC（直接构造、无 genesis 账本）返回
        False——向后兼容（getattr 缺省路径，自定义测试人格零行为）；
        引擎内核心 NPC 挂账本但未饮亦不醉。"""
        persona = Persona(id="wanderer", name="流浪客", role="villager",
                          location_id="plaza", greeting_bank=["……"],
                          fallback_bank=["……"])
        npc = NPC(persona, MockLLMProvider(), World())
        self.assertIsNone(npc.tag_ledger)
        self.assertFalse(is_dead_drunk(npc))
        _llm, engine = _fresh_engine()
        self.assertFalse(is_dead_drunk(engine.npcs["chen"]),
                         msg="挂账本但未饮的核心 NPC 不得判醉")


# ============================================================================ #
# 12. 默认场景惰性（机制默认零行为——token 基线 4519 不动的根）
# ============================================================================ #

class TestDefaultSceneLazy(unittest.TestCase):
    """默认场景：无赠饮 → 酒账恒零 → 瞬态桶恒空 → prompt 逐字节不变。"""

    def test_default_scene_no_drunk_anywhere(self):
        """①默认引擎构造 + 全天 24 tick + 核心对话：全部 prompt 与
        结果不含"烂醉如泥"，酒账恒零，零 npc_drunk 事件。"""
        llm, engine = _fresh_engine()
        for _ in range(24):
            engine.tick(60)
        engine.player_says(SLOW_ASK, "chen")
        engine.player_says(TAVERN_ASK, "tavern_sun")
        engine.player_says(SLOW_ASK, "lily")
        self.assertEqual(engine.intoxication_ledger._bowls, {},
                         msg="默认村庄无赠饮：酒账恒零")
        self.assertEqual(_drunk_events(engine), [])
        for messages in llm.captured:
            for msg in messages:
                self.assertNotIn(DRUNK_LABEL, msg["content"],
                                 msg="默认场景 prompt 不得出现烂醉如泥")


# ============================================================================ #
# 13. 契约常量锁 + filler 醉态覆盖单元级
# ============================================================================ #

class TestConstantsLock(unittest.TestCase):
    """契约常量一字不差（与并行子代理 B 共享同一契约字符串）。"""

    def test_contract_constants_locked(self):
        """①常量锁：标签 id/label、TTL 4 小时、换算分子分母（经锚点
        5.0→4 碗锁定）、拒绝句、失态池、垫话池、快脑前缀；垫话池每条
        ≤15 字符（垫话纪律）。"""
        self.assertEqual(DRUNK_TAG_ID, "state_drunk")
        self.assertEqual(DRUNK_LABEL, "烂醉如泥")
        self.assertEqual(DRUNK_TTL_HOURS, 4)
        self.assertEqual(bowls_needed(5.0), 4)  # 4×5/5 锚点锁定换算常数
        self.assertEqual(DRUNK_REFUSAL,
                         "（趴在桌上打鼾）……嗝……买卖……明儿再说……")
        self.assertEqual(DRUNK_FAILURES,
                         ("{name}喝得烂醉如泥，趴在桌上不省人事",
                          "{name}醉得东倒西歪，扶着桌子直打晃"))
        self.assertEqual(DRUNK_FILLERS,
                         ("（舌头打结）嗝……说、说啥？",
                          "（趴在桌上）嗝……有、有事？"))
        self.assertEqual(FAST_DRUNK_PREFIX, "（打着酒嗝）")
        for text in DRUNK_FILLERS:
            self.assertLessEqual(len(text), 15,
                                 msg=f"垫话醉变体「{text}」超 15 字符")

    def test_filler_drunk_overrides_temperament(self):
        """②filler.generate(drunk=True) 忽略脾气掩码（PM 裁定：醉态
        覆盖脾气常态）：高压暴躁态也回醉话池、计数取模轮转回卷；
        drunk=False 路径逐字节不变（默认参数向后兼容）；空掩码 +
        醉态仍回醉话（"无脾气"场景同被覆盖）。"""
        filler = FillerEngine("irritable")
        high_stress = InnerState(S_stress=0.7)
        # 未醉对照：高压暴躁垫话（既有行为零回归）
        self.assertEqual(filler.generate(high_stress), "（皱眉）找老夫何事？")
        # 醉态覆盖：同高压状态 drunk=True → 醉话池轮转
        self.assertEqual(filler.generate(high_stress, drunk=True),
                         DRUNK_FILLERS[0])
        self.assertEqual(filler.generate(high_stress, drunk=True),
                         DRUNK_FILLERS[1])
        self.assertEqual(filler.generate(high_stress, drunk=True),
                         DRUNK_FILLERS[0])  # 取模回卷
        # 未醉路径恢复暴躁常态（向后兼容）
        self.assertEqual(filler.generate(high_stress), "（皱眉）找老夫何事？")
        # 空掩码 + 醉态：仍回醉话
        self.assertEqual(FillerEngine("").generate(InnerState(), drunk=True),
                         DRUNK_FILLERS[0])


if __name__ == "__main__":
    unittest.main()

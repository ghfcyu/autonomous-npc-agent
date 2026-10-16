"""T6 第一小步 · 醉酒结算最小闭环验收测试（玩家可感知口径）。

被测契约（PM 定死，engine/intoxication.py 由并行子代理 A 按同一契约
实现；本文件只测不改，不写任何 engine 代码）：

- 饮酒链路：player_gives(npc, 含"酒"物品) → item_given 事件 →
  IntoxicationLedger（NPCEngine.intoxication_ledger 属性）累计碗数 →
  达该 NPC 酒量阈值（bowls_needed(alcohol_tol) = ceil(4×tol/5)）→
  结算挂 TRANSIENT 瞬态标签（DRUNK_TAG_ID/DRUNK_LABEL，TTL=
  DRUNK_TTL_HOURS 世界小时）并发 npc_drunk 失态事件（summary 为
  DRUNK_FAILURES 轮转、{name}=NPC 名）；
- 三通道玩家可感知：①交易拒绝（player_buys/player_sells 返回
  reason="drunk" + reply 逐字等于 DRUNK_REFUSAL）②垫话醉变体
  （DRUNK_FILLERS 覆盖脾气常态）+ 快脑醉前缀（FAST_DRUNK_PREFIX）；
- 失态记忆：victim 长期直写（importance 0.75）+ 同地目击者（如
  tavern_sun 目击 chen 烂醉）均记住，次晨跨对话【相关长期记忆】引用；
- 泄底防护：任何 prompt/文案不得含 SECRET_POOL 把柄内容；
- 边界纪律：醉酒链路零 LLM；npc_drunk 不改任何 8D（饮酒情绪已由
  赠予六情"好"矢量独占）；拒绝路径零事件零增量。

断言口径（本文件全部用例遵守）：
1. **只断言玩家可感知行为**——prompt 内容级（RecordingLLM 捕获慢脑
   messages + extract_block 取块 + 身份标签行）、返回结构（ok/reason/
   reply/filler/brain 键）、文案逐字（一律 import 契约常量比对，
   不复制字符串字面量）、账面与记忆数据（visible_tags/长期记忆条目，
   均为玩家可复核出口），不锁内部实现细节；
2. **真实数据驱动**——碗数阈值一律经 generate_village() 取真实档案
   alcohol_tol 再用 bowls_needed() 计算，严禁手写 tol/阈值数字；
   TTL 边界一律用 DRUNK_TTL_HOURS 常量，不手写 4；
3. **对照组唯一差异法**——每条主链路配对照组（唯一差异=有无饮酒/
   醉否），防词面巧合（探询句本身含"烂醉"，故对照断言锁定失态模板
   整句产物而非裸字样，沿 test_tavern_memory.py 对照纪律）；
4. **时间推进口径**——engine.tick(minutes=...) 与
   tests/test_habit.py/test_tavern_memory.py 一致；_fresh_engine 内
   random.seed 固定天气随机（world.py 天气走全局 random），时间推进
   类用例对天气噪声完全确定；
5. **零重复锁**——<35token 红线与 PINNED 4519 已有测试锁定，本文件
   不重写；默认场景惰性断言在 test_token_baseline_deterministic.py
   新增方法中（本文件不重复跑基线场景）。

并行时序说明：子代理 A 的 engine/intoxication.py 未就位时，本文件
import 即红（ModuleNotFoundError）——预期内的并行时序，不为迁就
现状改契约语义。
"""

import random
import re
import unittest

from engine.economics import endowment_price, estimate_value
from engine.engine import NPCEngine
from engine.intoxication import (DRUNK_FAILURES, DRUNK_FILLERS, DRUNK_LABEL,
                                 DRUNK_REFUSAL, DRUNK_TAG_ID, DRUNK_TTL_HOURS,
                                 FAST_DRUNK_PREFIX, IntoxicationLedger,
                                 bowls_needed, is_dead_drunk)
from engine.llm.mock import MockLLMProvider
from engine.tag_mount import SECRET_POOL
from engine.village_genesis import generate_village


class RecordingLLM(MockLLMProvider):
    """记录完整 messages 的 Mock（慢脑 prompt 内容级断言用，沿
    test_tavern_memory.py 同款装配模式，本文件自持一份避免跨测试耦合）。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.captured = []

    def chat(self, messages, temperature=0.7):
        self.captured.append([dict(m) for m in messages])
        return super().chat(messages, temperature=temperature)


def extract_block(prompt: str, marker: str) -> str:
    """提取 prompt 中 marker 标记块内容（marker 后到首个空行）。

    逻辑与 scripts/dialogue_quality.py / tests/test_tavern_memory.py
    extract_block 同口径（块级精确断言的既有模式），本文件自持一份。
    """
    if not prompt or marker not in prompt:
        return ""
    tail = prompt.split(marker, 1)[1].lstrip("\n")
    end = tail.find("\n\n")
    return tail[:end] if end != -1 else tail


# ---- 场景常量（确定性：固定文本 + 固定推进节奏；均超快脑句长闸门） ----
# 赠饮物品：含"酒"字符（酒账判定口径），且为 tavern_sun 经营范围与
# 默认库存品（对照成交链用同一物品，唯一差异法）。
WINE_ITEM = "米酒"
# 慢脑寒暄句（>12 字稳定走慢脑产生 prompt；不含快脑问候/问价/问路模式）
SLOW_ASK_TAVERN = "孙掌柜，你这儿最近生意如何，客人多不多呀？"
SLOW_ASK_CHEN = "陈师傅，跟您打听个事儿，您最近可有什么新活计？"
SLOW_ASK_LILY = "莉莉老板，你这儿最近都进些什么稀罕货色呀？"
# 快脑报时句（ask_time 意图整句全匹配，稳定命中 0 LLM 秒回）
ASK_TIME = "现在几点"
# 次晨跨对话探询句：含"烂醉"字样，并以标点隔出与失态模板一致的
# 连续片段"趴在桌上不省人事"以命中检索（memory._TOKEN_RE 按连续
# 中文串分词——裸"烂醉"落在不同连续串时 token 重合为 0，须引用
# 失态场景片段才能命中 top_k=3 检索）。>12 字稳定走慢脑。
# 注意：探询句不得完整复述失态模板（对照组断言锁定 DRUNK_FAILURES
# 格式化整句产物而非裸字样——探询句缺"{name}喝得烂醉如泥"前缀，
# 对照 assertNotIn 仍成立，防词面巧合纪律不变）。
MORNING_PROBE = "陈师傅，听说你昨儿喝得烂醉如泥，趴在桌上不省人事，是真的吗？"
# 同小时判重压力口径：5 次 tick(10) 全程同一小时（8:00→8:50）；
# 若误按每次 tick 递减，5 步足以耗尽 TTL=4 提前醒酒，判据可区分。
SAME_HOUR_TICKS = 5

# random seed：tick 推进时天气变化走全局 random.random()（world.py
# WEATHER_CHANGE_PROB），不 seed 则组合运行时随机红（沿
# test_tavern_memory.py _fresh_engine 既有纪律）。
_SCENARIO_SEED = 20261017


def _fresh_engine(seed: int = _SCENARIO_SEED):
    """fresh 引擎 + 录制 LLM：独立世界与醉账，用例间零污染。"""
    random.seed(seed)
    llm = RecordingLLM()
    engine = NPCEngine(llm=llm)
    return llm, engine


def _alcohol_tol(npc_id: str) -> float:
    """经 generate_village() 取真实档案酒量（单一来源，严禁手写数字）。"""
    return generate_village()[npc_id].innate.alcohol_tol


def _bowls(npc_id: str) -> int:
    """该 NPC 烂醉所需碗数（bowls_needed API 计算，真实数据驱动）。"""
    return bowls_needed(_alcohol_tol(npc_id))


def _give_bowls(engine: NPCEngine, npc_id: str, count: int) -> None:
    """赠饮 count 碗（宽松模式：玩家无库存也计数，契约口径）。"""
    for _ in range(count):
        result = engine.player_gives(npc_id, WINE_ITEM)
        assert result["ok"], f"player_gives 应成功（宽松模式）: {result}"


def _drunk_visible(engine: NPCEngine, npc_id: str) -> bool:
    """瞬态醉酒标签是否在玩家可见标签集（tag_id 与 label 双契约常量比对）。"""
    tags = engine.npcs[npc_id].tag_ledger.visible_tags()
    return any(t.tag_id == DRUNK_TAG_ID and t.label == DRUNK_LABEL
               for t in tags)


def _identity_line(system_prompt: str):
    """提取 system prompt 身份标签行内容（无该行返回 None）。

    TRANSIENT 桶自动进 visible_tags() 渲染序（INNATE→ACQUIRED→
    TRANSIENT→RELATIONAL），decision.py 零改动自动管道——醉酒标签
    的唯一 prompt 出口即此行，行级断言防"标签挂了但没进决策上下文"。
    """
    match = re.search(r"身份标签：(.+)", system_prompt)
    return match.group(1).strip() if match else None


def _failure_variants(npc_name: str) -> list:
    """失态模板产物的全部轮转变体（{name}=NPC 名，import 契约常量格式化）。"""
    return [template.format(name=npc_name) for template in DRUNK_FAILURES]


def _long_contents(engine: NPCEngine, npc_id: str) -> list:
    """读 NPC 长期记忆全部文本（失态记忆直写断言口）。"""
    return [r.content for r in engine.npcs[npc_id].memory.long.records]


def _assert_no_secret_leak(testcase: unittest.TestCase, texts) -> None:
    """泄底防护共用断言：texts 中任何一段都不得含 SECRET_POOL 任何
    一条把柄的 label 与 description（遍历 import 的池常量，不手写清单）。"""
    for tag_id, label, description in SECRET_POOL:
        for text in texts:
            testcase.assertNotIn(
                label, text,
                msg=f"泄底：prompt/文案含把柄 label「{label}」，"
                    f"文本片段: {text[:120]!r}")
            testcase.assertNotIn(
                description, text,
                msg=f"泄底：prompt/文案含把柄 description「{description}」，"
                    f"文本片段: {text[:120]!r}")


# ========================================================================== #
# 0. TestContractWiring —— 契约接线（引擎属性暴露）
# ========================================================================== #
class TestContractWiring(unittest.TestCase):
    """契约接线：NPCEngine.intoxication_ledger 属性存在且为契约类实例。"""

    def test_engine_exposes_intoxication_ledger(self):
        """①引擎暴露醉账属性（契约点名 engine.intoxication_ledger，
        IntoxicationLedger 实例）——属性名接线错误当场暴露。"""
        _llm, engine = _fresh_engine()
        self.assertIsInstance(engine.intoxication_ledger,
                              IntoxicationLedger,
                              msg="NPCEngine 应暴露 intoxication_ledger "
                                  "属性（IntoxicationLedger 实例）")


# ========================================================================== #
# 1. TestDrunkForkByTolerance —— 醉酒分叉（真实数据驱动）
# ========================================================================== #
class TestDrunkForkByTolerance(unittest.TestCase):
    """同量赠饮，酒量差的先倒、酒量好的不倒——先天 6D alcohol_tol 驱动的
    玩家可感知分叉（验收标准 1：无任何手写名单/数字）。"""

    def test_threshold_boundary_sober_then_drunk(self):
        """①阈值边界：tavern_sun 赠 (阈值-1) 碗不醉，第阈值碗烂醉
        （碗数经 generate_village()+bowls_needed() 计算）。"""
        _llm, engine = _fresh_engine()
        threshold = _bowls("tavern_sun")
        self.assertGreaterEqual(threshold, 1, "阈值应为 ≥1 的正整数")
        _give_bowls(engine, "tavern_sun", threshold - 1)
        self.assertFalse(_drunk_visible(engine, "tavern_sun"),
                         msg=f"{threshold - 1} 碗（阈值-1）不应烂醉")
        self.assertFalse(is_dead_drunk(engine.npcs["tavern_sun"]),
                         msg="is_dead_drunk 应与可见标签口径一致（未醉）")
        _give_bowls(engine, "tavern_sun", 1)
        self.assertTrue(_drunk_visible(engine, "tavern_sun"),
                        msg=f"第 {threshold} 碗达阈值应结算烂醉")
        self.assertTrue(is_dead_drunk(engine.npcs["tavern_sun"]),
                        msg="is_dead_drunk 应与可见标签口径一致（烂醉）")

    def test_same_amount_fork_drunk_vs_sober_prompt_level(self):
        """②同量分叉（灵魂断言）：同量米酒——tavern_sun（阈值=3 碗）
        烂醉 vs chen（阈值=5 碗）不醉；prompt 内容级：烂醉者慢脑
        system prompt 身份标签行含"烂醉如泥"，烂醉前与 chen 均不含。"""
        tavern_bowls = _bowls("tavern_sun")
        chen_bowls = _bowls("chen")
        self.assertGreater(chen_bowls, tavern_bowls,
                           msg="分叉前提：chen 酒量阈值须大于 tavern_sun"
                               "（真实档案驱动，实测 5 > 3）")
        # —— 主链路：tavern_sun —— #
        llm, engine = _fresh_engine()
        # 烂醉前对照：先对话一次，身份标签行不含醉酒标签
        result0 = engine.player_says(SLOW_ASK_TAVERN, "tavern_sun")
        self.assertEqual(result0["brain"], "slow",
                         msg="长句须走慢脑产生 prompt（前置）")
        line0 = _identity_line(llm.captured[-1][0]["content"])
        self.assertIsNotNone(line0, msg="身份标签行应存在（创生账本前置）")
        self.assertNotIn(DRUNK_LABEL, line0,
                         msg=f"烂醉前身份标签行不应含 {DRUNK_LABEL!r}，"
                             f"实际: {line0!r}")
        # 同量赠饮 → 烂醉 → 身份标签行含醉酒标签
        _give_bowls(engine, "tavern_sun", tavern_bowls)
        self.assertTrue(_drunk_visible(engine, "tavern_sun"),
                        msg=f"tavern_sun 赠 {tavern_bowls} 碗应烂醉")
        result1 = engine.player_says(SLOW_ASK_TAVERN, "tavern_sun")
        self.assertEqual(result1["brain"], "slow")
        line1 = _identity_line(llm.captured[-1][0]["content"])
        self.assertIn(DRUNK_LABEL, line1,
                      msg=f"烂醉后身份标签行应含 {DRUNK_LABEL!r}，"
                          f"实际: {line1!r}")
        # —— 对照引擎：chen，唯一差异 = 换了酒量好的 NPC —— #
        llm2, engine2 = _fresh_engine()
        _give_bowls(engine2, "chen", tavern_bowls)
        self.assertFalse(_drunk_visible(engine2, "chen"),
                         msg=f"chen 赠同样 {tavern_bowls} 碗不应烂醉"
                             f"（其阈值 {chen_bowls} 碗）")
        result2 = engine2.player_says(SLOW_ASK_CHEN, "chen")
        self.assertEqual(result2["brain"], "slow")
        line2 = _identity_line(llm2.captured[-1][0]["content"])
        self.assertIsNotNone(line2, msg="对照 chen 身份标签行应存在")
        self.assertNotIn(DRUNK_LABEL, line2,
                         msg=f"未醉 chen 身份标签行不应含 {DRUNK_LABEL!r}，"
                             f"实际: {line2!r}")

    def test_chen_crosses_own_threshold_incrementally(self):
        """③chen 续赠到自身阈值：3 碗（tavern_sun 阈值量）不醉 →
        再赠 (chen 阈值 - tavern_sun 阈值) 碗（累计达自身阈值）烂醉
        （增量碗数经 API 差值计算，不手写）。"""
        tavern_bowls = _bowls("tavern_sun")
        chen_bowls = _bowls("chen")
        extra = chen_bowls - tavern_bowls
        self.assertGreaterEqual(extra, 1,
                                msg="分叉前提：两阈值应有正差值")
        llm, engine = _fresh_engine()
        _give_bowls(engine, "chen", tavern_bowls)
        self.assertFalse(_drunk_visible(engine, "chen"),
                         msg=f"chen {tavern_bowls} 碗不醉（前置）")
        _give_bowls(engine, "chen", extra)
        self.assertTrue(_drunk_visible(engine, "chen"),
                        msg=f"累计 {chen_bowls} 碗达 chen 自身阈值应烂醉")
        result = engine.player_says(SLOW_ASK_CHEN, "chen")
        self.assertEqual(result["brain"], "slow")
        line = _identity_line(llm.captured[-1][0]["content"])
        self.assertIn(DRUNK_LABEL, line,
                      msg=f"chen 烂醉后身份标签行应含 {DRUNK_LABEL!r}，"
                          f"实际: {line!r}")


# ========================================================================== #
# 2. TestDrunkTTLLifecycle —— 瞬态标签生命周期（验收标准 2）
# ========================================================================== #
class TestDrunkTTLLifecycle(unittest.TestCase):
    """烂醉 4 小时到期熄灭；同 hour 判重；烂醉期再赠酒 TTL 覆盖重置。

    时间口径：世界默认 08:00 起步，结算发生于 8:00（无需先 tick）；
    tick(60) 每次恰跨 1 个小时界；TTL 边界一律用 DRUNK_TTL_HOURS 常量。
    """

    def test_same_hour_multiple_ticks_still_drunk(self):
        """①同 hour 判重：烂醉后同小时 5 次 tick(10)（8:00→8:50）仍醉
        ——一天 144 次 tick 只走 24 步的"有泵"口径；若误按每次 tick
        递减，5 步即耗尽 TTL 提前醒酒，判据可区分。"""
        _llm, engine = _fresh_engine()
        _give_bowls(engine, "tavern_sun", _bowls("tavern_sun"))
        self.assertTrue(_drunk_visible(engine, "tavern_sun"), msg="前置：已烂醉")
        for _ in range(SAME_HOUR_TICKS):
            engine.tick(10)
        self.assertEqual(engine.world.hour, 8,
                         msg="5 次 tick(10) 应仍同一小时（场景前置）")
        self.assertTrue(_drunk_visible(engine, "tavern_sun"),
                        msg="同小时多次 tick 只递减一次，不得提前醒酒")
        self.assertTrue(is_dead_drunk(engine.npcs["tavern_sun"]),
                        msg="烂醉判定应仍成立")

    def test_expires_after_ttl_hours_and_prompt_restored(self):
        """②到期熄灭：跨 (TTL-1) 个小时界仍醉，跨满 TTL 个小时界
        visible_tags 不含；随后对话 prompt 身份标签行恢复不含醉酒标签
        （TRANSIENT 出桶后 prompt 恢复原样的内容级旁证）。"""
        llm, engine = _fresh_engine()
        _give_bowls(engine, "tavern_sun", _bowls("tavern_sun"))
        self.assertTrue(_drunk_visible(engine, "tavern_sun"), msg="前置：已烂醉")
        for _ in range(DRUNK_TTL_HOURS - 1):
            engine.tick(60)
        self.assertTrue(_drunk_visible(engine, "tavern_sun"),
                        msg=f"跨 {DRUNK_TTL_HOURS - 1} 个小时界应仍烂醉")
        engine.tick(60)  # 第 TTL 个小时界
        self.assertFalse(_drunk_visible(engine, "tavern_sun"),
                         msg=f"跨满 {DRUNK_TTL_HOURS} 个小时界应自动熄灭")
        # 12:00 仍营业时段（tavern_sun 14 点才转 IDLE），慢脑对话可达
        result = engine.player_says(SLOW_ASK_TAVERN, "tavern_sun")
        self.assertEqual(result["brain"], "slow",
                         msg="营业时段长句应走慢脑（前置）")
        line = _identity_line(llm.captured[-1][0]["content"])
        self.assertIsNotNone(line, msg="身份标签行应仍存在（创生标签不灭）")
        self.assertNotIn(DRUNK_LABEL, line,
                         msg=f"熄灭后身份标签行应恢复不含 {DRUNK_LABEL!r}，"
                             f"实际: {line!r}")

    def test_re_gift_resets_ttl(self):
        """③TTL 覆盖重置：烂醉期再赠 1 碗 → 重置后再跨 (TTL-1) 个
        小时界仍醉、第 TTL 个小时界才熄灭（又一轮买醉）。"""
        _llm, engine = _fresh_engine()
        _give_bowls(engine, "tavern_sun", _bowls("tavern_sun"))
        self.assertTrue(_drunk_visible(engine, "tavern_sun"), msg="前置：已烂醉")
        engine.tick(60)  # 9:00，TTL 已走 1 步
        _give_bowls(engine, "tavern_sun", 1)  # 烂醉期再赠 → TTL 覆盖重置
        for _ in range(DRUNK_TTL_HOURS - 1):
            engine.tick(60)  # 9:00 → 12:00
        self.assertTrue(_drunk_visible(engine, "tavern_sun"),
                        msg=f"重置后再跨 {DRUNK_TTL_HOURS - 1} 个小时界"
                            f"应仍烂醉（未重置则此刻已醒，判据可区分）")
        engine.tick(60)  # 第 TTL 个小时界
        self.assertFalse(_drunk_visible(engine, "tavern_sun"),
                        msg=f"重置后第 {DRUNK_TTL_HOURS} 个小时界才熄灭")


# ========================================================================== #
# 3. TestDrunkTradeChannel —— 三通道之一：交易拒绝（验收标准 3①）
# ========================================================================== #
class TestDrunkTradeChannel(unittest.TestCase):
    """烂醉期买卖一律确定性拒绝（unknown npc 检查后立即拒绝，不查库存
    不成交）；对照组：未醉同句正常成交。"""

    def test_drunk_buys_sells_rejected_with_contract_reply(self):
        """①烂醉 tavern_sun（库存确有米酒）买/卖均被拒：ok=False、
        reason="drunk"、reply 与 DRUNK_REFUSAL 逐字相等（import 契约
        常量比对）；拒绝先于库存检查（有货也拒）；零交易事件零库存转移。"""
        _llm, engine = _fresh_engine()
        _give_bowls(engine, "tavern_sun", _bowls("tavern_sun"))
        self.assertTrue(_drunk_visible(engine, "tavern_sun"), msg="前置：已烂醉")
        self.assertIn(WINE_ITEM,
                      engine.world.entities["tavern_sun"].inventory,
                      msg="前置：烂醉掌柜库存里确有米酒——拒绝必须先于"
                          "库存检查（有货也拒）")
        buy = engine.player_buys("tavern_sun", WINE_ITEM)
        self.assertFalse(buy["ok"], msg="烂醉期买入应被拒")
        self.assertEqual(buy["reason"], "drunk")
        self.assertEqual(buy["reply"], DRUNK_REFUSAL,
                         msg=f"拒绝文案应与契约 DRUNK_REFUSAL 逐字相等，"
                             f"实际: {buy['reply']!r}")
        sell = engine.player_sells("tavern_sun", WINE_ITEM)
        self.assertFalse(sell["ok"], msg="烂醉期卖出应被拒")
        self.assertEqual(sell["reason"], "drunk")
        self.assertEqual(sell["reply"], DRUNK_REFUSAL,
                         msg=f"拒绝文案应与契约 DRUNK_REFUSAL 逐字相等，"
                             f"实际: {sell['reply']!r}")
        # 拒绝零副作用：库存不动、不发任何交易事件
        self.assertIn(WINE_ITEM,
                      engine.world.entities["tavern_sun"].inventory,
                      msg="拒绝不得转移库存")
        self.assertFalse(any(e.kind in ("item_bought", "item_sold")
                             for e in engine.world.bus.history),
                         msg="拒绝不得发布交易事件")

    def test_sober_control_normal_deal(self):
        """②对照组：未醉 tavern_sun 买米酒正常成交（禀赋溢价价），
        回复非拒绝文案——唯一差异=醉否，防拒绝逻辑误伤正常交易。"""
        _llm, engine = _fresh_engine()
        self.assertFalse(_drunk_visible(engine, "tavern_sun"),
                         msg="前置：全新引擎未醉")
        buy = engine.player_buys("tavern_sun", WINE_ITEM)
        self.assertTrue(buy["ok"],
                        msg="未醉对照：买米酒应正常成交")
        self.assertEqual(buy["price"],
                         endowment_price(estimate_value(WINE_ITEM)),
                         msg="成交价应为禀赋溢价价（既有交易契约价）")
        self.assertNotEqual(buy.get("reply", ""), DRUNK_REFUSAL,
                             msg="正常成交回复不得是醉拒文案")
        self.assertNotIn("drunk", buy,
                         msg="正常成交返回不得带 drunk 拒绝键")


# ========================================================================== #
# 4. TestDrunkDialogueChannels —— 三通道之二/三：垫话醉变体 + 快脑醉前缀
# ========================================================================== #
class TestDrunkDialogueChannels(unittest.TestCase):
    """烂醉期对话可感知：垫话被醉变体覆盖（醉态覆盖脾气常态）；快脑
    命中日常句照常秒回、只带酒嗝前缀。均配未醉对照组（唯一差异法）。"""

    def test_drunk_filler_variant_with_sober_control(self):
        """①烂醉 tavern_sun 垫话 ∈ DRUNK_FILLERS（import 契约池比对）；
        对照：未醉同句垫话 ∉ DRUNK_FILLERS。"""
        _llm, engine = _fresh_engine()
        _give_bowls(engine, "tavern_sun", _bowls("tavern_sun"))
        result = engine.player_says(SLOW_ASK_TAVERN, "tavern_sun")
        self.assertEqual(result["brain"], "slow", msg="长句走慢脑（前置）")
        self.assertIn(result["filler"], DRUNK_FILLERS,
                      msg=f"烂醉期垫话应为醉变体，实际: "
                          f"{result['filler']!r}")
        # 对照组：唯一差异 = 未醉
        _llm2, engine2 = _fresh_engine()
        control = engine2.player_says(SLOW_ASK_TAVERN, "tavern_sun")
        self.assertEqual(control["brain"], "slow")
        self.assertNotIn(control["filler"], DRUNK_FILLERS,
                         msg=f"未醉垫话不应是醉变体，实际: "
                             f"{control['filler']!r}")

    def test_fast_brain_drunk_prefix_with_sober_control(self):
        """②烂醉 NPC 问"现在几点"快脑命中（0 LLM），reply 以
        FAST_DRUNK_PREFIX 开头（醉汉也能报时，日常句照常秒回只带
        酒嗝）；对照：未醉同句不带该前缀。"""
        llm, engine = _fresh_engine()
        _give_bowls(engine, "tavern_sun", _bowls("tavern_sun"))
        self.assertTrue(_drunk_visible(engine, "tavern_sun"), msg="前置：已烂醉")
        calls_before = llm.call_count
        result = engine.player_says(ASK_TIME, "tavern_sun")
        self.assertEqual(result["brain"], "fast",
                         msg="报时句应快脑命中（前置）")
        self.assertTrue(result["reply"].startswith(FAST_DRUNK_PREFIX),
                        msg=f"快脑回复应以 {FAST_DRUNK_PREFIX!r} 开头，"
                            f"实际: {result['reply']!r}")
        self.assertEqual(llm.call_count, calls_before,
                        msg="快脑命中路径 0 LLM 调用")
        # 对照组：唯一差异 = 未醉
        _llm2, engine2 = _fresh_engine()
        control = engine2.player_says(ASK_TIME, "tavern_sun")
        self.assertEqual(control["brain"], "fast")
        self.assertFalse(control["reply"].startswith(FAST_DRUNK_PREFIX),
                         msg=f"未醉快脑回复不得带酒嗝前缀，实际: "
                             f"{control['reply']!r}")


# ========================================================================== #
# 5. TestMorningAfterMemory —— 三通道之四：次晨跨对话记忆引用
# ========================================================================== #
class TestMorningAfterMemory(unittest.TestCase):
    """「陈师傅记得自己昨儿烂醉如泥」：失态记忆长期直写，次晨对话
    【相关长期记忆】块引用失态模板产物；对照组无饮酒则无（沿
    test_tavern_memory.py 的 RecordingLLM+extract_block 对照配方）。"""

    def test_next_morning_memory_reference_main_chain(self):
        """①主链路：chen 赠满自身阈值烂醉 → 24× tick(60) 跨 TTL 到
        次晨 8:00 → 探询句（含"烂醉"）→ 慢脑 prompt【相关长期记忆】
        块含失态模板产物（DRUNK_FAILURES 格式化，任一轮转变体）。"""
        llm, engine = _fresh_engine()
        chen_name = engine.npcs["chen"].persona.name
        _give_bowls(engine, "chen", _bowls("chen"))
        self.assertTrue(_drunk_visible(engine, "chen"), msg="前置：已烂醉")
        for _ in range(24):  # 8:00 → 次日 8:00（跨 TTL，次晨 WORKING 时段）
            engine.tick(60)
        result = engine.player_says(MORNING_PROBE, "chen")
        self.assertEqual(result["brain"], "slow",
                         msg="次晨工作时段长句应走慢脑产生 prompt")
        prompt = llm.captured[-1][1]["content"]
        block = extract_block(prompt, "【相关长期记忆】")
        variants = _failure_variants(chen_name)
        self.assertTrue(any(variant in block for variant in variants),
                        msg=f"次晨对话【相关长期记忆】块应含失态模板产物"
                            f"（期望含 {variants!r} 之一），实际块内容: "
                            f"{block!r}")

    def test_control_chain_without_drinking_no_failure_text(self):
        """②对照组：同链路唯一差异 = 无饮酒（零赠饮零事件）→ 次晨
        prompt 不含任何失态模板产物——证明主链路命中来自失态记忆而非
        探询句词面巧合（探询句本身含"烂醉如泥"字样）。"""
        llm, engine = _fresh_engine()
        chen_name = engine.npcs["chen"].persona.name
        for _ in range(24):
            engine.tick(60)
        result = engine.player_says(MORNING_PROBE, "chen")
        self.assertEqual(result["brain"], "slow",
                         msg="对照组同样走慢脑，保证 prompt 可比")
        prompt = llm.captured[-1][1]["content"]
        for variant in _failure_variants(chen_name):
            self.assertNotIn(variant, prompt,
                             msg="对照组（无饮酒）prompt 不应出现失态"
                                 "模板产物")
        # 排除假阴性：对照组记忆与慢脑链路真实在工作（说话条目进短期块）
        recent = extract_block(prompt, "【最近的经历】")
        self.assertIn("玩家说：", recent,
                      msg=f"对照组【最近的经历】块应有说话短期记忆"
                          f"（链路活跃性前置），实际: {recent!r}")
        self.assertEqual(len(engine.npcs["chen"].memory.long.records), 0,
                         msg="对照组无饮酒无赠饮，长期记忆应为空")


# ========================================================================== #
# 6. TestWitnessPerception —— 同地目击者感知
# ========================================================================== #
class TestWitnessPerception(unittest.TestCase):
    """「掌柜的什么都看在眼里」：同地目击者记住他人失态，异地者不感知
    （环境事件只感知同地点发生的——npc.py 既有闸门）。"""

    def test_tavern_sun_witnesses_chen_failure(self):
        """①把 chen 挪到酒馆 → 赠满烂醉 → 同地的 tavern_sun 长期记忆
        含 chen 失态记录；victim 自己也记住（点名长期直写）；异地
        对照：lily（集市）零感知。"""
        _llm, engine = _fresh_engine()
        chen_name = engine.npcs["chen"].persona.name
        engine.world.move_entity("chen", "tavern")
        self.assertEqual(engine.npcs["chen"].entity.location_id, "tavern",
                         msg="前置：chen 已挪到酒馆")
        self.assertEqual(engine.npcs["tavern_sun"].entity.location_id, "tavern",
                         msg="前置：tavern_sun 本就在酒馆（同地目击）")
        _give_bowls(engine, "chen", _bowls("chen"))
        self.assertTrue(_drunk_visible(engine, "chen"), msg="前置：chen 已烂醉")
        variants = _failure_variants(chen_name)
        # 同地目击者：掌柜的长期记忆含失态记录
        tavern_contents = _long_contents(engine, "tavern_sun")
        self.assertTrue(any(variant in content
                            for variant in variants
                            for content in tavern_contents),
                        msg=f"同地目击者 tavern_sun 应记住 chen 失态"
                            f"（期望含 {variants!r} 之一），实际长期记忆: "
                            f"{tavern_contents!r}")
        # victim 自己：点名感知长期直写
        chen_contents = _long_contents(engine, "chen")
        self.assertTrue(any(variant in content
                            for variant in variants
                            for content in chen_contents),
                        msg=f"victim chen 自己应记住失态（长期直写），"
                            f"实际长期记忆: {chen_contents!r}")
        # 异地对照：lily（market）不感知（唯一差异 = 不同地）
        lily_contents = _long_contents(engine, "lily")
        self.assertEqual(lily_contents, [],
                         msg=f"异地 lily 不应感知酒馆失态事件，"
                             f"实际长期记忆: {lily_contents!r}")


# ========================================================================== #
# 7. TestSecrecyGuard —— 泄底防护（对抗预告条款，验收标准 5）
# ========================================================================== #
class TestSecrecyGuard(unittest.TestCase):
    """无勒索史的核心 NPC 日常对话 prompt 零把柄内容：默认场景与烂醉
    场景的全部 prompt、拒绝文案、失态文案均不得含 SECRET_POOL 任何
    一条的 label 与 description（遍历 import 池常量，不手写清单）。"""

    def test_default_scene_core_dialogues_no_secret_leak(self):
        """①默认场景：chen/lily/tavern_sun 三核心各一次慢脑对话，捕获
        的全部 prompt（system+user）不含任何把柄内容；前置断言三人
        账本各挂 ≥1 把柄（防泄底断言空转）。"""
        llm, engine = _fresh_engine()
        dialogues = (("chen", SLOW_ASK_CHEN),
                     ("lily", SLOW_ASK_LILY),
                     ("tavern_sun", SLOW_ASK_TAVERN))
        for npc_id, ask in dialogues:
            self.assertGreaterEqual(
                len(engine.npcs[npc_id].tag_ledger.secrets()), 1,
                msg=f"前置：{npc_id} 创生账本应挂 ≥1 绝密把柄"
                    f"（泄底防护断言非空转）")
            result = engine.player_says(ask, npc_id)
            self.assertEqual(result["brain"], "slow",
                             msg=f"{npc_id} 长句应走慢脑（前置）")
        self.assertGreaterEqual(len(llm.captured), 3,
                                msg="三次慢脑对话应捕获 ≥3 组 messages")
        all_prompts = [message["content"] for pair in llm.captured
                       for message in pair]
        _assert_no_secret_leak(self, all_prompts)

    def test_drunk_scene_prompts_and_texts_no_secret_leak(self):
        """②烂醉场景：醉者慢脑 prompt（身份标签行带"烂醉如泥"）、
        拒绝文案 DRUNK_REFUSAL、失态文案（DRUNK_FAILURES 全变体）、
        垫话醉变体——全部不得含任何把柄内容。"""
        llm, engine = _fresh_engine()
        _give_bowls(engine, "tavern_sun", _bowls("tavern_sun"))
        result = engine.player_says(SLOW_ASK_TAVERN, "tavern_sun")
        self.assertEqual(result["brain"], "slow", msg="长句走慢脑（前置）")
        system_line = _identity_line(llm.captured[-1][0]["content"])
        self.assertIn(DRUNK_LABEL, system_line,
                      msg="前置：烂醉场景身份标签行确带醉酒标签"
                          "（泄底断言在醉态注入下检查）")
        tavern_name = engine.npcs["tavern_sun"].persona.name
        texts = ([DRUNK_REFUSAL] + _failure_variants(tavern_name)
                 + [result["filler"]]
                 + [message["content"] for pair in llm.captured
                    for message in pair])
        _assert_no_secret_leak(self, texts)


# ========================================================================== #
# 8. TestDrunkZeroLLM —— 醉酒链路零 LLM（验收标准 7 之醉酒部分）
# ========================================================================== #
class TestDrunkZeroLLM(unittest.TestCase):
    """醉酒链路（赠饮结算 + 交易拒绝 + 快脑醉句）全程零 LLM 调用零
    token——Tier 0 纯规则封印（对齐 HabitLoop/WealthLedger 纪律）。"""

    def test_full_drunk_chain_zero_llm(self):
        """①赠饮至结算 → 买被拒 → 卖被拒 → 快脑醉句秒回：全程
        call_count 与 total_tokens_used 均为 0（各步前置断言真实发生，
        非空转）。"""
        llm, engine = _fresh_engine()
        self.assertEqual(llm.call_count, 0, msg="前置：全新引擎零调用")
        # 1) 赠饮结算
        _give_bowls(engine, "tavern_sun", _bowls("tavern_sun"))
        self.assertTrue(_drunk_visible(engine, "tavern_sun"),
                        msg="前置：烂醉结算真实发生")
        self.assertEqual(llm.call_count, 0,
                         msg="赠饮结算应零 LLM 调用")
        # 2) 交易拒绝（双向）
        self.assertEqual(engine.player_buys("tavern_sun", WINE_ITEM)["reason"],
                         "drunk", msg="前置：买入拒绝真实发生")
        self.assertEqual(engine.player_sells("tavern_sun", WINE_ITEM)["reason"],
                         "drunk", msg="前置：卖出拒绝真实发生")
        self.assertEqual(llm.call_count, 0,
                         msg="交易拒绝应零 LLM 调用")
        # 3) 快脑醉句
        result = engine.player_says(ASK_TIME, "tavern_sun")
        self.assertEqual(result["brain"], "fast", msg="前置：快脑命中")
        self.assertTrue(result["reply"].startswith(FAST_DRUNK_PREFIX),
                        msg="前置：醉前缀真实生效")
        self.assertEqual(llm.call_count, 0,
                         msg="快脑醉句应零 LLM 调用")
        self.assertEqual(llm.total_tokens_used, 0,
                         msg="醉酒链路全程零 token")


# ========================================================================== #
# 9. TestDrunkInnerStateBoundary —— 六情边界（8D 心智参数守恒）
# ========================================================================== #
class TestDrunkInnerStateBoundary(unittest.TestCase):
    """六情边界（共线性纪律硬边界）：饮酒的情绪语义已由 item_given 的
    荀子六情"好"矢量独占——醉账结算与拒绝路径均不得再改任何 8D
    心智参数（同一事件双份情绪增量 = 两套情绪系统漂移死穴）。"""

    def test_settlement_inner_state_matches_gift_only_expectation(self):
        """①结算零额外 8D：达阈值结算（挂标签+发失态事件）那一碗的
        8D 增量，必须恰等于此前单碗赠饮的增量——若 npc_drunk 事件
        私写 8D，增量必然超出，当场暴露。"""
        _llm, engine = _fresh_engine()
        npc = engine.npcs["tavern_sun"]
        threshold = _bowls("tavern_sun")
        self.assertGreaterEqual(threshold, 2, msg="场景前置：阈值 ≥2")
        initial = dict(npc.inner_state.to_dict())
        _give_bowls(engine, "tavern_sun", 1)
        after_one = dict(npc.inner_state.to_dict())
        _give_bowls(engine, "tavern_sun", threshold - 2)  # 累计阈值-1，未结算
        after_last_sober = dict(npc.inner_state.to_dict())
        self.assertFalse(_drunk_visible(engine, "tavern_sun"),
                         msg=f"前置：累计 {threshold - 1} 碗未达阈值不醉")
        _give_bowls(engine, "tavern_sun", 1)  # 第阈值碗：结算（挂标签+发事件）
        after_settled = dict(npc.inner_state.to_dict())
        self.assertTrue(_drunk_visible(engine, "tavern_sun"),
                        msg="前置：结算真实发生")
        for key, value in initial.items():
            single_gift_delta = after_one[key] - value
            expected = after_last_sober[key] + single_gift_delta
            self.assertAlmostEqual(after_settled[key], expected, places=4,
                                   msg=f"结算碗的 8D 增量应恰等于单碗赠饮"
                                       f"增量（{key}: 期望 {expected}，"
                                       f"实际 {after_settled[key]}——"
                                       f"npc_drunk 不得私写 8D）")

    def test_rejection_path_inner_state_unchanged(self):
        """②拒绝路径 8D 不变：烂醉 NPC 交易被拒前后 inner_state.
        to_dict() 全等（拒绝零事件零增量）；总线零新事件。"""
        _llm, engine = _fresh_engine()
        _give_bowls(engine, "tavern_sun", _bowls("tavern_sun"))
        npc = engine.npcs["tavern_sun"]
        before = dict(npc.inner_state.to_dict())
        history_before = len(engine.world.bus.history)
        buy = engine.player_buys("tavern_sun", WINE_ITEM)
        sell = engine.player_sells("tavern_sun", WINE_ITEM)
        self.assertEqual(buy["reason"], "drunk", msg="前置：买入被拒")
        self.assertEqual(sell["reason"], "drunk", msg="前置：卖出被拒")
        self.assertEqual(npc.inner_state.to_dict(), before,
                         msg="拒绝路径零事件零增量，8D 逐项不变")
        self.assertEqual(len(engine.world.bus.history), history_before,
                         msg="拒绝不得发布任何事件")


if __name__ == "__main__":
    unittest.main()

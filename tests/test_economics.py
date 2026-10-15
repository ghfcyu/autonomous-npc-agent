"""T5 第二小步验收测试：EWMA 财富基准（享乐适应的事件触发离散化）。

被测契约（实现已冻结，engine/economics.py + engine.py 接线 + decision.py
fortune 注入；本文件只测不改）：
1. WealthLedger/WealthAccount：W += value；暖态 R = α·W + (1-α)·R；
   冷启动种子豁免——账本建立后首笔收获只加 W 不动 R（种子事件）；
   不变量 baseline == float(wealth) ⟺ 无收获事件史；
2. 初值单一来源：NPCEngine.__init__ 对 generate_village() 全部 25 人
   ensure_account（键=npc_id，值=profile.innate.wealth 创生档案），
   幂等；订阅 world.bus.subscribe(ledger.on_event, kinds=["item_given"])；
   spawn() 挂 npc.wealth_ledger；
3. decide() 经 getattr(npc,"wealth_ledger",None) 取 fortune_tag：非空时
   【此刻内心】行 tags=[非"心境平稳"标签]+[fortune]（追加行尾），为空时
   与旧格式逐字节全等（基线不变性，token 4519）；
4. 六情边界：财富层不触碰 8D（同一事件的情绪语义由 inner_state 六情
   通道独占），对决策链唯一出口是 fortune_tag 离散中文标签，浮点
   W/R/gain_ratio 严禁进 prompt（<35token 规范）。

用例清单（11 类，第 10 类为 tests/test_inner_state.py TestPromptTokenRedline
的功能变更口径扩展，不在本文件）：
1. TestEWMAFormula——EWMA 契约公式单元级（冷启动/暖态/no-op/事件守卫）
2. TestSingleSource——初值单一来源逐人全等 + ensure_account 幂等
3. TestHedonicAdaptation——灵魂断言：三把铁剑 gain_ratio 严格递减
4. TestReferenceDependence——穷富分叉：同一礼物穷人有感富人无感
5. TestFortuneBand——显著带阈值语义（小礼/无事件/无账/≥ 边界）
6. TestDecisionInjection——端到端玩家可感知口径（prompt 内容级）
7. TestCompoundBoundary——物品词命中口径（复合品/词尾/未知/账本侧锁）
8. TestZeroLLMBoundary——零 LLM + 六情架构边界 + 情绪通道独立运转
9. TestTokenBaselineGuard——token 基线场景 fortune 恒空（全等根因）
11. TestConstantsLock——契约常量锁 + 与 fast_brain 报价口径对齐

编写纪律：零第三方依赖（unittest 标准库）；引擎构造沿
tests/test_tavern_memory.py 的 _fresh_engine 模式（random.seed 双保险）；
核心 NPC id 为 chen/lily/tavern_sun（非文件名）；API 签名
player_says(text, npc_id)（text 在前）、player_gives(npc_id, item)。
"""

import ast
import random
import re
import unittest
from unittest import mock

from engine.economics import (DEFAULT_ITEM_VALUE, EWMA_ALPHA, GAIN_TAG,
                              GAIN_TAG_RATIO, ITEM_VALUES, WealthLedger)
from engine.engine import NPCEngine
from engine.inner_state import XUNZI_EMOTION_VECTORS
from engine.llm import create_provider
from engine.npc import NPC, Persona
from engine.village_genesis import generate_village
from engine.world import World, WorldEvent


# ---- 场景常量（确定性：固定文本保证超快脑全部句长闸门，稳定走慢脑） ----
# 21 字长句：超 greet/presence/farewell(6)/direction(12)/price(8)/time(8)
# 全部句长闸门，且不含赞美/批评关键词（防 8D 标签被六情通道扰动）。
SLOW_ASK = "陈师傅，跟您打听个事儿，您这儿最近可有什么新鲜玩意儿？"
LEDGERLESS_ASK = "你好呀，这位朋友"


def _fresh_engine(seed: int = 20261015) -> NPCEngine:
    """fresh 引擎：独立世界与账本，用例间零污染。

    random.seed 双保险（沿 tests/test_tavern_memory.py 的 _fresh_engine
    模式）：本文件用例不依赖 tick 天气随机，但种子固定保证任何后续
    维护（加 tick 场景）不引入随机红；引擎构造口径按任务书契约
    NPCEngine(npc_configs=None, llm=create_provider("mock"))。
    """
    random.seed(seed)
    return NPCEngine(npc_configs=None, llm=create_provider("mock"))


def _inner_line(prompt: str) -> str:
    """提取 user prompt 中【此刻内心】行内容（到行尾，不含换行）。"""
    match = re.search(r"【此刻内心】(.+)", prompt)
    assert match is not None, "【此刻内心】行缺失"
    return match.group(1)


def _last_user_prompt(spy) -> str:
    """从 mock.patch.object(..., wraps=...) 的 spy 中取最近一次 LLM 调用
    的 user prompt 文本（prompt 内容级断言，非调用计数口径）。"""
    messages = spy.call_args_list[-1].args[0]
    return messages[1]["content"]


# ============================================================================ #
# 1. EWMA 契约公式（单元级）
# ============================================================================ #

class TestEWMAFormula(unittest.TestCase):
    """EWMA 公式单元级：冷启动种子豁免 + 暖态字面契约 + 守卫口径。"""

    def test_cold_start_first_gain_updates_wealth_not_baseline(self):
        """①冷启动：账本建立后首笔收获只加 W 不动 R（种子事件，
        可解性论证见 economics.py docstring）。"""
        ledger = WealthLedger()
        acc = ledger.ensure_account("smith", 30)
        # 建账即基准：无收获事件史不变量 baseline == float(wealth)
        self.assertEqual(acc.baseline, float(acc.wealth))
        ledger.apply_gain("smith", 10)
        self.assertEqual(acc.wealth, 40)        # 收获全额入账
        self.assertEqual(acc.baseline, 30.0)    # 基准锚定不动（种子豁免）
        # 首笔后不变量翻转：有事件史 ⟺ baseline != float(wealth)
        self.assertNotEqual(acc.baseline, float(acc.wealth))

    def test_warm_second_gain_exact_ewma(self):
        """②暖态第二笔精确断言：R = α·W_new + (1-α)·R_prev
        （α=0.5 为 2 的负幂，二进制浮点精确可复现，无累进误差）。"""
        ledger = WealthLedger()
        ledger.ensure_account("smith", 30)
        ledger.apply_gain("smith", 10)           # 种子：W=40 R=30
        prev_baseline = ledger.account("smith").baseline
        ledger.apply_gain("smith", 10)           # 暖态：W=50
        acc = ledger.account("smith")
        expected = EWMA_ALPHA * acc.wealth + (1 - EWMA_ALPHA) * prev_baseline
        self.assertEqual(acc.wealth, 50)
        self.assertEqual(acc.baseline, expected)  # 契约字面公式（精确）
        self.assertEqual(acc.baseline, 40.0)      # 0.5·50 + 0.5·30

    def test_apply_gain_noop_without_account(self):
        """③未建账 apply_gain no-op：不凭空造 W=0 的账
        （否则 gain_ratio 分母退化恒触发，见 economics.py R₀ 论证）。"""
        ledger = WealthLedger()
        ledger.apply_gain("ghost", 10)
        self.assertIsNone(ledger.account("ghost"))
        # 既有账不受未建账调用影响
        ledger.ensure_account("smith", 30)
        ledger.apply_gain("ghost", 10)
        self.assertEqual(ledger.account("smith").wealth, 30)
        self.assertEqual(ledger.account("smith").baseline, 30.0)

    def test_on_event_ignores_non_item_given_kinds(self):
        """④on_event 只消费 item_given：其他 kind 一律不入账
        （引擎侧按 kinds=["item_given"] 订阅，本守卫双保险）。"""
        ledger = WealthLedger()
        ledger.ensure_account("smith", 30)
        for kind in ("player_spoke", "npc_action", "time_passed",
                     "player_entered"):
            ledger.on_event(WorldEvent(0, kind, "player",
                                       {"to": "smith", "item": "铁剑"}))
        acc = ledger.account("smith")
        self.assertEqual(acc.wealth, 30)
        self.assertEqual(acc.baseline, 30.0)

    def test_on_event_to_other_or_unknown_not_accounted(self):
        """⑤payload.to 指向他人/无账不入账：本账零污染，无名者不建账。"""
        ledger = WealthLedger()
        ledger.ensure_account("smith", 30)
        ledger.ensure_account("neighbor", 30)
        # to 指向他人：只有目标账入账
        ledger.on_event(WorldEvent(0, "item_given", "player",
                                   {"to": "neighbor", "item": "铁剑"}))
        self.assertEqual(ledger.account("neighbor").wealth, 40)
        self.assertEqual(ledger.account("smith").wealth, 30)
        # to 无账：不建账不入账
        ledger.on_event(WorldEvent(0, "item_given", "player",
                                   {"to": "stranger", "item": "铁剑"}))
        self.assertIsNone(ledger.account("stranger"))
        self.assertEqual(ledger.account("smith").wealth, 30)
        self.assertEqual(ledger.account("neighbor").wealth, 40)


# ============================================================================ #
# 2. 初值单一来源
# ============================================================================ #

class TestSingleSource(unittest.TestCase):
    """初值单一来源：引擎建账逐人复用 generate_village() 创生档案。"""

    def test_engine_accounts_match_village_genesis(self):
        """①引擎建账后 25 NPC 账面 wealth 与 generate_village() 创生档案
        逐人全等（键集全等 + 逐人值全等——两套创生逻辑漂移即红）。"""
        engine = _fresh_engine()
        profiles = generate_village()
        # 白盒键集对账：恰 25 账、键集与创生花名册全等（单一来源纪律）
        self.assertEqual(len(engine.wealth_ledger._accounts), 25)
        self.assertEqual(set(engine.wealth_ledger._accounts), set(profiles))
        for npc_id, profile in profiles.items():
            acc = engine.wealth_ledger.account(npc_id)
            self.assertIsNotNone(acc, msg=f"{npc_id} 未建账")
            self.assertEqual(acc.wealth, profile.innate.wealth,
                             msg=f"{npc_id} 账面与创生档案不一致")

    def test_ensure_account_idempotent(self):
        """②ensure_account 幂等：二次调用不改账（建账是创生事件）；
        有事件史后再次 ensure 也不重置账面。"""
        ledger = WealthLedger()
        first = ledger.ensure_account("smith", 30)
        again = ledger.ensure_account("smith", 999)
        self.assertIs(first, again)
        self.assertEqual(again.wealth, 30)
        self.assertEqual(again.baseline, 30.0)
        # 有收获事件史后：ensure 不回滚账面与基准
        ledger.apply_gain("smith", 10)
        ledger.ensure_account("smith", 30)
        acc = ledger.account("smith")
        self.assertEqual(acc.wealth, 40)
        self.assertEqual(acc.baseline, 30.0)

    def test_baseline_initializes_to_w0(self):
        """③基准初值 R = W₀（创生即基准：首日财富感知为零，
        静默对照路径的根）。"""
        ledger = WealthLedger()
        acc = ledger.ensure_account("smith", 17)
        self.assertIsInstance(acc.baseline, float)
        self.assertEqual(acc.baseline, 17.0)
        # 引擎侧 25 账全部 R == float(W₀)
        engine = _fresh_engine()
        for npc_id, profile in generate_village().items():
            acc = engine.wealth_ledger.account(npc_id)
            self.assertEqual(acc.baseline, float(profile.innate.wealth),
                             msg=f"{npc_id} 基准初值应为 float(W₀)")


# ============================================================================ #
# 3. 享乐适应（灵魂断言）
# ============================================================================ #

class TestHedonicAdaptation(unittest.TestCase):
    """灵魂断言：常送礼就不稀罕了——连收三把铁剑，显著带熄灭。"""

    def _three_swords_ratios(self, engine):
        ratios = []
        for _ in range(3):
            engine.player_gives("chen", "铁剑")
            acc = engine.wealth_ledger.account("chen")
            ratios.append((acc.wealth - acc.baseline) / max(acc.baseline, 1.0))
        return ratios

    def test_gain_ratio_strictly_decreasing(self):
        """①chen（创生 W₀=30）连收三把铁剑，gain_ratio 严格递减：
        0.333 → 0.250 → 0.200 量级（享乐适应的事件触发离散化）。"""
        engine = _fresh_engine()
        # 前置：chen 创生财富 W₀=30（引擎默认配置下 crc32 确定性）
        self.assertEqual(engine.wealth_ledger.account("chen").wealth, 30)
        r1, r2, r3 = self._three_swords_ratios(engine)
        self.assertGreater(r1, r2, msg="ratio 须严格递减")
        self.assertGreater(r2, r3, msg="ratio 须严格递减")
        self.assertAlmostEqual(r1, 10 / 30, places=6)
        self.assertAlmostEqual(r2, 0.25, places=6)
        self.assertAlmostEqual(r3, 0.2, places=6)
        # 账面链精确复核：W 30→40→50→60，R 30→30→40→50
        acc = engine.wealth_ledger.account("chen")
        self.assertEqual(acc.wealth, 60)
        self.assertEqual(acc.baseline, 50.0)  # 0.5·60 + 0.5·40，二进制精确

    def test_third_sword_extinguishes_fortune_tag(self):
        """②第三把铁剑 fortune_tag 返回 ""（前两把显著、第三把熄灭）。"""
        engine = _fresh_engine()
        engine.player_gives("chen", "铁剑")
        self.assertEqual(engine.wealth_ledger.fortune_tag("chen"), GAIN_TAG)
        engine.player_gives("chen", "铁剑")
        # 第二把 ratio 恰 0.25 临界，≥ 语义仍触发
        self.assertEqual(engine.wealth_ledger.fortune_tag("chen"), GAIN_TAG)
        engine.player_gives("chen", "铁剑")
        self.assertEqual(engine.wealth_ledger.fortune_tag("chen"), "")


# ============================================================================ #
# 4. 参照依赖（穷富分叉）
# ============================================================================ #

class TestReferenceDependence(unittest.TestCase):
    """参照依赖：NPC 对财富的感知是相对基准 R 的，而非绝对加总 W。"""

    def test_same_gift_poor_feels_rich_does_not(self):
        """单元构造 W0=50 与 W0=25 各收 10 两：富者 tag==""、穷者
        tag==GAIN_TAG——同一礼物穷人有感、富人无感（前景理论参照点）。"""
        ledger = WealthLedger()
        ledger.ensure_account("rich", 50)
        ledger.ensure_account("poor", 25)
        ledger.apply_gain("rich", 10)
        ledger.apply_gain("poor", 10)
        self.assertEqual(ledger.account("rich").wealth, 60)
        self.assertEqual(ledger.account("poor").wealth, 35)
        # 10/50 = 0.2 < 0.25：富者无感
        self.assertEqual(ledger.fortune_tag("rich"), "")
        # 10/25 = 0.4 ≥ 0.25：穷者有感
        self.assertEqual(ledger.fortune_tag("poor"), GAIN_TAG)


# ============================================================================ #
# 5. 财富显著带阈值语义
# ============================================================================ #

class TestFortuneBand(unittest.TestCase):
    """显著带：小礼不显著、无事件/无账恒空、阈值 ≥ 语义恰触发。"""

    def test_small_gift_rations_not_significant(self):
        """①小礼（干粮 1 两）不显著：W 30→31，ratio=1/30 ≈ 0.033 < 0.25。"""
        engine = _fresh_engine()
        engine.player_gives("chen", "干粮")
        acc = engine.wealth_ledger.account("chen")
        self.assertEqual(acc.wealth, 31)           # 干粮估值 1 两
        self.assertEqual(acc.baseline, 30.0)
        self.assertEqual(engine.wealth_ledger.fortune_tag("chen"), "")

    def test_no_event_fortune_tag_empty(self):
        """②无收获事件 fortune_tag==""（静默对照路径：基线不变性的根）。"""
        engine = _fresh_engine()
        for npc_id in ("chen", "lily", "tavern_sun"):
            acc = engine.wealth_ledger.account(npc_id)
            # 无事件史不变量：baseline == float(wealth)
            self.assertEqual(acc.baseline, float(acc.wealth), msg=npc_id)
            self.assertEqual(engine.wealth_ledger.fortune_tag(npc_id), "")

    def test_unknown_npc_fortune_tag_empty(self):
        """③无账 npc_id 返回 ""（player/未知 id 不凭空感知）。"""
        ledger = WealthLedger()
        self.assertEqual(ledger.fortune_tag("nobody"), "")
        engine = _fresh_engine()
        self.assertEqual(engine.wealth_ledger.fortune_tag("player"), "")
        self.assertEqual(engine.wealth_ledger.fortune_tag("unknown_id"), "")

    def test_threshold_boundary_exact_ratio_triggers(self):
        """④阈值上/下边界：W0=40 收 10 两 ratio=0.25 恰触发（≥ 语义）；
        W0=41 收 10 两 ratio≈0.244 不触发。"""
        ledger = WealthLedger()
        ledger.ensure_account("at40", 40)
        ledger.apply_gain("at40", 10)
        self.assertEqual((50 - 40) / max(40, 1), 0.25)   # 恰等阈值
        self.assertEqual(ledger.fortune_tag("at40"), GAIN_TAG)
        ledger.ensure_account("at41", 41)
        ledger.apply_gain("at41", 10)
        self.assertAlmostEqual((51 - 41) / max(41, 1), 0.2439, places=4)
        self.assertEqual(ledger.fortune_tag("at41"), "")


# ============================================================================ #
# 6. 决策注入（端到端，玩家可感知口径）
# ============================================================================ #

class TestDecisionInjection(unittest.TestCase):
    """端到端：财富显著带标签进决策上下文【此刻内心】行（prompt 内容级）。"""

    def test_gifted_npc_prompt_contains_fortune_tag(self):
        """①eng.player_gives("chen","铁剑") 后 player_says 的 user prompt
        【此刻内心】行含"最近发了笔小财"（wraps 捕获 messages，文本断言
        非调用计数——防"账本在但对话不用"的隐性回归）。"""
        engine = _fresh_engine()
        engine.player_gives("chen", "铁剑")
        # 前置：账本侧显著带已触发
        self.assertEqual(engine.wealth_ledger.fortune_tag("chen"), GAIN_TAG)
        with mock.patch.object(engine.llm, "chat",
                               wraps=engine.llm.chat) as spy:
            result = engine.player_says(SLOW_ASK, "chen")
        self.assertEqual(result["brain"], "slow",
                         msg="长句须走慢脑产生 prompt（前置）")
        line = _inner_line(_last_user_prompt(spy))
        self.assertIn(GAIN_TAG, line,
                      msg=f"【此刻内心】行应含 {GAIN_TAG!r}，实际: {line!r}")

    def test_control_npc_prompt_calm_without_fortune(self):
        """②对照：未送礼 NPC（lily）的行恰为"心境平稳"，且全文不含
        财富标签——证明主链路命中来自赠予显著带而非句面巧合。"""
        engine = _fresh_engine()
        with mock.patch.object(engine.llm, "chat",
                               wraps=engine.llm.chat) as spy:
            result = engine.player_says(SLOW_ASK, "lily")
        self.assertEqual(result["brain"], "slow")
        user_prompt = _last_user_prompt(spy)
        self.assertEqual(_inner_line(user_prompt), "心境平稳")
        self.assertNotIn(GAIN_TAG, user_prompt)

    def test_fortune_replaces_calm_tag(self):
        """③替换语义：fortune 非空时该行不含"心境平稳"（8D 全常带与
        "发了笔小财"语义矛盾，替换不增行宽——decision.py 拼接契约）。"""
        engine = _fresh_engine()
        engine.player_gives("chen", "铁剑")
        with mock.patch.object(engine.llm, "chat",
                               wraps=engine.llm.chat) as spy:
            engine.player_says(SLOW_ASK, "chen")
        line = _inner_line(_last_user_prompt(spy))
        self.assertNotIn("心境平稳", line)
        # 8D 全常带时：行内恰一个财富标签（"心境平稳"被替换）
        self.assertEqual(line, GAIN_TAG)

    def test_ledgerless_npc_prompt_old_format(self):
        """④未挂账本的直接构造 NPC（getattr 缺省）：prompt 与旧格式
        逐字节全等（向后兼容——接线前格式零变化）。"""
        persona = Persona(id="wanderer", name="流浪客", role="villager",
                          location_id="plaza", greeting_bank=["……"],
                          fallback_bank=["……"])
        world = World()
        llm = create_provider("mock")
        npc = NPC(persona, llm, world)
        self.assertFalse(hasattr(npc, "wealth_ledger"),
                         msg="直接构造 NPC 不应挂财富账本（getattr 缺省前置）")
        with mock.patch.object(llm, "chat", wraps=llm.chat) as spy:
            npc.handle_player_input(world, LEDGERLESS_ASK)
        prompt = _last_user_prompt(spy)
        # 旧格式行 = 纯 8D 离散标签拼接（无任何财富标签拼接）
        expected_line = "【此刻内心】" + "、".join(
            npc.inner_state.to_discrete_tags())
        self.assertIn(expected_line, prompt)
        self.assertNotIn(GAIN_TAG, prompt)
        # 全等证明：decide() getattr 缺省路径产出 == fortune 缺省 ""直调
        # （fortune 为空时与接线前旧格式逐字节全等的根因断言）
        direct = npc.decision._user_prompt(
            LEDGERLESS_ASK, world,
            npc.memory.context_for(LEDGERLESS_ASK),
            world.snapshot("wanderer"), npc.inner_state)
        self.assertEqual(prompt, direct)


# ============================================================================ #
# 7. 物品词命中口径（复合品边界）
# ============================================================================ #

class TestCompoundBoundary(unittest.TestCase):
    """物品词命中：子串命中 + 品类后缀边界 + 多词长词优先 + 未知回退。"""

    def _gain_with(self, item: str) -> int:
        """fresh 账本收一件 item 后的账面 W（经 on_event 公共路径）。"""
        ledger = WealthLedger()
        ledger.ensure_account("smith", 30)
        ledger.on_event(WorldEvent(0, "item_given", "player",
                                   {"to": "smith", "item": item}))
        return ledger.account("smith").wealth

    def test_sword_sheath_compound_default_value(self):
        """①"铁剑鞘"按 DEFAULT 1 两（品类后缀边界：复合品 ≠ 铁剑）。"""
        self.assertEqual(self._gain_with("铁剑鞘"), 31)

    def test_measure_word_prefix_tail_hit(self):
        """②"一把铁剑"命中 10 两（词尾命中：量词前缀不干扰）。"""
        self.assertEqual(self._gain_with("一把铁剑"), 40)

    def test_unknown_item_default_value(self):
        """③未知物品"传家玉佩"按 DEFAULT_ITEM_VALUE=1 两
        （宽口径：无价之礼也是心意，基准照常追踪）。"""
        self.assertEqual(self._gain_with("传家玉佩"),
                         30 + DEFAULT_ITEM_VALUE)

    def test_leather_armor_ledger_side_lock(self):
        """④"皮甲"8 两与快脑报价语义对齐断言：ITEM_VALUES["皮甲"]==8
        只锁账本侧（重置价值），不锁快脑——fast_brain"皮甲五两"为
        商人让利零售口径，两套口径有意分层（economics.py docstring）。"""
        self.assertEqual(ITEM_VALUES["皮甲"], 8)
        self.assertEqual(self._gain_with("皮甲"), 38)


# ============================================================================ #
# 8. 零 LLM 边界 + 六情架构边界
# ============================================================================ #

class TestZeroLLMBoundary(unittest.TestCase):
    """零 LLM：财富层 Tier 0 纯规则零 token；六情边界：不触碰 8D。"""

    def test_ledger_operations_zero_llm_calls(self):
        """①WealthLedger 全操作（送礼入账/标签查询/账目观测/幂等建账）
        后 mock.call_count 不增——财富层本身零 LLM。"""
        engine = _fresh_engine()
        before = engine.llm.call_count
        engine.player_gives("chen", "铁剑")
        engine.wealth_ledger.fortune_tag("chen")
        engine.wealth_ledger.fortune_tag("lily")
        engine.wealth_ledger.account("chen")
        engine.wealth_ledger.account("nobody")
        engine.wealth_ledger.ensure_account("chen", 30)  # 幂等 no-op
        self.assertEqual(engine.llm.call_count, before,
                         msg="财富账本操作不得触发任何 LLM 调用")

    def test_economics_source_no_inner_state_reference(self):
        """②六情边界结构断言：economics.py 代码不出现 "inner_state"
        引用（读源文件文本，剔除模块 docstring 后断言——锁"财富层
        不触碰 8D"的架构边界；docstring 对 inner_state.py 的设计说明
        属文档非代码）。"""
        import engine.economics as economics_mod
        with open(economics_mod.__file__, encoding="utf-8") as fh:
            source = fh.read()
        module_doc = ast.get_docstring(ast.parse(source), clean=False) or ""
        code_text = source.replace(module_doc, "", 1)
        self.assertNotIn("inner_state", code_text,
                         msg="economics.py 代码层不得引用 inner_state"
                             "（同一事件双份情绪增量=两套情绪系统漂移）")

    def test_gift_emotion_channel_e_p_independent(self):
        """③送礼事件 chen 的 8D e_P 仍由六情通道变化（增量≠0 且恰为
        XUNZI"好"矢量首分量）——情绪通道独立运转，与财富层双轨并行。"""
        engine = _fresh_engine()
        chen = engine.npcs["chen"]
        e_p_before = chen.inner_state.e_P
        engine.player_gives("chen", "铁剑")
        e_p_after = chen.inner_state.e_P
        self.assertNotEqual(e_p_after, e_p_before,
                            msg="送礼 e_P 增量须 ≠ 0（六情通道独立运转）")
        self.assertAlmostEqual(e_p_after - e_p_before,
                               XUNZI_EMOTION_VECTORS["好"][0], places=9)
        # 同一事件双通道各自落地：8D 变了，财富账也变了（两套独立系统）
        self.assertEqual(engine.wealth_ledger.account("chen").wealth, 40)


# ============================================================================ #
# 9. token 基线守护
# ============================================================================ #

class TestTokenBaselineGuard(unittest.TestCase):
    """token 基线守护：基线场景（无财富事件）fortune 恒空——prompt
    全等根因断言（4519 数字复现由 PM 负责，本类只锁 fortune 空侧）。"""

    # 与 scripts/token_baseline.py CORE_DIALOGUES 同口径（4 句均命中
    # Mock 话题库且不命中快脑——token 基线 4 句快脑硬保护锁死）
    BASELINE_DIALOGUES = [
        ("铁匠，打把剑", "chen"),
        ("有好铁矿石吗", "chen"),
        ("有新货吗", "lily"),
        ("有什么消息", "lily"),
    ]

    def test_baseline_scenario_fortune_tags_empty(self):
        """token_baseline 场景（24 tick 全天运转 + 4 核心对话，全程零
        财富事件）：chen/lily fortune_tag 均为 ""——fortune 空 →
        【此刻内心】行零变化 → prompt 与接线前逐字节全等的根因。"""
        random.seed(42)  # 与 scripts/token_baseline.py 同种子口径
        engine = NPCEngine(llm=create_provider("mock"))
        for _ in range(24):
            engine.tick(60)
        with mock.patch.object(engine.llm, "chat",
                               wraps=engine.llm.chat) as spy:
            for text, npc_id in self.BASELINE_DIALOGUES:
                result = engine.player_says(text, npc_id)
                self.assertEqual(result["brain"], "slow",
                                 msg=f"{text!r} 须走慢脑产生 prompt"
                                     "（基线口径前置）")
        self.assertEqual(engine.wealth_ledger.fortune_tag("chen"), "")
        self.assertEqual(engine.wealth_ledger.fortune_tag("lily"), "")
        # 4 次 prompt 全文均不含财富标签（prompt 全等的直接证据）
        self.assertEqual(len(spy.call_args_list), 4)
        for call in spy.call_args_list:
            self.assertNotIn(GAIN_TAG, call.args[0][1]["content"])
        # 无收获事件史不变量：账面未被任何事件触碰（建账后零变化）
        for npc_id in ("chen", "lily"):
            acc = engine.wealth_ledger.account(npc_id)
            self.assertEqual(acc.baseline, float(acc.wealth), msg=npc_id)


# ============================================================================ #
# 11. 契约常量锁 + 两套价值口径对齐
# ============================================================================ #

class TestConstantsLock(unittest.TestCase):
    """常量锁：EWMA/显著带阈值/物品估值表；与 fast_brain 报价口径对齐。"""

    def test_constants_values_locked(self):
        """契约常量锁：EWMA_ALPHA=0.5（一礼半适应）、GAIN_TAG、
        GAIN_TAG_RATIO=0.25、ITEM_VALUES 逐项、DEFAULT_ITEM_VALUE=1。"""
        self.assertEqual(EWMA_ALPHA, 0.5)
        self.assertEqual(GAIN_TAG, "最近发了笔小财")
        self.assertEqual(GAIN_TAG_RATIO, 0.25)
        self.assertEqual(ITEM_VALUES,
                         {"铁剑": 10, "皮甲": 8, "铁锤": 3,
                          "米酒": 2, "草药": 1, "干粮": 1})
        self.assertEqual(DEFAULT_ITEM_VALUE, 1)

    def test_price_alignment_with_fast_brain(self):
        """ITEM_VALUES 与 fast_brain _PRICE_LINES 铁剑/铁锤语义对齐：
        "铁剑"→10 两 ↔ 报价句含"铁剑"与"十两"；"铁锤"→3 两 ↔ 含"三两"
        ——玩家问价所得与 NPC 收礼所感同价，防两套价值口径漂移。
        （皮甲有意不对齐：快脑"五两"为让利零售口径，账本记重置价值 8。）"""
        from engine.fast_brain import _PRICE_LINES
        self.assertEqual(ITEM_VALUES["铁剑"], 10)
        self.assertIn("铁剑", _PRICE_LINES["铁剑"])
        self.assertIn("十两", _PRICE_LINES["铁剑"])   # "铁剑十两银子"口径
        self.assertEqual(ITEM_VALUES["铁锤"], 3)
        self.assertIn("三两", _PRICE_LINES["铁锤"])    # "铁锤三两"口径


if __name__ == "__main__":
    unittest.main()

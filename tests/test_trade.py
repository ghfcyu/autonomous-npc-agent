"""T5 第三小步验收测试：交易 API 最小闭环 + 损失域（审查指令 1）。

被测契约（PM 定死，本文件只测不改）：
1. economics：LOSS_TAG/LOSS_TAG_RATIO/ENDOWMENT_MULTIPLIER 常量；
   endowment_price 传统四舍五入（契约表 10→25/8→20/3→8/2→5/1→3）；
   cn_amount 1-99 中文金额；estimate_value 公开化（原 _estimate_value）；
   apply_loss（W 减、R 完全不动=损失域基准零吸收）；fortune_tag 双向
   （gain/loss 数学互斥，空路径行为与现状逐字节一致）；on_event 消费
   item_given/item_bought/item_sold 三类事件；
2. engine：player_buys/player_sells 公开 API（买入禀赋溢价 2.5×、
   卖出标准价不压价、手动库存转移防双事件、out_of_stock 拒绝、
   背景 NPC 不开放、宽松成交模式）；订阅 kinds 三类；
3. memory：item_bought/item_sold 模板（importance 0.6 日常档）；
4. npc：交易事件 seller/buyer 点名感知兼容（无视距离必可闻）。

灵魂断言（TestLossAsymmetry）：损失域基准零吸收 vs 收获域半吸收——
痛感不钝化（chen 连卖三把铁剑 LOSS_TAG×3，loss_ratio 0.333→0.667→
1.000）对享乐适应（全新账连收三把，第三笔 GAIN_TAG 熄灭），
同量纲的收获侧熄灭 vs 损失侧持续，这是损失厌恶 2.5 倍
（ENDOWMENT_MULTIPLIER 禀赋溢价）的离散工程根源。

编写纪律：零第三方依赖（unittest 标准库）；场景构造沿
tests/test_economics.py 的 _fresh_engine 模式（random.seed 双保险）；
只断言玩家可感知行为与玩家可复核的账面/事件/记忆/prompt 内容，
不写数字锁空转（W₀ 等前置数字仅作场景锚，行为断言一律用
前后差值/集合成员/内容包含口径）。六情边界（交易事件不产生 8D
情绪增量）为设计内边界：六情映射不认识 item_bought/item_sold，
经济感知只经 fortune_tag 离散标签出口（economics.py 模块 docstring）。
"""

import random
import re
import unittest
from unittest import mock

from engine.economics import (ENDOWMENT_MULTIPLIER, GAIN_TAG, LOSS_TAG,
                              LOSS_TAG_RATIO, WealthLedger, cn_amount,
                              endowment_price, estimate_value)
from engine.engine import NPCEngine
from engine.llm import create_provider
from engine.world import WorldEvent


# ---- 场景常量（与 tests/test_economics.py 同口径：21 字长句稳定走慢脑） ----
SLOW_ASK = "陈师傅，跟您打听个事儿，您这儿最近可有什么新鲜玩意儿？"


def _fresh_engine(seed: int = 20261016) -> NPCEngine:
    """fresh 引擎：独立世界与账本，用例间零污染（random.seed 双保险）。"""
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


def _short_contents(engine: NPCEngine, npc_id: str) -> list:
    """读 NPC 短期记忆全部文本（感知链路验证口）。"""
    return [r.content for r in engine.npcs[npc_id].memory.short.recent()]


# ============================================================================ #
# 1. 端到端买入（player_buys）
# ============================================================================ #

class TestPlayerBuys(unittest.TestCase):
    """player_buys：禀赋溢价成交链 + 库存转移 + 卖方入账 + 感知记忆。"""

    def test_buy_end_to_end_chain(self):
        """①契约场景：先给 chen 库存放一把"铁剑"→ player_buys 返回 ok、
        price==25（endowment_price(10)）、player 库存获得铁剑、chen 库存
        减一、chen 账 W 增加 25（item_bought→apply_gain 经引擎订阅链）、
        chen 短期记忆含买入记录（seller 点名感知）。"""
        engine = _fresh_engine()
        engine.world.entities["chen"].inventory.append("铁剑")  # 契约口径先存放
        before_count = engine.world.entities["chen"].inventory.count("铁剑")
        before_w = engine.wealth_ledger.account("chen").wealth
        result = engine.player_buys("chen", "铁剑")
        self.assertTrue(result["ok"])
        self.assertEqual(result["price"], 25)          # endowment_price(10)
        self.assertEqual(result["from"], "chen")
        self.assertTrue(result["endowment_premium"])
        # 库存转移：玩家获得、卖家减一
        self.assertIn("铁剑", engine.world.entities["player"].inventory)
        self.assertEqual(engine.world.entities["chen"].inventory.count("铁剑"),
                         before_count - 1)
        # 卖方收钱入账：W + 25（事件 → 引擎订阅 → apply_gain）
        self.assertEqual(engine.wealth_ledger.account("chen").wealth,
                         before_w + 25)
        # 唯一事件源 item_bought：恰一条、payload 键齐全、无 item_given 双事件
        bought = [e for e in engine.world.bus.history if e.kind == "item_bought"]
        self.assertEqual(len(bought), 1)
        self.assertEqual(bought[0].payload["seller"], "chen")
        self.assertEqual(bought[0].payload["item"], "铁剑")
        self.assertEqual(bought[0].payload["price"], 25)
        self.assertFalse(any(e.kind == "item_given"
                             for e in engine.world.bus.history),
                         msg="手动转移不得触发 item_given（双事件双记账）")
        # 感知链路：chen 记得这笔卖出（memory.py 模板产物）
        self.assertTrue(any("从我手里买走了铁剑" in c
                            for c in _short_contents(engine, "chen")))
        # 回复确定性 + 中文读数 + ≤40 字符
        self.assertEqual(result["reply"], "「铁剑你拿好，二十五两不二价。」")
        self.assertLessEqual(len(result["reply"]), 40)

    def test_buy_out_of_stock_rejected(self):
        """②无库存拒绝：lily（商人，库存无铁剑）→ ok=False reason=
        out_of_stock；账面零变化、零事件发布（bus.history 长度不变）。"""
        engine = _fresh_engine()
        before_w = engine.wealth_ledger.account("lily").wealth
        history_before = len(engine.world.bus.history)
        result = engine.player_buys("lily", "铁剑")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "out_of_stock")
        self.assertEqual(engine.wealth_ledger.account("lily").wealth, before_w)
        self.assertNotIn("铁剑", engine.world.entities["player"].inventory)
        # 拒绝路径零副作用：不发事件、不动库存
        self.assertEqual(len(engine.world.bus.history), history_before)
        self.assertFalse(any(e.kind in ("item_bought", "item_sold", "item_given")
                              for e in engine.world.bus.history))

    def test_buy_rejects_unknown_and_background_npc(self):
        """③unknown npc：无名 id 与背景 NPC id（baker_liu 在
        background_npcs）一律拒绝——背景 NPC 不开放玩家交易（PM 书面
        评估裁定，交易闭环只进核心 NPC）。"""
        engine = _fresh_engine()
        self.assertIn("baker_liu", engine.background_npcs)  # 前置：确为背景 NPC
        self.assertNotIn("baker_liu", engine.npcs)
        self.assertEqual(engine.player_buys("nobody", "铁剑"),
                         {"ok": False, "reason": "unknown npc"})
        self.assertEqual(engine.player_buys("baker_liu", "铁剑"),
                         {"ok": False, "reason": "unknown npc"})
        self.assertEqual(engine.player_sells("baker_liu", "铁剑"),
                         {"ok": False, "reason": "unknown npc"})


# ============================================================================ #
# 2. 端到端卖出（player_sells）
# ============================================================================ #

class TestPlayerSells(unittest.TestCase):
    """player_sells：标准价收购链 + 买方记账（损失域）+ 感知记忆。"""

    def test_sell_end_to_end_chain(self):
        """①契约场景：player_sells("chen","铁剑")（玩家先持有）→ chen 库存
        获得铁剑、chen 账 W 减 10（item_sold→apply_loss）、item_sold 事件
        发布、chen 短期记忆含"把铁剑卖给了我"模板产物。"""
        engine = _fresh_engine()
        engine.world.entities["player"].inventory.append("铁剑")  # 玩家侧持有
        before_count = engine.world.entities["chen"].inventory.count("铁剑")
        before_w = engine.wealth_ledger.account("chen").wealth
        result = engine.player_sells("chen", "铁剑")
        self.assertTrue(result["ok"])
        self.assertEqual(result["price"], 10)     # 收购按标准价（estimate_value）
        self.assertEqual(result["to"], "chen")
        # 库存转移：NPC 获得、玩家失去
        self.assertEqual(engine.world.entities["chen"].inventory.count("铁剑"),
                         before_count + 1)
        self.assertNotIn("铁剑", engine.world.entities["player"].inventory)
        # 买方付钱记账：W - 10（损失域）
        self.assertEqual(engine.wealth_ledger.account("chen").wealth,
                         before_w - 10)
        # item_sold 事件发布，payload 键齐全
        sold = [e for e in engine.world.bus.history if e.kind == "item_sold"]
        self.assertEqual(len(sold), 1)
        self.assertEqual(sold[0].payload["buyer"], "chen")
        self.assertEqual(sold[0].payload["price"], 10)
        # 感知链路：chen 记得这笔买入（buyer 点名感知）
        self.assertTrue(any("把铁剑卖给了我" in c
                            for c in _short_contents(engine, "chen")))
        # 回复确定性 + 中文读数 + ≤40 字符
        self.assertEqual(result["reply"], "「铁剑我收下了，十两付你。」")
        self.assertLessEqual(len(result["reply"]), 40)

    def test_sell_lenient_without_player_inventory(self):
        """②宽松成交：玩家库存无该物品同样成交（player_gives 宽松模式
        同构——玩家侧库存不做严格模拟）；NPC 侧库存不动、事件照发、
        账照记。"""
        engine = _fresh_engine()
        self.assertNotIn("铁剑", engine.world.entities["player"].inventory)
        before_count = engine.world.entities["chen"].inventory.count("铁剑")
        before_w = engine.wealth_ledger.account("chen").wealth
        result = engine.player_sells("chen", "铁剑")
        self.assertTrue(result["ok"])
        self.assertEqual(result["price"], 10)
        self.assertEqual(engine.world.entities["chen"].inventory.count("铁剑"),
                         before_count)          # 宽松模式不动 NPC 库存
        self.assertEqual(engine.wealth_ledger.account("chen").wealth,
                         before_w - 10)        # 记账照常
        self.assertTrue(any(e.kind == "item_sold" for e in engine.world.bus.history))


# ============================================================================ #
# 3. 损失域不对称（灵魂断言）
# ============================================================================ #

class TestLossAsymmetry(unittest.TestCase):
    """灵魂断言：收获侧基准半吸收（享乐适应，第三把剑熄灭）vs
    损失侧基准零吸收（痛感不钝化，标签持续显著）。"""

    def test_apply_loss_baseline_sticky_vs_gain_half_absorption(self):
        """①单元级不对称：apply_loss 后 R 恒定不动（零吸收）；apply_gain
        暖态 R=αW+(1-α)R 半吸收——两域同函数族、相反吸收率。"""
        ledger = WealthLedger()
        ledger.ensure_account("seller", 30)
        ledger.apply_loss("seller", 10)
        acc = ledger.account("seller")
        self.assertEqual(acc.wealth, 20)
        self.assertEqual(acc.baseline, 30.0)     # R 完全不动（损失域粘性）
        # 对照：收获侧第二笔起半吸收
        ledger.ensure_account("jones", 30)
        ledger.apply_gain("jones", 10)            # 种子：W=40 R=30
        ledger.apply_gain("jones", 10)            # 暖态：W=50
        self.assertEqual(ledger.account("jones").baseline, 40.0)  # 0.5·50+0.5·30
        # apply_loss 未建账 no-op（与 apply_gain 同一守卫口径）
        ledger.apply_loss("ghost", 10)
        self.assertIsNone(ledger.account("ghost"))

    def test_three_sales_persist_vs_three_gifts_extinguish(self):
        """②灵魂对比（契约第 4 断言）：chen W₀=30 R=30 建账，连卖三把
        铁剑（每把 10 两）→ W=0、R 恒 30，每笔后 fortune_tag 依次
        LOSS_TAG×3（loss_ratio 0.333→0.667→1.000，痛感持续不钝化）；
        对照组全新账连收三把铁剑（享乐适应）第三笔 GAIN_TAG 熄灭——
        同量纲收获侧熄灭 vs 损失侧持续。"""
        engine = _fresh_engine()
        acc = engine.wealth_ledger.account("chen")
        self.assertEqual((acc.wealth, acc.baseline), (30, 30.0))  # 场景锚：W₀=30 R=30
        ratios, tags = [], []
        for _ in range(3):
            engine.player_sells("chen", "铁剑")
            acc = engine.wealth_ledger.account("chen")
            ratios.append((acc.baseline - acc.wealth) / max(acc.baseline, 1.0))
            tags.append(engine.wealth_ledger.fortune_tag("chen"))
        self.assertEqual(acc.wealth, 0)          # 30 - 3×10
        self.assertEqual(acc.baseline, 30.0)     # R 恒定：损失侧零吸收
        self.assertAlmostEqual(ratios[0], 1 / 3, places=6)   # 0.333
        self.assertAlmostEqual(ratios[1], 2 / 3, places=6)   # 0.667
        self.assertAlmostEqual(ratios[2], 1.0, places=6)      # 1.000
        self.assertEqual(tags, [LOSS_TAG, LOSS_TAG, LOSS_TAG])
        # 对照组：全新引擎连收三把铁剑——第三笔熄灭（享乐适应）
        control = _fresh_engine()
        control_tags = []
        for _ in range(3):
            control.player_gives("chen", "铁剑")
            control_tags.append(control.wealth_ledger.fortune_tag("chen"))
        self.assertEqual(control_tags, [GAIN_TAG, GAIN_TAG, ""])


# ============================================================================ #
# 4. 禀赋估值（WTA=2.5×WTP 端到端）
# ============================================================================ #

class TestEndowmentPricing(unittest.TestCase):
    """禀赋溢价：endowment_price 四舍五入契约表 + "10 两卖给铁匠、
    25 两卖回给你"端到端链。"""

    def test_endowment_price_rounding_table(self):
        """①契约取整表逐项：铁剑 10→25、皮甲 8→20、铁锤 3→8、米酒
        2→5、干粮/草药 1→3（传统四舍五入 int(x+0.5)）；且与
        estimate_value 公开函数链路一致。"""
        for base, price in ((10, 25), (8, 20), (3, 8), (2, 5), (1, 3)):
            self.assertEqual(endowment_price(base), price)
        self.assertEqual(endowment_price(estimate_value("铁剑")), 25)
        self.assertEqual(endowment_price(estimate_value("皮甲")), 20)
        self.assertEqual(endowment_price(estimate_value("铁锤")), 8)
        self.assertEqual(endowment_price(estimate_value("米酒")), 5)
        self.assertEqual(endowment_price(estimate_value("干粮")), 3)
        self.assertEqual(endowment_price(estimate_value("草药")), 3)
        self.assertEqual(estimate_value("铁剑"), 10)  # 公开化签名行为不变

    def test_sell_ten_buy_back_twentyfive_chain(self):
        """②端到端链（契约第 5 断言）：无库存买入路径拒绝；player_sells
        让 chen 拥有铁剑后 player_buys 价格 25 == endowment_price(
        estimate_value("铁剑")) == 2.5×10 四舍五入——"你 10 两卖给
        铁匠、他 25 两卖回给你"。"""
        engine = _fresh_engine()
        # 前置一：无库存路径拒绝（卖出侧宽松对称地不拒绝，见 TestPlayerSells）
        self.assertEqual(engine.player_buys("lily", "铁剑")["reason"],
                         "out_of_stock")
        # 玩家先持有铁剑再卖（转移路径真实发生）
        engine.world.entities["player"].inventory.append("铁剑")
        sell = engine.player_sells("chen", "铁剑")
        self.assertTrue(sell["ok"])
        self.assertEqual(sell["price"], 10)       # 你 10 两卖给铁匠
        buy = engine.player_buys("chen", "铁剑")
        self.assertTrue(buy["ok"])
        self.assertEqual(buy["price"], 25)        # 他 25 两卖回给你
        self.assertEqual(buy["price"],
                         endowment_price(estimate_value("铁剑")))
        self.assertTrue(buy["endowment_premium"])
        # 卖回后玩家重获铁剑（钱货两讫闭环）
        self.assertIn("铁剑", engine.world.entities["player"].inventory)


# ============================================================================ #
# 5. 损失标签进决策上下文（prompt 内容级）
# ============================================================================ #

class TestLossPromptInjection(unittest.TestCase):
    """端到端玩家可感知口径：损失显著带标签进【此刻内心】行
    （decision.py fortune_tag 双向自动生效，本类只测不改 decision）。"""

    def test_sold_npc_prompt_contains_loss_tag(self):
        """①player_sells 触发损失标签后，chen 下一次慢脑决策上下文的
        【此刻内心】行含"最近手头紧"（wraps 捕获 messages，prompt 内容
        级断言）。"""
        engine = _fresh_engine()
        engine.player_sells("chen", "铁剑")
        # 前置：账本侧损失显著带已触发（loss_ratio 0.333 ≥ 0.25）
        self.assertEqual(engine.wealth_ledger.fortune_tag("chen"), LOSS_TAG)
        with mock.patch.object(engine.llm, "chat",
                               wraps=engine.llm.chat) as spy:
            result = engine.player_says(SLOW_ASK, "chen")
        self.assertEqual(result["brain"], "slow",
                         msg="长句须走慢脑产生 prompt（前置）")
        line = _inner_line(_last_user_prompt(spy))
        self.assertIn(LOSS_TAG, line,
                      msg=f"【此刻内心】行应含 {LOSS_TAG!r}，实际: {line!r}")
        # 替换语义与 gain 侧同构：非空 fortune 替换"心境平稳"
        self.assertNotIn("心境平稳", line)

    def test_fresh_engine_prompt_calm_without_loss_tag(self):
        """②对照：无交易全新引擎 chen 的【此刻内心】行恰为"心境平稳"，
        全文不含损失标签——证明主链路命中来自交易损失带而非巧合。"""
        engine = _fresh_engine()
        with mock.patch.object(engine.llm, "chat",
                               wraps=engine.llm.chat) as spy:
            result = engine.player_says(SLOW_ASK, "chen")
        self.assertEqual(result["brain"], "slow")
        user_prompt = _last_user_prompt(spy)
        self.assertEqual(_inner_line(user_prompt), "心境平稳")
        self.assertNotIn(LOSS_TAG, user_prompt)
        self.assertNotIn(GAIN_TAG, user_prompt)


# ============================================================================ #
# 6. 六情边界（交易事件不产生 8D 情绪增量）
# ============================================================================ #

class TestSixEmotionBoundary(unittest.TestCase):
    """六情边界：交易前后 8D 心智参数逐项不变——六情映射不认识
    item_bought/item_sold 是设计内边界（经济事件的情绪语义为空，
    感知只经 fortune_tag 离散标签出口）。"""

    def test_trades_leave_inner_state_untouched(self):
        """①player_sells + player_buys 前后 chen 的 8D 逐项不变；
        对照：赠予事件（item_given）8D 有六情增量——两条通道分层证据。"""
        engine = _fresh_engine()
        chen = engine.npcs["chen"]
        before = dict(chen.inner_state.to_dict())
        engine.world.entities["player"].inventory.append("铁剑")
        engine.player_sells("chen", "铁剑")
        buy = engine.player_buys("chen", "铁剑")
        self.assertTrue(buy["ok"])
        self.assertEqual(chen.inner_state.to_dict(), before)  # 8D 逐项不变
        # 对照：赠予走六情通道（"好"矢量），8D 必有增量——分层非失效
        engine.player_gives("chen", "干粮")
        self.assertNotEqual(chen.inner_state.to_dict(), before)


# ============================================================================ #
# 7. 账本事件接线（on_event 单元级）
# ============================================================================ #

class TestLedgerEventWiring(unittest.TestCase):
    """on_event 三类事件守卫与计价口径（payload 键 seller/buyer、有账
    才动、price 非整数防御性跳过）。"""

    def test_on_event_item_bought_seller_gain(self):
        """①item_bought：卖家有账 apply_gain(成交价)——首笔交易收入同
        样享种子豁免（cold 判定对收获统一语义）；无账 seller 不建账。"""
        ledger = WealthLedger()
        ledger.ensure_account("smith", 30)
        ledger.on_event(WorldEvent(0, "item_bought", "player",
                                    {"seller": "smith", "item": "铁剑",
                                     "price": 25}))
        acc = ledger.account("smith")
        self.assertEqual(acc.wealth, 55)      # 30 + 25（成交价入账）
        self.assertEqual(acc.baseline, 30.0)    # 首笔收获种子豁免（R 锚定）
        ledger.on_event(WorldEvent(0, "item_bought", "player",
                                    {"seller": "stranger", "item": "铁剑",
                                     "price": 25}))
        self.assertIsNone(ledger.account("stranger"))
        self.assertEqual(ledger.account("smith").wealth, 55)  # 本账零污染

    def test_on_event_item_sold_buyer_loss(self):
        """②item_sold：买家有账 apply_loss(价金)，R 完全不动；无账
        buyer 不建账。"""
        ledger = WealthLedger()
        ledger.ensure_account("smith", 30)
        ledger.on_event(WorldEvent(0, "item_sold", "player",
                                    {"buyer": "smith", "item": "铁剑",
                                     "price": 10}))
        acc = ledger.account("smith")
        self.assertEqual(acc.wealth, 20)
        self.assertEqual(acc.baseline, 30.0)    # 损失域基准粘性
        ledger.on_event(WorldEvent(0, "item_sold", "player",
                                    {"buyer": "stranger", "item": "铁剑",
                                     "price": 10}))
        self.assertIsNone(ledger.account("stranger"))
        self.assertEqual(ledger.account("smith").wealth, 20)

    def test_on_event_malformed_price_skipped(self):
        """③防御性兜底：price 非整数/缺失时跳过不入账（不凭空估值，
        不因畸形 payload 崩溃——economics.py docstring 书面裁定）。"""
        ledger = WealthLedger()
        ledger.ensure_account("smith", 30)
        ledger.on_event(WorldEvent(0, "item_bought", "player",
                                    {"seller": "smith", "item": "铁剑",
                                     "price": "很贵"}))
        self.assertEqual(ledger.account("smith").wealth, 30)
        ledger.on_event(WorldEvent(0, "item_sold", "player",
                                    {"buyer": "smith", "item": "铁剑"}))
        self.assertEqual(ledger.account("smith").wealth, 30)
        self.assertEqual(ledger.account("smith").baseline, 30.0)

    def test_fortune_tag_mutual_exclusion_and_neutral_zone(self):
        """④双向互斥与静默带：浅损失/浅收获（<0.25）双静默返回 ""；
        W==R 双零静默——fortune 空路径行为与既有现状逐字节一致
        （token 基线 4519 的根）。"""
        ledger = WealthLedger()
        ledger.ensure_account("shallow_loss", 30)
        ledger.apply_loss("shallow_loss", 5)   # 5/30 ≈ 0.167 < 0.25
        self.assertEqual(ledger.fortune_tag("shallow_loss"), "")
        ledger.ensure_account("shallow_gain", 30)
        ledger.apply_gain("shallow_gain", 5)   # 种子：5/30 ≈ 0.167 < 0.25
        self.assertEqual(ledger.fortune_tag("shallow_gain"), "")
        ledger.ensure_account("even", 30)      # W==R：双零
        self.assertEqual(ledger.fortune_tag("even"), "")
        self.assertEqual(ledger.fortune_tag("nobody"), "")  # 无账恒空


# ============================================================================ #
# 8. 负账面边界
# ============================================================================ #

class TestNegativeWealth(unittest.TestCase):
    """负账面（账面赤字裁定）：卖出累计超 W₀ → W 为负、LOSS_TAG 持续
    （分母 max(R,1) 保护）。"""

    def test_negative_wealth_loss_tag_persists(self):
        """①chen W₀=30 连卖四把铁剑（40 两）→ W=-10 账面赤字、
        LOSS_TAG 持续；②R=0 账（W₀=0）分母退化保护：loss_ratio 以
        max(R,1)=1 计仍触发。"""
        engine = _fresh_engine()
        for _ in range(4):
            engine.player_sells("chen", "铁剑")
        acc = engine.wealth_ledger.account("chen")
        self.assertEqual(acc.wealth, -10)       # 30 - 40：赤字如实登记
        self.assertEqual(acc.baseline, 30.0)
        self.assertEqual(engine.wealth_ledger.fortune_tag("chen"), LOSS_TAG)
        # 分母保护：R=0 账不除零，ratio 以 1 为底
        ledger = WealthLedger()
        ledger.ensure_account("beggar", 0)
        self.assertEqual(ledger.fortune_tag("beggar"), "")   # W==R=0 静默
        ledger.apply_loss("beggar", 3)
        self.assertEqual(ledger.account("beggar").wealth, -3)
        self.assertEqual(ledger.fortune_tag("beggar"), LOSS_TAG)


# ============================================================================ #
# 9. 中文金额读数（cn_amount）
# ============================================================================ #

class TestCnAmount(unittest.TestCase):
    """cn_amount：1-99 中文读数契约表（十位带零/十位带五/个位）+
    超界回退阿拉伯数字（书面裁定）。"""

    def test_cn_amount_table(self):
        """①契约表：10→十、15→十五、20→二十、25→二十五、8→八、
        3→三、1→一；补充覆盖 30（十位带零）、11/19（十几）、99。"""
        cases = {10: "十", 15: "十五", 20: "二十", 25: "二十五",
                 8: "八", 3: "三", 1: "一",
                 30: "三十", 11: "十一", 19: "十九", 99: "九十九"}
        for n, expected in cases.items():
            self.assertEqual(cn_amount(n), expected, msg=f"cn_amount({n})")

    def test_cn_amount_out_of_range_fallback(self):
        """②超界回退：0/100/负数回退 str(n) 阿拉伯数字——确定性兜底
        （业务价格 ≤25 两不可达越界，详见 economics.py docstring）。"""
        self.assertEqual(cn_amount(0), "0")
        self.assertEqual(cn_amount(100), "100")
        self.assertEqual(cn_amount(-5), "-5")


# ============================================================================ #
# 10. 冷启动交互（先损失后收获判暖态）
# ============================================================================ #

class TestColdStartInteraction(unittest.TestCase):
    """冷启动不变量第三小步更新：先 apply_loss 后首笔收获不豁免
    （判暖态——已有交易史，种子豁免只属无事件史的处女账）。"""

    def test_loss_first_then_gain_is_warm(self):
        """①单元级：W₀=30 损失 1 → W=29 R=30，收获 10 → 暖态半吸收
        R=0.5·39+0.5·30=34.5（若误豁免则停在 30.0——数字可区分判据）。"""
        ledger = WealthLedger()
        ledger.ensure_account("smith", 30)
        ledger.apply_loss("smith", 1)
        self.assertFalse(ledger.account("smith").baseline
                         == float(ledger.account("smith").wealth))  # 等式已破
        ledger.apply_gain("smith", 10)
        acc = ledger.account("smith")
        self.assertEqual(acc.wealth, 39)
        self.assertEqual(acc.baseline, 34.5)    # 判暖态：半吸收生效

    def test_event_driven_loss_then_gain_warm(self):
        """②事件驱动同语义：item_sold（买家损失）后首笔 item_given
        收获同样不豁免——经 on_event 公共路径验证守卫链不误豁免。"""
        ledger = WealthLedger()
        ledger.ensure_account("smith", 30)
        ledger.on_event(WorldEvent(0, "item_sold", "player",
                                    {"buyer": "smith", "item": "草药",
                                     "price": 1}))
        ledger.on_event(WorldEvent(0, "item_given", "player",
                                    {"to": "smith", "item": "铁剑"}))
        acc = ledger.account("smith")
        self.assertEqual(acc.wealth, 39)        # 30 - 1 + 10
        self.assertEqual(acc.baseline, 34.5)    # 首笔收获不再豁免（判暖态）


# ============================================================================ #
# 11. 契约常量锁
# ============================================================================ #

class TestTradeConstantsLock(unittest.TestCase):
    """常量锁：损失显著带与禀赋溢价契约值（防漂移）。"""

    def test_trade_constants_locked(self):
        """①LOSS_TAG/LOSS_TAG_RATIO/ENDOWMENT_MULTIPLIER 契约值锁：
        损失标签 0.25 阈值与收获侧对称；溢价系数 2.5 = 损失厌恶 λ≈2.25
        的工程取整（WTA/WTP 之比）。"""
        self.assertEqual(LOSS_TAG, "最近手头紧")
        self.assertEqual(LOSS_TAG_RATIO, 0.25)
        self.assertEqual(ENDOWMENT_MULTIPLIER, 2.5)
        # 溢价链根断言：25 == int(10 * 2.5 + 0.5)（传统四舍五入）
        self.assertEqual(ENDOWMENT_MULTIPLIER * estimate_value("铁剑"), 25.0)


if __name__ == "__main__":
    unittest.main()

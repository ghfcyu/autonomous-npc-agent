"""快脑引擎对抗性测试：T3 快慢脑分流（规则匹配 + 0-token + 性格-状态双向接入）。

对抗性设计（先红后绿，断言精确到行为语义）：
1. 五意图规则齐全锁 + 正例命中 + ≥4 条反例不命中（保守匹配闸门）
2. token 基线 4 句硬保护：全部不命中走慢脑（快脑误伤基线即失败）
3. 0-token 实证：快脑命中后 MockLLMProvider.call_count 不变、
   engine.token_stats 的 calls 与 total_tokens 均不变
4. 性格-状态双向接入执法条款：同意图不同脾气掩码回复可区分、
   同掩码不同 8D 状态回复可区分（只查一方 = 两张皮，点名打回）
5. 接管率统计：混合对话后 fast_brain_stats 的 hits/total 正确
6. 向后兼容：未命中返回结构与既有字段完全一致（brain="slow"）
7. 快脑 SPEAK 回流：npc_action 事件 + TALKING 状态（与慢脑一致）
8. SLEEPING 不拦截（走慢脑梦呓路径）；确定性：同输入两次全等
9. 世界消费实证：问路回复含真实地点名、报时含 world.clock
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from engine.actions import ActionType
from engine.engine import NPCEngine
from engine.fast_brain import (FAST_REPLY_TONES, INTENT_RULES, FastBrain)
from engine.llm.mock import MockLLMProvider
from engine.npc import Persona
from engine.states import NPCState

# token 基线 4 句（scripts/token_baseline.py 的 CORE_DIALOGUES 全集）：
# 快脑误命中任何一句 → token 基线 4769 被破坏 → 本测试红。
TOKEN_BASELINE_SENTENCES = (
    "铁匠，打把剑", "有好铁矿石吗", "有新货吗", "有什么消息",
)

# 各意图正例样本（respond 命中的输入 → intent_id）
POSITIVE_SAMPLES = {
    "greet": ("你好", "晚上好", "喂"),
    "ask_direction": ("铁匠铺怎么走", "水井边在哪", "请问面包铺在哪里"),
    "ask_price": ("这个多少钱", "贵不贵", "剑要多少钱"),
    "farewell": ("再见", "我走了"),
    "ask_time": ("几点了", "什么时辰", "现在几点了"),
}


def make_chen_persona(temperament: str = "") -> Persona:
    """测试用铁匠陈人格卡（temperament 可注入；默认无掩码）。"""
    return Persona(
        id="chen", name="铁匠陈", role="blacksmith", location_id="forge",
        personality="沉稳寡言", speech_style="简短", backstory="三代铁匠",
        greeting_bank=["炉子还热着。"], fallback_bank=["嗯，你说。"],
        sleep_mumble="（鼾声）", topic_responses={"剑|刀": ["打剑？拿好铁来。"]},
        temperament=temperament,
    )


def make_chen_engine(temperament: str = "") -> NPCEngine:
    """仅 chen 的引擎（MockLLMProvider，测试场景可控）。"""
    return NPCEngine(llm=MockLLMProvider(),
                     npc_configs=[make_chen_persona(temperament)])


# ============================================================================ #
# 1. 规则表结构锁 + NPC 装配
# ============================================================================ #

class TestRuleTableLock(unittest.TestCase):
    """五意图规则齐全性 + 装配模式锁（防"悄悄删规则"回归）。"""

    def test_five_intents_in_order(self):
        """五意图齐全且顺序稳定（greet → direction → price → farewell → time）。"""
        ids = [r[0] for r in INTENT_RULES]
        self.assertEqual(ids, ["greet", "ask_direction", "ask_price",
                               "farewell", "ask_time"],
                         f"意图规则表缺失或乱序：{ids}")

    def test_every_npc_holds_fast_brain(self):
        """无脾气掩码的 NPC 也持有 FastBrain 实例（匹配与脾气无关）。"""
        engine = make_chen_engine("")  # 无掩码
        npc = engine.npcs["chen"]
        self.assertIsInstance(npc.fast_brain, FastBrain,
                              "无掩码 NPC 未持有快脑实例")
        # 无掩码 NPC 的快脑同样可用（命中 greet）
        action = npc.fast_brain.respond("你好", npc, engine.world)
        self.assertIsNotNone(action, "无掩码 NPC 快脑不可用——匹配被脾气绑架")

    def test_tone_masks_align_with_filler(self):
        """语气掩码 id 与 filler.py 同款三掩码（irritable/cheerful/aloof）。"""
        for key in ("irritable", "cheerful", "aloof"):
            self.assertIn(key, FAST_REPLY_TONES,
                          f"快脑语气表缺少掩码 {key}——与 filler 掩码口径脱钩")


# ============================================================================ #
# 2. 意图匹配单元：正例命中 + 反例不命中
# ============================================================================ #

class TestIntentMatching(unittest.TestCase):
    """五意图正例命中断言（intent_id 与 Action 结构）。"""

    def setUp(self):
        self.engine = make_chen_engine("")
        self.npc = self.engine.npcs["chen"]
        self.world = self.engine.world

    def _hit(self, text: str, expected_intent: str):
        action = self.npc.fast_brain.respond(text, self.npc, self.world)
        self.assertIsNotNone(action, f"「{text}」应命中 {expected_intent}")
        self.assertIs(action.type, ActionType.SPEAK)
        self.assertEqual(action.payload["intent"], expected_intent)
        self.assertTrue(action.payload["text"], "快脑回复文本不得为空")

    def test_greet_hits(self):
        for text in POSITIVE_SAMPLES["greet"]:
            self._hit(text, "greet")

    def test_direction_hits(self):
        for text in POSITIVE_SAMPLES["ask_direction"]:
            self._hit(text, "ask_direction")

    def test_price_hits(self):
        for text in POSITIVE_SAMPLES["ask_price"]:
            self._hit(text, "ask_price")

    def test_farewell_hits(self):
        for text in POSITIVE_SAMPLES["farewell"]:
            self._hit(text, "farewell")

    def test_time_hits(self):
        for text in POSITIVE_SAMPLES["ask_time"]:
            self._hit(text, "ask_time")


class TestConservativeMiss(unittest.TestCase):
    """反例不命中：长句/复杂句/含关键词但句式不符，一律走慢脑。"""

    def setUp(self):
        self.engine = make_chen_engine("")
        self.npc = self.engine.npcs["chen"]
        self.world = self.engine.world

    def _miss(self, text: str):
        action = self.npc.fast_brain.respond(text, self.npc, self.world)
        self.assertIsNone(action, f"「{text}」不该命中快脑——复杂对话被误伤")

    def test_long_greeting_misses(self):
        """含问候关键词但句长超限（>6 字）→ 慢脑。"""
        self._miss("你好，听说你是这条街上手艺最好的铁匠？")

    def test_extended_greeting_misses(self):
        """「你好呀」带语气扩展、整句非问候语 → 慢脑（保守回归保护，
        既有 test_background_npc 的 LLM 调用链路不得被快脑截胡）。"""
        self._miss("你好呀")

    def test_long_price_misses(self):
        """含问价关键词但句长超限（>8 字）→ 慢脑。"""
        self._miss("这把剑连同鞘和配重一共多少钱")

    def test_mid_sentence_price_misses(self):
        """问价词不在句尾（复杂议价句）→ 慢脑。"""
        self._miss("你说多少钱合适")

    def test_complex_direction_misses(self):
        """句长 ≤12 但含复杂内容（非「地点+方位问法」整句）→ 慢脑。"""
        self._miss("从铁匠铺走到酒馆要多久")

    def test_bare_location_misses(self):
        """只报地点名、无方位问法 → 慢脑。"""
        self._miss("铁匠铺")


# ============================================================================ #
# 3. token 基线 4 句硬保护
# ============================================================================ #

class TestBaselineProtection(unittest.TestCase):
    """快脑不得拦截 token 基线 4 句——拦截即破坏 4769 token 口径。"""

    def setUp(self):
        self.engine = make_chen_engine("irritable")
        self.npc = self.engine.npcs["chen"]

    def test_four_baseline_respond_none(self):
        """respond 单元口径：4 句全部返回 None（走慢脑）。"""
        for text in TOKEN_BASELINE_SENTENCES:
            self.assertIsNone(
                self.npc.fast_brain.respond(text, self.npc, self.engine.world),
                f"快脑误命中基线对话「{text}」——token 基线被破坏")

    def test_four_baseline_engine_slow(self):
        """引擎口径：4 句 brain 全为 "slow"，hits=0。"""
        for text in TOKEN_BASELINE_SENTENCES:
            result = self.engine.player_says(text, "chen")
            self.assertEqual(result["brain"], "slow",
                             f"基线对话「{text}」被快脑拦截")
        self.assertEqual(self.engine.fast_brain_stats, {"hits": 0, "total": 4})


# ============================================================================ #
# 4. 0-token 实证
# ============================================================================ #

class TestZeroToken(unittest.TestCase):
    """快脑命中：LLM 调用次数与 token_stats 全不变（0-token 实证口径）。"""

    def test_fast_hit_skips_llm_and_token_stats(self):
        """先慢脑后快脑：快脑命中后 call_count 与 token_stats 严格不变。"""
        engine = make_chen_engine("irritable")
        engine.player_says("铁匠，打把剑", "chen")  # 慢脑：产生 1 次 LLM 调用
        calls_before = engine.llm.call_count
        self.assertGreater(calls_before, 0, "前置：慢脑对话应产生 LLM 调用")
        stats_before = dict(engine.token_stats["chen"])
        self.assertGreater(stats_before["calls"], 0)

        result = engine.player_says("你好", "chen")  # 快脑命中
        self.assertEqual(result["brain"], "fast")
        # LLM 调用次数不变（快脑 0 LLM 调用）
        self.assertEqual(engine.llm.call_count, calls_before,
                         "快脑命中产生了 LLM 调用")
        # token_stats 全键不变（calls 与 total_tokens 均不增——
        # 不读 last_usage 残留值、不累计、calls 不 +1）
        self.assertEqual(engine.token_stats["chen"], stats_before,
                         "快脑命中污染了 token_stats（读到慢脑残留 usage）")

    def test_fast_only_npc_absent_from_token_stats(self):
        """纯快脑对话的 NPC 不进 token_stats（0-token 不聚合）。"""
        engine = make_chen_engine("")
        result = engine.player_says("你好", "chen")
        self.assertEqual(result["brain"], "fast")
        self.assertNotIn("chen", engine.token_stats,
                          "纯快脑对话不应产生任何 token 聚合")
        status = engine.status()["token_stats"]
        self.assertEqual(status["llm_calls"], 0)
        self.assertEqual(status["total_tokens"], 0)


# ============================================================================ #
# 5. 性格-状态双向接入（执法条款：只查一方即两张皮）
# ============================================================================ #

class TestPersonaStateFork(unittest.TestCase):
    """玩家可感知分叉：脾气掩码 × 8D 当下状态同时消费。"""

    def setUp(self):
        self.engine = make_chen_engine("irritable")
        self.npc = self.engine.npcs["chen"]
        self.world = self.engine.world

    def _respond_text(self, text: str) -> str:
        action = self.npc.fast_brain.respond(text, self.npc, self.world)
        self.assertIsNotNone(action)
        return action.payload["text"]

    def test_masks_distinguishable(self):
        """同意图不同脾气掩码（irritable/cheerful/无掩码）回复可区分。"""
        replies = {}
        for temperament in ("irritable", "cheerful", ""):
            self.npc.persona.temperament = temperament
            replies[temperament] = self._respond_text("几点了")
        self.assertNotEqual(replies["irritable"], replies["cheerful"],
                             "不同脾气掩码回复不可区分——掩码没接入")
        self.assertNotEqual(replies["irritable"], replies[""],
                             "有/无掩码回复不可区分")
        self.assertNotEqual(replies["cheerful"], replies[""],
                             "cheerful 与中性变体不可区分")

    def test_state_bands_distinguishable(self):
        """同掩码不同 8D 状态（高压 vs 默认）回复可区分。"""
        self.npc.inner_state.S_stress = 0.2  # 默认带
        default_reply = self._respond_text("几点了")
        self.npc.inner_state.S_stress = 0.7  # 高压带（>0.6，与 filler 同阈值）
        stressed_reply = self._respond_text("几点了")
        self.assertNotEqual(stressed_reply, default_reply,
                            "同掩码不同 8D 状态回复不可区分——状态没接入")
        self.assertIn("（皱眉）", stressed_reply, "irritable 高压带应更冲")

    def test_no_mask_neutral_variant(self):
        """无掩码（空串）用中性变体：不带语气前缀（向后兼容）。"""
        self.npc.persona.temperament = ""
        reply = self._respond_text("几点了")
        self.assertFalse(reply.startswith("（"), "中性变体不该带语气前缀")
        self.assertTrue(reply, "中性变体回复不得为空")

    def test_reply_length_lock(self):
        """≤40 字符锁：全掩码 × 全状态带 × 全意图的回复一律不超长。"""
        samples = {"greet": "你好", "ask_direction": "铁匠铺怎么走",
                   "ask_price": "这个多少钱", "farewell": "再见",
                   "ask_time": "几点了"}
        band_presets = ({"S_stress": 0.7}, {"e_A": 0.8}, {"e_P": 0.3},
                        {"e_D": 0.8}, {"p_fatigue": 0.8}, {})
        for temperament in ("irritable", "cheerful", "aloof", ""):
            self.npc.persona.temperament = temperament
            for band in band_presets:
                for key in ("S_stress", "e_A", "e_P", "e_D", "p_fatigue"):
                    setattr(self.npc.inner_state, key, 0.2)
                for key, value in band.items():
                    setattr(self.npc.inner_state, key, value)
                for text in samples.values():
                    reply = self._respond_text(text)
                    self.assertLessEqual(
                        len(reply), 40,
                        f"快脑回复「{reply}」超 40 字符（{len(reply)}）")


# ============================================================================ #
# 6. 引擎集成：brain 字段 / SPEAK 回流 / 接管率统计 / 向后兼容
# ============================================================================ #

class TestEngineIntegration(unittest.TestCase):
    """player_says 分叉契约：brain 字段、回流事件、接管统计、结构兼容。"""

    def setUp(self):
        self.engine = make_chen_engine("irritable")

    def test_brain_field_fast_and_slow(self):
        """返回结构 brain 字段：快脑命中 "fast"，未命中 "slow"。"""
        fast = self.engine.player_says("你好", "chen")
        self.assertEqual(fast["brain"], "fast")
        slow = self.engine.player_says("铁匠，打把剑", "chen")
        self.assertEqual(slow["brain"], "slow")

    def test_fast_speak_reflow(self):
        """快脑 SPEAK 回流：bus 上有 npc_action 事件 + NPC 进入 TALKING
        （与慢脑 SPEAK 行为一致）。"""
        bus_before = len(self.engine.world.bus.history)
        result = self.engine.player_says("你好", "chen")
        npc = self.engine.npcs["chen"]
        self.assertEqual(result["brain"], "fast")
        self.assertIs(npc.state_machine.state, NPCState.TALKING,
                      "快脑 SPEAK 后 NPC 应进入交谈状态")
        npc_actions = [e for e in list(self.engine.world.bus.history)[bus_before:]
                       if e.kind == "npc_action"
                       and e.payload.get("npc") == "chen"]
        self.assertGreaterEqual(len(npc_actions), 1,
                                "快脑 SPEAK 未回流 npc_action 事件")

    def test_fast_action_dict_contract(self):
        """快脑命中的 action 字典：type=speak 且带 intent 字段。"""
        result = self.engine.player_says("再见", "chen")
        self.assertEqual(result["brain"], "fast")
        self.assertEqual(result["action"]["action"], "speak")
        self.assertEqual(result["action"]["intent"], "farewell")
        self.assertTrue(result["action"]["text"])

    def test_stats_mixed_hits_total(self):
        """接管率统计：混合对话（2 快 2 慢）后 hits/total 正确。"""
        self.engine.player_says("你好", "chen")           # fast
        self.engine.player_says("铁匠，打把剑", "chen")   # slow
        self.engine.player_says("再见", "chen")           # fast
        self.engine.player_says("有什么消息", "chen")     # slow
        self.assertEqual(self.engine.fast_brain_stats,
                         {"hits": 2, "total": 4})
        # status() 同步暴露
        self.assertEqual(self.engine.status()["fast_brain"],
                         {"hits": 2, "total": 4})

    def test_unknown_npc_does_not_count_total(self):
        """未知 NPC 的调用不计入接管率 total（错误返回，非有效对话）。"""
        self.engine.player_says("你好", "nobody")
        self.assertEqual(self.engine.fast_brain_stats, {"hits": 0, "total": 0})

    def test_slow_result_structure_unchanged(self):
        """向后兼容：未命中时既有字段全保留（brain="slow" 为纯新增）。"""
        result = self.engine.player_says("铁匠，打把剑", "chen")
        for key in ("ok", "npc", "npc_id", "action", "reply",
                    "filler", "state", "clock"):
            self.assertIn(key, result, f"慢脑返回结构丢失既有字段 {key}")
        self.assertEqual(result["brain"], "slow")
        self.assertTrue(result["ok"])
        self.assertIn("剑", result["reply"])  # Mock 话题库命中不变


# ============================================================================ #
# 7. SLEEPING 守卫（快脑不拦截，交慢脑既有路径）
# ============================================================================ #

class TestSleepingGuard(unittest.TestCase):
    """SLEEPING 的 NPC 快脑不放行：走慢脑梦呓路径（既有行为零变化）。"""

    def setUp(self):
        self.engine = make_chen_engine("irritable")
        self.npc = self.engine.npcs["chen"]

    def test_respond_none_when_sleeping(self):
        """respond 单元口径：睡觉 NPC 快脑匹配直接返回 None。"""
        self.npc.state_machine.force(NPCState.SLEEPING)
        self.assertIsNone(
            self.npc.fast_brain.respond("你好", self.npc, self.engine.world),
            "睡觉 NPC 被快脑拦截——应交慢脑 REFUSE/梦呓路径")

    def test_engine_sleeping_goes_slow(self):
        """引擎口径：睡觉 NPC 说「你好」走慢脑，回梦呓、不进 TALKING、
        hits 不增。"""
        self.npc.state_machine.force(NPCState.SLEEPING)
        result = self.engine.player_says("你好", "chen")
        self.assertEqual(result["brain"], "slow",
                         "睡觉 NPC 被快脑拦截——REFUSE 语义被破坏")
        self.assertIn("鼾声", result["reply"])  # 慢脑梦呓回退不变
        self.assertIs(self.npc.state_machine.state, NPCState.SLEEPING)
        self.assertEqual(self.engine.fast_brain_stats, {"hits": 0, "total": 1})


# ============================================================================ #
# 8. 确定性 + 世界消费实证
# ============================================================================ #

class TestDeterminismAndWorldConsumption(unittest.TestCase):
    """同输入两次全等；问路消费真实地点、报时消费 world.clock。"""

    def setUp(self):
        self.engine = make_chen_engine("irritable")
        self.npc = self.engine.npcs["chen"]
        self.world = self.engine.world

    def test_same_input_same_result(self):
        """确定性：同输入两次 respond 的 Action 全等。"""
        first = self.npc.fast_brain.respond("铁匠铺怎么走", self.npc, self.world)
        second = self.npc.fast_brain.respond("铁匠铺怎么走", self.npc, self.world)
        self.assertEqual(first.to_dict(), second.to_dict(),
                         "同输入两次快脑结果不一致")

    def test_direction_reply_uses_real_location(self):
        """问路回复消费真实世界地点：含 world.locations 真实地名 + 方位词。"""
        action = self.npc.fast_brain.respond("铁匠铺怎么走", self.npc, self.world)
        reply = action.payload["text"]
        self.assertIn("铁匠铺", reply, "问路回复未消费真实地点名")
        self.assertRegex(reply, r"在[东西南北]{1,2}边",
                         "问路回复未给出真实方位")
        # 玩家在中央广场(230,220)，铁匠铺(120,80) → 西北
        self.assertIn("西北边", reply)

    def test_time_reply_uses_world_clock(self):
        """报时回复消费 world.clock 真实时刻。"""
        action = self.npc.fast_brain.respond("几点了", self.npc, self.world)
        self.assertIn(self.world.clock, action.payload["text"],
                      "报时回复未消费 world.clock")

    def test_price_reply_uses_persona_role(self):
        """查价回复按职业给确定性报价（blacksmith 走铁匠报价句）。"""
        action = self.npc.fast_brain.respond("这个多少钱", self.npc, self.world)
        self.assertIn("铁剑", action.payload["text"],
                      "查价回复未按 blacksmith 职业报价")


if __name__ == "__main__":
    unittest.main()

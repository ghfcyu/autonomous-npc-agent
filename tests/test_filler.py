"""垫话引擎对抗性测试：脾气掩码 + 8D 当下状态 → 同步垫话。

对抗性设计（先红后绿，断言精确到字符串内容）：
1. 三掩码齐全性锁：缺任一掩码即失败（防"悄悄删掩码"回归）
2. 同一 NPC 不同 8D 状态必须产出不同垫话（防"性格与状态两张皮"）
3. 规则优先级锁：高压 + 高唤醒同时命中时，排前的 S_stress 规则胜出
4. ≤15 token 锁：任何模板超长即失败（token 硬约束）
5. player_says 集成：filler 消费的是「听到玩家说话」后的反应性状态
   （S_stress=0.6 预置 → 事件 +0.05 → 0.65 > 0.6 → 高压垫话，
    证明垫话读到的是事件后状态而非事件前状态）
6. filler 0-token 实证：有/无 temperament 的两个引擎 total_tokens 相等
   （filler 不进 LLM prompt）
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from engine.engine import NPCEngine
from engine.filler import FillerEngine, TEMPERAMENT_MASKS
from engine.inner_state import InnerState
from engine.llm.mock import MockLLMProvider
from engine.npc import Persona


def make_chen_persona(temperament: str = "irritable") -> Persona:
    """测试用铁匠陈人格卡（temperament 可注入）。"""
    return Persona(
        id="chen", name="铁匠陈", role="blacksmith", location_id="forge",
        personality="沉稳寡言", speech_style="简短", backstory="三代铁匠",
        greeting_bank=["炉子还热着。"], fallback_bank=["嗯，你说。"],
        sleep_mumble="（鼾声）", topic_responses={"剑|刀": ["打剑？拿好铁来。"]},
        temperament=temperament,
    )


def make_chen_engine(temperament: str = "irritable") -> NPCEngine:
    """仅 chen 的引擎（MockLLMProvider，测试场景可控）。"""
    return NPCEngine(llm=MockLLMProvider(),
                     npc_configs=[make_chen_persona(temperament)])


# 无赞美/批评关键词的输入（避开 PRAISE/CRITICISM 关键字，保证状态增量可精确推算）。
# 原为「在吗」——2026-10-10 22:00 PM 裁定 presence（确认在场）意图入
# 快脑规则表后，「在吗」整句全等命中快脑秒回（0 LLM 调用、token_stats
# 不聚合），本组测试原意是「filler 随慢脑路径生成/垫话不进 prompt」而非
# 锁「在吗」走慢脑，故改为不命中快脑六意图的中性慢脑句（已核：不匹配
# 问候/告别/presence 短语、不以问价尾巴结尾、不匹配时间/问路问法，
# 且不含赞美/批评关键词——沿用 test_background_npc.py 同款「改测试
# 样例而非迁就快脑」裁定口径）。
NEUTRAL_TEXT = "最近生意怎么样？"


# ============================================================================ #
# 1. 掩码表齐全性与模板硬约束
# ============================================================================ #

class TestMaskTable(unittest.TestCase):
    """掩码表结构锁：三掩码齐全 + 每条模板 ≤15 字符。"""

    def test_three_masks_present(self):
        """三掩码齐全性锁：irritable/cheerful/aloof 必须同时存在。"""
        for key in ("irritable", "cheerful", "aloof"):
            self.assertIn(key, TEMPERAMENT_MASKS,
                          f"脾气掩码表缺少 {key}——掩码被误删")

    def test_template_length_lock(self):
        """≤15 token 锁：遍历所有掩码的所有模板，逐一断言长度。"""
        for mask_id, rules in TEMPERAMENT_MASKS.items():
            for rule in rules:
                template = rule[1]
                self.assertLessEqual(
                    len(template), 15,
                    f"掩码 {mask_id} 模板「{template}」超 15 字符（{len(template)}）")

    def test_every_mask_has_default_rule(self):
        """每个掩码末条规则必须是 default True（保证 generate 永不落空）。"""
        for mask_id, rules in TEMPERAMENT_MASKS.items():
            default_test = rules[-1][0]
            self.assertTrue(default_test(InnerState()),
                            f"掩码 {mask_id} 缺 default True 规则，存在落空风险")


# ============================================================================ #
# 2. 单掩码状态分叉（同 NPC 不同状态可区分）
# ============================================================================ #

class TestIrritableBranching(unittest.TestCase):
    """暴躁掩码：S_stress 高压 / e_A 高唤醒 / 默认 三带分叉。"""

    def setUp(self):
        self.engine = FillerEngine("irritable")

    def test_high_stress(self):
        """S_stress=0.7 → （皱眉）找老夫何事？"""
        state = InnerState(S_stress=0.7)
        self.assertEqual(self.engine.generate(state), "（皱眉）找老夫何事？")

    def test_high_arousal(self):
        """S_stress=0.2, e_A=0.8 → （抡锤）说！"""
        state = InnerState(S_stress=0.2, e_A=0.8)
        self.assertEqual(self.engine.generate(state), "（抡锤）说！")

    def test_default_band(self):
        """S_stress=0.2, e_A=0.3 → （抬眼）何事？"""
        state = InnerState(S_stress=0.2, e_A=0.3)
        self.assertEqual(self.engine.generate(state), "（抬眼）何事？")

    def test_state_forks_distinguishable(self):
        """状态分叉锁：高压态与默认态垫话字符串必须不同。"""
        high = self.engine.generate(InnerState(S_stress=0.7))
        low = self.engine.generate(InnerState(S_stress=0.2, e_A=0.3))
        self.assertNotEqual(high, low, "同 NPC 不同状态垫话不可区分——状态没接入")

    def test_priority_stress_before_arousal(self):
        """优先级锁：S_stress=0.7 且 e_A=0.8 同时命中时，排前的压力规则胜出。"""
        state = InnerState(S_stress=0.7, e_A=0.8)
        self.assertEqual(self.engine.generate(state), "（皱眉）找老夫何事？")


class TestCheerfulBranching(unittest.TestCase):
    """喜悦掩码：e_P 高愉悦 / 低愉悦 / 默认 三带分叉。"""

    def setUp(self):
        self.engine = FillerEngine("cheerful")

    def test_high_pleasure(self):
        self.assertEqual(self.engine.generate(InnerState(e_P=0.8)),
                         "（笑盈盈）客官来啦！")

    def test_low_pleasure(self):
        self.assertEqual(self.engine.generate(InnerState(e_P=0.3)),
                         "（勉强笑）有何吩咐？")

    def test_mid_pleasure_default(self):
        self.assertEqual(self.engine.generate(InnerState(e_P=0.5)),
                         "（热情）里边请！")


class TestAloofBranching(unittest.TestCase):
    """沉稳/冷淡掩码：e_D 高支配 / p_fatigue 疲惫 / 默认 三带分叉。"""

    def setUp(self):
        self.engine = FillerEngine("aloof")

    def test_high_dominance(self):
        self.assertEqual(self.engine.generate(InnerState(e_D=0.8)),
                         "（淡淡一瞥）说吧。")

    def test_fatigue_band(self):
        """p_fatigue=0.8, e_D=0.5（不触发高支配）→ （揉眉）……有事？"""
        state = InnerState(p_fatigue=0.8, e_D=0.5)
        self.assertEqual(self.engine.generate(state), "（揉眉）……有事？")

    def test_default_band(self):
        self.assertEqual(self.engine.generate(InnerState(e_D=0.5, p_fatigue=0.2)),
                         "（平静）何事？")


# ============================================================================ #
# 3. 跨 NPC 可区分 + 空掩码向后兼容
# ============================================================================ #

class TestCrossNpcAndFallback(unittest.TestCase):
    """不同 NPC 默认态可区分；空/未知掩码返回空串。"""

    def test_masks_distinguish_npcs(self):
        """irritable 默认态 vs cheerful 默认态，字符串不同。"""
        irritable = FillerEngine("irritable").generate(InnerState())
        cheerful = FillerEngine("cheerful").generate(InnerState())
        self.assertNotEqual(irritable, cheerful, "不同脾气掩码默认垫话不可区分")

    def test_empty_temperament_returns_empty(self):
        """空掩码 → 空串（向后兼容，不影响既有 NPC）。"""
        self.assertEqual(FillerEngine("").generate(InnerState()), "")

    def test_unknown_temperament_returns_empty(self):
        """未注册掩码 → 空串。"""
        self.assertEqual(FillerEngine("unknown").generate(InnerState()), "")


# ============================================================================ #
# 4. player_says 集成
# ============================================================================ #

class TestPlayerSaysIntegration(unittest.TestCase):
    """filler 键进入 player_says 返回契约，且消费事件后的反应性状态。"""

    def test_filler_key_in_result(self):
        """默认状态：事件后 S_stress=0.25(≤0.6)、e_A=0.35(≤0.7) → 默认垫话。"""
        engine = make_chen_engine("irritable")
        result = engine.player_says(NEUTRAL_TEXT, "chen")
        self.assertTrue(result["ok"])
        self.assertIn("filler", result)
        self.assertIsInstance(result["filler"], str)
        self.assertGreater(len(result["filler"]), 0)
        self.assertEqual(result["filler"], "（抬眼）何事？")

    def test_filler_consumes_post_event_state(self):
        """反应性状态锁：预置 S_stress=0.6 → 事件 +0.05 → 0.65 > 0.6
        → 高压垫话（证明读的是事件后状态，非事件前）。"""
        engine = make_chen_engine("irritable")
        engine.npcs["chen"].inner_state.S_stress = 0.6
        result = engine.player_says(NEUTRAL_TEXT, "chen")
        self.assertEqual(result["filler"], "（皱眉）找老夫何事？")

    def test_no_temperament_filler_empty(self):
        """无 temperament 的 NPC（向后兼容）→ filler 为空串。"""
        engine = make_chen_engine("")
        result = engine.player_says(NEUTRAL_TEXT, "chen")
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("filler"), "")


# ============================================================================ #
# 5. filler 0-token 实证（不进 LLM prompt）
# ============================================================================ #

class TestFillerZeroToken(unittest.TestCase):
    """有/无脾气掩码的两个引擎 token 消耗完全一致——filler 不进 prompt。"""

    def test_token_unchanged_with_filler(self):
        """两次对比：temperament=irritable 与 temperament="" 的引擎，
        同一输入下 token_stats 的 total_tokens 严格相等。"""
        engine_with = make_chen_engine("irritable")
        engine_without = make_chen_engine("")
        engine_with.player_says(NEUTRAL_TEXT, "chen")
        engine_without.player_says(NEUTRAL_TEXT, "chen")

        stats_with = engine_with.token_stats["chen"]
        stats_without = engine_without.token_stats["chen"]
        self.assertEqual(stats_with["prompt_tokens"], stats_without["prompt_tokens"],
                         "filler 改变了 prompt token——垫话被误注入 prompt")
        self.assertEqual(stats_with["total_tokens"], stats_without["total_tokens"],
                         "filler 改变了 total_tokens——垫话被误注入 prompt")
        # 垫话侧证：有掩码引擎产出了非空 filler，无掩码引擎为空
        self.assertGreater(len(engine_with.npcs["chen"].filler_engine.temperament_id), 0)


if __name__ == "__main__":
    unittest.main()

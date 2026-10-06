"""对话质量首批 2 指标测试：记忆引用率 + 状态影响可见性。

覆盖四个维度（对应任务书验收）：
1. 口径正确性：构造已知输入（自定义单元素 bank Persona / distinguish
   纯函数 / 提取函数）→ 期望分子/分母/分数；
2. 可复现性：run_baseline() 连续两次返回的基线分完全一致；
3. 8D 消费：状态分叉对的预置键必须是 InnerState.PARAMS 的 8D 字段，
   硬约束对断言 reason 字符串 "stress_too_high" / "fatigue_too_high"
   （即 S_stress / p_fatigue 语义），旧 5D 字段直接抛 ValueError；
4. 玩家可感知：所有端到端断言落在玩家可见的返回值上——
   result["action"]（refuse/speak 分叉）、result["reply"]（文本变化）、
   result["filler"]（垫话分叉），不写与玩家输出无关的空测试。
"""

import unittest

from engine.engine import NPCEngine
from engine.inner_state import InnerState
from engine.npc import Persona
from scripts.dialogue_quality import (
    MEMORY_SCENARIOS,
    STATE_PAIRS,
    ProbeLLM,
    SideRecord,
    _run_state_side,
    distinguish,
    extract_block,
    extract_inner_line,
    hits_terms,
    run_baseline,
    run_memory_scenarios,
    run_state_scenarios,
    score,
)

# ------------------------------------------------------------------ #
# 测试辅助
# ------------------------------------------------------------------ #

SAMPLE_PROMPT = (
    "【相关长期记忆】\n- 收到来自 player 的物品：铁矿石\n\n"
    "【最近的经历】\n- 玩家说：上次送你的矿石，成色怎么样？\n\n"
    "【当前世界】时间 08:00，天气 晴，你在 铁匠铺\n"
    "【此刻内心】疲劳: 0.25，压力: 0.83\n"
    "【玩家说】上次送你的矿石，成色怎么样？"
)


def known_persona(topic_reply: str) -> Persona:
    """已知输入用 Persona：单元素 bank，保证 Mock 输出完全确定。"""
    return Persona(
        id="t1", name="测试匠", role="blacksmith", location_id="forge",
        personality="沉稳", speech_style="简短", backstory="测试",
        greeting_bank=["欢迎。"], fallback_bank=["嗯。"],
        topic_responses={"矿": [topic_reply]},
    )


KNOWN_SCENARIO = {
    "npc": "t1", "item": "铁矿石", "ask": "矿石怎么样？",
    "core": ["铁矿石", "矿石", "铁矿"], "label": "known",
}


# ------------------------------------------------------------------ #
# 1. 口径纯函数
# ------------------------------------------------------------------ #


class TestCalibrationHelpers(unittest.TestCase):
    """核心词命中 / 块提取 / 分叉判定的口径单元断言。"""

    CORE = ["铁矿石", "矿石", "铁矿"]

    def test_hits_terms_positive(self):
        self.assertTrue(hits_terms("收到来自 player 的物品：铁矿石", self.CORE))
        self.assertTrue(hits_terms("有好铁矿就拿来", self.CORE))

    def test_hits_terms_negative(self):
        self.assertFalse(hits_terms("（无）", self.CORE))
        self.assertFalse(hits_terms("嗯，你说。", self.CORE))
        # 空文本 / 空词表恒 False
        self.assertFalse(hits_terms("", self.CORE))
        self.assertFalse(hits_terms("铁矿石", []))

    def test_extract_memory_block(self):
        block = extract_block(SAMPLE_PROMPT, "【相关长期记忆】")
        self.assertEqual(block, "- 收到来自 player 的物品：铁矿石")
        self.assertEqual(extract_block(SAMPLE_PROMPT, "【不存在】"), "")
        self.assertEqual(extract_block("", "【相关长期记忆】"), "")

    def test_extract_inner_line(self):
        self.assertEqual(extract_inner_line(SAMPLE_PROMPT),
                         "疲劳: 0.25，压力: 0.83")
        # 未调用 LLM（硬约束拒绝）→ prompt 为 None → None
        self.assertIsNone(extract_inner_line(None))

    def test_score(self):
        self.assertEqual(score(3, 6), 0.5)
        self.assertEqual(score(0, 6), 0.0)
        self.assertEqual(score(1, 3), round(1 / 3, 4))
        # 分母为 0 记 0.0，不抛除零
        self.assertEqual(score(0, 0), 0.0)


class TestDistinguish(unittest.TestCase):
    """状态分叉判定：C1 动作 / C2 垫话 / C3 内心注入三通道口径。"""

    def test_action_fork_c1(self):
        """硬约束分叉：REFUSE(stress_too_high) vs SPEAK → 玩家可感知。"""
        a = SideRecord(action={"action": "refuse", "reason": "stress_too_high"},
                       filler="（抬眼）何事？", inner=None)
        b = SideRecord(action={"action": "speak", "text": "嗯，你说。"},
                       filler="（抬眼）何事？", inner=None)
        visible, ch = distinguish(a, b)
        self.assertTrue(visible)
        self.assertTrue(ch["C1_action"])
        self.assertFalse(ch["C2_filler"])
        self.assertIsNone(ch["C3_inner"])  # REFUSE 侧无 LLM 调用 → N/A

    def test_filler_fork_c2(self):
        a = SideRecord(action={"action": "speak", "text": "嗯"}, filler="（笑盈盈）客官来啦！", inner="愉悦: 0.50")
        b = SideRecord(action={"action": "speak", "text": "嗯"}, filler="（勉强笑）有何吩咐？", inner="愉悦: 0.50")
        visible, ch = distinguish(a, b)
        self.assertTrue(visible)
        self.assertFalse(ch["C1_action"])
        self.assertTrue(ch["C2_filler"])
        self.assertFalse(ch["C3_inner"])

    def test_inner_fork_c3_only(self):
        """仅【此刻内心】注入分叉（Mock 回复不变的带内场景）→ 仍计可区分。"""
        a = SideRecord(action={"action": "speak", "text": "嗯"}, filler="x", inner="压力: 0.25")
        b = SideRecord(action={"action": "speak", "text": "嗯"}, filler="x", inner="压力: 0.55")
        visible, ch = distinguish(a, b)
        self.assertTrue(visible)
        self.assertFalse(ch["C1_action"])
        self.assertFalse(ch["C2_filler"])
        self.assertTrue(ch["C3_inner"])

    def test_identical_sides_not_distinguishable(self):
        """同状态同输入两侧完全一致 → 不可区分（口径不是恒 1）。"""
        a = SideRecord(action={"action": "speak", "text": "嗯"}, filler="x", inner="压力: 0.25")
        visible, ch = distinguish(a, a)
        self.assertFalse(visible)
        self.assertFalse(any(v is True for v in ch.values()))


# ------------------------------------------------------------------ #
# 2. 指标① 口径：已知输入 → 期望分数
# ------------------------------------------------------------------ #


class TestMemoryReferenceCalibration(unittest.TestCase):
    """记忆引用率：构造已知输入，断言分子/分母/细分子分。"""

    def test_known_input_full_hit(self):
        """话题回复含实体词 → J1+J2 双命中 → 1/1，J2 子分 1/1。"""
        persona = known_persona("你送的那块矿石，我记得。")
        result = run_memory_scenarios(scenarios=[KNOWN_SCENARIO],
                                      personas=[persona])
        self.assertEqual(result.denominator, 1)
        self.assertEqual(result.numerator, 1)
        self.assertEqual(result.score, 1.0)
        self.assertEqual(result.sub["J1_注入层"], "1/1")
        self.assertEqual(result.sub["J2_回复层"], "1/1")
        detail = result.details[0]
        self.assertTrue(detail["valid"])
        self.assertTrue(detail["inject_hit"])
        self.assertTrue(detail["reply_hit"])

    def test_known_input_inject_only(self):
        """回复不含实体词 → 仅 J1 命中（OR 口径主分仍 1/1），J2 子分 0/1。"""
        persona = known_persona("好料我认得。")  # 无核心词
        result = run_memory_scenarios(scenarios=[KNOWN_SCENARIO],
                                      personas=[persona])
        self.assertEqual(result.denominator, 1)
        self.assertEqual(result.numerator, 1)   # J1 命中即计分子
        self.assertEqual(result.sub["J1_注入层"], "1/1")
        self.assertEqual(result.sub["J2_回复层"], "0/1")
        self.assertTrue(result.details[0]["inject_hit"])
        self.assertFalse(result.details[0]["reply_hit"])

    def test_memory_block_is_long_term_channel(self):
        """J1 判定对象是【相关长期记忆】块：块中必须真实出现送礼记忆。"""
        persona = known_persona("你送的那块矿石，我记得。")
        result = run_memory_scenarios(scenarios=[KNOWN_SCENARIO],
                                      personas=[persona])
        self.assertIn("收到来自 player 的物品：铁矿石",
                      result.details[0]["memory_block"])


# ------------------------------------------------------------------ #
# 3. 指标② 口径 + 8D 消费
# ------------------------------------------------------------------ #


class TestStateVisibility8D(unittest.TestCase):
    """状态可见性：8D 字段纪律、硬约束 reason 语义、玩家可感知分叉。"""

    def test_state_pairs_use_8d_fields_only(self):
        """所有分叉对的预置键必须是 8D 字段（防退回旧 5D）。"""
        self.assertEqual(len(InnerState.PARAMS), 8)
        self.assertIn("S_stress", InnerState.PARAMS)
        self.assertIn("p_fatigue", InnerState.PARAMS)
        for pair in STATE_PAIRS:
            for side in ("side_a", "side_b"):
                self.assertTrue(set(pair[side]) <= set(InnerState.PARAMS),
                                msg=f"{pair['label']}.{side} 含非 8D 字段")

    def test_old_5d_field_rejected(self):
        """向 _run_state_side 传旧 5D 字段（如 mood）→ ValueError。"""
        personas = Persona.load_all()
        with self.assertRaises(ValueError):
            _run_state_side(personas, "chen", "你好", {"mood": 0.9})

    def test_hard_constraint_reason_semantics(self):
        """硬约束对的高压侧 reason 必须是 S_stress/p_fatigue 语义字符串。"""
        result = run_state_scenarios()
        by_label = {d["label"]: d for d in result.details}
        for label, expected in [
            ("chen-S_stress硬约束", "stress_too_high"),
            ("lily-S_stress硬约束", "stress_too_high"),
            ("chen-p_fatigue硬约束", "fatigue_too_high"),
            ("lily-p_fatigue硬约束", "fatigue_too_high"),
        ]:
            detail = by_label[label]
            self.assertEqual(detail["action_b"].get("reason"), expected,
                             msg=f"{label} reason 不符")
            self.assertEqual(detail["reason_b"], expected)
            self.assertTrue(detail["reason_match"])
            # 低压侧是正常说话，不拒绝
            self.assertEqual(detail["action_a"].get("action"), "speak")

    def test_hard_constraint_action_fork_player_visible(self):
        """端到端：S_stress 0.2 vs 0.85 → refuse/speak 分叉且玩家可见输出全变。"""
        personas = Persona.load_all()
        low = _run_state_side(personas, "chen", "最近生意怎么样？", {"S_stress": 0.2})
        high = _run_state_side(personas, "chen", "最近生意怎么样？", {"S_stress": 0.85})
        # 玩家可感知 1：动作类型分叉（speak vs refuse）
        self.assertEqual(low.action["action"], "speak")
        self.assertEqual(high.action["action"], "refuse")
        self.assertEqual(high.action["reason"], "stress_too_high")
        # 玩家可感知 2：垫话分叉（irritable 掩码 S_stress>0.6 高压带）
        self.assertNotEqual(low.filler, high.filler)
        self.assertIn("皱眉", high.filler)
        # 玩家可感知 3：高压侧连 LLM 都未调用（【此刻内心】无注入 → None）
        self.assertIsNone(high.inner)
        self.assertIsNotNone(low.inner)

    def test_refuse_reply_text_player_visible(self):
        """端到端：拒绝对玩家呈现的 reply 文本与正常对话不同。"""
        def _run(preset):
            probe = ProbeLLM()
            engine = NPCEngine(llm=probe, npc_configs=Persona.load_all())
            npc = engine.npcs["chen"]
            npc.inner_state.p_fatigue = preset["p_fatigue"]
            return engine.player_says("最近生意怎么样？", "chen")

        fresh = _run({"p_fatigue": 0.2})
        tired = _run({"p_fatigue": 0.9})
        self.assertEqual(fresh["action"]["action"], "speak")
        self.assertEqual(tired["action"]["action"], "refuse")
        self.assertIn("没有回应", tired["reply"])
        self.assertNotEqual(fresh["reply"], tired["reply"])

    def test_inner_prompt_line_changes_with_state(self):
        """【此刻内心】注入文本随 8D 预置状态变化（真实 LLM 传导通道）。"""
        personas = Persona.load_all()
        a = _run_state_side(personas, "chen", "最近生意怎么样？", {"S_stress": 0.2})
        b = _run_state_side(personas, "chen", "最近生意怎么样？", {"S_stress": 0.5})
        # 事件后 +0.05 → 0.25 / 0.55，均不触硬约束，两侧都调用了 LLM
        self.assertIsNotNone(a.inner)
        self.assertIsNotNone(b.inner)
        self.assertIn("压力: 0.25", a.inner)
        self.assertIn("压力: 0.55", b.inner)
        self.assertNotEqual(a.inner, b.inner)


# ------------------------------------------------------------------ #
# 4. Mock 基线分：实测值 + 可复现性
# ------------------------------------------------------------------ #


class TestBaseline(unittest.TestCase):
    """基线分断言：分数是多少就报多少（实测值），且连续两次完全一致。"""

    @classmethod
    def setUpClass(cls):
        cls.first = run_baseline()
        cls.second = run_baseline()

    def test_baseline_reproducible(self):
        """连续两次 run_baseline() 结果完全一致（确定性可复现）。"""
        self.assertEqual(self.first, self.second)
        self.assertEqual(self.first["baseline"], self.second["baseline"])

    def test_memory_reference_baseline_values(self):
        mem = self.first["memory_reference_rate"]
        self.assertEqual(mem["denominator"], len(MEMORY_SCENARIOS))   # 6 链
        self.assertEqual(mem["numerator"], 6)                        # J1 全命中
        self.assertEqual(mem["score"], 1.0)
        self.assertEqual(mem["sub"]["J1_注入层"], "6/6")
        self.assertEqual(mem["sub"]["J2_回复层"], "1/6")             # Mock 表面引用短板

    def test_state_visibility_baseline_values(self):
        st = self.first["state_visibility_rate"]
        self.assertEqual(st["denominator"], len(STATE_PAIRS))        # 6 对
        self.assertEqual(st["numerator"], 6)
        self.assertEqual(st["score"], 1.0)
        self.assertEqual(st["sub"]["C1_动作分叉"], "4/6")
        self.assertEqual(st["sub"]["C2_垫话分叉"], "2/6")
        self.assertEqual(st["sub"]["C3_内心注入分叉"], "2/2（其余 4 对 N/A）")

    def test_no_memory_control_proves_repetition(self):
        """对照：不送礼 reply 层仍命中 → Mock 表面引用与记忆无关。"""
        ctrl = self.first["controls"]["no_memory"]
        self.assertFalse(ctrl["inject_hit"])   # 无记忆 → 注入块为（无）
        self.assertTrue(ctrl["reply_hit"])     # 话术库仍带出"铁矿"

    def test_determinism_control_not_distinguishable(self):
        """对照：同状态同输入两次 → 全通道一致。"""
        ctrl = self.first["controls"]["state_determinism"]
        self.assertFalse(ctrl["distinguishable"])

    def test_total_scenarios(self):
        self.assertEqual(self.first["total_scenarios"],
                         len(MEMORY_SCENARIOS) + len(STATE_PAIRS))   # 12


if __name__ == "__main__":
    unittest.main()

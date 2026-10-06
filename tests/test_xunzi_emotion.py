"""T1 对抗性测试：荀子六情映射表（XUNZI_EMOTION_VECTORS）。

先红后绿设计：
- 红：映射表缺失任一情、或矢量数值偏离契约 → 六条断言立即失败；
- 绿：六情齐全且数值精确符合 T1 契约 → 通过。

契约（《荀子·天论》"好恶喜怒哀乐臧焉"，情 → PAD 增量矢量；
2026-10-07 审查裁定：方向归正跟随 docs/NPC_TAG_DATABASE.md 2.3 节规范——
怒 e_D 由负归正为正、乐 e_A 由正归正为负，幅度保持规范约 1/6 缩放）：
    好 (0.05, 0.03, 0.02)   恶 (-0.05, 0.03, -0.02)   喜 (0.08, 0.05, 0.0)
    怒 (-0.08, 0.10, 0.08)  哀 (-0.06, -0.04, -0.03)  乐 (0.06, -0.05, 0.03)
"""

import unittest

from engine.inner_state import InnerState, StateUpdater, XUNZI_EMOTION_VECTORS


class TestXunziEmotionVectors(unittest.TestCase):
    """荀子六情 → PAD 增量矢量映射契约。"""

    def test_six_emotions_complete(self):
        """六情键齐全：好/恶/喜/怒/哀/乐，缺任一即失败。"""
        self.assertEqual(set(XUNZI_EMOTION_VECTORS.keys()),
                         {"好", "恶", "喜", "怒", "哀", "乐"})

    def test_vector_values(self):
        """各情矢量值精确断言（契约数值，不得偏离）。"""
        self.assertEqual(XUNZI_EMOTION_VECTORS["好"], (0.05, 0.03, 0.02))
        self.assertEqual(XUNZI_EMOTION_VECTORS["恶"], (-0.05, 0.03, -0.02))
        self.assertEqual(XUNZI_EMOTION_VECTORS["喜"], (0.08, 0.05, 0.0))
        self.assertEqual(XUNZI_EMOTION_VECTORS["怒"], (-0.08, 0.10, 0.08))
        self.assertEqual(XUNZI_EMOTION_VECTORS["哀"], (-0.06, -0.04, -0.03))
        self.assertEqual(XUNZI_EMOTION_VECTORS["乐"], (0.06, -0.05, 0.03))

    def test_vector_signature(self):
        """每情值是 3 元组，各分量为 float。"""
        for emotion, vector in XUNZI_EMOTION_VECTORS.items():
            self.assertIsInstance(vector, tuple,
                                  f"「{emotion}」矢量应为 tuple")
            self.assertEqual(len(vector), 3,
                             f"「{emotion}」矢量应为 3 元组 (d_e_P, d_e_A, d_e_D)")
            for component in vector:
                self.assertIsInstance(component, float,
                                       f"「{emotion}」矢量分量应为 float")

    def test_apply_xunzi_to_state(self):
        """对默认 InnerState 施加"怒"矢量（e_D 已归正为正）：
        e_P 0.5-0.08=0.42, e_A 0.3+0.10=0.40, e_D 0.5+0.08=0.58。"""
        updater = StateUpdater(npc=None)  # _apply_xunzi 只操作 state，无需 npc
        state = InnerState()
        updater._apply_xunzi(state, "怒")
        self.assertAlmostEqual(state.e_P, 0.42)
        self.assertAlmostEqual(state.e_A, 0.40)
        self.assertAlmostEqual(state.e_D, 0.58)
        # 生理稳态与压力负荷不受六情矢量影响（正交性）
        self.assertAlmostEqual(state.p_fatigue, 0.2)
        self.assertAlmostEqual(state.p_hunger, 0.3)
        self.assertAlmostEqual(state.p_pain, 0.1)
        self.assertAlmostEqual(state.p_drive, 0.5)
        self.assertAlmostEqual(state.S_stress, 0.2)


class TestXunziSpecContractLock(unittest.TestCase):
    """矢量-规范契约锁：代码六情矢量 vs docs/NPC_TAG_DATABASE.md 2.3 节。

    消除"测试只锁代码自洽值"的盲区：规范矢量硬编码于测试内，
    任何与规范的清单/方向/幅度偏离将在此立即失败，把规范契约
    接入验证链（2026-10-07 审查裁定新增）。

    锁口径（三条断言）：
    ① 清单锁：代码六情键与规范六情完全一致（六键齐备）；
    ② 方向锁：规范非零分量处，代码对应分量不得与规范方向相反
       （spec*code ≥ 0；代码分量 0.0 视为"该维无增量"，不构成反向，
       如"喜" e_D 规范 +0.2 / 代码 0.0）；两处归正点另作严格断言：
       怒 e_D > 0、乐 e_A < 0；
    ③ 缩放锁：规范非零分量处 |code| ≤ |spec|（代码为规范约 1/6
       幅度的小步进缩放版，不得超规范幅度）；规范零分量处
       （仅"好" e_D，规范主张该维无偏置）|code| 不得超过代码
       全表最大分量步进，防止在规范零维引入超尺度偏置。
    """

    # 规范矢量表：docs/NPC_TAG_DATABASE.md 2.3 节 (Δe_P, Δe_A, Δe_D)
    SPEC_VECTORS = {
        "好": (0.3, 0.2, 0.0),      # Desire/Liking：欲求与吸引
        "恶": (-0.4, 0.1, -0.1),    # Aversion/Disgust：厌恶
        "喜": (0.5, 0.4, 0.2),      # Joy/Triumph：爆发愉悦
        "怒": (-0.3, 0.6, 0.5),     # Wrath/Indignation：攻击本能
        "哀": (-0.6, -0.4, -0.4),   # Grief/Sorrow：能量退缩
        "乐": (0.4, -0.3, 0.1),     # Contentment：身心松弛
    }
    COMPONENT_NAMES = ("e_P", "e_A", "e_D")

    def test_spec_emotion_roster_lock(self):
        """断言①：代码六情清单与规范完全一致（六键齐备）。"""
        self.assertEqual(set(XUNZI_EMOTION_VECTORS.keys()),
                         set(self.SPEC_VECTORS.keys()))
        self.assertEqual(len(XUNZI_EMOTION_VECTORS), 6)
        for emotion in self.SPEC_VECTORS:
            self.assertIn(emotion, XUNZI_EMOTION_VECTORS,
                          f"规范六情「{emotion}」在代码映射表中缺失")

    def test_direction_lock(self):
        """断言②：方向锁——规范非零分量处代码不得反向；归正点严格断言。"""
        for emotion, spec_vec in self.SPEC_VECTORS.items():
            code_vec = XUNZI_EMOTION_VECTORS[emotion]
            for i, name in enumerate(self.COMPONENT_NAMES):
                spec_v, code_v = spec_vec[i], code_vec[i]
                if spec_v != 0.0:
                    self.assertGreaterEqual(
                        spec_v * code_v, 0.0,
                        f"「{emotion}」{name} 方向与规范相反："
                        f"规范 {spec_v:+.1f}，代码 {code_v:+.2f}")
        # 归正点显式严格断言（2026-10-07 审查裁定）
        self.assertGreater(
            XUNZI_EMOTION_VECTORS["怒"][2], 0.0,
            "怒 e_D 必须为正：规范 +0.5（愤怒=高支配）")
        self.assertLess(
            XUNZI_EMOTION_VECTORS["乐"][1], 0.0,
            "乐 e_A 必须为负：规范 -0.3（乐=身心松弛低唤醒）")

    def test_scaling_lock(self):
        """断言③：缩放锁——代码分量幅度不得超规范（小步进缩放版）。"""
        # 代码全表最大分量步进：规范零分量处的偏置上界基准
        max_step = max(abs(c)
                       for vec in XUNZI_EMOTION_VECTORS.values()
                       for c in vec)
        for emotion, spec_vec in self.SPEC_VECTORS.items():
            code_vec = XUNZI_EMOTION_VECTORS[emotion]
            for i, name in enumerate(self.COMPONENT_NAMES):
                spec_v, code_v = spec_vec[i], code_vec[i]
                if spec_v != 0.0:
                    self.assertLessEqual(
                        abs(code_v), abs(spec_v),
                        f"「{emotion}」{name} 超规范幅度："
                        f"|{code_v:.2f}| > |{spec_v:.1f}|")
                else:
                    self.assertLessEqual(
                        abs(code_v), max_step,
                        f"「{emotion}」{name} 规范为 0（无偏置主张），"
                        f"代码偏置 |{code_v:.2f}| 超全表最大步进 {max_step:.2f}")


if __name__ == "__main__":
    unittest.main()

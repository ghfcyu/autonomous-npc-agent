"""T1 对抗性测试：荀子六情映射表（XUNZI_EMOTION_VECTORS）。

先红后绿设计：
- 红：映射表缺失任一情、或矢量数值偏离契约 → 六条断言立即失败；
- 绿：六情齐全且数值精确符合 T1 契约 → 通过。

契约（《荀子·天论》"好恶喜怒哀乐臧焉"，情 → PAD 增量矢量）：
    好 (0.05, 0.03, 0.02)   恶 (-0.05, 0.03, -0.02)   喜 (0.08, 0.05, 0.0)
    怒 (-0.08, 0.10, -0.05)  哀 (-0.06, -0.04, -0.03)  乐 (0.06, 0.04, 0.03)
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
        self.assertEqual(XUNZI_EMOTION_VECTORS["怒"], (-0.08, 0.10, -0.05))
        self.assertEqual(XUNZI_EMOTION_VECTORS["哀"], (-0.06, -0.04, -0.03))
        self.assertEqual(XUNZI_EMOTION_VECTORS["乐"], (0.06, 0.04, 0.03))

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
        """对默认 InnerState 施加"怒"矢量：
        e_P 0.5-0.08=0.42, e_A 0.3+0.10=0.40, e_D 0.5-0.05=0.45。"""
        updater = StateUpdater(npc=None)  # _apply_xunzi 只操作 state，无需 npc
        state = InnerState()
        updater._apply_xunzi(state, "怒")
        self.assertAlmostEqual(state.e_P, 0.42)
        self.assertAlmostEqual(state.e_A, 0.40)
        self.assertAlmostEqual(state.e_D, 0.45)
        # 生理稳态与压力负荷不受六情矢量影响（正交性）
        self.assertAlmostEqual(state.p_fatigue, 0.2)
        self.assertAlmostEqual(state.p_hunger, 0.3)
        self.assertAlmostEqual(state.p_pain, 0.1)
        self.assertAlmostEqual(state.p_drive, 0.5)
        self.assertAlmostEqual(state.S_stress, 0.2)


if __name__ == "__main__":
    unittest.main()

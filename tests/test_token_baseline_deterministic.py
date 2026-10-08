"""token_baseline 确定性测试：同场景多次运行结果必须完全一致。

背景（2026-10-05 数字纠正）：
    engine/world.py:229 天气变化走 random.random()，24 次 tick 中随机
    天气事件写入 NPC 记忆 → context_for 输出变化 → prompt token 波动。
    scripts/token_baseline.py main() 首行 random.seed(42) 固定天气序列后，
    token 总消耗可确定性复现，口径唯一。

口径变更史（T1 8D 心智基底迁移，2026-10-06）：
    - 旧口径 4553：5D 内状态（5 参数【此刻内心】）+ 关系网无玩家边；
    - 新口径 4737：8D 基底（8 参数【此刻内心】）+ 系统 prompt
      注入玩家亲缘度边（NPC 初始化自动建 chen→player / lily→player）。
    两次完整场景实测严格相等，仍在审查硬标准 4400-4800 内。

口径变更史（T2 收口先行批，2026-10-09）：
    - 4737 → 4769：configs 声明 tag_profile="genesis" 的 chen/lily
      挂载常驻化，system prompt 增身份标签行（8 字符/次调用×4 次调用），
      属常驻化身份标签行带来的合法增量（结构性功能变更，非单点裁剪），
      实测仍在审查硬标准 4400-4800 内。

对抗性设计（先红后绿）：
    红：seed 未固定 → 两次完整场景 total_tokens 概率性不等；
    绿：seed=42 → 两次 total_tokens 严格相等且恒为 4769 → 通过。

测试路径与命令行复跑完全一致：直接调用 scripts/token_baseline.py 的
main()（24 tick + 4 对话的完整场景），不做任何桩替换，保证测的就是
审查者/迭代者一键复跑的真实代码。
"""

import contextlib
import importlib.util
import io
import os
import random
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASELINE_SCRIPT_PATH = os.path.join(_PROJECT_ROOT, "scripts",
                                     "token_baseline.py")

# seed=42 固定后的基线值（T2 收口先行批 chen/lily 挂载常驻化后实测，
# 两次运行严格相等；4737 → 4769 为常驻化身份标签行带来的合法增量）。
# 若未来迭代合法改变 token 基线，须有意识更新此值并同步 README/PROGRESS，
# 不允许数字未经代码验证进入文档（2026-10-04 审查批评项）。
PINNED_TOTAL_TOKENS = 4769
# 审查硬标准区间
HARD_RANGE = (4400, 4800)


def _load_baseline_module():
    """按文件路径加载 scripts/token_baseline.py（scripts/ 不是 package）。"""
    spec = importlib.util.spec_from_file_location(
        "token_baseline_under_test", _BASELINE_SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_full_baseline_scenario():
    """跑一次完整基线场景（24 tick + 4 对话），返回 token 总消耗。

    与 `python3 scripts/token_baseline.py` 同一条代码路径（main()），
    stdout 重定向捕获后解析「Token 总消耗」行。
    """
    module = _load_baseline_module()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        module.main()
    for line in buffer.getvalue().splitlines():
        if "Token 总消耗" in line:
            return int(line.rsplit("：", 1)[1].strip())
    raise AssertionError("token_baseline 输出中未找到「Token 总消耗」行")


class TestTokenBaselineDeterministic(unittest.TestCase):
    """token_baseline 场景确定性：多次运行 total_tokens 必须完全一致。"""

    def setUp(self):
        # main() 内 random.seed(42) 是全局副作用；保存/恢复随机状态，
        # 避免泄漏到后续测试文件（unittest 按文件名序执行，test_village/
        # test_world 在本文件之后运行）。
        self._saved_random_state = random.getstate()

    def tearDown(self):
        random.setstate(self._saved_random_state)

    def test_baseline_deterministic(self):
        """两次完整场景（24 tick + 4 对话）total_tokens 完全相等。"""
        total_first = _run_full_baseline_scenario()
        total_second = _run_full_baseline_scenario()
        self.assertEqual(
            total_first, total_second,
            f"两次完整场景 token 总消耗不一致：{total_first} vs "
            f"{total_second}——随机性未被 seed 固定，基线不可复现")
        # 数字固化：seed=42 基线恒为 4769（T2 常驻化后实测口径；
        # 4737→4769 为常驻化身份标签行带来的合法增量）。
        # 此断言即"数字经代码验证"的流程固化。
        self.assertEqual(
            total_first, PINNED_TOTAL_TOKENS,
            f"token 基线应为 {PINNED_TOTAL_TOKENS}，实际 {total_first}——"
            f"若为合法变更请同步更新 PINNED_TOTAL_TOKENS 与文档口径")

    def test_baseline_in_hard_range(self):
        """token 总消耗落在审查硬标准区间 4400-4800 内。"""
        total = _run_full_baseline_scenario()
        low, high = HARD_RANGE
        self.assertTrue(
            low <= total <= high,
            f"token 总消耗 {total} 超出审查硬标准 {low}-{high}")


if __name__ == "__main__":
    unittest.main()

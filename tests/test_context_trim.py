"""对抗性测试：上下文裁剪 6→4，验证 token 削减手段落地。

设计为"先红后绿"：
- test_recent_context_window_is_four：常量值必须是 4，旧值 6 立即失败。
- test_context_for_returns_at_most_four_recent：写入 8 条事件后 recent <= 4，
  旧代码 recent(6) 会返回 6 条导致失败。
- test_context_for_returns_most_recent_four：验证返回的是最新 4 条且顺序正确。
"""

import unittest

from engine.memory import RECENT_CONTEXT_WINDOW, MemorySystem
from engine.world import WorldEvent


def _player_spoke(text: str) -> WorldEvent:
    """构造一条 player_spoke 事件。"""
    return WorldEvent(kind="player_spoke", actor="玩家", tick=0,
                     payload={"text": text})


class TestContextTrim(unittest.TestCase):
    """上下文裁剪对抗性测试。"""

    def test_recent_context_window_is_four(self):
        """常量必须是 4，禁止回退到旧的 6。"""
        self.assertEqual(RECENT_CONTEXT_WINDOW, 4)

    def test_context_for_returns_at_most_four_recent(self):
        """写入 8 条事件后 recent 最多 4 条（旧代码返回 6 会失败）。"""
        mem = MemorySystem("test", store_dir=None, capacity=10)
        for i in range(8):
            mem.observe(_player_spoke(f"第{i}句"))
        ctx = mem.context_for("测试")
        self.assertLessEqual(len(ctx["recent"]), 4)

    def test_context_for_returns_most_recent_four(self):
        """返回最新 4 条，顺序为最旧在前、最新在后。

        observe 把 player_spoke 转写为 "玩家说：{text}"，
        写入 "第4句"~"第7句" 后记忆内容为 "玩家说：第4句" 等。
        """
        mem = MemorySystem("test", store_dir=None, capacity=10)
        for i in range(8):
            mem.observe(_player_spoke(f"第{i}句"))
        ctx = mem.context_for("测试")
        expected = [f"玩家说：第{i}句" for i in range(4, 8)]
        self.assertEqual(ctx["recent"], expected)


if __name__ == "__main__":
    unittest.main()

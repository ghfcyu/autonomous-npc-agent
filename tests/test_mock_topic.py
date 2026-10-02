"""MockLLMProvider 话题匹配范围修复测试：仅扫描【玩家说】段。

验收维度（1 个测试类，4 个用例）：
1. 玩家说段命中话题 → 返回话题响应
2. 地点名含关键词（铁匠铺含"铁"）不再误触发 → 返回 fallback
3. 外观文本含关键词（打铁皮围裙含"铁"）不再误触发 → 返回 fallback
4. 无【玩家说】标记时回退整个 user（向后兼容）→ 仍能命中话题
"""

import json
import unittest

from engine.llm.mock import MockLLMProvider


# ------------------------------------------------------------------ #
# 辅助构造
# ------------------------------------------------------------------ #
def _make_messages(user_text, banks=None):
    """构造含 banks 的 messages，banks 单元素保证 _pick 确定性。"""
    banks = banks or {
        "topic_responses": {"铁": ["命中铁话题"]},
        "greeting_bank": ["招呼"],
        "fallback_bank": ["未命中fallback"],
    }
    system = (f"<!--persona:chen-->\n你是铁匠陈。\n"
              f"<<<banks>>>{json.dumps(banks, ensure_ascii=False)}")
    return [{"role": "system", "content": system},
            {"role": "user", "content": user_text}]


def _reply_text(mock, messages):
    """调 chat 并解析返回 JSON 取 text 字段。"""
    raw = mock.chat(messages)
    return json.loads(raw)["text"]


class TestMockTopicScoping(unittest.TestCase):
    """话题匹配仅扫描【玩家说】段，地点名/外观文本不再误触发。"""

    def setUp(self):
        # 首次调用走 greeting_bank；warmup 后 count > 0，未命中话题才走
        # fallback_bank，使后续断言能区分 "未命中fallback" 与 "招呼"。
        self.mock = MockLLMProvider()
        self.mock.chat(_make_messages("hello"))

    def test_player_say_segment_match(self):
        """玩家说段含"铁" → 命中话题。"""
        text = _reply_text(self.mock,
                           _make_messages("【玩家说】给我打一把铁剑"))
        self.assertEqual(text, "命中铁话题")

    def test_location_name_no_false_match(self):
        """地点名"铁匠铺"含"铁"但不在玩家说段 → 不误触发，返回 fallback。"""
        user_text = ("【当前世界】时间 08:00，你在 铁匠铺\n"
                     "【玩家说】今天天气真好")
        text = _reply_text(self.mock, _make_messages(user_text))
        self.assertEqual(text, "未命中fallback")

    def test_appearance_text_no_false_match(self):
        """外观"打铁皮围裙"含"铁"但不在玩家说段 → 不误触发，返回 fallback。"""
        user_text = ("【周围的人】陈（穿着打铁皮围裙）\n"
                     "【玩家说】你好啊")
        text = _reply_text(self.mock, _make_messages(user_text))
        self.assertEqual(text, "未命中fallback")

    def test_no_marker_fallback_to_full_user(self):
        """无【玩家说】标记 → 回退整个 user，仍能命中话题（向后兼容）。"""
        text = _reply_text(self.mock, _make_messages("给我打一把铁剑"))
        self.assertEqual(text, "命中铁话题")


if __name__ == "__main__":
    unittest.main()

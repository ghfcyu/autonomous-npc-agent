"""Token 度量基线测试：Mock/OpenAI Provider 的 usage 上报与 NPCEngine 聚合。

验收维度（4 个测试类，10 个用例）：
1. MockLLMProvider 字符数估算：last_usage 三键、公式、确定性、累计
2. OpenAICompatProvider 从 API 响应 body.usage 解析：有 usage / 无 usage
3. 背景 NPC 零 LLM 路径零 token；token_stats 仅含核心 NPC
4. status() 顶层 token_stats 字段结构 + 对话后值
"""

import json
import unittest
from unittest.mock import patch, MagicMock

from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.llm.openai_compat import OpenAICompatProvider
from engine.world import WorldEvent


# ------------------------------------------------------------------ #
# 辅助构造
# ------------------------------------------------------------------ #
def _make_messages(player_input="你好", persona_id="test"):
    """构造含 banks 的 messages，确保 MockLLMProvider.chat 正常返回。"""
    banks = {"greeting_bank": ["你好。"], "fallback_bank": ["嗯。"]}
    system = (f"<!--persona:{persona_id}-->\n你是测试NPC。\n"
              f"<<<banks>>>{json.dumps(banks, ensure_ascii=False)}")
    user = f"【玩家说】{player_input}"
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def _patch_urlopen(body: dict):
    """构造 patch urllib.request.urlopen 的上下文，返回 (patcher, body_bytes)。

    用法：
        patcher, body_bytes = _patch_urlopen(body)
        with patcher:
            provider.chat(messages)
    """
    patcher = patch("urllib.request.urlopen")
    m = patcher.start()
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode("utf-8")
    resp.__enter__ = lambda self: resp
    resp.__exit__ = lambda *a: None
    m.return_value = resp
    return patcher


# ================================================================== #
# 1. MockLLMProvider 字符数估算
# ================================================================== #
class TestMockTokenEstimation(unittest.TestCase):
    """Mock Provider 以字符数估算 token，每次 chat 更新 last_usage。"""

    def test_mock_chat_updates_last_usage(self):
        mock = MockLLMProvider()
        mock.chat(_make_messages())
        self.assertGreater(mock.last_usage["prompt_tokens"], 0)
        self.assertGreater(mock.last_usage["completion_tokens"], 0)
        self.assertGreater(mock.last_usage["total_tokens"], 0)

    def test_mock_token_estimation_formula(self):
        mock = MockLLMProvider()
        messages = _make_messages(player_input="给我打一把铁剑",
                                  persona_id="chen")
        system = messages[0]["content"]
        user = messages[1]["content"]
        response = mock.chat(messages)
        self.assertEqual(mock.last_usage["prompt_tokens"],
                         len(system) + len(user))
        self.assertEqual(mock.last_usage["completion_tokens"], len(response))
        self.assertEqual(mock.last_usage["total_tokens"],
                         len(system) + len(user) + len(response))

    def test_mock_token_deterministic(self):
        """同输入两次 chat（各自全新 Provider，同 seed）→ last_usage 完全相等。"""
        mock1 = MockLLMProvider()
        mock1.chat(_make_messages())
        first = dict(mock1.last_usage)

        mock2 = MockLLMProvider()
        mock2.chat(_make_messages())
        second = dict(mock2.last_usage)

        self.assertEqual(first, second)

    def test_mock_total_tokens_accumulates(self):
        mock = MockLLMProvider()
        mock.chat(_make_messages(player_input="你好"))
        first_total = mock.last_usage["total_tokens"]
        mock.chat(_make_messages(player_input="再来一次"))
        second_total = mock.last_usage["total_tokens"]
        self.assertEqual(mock.total_tokens_used, first_total + second_total)


# ================================================================== #
# 2. OpenAICompatProvider 从 API 响应解析 usage
# ================================================================== #
class TestOpenAIUsageParsing(unittest.TestCase):
    """OpenAI 兼容 Provider 从响应 body.usage 读取 token 统计。"""

    def test_openai_parses_usage_from_body(self):
        body = {
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                       "total_tokens": 15},
        }
        provider = OpenAICompatProvider(base_url="http://x", api_key="y")
        with patch("urllib.request.urlopen") as m:
            resp = MagicMock()
            resp.read.return_value = json.dumps(body).encode("utf-8")
            resp.__enter__ = lambda self: resp
            resp.__exit__ = lambda *a: None
            m.return_value = resp
            result = provider.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(result, "hi")
        self.assertEqual(provider.last_usage,
                         {"prompt_tokens": 10, "completion_tokens": 5,
                          "total_tokens": 15})
        self.assertEqual(provider.total_tokens_used, 15)

    def test_openai_handles_missing_usage(self):
        body = {"choices": [{"message": {"content": "hi"}}]}
        provider = OpenAICompatProvider(base_url="http://x", api_key="y")
        with patch("urllib.request.urlopen") as m:
            resp = MagicMock()
            resp.read.return_value = json.dumps(body).encode("utf-8")
            resp.__enter__ = lambda self: resp
            resp.__exit__ = lambda *a: None
            m.return_value = resp
            provider.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(provider.last_usage,
                         {"prompt_tokens": 0, "completion_tokens": 0,
                          "total_tokens": 0})


# ================================================================== #
# 3. 背景 NPC 零 LLM 路径零 token；token_stats 仅核心 NPC
# ================================================================== #
class TestBackgroundZeroToken(unittest.TestCase):
    """背景 NPC 走规则路径不消耗 token，token_stats 仅聚合核心 NPC。"""

    def test_background_npcs_zero_token(self):
        engine = NPCEngine(llm=MockLLMProvider())
        # (a) 多轮 accumulate_event_slot(1.0) 触发默认环境事件池
        for _ in range(10):
            engine.world.accumulate_event_slot(1.0)
        # (b) 直接发布 env_event，覆盖核心 NPC 所在地点
        for _ in range(5):
            engine.world.bus.publish(WorldEvent(
                engine.world.tick_count, "env_event", "chen",
                {"summary": "测试环境事件"}))
            engine.world.bus.publish(WorldEvent(
                engine.world.tick_count, "env_event", "lily",
                {"summary": "测试环境事件"}))
        # (c) 将玩家移至每个背景 NPC 所在地并发布 env_event
        for bg_id, bg in engine.background_npcs.items():
            engine.move_player(bg.entity.location_id)
            engine.world.bus.publish(WorldEvent(
                engine.world.tick_count, "env_event", "player",
                {"summary": "测试环境事件"}))
        # (d) 多轮 tick 推进时间
        for _ in range(10):
            engine.tick(60)
        # 零 LLM 调用 + 零 token 消耗
        self.assertEqual(engine.llm.call_count, 0)
        self.assertEqual(engine.llm.total_tokens_used, 0)

    def test_token_stats_only_core_npcs(self):
        engine = NPCEngine(llm=MockLLMProvider())
        engine.player_says("铁匠，打把剑", "chen")
        engine.player_says("有新货吗", "lily")
        # token_stats 含核心 NPC
        self.assertIn("chen", engine.token_stats)
        self.assertIn("lily", engine.token_stats)
        # 不含任何背景 NPC id
        for bg_id in engine.background_npcs:
            self.assertNotIn(bg_id, engine.token_stats,
                             f"背景 NPC {bg_id} 不应进入 token_stats")
        # chen 调用 1 次，total_tokens > 0
        self.assertEqual(engine.token_stats["chen"]["calls"], 1)
        self.assertGreater(engine.token_stats["chen"]["total_tokens"], 0)


# ================================================================== #
# 4. status() 顶层 token_stats 字段
# ================================================================== #
class TestStatusTokenField(unittest.TestCase):
    """status() 返回值含 token_stats 顶层键。"""

    def test_status_has_token_stats(self):
        engine = NPCEngine(llm=MockLLMProvider())
        status = engine.status()
        self.assertIn("token_stats", status)
        ts = status["token_stats"]
        self.assertIn("by_npc", ts)
        self.assertIn("total_tokens", ts)
        self.assertIn("llm_calls", ts)

    def test_status_token_stats_after_dialogue(self):
        engine = NPCEngine(llm=MockLLMProvider())
        engine.player_says("铁匠，打把剑", "chen")
        status = engine.status()
        ts = status["token_stats"]
        self.assertGreater(ts["total_tokens"], 0)
        self.assertEqual(ts["by_npc"]["chen"]["calls"], 1)


if __name__ == "__main__":
    unittest.main()

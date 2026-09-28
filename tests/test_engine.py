"""引擎端到端集成测试：感知→记忆→决策→行动 全链路（Mock LLM）。"""

import unittest

from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.npc import Persona
from engine.states import NPCState


def make_engine():
    personas = [
        Persona(id="chen", name="铁匠陈", role="blacksmith", location_id="forge",
                personality="沉稳", speech_style="简短", backstory="三代铁匠",
                greeting_bank=["炉子热着。"], fallback_bank=["嗯，你说。"],
                sleep_mumble="（鼾声）", schedule={"20": "SLEEPING"},
                topic_responses={"剑|刀": ["打剑？拿好铁来。"]}),
        Persona(id="lily", name="商人莉莉", role="merchant", location_id="market",
                personality="热情", speech_style="快", backstory="跑过五个城镇",
                greeting_bank=["贵客来啦！"], fallback_bank=["我听着呢！"],
                sleep_mumble="（梦中谈价）", schedule={"21": "SLEEPING"},
                topic_responses={"货|买": ["新到的皮子！"]}),
    ]
    return NPCEngine(llm=MockLLMProvider(), npc_configs=personas)


class TestEngineEndToEnd(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()

    def test_player_says_full_loop(self):
        result = self.engine.player_says("帮我打一把剑", "chen")
        self.assertTrue(result["ok"])
        self.assertIn("剑", result["reply"])
        self.assertEqual(result["state"], "交谈")
        # 感知 → 记忆：短期记忆应有玩家发言
        npc = self.engine.npcs["chen"]
        contents = " ".join(r.content for r in npc.memory.short.recent())
        self.assertIn("打一把剑", contents)

    def test_unknown_npc(self):
        result = self.engine.player_says("你好", "nobody")
        self.assertFalse(result["ok"])

    def test_schedule_sleeps_at_night(self):
        # 从 08:00 推进到 20:30
        for _ in range(75):
            self.engine.tick(10)
        self.assertIs(self.engine.npcs["chen"].state_machine.state, NPCState.SLEEPING)
        result = self.engine.player_says("打把剑", "chen")
        self.assertIn("鼾声", result["reply"])  # 睡觉 → 梦呓回退

    def test_consolidation_after_many_interactions(self):
        for i in range(40):
            self.engine.player_says(f"第{i}句话", "lily")
        npc = self.engine.npcs["lily"]
        self.assertGreater(len(npc.memory.long.records), 0)

    def test_status_shape(self):
        status = self.engine.status()
        self.assertIn("world", status)
        self.assertIn("npcs", status)
        self.assertEqual(len(status["npcs"]), 2)
        self.assertEqual(status["world"]["player"]["location"], "plaza")

    def test_player_move(self):
        result = self.engine.move_player("forge")
        self.assertTrue(result["ok"])
        self.assertEqual(self.engine.world.entities["player"].location_id, "forge")


if __name__ == "__main__":
    unittest.main()

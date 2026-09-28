"""G1 功能验收测试：重要事件直写长期记忆 + 玩家送礼接口。

覆盖两块契约：
1. ``MemorySystem.observe``：转写 importance >= LONG_TERM_IMPORTANCE_THRESHOLD(0.7)
   的事件，在写短期记忆的同时写一条长期记忆（item_given 是当前模板表中唯一
   importance >= 0.7 的事件类型，player_spoke=0.6 等仍只写短期）。
2. ``NPCEngine.player_gives(npc_id, item)``：送礼发布 actor="player" 的
   item_given 事件、被目标 NPC 感知并沉淀为长期记忆、进入决策上下文。

G1 实现并行开发中，本文件用例允许暂时红，由 PM 统一验收。
"""

import unittest

from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.memory import MemorySystem
from engine.npc import Persona
from engine.world import WorldEvent


def make_engine():
    """与 tests/test_engine.py 相同的构造方式：Mock LLM + 内联 Persona，无落盘。"""
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


class TestObserveWritesLongTerm(unittest.TestCase):
    """高重要度事件（item_given，importance=0.8）应同时写短期与长期记忆。"""

    def test_item_given_writes_both_memories(self):
        mem = MemorySystem("smith")
        before = len(mem.long.records)
        mem.observe(WorldEvent(5, "item_given", "player",
                               {"to": "smith", "item": "铁矿石"}))
        # ① 长期记忆恰好多 1 条
        self.assertEqual(len(mem.long.records), before + 1)
        rec = mem.long.records[-1]
        # ② content 含物品名
        self.assertIn("铁矿石", rec.content)
        # ③ tags 含事件类型
        self.assertIn("item_given", rec.tags)
        # ④ 重要度达到阈值
        self.assertGreaterEqual(rec.importance, 0.7)
        # ⑤ 短期记忆也有记录（感知不缺席）
        self.assertEqual(len(mem.short), 1)
        self.assertIn("铁矿石", mem.short.recent()[-1].content)

    def test_long_term_record_fields_match_contract(self):
        """长期记录字段应与契约一致：content/tags/importance 同短期，kind/tick 同事件。"""
        mem = MemorySystem("smith")
        event = WorldEvent(5, "item_given", "player",
                           {"to": "smith", "item": "铁矿石"})
        mem.observe(event)
        rec = mem.long.records[-1]
        short_rec = mem.short.recent()[-1]
        self.assertEqual(rec.content, short_rec.content)
        self.assertEqual(rec.tags, short_rec.tags)
        self.assertEqual(rec.importance, short_rec.importance)
        self.assertEqual(rec.kind, "interaction")
        self.assertEqual(rec.tick, event.tick)

    def test_threshold_constant_value(self):
        # 延迟导入：G1 落地前仅本用例 error，不影响其他用例独立运行
        from engine.memory import LONG_TERM_IMPORTANCE_THRESHOLD
        self.assertEqual(LONG_TERM_IMPORTANCE_THRESHOLD, 0.7)


class TestLowValueStaysShortTerm(unittest.TestCase):
    """低于阈值的事件（player_spoke importance=0.6）仍只写短期记忆。"""

    def test_low_importance_event_not_in_long_term(self):
        mem = MemorySystem("smith")
        # player_spoke 转写模板 importance=0.6 < 0.7
        mem.observe(WorldEvent(3, "player_spoke", "player",
                               {"to": "smith", "text": "你好"}))
        self.assertEqual(mem.long.records, [])
        self.assertEqual(len(mem.short), 1)
        self.assertIn("你好", mem.short.recent()[-1].content)

    def test_multiple_low_value_events_stay_short_term(self):
        """模板表中其余低价值 kind（0.2~0.4）也都不触发长期直写。"""
        mem = MemorySystem("smith")
        mem.observe(WorldEvent(1, "player_spoke", "player",
                               {"to": "smith", "text": "闲聊"}))
        mem.observe(WorldEvent(2, "weather_changed", "world", {"weather": "rain"}))
        mem.observe(WorldEvent(3, "entity_moved", "player",
                               {"from": "plaza", "to": "forge"}))
        self.assertEqual(len(mem.short), 3)
        self.assertEqual(mem.long.records, [])


class TestPlayerGivesEndToEnd(unittest.TestCase):
    """player_gives 送礼闭环：事件发布 → NPC 感知 → 长期记忆 → 决策上下文。"""

    def setUp(self):
        self.engine = make_engine()

    def test_player_gives_creates_long_term_memory(self):
        result = self.engine.player_gives("chen", "铁矿石")
        # ① 返回契约
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("item"), "铁矿石")
        self.assertEqual(result.get("to"), "chen")
        self.assertIn("from_inventory", result)
        # 必有一条 actor="player" 的 item_given 事件发布
        given = [e for e in self.engine.world.bus.history
                 if e.kind == "item_given" and e.actor == "player"]
        self.assertGreaterEqual(len(given), 1)
        self.assertTrue(any(e.payload.get("to") == "chen"
                            and e.payload.get("item") == "铁矿石"
                            for e in given))
        # ② 目标 NPC 感知并沉淀长期记忆（"铁矿石" 不在玩家初始背包，
        #    覆盖 from_inventory=False 的凭空送礼路径）
        npc = self.engine.npcs["chen"]
        self.assertTrue(any("铁矿石" in r.content
                            for r in npc.memory.short.recent()))
        matches = [r for r in npc.memory.long.records if "铁矿石" in r.content]
        self.assertGreaterEqual(len(matches), 1)
        # ③ 进入决策上下文
        ctx = npc.memory.context_for("铁矿石")
        self.assertTrue(ctx["long_term"])
        self.assertTrue(any("铁矿石" in c for c in ctx["long_term"]))

    def test_player_gives_unknown_npc(self):
        result = self.engine.player_gives("nobody", "x")
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("reason"), "unknown npc")
        # 送礼失败不应产生任何 item_given 事件
        given = [e for e in self.engine.world.bus.history
                 if e.kind == "item_given" and e.actor == "player"]
        self.assertEqual(len(given), 0)

    def test_player_gives_twice_accumulates(self):
        # "苹果" 在玩家初始背包中，覆盖 from_inventory=True 的背包扣减路径
        self.engine.player_gives("chen", "铁矿石")
        self.engine.player_gives("chen", "苹果")
        npc = self.engine.npcs["chen"]
        related = [r for r in npc.memory.long.records
                   if "铁矿石" in r.content or "苹果" in r.content]
        self.assertGreaterEqual(len(related), 2)
        self.assertTrue(any("铁矿石" in r.content for r in related))
        self.assertTrue(any("苹果" in r.content for r in related))


class TestConsolidationRegression(unittest.TestCase):
    """回归：长期直写不破坏既有巩固路径（短期满 → 压缩摘要入长期）。"""

    def test_consolidate_still_works_with_long_term_writes(self):
        mem = MemorySystem("smith", capacity=4)
        # 2 条高价值（短期+长期双写）+ 2 条低价值（只写短期），短期恰好满容
        mem.observe(WorldEvent(1, "item_given", "player",
                               {"to": "smith", "item": "铁矿石"}))
        mem.observe(WorldEvent(2, "player_spoke", "player",
                               {"to": "smith", "text": "你好"}))
        mem.observe(WorldEvent(3, "item_given", "player",
                               {"to": "smith", "item": "苹果"}))
        mem.observe(WorldEvent(4, "player_spoke", "player",
                               {"to": "smith", "text": "再见"}))
        self.assertEqual(len(mem.short), 4)
        # 高价值直写产生了 2 条 interaction 长期记录
        interactions = [r for r in mem.long.records if r.kind == "interaction"]
        self.assertEqual(len(interactions), 2)
        # 巩固仍正常：最旧一批压缩为 summary 入长期
        record = mem.consolidate()
        self.assertIsNotNone(record)
        self.assertEqual(record.kind, "summary")
        self.assertIn("收到来自", record.content)
        # 长期 = 2 条直写 interaction + 1 条巩固 summary
        self.assertEqual(len(mem.long.records), 3)
        self.assertEqual(len([r for r in mem.long.records
                              if r.kind == "summary"]), 1)
        # 容量 4 < CONSOLIDATE_BATCH 8 → 短期整批弹出清空
        self.assertEqual(len(mem.short), 0)


if __name__ == "__main__":
    unittest.main()

"""记忆层测试：读写、检索评分、巩固。"""

import os
import tempfile
import unittest

from engine.memory import MemoryRecord, MemorySystem, ShortTermMemory, LongTermMemory
from engine.world import WorldEvent


class TestShortTermMemory(unittest.TestCase):
    def test_capacity_bounded(self):
        stm = ShortTermMemory(capacity=5)
        for i in range(10):
            stm.add(MemoryRecord(content=f"事件{i}", tick=i))
        self.assertEqual(len(stm), 5)
        self.assertEqual(stm.recent()[-1].content, "事件9")


class TestLongTermMemory(unittest.TestCase):
    def test_retrieve_ranks_by_relevance(self):
        ltm = LongTermMemory()
        ltm.add(MemoryRecord("玩家张三送过我铁矿石", 1, 0.8, ["张三", "item_given"]))
        ltm.add(MemoryRecord("今天下了雨", 2, 0.2, ["weather"]))
        ltm.add(MemoryRecord("与玩家李四闲聊了几句", 3, 0.3, ["李四"]))
        hits = ltm.retrieve("张三 铁矿石", top_k=2)
        self.assertEqual(hits[0].content, "玩家张三送过我铁矿石")

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "npc.json")
            ltm = LongTermMemory(path, "npc1")
            ltm.add(MemoryRecord("记得玩家欠我钱", 5, 0.9, ["债务"]))
            ltm._persist()
            ltm2 = LongTermMemory(path, "npc1")
            self.assertEqual(len(ltm2.records), 1)
            self.assertEqual(ltm2.records[0].content, "记得玩家欠我钱")


class TestMemorySystem(unittest.TestCase):
    def _system(self, capacity=4):
        return MemorySystem("npc-test", store_dir=None, capacity=capacity)

    def test_observe_player_spoke(self):
        mem = self._system()
        mem.observe(WorldEvent(1, "player_spoke", "player", {"to": "npc-test", "text": "你好"}))
        self.assertEqual(len(mem.short), 1)
        self.assertIn("你好", mem.short.recent()[0].content)

    def test_observe_ignores_uninteresting_events(self):
        mem = self._system()
        mem.observe(WorldEvent(1, "time_passed", "world", {}))
        self.assertEqual(len(mem.short), 0)

    def test_consolidate_only_when_full(self):
        mem = self._system(capacity=4)
        for i in range(3):
            mem.observe(WorldEvent(i, "player_spoke", "player", {"text": f"第{i}句"}))
        self.assertIsNone(mem.consolidate())  # 未满不巩固
        mem.observe(WorldEvent(3, "player_spoke", "player", {"text": "第3句"}))
        record = mem.consolidate()
        self.assertIsNotNone(record)
        self.assertEqual(record.kind, "summary")
        self.assertIn("交谈", record.content)
        self.assertGreater(len(mem.long.records), 0)

    def test_context_for_returns_both_layers(self):
        mem = self._system()
        mem.long.add(MemoryRecord("玩家送过铁矿石", 1, 0.8, ["item_given"]))
        mem.observe(WorldEvent(2, "player_spoke", "player", {"text": "打把剑"}))
        ctx = mem.context_for("铁矿石 打剑")
        self.assertIn("recent", ctx)
        self.assertIn("long_term", ctx)


if __name__ == "__main__":
    unittest.main()

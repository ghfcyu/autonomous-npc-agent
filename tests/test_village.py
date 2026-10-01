"""G6-A 小村庄规模与行为契约验收测试。

验收维度（8 个用例，统一构造 NPCEngine(llm=MockLLMProvider()) 全默认扫描 configs/）：
1. NPC 规模：核心 2 + 背景 ≥8 = 总 ≥10
2. 地点规模：≥15 个，三类齐全 shop ≥5 / public ≥2 / residence ≥8
3. 全员居所已注册：每个 NPC 的 residence 值在 world.locations 中存在
4. 背景 NPC 零 LLM + 规则反应产出 npc_action
5. tick 连续推进一整天不异常，返回三键
6. status 全村庄覆盖，每个地点含 id/name/x/y/entities
7. 核心 NPC 对话不降级（Mock 话题命中，reply 非空）
8. 核心 NPC 居所精确值 house_chen / house_lily
"""

import unittest

from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.world import WorldEvent


class TestVillage(unittest.TestCase):
    """小村庄规模与行为契约验收。"""

    def setUp(self):
        self.engine = NPCEngine(llm=MockLLMProvider())

    # ---------------------------------------------------------------- #
    # 1. NPC 规模
    # ---------------------------------------------------------------- #
    def test_village_has_enough_npcs(self):
        engine = self.engine
        # 核心 NPC 恰好 2 个：chen + lily
        self.assertEqual(len(engine.npcs), 2)
        self.assertIn("chen", engine.npcs)
        self.assertIn("lily", engine.npcs)
        # 背景 NPC ≥ 8，且包含 old_zhang、guard_wang
        self.assertGreaterEqual(len(engine.background_npcs), 8)
        self.assertIn("old_zhang", engine.background_npcs)
        self.assertIn("guard_wang", engine.background_npcs)
        # status["npcs"] 总数 ≥ 10（核心 + 背景）
        status = engine.status()
        self.assertGreaterEqual(len(status["npcs"]), 10)

    # ---------------------------------------------------------------- #
    # 2. 地点规模与三类齐全
    # ---------------------------------------------------------------- #
    def test_village_has_enough_locations(self):
        engine = self.engine
        locs = engine.world.locations
        self.assertGreaterEqual(len(locs), 15)
        # 按 Location.category 统计三类
        counts = {"shop": 0, "public": 0, "residence": 0}
        for loc in locs.values():
            cat = getattr(loc, "category", None)
            if cat in counts:
                counts[cat] += 1
        self.assertGreaterEqual(counts["shop"], 5)
        self.assertGreaterEqual(counts["public"], 2)
        self.assertGreaterEqual(counts["residence"], 8)

    # ---------------------------------------------------------------- #
    # 3. 全员居所已注册为地点
    # ---------------------------------------------------------------- #
    def test_all_npcs_have_residence(self):
        engine = self.engine
        loc_ids = set(engine.world.locations.keys())
        # 核心 NPC：persona.residence
        for npc_id, npc in engine.npcs.items():
            res = npc.persona.residence
            self.assertTrue(res, f"核心 NPC {npc_id} 缺少 residence")
            self.assertIn(res, loc_ids,
                          f"核心 NPC {npc_id} residence '{res}' 未注册为地点")
        # 背景 NPC：bg.residence
        for bg_id, bg in engine.background_npcs.items():
            res = bg.residence
            self.assertTrue(res, f"背景 NPC {bg_id} 缺少 residence")
            self.assertIn(res, loc_ids,
                          f"背景 NPC {bg_id} residence '{res}' 未注册为地点")

    # ---------------------------------------------------------------- #
    # 4. 背景 NPC 零 LLM + 规则反应
    # ---------------------------------------------------------------- #
    def test_background_npcs_zero_llm(self):
        engine = self.engine
        # 构造后 call_count 必为 0
        self.assertEqual(engine.llm.call_count, 0)

        # (a) accumulate_event_slot(1.0) 立即触发默认环境事件池
        for _ in range(10):
            engine.world.accumulate_event_slot(1.0)
        self.assertEqual(engine.llm.call_count, 0)

        # (b) 直接发布 env_event，覆盖核心 NPC 所在地点
        for _ in range(5):
            engine.world.bus.publish(WorldEvent(
                engine.world.tick_count, "env_event", "chen",
                {"summary": "测试环境事件"}))
            engine.world.bus.publish(WorldEvent(
                engine.world.tick_count, "env_event", "lily",
                {"summary": "测试环境事件"}))
        self.assertEqual(engine.llm.call_count, 0)

        # (c) 将玩家移至每个背景 NPC 所在地并发布 env_event，保证反应
        for bg_id, bg in engine.background_npcs.items():
            engine.move_player(bg.entity.location_id)
            engine.world.bus.publish(WorldEvent(
                engine.world.tick_count, "env_event", "player",
                {"summary": "测试环境事件"}))
        self.assertEqual(engine.llm.call_count, 0)

        # (d) 多轮 tick 推进时间
        for _ in range(10):
            engine.tick(60)
        self.assertEqual(engine.llm.call_count, 0)

        # 全程零 LLM 已验证；断言背景 NPC 有规则反应
        npc_actions = [e for e in engine.world.bus.history
                       if e.kind == "npc_action"]
        self.assertGreater(len(npc_actions), 0,
                            "背景 NPC 应至少产出一条 npc_action 规则反应")

    # ---------------------------------------------------------------- #
    # 5. tick 推进一整天
    # ---------------------------------------------------------------- #
    def test_village_tick_runs(self):
        engine = self.engine
        for _ in range(24):
            result = engine.tick(60)
            self.assertIn("clock", result)
            self.assertIn("tick", result)
            self.assertIn("schedule_applied", result)

    # ---------------------------------------------------------------- #
    # 6. status 全村庄覆盖
    # ---------------------------------------------------------------- #
    def test_status_covers_village(self):
        engine = self.engine
        status = engine.status()
        locs = status["world"]["locations"]
        # status 地点数 == world.locations 数（全村庄覆盖）
        self.assertEqual(len(locs), len(engine.world.locations))
        for loc in locs:
            self.assertIn("id", loc)
            self.assertIn("name", loc)
            self.assertIn("x", loc)
            self.assertIn("y", loc)
            self.assertIn("entities", loc)

    # ---------------------------------------------------------------- #
    # 7. 核心 NPC 对话不降级
    # ---------------------------------------------------------------- #
    def test_core_npc_dialogue_mock(self):
        engine = self.engine
        r1 = engine.player_says("铁匠，打把剑", "chen")
        self.assertTrue(r1["ok"])
        self.assertTrue(r1["reply"], "chen reply 不应为空")

        r2 = engine.player_says("有新货吗", "lily")
        self.assertTrue(r2["ok"])
        self.assertTrue(r2["reply"], "lily reply 不应为空")

    # ---------------------------------------------------------------- #
    # 8. 核心 NPC 居所精确值
    # ---------------------------------------------------------------- #
    def test_core_npc_residence(self):
        engine = self.engine
        self.assertEqual(engine.npcs["chen"].persona.residence, "house_chen")
        self.assertEqual(engine.npcs["lily"].persona.residence, "house_lily")


if __name__ == "__main__":
    unittest.main()

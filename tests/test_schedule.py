"""G6-B 作息驱动位置移动验收测试。

测试 G6-B「作息驱动位置移动」：NPC 按作息表在工作地与居所之间移动，
背景 NPC 也参与作息调度，全村庄运转后 token 消耗仍在基线范围内。

验收维度（3 个测试类，12 个用例）：
1. TestScheduleMovement：SLEEPING 移居所 / WORKING 移工作地 / IDLE 保持原位
2. TestVillageFullOperation：全村庄多轮运转的位置变化、事件回流、status 覆盖
3. TestTokenBaselineAfterOperation：全天运转后核心对话 token 基线、背景零 LLM

时间机制约定：
- 世界从 8:00 开始（game_minute=480），tick(minutes) 先推进 game_minute 再取 hour
- 测试某小时作息：设 ``engine.world.game_minute = hour*60`` 后调 ``engine.tick(10)``
  此时 hour = (hour*60 + 10) // 60 = hour，apply_schedule(hour) 被调用
"""

import unittest

from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.world import WorldEvent


class TestScheduleMovement(unittest.TestCase):
    """作息驱动位置变化：SLEEPING 移居所、WORKING 移工作地、IDLE 保持原位。"""

    def setUp(self):
        self.engine = NPCEngine(llm=MockLLMProvider())

    def test_core_npc_sleeping_moves_to_residence(self):
        """核心 NPC chen 在 20:00 SLEEPING 时从 forge 移动到居所 house_chen。"""
        engine = self.engine
        engine.world.game_minute = 20 * 60  # 20:00
        engine.tick(10)  # 推进到 20:10，hour=20，schedule=SLEEPING
        self.assertEqual(engine.npcs["chen"].entity.location_id, "house_chen")

    def test_core_npc_working_at_workplace(self):
        """chen 先睡眠到 house_chen，次日 6:00 WORKING 移回工作地 forge。"""
        engine = self.engine
        # 20:00 → SLEEPING → house_chen
        engine.world.game_minute = 20 * 60
        engine.tick(10)
        self.assertEqual(engine.npcs["chen"].entity.location_id, "house_chen")
        # 次日 5:50，tick(10) 后 6:00 → WORKING → forge
        engine.world.game_minute = 5 * 60 + 50
        engine.tick(10)
        self.assertEqual(engine.npcs["chen"].entity.location_id, "forge")

    def test_background_npc_sleeping_moves_to_residence(self):
        """背景 NPC baker_liu 在 20:00 SLEEPING 时从 bakery 移动到 house_liu。"""
        engine = self.engine
        engine.world.game_minute = 20 * 60
        engine.tick(10)
        self.assertEqual(
            engine.background_npcs["baker_liu"].entity.location_id, "house_liu")

    def test_background_npc_working_returns_to_workplace(self):
        """baker_liu 先睡眠到 house_liu，次日 6:00 WORKING 移回工作地 bakery。"""
        engine = self.engine
        engine.world.game_minute = 20 * 60
        engine.tick(10)
        self.assertEqual(
            engine.background_npcs["baker_liu"].entity.location_id, "house_liu")
        engine.world.game_minute = 5 * 60 + 50
        engine.tick(10)
        self.assertEqual(
            engine.background_npcs["baker_liu"].entity.location_id, "bakery")

    def test_idle_state_keeps_position(self):
        """IDLE 状态保持当前位置不动：chen 12:00 IDLE 仍在 forge。"""
        engine = self.engine
        # chen 初始在 forge，状态 WORKING
        self.assertEqual(engine.npcs["chen"].entity.location_id, "forge")
        engine.world.game_minute = 12 * 60  # 12:00
        engine.tick(10)  # hour=12，schedule=IDLE，不移动
        self.assertEqual(engine.npcs["chen"].entity.location_id, "forge")


class TestVillageFullOperation(unittest.TestCase):
    """全村庄多轮运转：位置变化、entity_moved 事件、npc_action 反应、status 覆盖。"""

    def setUp(self):
        self.engine = NPCEngine(llm=MockLLMProvider())

    def test_full_day_tick_positions_change(self):
        """24 次 tick(60) 一整天，至少有一次 entity_moved 事件（位置发生变化）。

        NPC 在一天中会在工作地与居所间往返，产生 entity_moved 事件；
        用事件存在性证明位置变化，避免「次日同一时刻位置复原」的假阴性。
        """
        engine = self.engine
        for _ in range(24):
            engine.tick(60)
        moved = [e for e in engine.world.bus.history if e.kind == "entity_moved"]
        self.assertGreater(len(moved), 0, "一整天运转后应至少有一次 NPC 位置移动")

    def test_tick_publishes_entity_moved_events(self):
        """运行到 SLEEPING 时间点后，bus.history 中有 entity_moved 事件。"""
        engine = self.engine
        engine.world.game_minute = 20 * 60
        engine.tick(10)  # hour=20，多 NPC 进入 SLEEPING 并移动到居所
        moved = [e for e in engine.world.bus.history if e.kind == "entity_moved"]
        self.assertGreater(len(moved), 0, "SLEEPING 时间点应发布 entity_moved 事件")

    def test_village_operation_has_npc_actions(self):
        """24 次 tick(60) 后，bus.history 中有 npc_action 事件（背景 NPC 规则反应）。

        确定性路径：次日 7:00 lily 移回 market，而 old_zhang 已于 6:00 先到 market，
        old_zhang 对 lily 的 entity_moved 事件触发规则反应，发布 npc_action。
        """
        engine = self.engine
        for _ in range(24):
            engine.tick(60)
        actions = [e for e in engine.world.bus.history if e.kind == "npc_action"]
        self.assertGreater(
            len(actions), 0, "全天运转后背景 NPC 应有 npc_action 规则反应")

    def test_status_covers_all_locations_after_ticks(self):
        """24 次 tick(60) 后，status 的 locations 数量等于 world.locations 数量。"""
        engine = self.engine
        for _ in range(24):
            engine.tick(60)
        status = engine.status()
        self.assertEqual(
            len(status["world"]["locations"]), len(engine.world.locations))

    def test_schedule_applied_includes_background_npcs(self):
        """tick 到有 schedule 条目的小时，schedule_applied 同时包含核心与背景 NPC。

        核心 NPC 的 value 为中文状态名（NPCState.value），背景 NPC 的 value 为
        英文状态名（schedule 原始字符串），二者在 schedule_applied 中并存。
        """
        engine = self.engine
        engine.world.game_minute = 20 * 60
        result = engine.tick(10)
        applied = result["schedule_applied"]
        # 核心 NPC chen：value 为中文状态名（NPCState.SLEEPING.value == "睡觉"）
        self.assertIn("chen", applied)
        self.assertEqual(applied["chen"], "睡觉")
        # 背景 NPC baker_liu：value 为英文状态名
        self.assertIn("baker_liu", applied)
        self.assertEqual(applied["baker_liu"], "SLEEPING")


class TestTokenBaselineAfterOperation(unittest.TestCase):
    """token 基线对比：全天运转后核心对话 token 在基线范围、背景 NPC 零 token。"""

    def setUp(self):
        self.engine = NPCEngine(llm=MockLLMProvider())

    def test_core_dialogue_token_in_baseline_range(self):
        """全天运转后 4 次核心对话 token 在 4400-4800，调用次数为 4。

        流程：24 次 tick(60) 模拟一整天 → 时间回到 8:00、NPC 在工作地 WORKING
        → 4 次核心对话（2 chen + 2 lily，均命中 Mock 话题库）。
        基线约 4435，运转产生的少量移动记忆仅轻微影响 recent(4) 上下文（上下文裁剪）。
        """
        engine = self.engine
        for _ in range(24):
            engine.tick(60)
        # 此时时间回到 8:00，NPC 在工作地
        engine.player_says("铁匠，打把剑", "chen")
        engine.player_says("有好铁矿石吗", "chen")
        engine.player_says("有新货吗", "lily")
        engine.player_says("有什么消息", "lily")
        self.assertGreaterEqual(engine.llm.total_tokens_used, 4400)
        self.assertLessEqual(engine.llm.total_tokens_used, 4800)
        self.assertEqual(engine.llm.call_count, 4)

    def test_background_zero_token_after_full_operation(self):
        """全天运转后背景 NPC 零 LLM 调用、零 token 消耗。

        tick 仅应用作息（规则路径，不调用 LLM），背景 NPC 架构上不持有 LLM 引用。
        """
        engine = self.engine
        for _ in range(24):
            engine.tick(60)
        self.assertEqual(engine.llm.call_count, 0)
        self.assertEqual(engine.llm.total_tokens_used, 0)


if __name__ == "__main__":
    unittest.main()

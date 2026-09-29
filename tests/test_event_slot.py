"""G3 随机事件槽测试：EventSlot 累积触发 + 环境事件发布与 NPC 感知。

覆盖四层：
1. ``EventSlot`` 单元行为（累积、触发、进度、归零、多次触发）；
2. ``World`` 集成（tick 自动累积、环境事件确定性轮播、自定义事件池）；
3. NPC 感知规则（自己的事件必可闻、同地点可闻、异地听不见）；
4. ``NPCEngine`` 端到端（player_says 推进事件槽直至环境事件回流）。
"""

import unittest

from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.npc import Persona
from engine.world import EventSlot, World

# World 默认环境事件池（engine/world.py 中的契约顺序）
DEFAULT_POOL = [
    {"actor": "chen", "summary": "铁匠陈想起该去收矿石了"},
    {"actor": "lily", "summary": "莉莉盘算着新货的报价"},
    {"actor": "chen", "summary": "铁匠陈觉得炉火该添炭了"},
    {"actor": "lily", "summary": "莉莉在整理货架上的商品"},
]


def make_engine():
    """两 NPC 引擎：chen@forge（铁匠铺）、lily@market（集市）。"""
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


def env_events_in(bus):
    """从事件总线历史中筛出全部环境事件。"""
    return [e for e in bus.history if e.kind == "env_event"]


def short_contents(npc):
    """NPC 短期记忆的全部文本。"""
    return [r.content for r in npc.memory.short.recent()]


class TestEventSlot(unittest.TestCase):
    """EventSlot 单元测试：累积进度、触发归零、计数递增。"""

    def test_initial_state(self):
        slot = EventSlot()
        self.assertEqual(slot.step, EventSlot.DEFAULT_STEP)
        self.assertEqual(slot.threshold, EventSlot.DEFAULT_THRESHOLD)
        self.assertEqual(slot.accumulation, 0.0)
        self.assertEqual(slot.trigger_count, 0)
        self.assertEqual(slot.progress, 0.0)

    def test_accumulate_no_trigger(self):
        slot = EventSlot()
        self.assertFalse(slot.accumulate())
        self.assertAlmostEqual(slot.accumulation, 0.05)
        self.assertEqual(slot.trigger_count, 0)

    def test_accumulate_triggers(self):
        slot = EventSlot()  # 0.05 * 20 = 1.0，恰好触发
        results = [slot.accumulate() for _ in range(20)]
        self.assertFalse(any(results[:19]))  # 前 19 次都不触发
        self.assertTrue(results[-1])        # 第 20 次触发
        self.assertEqual(slot.accumulation, 0.0)
        self.assertEqual(slot.trigger_count, 1)

    def test_custom_step_threshold(self):
        slot = EventSlot(step=0.1, threshold=0.5)  # 0.1 * 5 = 0.5
        results = [slot.accumulate() for _ in range(5)]
        self.assertFalse(any(results[:4]))
        self.assertTrue(results[-1])
        self.assertEqual(slot.trigger_count, 1)

    def test_accumulate_custom_amount(self):
        slot = EventSlot()  # threshold=1.0
        self.assertFalse(slot.accumulate(0.5))
        self.assertAlmostEqual(slot.accumulation, 0.5)
        self.assertTrue(slot.accumulate(0.5))
        self.assertEqual(slot.trigger_count, 1)

    def test_progress(self):
        slot = EventSlot(step=0.1, threshold=0.5)
        slot.accumulate()
        slot.accumulate()
        self.assertAlmostEqual(slot.progress, 0.4)  # 0.2 / 0.5
        # 进度值域 clamp 到 [0, 1]
        slot.accumulation = 2.0
        self.assertEqual(slot.progress, 1.0)
        slot.accumulation = -0.5
        self.assertEqual(slot.progress, 0.0)

    def test_multiple_triggers(self):
        slot = EventSlot(step=0.5, threshold=1.0)
        # 两次累积触发一次，连续触发计数递增
        self.assertFalse(slot.accumulate())
        self.assertTrue(slot.accumulate())
        self.assertEqual(slot.trigger_count, 1)
        self.assertFalse(slot.accumulate())
        self.assertTrue(slot.accumulate())
        self.assertEqual(slot.trigger_count, 2)

    def test_reset_on_trigger(self):
        slot = EventSlot(step=0.5, threshold=1.0)
        slot.accumulate()
        self.assertTrue(slot.accumulate())
        self.assertEqual(slot.accumulation, 0.0)  # 触发后归零
        # 归零后重新从 0 开始累积
        self.assertFalse(slot.accumulate())
        self.assertAlmostEqual(slot.accumulation, 0.5)


class TestWorldEventSlot(unittest.TestCase):
    """World 集成测试：tick 推进事件槽、环境事件确定性轮播。"""

    def test_tick_accumulates_slot(self):
        world = World()
        world.tick()
        self.assertGreater(world.event_slot.accumulation, 0)

    def test_tick_triggers_env_event(self):
        world = World()
        for _ in range(20):  # 20 * 0.05 = 1.0，恰好触发一次
            world.tick()
        events = env_events_in(world.bus)
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0].actor, "chen")

    def test_env_event_deterministic(self):
        world_a = World()
        world_b = World()
        for _ in range(20):
            world_a.tick()
            world_b.tick()
        summary_a = env_events_in(world_a.bus)[0].payload["summary"]
        summary_b = env_events_in(world_b.bus)[0].payload["summary"]
        self.assertEqual(summary_a, summary_b)  # 同种子同行为，确定性
        self.assertEqual(summary_a, DEFAULT_POOL[0]["summary"])

    def test_env_event_cycles_through_pool(self):
        world = World()
        world.event_slot = EventSlot(step=0.05, threshold=0.05)  # 每次 tick 必触发
        for _ in range(5):
            world.tick()
        summaries = [e.payload["summary"] for e in env_events_in(world.bus)]
        self.assertEqual(len(summaries), 5)
        # 按事件池顺序轮播，第 5 次回到池首
        expected = [d["summary"] for d in DEFAULT_POOL]
        self.assertEqual(summaries, expected + [expected[0]])

    def test_accumulate_event_slot_method(self):
        world = World()
        self.assertFalse(world.accumulate_event_slot(0.5))
        self.assertEqual(len(env_events_in(world.bus)), 0)
        self.assertTrue(world.accumulate_event_slot(0.5))
        events = env_events_in(world.bus)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "env_event")

    def test_custom_env_events(self):
        world = World(env_events=[{"actor": "chen", "summary": "测试事件"}])
        self.assertTrue(world.accumulate_event_slot(1.0))
        events = env_events_in(world.bus)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].actor, "chen")
        self.assertEqual(events[0].payload["summary"], "测试事件")


class TestEnvEventPerception(unittest.TestCase):
    """NPC 感知规则：自己的事件必可闻、同地点可闻、异地听不见。"""

    def setUp(self):
        self.engine = make_engine()

    def test_npc_perceives_own_env_event(self):
        self.engine.world.accumulate_event_slot(1.0)  # 首个事件 actor="chen"
        chen = self.engine.npcs["chen"]
        self.assertTrue(any("铁匠陈想起该去收矿石了" in c
                            for c in short_contents(chen)))
        # importance=0.5 低于 0.7 阈值：只进短期记忆，不沉淀长期
        self.assertFalse(any("铁匠陈想起该去收矿石了" in r.content
                             for r in chen.memory.long.records))

    def test_same_location_npc_perceives(self):
        # 额外生成一个与 chen 同在 forge 的守卫
        self.engine.spawn(Persona(id="guard", name="守卫", role="guard",
                                  location_id="forge", schedule={}))
        self.engine.world.accumulate_event_slot(1.0)  # actor="chen" 的事件
        for npc_id in ("chen", "guard"):
            npc = self.engine.npcs[npc_id]
            self.assertTrue(any("铁匠陈想起该去收矿石了" in c
                                for c in short_contents(npc)),
                            msg=f"{npc_id} 应感知同地点的环境事件")

    def test_different_location_npc_does_not_perceive(self):
        self.engine.world.accumulate_event_slot(1.0)  # chen@forge 的事件
        chen = self.engine.npcs["chen"]
        lily = self.engine.npcs["lily"]  # lily@market，异地听不见
        # 对照：事件确实发生了，chen 感知到
        self.assertTrue(any("铁匠陈想起该去收矿石了" in c
                            for c in short_contents(chen)))
        self.assertFalse(any("铁匠陈想起该去收矿石了" in c
                              for c in short_contents(lily)))


class TestEngineIntegration(unittest.TestCase):
    """引擎端到端：对话推进世界，事件槽累积直至环境事件回流。"""

    def setUp(self):
        self.engine = make_engine()

    def test_player_says_accumulates_slot(self):
        before = self.engine.world.event_slot.accumulation
        self.engine.player_says("最近生意怎么样？", "lily")
        slot = self.engine.world.event_slot
        self.assertGreater(slot.accumulation, before)
        self.assertEqual(slot.trigger_count, 0)  # 一次对话尚不满槽

    def test_player_says_multiple_triggers_event(self):
        self.engine.world.event_slot = EventSlot(threshold=0.1)  # 2 次对话触发
        self.engine.player_says("帮我打一把剑", "chen")
        self.assertEqual(len(env_events_in(self.engine.world.bus)), 0)
        self.engine.player_says("剑要多少钱？", "chen")
        events = env_events_in(self.engine.world.bus)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["summary"],
                         DEFAULT_POOL[0]["summary"])
        # 环境事件回流对话 NPC 的记忆（chen 自己的事件必可闻）
        chen = self.engine.npcs["chen"]
        self.assertTrue(any("铁匠陈想起该去收矿石了" in c
                            for c in short_contents(chen)))


if __name__ == "__main__":
    unittest.main()

"""世界层测试：事件总线、时间推进、快照。"""

import unittest

from engine.world import Entity, EventBus, Location, Weather, World, WorldEvent


class TestEventBus(unittest.TestCase):
    def test_pub_sub_by_kind(self):
        bus = EventBus()
        got = []
        bus.subscribe(lambda e: got.append(e), kinds=["player_spoke"])
        bus.publish(WorldEvent(1, "time_passed", "world", {}))
        bus.publish(WorldEvent(2, "player_spoke", "player", {"text": "hi"}))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].payload["text"], "hi")

    def test_catch_all_receives_everything(self):
        bus = EventBus()
        got = []
        bus.subscribe(got.append)
        bus.publish(WorldEvent(1, "a", "x"))
        bus.publish(WorldEvent(2, "b", "y"))
        self.assertEqual(len(got), 2)

    def test_history_ring_buffer(self):
        bus = EventBus(history_size=3)
        for i in range(5):
            bus.publish(WorldEvent(i, "tick", "world"))
        self.assertEqual(len(bus.history), 3)
        self.assertEqual(bus.history[-1].tick, 4)


class TestWorld(unittest.TestCase):
    def _world(self):
        world = World(locations=[Location("a", "甲地", 0, 0), Location("b", "乙地", 1, 1)])
        world.add_entity(Entity("player", "player", "旅行者", "a", ["金币"]), announce=False)
        world.add_entity(Entity("npc1", "npc", "铁匠陈", "b", ["锤子"]), announce=False)
        return world

    def test_tick_advances_time(self):
        world = self._world()
        start = world.game_minute
        world.tick(30)
        self.assertEqual(world.game_minute, (start + 30) % 1440)
        self.assertEqual(world.tick_count, 1)

    def test_move_entity_publishes_event(self):
        world = self._world()
        events = []
        world.bus.subscribe(events.append, kinds=["entity_moved"])
        self.assertTrue(world.move_entity("player", "b"))
        self.assertFalse(world.move_entity("player", "nowhere"))
        self.assertEqual(len(events), 1)

    def test_transfer_item(self):
        world = self._world()
        self.assertFalse(world.transfer_item("npc1", "player", "不存在的东西"))
        self.assertTrue(world.transfer_item("npc1", "player", "锤子"))
        self.assertIn("锤子", world.entities["player"].inventory)

    def test_snapshot_scoped_to_viewer_location(self):
        world = self._world()
        snap = world.snapshot("npc1")
        names = [e["id"] for e in snap["nearby"]]
        self.assertNotIn("npc1", names)  # 不含自己
        self.assertEqual(snap["my_location"], "乙地")

    def test_weather_change_publishes_event(self):
        world = self._world()
        world.weather = Weather.RAIN
        events = []
        world.bus.subscribe(events.append, kinds=["weather_changed"])
        # 概率事件：把概率拉满来验证通路
        import engine.world as w
        old = w.WEATHER_CHANGE_PROB
        w.WEATHER_CHANGE_PROB = 1.0
        try:
            world.tick()
        finally:
            w.WEATHER_CHANGE_PROB = old
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()

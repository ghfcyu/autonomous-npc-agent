"""背景 NPC 反应同 hour 判重测试（第十二次审查观察项落地）。

被测契约（PM 裁定口径，engine/background_npc.py _on_event）：
- 判重键 (hour, event.kind)：同 hour 内同一事件类型至多发布一条
  npc_action 反应，消除复读式连发（farmer_zhao tick11 三连复读）；
- 判重检查在位置过滤之后：别处事件不消耗本 hour 配额；
- 跨 hour 自动恢复反应资格（hour 不等即判新，无需显式重置）；
- 不同事件类型互不共享配额（env_event 与 entity_moved 各自计数）；
- 判重只作用于 npc_action 发布，不影响 EnvMind 演化与变体后缀。

hour 口径与 EnvMind 演化层一致：engine.tick 经
``apply_schedule(self.world.hour)`` 传入演化，反应判重同取
``self._world.hour``（game_minute // 60）。

搭建先例：事件注入沿 tests/test_event_slot.py 的 bus.publish 手动
注入；hour 推进沿 tests/test_schedule.py 的 world.game_minute 直设；
配置 dict 沿 tests/test_background_npc.py 的 G4 契约结构。
纯标准库 + 项目内 engine 模块，零第三方依赖。
"""

import unittest

from engine.background_npc import BackgroundNPC
from engine.village_genesis import EnvMind
from engine.world import Entity, World, WorldEvent


def bg_config(npc_id, name, role, location_id, summary, reactions):
    """背景 NPC 配置 dict（沿 test_background_npc.py 的 G4 契约结构）。"""
    return {"id": npc_id, "name": name, "role": role,
            "location_id": location_id, "summary": summary,
            "reactions": reactions}


def make_stage(env_mind=None):
    """判重舞台：碎嘴闲人 gossip@forge 反应同地 chen@forge 的 env_event。

    World 默认 8:00 起（hour=8），全程不动 game_minute 即同 hour；
    事件源 chen 注册为实体（未注册实体的 actor 位置过滤会放行，
    注册后位置过滤行为确定）。env_mind 注入可选用（变体回归用例）。
    """
    world = World()
    world.add_entity(Entity(id="chen", kind="npc", name="铁匠陈",
                            location_id="forge"), announce=False)
    npc = BackgroundNPC(bg_config(
        "gossip", "碎嘴闲人", "idler", "forge", "爱搭话的路人",
        {"env_event": ["{npc_name} 摇着头小声说：{summary}",
                       "{npc_name} 竖起了耳朵：{summary}"]}),
        world, env_mind=env_mind)
    return world, npc


def publish_env_event(world, actor, summary):
    """手动注入一条 env_event（沿 test_event_slot.py bus.publish 先例）。"""
    world.bus.publish(WorldEvent(world.tick_count, "env_event", actor,
                                  {"summary": summary}))


def reactions_of(world, npc_id):
    """该背景 NPC 发布的全部 npc_action（玩家可感知反应出口）。"""
    return [e for e in world.bus.history
            if e.kind == "npc_action" and e.actor == npc_id]


class TestReactionDedup(unittest.TestCase):
    """同 hour 判重：复读消失 / 跨 hour 恢复 / per-kind 配额 / 位置过滤优先。"""

    def test_same_hour_same_kind_reacts_once(self):
        """用例 1：同 hour 连投 3 条同地同类事件，玩家只看见 1 条反应。"""
        world, npc = make_stage()
        for i in range(3):
            publish_env_event(world, "chen", f"第{i}声吆喝")
        actions = reactions_of(world, "gossip")
        self.assertEqual(len(actions), 1,
                         "同 hour 内同类事件应只发布一条 npc_action（防复读）")
        # 首条反应对第一条事件负责（配额由首个通过过滤的事件锁定）
        self.assertIn("第0声吆喝", actions[0].payload["summary"])
        self.assertIn("碎嘴闲人", actions[0].payload["summary"])

    def test_next_hour_reacts_again(self):
        """用例 2：跨 hour 后同类事件恢复反应资格，且模板轮转照常推进。"""
        world, npc = make_stage()
        publish_env_event(world, "chen", "晨间的吆喝")
        world.game_minute += 60          # 8 点 → 9 点（hour 口径同演化层）
        publish_env_event(world, "chen", "晌午的吆喝")
        actions = reactions_of(world, "gossip")
        self.assertEqual(len(actions), 2,
                         "跨 hour 后同类事件应获得新的反应资格")
        texts = [a.payload["summary"] for a in actions]
        self.assertIn("摇着头小声说", texts[0])
        self.assertIn("晨间的吆喝", texts[0])
        self.assertIn("竖起了耳朵", texts[1],
                      "第二条反应应轮转到模板 2（判重跳过的事件不吞轮转计数）")
        self.assertIn("晌午的吆喝", texts[1])

    def test_different_kinds_hold_independent_quota(self):
        """用例 3：同 hour 内 env_event 反应过不占 entity_moved 的配额。"""
        world = World()
        world.add_entity(Entity(id="chen", kind="npc", name="铁匠陈",
                                location_id="plaza"), announce=False)
        world.add_entity(Entity(id="player", kind="player", name="玩家",
                                location_id="market"), announce=False)
        BackgroundNPC(bg_config(
            "guard_wang", "守卫王", "guard", "plaza", "广场守卫",
            {"env_event": ["{npc_name} 手按剑柄扫视四周：{summary}"],
             "entity_moved": ["{npc_name} 朝走过来的方向点了点头"]}), world)
        publish_env_event(world, "chen", "广场上起了骚动")
        world.move_entity("player", "plaza")   # 同 hour 的抵达事件
        actions = reactions_of(world, "guard_wang")
        self.assertEqual(len(actions), 2,
                         "同 hour 内 env_event 与 entity_moved 应各反应一次"
                         "（判重按事件类型分别计数）")
        self.assertIn("手按剑柄", actions[0].payload["summary"])
        self.assertIn("点了点头", actions[1].payload["summary"])
        # 反证 per-kind：再投一条同类 env_event 已无配额，总反应数不变
        publish_env_event(world, "chen", "广场上又起了骚动")
        self.assertEqual(len(reactions_of(world, "guard_wang")), 2,
                         "env_event 配额耗尽后同 hour 再投同类事件不应新增反应")

    def test_out_of_location_event_consumes_no_quota(self):
        """用例 4：位置过滤优先于判重——别处事件不消耗本 hour 配额。"""
        world = World()
        world.add_entity(Entity(id="chen", kind="npc", name="铁匠陈",
                                location_id="forge"), announce=False)
        world.add_entity(Entity(id="lily", kind="npc", name="商人莉莉",
                                location_id="market"), announce=False)
        BackgroundNPC(bg_config(
            "gossip", "碎嘴闲人", "idler", "forge", "爱搭话的路人",
            {"env_event": ["{npc_name} 摇着头小声说：{summary}"]}), world)
        publish_env_event(world, "lily", "集市那头的动静")   # 异地：位置过滤拦下
        self.assertEqual(reactions_of(world, "gossip"), [],
                         "异地事件本就不应触发反应")
        publish_env_event(world, "chen", "铺子门口的动静")     # 同地：仍可反应
        actions = reactions_of(world, "gossip")
        self.assertEqual(len(actions), 1,
                         "异地事件不应消耗本 hour 同类配额（判重在位置过滤之后）")
        self.assertIn("铺子门口的动静", actions[0].payload["summary"])
        publish_env_event(world, "chen", "又一桩动静")         # 配额已耗：不再反应
        self.assertEqual(len(reactions_of(world, "gossip")), 1,
                         "同地同 hour 第二条同类事件应被判重拦下")

    def test_variant_suffix_survives_dedup(self):
        """用例 5（回归）：判重不影响 EnvMind 变体后缀——两个 hour 的反应
        文案均带高疲劳『哈欠』后缀，变体逻辑与判重正交。"""
        world, npc = make_stage(env_mind=EnvMind(0.8, 0.2, 0.2, 0.2))
        publish_env_event(world, "chen", "晨间的吆喝")
        world.game_minute += 60
        publish_env_event(world, "chen", "晌午的吆喝")
        texts = [a.payload["summary"] for a in reactions_of(world, npc.id)]
        self.assertEqual(len(texts), 2)
        self.assertIn("晨间的吆喝", texts[0])
        self.assertIn("晌午的吆喝", texts[1])
        for text in texts:
            self.assertIn("碎嘴闲人", text,
                          "变体后缀应叠加在既有反应文本上（模板先格式化）")
            self.assertIn("哈欠", text,
                          "高疲劳状态带下每个 hour 的反应文案均应带变体后缀"
                          "（判重不触碰 _variant_suffix 路径）")


if __name__ == "__main__":
    unittest.main()

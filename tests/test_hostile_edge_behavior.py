"""T4-c 敌意边行为出口测试：背景 NPC 规则反应消费敌意关系边。

村庄冤家关系玩家可感知（渔夫与磨坊主互哼）的行为契约：
- 命中条件：注入关系网 + 事件 actor 非空非玩家 + self→actor 存在
  affinity<0 的关系边 → 反应文案前拼敌意前缀；
- 社会层优先：敌意前缀与 EnvMind 生理层后缀同时命中时只取前缀，
  不叠加（至多一条纪律）；
- 零 LLM：敌意变体纯文案拼接，Tier 0/1 封印不破；
- 向后兼容：不传 relationships / 显式传 None / 敌意边未命中事件
  actor 时，文案与旧版逐字一致（含 EnvMind 后缀路径）；
- 判重兼容：敌意变体在判重之后的文案层，同 hour 同类事件仍只发
  一条 npc_action（10-13 已落地判重不受影响）。

用例 a-g 全部自构造小场景（显式 background_configs 最小背景 NPC
dict + 手工 RelationshipNetwork + MockLLMProvider），不依赖默认
村庄构造与 23/22 背景口径（并行改造不互扰）；用例 h 为默认村庄
端到端：configs/relationships.json 真实敌意对 fisher_zhou↔
miller_qian（冤家 -0.55 / -0.6 双向），miller_qian 的事件带到
fisher_zhou 面前 → 反应带敌意前缀。
"""

import unittest

from engine.background_npc import HOSTILE_PREFIXES, BackgroundNPC
from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.relationships import Relationship, RelationshipNetwork
from engine.village_genesis import EnvMind
from engine.world import Entity, Location, World, WorldEvent

# 自定义地点 id：add_entity 不校验地点表，位置过滤纯字符串比较，
# 与 configs/locations.json（并行改造区）零依赖。
DOCK = "dock"


def bg_config(npc_id, name, reactions=None):
    """最小背景 NPC 配置 dict（缺省含单条 env_event 反应模板）。"""
    return {"id": npc_id, "name": name, "role": "villager",
            "location_id": DOCK, "summary": f"{name}，测试用背景住民",
            "reactions": reactions if reactions is not None else {
                "env_event": ["{npc_name} 应了一声：{summary}"]}}


def make_net(*edges):
    """手工关系网：edges 为 (source_id, target_id, relation, affinity)。"""
    return RelationshipNetwork([
        Relationship(source_id=s, target_id=t, relation=r, affinity=a)
        for s, t, r, a in edges])


def make_world():
    """单地点最小世界（自定义 locations + 占位事件池，不碰默认口径）。"""
    return World(locations=[Location(DOCK, "码头", 0, 0, "work")],
                 env_events=[{"actor": "none", "summary": "占位"}])


def make_engine(backgrounds, relationships):
    """混编引擎小场景：零核心 NPC + 显式背景配置 + 显式关系网。"""
    llm = MockLLMProvider()
    engine = NPCEngine(llm=llm, npc_configs=[], background_configs=backgrounds,
                       relationships=relationships)
    return engine, llm


def spawn_actor(world, actor_id):
    """注册事件 actor 实体到背景 NPC 同地点（位置过滤通过）。"""
    world.add_entity(Entity(id=actor_id, kind="npc", name=actor_id,
                            location_id=DOCK), announce=False)


def fire_env_event(world, actor_id, summary="有人在码头大声说话"):
    """直接向总线发布一条 env_event（不依赖事件槽随机机制）。"""
    world.bus.publish(WorldEvent(world.tick_count, "env_event",
                                 actor_id, {"summary": summary}))


def actions_of(world, npc_id):
    """事件历史中某背景 NPC 产出的全部 npc_action。"""
    return [e for e in world.bus.history
            if e.kind == "npc_action" and e.actor == npc_id]


def has_prefix(text):
    """文案是否以任一敌意前缀开头。"""
    return any(text.startswith(p) for p in HOSTILE_PREFIXES)


class TestHostileEdgeBehavior(unittest.TestCase):
    """敌意关系边 → 背景 NPC 规则反应文案变体（零 LLM 社会层出口）。"""

    def test_hostile_actor_prefix_neutral_actor_plain(self):
        """a) 敌意分叉：对敌意 actor 反应带前缀，对非敌意 actor 不带。"""
        net = make_net(("npc_a", "npc_b", "冤家", -0.6))
        engine, llm = make_engine([bg_config("npc_a", "阿甲")], net)
        spawn_actor(engine.world, "npc_b")
        spawn_actor(engine.world, "npc_c")
        fire_env_event(engine.world, "npc_b", "钱掌柜在码头晃悠")
        acts = actions_of(engine.world, "npc_a")
        self.assertEqual(len(acts), 1)
        text = acts[0].payload["summary"]
        # counter=0 确定性首项：前缀池第 1 条拼在模板文案前
        self.assertTrue(text.startswith(HOSTILE_PREFIXES[0]))
        self.assertIn("阿甲 应了一声", text)
        self.assertIn("钱掌柜在码头晃悠", text)
        # 对照组（跨 hour 解锁判重）：非敌意 actor 同类事件无前缀
        engine.world.game_minute += 60
        fire_env_event(engine.world, "npc_c", "货郎挑担路过")
        acts = actions_of(engine.world, "npc_a")
        self.assertEqual(len(acts), 2)
        plain = acts[1].payload["summary"]
        self.assertFalse(has_prefix(plain))
        self.assertIn("货郎挑担路过", plain)
        self.assertEqual(llm.call_count, 0)

    def test_bidirectional_hostile_pair(self):
        """b) 双向敌意对：A 对 B、B 对 A 的事件反应都带前缀。"""
        net = make_net(("npc_a", "npc_b", "冤家", -0.6),
                       ("npc_b", "npc_a", "冤家", -0.55))
        engine, llm = make_engine([bg_config("npc_a", "阿甲"),
                                   bg_config("npc_b", "老钱")], net)
        # 同 hour 两条事件：各对对方的动作反应一次（各自判重独立，
        # 自身事件 self-check 最先跳过、不消耗判重配额）
        fire_env_event(engine.world, "npc_b", "老钱嘟囔了几句")
        fire_env_event(engine.world, "npc_a", "阿甲剁了下鱼")
        a_acts = actions_of(engine.world, "npc_a")
        b_acts = actions_of(engine.world, "npc_b")
        self.assertEqual(len(a_acts), 1)
        self.assertEqual(len(b_acts), 1)
        # 双方 counter 均首次 → 均为前缀池首项（确定性）
        self.assertTrue(a_acts[0].payload["summary"].startswith(HOSTILE_PREFIXES[0]))
        self.assertTrue(b_acts[0].payload["summary"].startswith(HOSTILE_PREFIXES[0]))
        self.assertEqual(llm.call_count, 0)

    def test_hostile_reactions_cost_zero_llm(self):
        """c) 零 LLM 维持：多次敌意反应全程 provider 零调用，前缀确定性轮转。"""
        net = make_net(("npc_a", "npc_b", "冤家", -0.6))
        engine, llm = make_engine([bg_config("npc_a", "阿甲")], net)
        spawn_actor(engine.world, "npc_b")
        for i in range(3):
            fire_env_event(engine.world, "npc_b", f"老钱第{i}次路过")
            engine.world.game_minute += 60  # 跨 hour 逐次解锁判重
        acts = actions_of(engine.world, "npc_a")
        self.assertEqual(len(acts), 3)
        texts = [e.payload["summary"] for e in acts]
        # 前缀随既有计数器取模轮转（与模板轮换同构）：0 → 1 → 2
        self.assertTrue(texts[0].startswith(HOSTILE_PREFIXES[0]))
        self.assertTrue(texts[1].startswith(HOSTILE_PREFIXES[1]))
        self.assertTrue(texts[2].startswith(HOSTILE_PREFIXES[2]))
        self.assertEqual(llm.call_count, 0)
        self.assertEqual(llm.call_log, [])

    def test_relationships_none_backward_compatible(self):
        """d) 向后兼容：不传/显式 None/敌意网未命中 actor 时文案逐字一致。"""
        tired = EnvMind(p_fatigue=0.9, p_hunger=0.1, e_P=0.5, S_stress=0.1)
        net = make_net(("npc_a", "npc_b", "冤家", -0.6))  # 有敌意边但事件 actor 是 c

        def scenario(mode):
            world = make_world()
            spawn_actor(world, "npc_c")
            cfg = bg_config("npc_a", "阿甲")
            if mode == "default":  # 旧版形态：不传 relationships
                BackgroundNPC(cfg, world, env_mind=tired)
            elif mode == "none":    # 显式 None
                BackgroundNPC(cfg, world, env_mind=tired, relationships=None)
            else:                   # 传敌意网但 actor 非敌意对象
                BackgroundNPC(cfg, world, env_mind=tired, relationships=net)
            fire_env_event(world, "npc_c", "货郎挑担路过")
            return actions_of(world, "npc_a")[0].payload["summary"]

        # 三种形态逐字一致：模板正文 + EnvMind 疲惫后缀，敌意边不殃及
        # 无辜 actor（敌意检查按事件 actor 查询而非"网上有敌意就触发"）
        expect = "阿甲 应了一声：货郎挑担路过，一边打了个哈欠"
        self.assertEqual(scenario("default"), expect)
        self.assertEqual(scenario("none"), expect)
        self.assertEqual(scenario("net"), expect)

    def test_dedup_unchanged_with_hostile_variant(self):
        """e) 判重兼容：同 hour 同类事件仍只发 1 条；跨 hour 恢复。"""
        net = make_net(("npc_a", "npc_b", "冤家", -0.6))
        engine, llm = make_engine([bg_config("npc_a", "阿甲")], net)
        spawn_actor(engine.world, "npc_b")
        fire_env_event(engine.world, "npc_b", "老钱头一次路过")
        fire_env_event(engine.world, "npc_b", "老钱第二次路过")  # 同 hour 判重
        self.assertEqual(len(actions_of(engine.world, "npc_a")), 1)
        engine.world.game_minute += 60  # 跨 hour 自动恢复反应资格
        fire_env_event(engine.world, "npc_b", "老钱第三次路过")
        acts = actions_of(engine.world, "npc_a")
        self.assertEqual(len(acts), 2)
        # 前缀随计数轮转（第二次反应 counter=1）：冷哼 → 别过脸
        self.assertTrue(acts[0].payload["summary"].startswith(HOSTILE_PREFIXES[0]))
        self.assertTrue(acts[1].payload["summary"].startswith(HOSTILE_PREFIXES[1]))
        self.assertEqual(llm.call_count, 0)

    def test_hostile_prefix_overrides_env_mind_suffix(self):
        """f) 敌意前缀优先于 EnvMind 后缀：命中敌意时无疲惫后缀。"""
        tired = EnvMind(p_fatigue=0.9, p_hunger=0.1, e_P=0.5, S_stress=0.1)
        net = make_net(("npc_a", "npc_b", "冤家", -0.6))
        world = make_world()
        spawn_actor(world, "npc_b")
        spawn_actor(world, "npc_c")
        BackgroundNPC(bg_config("npc_a", "阿甲"), world,
                      env_mind=tired, relationships=net)
        fire_env_event(world, "npc_b", "老钱在码头转悠")
        acts = actions_of(world, "npc_a")
        self.assertEqual(len(acts), 1)
        hostile_text = acts[0].payload["summary"]
        self.assertTrue(hostile_text.startswith(HOSTILE_PREFIXES[0]))
        # 同时命中只取敌意前缀：EnvMind 疲惫后缀被顶掉，不叠加
        self.assertNotIn("打了个哈欠", hostile_text)
        self.assertNotIn("咕咕叫", hostile_text)
        # 对照（跨 hour，非敌意 actor）：EnvMind 后缀路径完整存活
        world.game_minute += 60
        fire_env_event(world, "npc_c", "货郎挑担路过")
        acts = actions_of(world, "npc_a")
        self.assertEqual(len(acts), 2)
        plain = acts[1].payload["summary"]
        self.assertFalse(has_prefix(plain))
        self.assertIn("打了个哈欠", plain)

    def test_non_hostile_edges_no_prefix(self):
        """g) 亲缘/中性边不触发：affinity=0.6 与 0.0 均无前缀。"""
        net = make_net(("npc_a", "npc_c", "老友", 0.6),
                       ("npc_a", "npc_d", "旧识", 0.0))
        engine, llm = make_engine([bg_config("npc_a", "阿甲")], net)
        spawn_actor(engine.world, "npc_c")
        spawn_actor(engine.world, "npc_d")
        fire_env_event(engine.world, "npc_c", "货郎笑呵呵路过")
        engine.world.game_minute += 60
        fire_env_event(engine.world, "npc_d", "货郎挑担路过")
        acts = actions_of(engine.world, "npc_a")
        self.assertEqual(len(acts), 2)
        for e in acts:
            self.assertFalse(has_prefix(e.payload["summary"]))
        self.assertEqual(llm.call_count, 0)

    def test_default_village_fisher_miller_hostile_end_to_end(self):
        """h) 默认村庄端到端：miller_qian 的事件带到 fisher_zhou 面前 → 敌意前缀。

        configs/relationships.json 真实敌意对（双向）：
        fisher_zhou→miller_qian 冤家 -0.55；miller_qian→fisher_zhou 冤家 -0.6。
        """
        engine = NPCEngine(llm=MockLLMProvider())
        fisher = engine.background_npcs["fisher_zhou"]
        self.assertIn("miller_qian", engine.world.entities)
        # 冤家来到渔夫地盘（同地点，位置过滤通过）
        self.assertTrue(
            engine.world.move_entity("miller_qian", fisher.entity.location_id))
        summary = "钱多福在河堤上踱步"
        engine.world.bus.publish(WorldEvent(
            engine.world.tick_count, "env_event", "miller_qian",
            {"summary": summary}))
        fisher_acts = actions_of(engine.world, "fisher_zhou")
        # 精确锁定 env_event 那条反应（entity_moved 反应不含本 summary）
        env_reacts = [e for e in fisher_acts if summary in e.payload["summary"]]
        self.assertEqual(len(env_reacts), 1)
        self.assertTrue(has_prefix(env_reacts[0].payload["summary"]))
        # 端到端全程零 LLM：敌意变体纯文案拼接，Tier 0/1 封印不破
        self.assertEqual(engine.llm.call_count, 0)


if __name__ == "__main__":
    unittest.main()

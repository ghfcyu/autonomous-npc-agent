"""NPC 间社交回路验收测试（第十四次审查指令 2：活村庄社会生态最小闭环）。

被测契约（engine/socialize.py，纯规则 0-token，Tier 0/1 封印层）：

- 五层闸门（apply(hour) 每 tick 一次，全过才互动）：亲和（affinity>=0，
  敌意边排除——T4-c 敌意前缀单出口已覆盖冲突语义）/ 范围（双方均
  背景 NPC 且不同，核心 NPC 不进）/ 判重（无序对当天至多一次）/
  位置（同地）/ 状态（WORKING/IDLE；None=作息未应用保守不动，
  SLEEPING 深夜不社交）；
- 文案池分派（TEMPLATE_POOLS 单一查表）：师父/师傅→徒弟视角池、
  徒弟→师父视角池、血缘类（KIN_RELATION_TYPES 单一来源 import 自
  engine/background_npc.py）→家人池（方向无关，不用爹/娘称呼）、
  邻居→邻里池、恩人/老友/酒友/病人→交情池、主顾/老主顾/供货商/
  同行/同僚/货源/东家→往来池、未映射类型→交情池兜底；per-pair
  触发计数取模确定性轮转（与 habit.py HABIT_TEMPLATES 同构）；
- day 回绕判重：hour 23→0 回绕 _day+1 并清空当天判重集合，同对
  次日可再互动（World 无 day 属性，_last_hour 回绕是唯一合法日界）；
- relationships=None 零行为（与无 SocialLoop 等价，向后兼容）；
- 引擎接线：tick 中 HabitLoop.apply 之后 SocialLoop.apply（先习惯
  聚集落位、后社交扫描同地对）；payload 的 "with"/"relation" 键为
  审查验收「断言互动双方与关系边类型」的数据出口；零 LLM。

默认村庄全天基线（与 PM 推演清单的已核实差异，2026-10-16 实测锁定）：
- 6 个唯一互动对与 PM 清单完全一致（boy_shitou→master_wen 师徒 /
  herb_boy→doc_qin 师徒 / farmer_wang→zhao_daniu 母子 /
  cobbler_jia→carpenter_lu 邻居 / guard_wang→doc_qin 恩人 /
  guard_wang→fisher_zhou 酒友）；hour 17 酒馆两对（黄昏习惯聚集后
  IDLE 同地）与 PM 清单完全一致；
- 差异 1（hour 归属）：PM 推演四个日间对在 hour 9 触发；实测在
  hour 12（farmer_wang/zhao_daniu、herb_boy/doc_qin、
  cobbler_jia/carpenter_lu）与 hour 15（boy_shitou/master_wen）触发。
  根因：世界自 08:00 起，各 NPC 作息表 WORKING 转移点（5/6/7 点）
  早于世界起点，state_str 直到首个被 tick 命中的表项小时（12 点
  IDLE / 15 点 IDLE）才注册；hour 9 状态为 None＝作息未应用，状态
  闸门保守不动（契约语义，沿 HabitLoop 纪律）；
- 差异 2（总次数）：PM 推演"恰好 6 次各互动一次"；实测 24 次
  tick(60) 跨日界（08:00→次日 08:00），次日清晨 5/6/7 点 WORKING
  表项重新注册、日界回绕判重清零（本身是契约要求行为，
  TestSocialDayWrap 锁定），其中 4 对次日再次互动——总事件 10 次、
  唯一对 6 个、每天内每对恰一次。
本文件锁定实测现实（不强行改配置凑数）；差异已随交付报告提交 PM 裁定。

编写纪律（沿 test_habit.py）：单元级自构造小场景（自定义地点 + 手工
RelationshipNetwork + MockLLMProvider），不依赖默认村庄口径；端到端用
默认构造（configs/relationships.json 真实关系边）。
"""

import random
import unittest

from engine.background_npc import BackgroundNPC, KIN_RELATION_TYPES
from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.relationships import Relationship, RelationshipNetwork
from engine.socialize import (BUSINESS_TEMPLATES, DISCIPLE_TEMPLATES,
                              FALLBACK_TEMPLATES, FRIENDSHIP_TEMPLATES,
                              KIN_TEMPLATES, MASTER_TEMPLATES,
                              NEIGHBOR_TEMPLATES, TEMPLATE_POOLS, SocialLoop)
from engine.world import Location, World


def social_cfg(npc_id, name, location="hall", schedule=None):
    """最小背景 NPC 配置 dict（schedule 缺省 8 点 WORKING）。"""
    return {"id": npc_id, "name": name, "role": "villager",
            "location_id": location, "summary": f"{name}，测试用背景住民",
            "residence": "home",
            "schedule": schedule if schedule is not None else {"8": "WORKING"}}


def make_net(*edges):
    """手工关系网：edges 为 (source_id, target_id, relation, affinity)。"""
    return RelationshipNetwork([
        Relationship(source_id=s, target_id=t, relation=r, affinity=a)
        for s, t, r, a in edges])


def make_world():
    """三地点最小世界（hall 同地舞台 / far 异地 / home 居所）。"""
    return World(locations=[Location("hall", "正屋", 0, 0, "public"),
                            Location("far", "后院", 50, 50, "work"),
                            Location("home", "居所", 100, 100, "residence")],
                 env_events=[{"actor": "none", "summary": "占位"}])


def spawn(world, configs):
    """按配置批量构建背景 NPC（零 LLM，纯规则对象）。"""
    return [BackgroundNPC(cfg, world) for cfg in configs]


def stage(npc_specs, edges):
    """单元舞台：npc_specs=[(id,name,location,schedule)]，edges=关系元组。

    返回 (world, npcs_dict, loop)；状态注册由用例自行 apply_schedule。
    """
    world = make_world()
    npcs = spawn(world, [social_cfg(nid, name, loc, sched)
                         for nid, name, loc, sched in npc_specs])
    loop = SocialLoop(world, npcs, make_net(*edges))
    return world, {bg.id: bg for bg in npcs}, loop


def social_events(world):
    """事件历史中的社交 npc_action 事件（payload 含 with 键=数据出口）。"""
    return [e for e in world.bus.history
            if e.kind == "npc_action" and "with" in e.payload]


def two_npcs_working(edges, locations=("hall", "hall")):
    """双 NPC 同在工作态舞台：A/B 均 WORKING，位置可指定。"""
    world = make_world()
    npcs = spawn(world, [social_cfg("npc_a", "阿甲", location=locations[0]),
                         social_cfg("npc_b", "老乙", location=locations[1])])
    for npc in npcs:
        npc.apply_schedule(8)  # WORKING 表项注册
    loop = SocialLoop(world, npcs, make_net(*edges))
    return world, npcs, loop


# ========================================================================== #
# 1. TestSocialGates —— 五层闸门逐条正反（单元级小场景）
# ========================================================================== #
class TestSocialGates(unittest.TestCase):
    """五层闸门：亲和（敌意排除/零界通过）/ 范围（核心 NPC、自环排除）/
    判重（无序对当天一次）/ 位置（同地才社交）/ 状态（None 与
    SLEEPING 不社交）。"""

    def test_affinity_gate_hostile_excluded(self):
        """a) 亲和闸门反例：敌意边（affinity<0，如冤家）不进社交回路。"""
        world, _, loop = two_npcs_working(
            [("npc_a", "npc_b", "冤家", -0.6)])
        loop.apply(9)
        self.assertEqual(social_events(world), [],
                         msg="敌意边不社交——冲突语义由 T4-c 敌意前缀单出口承载")

    def test_affinity_gate_zero_boundary_passes(self):
        """b) 亲和闸门边界：affinity == 0 恰好通过（>= 0 含等号）。"""
        world, _, loop = two_npcs_working(
            [("npc_a", "npc_b", "熟人", 0.0)])
        loop.apply(9)
        self.assertEqual(len(social_events(world)), 1)

    def test_scope_gate_non_background_excluded(self):
        """c) 范围闸门反例：边指向核心 NPC（不在背景集合）不互动。"""
        world, _, loop = two_npcs_working(
            [("npc_a", "chen", "老主顾", 0.5),
             ("npc_b", "lily", "同行", 0.4)])
        loop.apply(9)
        self.assertEqual(social_events(world), [],
                         msg="核心 NPC 有 LLM 对话链，不进规则社交回路")

    def test_scope_gate_self_loop_excluded(self):
        """d) 范围闸门反例：source_id == target_id 自环边不互动。"""
        world, _, loop = two_npcs_working(
            [("npc_a", "npc_a", "熟人", 0.5)])
        loop.apply(9)
        self.assertEqual(social_events(world), [])

    def test_scope_gate_both_background_interact(self):
        """e) 范围闸门正例：双方均为背景 NPC 且不同 → 互动成立。"""
        world, _, loop = two_npcs_working(
            [("npc_a", "npc_b", "同僚", 0.5)])
        loop.apply(9)
        events = social_events(world)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].actor, "npc_a")
        self.assertEqual(events[0].payload["with"], "npc_b")

    def test_dedup_gate_reversed_edge_single_event(self):
        """f) 判重闸门：a→b 与 b→a 双向边同天只互动一次（无序对口径）。"""
        world, _, loop = two_npcs_working(
            [("npc_a", "npc_b", "同僚", 0.5),
             ("npc_b", "npc_a", "同僚", 0.5)])
        loop.apply(9)
        events = social_events(world)
        self.assertEqual(len(events), 1,
                         msg="无序对判重：先到的边（文件插入序）触发，反向边跳过")
        self.assertEqual(events[0].actor, "npc_a",
                         msg="文件插入序确定性：首条边的 source 为 speaker")

    def test_dedup_gate_same_hour_repeat_apply(self):
        """g) 判重闸门：同 hour 两次 apply 只发一条（每对每天一次）。"""
        world, _, loop = two_npcs_working(
            [("npc_a", "npc_b", "同僚", 0.5)])
        loop.apply(9)
        loop.apply(9)
        self.assertEqual(len(social_events(world)), 1)

    def test_dedup_gate_multi_hour_same_day(self):
        """h) 判重闸门：同一天跨 hour 多次扫描（10/11/12 点）仍只一条。"""
        world, _, loop = two_npcs_working(
            [("npc_a", "npc_b", "同僚", 0.5)])
        for hour in (10, 11, 12):
            loop.apply(hour)
        self.assertEqual(len(social_events(world)), 1)

    def test_location_gate_different_places(self):
        """i) 位置闸门反例：两地（hall/far）不互动；同地后互动成立。"""
        world, _, loop = two_npcs_working(
            [("npc_a", "npc_b", "邻居", 0.4)],
            locations=("hall", "far"))
        loop.apply(9)
        self.assertEqual(social_events(world), [],
                         msg="社交必须同地——隔空搭话不存在")
        world.move_entity("npc_b", "hall")
        loop.apply(10)
        self.assertEqual(len(social_events(world)), 1)

    def test_state_gate_none_not_social(self):
        """j) 状态闸门反例：作息未应用（state_str=None）保守不动。"""
        world, npcs, loop = stage(
            [("npc_a", "阿甲", "hall", {"12": "IDLE"}),
             ("npc_b", "老乙", "hall", {"12": "IDLE"})],
            [("npc_a", "npc_b", "同僚", 0.5)])
        self.assertIsNone(npcs["npc_a"].state_str)
        loop.apply(9)
        self.assertEqual(social_events(world), [],
                         msg="None=作息未应用，沿 HabitLoop 纪律保守不动")

    def test_state_gate_sleeping_not_social(self):
        """k) 状态闸门反例：SLEEPING 深夜不社交——同屋也不闲聊。

        双 NPC 同居所（home）：22 点双双 SLEEPING 后仍同地，
        隔离出纯状态闸门反例（位置闸门放行）。"""
        world = make_world()
        npcs = spawn(world, [
            social_cfg("npc_a", "阿甲", "hall",
                       {"8": "WORKING", "22": "SLEEPING"}),
            social_cfg("npc_b", "老乙", "hall",
                       {"8": "WORKING", "22": "SLEEPING"}),
        ])
        loop = SocialLoop(world, npcs,
                          make_net(("npc_a", "npc_b", "同僚", 0.5)))
        for bg in npcs:
            bg.apply_schedule(8)   # 双 WORKING 同地
        loop.apply(9)
        self.assertEqual(len(social_events(world)), 1,
                         msg="正例先行：白天同地 WORKING 社交成立")
        for bg in npcs:
            bg.apply_schedule(22)  # 双双 SLEEPING → 同回居所 home（仍同地）
        loop.apply(22)
        self.assertEqual(len(social_events(world)), 1,
                         msg="农家全家夜里同屋但不闲聊——SLEEPING 状态闸门拦截")

    def test_state_gate_working_and_idle_both_pass(self):
        """l) 状态闸门正例：WORKING+WORKING 与 WORKING+IDLE 混合均可社交。"""
        world = make_world()
        npcs = spawn(world, [
            social_cfg("npc_a", "阿甲", "hall", {"8": "WORKING", "12": "IDLE"}),
            social_cfg("npc_b", "老乙", "hall", {"8": "WORKING"}),
        ])
        loop = SocialLoop(world, npcs,
                          make_net(("npc_a", "npc_b", "同僚", 0.5)))
        for bg in npcs:
            bg.apply_schedule(8)   # 双 WORKING
        loop.apply(9)
        self.assertEqual(len(social_events(world)), 1)
        # a 转 IDLE（保持 hall）后跨日界：同对重获资格，混合状态仍可社交
        loop._npcs["npc_a"].apply_schedule(12)
        loop.apply(0)              # hour 回绕 → 新一天
        self.assertEqual(len(social_events(world)), 2,
                         msg="WORKING+IDLE 混合状态通过状态闸门")


# ========================================================================== #
# 2. TestTemplateDispatch —— 文案池分派正确性（单一查表 + 行为验证）
# ========================================================================== #
class TestTemplateDispatch(unittest.TestCase):
    """文案池分派：类型→池映射常量锁定、逐池行为验证（含徒弟边走
    师父视角池、未映射类型兜底、跨天轮转确定性）。"""

    def test_pool_constants_mapping(self):
        """a) 契约常量锁定：TEMPLATE_POOLS 全类型映射 + 兜底池=交情池。"""
        self.assertIs(TEMPLATE_POOLS["师父"], DISCIPLE_TEMPLATES)
        self.assertIs(TEMPLATE_POOLS["师傅"], DISCIPLE_TEMPLATES)
        self.assertIs(TEMPLATE_POOLS["徒弟"], MASTER_TEMPLATES)
        for relation in KIN_RELATION_TYPES:
            self.assertIs(TEMPLATE_POOLS[relation], KIN_TEMPLATES,
                          msg=relation)
        self.assertIs(TEMPLATE_POOLS["邻居"], NEIGHBOR_TEMPLATES)
        for relation in ("恩人", "老友", "酒友", "病人"):
            self.assertIs(TEMPLATE_POOLS[relation], FRIENDSHIP_TEMPLATES,
                          msg=relation)
        for relation in ("主顾", "老主顾", "供货商", "同行", "同僚",
                         "货源", "东家"):
            self.assertIs(TEMPLATE_POOLS[relation], BUSINESS_TEMPLATES,
                          msg=relation)
        self.assertIs(FALLBACK_TEMPLATES, FRIENDSHIP_TEMPLATES)
        # 血缘类型集合单一来源：从 background_npc 导出，非复制第二份
        self.assertEqual(KIN_RELATION_TYPES,
                         frozenset({"父亲", "母亲", "儿子", "女儿",
                                    "妻子", "丈夫"}))
        # 每池恰 2 条模板（契约：与 habit.py 同构的轮转池）
        for pool in (DISCIPLE_TEMPLATES, MASTER_TEMPLATES, KIN_TEMPLATES,
                     NEIGHBOR_TEMPLATES, FRIENDSHIP_TEMPLATES,
                     BUSINESS_TEMPLATES):
            self.assertEqual(len(pool), 2)
            for template in pool:
                self.assertIn("{npc_name}", template)
                self.assertIn("{other_name}", template)

    def test_pool_dispatch_table(self):
        """b) 逐池行为验证：全类型分派正确 + 未映射类型（同乡）兜底交情池。"""
        cases = [
            ("师父", DISCIPLE_TEMPLATES), ("师傅", DISCIPLE_TEMPLATES),
            ("徒弟", MASTER_TEMPLATES),
            ("父亲", KIN_TEMPLATES), ("母亲", KIN_TEMPLATES),
            ("儿子", KIN_TEMPLATES), ("女儿", KIN_TEMPLATES),
            ("妻子", KIN_TEMPLATES), ("丈夫", KIN_TEMPLATES),
            ("邻居", NEIGHBOR_TEMPLATES),
            ("恩人", FRIENDSHIP_TEMPLATES), ("老友", FRIENDSHIP_TEMPLATES),
            ("酒友", FRIENDSHIP_TEMPLATES), ("病人", FRIENDSHIP_TEMPLATES),
            ("主顾", BUSINESS_TEMPLATES), ("老主顾", BUSINESS_TEMPLATES),
            ("供货商", BUSINESS_TEMPLATES), ("同行", BUSINESS_TEMPLATES),
            ("同僚", BUSINESS_TEMPLATES), ("货源", BUSINESS_TEMPLATES),
            ("东家", BUSINESS_TEMPLATES),
            ("同乡", FRIENDSHIP_TEMPLATES),  # 未映射 → 兜底
        ]
        for relation, pool in cases:
            with self.subTest(relation=relation):
                world, _, loop = two_npcs_working(
                    [("npc_a", "npc_b", relation, 0.5)])
                loop.apply(9)
                events = social_events(world)
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].payload["relation"], relation)
                candidates = {t.replace("{npc_name}", "阿甲")
                              .replace("{other_name}", "老乙") for t in pool}
                self.assertIn(events[0].payload["summary"], candidates)

    def test_disciple_pool_speaker_is_apprentice(self):
        """c) 师父边走徒弟视角池：speaker 是徒弟，对长辈说话（含敬语）。"""
        world, _, loop = two_npcs_working(
            [("npc_a", "npc_b", "师父", 0.75)])
        loop.apply(9)
        text = social_events(world)[0].payload["summary"]
        self.assertIn(DISCIPLE_TEMPLATES[0]
                      .replace("{npc_name}", "阿甲")
                      .replace("{other_name}", "老乙"), text)
        self.assertIn("师傅", text, msg="徒弟对长辈的称呼语义")

    def test_master_pool_for_disciple_edge(self):
        """d) 徒弟边走师父视角池：speaker 是师父，对晚辈指点（契约指定用例）。"""
        world, _, loop = two_npcs_working(
            [("npc_a", "npc_b", "徒弟", 0.8)])
        loop.apply(9)
        text = social_events(world)[0].payload["summary"]
        self.assertIn(MASTER_TEMPLATES[0]
                      .replace("{npc_name}", "阿甲")
                      .replace("{other_name}", "老乙"), text)

    def test_kin_pool_no_kinship_address(self):
        """e) 家人池方向无关措辞：不用爹/娘称呼，直接用对方名。"""
        world, _, loop = two_npcs_working(
            [("npc_a", "npc_b", "父亲", 0.9)])
        loop.apply(9)
        text = social_events(world)[0].payload["summary"]
        self.assertIn("老乙", text)
        self.assertNotIn("爹", text)
        self.assertNotIn("娘", text)

    def test_rotation_deterministic_across_days(self):
        """f) 轮转确定性：同对跨天两次互动取池内模板 0→1（计数取模）。"""
        world, _, loop = two_npcs_working(
            [("npc_a", "npc_b", "邻居", 0.4)])
        loop.apply(10)             # day 0 触发：模板 0
        loop.apply(0)              # 23→0 回绕（此处 10→0 亦回绕）：day 1
        events = social_events(world)
        self.assertEqual(len(events), 2)
        expected = [NEIGHBOR_TEMPLATES[0], NEIGHBOR_TEMPLATES[1]]
        for event, template in zip(events, expected):
            self.assertEqual(event.payload["summary"],
                             template.replace("{npc_name}", "阿甲")
                             .replace("{other_name}", "老乙"))


# ========================================================================== #
# 3. TestSocialDayWrap —— day 回绕判重（hour 23→0 后同对可再互动）
# ========================================================================== #
class TestSocialDayWrap(unittest.TestCase):
    """日界推导：_last_hour 回绕（23→0）_day+1 并清空当天判重集合；
    同对次日可再互动（轮转续接）；World 无 day 属性，回绕是唯一合法
    日界（契约 docstring 口径）。"""

    def test_wrap_clears_dedup_and_reinteracts(self):
        """a) 23→0 回绕：判重集合清空、同对次日再互动（模板轮转 0→1）。"""
        world, _, loop = two_npcs_working(
            [("npc_a", "npc_b", "同僚", 0.5)])
        pair = tuple(sorted(("npc_a", "npc_b")))
        loop.apply(23)             # day 0：互动 1 次
        self.assertEqual(len(social_events(world)), 1)
        self.assertEqual(loop.interacted_pairs, frozenset({pair}))
        loop.apply(23)             # 同 hour 再扫：判重，不重复
        self.assertEqual(len(social_events(world)), 1)
        # 挪走 b（位置闸门）后跨日界：判重集合清空但位置不符不互动
        world.move_entity("npc_b", "far")
        loop.apply(0)              # 23→0 回绕 → day 1
        self.assertEqual(loop.interacted_pairs, frozenset(),
                         msg="日界回绕应清空当天判重集合")
        self.assertEqual(len(social_events(world)), 1)
        # 挪回同地后次日再扫：同对可再互动（新判重周期）
        world.move_entity("npc_b", "hall")
        loop.apply(1)
        self.assertEqual(len(social_events(world)), 2)
        self.assertEqual(loop.interacted_pairs, frozenset({pair}))

    def test_no_wrap_within_day(self):
        """b) 日内 hour 递增（9→10→23）不触发日界：同对仍只互动一次。"""
        world, _, loop = two_npcs_working(
            [("npc_a", "npc_b", "同僚", 0.5)])
        for hour in range(9, 24):
            loop.apply(hour)
        self.assertEqual(len(social_events(world)), 1)


# ========================================================================== #
# 4. TestSocialNoneRelationships —— None 零行为 + 空网络向后兼容
# ========================================================================== #
class TestSocialNoneRelationships(unittest.TestCase):
    """relationships=None 零行为（与无 SocialLoop 等价）；空关系网络
    引擎全天运转零社交事件（向后兼容）。"""

    def test_none_zero_behavior(self):
        """a) relationships=None：任意 hour 扫描零事件零判重登记。"""
        world, npcs, loop = stage(
            [("npc_a", "阿甲", "hall", None),
             ("npc_b", "老乙", "hall", None)],
            [("npc_a", "npc_b", "同僚", 0.5)])
        loop_none = SocialLoop(world, npcs, None)
        for hour in (9, 10, 12, 17, 23, 0, 1):
            loop_none.apply(hour)
        self.assertEqual(social_events(world), [])
        self.assertEqual(loop_none.interacted_pairs, frozenset())

    def test_empty_network_engine_no_social(self):
        """b) 空关系网引擎（向后兼容）：全天 24 tick 零社交事件。"""
        engine = NPCEngine(llm=MockLLMProvider(),
                           relationships=RelationshipNetwork())
        collected = []
        engine.world.bus.subscribe(collected.append, kinds=["npc_action"])
        for _ in range(24):
            engine.tick(60)
        self.assertEqual([e for e in collected if "with" in e.payload], [])


# ========================================================================== #
# 5. TestMinimalFixtureEndToEnd —— 最小夹具端到端（引擎级玩家可感知）
# ========================================================================== #
class TestMinimalFixtureEndToEnd(unittest.TestCase):
    """双背景 NPC 最小引擎：恰一条社交 npc_action（payload 含
    with/relation、summary 含双方名）、status recent_events 玩家可
    感知、同天再 tick 不重复、零 LLM。"""

    def setUp(self):
        self.engine = NPCEngine(
            llm=MockLLMProvider(),
            npc_configs=[],
            background_configs=[
                {"id": "npc_a", "name": "阿甲", "role": "villager",
                 "location_id": "plaza", "summary": "阿甲，测试用背景住民",
                 "residence": "house_zhao", "schedule": {"9": "WORKING"}},
                {"id": "npc_b", "name": "老乙", "role": "villager",
                 "location_id": "plaza", "summary": "老乙，测试用背景住民",
                 "residence": "house_zhao", "schedule": {"9": "WORKING"}},
            ],
            relationships=make_net(("npc_a", "npc_b", "同僚", 0.5)))
        self.collected = []
        self.engine.world.bus.subscribe(self.collected.append,
                                        kinds=["npc_action"])

    def _socials(self):
        return [e for e in self.collected if "with" in e.payload]

    def test_single_social_event_payload_and_text(self):
        """a) tick(60) 一次（→9 点 WORKING 同地）：恰一条社交 npc_action。"""
        self.engine.tick(60)
        socials = self._socials()
        self.assertEqual(self.engine.world.hour, 9)
        self.assertEqual(len(socials), 1)
        self.assertEqual(len(self.collected), 1,
                         msg="最小夹具无其他 npc_action 来源，总数也应为 1")
        event = socials[0]
        self.assertEqual(event.actor, "npc_a")
        self.assertEqual(event.payload["npc"], "npc_a")
        self.assertEqual(event.payload["with"], "npc_b")
        self.assertEqual(event.payload["relation"], "同僚")
        self.assertIn("阿甲", event.payload["summary"])
        self.assertIn("老乙", event.payload["summary"])

    def test_player_visible_in_recent_events(self):
        """b) 玩家可感知：status() recent_events 可见该互动文案（含双方名）。"""
        self.engine.tick(60)
        socials = self._socials()
        expected = socials[0].payload["summary"]
        recent = [item["summary"] for item in
                  self.engine.status()["recent_events"]]
        self.assertIn(expected, recent)
        self.assertIn("阿甲", expected)
        self.assertIn("老乙", expected)

    def test_same_day_second_tick_no_repeat(self):
        """c) 同一天再 tick（→10 点，状态延续同地）：判重不重复互动。"""
        self.engine.tick(60)
        self.engine.tick(60)
        self.assertEqual(len(self._socials()), 1)

    def test_zero_llm_calls(self):
        """d) 零 LLM：社交回路纯规则，tick 运转零调用零 token。"""
        self.engine.tick(60)
        self.engine.tick(60)
        self.assertEqual(self.engine.llm.call_count, 0)
        self.assertEqual(self.engine.llm.total_tokens_used, 0)


# ========================================================================== #
# 6. TestDefaultVillageFullDay —— 默认村庄全天基线（实测锁定）
# ========================================================================== #
class TestDefaultVillageFullDay(unittest.TestCase):
    """默认引擎 24 次 tick(60)（08:00→次日 08:00）社交基线锁定。

    与 PM 推演清单的差异（实测根因，见模块 docstring）：hour 归属
    （9→12/15，state_str None 直到首个命中表项）与总次数（6→10，
    24 tick 跨日界次日清晨重互动）。6 个唯一对与 hour 17 酒馆两对
    与 PM 清单完全一致；每天内每对恰一次。
    """

    # PM 清单的 6 个唯一互动对（实测唯一对集合与其完全一致）
    EXPECTED_PAIRS = {
        ("boy_shitou", "master_wen"): "师父",
        ("doc_qin", "herb_boy"): "师父",
        ("farmer_wang", "zhao_daniu"): "儿子",
        ("carpenter_lu", "cobbler_jia"): "邻居",
        ("doc_qin", "guard_wang"): "恩人",
        ("fisher_zhou", "guard_wang"): "酒友",
    }
    # day 1（tick 1-16，hour 9..23+0）序列：(tick, speaker, other, relation)
    DAY1_SEQUENCE = (
        (4, "farmer_wang", "zhao_daniu", "儿子"),
        (4, "herb_boy", "doc_qin", "师父"),
        (4, "cobbler_jia", "carpenter_lu", "邻居"),
        (7, "boy_shitou", "master_wen", "师父"),
        (9, "guard_wang", "doc_qin", "恩人"),
        (9, "guard_wang", "fisher_zhou", "酒友"),
    )
    # day 1 各事件的期望 hour（tick t → hour (8+t)%24）
    DAY1_HOURS = (12, 12, 12, 15, 17, 17)
    # day 2（tick 17-24，次日清晨 WORKING 表项重注册 + 日界判重清零）
    DAY2_SEQUENCE = (
        (21, "farmer_wang", "zhao_daniu", "儿子"),
        (22, "boy_shitou", "master_wen", "师父"),
        (22, "cobbler_jia", "carpenter_lu", "邻居"),
        (23, "herb_boy", "doc_qin", "师父"),
    )
    DAY2_HOURS = (5, 6, 6, 7)

    def _run(self):
        """random.seed(42)（天气随机纪律，仿 test_token_baseline_deterministic）
        构造默认引擎并 24 次 tick(60)，返回社交事件列表。"""
        random.seed(42)
        engine = NPCEngine(llm=MockLLMProvider())
        collected = []
        engine.world.bus.subscribe(collected.append, kinds=["npc_action"])
        for _ in range(24):
            engine.tick(60)
        socials = [e for e in collected if "with" in e.payload]
        return engine, socials

    def test_full_day_event_sequence(self):
        """a) 全天序列锁定：day1 六次（12/12/12/15/17/17 点）+ day2 四次
        （5/6/6/7 点），逐事件断言 tick/speaker/other/relation。"""
        engine, socials = self._run()
        self.assertEqual(len(socials), 10)
        observed = [(e.tick, e.actor, e.payload["with"],
                     e.payload["relation"]) for e in socials]
        self.assertEqual(observed[:6], list(self.DAY1_SEQUENCE))
        self.assertEqual(observed[6:], list(self.DAY2_SEQUENCE))
        # hour 推导对账（tick t → hour (8+t)%24）
        for event, hour in zip(socials[:6], self.DAY1_HOURS):
            self.assertEqual((8 + event.tick) % 24, hour)
        for event, hour in zip(socials[6:], self.DAY2_HOURS):
            self.assertEqual((8 + event.tick) % 24, hour)

    def test_unique_pairs_match_baseline(self):
        """b) 唯一对集合与 PM 清单逐对吻合（with/relation 断言）。"""
        engine, socials = self._run()
        observed = {tuple(sorted((e.actor, e.payload["with"]))):
                    e.payload["relation"] for e in socials}
        self.assertEqual(observed, self.EXPECTED_PAIRS)

    def test_each_pair_once_per_day(self):
        """c) 每对每天恰一次：day1 六对各一次、day2 四对（子集）各一次。"""
        engine, socials = self._run()
        day1 = socials[:6]
        day2 = socials[6:]
        pairs1 = [tuple(sorted((e.actor, e.payload["with"]))) for e in day1]
        pairs2 = [tuple(sorted((e.actor, e.payload["with"]))) for e in day2]
        self.assertEqual(len(pairs1), len(set(pairs1)),
                         msg="day1 内每对恰一次")
        self.assertEqual(len(pairs2), len(set(pairs2)),
                         msg="day2 内每对恰一次")
        self.assertEqual(set(pairs1), set(self.EXPECTED_PAIRS))
        self.assertTrue(set(pairs2) < set(pairs1),
                        msg="day2 四对是 day1 六对的重复子集（日界清零重互动）")

    def test_summary_contains_both_names(self):
        """d) 玩家可读性：每条社交文案含双方显示名。"""
        engine, socials = self._run()
        for event in socials:
            speaker = engine.background_npcs[event.actor].name
            other = engine.background_npcs[event.payload["with"]].name
            self.assertIn(speaker, event.payload["summary"],
                          msg=f"{event.actor} 名缺失")
            self.assertIn(other, event.payload["summary"],
                          msg=f"{event.payload['with']} 名缺失")

    def test_hostile_pair_zero_interaction(self):
        """e) 冤家对（miller_qian/fisher_zhou）零互动：敌意被亲和闸门排除。"""
        engine, socials = self._run()
        hostile = {tuple(sorted(("miller_qian", "fisher_zhou"))),
                   ("miller_qian", "fisher_zhou"),
                   ("fisher_zhou", "miller_qian")}
        for event in socials:
            pair = (event.actor, event.payload["with"])
            self.assertNotIn(pair, hostile)
            self.assertNotEqual(event.payload["relation"], "冤家")
        # 兜底：从事件流反查该二人互为零互动
        ids = {"miller_qian", "fisher_zhou"}
        for event in socials:
            self.assertFalse(ids.issubset({event.actor, event.payload["with"]}))

    def test_background_zero_llm(self):
        """f) 背景 NPC 零 LLM：全天运转（含 10 次社交触发）零调用零 token。"""
        engine, socials = self._run()
        self.assertGreater(len(socials), 0, msg="社交确实触发过（非空转证明）")
        self.assertEqual(engine.llm.call_count, 0)
        self.assertEqual(engine.llm.total_tokens_used, 0)

    def test_interacted_pairs_property_after_run(self):
        """g) 只读观测出口：24 tick 后 interacted_pairs=当天（day2）四对。"""
        engine, socials = self._run()
        expected = frozenset({
            tuple(sorted(("farmer_wang", "zhao_daniu"))),
            tuple(sorted(("boy_shitou", "master_wen"))),
            tuple(sorted(("cobbler_jia", "carpenter_lu"))),
            tuple(sorted(("herb_boy", "doc_qin"))),
        })
        self.assertEqual(engine._social_loop.interacted_pairs, expected)


if __name__ == "__main__":
    unittest.main()

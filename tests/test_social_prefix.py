"""T4-c 社会关系反应出口 3/3 完整闭环测试（审查指令 2）。

敌意单类型前缀 → 血缘/师徒/敌意三类型统一出口（engine/
background_npc.py ``_social_prefix``，原 ``_hostile_prefix`` 升级
删除）的行为契约：

- 分派逻辑：``get_relation_to(self→actor)`` 未命中 / relationships
  未注入 / actor 为空或为玩家 / 中性类型（既非血缘亦非师徒亦非
  敌意）→ 空串（与旧版行为一致）；affinity<0 → 敌意池
  ``HOSTILE_PREFIXES``；relation=="师父" → 恭敬池
  ``MASTER_PREFIXES``；relation=="徒弟" → 长辈慈和池
  ``ELDER_PREFIXES``；relation ∈ 六类直系血缘（父亲/母亲/儿子/
  女儿/妻子/丈夫）→ 亲昵池 ``KIN_PREFIXES``；
- 优先级：敌意判定（affinity<0）先于类型判定——防未来数据出现
  「血缘+负值」误入亲昵池；一条 get_relation_to 只返回一条边，
  天然至多命中一类；
- 统一出口：任一社会前缀命中 → 前缀拼接且不叠加 EnvMind 生理层
  后缀（社会层前缀优先于生理层后缀，同时命中至多一条——文案
  克制纪律）；未命中 → EnvMind 后缀路径与旧版逐字一致；
- 轮转同构：前缀按 ``_reaction_counter`` 取模轮转（与模板轮换
  同一计数器同一机制）；
- 零 LLM：三类型前缀纯文案拼接，Tier 0/1 封印不破；
- 敌意回归：T4-c 既有敌意行为零回归（miller_qian 对 fisher_zhou
  仍带敌意前缀；既有 tests/test_hostile_edge_behavior.py 原样
  不动、全量回归另行确认）。

数据事实（configs/relationships.json 44 条边，已核实直接用）：
师徒边 herb_boy→doc_qin「师父」0.85 / doc_qin→herb_boy「徒弟」
0.8（二人同在 clinic）；血缘边 zhao_daniu→farmer_zhao「父亲」
0.85 / farmer_wang→farmer_zhao「丈夫」0.9（子/母同在 plaza，父在
farm 需移入）；敌意边 miller_qian→fisher_zhou「冤家」-0.6。默认
村庄端到端用例直接消费该真实数据；分派边界用例沿
test_hostile_edge_behavior.py 自构造小场景（显式最小背景配置 +
手工 RelationshipNetwork + MockLLMProvider），不依赖默认村庄口径。

编写纪律（沿既有测试）：断言全部以玩家可感知为口径——npc_action
反应文案可见分叉 / 零 LLM 调用计数，不锁与玩家输出无关的纯数字；
跨 hour 推进用 world.game_minute 直设（沿 test_reaction_dedup.py）。
"""

import unittest

from engine.background_npc import (
    ELDER_PREFIXES,
    HOSTILE_PREFIXES,
    KIN_PREFIXES,
    MASTER_PREFIXES,
    BackgroundNPC,
)
from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.relationships import Relationship, RelationshipNetwork
from engine.village_genesis import EnvMind
from engine.world import Entity, Location, World, WorldEvent

# 自定义地点 id（自构造场景）：add_entity 不校验地点表，位置过滤纯
# 字符串比较，与 configs/（并行改造区）零依赖（沿
# test_hostile_edge_behavior.py 同款）。
DOCK = "dock"

# 全部社会前缀池的并集（「无任何社会前缀」对照组断言用）。
ALL_SOCIAL_PREFIXES = (HOSTILE_PREFIXES + MASTER_PREFIXES
                       + ELDER_PREFIXES + KIN_PREFIXES)


# ---------------------------------------------------------------------- #
# 自构造小场景脚手架（沿 test_hostile_edge_behavior.py 同款）
# ---------------------------------------------------------------------- #
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


def spawn_actor(world, actor_id):
    """注册事件 actor 实体到背景 NPC 同地点（位置过滤通过）。"""
    world.add_entity(Entity(id=actor_id, kind="npc", name=actor_id,
                            location_id=DOCK), announce=False)


def fire_env_event(world, actor_id, summary):
    """直接向总线发布一条 env_event（不依赖事件槽随机机制）。"""
    world.bus.publish(WorldEvent(world.tick_count, "env_event",
                                 actor_id, {"summary": summary}))


def actions_of(world, npc_id):
    """事件历史中某背景 NPC 产出的全部 npc_action。"""
    return [e for e in world.bus.history
            if e.kind == "npc_action" and e.actor == npc_id]


def starts_with_any(text, prefixes):
    """文案是否以任一给定前缀开头。"""
    return any(text.startswith(p) for p in prefixes)


# ---------------------------------------------------------------------- #
# 默认村庄端到端脚手架（消费 configs/relationships.json 真实数据）
# ---------------------------------------------------------------------- #
def default_engine():
    """默认构造引擎（3 核心 + 22 背景，关系网接 configs/relationships.json）。"""
    return NPCEngine(llm=MockLLMProvider())


def village_fire(engine, actor_id, summary):
    """向默认村庄总线发布一条 env_event（沿既有端到端用例的注入方式）。"""
    engine.world.bus.publish(WorldEvent(
        engine.world.tick_count, "env_event", actor_id,
        {"summary": summary}))


def village_acts(engine, npc_id, contains=None):
    """默认村庄中某背景 NPC 的 npc_action；contains 非空时按文案子串过滤。"""
    acts = actions_of(engine.world, npc_id)
    if contains is None:
        return acts
    return [e for e in acts if contains in e.payload["summary"]]


# ========================================================================== #
# 1. TestMasterElderFork —— 师徒分叉（默认村庄真实师徒边）
# ========================================================================== #
class TestMasterElderFork(unittest.TestCase):
    """徒弟对师父的事件反应带恭敬前缀、对非师徒 NPC 不带；师父对徒弟
    带长辈慈和前缀（herb_boy↔doc_qin 同在 clinic，真实师徒双向边）。"""

    def test_herb_boy_reveres_master_plain_to_non_master(self):
        """a) 师徒分叉：对师父（doc_qin）恭敬前缀 vs 对非师徒（baker_liu）无前缀。"""
        engine = default_engine()
        doc_summary = "秦大夫在药柜前清点新到的药材"
        village_fire(engine, "doc_qin", doc_summary)
        reacts = village_acts(engine, "herb_boy", contains=doc_summary)
        self.assertEqual(len(reacts), 1,
                         msg="herb_boy 应恰对师父 doc_qin 的同地事件反应一次")
        text = reacts[0].payload["summary"]
        # counter=0 确定性首项：恭敬池第 1 条拼在模板文案前
        self.assertTrue(text.startswith(MASTER_PREFIXES[0]),
                        msg=f"徒弟对师父的反应应带恭敬前缀"
                            f"『{MASTER_PREFIXES[0]}』: {text}")
        self.assertIn("小蓟", text)
        self.assertIn(doc_summary, text)
        # 对照组（跨 hour 解锁判重）：非师徒 actor baker_liu 移入 clinic，
        # herb_boy 第二次 env 反应轮转至模板第 2 条（无 {summary}，以
        # 锚词「跟在师父身后」识别）——不带任何社会前缀
        engine.world.move_entity("baker_liu", "clinic")
        engine.world.game_minute += 60
        village_fire(engine, "baker_liu", "刘婶送来刚出炉的面包")
        plain_reacts = village_acts(engine, "herb_boy",
                                    contains="跟在师父身后")
        self.assertEqual(len(plain_reacts), 1,
                         msg="对非师徒 actor 的第二次 env 反应应存在（模板轮转）")
        plain = plain_reacts[0].payload["summary"]
        self.assertFalse(starts_with_any(plain, ALL_SOCIAL_PREFIXES),
                         msg=f"对非师徒 NPC 的反应不得带任何社会前缀: {plain}")
        self.assertEqual(engine.llm.call_count, 0)

    def test_doc_qin_elder_prefix_toward_apprentice(self):
        """b) 师徒反向：doc_qin 对徒弟 herb_boy 的事件反应带长辈慈和前缀。"""
        engine = default_engine()
        herb_summary = "小蓟蹲在院里分拣晒干的草药"
        village_fire(engine, "herb_boy", herb_summary)
        reacts = village_acts(engine, "doc_qin", contains=herb_summary)
        self.assertEqual(len(reacts), 1,
                         msg="doc_qin 应恰对徒弟 herb_boy 的同地事件反应一次")
        text = reacts[0].payload["summary"]
        self.assertTrue(text.startswith(ELDER_PREFIXES[0]),
                        msg=f"师父对徒弟的反应应带长辈慈和前缀"
                            f"『{ELDER_PREFIXES[0]}』: {text}")
        self.assertIn("秦大夫", text)
        self.assertIn(herb_summary, text)
        self.assertEqual(engine.llm.call_count, 0)


# ========================================================================== #
# 2. TestKinFork —— 血缘分叉（默认村庄真实血缘边）
# ========================================================================== #
class TestKinFork(unittest.TestCase):
    """儿子对父亲、妻子对丈夫的事件反应均带血缘亲昵前缀；对非血缘
    NPC 不带（zhao_daniu/farmer_wang 同在 plaza，父亲 farmer_zhao
    从 farm 移入）。"""

    def test_zhao_family_kin_prefix_fork(self):
        """a) 血缘分叉：父到广场，儿子（父亲边）与妻子（丈夫边）同事件
        双命中亲昵前缀；非血缘 actor 对照不带。"""
        engine = default_engine()
        # 父亲从 farm 移到 plaza 与子/妻同地（entity_moved 抵达反应同样
        # 走社会前缀出口——父亲来了，儿子「直起腰来打了个照面」带亲昵前缀）
        self.assertTrue(engine.world.move_entity("farmer_zhao", "plaza"))
        arrival = village_acts(engine, "zhao_daniu", contains="打了个照面")
        self.assertEqual(len(arrival), 1,
                         msg="父亲抵达 plaza 应触发儿子一次 entity_moved 反应")
        self.assertTrue(
            arrival[0].payload["summary"].startswith(KIN_PREFIXES[0]),
            msg=f"儿子对父亲抵达的反应应带亲昵前缀"
                f"『{KIN_PREFIXES[0]}』: {arrival[0].payload['summary']}")
        # env_event 分叉：同一父亲事件，儿子（父亲边）与妻子（丈夫边）双命中
        father_summary = "赵老汉在谷仓边筛新收的谷子"
        village_fire(engine, "farmer_zhao", father_summary)
        son_reacts = village_acts(engine, "zhao_daniu",
                                  contains=father_summary)
        self.assertEqual(len(son_reacts), 1)
        self.assertTrue(
            son_reacts[0].payload["summary"].startswith(KIN_PREFIXES[0]),
            msg=f"儿子对父亲的事件反应应带亲昵前缀: "
                f"{son_reacts[0].payload['summary']}")
        wife_reacts = village_acts(engine, "farmer_wang",
                                   contains=father_summary)
        self.assertEqual(len(wife_reacts), 1,
                         msg="妻子应对丈夫的同地事件同样反应一次（丈夫边命中）")
        self.assertTrue(
            wife_reacts[0].payload["summary"].startswith(KIN_PREFIXES[0]),
            msg=f"妻子对丈夫的事件反应应带亲昵前缀: "
                f"{wife_reacts[0].payload['summary']}")
        # 对照组（跨 hour 解锁判重）：非血缘 actor baker_liu 移入 plaza，
        # zhao_daniu 第二次 env 反应（模板轮转至第 2 条，锚词「抹了把汗」）
        # 不带任何社会前缀
        engine.world.move_entity("baker_liu", "plaza")
        engine.world.game_minute += 60
        village_fire(engine, "baker_liu", "刘婶在广场吆喝卖面包")
        plain_reacts = village_acts(engine, "zhao_daniu",
                                    contains="抹了把汗")
        self.assertEqual(len(plain_reacts), 1,
                         msg="对非血缘 actor 的第二次 env 反应应存在（模板轮转）")
        plain = plain_reacts[0].payload["summary"]
        self.assertFalse(starts_with_any(plain, ALL_SOCIAL_PREFIXES),
                         msg=f"对非血缘 NPC 的反应不得带任何社会前缀: {plain}")
        self.assertEqual(engine.llm.call_count, 0)


# ========================================================================== #
# 3. TestHostileRegression —— 敌意回归（T4-c 既有行为零回归）
# ========================================================================== #
class TestHostileRegression(unittest.TestCase):
    """敌意池行为零回归：miller_qian 对 fisher_zhou（冤家 -0.6）的
    事件反应仍带敌意前缀（统一出口升级不改变敌意判定与轮转）。"""

    def test_miller_qian_hostile_prefix_for_fisher_zhou(self):
        engine = default_engine()
        # 冤家从 river 移到 mill 与磨坊主同地（抵达反应同样带敌意前缀）
        self.assertTrue(engine.world.move_entity("fisher_zhou", "mill"))
        arrival = village_acts(engine, "miller_qian", contains="探出头来")
        self.assertEqual(len(arrival), 1)
        self.assertTrue(
            arrival[0].payload["summary"].startswith(HOSTILE_PREFIXES[0]),
            msg=f"冤家抵达的反应应带敌意前缀"
                f"『{HOSTILE_PREFIXES[0]}』: {arrival[0].payload['summary']}")
        fisher_summary = "周渔夫在磨坊外拍打着蓑衣上的水"
        village_fire(engine, "fisher_zhou", fisher_summary)
        reacts = village_acts(engine, "miller_qian", contains=fisher_summary)
        self.assertEqual(len(reacts), 1)
        text = reacts[0].payload["summary"]
        self.assertTrue(text.startswith(HOSTILE_PREFIXES[0]),
                        msg=f"miller_qian 对 fisher_zhou 应保持敌意前缀"
                            f"（T4-c 零回归）: {text}")
        self.assertIn("钱多福", text)
        self.assertEqual(engine.llm.call_count, 0)


# ========================================================================== #
# 4. TestUnifiedOutletPriority —— 统一出口优先级（社会前缀压过 EnvMind 后缀）
# ========================================================================== #
class TestUnifiedOutletPriority(unittest.TestCase):
    """任一类型社会前缀命中时 EnvMind 生理层后缀不叠加（预置高 e_P=0.8
    的「嘴角带着笑意」后缀验证被顶掉）；无社会边时 EnvMind 后缀路径与
    旧版一致（对照 actor 文案可见笑意）。"""

    # 高愉悦 EnvMind：其余三维平稳带，e_P=0.8 ≥ 0.65 → 「，嘴角带着笑意」
    JOYFUL = EnvMind(p_fatigue=0.2, p_hunger=0.2, e_P=0.8, S_stress=0.2)

    def _suppression_stage(self, edge):
        """单前缀抑制舞台：阿甲（高愉悦）+ 单条社会边（npc_b）+ 无边
        对照 actor（npc_c）。返回 (world, npc_id)。"""
        world = make_world()
        spawn_actor(world, "npc_b")
        spawn_actor(world, "npc_c")
        BackgroundNPC(bg_config("npc_a", "阿甲"), world,
                      env_mind=self.JOYFUL, relationships=make_net(edge))
        fire_env_event(world, "npc_b", "有人在码头高声说话")
        acts = actions_of(world, "npc_a")
        self.assertEqual(len(acts), 1, msg="社会边 actor 事件应恰触发一次反应")
        return world, acts[0].payload["summary"]

    def test_master_prefix_suppresses_env_mind_suffix(self):
        """a) 师父边：恭敬前缀命中，高愉悦后缀被顶掉；对照无边 actor
        笑意完整存活（EnvMind 路径与旧版一致）。"""
        world, text = self._suppression_stage(("npc_a", "npc_b", "师父", 0.85))
        self.assertTrue(text.startswith(MASTER_PREFIXES[0]),
                        msg=f"师父边反应应带恭敬前缀: {text}")
        self.assertNotIn("笑意", text,
                         msg="社会前缀命中时不得再叠加 EnvMind 高愉悦后缀"
                             "（至多一条纪律）")
        # 对照（跨 hour 解锁判重，无边 actor）：EnvMind 后缀路径完整存活
        world.game_minute += 60
        fire_env_event(world, "npc_c", "货郎挑担路过")
        acts = actions_of(world, "npc_a")
        self.assertEqual(len(acts), 2)
        plain = acts[1].payload["summary"]
        self.assertFalse(starts_with_any(plain, ALL_SOCIAL_PREFIXES),
                         msg=f"无边 actor 反应不得带任何社会前缀: {plain}")
        self.assertIn("笑意", plain,
                      msg="无社会边时 EnvMind 高愉悦后缀路径应与旧版一致")

    def test_elder_prefix_suppresses_env_mind_suffix(self):
        """b) 徒弟边：长辈慈和前缀命中，高愉悦后缀被顶掉。"""
        _, text = self._suppression_stage(("npc_a", "npc_b", "徒弟", 0.8))
        self.assertTrue(text.startswith(ELDER_PREFIXES[0]),
                        msg=f"徒弟边反应应带长辈慈和前缀: {text}")
        self.assertNotIn("笑意", text)

    def test_kin_prefix_suppresses_env_mind_suffix(self):
        """c) 父亲边：血缘亲昵前缀命中，高愉悦后缀被顶掉。"""
        _, text = self._suppression_stage(("npc_a", "npc_b", "父亲", 0.85))
        self.assertTrue(text.startswith(KIN_PREFIXES[0]),
                        msg=f"父亲边反应应带亲昵前缀: {text}")
        self.assertNotIn("笑意", text)

    def test_hostile_prefix_suppresses_env_mind_suffix(self):
        """d) 敌意边：敌意前缀命中，高愉悦后缀被顶掉（统一出口三类型全覆盖）。"""
        _, text = self._suppression_stage(("npc_a", "npc_b", "冤家", -0.6))
        self.assertTrue(text.startswith(HOSTILE_PREFIXES[0]),
                        msg=f"敌意边反应应带敌意前缀: {text}")
        self.assertNotIn("笑意", text)


# ========================================================================== #
# 5. TestDispatchBoundary —— 分派边界（自构造小场景）
# ========================================================================== #
class TestDispatchBoundary(unittest.TestCase):
    """分派优先级与中性类型边界：敌意判定（affinity<0）先于类型判定；
    六类直系血缘全量命中亲昵池；老友/兄长（旁系）/冤家+非负值等中性
    类型不前拼（与旧版行为一致）。"""

    def _plain_stage(self, edges):
        """无 EnvMind 单反应舞台：返回 (world, npc_id)。"""
        world = make_world()
        for _, target, _, _ in edges:
            spawn_actor(world, target)
        BackgroundNPC(bg_config("npc_a", "阿甲"), world,
                      relationships=make_net(*edges))
        return world

    def test_hostile_affinity_beats_kin_type(self):
        """a) 优先级锁定：父亲+负值（未来脏数据形态）走敌意池，不误入亲昵池。"""
        world = self._plain_stage([("npc_a", "npc_b", "父亲", -0.5)])
        fire_env_event(world, "npc_b", "有人在码头大声说话")
        acts = actions_of(world, "npc_a")
        self.assertEqual(len(acts), 1)
        text = acts[0].payload["summary"]
        self.assertTrue(text.startswith(HOSTILE_PREFIXES[0]),
                        msg=f"affinity<0 应优先于类型判定走敌意池: {text}")
        self.assertFalse(starts_with_any(text, KIN_PREFIXES),
                         msg="血缘类型+负值不得误入亲昵池（敌意判定最前）")

    def test_all_six_kin_types_hit_kin_pool(self):
        """b) 六类直系血缘全量命中：母亲/儿子/女儿补齐（父亲/妻子/丈夫
        由默认村庄用例覆盖），且前缀按 counter 取模轮转。"""
        edges = [("npc_a", "npc_b", "母亲", 0.85),
                 ("npc_a", "npc_c", "儿子", 0.8),
                 ("npc_a", "npc_d", "女儿", 0.9)]
        world = self._plain_stage(edges)
        for i, (_, target, _, _) in enumerate(edges):
            fire_env_event(world, target, f"第{i}位亲人来到码头")
            world.game_minute += 60  # 跨 hour 逐次解锁判重
        acts = actions_of(world, "npc_a")
        self.assertEqual(len(acts), 3)
        texts = [e.payload["summary"] for e in acts]
        # 亲昵池 2 条，counter 0/1/2 取模轮转：0 → 1 → 0
        for i, text in enumerate(texts):
            self.assertTrue(
                text.startswith(KIN_PREFIXES[i % len(KIN_PREFIXES)]),
                msg=f"第{i + 1}次血缘反应应带亲昵前缀"
                    f"『{KIN_PREFIXES[i % len(KIN_PREFIXES)]}』: {text}")
            self.assertIn(f"第{i}位亲人", text)

    def test_neutral_relation_types_stay_plain(self):
        """c) 中性类型不前拼：老友（正向普通边）/兄长（旁系血缘，不在
        六类直系集合）/冤家+affinity=0（敌意类型但非负值）均无前缀。"""
        edges = [("npc_a", "npc_c", "老友", 0.6),
                 ("npc_a", "npc_d", "兄长", 0.8),
                 ("npc_a", "npc_e", "冤家", 0.0)]
        world = self._plain_stage(edges)
        for i, (_, target, _, _) in enumerate(edges):
            fire_env_event(world, target, f"第{i}位路人经过码头")
            world.game_minute += 60
        acts = actions_of(world, "npc_a")
        self.assertEqual(len(acts), 3)
        for e in acts:
            self.assertFalse(
                starts_with_any(e.payload["summary"], ALL_SOCIAL_PREFIXES),
                msg=f"中性类型边不得触发任何社会前缀: "
                    f"{e.payload['summary']}")


# ========================================================================== #
# 6. TestPrefixRotation —— 前缀池轮转同构（默认村庄真实边）
# ========================================================================== #
class TestPrefixRotation(unittest.TestCase):
    """前缀按既有 _reaction_counter 取模轮转（与模板轮换同构）：同一
    NPC 连续多次对同一社会边 actor 的反应前缀逐次推进、池尽回卷。"""

    def test_master_prefix_rotates_with_counter(self):
        """a) 恭敬池（2 条）三轮：躬身 → 拱手行礼 → 躬身（counter 0/1/2）。"""
        engine = default_engine()
        summaries = ["秦大夫在药柜前翻找药材",
                     "秦大夫对着方子沉吟",
                     "秦大夫提笔写药方"]
        for i, summary in enumerate(summaries):
            village_fire(engine, "doc_qin", summary)
            engine.world.game_minute += 60  # 跨 hour 逐次解锁判重
        reacts = village_acts(engine, "herb_boy")
        self.assertEqual(len(reacts), 3,
                         msg="三次跨 hour 的师父事件应各触发一次徒弟反应")
        # 前缀轮转与模板轮换共用同一计数器：0/1/2 次反应前缀取模推进
        expect = [MASTER_PREFIXES[0], MASTER_PREFIXES[1], MASTER_PREFIXES[0]]
        for i, react in enumerate(reacts):
            self.assertTrue(
                react.payload["summary"].startswith(expect[i]),
                msg=f"第{i + 1}次反应前缀应为『{expect[i]}』（counter={i} "
                    f"取模轮转）: {react.payload['summary']}")
        self.assertEqual(engine.llm.call_count, 0)

    def test_elder_prefix_rotates_with_counter(self):
        """b) 长辈慈和池（2 条）两轮：颔首 → 含笑打量（counter 0/1）。"""
        engine = default_engine()
        for i in range(2):
            village_fire(engine, "herb_boy", f"小蓟第{i}次整理药篓")
            engine.world.game_minute += 60
        reacts = village_acts(engine, "doc_qin")
        self.assertEqual(len(reacts), 2)
        expect = [ELDER_PREFIXES[0], ELDER_PREFIXES[1]]
        for i, react in enumerate(reacts):
            self.assertTrue(
                react.payload["summary"].startswith(expect[i]),
                msg=f"第{i + 1}次反应前缀应为『{expect[i]}』: "
                    f"{react.payload['summary']}")
        self.assertEqual(engine.llm.call_count, 0)

    def test_kin_prefix_rotates_with_counter(self):
        """c) 亲昵池（2 条）两轮：父到广场后儿子与妻子对父亲事件的前缀
        同步取模推进（笑着招手 → 快步迎上前）。"""
        engine = default_engine()
        self.assertTrue(engine.world.move_entity("farmer_zhao", "plaza"))
        for i in range(2):
            village_fire(engine, "farmer_zhao", f"赵老汉第{i}次吆喝筛谷")
            engine.world.game_minute += 60
        # 只看 env_event 反应（父亲抵达的 entity_moved 反应用各自模板
        # 锚词排除，不混入轮转序列）
        for npc_id, arrival_anchor in (("zhao_daniu", "打了个照面"),
                                       ("farmer_wang", "看了看是谁来了")):
            reacts = [e for e in village_acts(engine, npc_id)
                      if arrival_anchor not in e.payload["summary"]]
            self.assertEqual(len(reacts), 2,
                             msg=f"{npc_id} 应对父亲的两次跨 hour 事件各反应一次")
            expect = [KIN_PREFIXES[0], KIN_PREFIXES[1]]
            for i, react in enumerate(reacts):
                self.assertTrue(
                    react.payload["summary"].startswith(expect[i]),
                    msg=f"{npc_id} 第{i + 1}次反应前缀应为『{expect[i]}』: "
                        f"{react.payload['summary']}")
        self.assertEqual(engine.llm.call_count, 0)


# ========================================================================== #
# 7. TestZeroLLMMaintained —— 三类型前缀全程零 LLM（Tier 0/1 封印）
# ========================================================================== #
class TestZeroLLMMaintained(unittest.TestCase):
    """四类社会前缀同 hour 齐发（师徒双向 + 血缘 + 敌意），全程
    MockLLMProvider 零调用零记录——社会层出口纯文案拼接，不破
    背景 NPC 零 LLM 架构封印。"""

    def test_all_social_kinds_zero_llm(self):
        engine = default_engine()
        # 布置血缘（父入 plaza）与敌意（冤家入 mill）同地舞台
        self.assertTrue(engine.world.move_entity("farmer_zhao", "plaza"))
        self.assertTrue(engine.world.move_entity("fisher_zhou", "mill"))
        # 同 hour 四路齐发：clinic 师徒双向 / plaza 血缘 / mill 敌意
        village_fire(engine, "doc_qin", "秦大夫在药柜前翻找药材")
        village_fire(engine, "herb_boy", "小蓟把晒好的药材收进药柜")
        village_fire(engine, "farmer_zhao", "赵老汉在谷仓边筛谷子")
        village_fire(engine, "fisher_zhou", "周渔夫在磨坊外叫卖鲜鱼")
        # 四类前缀各有至少一条命中（玩家可感知出口全类型在场）
        self.assertTrue(
            any(starts_with_any(e.payload["summary"], MASTER_PREFIXES)
                for e in village_acts(engine, "herb_boy")),
            msg="herb_boy 对师父事件应可见恭敬前缀")
        self.assertTrue(
            any(starts_with_any(e.payload["summary"], ELDER_PREFIXES)
                for e in village_acts(engine, "doc_qin")),
            msg="doc_qin 对徒弟事件应可见长辈慈和前缀")
        self.assertTrue(
            any(starts_with_any(e.payload["summary"], KIN_PREFIXES)
                for e in village_acts(engine, "zhao_daniu")),
            msg="zhao_daniu 对父亲事件应可见亲昵前缀")
        self.assertTrue(
            any(starts_with_any(e.payload["summary"], HOSTILE_PREFIXES)
                for e in village_acts(engine, "miller_qian")),
            msg="miller_qian 对冤家事件应可见敌意前缀")
        # 零 LLM 封印：全程零调用零记录
        self.assertEqual(engine.llm.call_count, 0,
                         msg="四类社会前缀反应应全程零 LLM 调用")
        self.assertEqual(engine.llm.call_log, [],
                         msg="零 LLM 不仅计数为零，调用记录也应为空")


if __name__ == "__main__":
    unittest.main()

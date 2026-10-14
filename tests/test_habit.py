"""T5 第一小步 · 黄昏酒馆习惯回路验收测试（杜希格习惯回路离散截断）。

被测契约（engine/habit.py，纯规则 0-token，Tier 0/1 封印层）：

- 习惯推导（离散、确定性）：与 tavern_sun 存在任一方向"酒友/老主顾"
  边（PUB_RELATION_TYPES 精确匹配——"主顾/供货商/东家"近义类型不
  命中）的背景 NPC 获得"黄昏酒馆"习惯；tavern_sun 本身若是背景 NPC
  则排除；relationships=None 时零习惯；
- 触发四层闸门（apply(hour) 每 tick 一次）：黄昏窗口 hour∈{17,18}
  → 同 hour 判重（至多一条习惯 npc_action）→ 状态 IDLE（None/
  WORKING/SLEEPING 不扰）→ 位置非 tavern（已在地不重复触发）；
- 文案：HABIT_TEMPLATES 纯中文模板按触发计数取模确定性轮转（与
  BackgroundNPC 反应模板轮换同构）；
- 熄灯语义：HabitLoop 不主动送回居所——19/20/21 点各 NPC
  apply_schedule SLEEPING 时既有作息移动接管（交接验证）；
- 零 LLM：HabitLoop 不持有任何 LLM 引用，全天运转（含黄昏习惯
  触发与连锁背景反应）call_count==0 / total_tokens==0。

用例分四个测试类：
1. TestHabitDerivation：习惯集合推导（默认村庄真实关系网 + 手工网
   正反边/近义类型/锚点排除分叉）；
2. TestHabitTriggerRules：触发闸门与文案轮转（手工小场景单元级）；
3. TestDuskGatheringEndToEnd：默认村庄端到端——17 点常客聚集酒馆
   （玩家可感知验收）、19/20/21 点熄灯交接、非黄昏不动、同 hour
   判重；
4. TestHabitZeroLLM：全天运转零 LLM + 空关系网引擎向后兼容。

编写纪律（沿 test_hostile_edge_behavior.py）：单元级自构造小场景
（自定义地点 + 手工 RelationshipNetwork + MockLLMProvider），不依赖
默认村庄口径；端到端用默认构造（configs/relationships.json 真实
酒馆边：guard_wang 酒友 / fisher_zhou 老主顾 / doc_qin 老主顾）。
"""

import unittest

from engine.background_npc import BackgroundNPC
from engine.engine import NPCEngine
from engine.habit import (DUSK_HOURS, HABIT_LOCATION, HABIT_TEMPLATES,
                          PUB_RELATION_TYPES, HabitLoop)
from engine.llm.mock import MockLLMProvider
from engine.relationships import Relationship, RelationshipNetwork
from engine.world import Location, World

# 常客三人组（configs/relationships.json 真实酒馆边，17-18 点均 IDLE）
REGULARS = ("doc_qin", "fisher_zhou", "guard_wang")
REGULAR_NAMES = {"doc_qin": "秦大夫", "fisher_zhou": "周渔夫",
                 "guard_wang": "王守卫"}


def habit_cfg(npc_id, name, location="clinic", schedule=None):
    """最小背景 NPC 配置 dict（schedule 缺省 12 点 IDLE→19 点 SLEEPING，
    与 doc_qin 同款黄昏前 IDLE 段）。"""
    return {"id": npc_id, "name": name, "role": "villager",
            "location_id": location, "summary": f"{name}，测试用背景住民",
            "residence": "home",
            "schedule": schedule if schedule is not None else {
                "12": "IDLE", "19": "SLEEPING"}}


def make_net(*edges):
    """手工关系网：edges 为 (source_id, target_id, relation, affinity)。"""
    return RelationshipNetwork([
        Relationship(source_id=s, target_id=t, relation=r, affinity=a)
        for s, t, r, a in edges])


def make_world():
    """三地点最小世界（clinic 工作地 / tavern 习惯锚点 / home 居所）。"""
    return World(locations=[Location("clinic", "医馆", 280, 200, "shop"),
                            Location("tavern", "酒馆", 60, 180, "shop"),
                            Location("home", "家", 100, 300, "residence")],
                 env_events=[{"actor": "none", "summary": "占位"}])


def spawn(world, configs):
    """按配置批量构建背景 NPC（零 LLM，纯规则对象）。"""
    return [BackgroundNPC(cfg, world) for cfg in configs]


def habit_summaries(world, npc_id, name):
    """事件历史中该 NPC 的习惯文案（HABIT_TEMPLATES format 后精确匹配）。"""
    candidates = {t.replace("{npc_name}", name) for t in HABIT_TEMPLATES}
    return [e.payload["summary"] for e in world.bus.history
            if e.kind == "npc_action" and e.actor == npc_id
            and e.payload["summary"] in candidates]


def tavern_locations(engine, npc_ids):
    """背景 NPC 当前位置表（快照用）。"""
    return {nid: engine.background_npcs[nid].entity.location_id
            for nid in npc_ids}


# ========================================================================== #
# 1. TestHabitDerivation —— 习惯集合推导（社会关系→习惯的数据流）
# ========================================================================== #
class TestHabitDerivation(unittest.TestCase):
    """习惯推导：酒友/老主顾边（任一方向）命中、近义类型不命中、
    锚点 NPC 排除、None 零习惯。"""

    def test_default_village_regulars_derived(self):
        """a) 默认村庄：guard_wang/fisher_zhou/doc_qin 三人命中习惯集合。

        configs/relationships.json 真实酒馆边三向齐备：
        tavern_sun→guard_wang 酒友（反向）/ fisher_zhou→tavern_sun
        老主顾（正向）/ tavern_sun→doc_qin 老主顾（反向）。
        """
        engine = NPCEngine(llm=MockLLMProvider())
        self.assertEqual(engine._habit_loop.habit_npc_ids,
                         frozenset(REGULARS))

    def test_default_village_non_pub_edges_excluded(self):
        """b) 无酒馆边 NPC 不在习惯集合（近义类型对照）。

        herder_ma→tavern_sun 为"主顾"（非"老主顾"，精确匹配不命中）；
        baker_liu/old_zhang/tavern_aman（东家边）等均无边命中；chen
        是核心 NPC（酒友边存在但不在背景层，天然不进集合）。
        """
        engine = NPCEngine(llm=MockLLMProvider())
        habit_ids = engine._habit_loop.habit_npc_ids
        for npc_id in ("herder_ma", "baker_liu", "old_zhang",
                       "tavern_aman", "weaver_yang"):
            self.assertNotIn(npc_id, habit_ids, msg=npc_id)
        # 酒友边持有者 chen 是核心 NPC：习惯回路是背景 NPC 专属层
        self.assertNotIn("chen", habit_ids)
        # 酒馆老板本身（核心 NPC）不在背景层，也不得自触发
        self.assertNotIn("tavern_sun", habit_ids)

    def test_manual_net_bidirectional_and_type_forks(self):
        """c) 手工网分叉：正/反边命中；"主顾/供货商"近义类型不命中；
        tavern_sun 若为背景 NPC 亦排除（锚点不自触发）。"""
        world = make_world()
        npcs = spawn(world, [
            habit_cfg("npc_a", "阿甲"), habit_cfg("npc_b", "老钱"),
            habit_cfg("npc_c", "阿丙"), habit_cfg("npc_d", "阿丁"),
            habit_cfg("tavern_sun", "孙老三", location="tavern")])
        net = make_net(
            ("npc_a", "tavern_sun", "酒友", 0.5),    # 正向：命中
            ("tavern_sun", "npc_b", "老主顾", 0.7),  # 反向：命中
            ("npc_c", "tavern_sun", "主顾", 0.4),    # 近义类型：不命中
            ("npc_d", "tavern_sun", "供货商", 0.5),  # 非酒馆社交边：不命中
            ("tavern_sun", "npc_a", "供货商", 0.3),  # 锚点出边非命中类型
        )
        habit = HabitLoop(world, npcs, net)
        self.assertEqual(habit.habit_npc_ids, frozenset({"npc_a", "npc_b"}))

    def test_relationships_none_zero_habits(self):
        """d) relationships=None 零习惯（与无 HabitLoop 等价）。"""
        world = make_world()
        npcs = spawn(world, [habit_cfg("npc_a", "阿甲")])
        habit = HabitLoop(world, npcs, None)
        self.assertEqual(habit.habit_npc_ids, frozenset())

    def test_dusk_hours_and_relation_type_constants(self):
        """e) 契约常量锁定：黄昏窗口 {17,18}、锚点酒馆、边类型精确集。"""
        self.assertEqual(DUSK_HOURS, frozenset({17, 18}))
        self.assertEqual(HABIT_LOCATION, "tavern")
        self.assertEqual(PUB_RELATION_TYPES, frozenset({"酒友", "老主顾"}))
        # 文案模板 ≥2 条纯中文（确定性轮转池）
        self.assertGreaterEqual(len(HABIT_TEMPLATES), 2)


# ========================================================================== #
# 2. TestHabitTriggerRules —— 触发闸门与文案轮转（单元级小场景）
# ========================================================================== #
class TestHabitTriggerRules(unittest.TestCase):
    """触发四层闸门：黄昏窗口/同 hour 判重/IDLE 状态/位置非在地。"""

    def _stage(self, schedule=None, relations=(("npc_a", "tavern_sun", "酒友", 0.5),)):
        """单 NPC 酒友舞台：手工世界 + 酒友边 + HabitLoop。"""
        world = make_world()
        npcs = spawn(world, [habit_cfg("npc_a", "阿甲", schedule=schedule)])
        habit = HabitLoop(world, npcs, make_net(*relations))
        return world, npcs[0], habit

    def test_dusk_idle_moves_to_tavern_with_text(self):
        """a) 黄昏触发：17 点 IDLE 常客被移到酒馆并发布习惯文案。"""
        world, npc, habit = self._stage()
        npc.apply_schedule(12)  # IDLE 段登记（保持工作地 clinic）
        self.assertEqual(npc.state_str, "IDLE")
        habit.apply(17)
        self.assertEqual(npc.entity.location_id, "tavern")
        summaries = habit_summaries(world, "npc_a", "阿甲")
        self.assertEqual(summaries, [HABIT_TEMPLATES[0].replace("{npc_name}", "阿甲")],
                         msg="首次触发应为模板池首条（计数器 0 取模确定性）")

    def test_non_dusk_hour_no_move(self):
        """b) 非黄昏时段（15 点）不触发：IDLE 常客仍留在工作地。"""
        world, npc, habit = self._stage()
        npc.apply_schedule(12)
        for hour in (15, 16, 19, 20, 8, 12):
            habit.apply(hour)
        self.assertEqual(npc.entity.location_id, "clinic")
        self.assertEqual(habit_summaries(world, "npc_a", "阿甲"), [])

    def test_state_gate_only_idle(self):
        """c) 状态闸门：WORKING/SLEEPING/None（作息未应用）均不触发。"""
        world, npc, habit = self._stage(
            schedule={"17": "WORKING", "19": "SLEEPING"})
        npc.apply_schedule(17)  # WORKING（驻工作地）
        habit.apply(17)
        self.assertEqual(npc.entity.location_id, "clinic",
                         msg="WORKING 打烊时段不扰——黄昏线索只拉收工歇脚的人")
        # None 状态（作息从未应用）：全新舞台保守不行动
        world2, npc2, habit2 = self._stage(schedule={"19": "SLEEPING"})
        habit2.apply(17)
        self.assertEqual(npc2.entity.location_id, "clinic")
        self.assertEqual(habit_summaries(world2, "npc_a", "阿甲"), [])
        # SLEEPING：直接推进到睡眠段再触发也不动（在家 home）
        world3, npc3, habit3 = self._stage()
        npc3.apply_schedule(12)
        npc3.apply_schedule(19)  # SLEEPING → home
        habit3.apply(17)
        self.assertEqual(npc3.entity.location_id, "home")

    def test_same_hour_dedup_single_text(self):
        """d) 同 hour 判重：17 点两次 apply 只发一条习惯文案。"""
        world, npc, habit = self._stage()
        npc.apply_schedule(12)
        habit.apply(17)
        habit.apply(17)  # 同 hour 第二次（判重键 hour=17 命中）
        self.assertEqual(habit_summaries(world, "npc_a", "阿甲"),
                         [HABIT_TEMPLATES[0].replace("{npc_name}", "阿甲")],
                         msg="同 hour 只发一条习惯 npc_action（沿 "
                             "BackgroundNPC._last_react_hour 模式）")

    def test_already_at_tavern_no_retrigger(self):
        """e) 已在地不重复触发：18 点时 17 点已入馆的常客不再发文。"""
        world, npc, habit = self._stage()
        npc.apply_schedule(12)
        habit.apply(17)  # → tavern + 文案 1 条
        habit.apply(18)  # 新 hour 判重放行，但位置闸门拦截
        self.assertEqual(npc.entity.location_id, "tavern")
        self.assertEqual(len(habit_summaries(world, "npc_a", "阿甲")), 1)

    def test_template_deterministic_rotation(self):
        """f) 文案确定性轮转：跨 hour 两次触发取模板池 0→1（计数取模）。"""
        world, npc, habit = self._stage()
        npc.apply_schedule(12)
        habit.apply(17)                       # 触发 1：模板 0
        world.move_entity("npc_a", "home")    # 手动挪走（模拟中途离席）
        habit.apply(18)                       # 触发 2：模板 1
        self.assertEqual(habit_summaries(world, "npc_a", "阿甲"), [
            HABIT_TEMPLATES[0].replace("{npc_name}", "阿甲"),
            HABIT_TEMPLATES[1].replace("{npc_name}", "阿甲")],
            msg="习惯文案随触发计数取模轮转（与反应模板轮换同构）")

    def test_none_relationships_zero_behavior(self):
        """g) relationships=None：零习惯零行为（无移动无事件）。"""
        world = make_world()
        npcs = spawn(world, [habit_cfg("npc_a", "阿甲")])
        habit = HabitLoop(world, npcs, None)
        npc = npcs[0]
        npc.apply_schedule(12)
        habit.apply(17)
        habit.apply(18)
        self.assertEqual(npc.entity.location_id, "clinic")
        self.assertEqual([e for e in world.bus.history
                          if e.kind == "npc_action"], [],
                         msg="零习惯 → 不产出任何习惯 npc_action")


# ========================================================================== #
# 3. TestDuskGatheringEndToEnd —— 默认村庄端到端（玩家可感知验收）
# ========================================================================== #
class TestDuskGatheringEndToEnd(unittest.TestCase):
    """黄昏酒馆聚集端到端：常客三人 17 点齐至酒馆、习惯文案可见、
    19/20/21 点各按作息熄灯归家、非黄昏不动、同 hour 判重。"""

    def setUp(self):
        self.engine = NPCEngine(llm=MockLLMProvider())
        # 先经 12 点 IDLE 表项登记状态（三常客作息 12 点起进入 IDLE 段）
        self.engine.world.game_minute = 12 * 60
        self.engine.tick(10)

    def _tick_to(self, hhmm):
        """跳到目标时刻前 10 分钟并 tick 一次（tick 先推进再应用）。"""
        hour, minute = hhmm
        self.engine.world.game_minute = hour * 60 + minute - 10
        self.engine.tick(10)

    def test_dusk_gathering_three_regulars_at_tavern(self):
        """a) 17 点酒馆聚集：三常客从工作地齐至 tavern + 习惯文案发布。

        玩家可感知口径：黄昏走进酒馆能看到秦大夫/周渔夫/王守卫
        三位常客（连同老板 tavern_sun 与帮工 tavern_aman）。
        """
        self._tick_to((17, 0))  # 16:50 tick → 17:00
        engine = self.engine
        self.assertEqual(engine.world.hour, 17)
        positions = tavern_locations(engine, REGULARS)
        self.assertEqual(positions, {nid: "tavern" for nid in REGULARS},
                         msg=f"17 点三常客应齐聚酒馆，实际 {positions}")
        # 习惯文案逐人可见（各恰一条，模板池首条确定性）
        for npc_id, name in REGULAR_NAMES.items():
            self.assertEqual(
                habit_summaries(engine.world, npc_id, name),
                [HABIT_TEMPLATES[0].replace("{npc_name}", name)],
                msg=f"{npc_id} 黄昏入馆应发布一条习惯 npc_action 文案")
        # 场景丰富度旁证：老板（核心）与帮工（背景）同在酒馆
        self.assertEqual(engine.npcs["tavern_sun"].entity.location_id, "tavern")
        self.assertEqual(
            engine.background_npcs["tavern_aman"].entity.location_id, "tavern")

    def test_lamp_off_handover_to_schedule(self):
        """b) 熄灯交接：19/20/21 点各 NPC SLEEPING 既有作息接管归家。

        HabitLoop 不主动送回居所——doc_qin 19 点/fisher_zhou 21 点/
        guard_wang 20 点各回各家，黄昏聚集自然散场。
        """
        self._tick_to((17, 0))   # 17:00 聚集
        engine = self.engine
        # 19:00 doc_qin 熄灯归家，其余二位仍在酒馆（夜宵态）
        self._tick_to((19, 0))
        self.assertEqual(engine.background_npcs["doc_qin"].entity.location_id,
                         "house_qin")
        self.assertEqual(engine.background_npcs["guard_wang"].entity.location_id,
                         "tavern")
        self.assertEqual(engine.background_npcs["fisher_zhou"].entity.location_id,
                         "tavern")
        # 20:00 guard_wang 归家；21:00 fisher_zhou 归家（酒馆散场完毕）
        self._tick_to((20, 0))
        self.assertEqual(engine.background_npcs["guard_wang"].entity.location_id,
                         "house_wang")
        self._tick_to((21, 0))
        self.assertEqual(engine.background_npcs["fisher_zhou"].entity.location_id,
                         "house_zhou")

    def test_non_dusk_no_move(self):
        """c) 非黄昏时段（15 点）不触发移动：三常客仍驻各自工作地。"""
        self._tick_to((15, 0))  # 15:00
        engine = self.engine
        self.assertEqual(engine.background_npcs["doc_qin"].entity.location_id,
                         "clinic")
        self.assertEqual(engine.background_npcs["fisher_zhou"].entity.location_id,
                         "river")
        self.assertEqual(engine.background_npcs["guard_wang"].entity.location_id,
                         "plaza")
        for npc_id, name in REGULAR_NAMES.items():
            self.assertEqual(habit_summaries(engine.world, npc_id, name), [])

    def test_same_hour_dedup_two_ticks(self):
        """d) 同 hour 判重：17 点两次 tick（17:00/17:10）只一条习惯文案。"""
        self._tick_to((17, 0))   # → 17:00 触发
        self.engine.tick(10)      # → 17:10 同 hour 第二次
        engine = self.engine
        self.assertEqual(engine.world.hour, 17)
        for npc_id, name in REGULAR_NAMES.items():
            self.assertEqual(len(habit_summaries(engine.world, npc_id, name)), 1,
                             msg=f"{npc_id} 同 hour 应只发一条习惯文案")

    def test_full_day_operation_dusk_gathering(self):
        """e) 全天逐小时运转（8:00→17:00）：17 点习惯回路自然触发聚集。"""
        engine = NPCEngine(llm=MockLLMProvider())
        engine.world.game_minute = 8 * 60
        for _ in range(9):  # 逐小时 8→17
            engine.tick(60)
        self.assertEqual(engine.world.hour, 17)
        self.assertEqual(tavern_locations(engine, REGULARS),
                         {nid: "tavern" for nid in REGULARS})
        # 18 点整点再 tick：仍在酒馆（不重复发文，位置闸门）
        engine.tick(60)
        self.assertEqual(tavern_locations(engine, REGULARS),
                         {nid: "tavern" for nid in REGULARS})


# ========================================================================== #
# 4. TestHabitZeroLLM —— 零 LLM 封印 + 空关系网向后兼容
# ========================================================================== #
class TestHabitZeroLLM(unittest.TestCase):
    """零 LLM 双口径：全天运转（含黄昏习惯触发与连锁背景反应）
    call_count==0 / total_tokens==0；空关系网引擎全天运转零习惯移动。"""

    def test_full_day_with_habit_zero_llm(self):
        """a) 全天 24 次 tick(60)（含 17/18 点习惯触发）：零 LLM 调用零 token。

        HabitLoop 纯规则移动+文案拼接，不持有任何 LLM 引用——黄昏
        事件链（entity_moved 连锁背景反应）亦全在 Tier 0/1 封印层。
        """
        engine = NPCEngine(llm=MockLLMProvider())
        for _ in range(24):
            engine.tick(60)
        self.assertEqual(engine.llm.call_count, 0,
                         msg="全天运转（含黄昏习惯触发）应零 LLM 调用")
        self.assertEqual(engine.llm.total_tokens_used, 0,
                         msg="全天运转（含黄昏习惯触发）应零 token")
        # 习惯确实在全天运转中真实触发过（非空转证明）
        self.assertTrue(any(
            habit_summaries(engine.world, npc_id, name)
            for npc_id, name in REGULAR_NAMES.items()))

    def test_empty_relationship_engine_no_habit_moves(self):
        """b) 空关系网引擎（向后兼容）：全天运转三常客无一次习惯移动。"""
        engine = NPCEngine(llm=MockLLMProvider(),
                           relationships=RelationshipNetwork())
        self.assertEqual(engine._habit_loop.habit_npc_ids, frozenset())
        engine.world.game_minute = 8 * 60
        for _ in range(24):
            engine.tick(60)
        # 黄昏窗口整天经过，但零习惯 → 无人被拉进酒馆（作息照常）
        for npc_id, name in REGULAR_NAMES.items():
            self.assertEqual(habit_summaries(engine.world, npc_id, name), [],
                             msg=f"{npc_id} 空关系网下不得有习惯文案")
        # 19 点 SLEEPING 后 doc_qin 仍按作息归家（既有行为零回归）
        engine.world.game_minute = 18 * 60 + 50
        engine.tick(10)
        self.assertEqual(engine.background_npcs["doc_qin"].entity.location_id,
                         "house_qin")


if __name__ == "__main__":
    unittest.main()

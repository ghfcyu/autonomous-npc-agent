"""T4-a 第二小步 · 25 人村庄接入运转集成验收测试（先红后绿）。

被测契约（实现子代理 A 并行交付，本文件按 PM 契约先行编写——A 合流前
相关用例为红是「先红后绿」的预期状态，交付合流后应全绿）：

- 规模口径：默认引擎（npc_configs=None 且 background_configs=None）
  2 核心 + 23 背景（旧 8 ∪ configs/village_roster 15 新住民）= 25 NPC、
  world 实体 26（含 player）、地点 33（既有 17 ∪ NEW_LOCATION_IDS 14 ∪
  farm/river），类别分布 residence 19 / shop 5 / work 5 / public 4；
- 工作地映射：12 组初始工作地锁定（含旧背景 farmer_zhao→farm、
  fisher_zhou→river；新住民 herder_ma→pasture 等）；
- 共居落位（提案 A8-1）：赵宅 4 人 / 大夫宅 2 人 / 学塾宅 2 人 /
  孙家 2 人——residence 契约 + 夜间 tick 后实体真实聚居；
- 作息反转：更夫 20:00 WORKING@plaza → 跨午夜 03:00 SLEEPING@宅；
  牧人 04:00 早出 WORKING@pasture；
- 全天 24 tick 无异常、15 新住民全部被作息驱动、status 覆盖 25 人；
- 零 LLM 双口径（25 人规模）：全天 tick + 1 句复杂对话，背景 NPC 调用
  计数与 token 双零，token 归属仅核心；
- 事件池扩容：≥32 条、actor ⊇ 花名册 25 人全员、首项 chen（轮播
  确定性锚）；
- token 裁剪双件：【周围的人】渲染前按 viewer 与各实体 |affinity| 降序
  稳定排序（无关系记 0.0）取前 5 行（NEARBY_PROMPT_LIMIT=5）；
  【人际关系】出边分两桶（亲缘保底桶在前），桶内 |affinity| 降序，
  总条数 ≤3 截断；
- 快脑商品词后缀边界收紧：「铁剑鞘多少钱」不提取「铁剑」回通用句；
- 问路动态地点：牧场/私塾（地点名动态取自 world.locations）；
- 快照覆盖：33 地点全量注册，viewer 快照字段正常。

编写纪律：
- 引擎构造照既有测试惯例（MockLLMProvider / 显式 relationships 传参，
  见 test_village.py 与 test_village_genesis.py 的构造方式）；
- 不直接 import 尚未存在的模块级常量（NEARBY_PROMPT_LIMIT 等以
  getattr 软引用），保证 A 合流前后本文件均可装载、红绿归因清晰；
- 断言玩家可感知行为（回复文本 / prompt 行数 / 实体落位），测试数量
  不是目标。
"""

import json
import os
import unittest

import engine.decision as decision_module
from engine.engine import DEFAULT_BG_CONFIG_DIR, NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.relationships import Relationship, RelationshipNetwork
from engine.village_genesis import (NEW_LOCATION_IDS, VILLAGE_ROSTER,
                                    load_newcomer_configs)
from engine.world import World


# ---------------------------------------------------------------------- #
# 测试脚手架
# ---------------------------------------------------------------------- #
def default_village_engine() -> NPCEngine:
    """默认构造引擎（生产/演示路径）：核心 2 + 背景 23 = 25 NPC。

    npc_configs=None 且 background_configs=None → 自动加载
    configs/npcs/（chen/lily）+ configs/background_npcs/（旧 8）+
    configs/village_roster/（新 15，经 village_genesis.load_newcomer_configs）。
    """
    return NPCEngine(llm=MockLLMProvider())


class RecordingLLM(MockLLMProvider):
    """记录完整 messages 的 Mock（断言慢脑 user prompt 内容用，见
    test_tag_residency.py 同款装配）。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.captured = []

    def chat(self, messages, temperature=0.7):
        self.captured.append([dict(m) for m in messages])
        return super().chat(messages, temperature)


def legacy_background_ids():
    """旧 8 背景 NPC 的 npc_id 集合（configs/background_npcs/ 动态对账）。"""
    ids = set()
    for fname in sorted(os.listdir(DEFAULT_BG_CONFIG_DIR)):
        if fname.endswith(".json"):
            with open(os.path.join(DEFAULT_BG_CONFIG_DIR, fname),
                      encoding="utf-8") as fh:
                ids.add(json.load(fh)["id"])
    return ids


def newcomer_ids():
    """15 新住民 npc_id 集合（configs/village_roster/ 动态对账）。"""
    return {cfg["id"] for cfg in load_newcomer_configs()}


def relation_lines(text):
    """to_prompt_text 输出中的关系行（剥掉「【人际关系】」段落头）。"""
    return [line for line in text.splitlines()
            if line and not line.startswith("【")]


def nearby_rows(user_prompt):
    """提取 user prompt 中【周围的人】块的行（到下一【段落头或串尾）。"""
    rows = []
    in_block = False
    for line in user_prompt.splitlines():
        if line.startswith("【周围的人】"):
            in_block = True
            continue
        if not in_block:
            continue
        if not line or line.startswith("【"):
            break
        rows.append(line)
    return rows


# ========================================================================== #
# 1. TestVillageScale —— 25 人 / 26 实体 / 33 地点规模锁
# ========================================================================== #
class TestVillageScale(unittest.TestCase):
    """默认引擎规模契约：2 核心 + 23 背景 = 25 NPC、实体 26（含 player）、
    33 地点（residence 19 / shop 5 / work 5 / public 4）、NEW 14 全在场。"""

    def setUp(self):
        self.engine = default_village_engine()

    def test_25_npcs_and_26_entities(self):
        engine = self.engine
        self.assertEqual(len(engine.npcs), 2)
        self.assertEqual(set(engine.npcs), {"chen", "lily"})
        # 背景 23 = 旧 8 + 新 15（动态对账，不硬编码清单）
        self.assertEqual(len(engine.background_npcs), 23)
        self.assertEqual(set(engine.background_npcs),
                         legacy_background_ids() | newcomer_ids())
        self.assertEqual(len(legacy_background_ids()), 8)
        self.assertEqual(len(newcomer_ids()), 15)
        # 25 NPC 全员 + player = 26 实体
        self.assertEqual(len(engine.world.entities), 26)
        roster_ids = {seed.npc_id for seed in VILLAGE_ROSTER}
        self.assertEqual(len(roster_ids), 25)
        self.assertEqual(roster_ids, set(engine.npcs) | set(engine.background_npcs))

    def test_33_locations_and_category_counts(self):
        locs = self.engine.world.locations
        self.assertEqual(len(locs), 33)
        counts = {}
        for loc in locs.values():
            category = getattr(loc, "category", "public")
            counts[category] = counts.get(category, 0) + 1
        # 类别分布契约：residence 19（既有 10+新 9）/ shop 5 /
        # work 5（pasture/mill/carpentry/farm/river）/ public 4
        # （plaza/well/school/shrine）
        self.assertEqual(counts, {"residence": 19, "shop": 5,
                                  "work": 5, "public": 4})
        # farm/river 两个 T4-a 新增工作地点（农田/河畔）
        self.assertEqual(locs["farm"].name, "农田")
        self.assertEqual(locs["river"].name, "河畔")

    def test_new_location_ids_all_registered(self):
        # NEW_LOCATION_IDS 14 个全部注册进 world.locations
        self.assertEqual(len(NEW_LOCATION_IDS), 14)
        for loc_id in NEW_LOCATION_IDS:
            self.assertIn(loc_id, self.engine.world.locations, msg=loc_id)
        # 兜底清单同步扩容：world._default_locations() 与 JSON 同为 33
        self.assertEqual(len(World._default_locations()), 33)
        fallback_ids = {loc.id for loc in World._default_locations()}
        for loc_id in (*NEW_LOCATION_IDS, "farm", "river"):
            self.assertIn(loc_id, fallback_ids, msg=loc_id)


# ========================================================================== #
# 2. TestWorkplaceMapping —— 工作地映射表断言（初始工作地）
# ========================================================================== #
class TestWorkplaceMapping(unittest.TestCase):
    """12 组初始工作地锁定：实体落位即配置工作地（引擎构造后未 tick，
    entity.location_id == 初始工作地），且工作地全部为已注册地点。"""

    WORKPLACES = {
        # 旧背景 2 人：T4-a 工作地迁移（residence 不变）
        "farmer_zhao": "farm",
        "fisher_zhou": "river",
        # 新住民 10 人
        "herder_ma": "pasture",
        "miller_qian": "mill",
        "carpenter_lu": "carpentry",
        "cobbler_jia": "carpentry",
        "master_wen": "school",
        "boy_shitou": "school",
        "widow_yi": "shrine",
        "peddler_luo": "market",
        "tavern_aman": "tavern",
        "herb_boy": "clinic",
    }

    def setUp(self):
        self.engine = default_village_engine()

    def test_initial_workplace_locations(self):
        for npc_id, workplace in self.WORKPLACES.items():
            self.assertIn(npc_id, self.engine.background_npcs, msg=npc_id)
            bg = self.engine.background_npcs[npc_id]
            self.assertEqual(bg.entity.location_id, workplace,
                             msg=f"{npc_id} 初始工作地应为 {workplace}")
            # 工作地必须是已注册地点（不是悬空引用）
            self.assertIn(workplace, self.engine.world.locations, msg=npc_id)


# ========================================================================== #
# 3. TestColocation —— 共居落位（提案 A8-1）
# ========================================================================== #
class TestColocation(unittest.TestCase):
    """共居契约：四组同 residence 共居；夜间 tick 后实体真实聚居同屋檐。"""

    GROUPS = {
        "house_zhao": ("farmer_zhao", "farmer_wang", "zhao_daniu", "farmhand_sun"),
        "house_qin": ("doc_qin", "herb_boy"),
        "house_school": ("master_wen", "boy_shitou"),
        "house_sun": ("tavern_sun", "tavern_aman"),
    }

    def setUp(self):
        self.engine = default_village_engine()

    def test_residence_colocation_groups(self):
        for home, members in self.GROUPS.items():
            self.assertIn(home, self.engine.world.locations, msg=home)
            for npc_id in members:
                self.assertIn(npc_id, self.engine.background_npcs,
                              msg=f"{home}/{npc_id}")
                self.assertEqual(self.engine.background_npcs[npc_id].residence,
                                 home, msg=f"{npc_id} 应以 {home} 为居所")

    def test_night_placement_real_colocation(self):
        # 推进穿过各组就寝时刻（19/20/21/22 点）：共居者夜间真实聚居
        self.engine.world.game_minute = 18 * 60
        for _ in range(4):
            self.engine.tick(60)  # 依次 19:00 / 20:00 / 21:00 / 22:00
        for home, members in self.GROUPS.items():
            for npc_id in members:
                self.assertEqual(
                    self.engine.background_npcs[npc_id].entity.location_id,
                    home, msg=f"{npc_id} 夜间应在家 {home}（共居落位）")


# ========================================================================== #
# 4. TestNightShift —— 作息反转（更夫夜班）与早出（牧人）
# ========================================================================== #
class TestNightShift(unittest.TestCase):
    """更夫 20:00 上工广场、跨午夜 03:00 收工回家；牧人 04:00 早出牧场。"""

    def setUp(self):
        self.engine = default_village_engine()

    def test_watchman_night_shift(self):
        engine = self.engine
        bg = engine.background_npcs["watchman_diao"]
        # 作息反转契约：20 点 WORKING、凌晨 3 点 SLEEPING
        self.assertEqual(bg.schedule.get("20"), "WORKING")
        self.assertEqual(bg.schedule.get("3"), "SLEEPING")
        # 20:00 → WORKING 且在工作地 plaza
        engine.world.game_minute = 19 * 60 + 50
        result = engine.tick(10)  # → 20:00
        self.assertEqual(result["schedule_applied"].get("watchman_diao"),
                         "WORKING")
        self.assertEqual(bg.entity.location_id, "plaza")
        # 跨午夜推进到 03:00 → SLEEPING 且回居所 house_watchman
        engine.world.game_minute = 23 * 60 + 50
        engine.tick(10)          # → 0:00（午夜，无表项，状态延续）
        engine.tick(60)          # → 1:00
        engine.tick(60)          # → 2:00
        result = engine.tick(60)  # → 3:00
        self.assertEqual(result["schedule_applied"].get("watchman_diao"),
                         "SLEEPING")
        self.assertEqual(bg.entity.location_id, "house_watchman")

    def test_herder_dawn_working(self):
        engine = self.engine
        bg = engine.background_npcs["herder_ma"]
        # 早出契约：04:00 WORKING
        self.assertEqual(bg.schedule.get("4"), "WORKING")
        engine.world.game_minute = 3 * 60 + 50
        result = engine.tick(10)  # → 4:00 早出
        self.assertEqual(result["schedule_applied"].get("herder_ma"),
                         "WORKING")
        self.assertEqual(bg.entity.location_id, "pasture")


# ========================================================================== #
# 5. TestVillageFullDay —— 全天运转：新住民被驱动 + status 全覆盖
# ========================================================================== #
class TestVillageFullDay(unittest.TestCase):
    """全天 24 次 tick(60)：每 tick 返回三键且无异常；全天累计 15 新住民
    全部被作息驱动（各自至少一次状态应用）；status 覆盖 25 人全员。"""

    def test_full_day_ticks_drive_all_newcomers(self):
        engine = default_village_engine()
        expected_newcomers = newcomer_ids()
        self.assertEqual(len(expected_newcomers), 15)
        applied = {}
        for _ in range(24):
            result = engine.tick(60)
            for key in ("clock", "tick", "schedule_applied"):
                self.assertIn(key, result)
            applied.update(result["schedule_applied"])
        undriven = expected_newcomers - set(applied)
        self.assertFalse(undriven,
                         msg=f"全天未被作息驱动的新住民: {sorted(undriven)}")

    def test_status_covers_all_25_villagers(self):
        engine = default_village_engine()
        for _ in range(24):
            engine.tick(60)
        status = engine.status()
        ids = {entry.get("id") for entry in status["npcs"]}
        roster_ids = {seed.npc_id for seed in VILLAGE_ROSTER}
        # 背景 23 人全在 status（连同核心 2 人 = 花名册 25 人全员）
        self.assertEqual(ids, roster_ids)
        self.assertEqual(len(ids), 25)


# ========================================================================== #
# 6. TestEnvZeroLLM25 —— 零 LLM 双口径（25 人规模）
# ========================================================================== #
class TestEnvZeroLLM25(unittest.TestCase):
    """25 人规模零 LLM 双口径：全天 24 tick 纯规则驱动（调用计数与 token
    双零）+ 1 句复杂对话恰 1 次慢脑调用，token 归属仅核心——23 个背景
    NPC 全部零条目（见 test_village_genesis.py 的 TestEnvZeroLLM 同款
    双口径写法，本类为 25 人默认构造口径）。"""

    COMPLEX_SENTENCE = "听说你是这条街上手艺最好的铁匠，但这炉子上的火候看起来有点乱啊？"

    def test_full_day_and_dialogue_zero_llm_for_background(self):
        engine = default_village_engine()
        self.assertEqual(len(engine.background_npcs), 23)  # 25 人规模前置
        self.assertEqual(engine.llm.call_count, 0)
        self.assertEqual(engine.llm.total_tokens_used, 0)
        # (a) 全天 24 tick 纯规则驱动：零 LLM 调用、零 token
        for _ in range(24):
            engine.tick(60)
        self.assertEqual(engine.llm.call_count, 0,
                         msg="24 tick 产生了 LLM 调用")
        self.assertEqual(engine.llm.total_tokens_used, 0,
                         msg="24 tick 产生了 token")
        self.assertEqual(engine.status()["token_stats"]["by_npc"], {})
        # (b) 1 句复杂对话（超全部快脑句长闸门 → 慢脑必达）
        result = engine.player_says(self.COMPLEX_SENTENCE, "chen")
        self.assertTrue(result["ok"])
        self.assertEqual(result["brain"], "slow")
        self.assertEqual(engine.llm.call_count, 1,
                         msg=f"复杂句应恰 1 次慢脑调用，"
                             f"实际 {engine.llm.call_count}")
        after = engine.status()["token_stats"]
        self.assertGreater(after["total_tokens"], 0)
        # token 归属仅核心 NPC：23 个背景 NPC 全部零条目
        self.assertEqual(set(after["by_npc"]), {"chen"},
                         msg="token 归属应仅核心 NPC")
        for bg_id in engine.background_npcs:
            self.assertNotIn(bg_id, after["by_npc"], msg=bg_id)


# ========================================================================== #
# 7. TestEventPoolCoverage25 —— 事件池 25 人全覆盖
# ========================================================================== #
class TestEventPoolCoverage25(unittest.TestCase):
    """_DEFAULT_ENV_EVENTS 扩容契约：总数 ≥32、actor 覆盖花名册 25 人
    全员、首项仍为 chen 的既有事件（轮播确定性锚）。"""

    def test_pool_covers_all_25_villagers(self):
        pool = list(World._DEFAULT_ENV_EVENTS)
        actors = {event["actor"] for event in pool}
        roster_ids = {seed.npc_id for seed in VILLAGE_ROSTER}
        self.assertEqual(len(roster_ids), 25)
        missing = roster_ids - actors
        self.assertFalse(missing,
                         msg=f"事件池缺少 NPC 的自发事件: {sorted(missing)}")

    def test_pool_size_at_least_32(self):
        self.assertGreaterEqual(len(World._DEFAULT_ENV_EVENTS), 32,
                                msg="15 新住民事件尾部追加后总数应 ≥32")

    def test_pool_first_item_is_chen_anchor(self):
        # 轮播确定性锚：首项仍为 chen 的既有事件（trigger 1 → chen）
        self.assertEqual(World._DEFAULT_ENV_EVENTS[0]["actor"], "chen")
        self.assertEqual(World._DEFAULT_ENV_EVENTS[0]["summary"],
                         "铁匠陈想起该去收矿石了")


# ========================================================================== #
# 8. TestNearbyLimit —— 【周围的人】≤5 行 + |affinity| 降序排序
# ========================================================================== #
class TestNearbyLimit(unittest.TestCase):
    """同地点 7 实体（chen + 6 背景）→ user prompt【周围的人】块 ≤5 行；
    给 chen 手工加 2 条高 |affinity| 关系 → 高亲缘实体行排在低亲缘前。"""

    # 6 个背景 NPC 移到 forge 与 chen 同地（7 实体；玩家留在 plaza 不入镜）
    BG_IDS = ("baker_liu", "doc_qin", "tavern_sun",
              "weaver_yang", "old_zhang", "guard_wang")
    BG_NAMES = {"baker_liu": "刘婶", "doc_qin": "秦大夫",
                "tavern_sun": "孙老三", "weaver_yang": "杨大姐",
                "old_zhang": "老张", "guard_wang": "王守卫"}
    # 慢脑必达句（超全部快脑句长闸门，见 test_village_genesis 同款）
    COMPLEX_SENTENCE = "最近铁价如何？我想订一批农具"

    def _make_engine(self) -> NPCEngine:
        # 显式空关系网：场景可控（chen 与 6 背景的 |affinity| 全部 0.0）
        engine = NPCEngine(llm=RecordingLLM(),
                           relationships=RelationshipNetwork())
        for bg_id in self.BG_IDS:
            self.assertIn(bg_id, engine.background_npcs, msg=bg_id)
            self.assertTrue(engine.world.move_entity(bg_id, "forge"), msg=bg_id)
        return engine

    def _user_prompt_of_last_call(self, engine) -> str:
        self.assertTrue(engine.llm.captured, "慢脑未被调用")
        return engine.llm.captured[-1][1]["content"]

    def test_nearby_prompt_limit_constant(self):
        # 契约常量锁：模块级 NEARBY_PROMPT_LIMIT = 5（getattr 软引用，
        # A 合流前为红、合流后转绿，不影响本文件其他用例装载）
        self.assertEqual(getattr(decision_module, "NEARBY_PROMPT_LIMIT", None),
                         5, "decision.NEARBY_PROMPT_LIMIT 应为模块级常量 5")

    def test_nearby_rows_capped_at_five(self):
        engine = self._make_engine()
        result = engine.player_says(self.COMPLEX_SENTENCE, "chen")
        self.assertEqual(result["brain"], "slow")
        rows = nearby_rows(self._user_prompt_of_last_call(engine))
        # 6 个同地实体 → 渲染 ≤5 行（NEARBY_PROMPT_LIMIT 截断）
        self.assertLessEqual(len(rows), 5,
                             msg=f"【周围的人】应 ≤5 行，实际 {len(rows)} 行")
        self.assertEqual(len(rows), 5)  # 6 nearby 截断后恰 5 行
        # 行内容确为同地实体名（不是别的段落混入）
        known_names = set(self.BG_NAMES.values())
        for row in rows:
            self.assertTrue(any(name in row for name in known_names),
                            msg=f"【周围的人】行含未知实体: {row}")

    def test_nearby_sorted_by_affinity_desc(self):
        engine = self._make_engine()
        # 手工加 2 条高 |affinity| 关系（chen → doc_qin 0.95 / tavern_sun 0.85）
        engine.relationships.add(Relationship(
            source_id="chen", target_id="doc_qin",
            relation="老友", affinity=0.95, target_name="秦大夫"))
        engine.relationships.add(Relationship(
            source_id="chen", target_id="tavern_sun",
            relation="酒友", affinity=0.85, target_name="孙老三"))
        result = engine.player_says(self.COMPLEX_SENTENCE, "chen")
        self.assertEqual(result["brain"], "slow")
        rows = nearby_rows(self._user_prompt_of_last_call(engine))
        self.assertLessEqual(len(rows), 5)
        # 高 |affinity| 实体必在截断后的行内（排在最前不被挤出）
        self.assertIn("秦大夫", rows[0], msg=f"最高亲缘应排首位: {rows}")
        self.assertIn("孙老三", rows[1], msg=f"次高亲缘应排第二: {rows}")
        # 低亲缘（无关系记 0.0）实体行出现在高亲缘之后
        # （old_zhang 的背景配置名为「老张」，与花名册叙事名「老张头」不同）
        low_rows = [i for i, row in enumerate(rows) if "老张" in row]
        self.assertTrue(low_rows, msg=f"低亲缘实体应仍在行内: {rows}")
        self.assertGreater(low_rows[0], 1, msg=f"低亲缘应排在高亲缘之后: {rows}")


# ========================================================================== #
# 9. TestRelationTop3 —— 【人际关系】Top3 + 亲缘保底桶（对抗性）
# ========================================================================== #
class TestRelationTop3(unittest.TestCase):
    """to_prompt_text(source_id) 两桶 + Top3 契约（提案修正项①）：

    - 保底桶（relation ∈ 亲缘类型集合）在前，普通桶在后；
    - 桶内各按 |affinity| 降序；总条数 ≤3 截断；
    - 对抗性：4 条高好感普通关系 + 1 条低好感「父亲」→ 父亲行必在
      （类型保底不被高好感挤出）。
    """

    @staticmethod
    def _rel(target, relation, affinity):
        return Relationship(source_id="chen", target_id=target,
                            relation=relation, affinity=affinity,
                            target_name=target)

    def _lines(self, *rels):
        net = RelationshipNetwork(list(rels))
        return relation_lines(net.to_prompt_text("chen"))

    def test_priority_father_not_squeezed_out(self):
        # 对抗性核心：父亲（0.1 低值）不被 4 条高值普通关系挤出
        lines = self._lines(
            self._rel("甲", "熟人", 0.9),
            self._rel("乙", "邻居", 0.8),
            self._rel("丙", "酒友", 0.7),
            self._rel("丁", "同行", 0.6),
            self._rel("戊", "父亲", 0.1),
        )
        self.assertLessEqual(len(lines), 3, msg=f"应 ≤3 条，实际 {lines}")
        self.assertTrue(any("父亲" in line for line in lines),
                        msg=f"亲缘保底桶被高好感普通关系挤出: {lines}")
        # 保底桶在前：父亲行是首行
        self.assertIn("父亲", lines[0], msg=f"保底桶应排在最前: {lines}")
        # 其余两行为普通桶 top2（0.9 / 0.8 降序）
        self.assertIn("甲", lines[1], msg=f"普通桶 top1 应为 0.9: {lines}")
        self.assertIn("乙", lines[2], msg=f"普通桶 top2 应为 0.8: {lines}")

    def test_normal_bucket_descending_by_affinity(self):
        lines = self._lines(
            self._rel("甲", "熟人", 0.9),
            self._rel("乙", "邻居", 0.5),
            self._rel("丙", "酒友", 0.7),
        )
        self.assertEqual(len(lines), 3)
        self.assertIn("甲", lines[0])
        self.assertIn("丙", lines[1])
        self.assertIn("乙", lines[2])

    def test_negative_affinity_ranked_by_abs(self):
        # |affinity| 口径：敌意 -0.9 排在好感 0.3 之前
        lines = self._lines(
            self._rel("仇", "宿敌", -0.9),
            self._rel("友", "熟人", 0.3),
        )
        self.assertEqual(len(lines), 2)
        self.assertIn("仇", lines[0])
        self.assertIn("友", lines[1])

    def test_two_relations_not_truncated(self):
        lines = self._lines(self._rel("甲", "熟人", 0.9),
                            self._rel("戊", "父亲", 0.1))
        self.assertEqual(len(lines), 2, msg="2 条关系不应被截断")

    def test_zero_relations_returns_empty_string(self):
        net = RelationshipNetwork()
        self.assertEqual(net.to_prompt_text("chen"), "")


# ========================================================================== #
# 10. TestGoodsSuffixBoundary —— 商品词后缀边界收紧
# ========================================================================== #
class TestGoodsSuffixBoundary(unittest.TestCase):
    """品类后缀边界（_GOODS_SUFFIX_CHARS="鞘柄套盒架扣绳垫钩"）：

    「铁剑鞘多少钱」不提取「铁剑」→ 快脑秒回通用句；回归保护：
    「铁剑多少钱」「铁锤多少钱」仍按问什么答什么报出商品价。"""

    def setUp(self):
        self.engine = default_village_engine()

    def _fast_reply(self, text, npc_id):
        calls_before = self.engine.llm.call_count
        result = self.engine.player_says(text, npc_id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["brain"], "fast", msg=text)
        self.assertEqual(result["action"]["intent"], "ask_price", msg=text)
        self.assertEqual(self.engine.llm.call_count, calls_before,
                         msg=f"快脑秒回不应产生 LLM 调用: {text}")
        return result["reply"]

    def test_sword_sheath_not_extracted(self):
        # 「铁剑鞘」：鞘为品类后缀 → 不提取「铁剑」，回通用句
        reply = self._fast_reply("铁剑鞘多少钱", "chen")
        self.assertTrue("看货定价" in reply or "童叟无欺" in reply,
                        msg=f"应回通用句，实际: {reply}")
        self.assertNotIn("十两", reply, msg=f"不得报出铁剑价: {reply}")
        self.assertNotIn("铁剑", reply, msg=f"不得提取「铁剑」: {reply}")

    def test_leather_armor_sleeve_not_extracted(self):
        # 「皮甲套」：套为品类后缀 → 不提取「皮甲」，回通用句
        reply = self._fast_reply("皮甲套多少钱", "lily")
        self.assertTrue("看货定价" in reply or "童叟无欺" in reply,
                        msg=f"应回通用句，实际: {reply}")
        self.assertNotIn("五两", reply, msg=f"不得报出皮甲价: {reply}")
        self.assertNotIn("皮甲", reply, msg=f"不得提取「皮甲」: {reply}")

    def test_regression_sword_price_unchanged(self):
        # 回归保护：问什么答什么纪律不回退
        reply = self._fast_reply("铁剑多少钱", "chen")
        self.assertIn("铁剑", reply)
        self.assertIn("十两", reply)

    def test_regression_hammer_price_unchanged(self):
        reply = self._fast_reply("铁锤多少钱", "chen")
        self.assertIn("铁锤", reply)
        self.assertIn("三两", reply)


# ========================================================================== #
# 11. TestDirectionNewLocations —— 问路动态地点（33 地点快脑）
# ========================================================================== #
class TestDirectionNewLocations(unittest.TestCase):
    """「牧场怎么走」「私塾怎么走」→ 快脑秒回且回复含目标地点中文名
    （地点动态取自 world.locations，T4-a 新地点自动生效）。"""

    def setUp(self):
        self.engine = default_village_engine()

    def _direction_reply(self, text):
        calls_before = self.engine.llm.call_count
        result = self.engine.player_says(text, "chen")
        self.assertTrue(result["ok"])
        self.assertEqual(result["brain"], "fast", msg=text)
        self.assertEqual(result["action"]["intent"], "ask_direction", msg=text)
        self.assertEqual(self.engine.llm.call_count, calls_before, msg=text)
        return result["reply"]

    def test_pasture_direction(self):
        reply = self._direction_reply("牧场怎么走")
        self.assertIn("牧场", reply, msg=f"问路回复应含目标地点中文名: {reply}")

    def test_school_direction(self):
        reply = self._direction_reply("私塾怎么走")
        self.assertIn("私塾", reply, msg=f"问路回复应含目标地点中文名: {reply}")


# ========================================================================== #
# 12. TestSnapshotCoverage —— 33 地点全量注册 + viewer 快照字段
# ========================================================================== #
class TestSnapshotCoverage(unittest.TestCase):
    """快照覆盖全村庄口径：33 地点全量注册进 world.locations（NEW 14 +
    farm/river）；viewer=chen 的 snapshot 含 my_location/clock/weather
    正常字段。"""

    def setUp(self):
        self.engine = default_village_engine()

    def test_all_33_locations_registered(self):
        locs = self.engine.world.locations
        self.assertEqual(len(locs), 33)
        for loc_id in (*NEW_LOCATION_IDS, "farm", "river"):
            self.assertIn(loc_id, locs, msg=loc_id)

    def test_viewer_snapshot_fields(self):
        snap = self.engine.world.snapshot("chen")
        self.assertIn("my_location", snap)
        self.assertIn("clock", snap)
        self.assertIn("weather", snap)
        # 字段取值正常：chen 在 forge（铁匠铺）
        self.assertEqual(snap["my_location"], "铁匠铺")
        self.assertIn(snap["weather"], {"sunny", "cloudy", "rain", "snow"})
        self.assertRegex(snap["clock"], r"^\d{2}:\d{2}$")


if __name__ == "__main__":
    unittest.main()

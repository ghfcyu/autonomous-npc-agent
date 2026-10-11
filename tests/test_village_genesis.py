"""T4 人口学村庄第一小步 · engine/village_genesis.py 验收测试（先红后绿）。

被测契约：25 NPC 人口学生成器（实现子代理 A 并行交付，本文件按 PM 契约
先行编写——``import engine.village_genesis`` 在 A 合流前为红是"先红后绿"
的预期状态，交付合流后应全绿；该模块缺失只影响本文件的用例装载，
对既有 397 用例零影响）。

验收口径（T4 人口学执法，五条款）：
1. 分布断言：6D 截断正态形状（均值带/标准差带/值域锁）+ 财富对数正态
   中位数带 + 齐普夫职业长尾（特殊职业 ≤2、非平顶、理论一致性）+
   金字塔分档占比（大样本 400，固定 seed 确定性，非 flaky）；
2. 分层：key（chen/lily）与 env（23 人）心智分层——key mind=None 由
   引擎常驻账本接管，env 走 EnvMind 4D 压缩稳态（字段与 8D 同名，
   共线性纪律：不新增维度）且常人平稳带值域；
3. 批量挂载复用：generate_village 与引擎 _build_tag_ledger 共用
   build_genesis_ledger 单一内核，crc32(npc_id) 确定性跨调用全量一致
   （挂载内核提取的直接证据）；
4. 零 LLM 双口径：15 新背景 NPC 全天 24 tick 纯规则驱动——调用计数
   与 token 两口径均零消耗；核心复杂句恰产生 1 次慢脑调用，token
   归属仅核心 NPC（背景 NPC 不进 token 统计）；
5. 配置完整性：configs/village_roster 15 份新住民配置字段齐备、
   居所/工作地全部注册为地点、夜班反转/早出作息与共居关系锁定。

编写纪律：
- 统计带宽严格按任务书，不自创更窄带宽；大样本循环一律用固定 seed
  的 random.Random 实例，绝不触碰全局 random；
- 引擎构造照既有测试惯例（MockLLMProvider / Persona.load_all /
  background_configs 显式传参，见 test_tag_residency.py 与
  test_village.py）；
- 零第三方依赖（random/statistics/json/os/dataclasses 标准库）。
"""

import dataclasses
import json
import os
import random
import statistics
import unittest
from collections import Counter

from engine.engine import DEFAULT_BG_CONFIG_DIR, NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.npc import Persona
from engine.tag_genesis import (ATTRIBUTE_RANGE, generate_innate_attributes,
                                zipf_probabilities)
from engine.tag_mount import (FLAW_POOL, SECRET_POOL, TagPhase,
                              build_genesis_ledger)
from engine.village_genesis import (NEWCOMER_CONFIG_DIR, NEW_LOCATION_IDS,
                                     VILLAGE_ROSTER, VILLAGE_SECTORS, EnvMind,
                                     VillagerProfile, describe_villager,
                                     generate_villager, generate_village,
                                     load_newcomer_configs)
from engine.world import DEFAULT_LOCATIONS_PATH, Location

# 6D 先天属性（契约口径：与 tag_genesis.InnateAttributes 同名六维）
ATTRS_6D = ("beauty", "strength", "savvy", "courage", "morality", "alcohol_tol")

# 大样本口径（任务书指定）：固定 seed 实例，绝不使用全局 random
BIG_SAMPLE_SEED = 20261011
BIG_SAMPLE_N = 400


# ---------------------------------------------------------------------- #
# 测试脚手架
# ---------------------------------------------------------------------- #
def innate_flaw_tags(ledger):
    """账本 INNATE 桶中的缺陷条目（tag_id 以 flaw_ 前缀，挂载内核契约）。"""
    return tuple(t for t in ledger.all_tags()
                 if t.phase is TagPhase.INNATE and t.tag_id.startswith("flaw_"))


def innate_secret_tags(ledger):
    """账本 INNATE 桶中的把柄条目（tag_id 以 secret_ 前缀，挂载内核契约）。"""
    return tuple(t for t in ledger.all_tags()
                 if t.phase is TagPhase.INNATE and t.tag_id.startswith("secret_"))


def legacy_location_ids():
    """已注册地点 id 集合（从 configs/locations.json 动态对账，不硬编码清单）。

    T4-a 地点扩容后该文件为 33 地点（既有 17 ∪ NEW_LOCATION_IDS 14 ∪
    farm/river）；函数名沿用历史口径，返回值为 JSON 全量注册地点。"""
    with open(DEFAULT_LOCATIONS_PATH, encoding="utf-8") as fh:
        return {item["id"] for item in json.load(fh)}


def legacy_background_ids():
    """旧 8 背景 NPC 的 npc_id 集合（它们已有 configs/background_npcs/ 配置，
    不在 configs/village_roster 新住民目录中）。"""
    ids = set()
    for fname in sorted(os.listdir(DEFAULT_BG_CONFIG_DIR)):
        if fname.endswith(".json"):
            with open(os.path.join(DEFAULT_BG_CONFIG_DIR, fname),
                      encoding="utf-8") as fh:
                ids.add(json.load(fh)["id"])
    return ids


def make_full_village_engine():
    """构造 T4 场景引擎：核心 2（chen/lily）+ 15 新背景 NPC + 33 地点。

    - npc_configs 显式传 Persona.load_all()：chen/lily 两核心（tag_profile
      声明走引擎常驻账本，与 test_tag_residency 惯例一致）；
    - background_configs 显式传 load_newcomer_configs()：仅加载 15 份
      新住民配置（engine.py 显式传入按传入值，生产目录 8 旧背景
      不加载）——引擎内 2+15=17 NPC 实体，这是任务书预期口径；
    - T4-a 地点扩容后 locations.json 已含全部 33 地点（既有 17 ∪ NEW
      14 ∪ farm/river），World() 构造即全量载入；下方 NEW_LOCATION_IDS
      14 个的手工注入变为同键覆盖（无害），保留以兼容扩容前场景。
    """
    engine = NPCEngine(llm=MockLLMProvider(),
                       npc_configs=Persona.load_all(),
                       background_configs=load_newcomer_configs())
    for loc_id in NEW_LOCATION_IDS:
        engine.world.locations[loc_id] = Location(
            id=loc_id, name=loc_id,
            category="residence" if loc_id.startswith("house_") else "public")
    return engine


# ========================================================================== #
# 1. TestRosterStructure —— 花名册结构锁（验收条款：分布断言之结构层）
# ========================================================================== #
class TestRosterStructure(unittest.TestCase):
    """25 NPC 花名册结构锁：sector 表全等、id 无重复、key/env 2+23、
    农夫恰 5 人、sector 计数呈长尾（最大阶层 ≥ 中位数 ×2）。"""

    EXPECTED_SECTORS = {"farming": 7, "hunt": 1, "craft": 5, "trade": 2,
                        "medicine": 2, "tavern": 2, "education": 2,
                        "guard": 2, "idle": 2}

    def test_sector_table_locked_and_totals_25(self):
        # VILLAGE_SECTORS 与契约表全等且合计 25
        self.assertEqual(VILLAGE_SECTORS, self.EXPECTED_SECTORS)
        self.assertEqual(sum(VILLAGE_SECTORS.values()), 25)

    def test_roster_exactly_25_unique_ids(self):
        # 花名册恰 25 条，npc_id 无重复
        self.assertEqual(len(VILLAGE_ROSTER), 25)
        ids = [seed.npc_id for seed in VILLAGE_ROSTER]
        self.assertEqual(len(set(ids)), 25, msg=f"npc_id 重复: {ids}")

    def test_sector_counts_match_table(self):
        # 逐 sector 计数与 VILLAGE_SECTORS 全等（结构一致性的双向锁）
        counts = Counter(seed.sector for seed in VILLAGE_ROSTER)
        self.assertEqual(dict(counts), VILLAGE_SECTORS)

    def test_key_env_split(self):
        # key 恰 2 人（chen/lily）、env 恰 23 人
        key_ids = sorted(seed.npc_id for seed in VILLAGE_ROSTER
                         if seed.kind == "key")
        self.assertEqual(key_ids, ["chen", "lily"])
        env_count = sum(1 for seed in VILLAGE_ROSTER if seed.kind == "env")
        self.assertEqual(env_count, 23)

    def test_farmer_occupation_exactly_five(self):
        # 单职业「农夫」类恰 5 人（farming 长尾基座）：id 集合按任务书
        # 花名册事实锁死；职业文案按类口径执法——农夫/农妇/雇农同属
        # 单一农耕职业类（含「农」字），与牧人/磨坊主等特殊职业区分。
        # 实现侧文案存在类内变体（王氏→农妇、孙小满→雇农），疑点已
        # 记入交付说明待 PM 终审裁定（精确文案 vs 类口径）。
        farmer_ids = {seed.npc_id for seed in VILLAGE_ROSTER
                      if seed.sector == "farming"
                      and seed.npc_id not in ("herder_ma", "miller_qian")}
        self.assertEqual(farmer_ids,
                         {"farmer_zhao", "farmer_wang", "zhao_daniu",
                          "cao_widow", "farmhand_sun"})
        self.assertEqual(len(farmer_ids), 5)
        for seed in VILLAGE_ROSTER:
            if seed.npc_id in farmer_ids:
                self.assertIn("农", seed.occupation,
                              msg=f"{seed.npc_id} 职业应为农夫类: "
                                  f"{seed.occupation}")

    def test_sector_counts_longtail_shape(self):
        # 长尾形状：farming 7 ≥ craft 5 ≥ 其余各 sector；最大阶层与中位数
        # 之比 ≥2（人口学阶层金字塔不是均匀分布）
        sectors = VILLAGE_SECTORS
        self.assertGreaterEqual(sectors["farming"], sectors["craft"])
        for name, count in sectors.items():
            if name not in ("farming", "craft"):
                self.assertGreaterEqual(sectors["craft"], count, msg=name)
        median = statistics.median(sorted(sectors.values()))
        self.assertGreaterEqual(sectors["farming"] / median, 2,
                                 msg=f"最大阶层 {sectors['farming']} / "
                                     f"中位数 {median} < 2，长尾坍缩")


# ========================================================================== #
# 2. TestNormalDistribution —— 正态形状统计断言（验收条款：分布断言）
# ========================================================================== #
class TestNormalDistribution(unittest.TestCase):
    """6D 截断正态 + 财富对数正态的形状统计（非仅值域合法性）。

    大样本隔离口径：generate_innate_attributes 循环 400 次（任务书建议，
    隔离属性生成本体）；全村口径：generate_village() 25 人小容量宽容带。
    """

    @classmethod
    def setUpClass(cls):
        rng = random.Random(BIG_SAMPLE_SEED)
        cls.samples = [generate_innate_attributes(rng)
                       for _ in range(BIG_SAMPLE_N)]

    def test_all_values_within_attribute_range(self):
        # 全值 ∈ ATTRIBUTE_RANGE [1.0, 10.0]（截断正态的截断锁）
        for sample in self.samples:
            for attr in ATTRS_6D:
                value = getattr(sample, attr)
                self.assertGreaterEqual(value, ATTRIBUTE_RANGE[0],
                                        msg=f"{attr}={value}")
                self.assertLessEqual(value, ATTRIBUTE_RANGE[1],
                                      msg=f"{attr}={value}")

    def test_attribute_means_in_band(self):
        # 每维均值 ∈ [4.5, 5.5]（μ=5.0 带宽容差）
        for attr in ATTRS_6D:
            mean = statistics.fmean(getattr(s, attr) for s in self.samples)
            self.assertGreaterEqual(mean, 4.5, msg=f"{attr} mean={mean:.4f}")
            self.assertLessEqual(mean, 5.5, msg=f"{attr} mean={mean:.4f}")

    def test_attribute_stdevs_in_band(self):
        # 每维样本标准差 ∈ [1.0, 1.5]（σ=1.25 与 alcohol_tol σ=1.5 共带宽）
        for attr in ATTRS_6D:
            sd = statistics.stdev([getattr(s, attr) for s in self.samples])
            self.assertGreaterEqual(sd, 1.0, msg=f"{attr} sd={sd:.4f}")
            self.assertLessEqual(sd, 1.5, msg=f"{attr} sd={sd:.4f}")

    def test_wealth_median_band_and_floor(self):
        # 财富：400 样本中位数 ∈ [14, 32]、最小值 ≥1 铜板
        wealths = [s.wealth for s in self.samples]
        median = statistics.median(wealths)
        self.assertGreaterEqual(median, 14, msg=f"median={median}")
        self.assertLessEqual(median, 32, msg=f"median={median}")
        self.assertGreaterEqual(min(wealths), 1)

    def test_village_wide_values_and_means(self):
        # 全村口径：25 人 6 维全值 ∈ [1,10]，每维全村均值 ∈ [3.5, 6.5]
        # （25 样本小容量宽容带；crc32 确定性种子，不 flaky）
        village = generate_village()
        self.assertEqual(len(village), 25)
        for attr in ATTRS_6D:
            values = [getattr(p.innate, attr) for p in village.values()]
            for value in values:
                self.assertGreaterEqual(value, ATTRIBUTE_RANGE[0],
                                        msg=f"{attr}={value}")
                self.assertLessEqual(value, ATTRIBUTE_RANGE[1],
                                     msg=f"{attr}={value}")
            mean = statistics.fmean(values)
            self.assertGreaterEqual(mean, 3.5, msg=f"{attr} mean={mean:.4f}")
            self.assertLessEqual(mean, 6.5, msg=f"{attr} mean={mean:.4f}")


# ========================================================================== #
# 3. TestZipfShape —— 齐普夫职业分布断言（验收条款：分布断言）
# ========================================================================== #
class TestZipfShape(unittest.TestCase):
    """职业长尾的齐普夫语义：农夫多/特殊职业 ≤2/非平顶/理论一致性。"""

    def test_farming_is_largest_sector(self):
        # 「农夫多」：farming 是最大 sector（齐普夫 top1 集中度）
        self.assertEqual(max(VILLAGE_SECTORS.values()),
                         VILLAGE_SECTORS["farming"])

    def test_special_sectors_capped_at_two(self):
        # 特殊职业类（medicine/education/guard/hunt）人数各 ≤2
        for sector in ("medicine", "education", "guard", "hunt"):
            self.assertLessEqual(VILLAGE_SECTORS[sector], 2, msg=sector)

    def test_longtail_not_flat(self):
        # 长尾单调性：降序计数非平顶，最大 ≥ 次大 ×1.4（整数交叉乘消除
        # 浮点误差——5*1.4 在二进制浮点下为 7.000...001，直接比较会误伤）
        desc = sorted(VILLAGE_SECTORS.values(), reverse=True)
        self.assertGreaterEqual(desc[0] * 10, desc[1] * 14,
                                msg=f"top1={desc[0]} top2={desc[1]} 平顶化")

    def test_zipf_theory_consistency(self):
        # 理论一致性：zipf(s=1.15) 的 top1 概率 ≥ 均匀分布的 3 倍
        self.assertGreaterEqual(zipf_probabilities(9)[0], 3 / 9,
                                msg=f"p1={zipf_probabilities(9)[0]:.4f}")


# ========================================================================== #
# 4. TestPyramidTiers —— 金字塔分档统计断言（验收条款：分布断言）
# ========================================================================== #
class TestPyramidTiers(unittest.TestCase):
    """缺陷/把柄分档形状：分档白名单（全村口径）+ 占比带宽（大样本 400）。

    tier 获取口径：ledger.all_tags() 中挂 INNATE 桶的 flaw_/secret_ 前缀
    条目的 tier 字段；大样本用 build_genesis_ledger(npc_id, rng) 直测
    挂载内核（T4 内核提取后的模块级公开 API）。
    """

    @classmethod
    def setUpClass(cls):
        rng = random.Random(BIG_SAMPLE_SEED)
        cls.ledgers = [build_genesis_ledger(f"probe_{i}", rng)
                       for i in range(BIG_SAMPLE_N)]
        cls.flaw_tiers = [tag.tier for ledger in cls.ledgers
                          for tag in innate_flaw_tags(ledger)]
        cls.secret_tiers = [tag.tier for ledger in cls.ledgers
                            for tag in innate_secret_tags(ledger)]

    def test_village_flaw_tier_whitelist(self):
        # 全村 25 人：缺陷 tier 全部 ∈ {"uncommon","rare","extreme"}
        # （common 档被分档规则剔除——太普通的缺陷玩家记不住）
        village = generate_village()
        for npc_id, profile in village.items():
            tags = innate_flaw_tags(profile.ledger)
            self.assertTrue(tags, msg=f"{npc_id} 缺陷缺失")
            for tag in tags:
                self.assertIn(tag.tier, {"uncommon", "rare", "extreme"},
                              msg=f"{npc_id} flaw tier={tag.tier}")

    def test_village_secret_tier_whitelist(self):
        # 全村 25 人：把柄 tier 全部 ∈ {"rare","extreme"}
        # （rare 起步——把柄越稀有，勒索价值越高）
        village = generate_village()
        for npc_id, profile in village.items():
            tags = innate_secret_tags(profile.ledger)
            self.assertTrue(tags, msg=f"{npc_id} 把柄缺失")
            for tag in tags:
                self.assertIn(tag.tier, {"rare", "extreme"},
                              msg=f"{npc_id} secret tier={tag.tier}")

    def test_flaw_tier_shares(self):
        # 大样本 400：缺陷 uncommon ∈ [0.55,0.80]、rare ∈ [0.15,0.37]、
        # extreme ∈ [0.01,0.15]（金字塔权重剔除 common 后重归一化）
        self.assertEqual(len(self.flaw_tiers), BIG_SAMPLE_N,
                         msg="每本账本应恰挂 1 条缺陷")
        counts = Counter(self.flaw_tiers)
        self.assertGreaterEqual(counts["uncommon"] / BIG_SAMPLE_N, 0.55,
                                msg=f"uncommon={counts['uncommon']}")
        self.assertLessEqual(counts["uncommon"] / BIG_SAMPLE_N, 0.80,
                             msg=f"uncommon={counts['uncommon']}")
        self.assertGreaterEqual(counts["rare"] / BIG_SAMPLE_N, 0.15,
                                msg=f"rare={counts['rare']}")
        self.assertLessEqual(counts["rare"] / BIG_SAMPLE_N, 0.37,
                             msg=f"rare={counts['rare']}")
        self.assertGreaterEqual(counts["extreme"] / BIG_SAMPLE_N, 0.01,
                                msg=f"extreme={counts['extreme']}")
        self.assertLessEqual(counts["extreme"] / BIG_SAMPLE_N, 0.15,
                             msg=f"extreme={counts['extreme']}")

    def test_secret_tier_shares(self):
        # 大样本 400：把柄 rare ∈ [0.70,0.92]（rare/extreme 重归一化 8:2）
        self.assertEqual(len(self.secret_tiers), BIG_SAMPLE_N,
                         msg="每本账本应恰挂 1 条把柄")
        counts = Counter(self.secret_tiers)
        self.assertEqual(set(counts), {"rare", "extreme"})
        self.assertGreaterEqual(counts["rare"] / BIG_SAMPLE_N, 0.70,
                                msg=f"rare={counts['rare']}")
        self.assertLessEqual(counts["rare"] / BIG_SAMPLE_N, 0.92,
                             msg=f"rare={counts['rare']}")


# ========================================================================== #
# 5. TestKeyEnvLayering —— key/env 分层（验收条款：分层）
# ========================================================================== #
class TestKeyEnvLayering(unittest.TestCase):
    """心智分层：key NPC 无压缩稳态（引擎常驻账本接管人格），环境 NPC
    持 EnvMind 4D 平稳带稳态；EnvMind 字段与 8D 同名（共线性纪律）。"""

    @classmethod
    def setUpClass(cls):
        cls.village = generate_village()
        cls.key_profiles = {npc_id: p for npc_id, p in cls.village.items()
                            if p.seed.kind == "key"}
        cls.env_profiles = {npc_id: p for npc_id, p in cls.village.items()
                            if p.seed.kind == "env"}

    def test_key_npcs_mind_none_ledger_present(self):
        # chen/lily：mind is None（人格由引擎常驻账本接管），ledger 非空
        self.assertEqual(set(self.key_profiles), {"chen", "lily"})
        for npc_id, profile in self.key_profiles.items():
            self.assertIsNone(profile.mind, msg=f"{npc_id} key 不应有 EnvMind")
            self.assertTrue(profile.ledger.all_tags(),
                            msg=f"{npc_id} 账本为空")

    def test_env_minds_calm_band(self):
        # 23 env：mind 为 EnvMind 且四维落在常人平稳带
        # （p_fatigue/p_hunger ∈ [0.10,0.30]、e_P ∈ [0.40,0.60]、
        #   S_stress ∈ [0.05,0.25]——背景稳态不是戏剧化极端值）
        self.assertEqual(len(self.env_profiles), 23)
        for npc_id, profile in self.env_profiles.items():
            mind = profile.mind
            self.assertIsInstance(mind, EnvMind, msg=npc_id)
            self.assertGreaterEqual(mind.p_fatigue, 0.10, msg=npc_id)
            self.assertLessEqual(mind.p_fatigue, 0.30, msg=npc_id)
            self.assertGreaterEqual(mind.p_hunger, 0.10, msg=npc_id)
            self.assertLessEqual(mind.p_hunger, 0.30, msg=npc_id)
            self.assertGreaterEqual(mind.e_P, 0.40, msg=npc_id)
            self.assertLessEqual(mind.e_P, 0.60, msg=npc_id)
            self.assertGreaterEqual(mind.S_stress, 0.05, msg=npc_id)
            self.assertLessEqual(mind.S_stress, 0.25, msg=npc_id)

    def test_envmind_field_names_match_inner_state(self):
        # 共线性纪律：EnvMind 字段名集合 == 4D 压缩稳态契约
        # {"p_fatigue","p_hunger","e_P","S_stress"}——与 8D 同名，无新维度
        names = {field.name for field in dataclasses.fields(EnvMind)}
        self.assertEqual(names, {"p_fatigue", "p_hunger", "e_P", "S_stress"})
        # frozen 契约：稳态不可变（frozen dataclass 赋值即抛错）
        mind = next(iter(self.env_profiles.values())).mind
        with self.assertRaises(Exception):
            mind.p_fatigue = 0.99

    def test_generate_villager_single_env_seed(self):
        # 分层生成入口：generate_villager(任一 env seed) 返回带 EnvMind 的
        # VillagerProfile（单点行为与 generate_village 全量口径一致）
        env_seed = next(seed for seed in VILLAGE_ROSTER if seed.kind == "env")
        profile = generate_villager(env_seed)
        self.assertIsInstance(profile, VillagerProfile)
        self.assertEqual(profile.seed.npc_id, env_seed.npc_id)
        self.assertIsInstance(profile.mind, EnvMind)


# ========================================================================== #
# 6. TestLedgerReuseAndDeterminism —— 批量挂载常驻化复用（验收条款：
#    批量挂载复用 + 确定性，核心验收）
# ========================================================================== #
class TestLedgerReuseAndDeterminism(unittest.TestCase):
    """generate_village 确定性 + 与引擎常驻化同源 + label 全部出自池。

    同源证据：generate_village()["chen"].ledger 与引擎
    _build_tag_ledger("chen") 的 flaw/secret label 全等——同一 crc32 种子
    同一挂载内核（build_genesis_ledger）的直接证据，两套挂载逻辑零漂移。
    """

    @staticmethod
    def _mind_signature(profile):
        """EnvMind 四维签名（None 传 None——key NPC 无稳态）。"""
        mind = profile.mind
        if mind is None:
            return None
        return tuple(getattr(mind, field.name)
                     for field in dataclasses.fields(EnvMind))

    def test_generate_village_deterministic(self):
        # 确定性：两次调用逐 NPC 的 innate 六维值、flaw_label、secret_label、
        # mind 四维全等（crc32(npc_id) 种子，跨调用/跨进程稳定）
        village_a = generate_village()
        village_b = generate_village()
        self.assertEqual(set(village_a), set(village_b))
        for npc_id, profile_a in village_a.items():
            profile_b = village_b[npc_id]
            self.assertEqual(profile_a.innate.to_dict(),
                             profile_b.innate.to_dict(), msg=npc_id)
            self.assertEqual(profile_a.flaw_label, profile_b.flaw_label,
                             msg=npc_id)
            self.assertEqual(profile_a.secret_label, profile_b.secret_label,
                             msg=npc_id)
            self.assertEqual(self._mind_signature(profile_a),
                             self._mind_signature(profile_b), msg=npc_id)

    def test_chen_ledger_same_source_as_engine(self):
        # 与引擎常驻化同源：引擎 _build_tag_ledger("chen")（薄壳转调
        # build_genesis_ledger）与 generate_village()["chen"].ledger 的
        # flaw/secret label 全等（空场景引擎，惯例见 test_tag_residency）
        engine = NPCEngine(llm=MockLLMProvider(),
                           npc_configs=[], background_configs=[])
        engine_ledger = engine._build_tag_ledger("chen")
        village_ledger = generate_village()["chen"].ledger
        engine_flaws = [tag.label for tag in innate_flaw_tags(engine_ledger)]
        village_flaws = [tag.label for tag in innate_flaw_tags(village_ledger)]
        engine_secrets = [tag.label for tag in innate_secret_tags(engine_ledger)]
        village_secrets = [tag.label for tag in innate_secret_tags(village_ledger)]
        self.assertTrue(engine_flaws and village_flaws)
        self.assertEqual(engine_flaws, village_flaws,
                         msg="chen 缺陷挂载与引擎常驻化不同源")
        self.assertTrue(engine_secrets and village_secrets)
        self.assertEqual(engine_secrets, village_secrets,
                         msg="chen 把柄挂载与引擎常驻化不同源")
        # profile 冗余字段与账本条目一致（label 不是账本外的孤立文案）
        self.assertIn(generate_village()["chen"].flaw_label, village_flaws)
        self.assertIn(generate_village()["chen"].secret_label, village_secrets)

    def test_all_labels_nonempty_and_from_pools(self):
        # 25 人每人 flaw_label/secret_label 非空且来自 FLAW_POOL/SECRET_POOL
        # 的中文 label（import 池对账，排除巧合文案）
        flaw_labels = {entry[1] for entry in FLAW_POOL}
        secret_labels = {entry[1] for entry in SECRET_POOL}
        for npc_id, profile in generate_village().items():
            self.assertIsInstance(profile.flaw_label, str)
            self.assertTrue(profile.flaw_label, msg=f"{npc_id} flaw_label 空")
            self.assertIn(profile.flaw_label, flaw_labels, msg=npc_id)
            self.assertIsInstance(profile.secret_label, str)
            self.assertTrue(profile.secret_label, msg=f"{npc_id} secret_label 空")
            self.assertIn(profile.secret_label, secret_labels, msg=npc_id)


# ========================================================================== #
# 7. TestNewcomerConfigs —— 15 份新住民配置完整性（验收条款：配置完整性）
# ========================================================================== #
class TestNewcomerConfigs(unittest.TestCase):
    """configs/village_roster 15 份配置：字段齐备、id 与花名册新增 15 人
    全等、居所/工作地全部注册为地点、夜班/早出作息与共居关系锁定。"""

    REQUIRED_KEYS = ("id", "name", "role", "summary",
                     "location_id", "residence", "schedule")

    @classmethod
    def setUpClass(cls):
        cls.configs = load_newcomer_configs()

    def _config_of(self, npc_id):
        for cfg in self.configs:
            if cfg["id"] == npc_id:
                return cfg
        self.fail(f"缺少新住民配置: {npc_id}")

    def test_config_dir_name(self):
        # 契约：新住民配置目录指向 configs/village_roster
        self.assertEqual(os.path.basename(NEWCOMER_CONFIG_DIR),
                         "village_roster")

    def test_exactly_fifteen_configs(self):
        # 恰 15 份（25 - 2 key - 8 旧背景），id 无重复
        self.assertEqual(len(self.configs), 15)
        self.assertEqual(len({cfg["id"] for cfg in self.configs}), 15)

    def test_required_keys_non_empty(self):
        # 每份含 id/name/role/summary/location_id/residence/schedule 非空键
        for cfg in self.configs:
            for key in self.REQUIRED_KEYS:
                self.assertIn(key, cfg, msg=f"{cfg.get('id')} 缺 {key}")
                self.assertTrue(cfg[key], msg=f"{cfg.get('id')} {key} 为空")
            self.assertIsInstance(cfg["schedule"], dict,
                                  msg=cfg["id"])
            self.assertTrue(cfg["schedule"], msg=cfg["id"])

    def test_ids_match_roster_newcomers(self):
        # id 与 VILLAGE_ROSTER 中 env 新增 15 人集合全等（旧 8 背景 NPC
        # 已有 configs/background_npcs/ 配置，不在 village_roster 目录）
        roster_env_ids = {seed.npc_id for seed in VILLAGE_ROSTER
                          if seed.kind == "env"}
        expected_newcomers = roster_env_ids - legacy_background_ids()
        self.assertEqual(len(expected_newcomers), 15)
        self.assertEqual({cfg["id"] for cfg in self.configs},
                         expected_newcomers)

    def test_residences_and_workplaces_registered(self):
        # 每份 residence 与 location_id ∈ 已注册地点集合
        # （从 configs/locations.json 动态读取比对，不硬编码；T4-a 扩容后
        # 该文件为 33 地点 = 既有 17 ∪ NEW 14 ∪ farm/river）
        valid = legacy_location_ids() | set(NEW_LOCATION_IDS)
        self.assertEqual(len(legacy_location_ids()), 33)
        for cfg in self.configs:
            self.assertIn(cfg["residence"], valid,
                          msg=f"{cfg['id']} residence 未注册: {cfg['residence']}")
            self.assertIn(cfg["location_id"], valid,
                          msg=f"{cfg['id']} location_id 未注册: {cfg['location_id']}")

    def test_watchman_night_shift(self):
        # watchman_diao 夜班反转：20 点 WORKING、凌晨 3 点 SLEEPING
        schedule = self._config_of("watchman_diao")["schedule"]
        self.assertEqual(schedule.get("20"), "WORKING")
        self.assertEqual(schedule.get("3"), "SLEEPING")

    def test_herder_dawn_schedule(self):
        # herder_ma 04:00 早出放牧：schedule 含 "4": WORKING
        schedule = self._config_of("herder_ma")["schedule"]
        self.assertEqual(schedule.get("4"), "WORKING")

    def test_shared_residences(self):
        # 共居关系：雇农住赵家、药童住秦家、酒馆伙计住孙家、学童住学堂
        for npc_id, home in (("farmer_wang", "house_zhao"),
                              ("zhao_daniu", "house_zhao"),
                              ("farmhand_sun", "house_zhao"),
                              ("herb_boy", "house_qin"),
                              ("tavern_aman", "house_sun"),
                              ("boy_shitou", "house_school")):
            self.assertEqual(self._config_of(npc_id)["residence"], home,
                             msg=npc_id)


# ========================================================================== #
# 8. TestEnvZeroLLM —— 环境 NPC 零 LLM 双口径（验收条款：零 LLM；
#    T4 人口学执法）
# ========================================================================== #
class TestEnvZeroLLM(unittest.TestCase):
    """15 新背景 NPC 全天 24 tick 纯规则驱动：调用计数与 token 双口径零消耗。

    实体口径：npc_configs 显式传 Persona.load_all()（chen/lily 两核心）+
    background_configs 显式传 load_newcomer_configs()（15 新背景）→
    engine.py:66-75 显式传入按传入值，生产目录 8 旧背景不加载，引擎内
    2+15=17 实体（任务书预期口径，不断言 25）。
    """

    def test_engine_scale_17_npcs_33_locations(self):
        # 17 实体口径：2 核心 + 15 新背景全在场；33 地点 = 既有 17 ∪
        # NEW 14 ∪ farm/river（T4-a 地点扩容后 locations.json 全量注册）
        engine = make_full_village_engine()
        self.assertEqual(len(engine.npcs), 2)
        self.assertEqual(set(engine.npcs), {"chen", "lily"})
        newcomer_ids = {cfg["id"] for cfg in load_newcomer_configs()}
        self.assertEqual(len(newcomer_ids), 15)
        self.assertEqual(set(engine.background_npcs), newcomer_ids)
        for npc_id in newcomer_ids | {"chen", "lily"}:
            self.assertIn(npc_id, engine.world.entities, msg=npc_id)
        legacy = legacy_location_ids()
        self.assertEqual(len(legacy), 33)
        self.assertEqual(len(engine.world.locations), 33)
        for loc_id in legacy | set(NEW_LOCATION_IDS):
            self.assertIn(loc_id, engine.world.locations, msg=loc_id)

    def test_full_day_tick_zero_llm_calls_and_tokens(self):
        # 双口径之一：全天 24 次 tick（1 小时步长，跨完整昼夜）零 LLM
        # 调用、零 token、token 统计表空——背景 NPC 规则反应不产生任何
        # LLM 消耗；对照：作息确有驱动（活动发生了，但零 token）
        engine = make_full_village_engine()
        self.assertEqual(engine.llm.call_count, 0)
        self.assertEqual(engine.llm.total_tokens_used, 0)
        applied = {}
        for _ in range(24):
            result = engine.tick(60)
            applied.update(result["schedule_applied"])
        self.assertEqual(engine.llm.call_count, 0,
                         msg="24 tick 产生了 LLM 调用")
        self.assertEqual(engine.llm.total_tokens_used, 0,
                         msg="24 tick 产生了 token")
        self.assertEqual(engine.status()["token_stats"]["by_npc"], {})
        newcomer_ids = {cfg["id"] for cfg in load_newcomer_configs()}
        self.assertTrue(newcomer_ids & set(applied),
                        msg="新背景 NPC 全天未驱动任何作息，零 token 断言失效")

    def test_slow_brain_single_call_token_accounting(self):
        # 双口径之二：对 chen 说一句快脑不可拦截的复杂话（14 字，超出全部
        # 快脑句长上限闸门，慢脑必达），恰 1 次 LLM 调用；token 总量调用前
        # 为 0、后 >0，且归属仅核心 NPC——15 背景全程零 token 记录
        engine = make_full_village_engine()
        before = engine.status()["token_stats"]
        self.assertEqual(before["total_tokens"], 0)
        self.assertEqual(before["llm_calls"], 0)
        self.assertEqual(before["by_npc"], {})
        result = engine.player_says("最近铁价如何？我想订一批农具", "chen")
        self.assertTrue(result["ok"])
        self.assertEqual(engine.llm.call_count, 1,
                         msg=f"应恰 1 次慢脑调用，实际 {engine.llm.call_count}")
        after = engine.status()["token_stats"]
        self.assertGreater(after["total_tokens"], 0)
        self.assertEqual(set(after["by_npc"]), {"chen"},
                         msg="token 归属应仅核心 NPC")
        self.assertGreater(after["by_npc"]["chen"]["total_tokens"], 0)
        newcomer_ids = {cfg["id"] for cfg in load_newcomer_configs()}
        self.assertFalse(newcomer_ids & set(after["by_npc"]),
                         msg="背景 NPC 不应出现在 token 统计中")

    def test_newcomer_to_dict_contract(self):
        # 15 新背景 NPC 的 to_dict 含 residence/schedule 字段且
        # type=="background"（背景轻量实体契约，引擎 status 可见）
        engine = make_full_village_engine()
        for cfg in load_newcomer_configs():
            data = engine.background_npcs[cfg["id"]].to_dict()
            self.assertEqual(data["type"], "background", msg=cfg["id"])
            self.assertIn("residence", data, msg=cfg["id"])
            self.assertIn("schedule", data, msg=cfg["id"])
            self.assertEqual(data["residence"], cfg["residence"], msg=cfg["id"])
            self.assertEqual(data["schedule"], cfg["schedule"], msg=cfg["id"])


# ========================================================================== #
# 9. TestDescribeNarrative —— 叙事可读性防护（PM 人工终审的测试托底）
# ========================================================================== #
class TestDescribeNarrative(unittest.TestCase):
    """describe_villager 中文叙事摘要：非空且含 name/occupation/
    flaw_label/secret_label（人口学档案口径——把柄进叙事档案是文档语义，
    与"把柄不进决策 prompt"的隔离纪律分属两个消费面）。"""

    @classmethod
    def setUpClass(cls):
        cls.village = generate_village()

    def test_describe_contains_identity_and_labels(self):
        # 每 1 人输出非空、含该人 name 与 occupation、含 flaw_label、
        # 含 secret_label
        for npc_id, profile in self.village.items():
            text = describe_villager(profile)
            self.assertIsInstance(text, str, msg=npc_id)
            self.assertTrue(text, msg=npc_id)
            self.assertIn(profile.seed.name, text, msg=npc_id)
            self.assertIn(profile.seed.occupation, text, msg=npc_id)
            self.assertIn(profile.flaw_label, text, msg=npc_id)
            self.assertIn(profile.secret_label, text, msg=npc_id)

    def test_herder_ma_narrative_mentions_shepherd(self):
        # 牧人的叙事必须含「牧人」字样（职业中文可读性锚点）
        text = describe_villager(self.village["herder_ma"])
        self.assertIn("牧人", text)


if __name__ == "__main__":
    unittest.main()

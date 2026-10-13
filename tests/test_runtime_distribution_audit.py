"""T4 收官 · 运转期分布对账测试（审查指令 1，2026-10-14）。

对账背景：T4-b 第二小步落地的 EnvMind 每小时演化在运转期被探针发现
e_P 维机制缺陷——原 SLEEPING 段 -0.03/h 单调下移使每日净变化约 -0.16、
17/22 低于生成带下界，多日运转全村情绪必向 0 塌陷，威胁「持续运转
的活村庄」。修正（engine/background_npc.py）：e_P 维从单调增量改为
段内均衡收敛（E_P_EQUILIBRIUM_TARGETS：WORKING 0.45/IDLE 0.68/
SLEEPING 0.50，E_P_CONVERGENCE 0.5），三维单调步长（生理/压力）保留。

本文件为统计口径对账（非逐值口径），两个测试类：

- TestEnvMindRuntimeDriftAudit：真实村庄默认引擎全天运转
  （tick(60)×24，08:00→次日 08:00 完整游戏日，与既有测试同口径）后，
  22 个 env NPC 的 4D EnvMind 分布形状对账——每维断言均值漂移带、
  σ 形状带、[0,1] 值域，外加 e_P 三段分化结构与多日不塌陷证明；
- TestPopulationStructureInvariance：运转只演化运行时状态（位置/
  EnvMind），不改变人口结构（职业/属性/标签账本为创生期常量）——
  花名册直方图、generate_village 再生成、status() 角色分布、key/env
  分层、齐普夫形状，全天运转前后逐项一致。

定带纪律（审查指令 1 硬要求）：带宽基于 2026-10-14 修正后探针实测
（.temp 探针口径：statistics.pstdev 总体标准差，确定性村庄单日双跑
逐字段一致），每侧带边距实测值余量 ≥30%，禁止拍脑袋数字锁。实测
锚（初值均值→后均值 / 初值σ→后σ）：
  p_fatigue 0.1913→0.2509 / 0.0549→0.0943（漂移 +0.0596，σ×1.72）
  p_hunger  0.1926→0.2279 / 0.0559→0.1113（漂移 +0.0353，σ×1.99）
  e_P       0.5037→0.4653 / 0.0643→0.0382（漂移 -0.0385，修正前
            探针为 0.504→0.332 单调下移 17/22 超带——机制缺陷已修复）
  S_stress  0.1674→0.0314 / 0.0640→0.0118（漂移 -0.1361，地板聚集）

漂移成因可解释（叙事判读，活村庄的合理形态而非扭曲）：
- p_fatigue 右移：劳作段 +0.08/h 累积（牧人清晨上牧场、午后打哈欠）
  与清晨作息分化共同贡献；终点 σ 放大是 22 人各自处于不同作息段
  数小时的分化形态（更夫夜班刚睡、农夫已劳作 3 小时），不是爆炸；
- p_hunger 温和右移：劳作日间消耗（+0.06/h）盖过歇晌用餐回收；
- e_P 三段分化：均衡收敛带（WORKING≈0.45 平淡/SLEEPING≈0.50 归平/
  IDLE≈0.68 带笑）；终点漂移为负是 08:00 终点 20/22 处于 WORKING 段
  的结构性小幅左移（0.504→0.465），非单调塌陷——多日运转分布逐日
  完全稳定（见 test_e_p_multi_day_no_collapse）；
- S_stress 左移+σ 收缩：IDLE/SLEEPING 双释放步长叠加 clamp 0 地板
  聚集——全村松弛态，σ 下限守卫防分布被压扁到无个体差异。

终点口径：次日 08:00，各 NPC 处于不同作息段数小时，分布由作息分化
贡献（创生期生成带只是 08:00 起点的初始分布，运转后形状由作息语义
接管——这正是「活村庄」运转期形态的对账依据）。
"""

import statistics
import unittest
from collections import Counter

from engine.background_npc import E_P_EQUILIBRIUM_TARGETS
from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.tag_mount import TagPhase
from engine.village_genesis import (VILLAGE_ROSTER, VILLAGE_SECTORS,
                                    generate_village)

# ---------------------------------------------------------------------- #
# 对账口径常量
# ---------------------------------------------------------------------- #
ENV_IDS = tuple(seed.npc_id for seed in VILLAGE_ROSTER if seed.kind == "env")
KEY_IDS = tuple(seed.npc_id for seed in VILLAGE_ROSTER if seed.kind == "key")
ENV_MIND_FIELDS = ("p_fatigue", "p_hunger", "e_P", "S_stress")

# 实测锚定带宽表：{维度: (漂移带下界, 漂移带上界, σ带下界, σ带上界)}。
# 每维带边距 2026-10-14 修正后探针实测值的余量（以 |实测| 为基）：
#   p_fatigue 漂移 +0.0596 → [+0.040, +0.080]（余量 33%/34%）
#            σ 0.0943 → [0.050, 0.150]（余量 47%/59%）
#   p_hunger  漂移 +0.0353 → [+0.020, +0.055]（余量 43%/56%）
#            σ 0.1113 → [0.060, 0.170]（余量 46%/53%）
#   e_P       漂移 -0.0385 → [-0.060, -0.015]（余量 56%/61%）
#            σ 0.0382 → [0.020, 0.060]（余量 48%/57%）
#   S_stress  漂移 -0.1361 → [-0.190, -0.090]（余量 40%/34%）
#            σ 0.0118 → [0.005, 0.050]（余量 58%/324%，上限宽——地板
#            聚集后若有少数高压个体冒头亦在形状带内）
DRIFT_BANDS = {
    "p_fatigue": (0.040, 0.080),
    "p_hunger": (0.020, 0.055),
    "e_P": (-0.060, -0.015),
    "S_stress": (-0.190, -0.090),
}
SIGMA_BANDS = {
    "p_fatigue": (0.050, 0.150),
    "p_hunger": (0.060, 0.170),
    "e_P": (0.020, 0.060),
    "S_stress": (0.005, 0.050),
}

# e_P 三段分化收敛半径：段内均衡位 ±0.06。依据：WORKING 群（终点
# n=20）实测均值 0.4551 距均衡位 0.45 仅 0.0051；IDLE 群（n=1）终点
# 仅闲适 2 小时，半衰期 1 小时的 2 小时残余差距 0.045（0.68-0.23×
# 0.5²=0.635）在半径内，多闲适一小时将进一步收敛。
E_P_SEGMENT_RADIUS = 0.06


def run_full_day(days: int = 1) -> NPCEngine:
    """默认村庄引擎运转完整游戏日（tick(60)×24×days，08:00 起点）。

    llm=MockLLMProvider 仅替换 LLM 供应商（全程 tick 零 LLM 调用，
    供应商不参与），村庄构造路径与生产/演示完全一致：默认 22 背景
    （含 env_mind_for 注入）+ 3 核心。
    """
    engine = NPCEngine(llm=MockLLMProvider())
    for _ in range(24 * days):
        engine.tick(60)
    return engine


def innate_flaw_tags(ledger):
    """账本 INNATE 桶缺陷条目（沿 test_village_genesis.py 同款口径）。"""
    return tuple(t for t in ledger.all_tags()
                 if t.phase is TagPhase.INNATE and t.tag_id.startswith("flaw_"))


def innate_secret_tags(ledger):
    """账本 INNATE 桶把柄条目（沿 test_village_genesis.py 同款口径）。"""
    return tuple(t for t in ledger.all_tags()
                 if t.phase is TagPhase.INNATE and t.tag_id.startswith("secret_"))


def profile_fingerprint(profile):
    """村民人口学指纹：(职业, 缺陷标签, 缺陷tier, 把柄标签, 把柄tier)。

    覆盖任务书口径——职业分布、每人 flaw_label/secret_label 逐字、
    tier 占比（tier 进指纹逐人锁定，占比随之锁定）。
    """
    flaw = innate_flaw_tags(profile.ledger)[0]
    secret = innate_secret_tags(profile.ledger)[0]
    return (profile.seed.occupation, profile.flaw_label, flaw.tier,
            profile.secret_label, secret.tier)


# ========================================================================== #
# 1. TestEnvMindRuntimeDriftAudit —— EnvMind 4D 运转期分布对账
# ========================================================================== #
class TestEnvMindRuntimeDriftAudit(unittest.TestCase):
    """EnvMind 4D 运转期分布对账（统计口径）：真实村庄默认引擎全天
    运转后，各维均值漂移在叙事带内、σ 在形状带内（下限防压扁、上限防
    爆炸）、值域 [0,1]、22 env 全员有快照；e_P 三段分化 + 多日不塌陷
    为修正机制的运转期直接证明。"""

    @classmethod
    def setUpClass(cls):
        # 初值：从 village_genesis.generate_village() 收集（创生期生成带）
        village = generate_village()
        cls.init_values = {
            field: [getattr(village[npc_id].mind, field) for npc_id in ENV_IDS]
            for field in ENV_MIND_FIELDS
        }
        # 运转后：默认引擎 24×tick(60)（08:00→次日 08:00 完整游戏日）
        cls.engine = run_full_day(1)
        cls.post_values = {
            field: [getattr(cls.engine.background_npcs[npc_id].mind, field)
                    for npc_id in ENV_IDS]
            for field in ENV_MIND_FIELDS
        }

    def test_audit_scale_all_22_env_minds_snapshotted(self):
        """对账规模前置：22 个 env NPC 全员注入 env_mind（快照非 None）。"""
        self.assertEqual(len(ENV_IDS), 22,
                         msg="花名册 env 层应为 22 人（3+22 分层对账前置）")
        self.assertEqual(set(self.engine.background_npcs), set(ENV_IDS),
                         msg="引擎背景层应恰为花名册 22 个 env NPC（对账对象"
                             "完整性——运转不增删人口）")
        for npc_id in ENV_IDS:
            self.assertIsNotNone(
                self.engine.background_npcs[npc_id].mind,
                msg=f"{npc_id} 应经 env_mind_for 注入 4D EnvMind（缺快照则"
                    f"分布对账样本失真——统计口径要求全员在场）")

    def test_all_values_within_unit_range(self):
        """值域硬不变量：运转后 4D 全部值 ∈ [0,1]（演化 clamp 不越界）。"""
        for field, values in self.post_values.items():
            for value in values:
                self.assertGreaterEqual(value, 0.0,
                                        msg=f"{field}={value:.4f} 不得越下界 0.0")
                self.assertLessEqual(value, 1.0,
                                     msg=f"{field}={value:.4f} 不得越上界 1.0"
                                         f"（玩家可感知口径：状态值溢出会破坏"
                                         f"变体带文案的触发语义）")

    def test_mean_drift_within_narrative_bands(self):
        """各维均值漂移在叙事带内（带外即红）：劳作累积右移×2、压力地板
        左移、e_P 结构性小幅左移不塌陷——漂移成因可解释是对账判读，
        带宽距 2026-10-14 实测漂移每侧余量 ≥30%。"""
        for field in ENV_MIND_FIELDS:
            init_mean = statistics.mean(self.init_values[field])
            post_mean = statistics.mean(self.post_values[field])
            drift = post_mean - init_mean
            low, high = DRIFT_BANDS[field]
            self.assertGreaterEqual(
                drift, low,
                msg=f"{field} 全天漂移 {drift:+.4f} 应 ≥ {low:+.3f}（实测锚"
                    f"{self._measured_anchor(field)}；跌破下界=漂移形态超出"
                    f"叙事带——活村庄的日形态不应漂移过猛）")
            self.assertLessEqual(
                drift, high,
                msg=f"{field} 全天漂移 {drift:+.4f} 应 ≤ {high:+.3f}（实测锚"
                    f"{self._measured_anchor(field)}；冲破上界=作息演化失去"
                    f"日平衡（该睡回低位/该收敛归位），多日运转必爆带）")

    def test_sigma_shape_within_bands(self):
        """各维 σ 在形状带内：下限防「分布被压扁到无个体差异」（22 人
        变成一个人格），上限防「爆炸」（作息噪声失控）；S_stress 收缩
        至地板聚集带是压力释放的叙事合理形态，σ 下限仍保住个体差异。"""
        for field in ENV_MIND_FIELDS:
            post_sigma = statistics.pstdev(self.post_values[field])
            init_sigma = statistics.pstdev(self.init_values[field])
            low, high = SIGMA_BANDS[field]
            self.assertGreaterEqual(
                post_sigma, low,
                msg=f"{field} 运转后 σ={post_sigma:.4f} 应 ≥ {low:.3f}"
                    f"（初值 σ={init_sigma:.4f}；跌破下界=分布被压扁到无"
                    f"个体差异——背景 NPC 失去人口学分化，变体带沦为"
                    f"全村同脸）")
            self.assertLessEqual(
                post_sigma, high,
                msg=f"{field} 运转后 σ={post_sigma:.4f} 应 ≤ {high:.3f}"
                    f"（初值 σ={init_sigma:.4f}；冲破上界=个体差异爆炸，"
                    f"作息语义失稳）")

    def test_e_p_three_segment_differentiation(self):
        """e_P 三段分化结构（修正核心验收）：终点按各 NPC 末段分组，
        组均值锚定段内均衡位 E_P_EQUILIBRIUM_TARGETS ± 收敛半径——
        劳作平淡/闲适带笑/睡眠归平；玩家可感知口径：三段各自对应
        变体带的可达/不可达梯度，情绪不再全村同值也不再向 0 塌陷。"""
        by_state = {}
        for npc_id in ENV_IDS:
            state = self.engine.background_npcs[npc_id].to_dict()["current_state"]
            self.assertIn(state, E_P_EQUILIBRIUM_TARGETS,
                          msg=f"{npc_id} 末段 {state} 应为已配置均衡位的作息段")
            by_state.setdefault(state, []).append(
                getattr(self.engine.background_npcs[npc_id].mind, "e_P"))
        self.assertTrue(by_state,
                        msg="终点分组不得为空（规模前置失败）")
        for state, values in sorted(by_state.items()):
            target = E_P_EQUILIBRIUM_TARGETS[state]
            group_mean = statistics.mean(values)
            self.assertLessEqual(
                abs(group_mean - target), E_P_SEGMENT_RADIUS,
                msg=f"末段 {state} 群（n={len(values)}）e_P 均值 "
                    f"{group_mean:.4f} 应在均衡位 {target}±{E_P_SEGMENT_RADIUS}"
                    f" 收敛半径内（均衡收敛机制的结构签名：劳作平淡 0.45/"
                    f"闲适带笑 0.68/睡眠归平 0.50——偏离即收敛机制未按段"
                    f"分化运转）")

    def test_e_p_multi_day_no_collapse(self):
        """e_P 多日不塌陷（机制缺陷修复的直接运转期证明）：3 个完整
        游戏日连转，全村 e_P 不低于生成带下界 0.40，且分布不随天数
        漂移。修正前原机制每日净变化约 -0.16，3 日理论塌至 ~0.02，
        全村情绪归零、变体带永久不可达；修正后均衡收敛保证值域
        [min(初值,段内目标), max(初值,段内目标)] ⊆ [0.40, 0.68]。"""
        engine_3d = run_full_day(3)
        e_p_3d = [engine_3d.background_npcs[npc_id].mind.e_P
                  for npc_id in ENV_IDS]
        for npc_id, value in zip(ENV_IDS, e_p_3d):
            self.assertGreaterEqual(
                value, 0.40,
                msg=f"3 日连转后 {npc_id} e_P={value:.4f} 应 ≥ 0.40"
                    f"（生成带下界；均衡收敛机制保证值不跌破初值与段内"
                    f"目标的下确界——塌陷即机制缺陷复现，威胁『持续运转"
                    f"的活村庄』）")
        day1_mean = statistics.mean(self.post_values["e_P"])
        day3_mean = statistics.mean(e_p_3d)
        self.assertLessEqual(
            abs(day3_mean - day1_mean), 0.02,
            msg=f"e_P 3 日均值 {day3_mean:.4f} 与 1 日均值 {day1_mean:.4f} "
                f"差应 ≤ 0.02（实测逐日完全稳定——日循环收敛为周期稳态，"
                f"分布不随天数漂移；漂移即向塌陷或爆带方向滑动）")

    @staticmethod
    def _measured_anchor(field):
        """实测锚注脚（断言 msg 里写明带的实证依据，防拍脑袋数字锁）。"""
        anchors = {
            "p_fatigue": "漂移 +0.0596，劳作累积右移+清晨作息分化",
            "p_hunger": "漂移 +0.0353，劳作日间消耗温和右移",
            "e_P": "漂移 -0.0385，修正前为 -0.172 单调下移（17/22 超带），"
                   "修正后终点 20/22 处于 WORKING 段的结构性小幅左移",
            "S_stress": "漂移 -0.1361，IDLE/SLEEPING 双释放+clamp 0 地板聚集",
        }
        return anchors[field]


# ========================================================================== #
# 2. TestPopulationStructureInvariance —— 人口结构运转期不变性
# ========================================================================== #
class TestPopulationStructureInvariance(unittest.TestCase):
    """人口结构运转期不变性：运转只演化运行时状态（位置/EnvMind），不
    改变人口结构（职业/属性/标签账本为创生期常量）。全天运转前后：
    花名册职业直方图一致、generate_village() 再生成逐字一致（确定性+
    无状态污染）、status() 角色分布与花名册职业计数一致、key/env 分层
    3+22 不变、齐普夫形状守卫不破。"""

    @classmethod
    def setUpClass(cls):
        # 运转前快照（创生期常量 + 引擎观测出口）
        cls.occupation_hist_before = Counter(
            seed.occupation for seed in VILLAGE_ROSTER)
        cls.village_fingerprints_before = {
            npc_id: profile_fingerprint(profile)
            for npc_id, profile in generate_village().items()
        }
        engine_pre = NPCEngine(llm=MockLLMProvider())
        cls.roles_before = {entry["id"]: entry["role"]
                            for entry in engine_pre.status()["npcs"]}
        cls.layering_before = (len(KEY_IDS), len(ENV_IDS))
        # 全天运转（对账对象）
        cls.engine = run_full_day(1)

    def test_roster_sector_histogram_unchanged_by_running(self):
        """花名册职业/部门直方图运转前后一致（运转不修改创生期常量）：
        模块级 VILLAGE_ROSTER 的职业直方图与部门表 VILLAGE_SECTORS
        逐键对账——运转后的花名册仍是最初的世界设定。"""
        hist_after = Counter(seed.occupation for seed in VILLAGE_ROSTER)
        self.assertEqual(hist_after, self.occupation_hist_before,
                         msg="VILLAGE_ROSTER 职业直方图应运转前后一致"
                             "（运转不修改创生期常量——世界设定不得被"
                             "运行时状态回写）")
        sector_hist = Counter(seed.sector for seed in VILLAGE_ROSTER)
        self.assertEqual(dict(sector_hist), dict(VILLAGE_SECTORS),
                          msg="花名册部门计数应与 VILLAGE_SECTORS 常量表"
                              "逐键对账（farming 7/craft 5/...——齐普夫"
                              "阶层表的静态完整性）")

    def test_regenerate_village_identical_after_running(self):
        """运转后再调 generate_village()：每人职业/flaw_label/secret_label
        及其 tier 与运转前逐字一致——确定性种子（crc32）与无状态污染的
        双重证明（运转不回写 rng 流、不污染创生层）。"""
        village_after = generate_village()
        fingerprints_after = {npc_id: profile_fingerprint(profile)
                              for npc_id, profile in village_after.items()}
        self.assertEqual(fingerprints_after, self.village_fingerprints_before,
                         msg="运转后 generate_village() 再生成的全村人口学"
                             "指纹应与运转前逐字一致（职业/缺陷/把柄标签及"
                             "tier 全等——确定性创生层不受运转影响，玩家"
                             "在任何一天进村看到的都是同一个村庄）")

    def test_status_role_distribution_matches_roster(self):
        """engine.status() 全部 NPC 的 role 分布与花名册职业计数一致：
        25 人全员在场（运转不增删人口）、id→花名册联表的职业直方图与
        花名册直算逐键相等、每人 role 字符串运转前后不变（运转只改
        location/EnvMind 等运行时状态，不改身份）。"""
        entries = self.engine.status()["npcs"]
        ids_after = {entry["id"] for entry in entries}
        roster_ids = {seed.npc_id for seed in VILLAGE_ROSTER}
        self.assertEqual(ids_after, roster_ids,
                         msg="status() 应恰含花名册 25 人全员（运转不增删"
                             "人口——玩家见到的村民名单是创生期定数）")
        roles_after = {entry["id"]: entry["role"] for entry in entries}
        self.assertEqual(roles_after, self.roles_before,
                         msg="每人 role 字符串应运转前后逐字一致（身份为"
                             "创生期常量；运转只演化位置/EnvMind 运行时"
                             "状态）")
        roster_index = {seed.npc_id: seed for seed in VILLAGE_ROSTER}
        occupation_via_status = Counter(
            roster_index[entry["id"]].occupation for entry in entries)
        self.assertEqual(occupation_via_status, self.occupation_hist_before,
                         msg="status() 全员经 id 联表花名册的职业直方图应与"
                             "花名册直算逐键一致（role 分布与职业计数对账——"
                             "观测出口不丢失不错认人口）")

    def test_key_env_layering_invariant(self):
        """key/env 分层 3+22 运转前后不变：3 核心（chen/lily/tavern_sun，
        8D 全量 InnerState）+ 22 背景（4D 压缩 EnvMind）——运转不改变
        分层结构（Tier 封印架构的人口学前提）。"""
        key_ids_after = tuple(seed.npc_id for seed in VILLAGE_ROSTER
                              if seed.kind == "key")
        env_ids_after = tuple(seed.npc_id for seed in VILLAGE_ROSTER
                              if seed.kind == "env")
        self.assertEqual((len(key_ids_after), len(env_ids_after)),
                         self.layering_before,
                         msg="key/env 分层应保持 3+22（运转前后不变——"
                             "核心与背景的架构分层是 Tier 封印与 token"
                             "经济性的人口学前提）")
        self.assertEqual(key_ids_after, KEY_IDS,
                         msg="key 层应仍为 chen/lily/tavern_sun（核心 NPC"
                             "名单运转不改变）")
        self.assertEqual(set(self.engine.background_npcs), set(env_ids_after),
                         msg="引擎背景层应仍恰为 22 个 env NPC（与花名册"
                             "env 层逐一对账）")

    def test_zipf_shape_guard(self):
        """齐普夫形状守卫：农业核心部门（农夫+农妇+雇农=5）为最大部门、
        单例长尾部门占多数——运转后的世界仍是那个一头大、长尾多的
        人口学村庄（齐普夫阶层不被运转洗平，防「平庸泥潭」死穴）。"""
        farm_core = sum(1 for seed in VILLAGE_ROSTER
                        if seed.occupation in ("农夫", "农妇", "雇农"))
        self.assertEqual(farm_core, 5,
                         msg="农业核心部门（农夫+农妇+雇农）应为 5 人"
                             "（人口学设定：农业为最大生计部门）")
        other_max = max(
            (count for occupation, count in self.occupation_hist_before.items()
             if occupation not in ("农夫", "农妇")),
            default=0,
        )
        self.assertGreater(farm_core, other_max,
                            msg=f"农业核心部门 {farm_core} 人应大于其余任一"
                                f"单一职业部门（最大 {other_max} 人）——"
                                f"齐普夫头部形状守卫")
        singleton_departments = [
            occupation for occupation, count in self.occupation_hist_before.items()
            if count == 1]
        distinct = list(self.occupation_hist_before)
        self.assertGreater(
            len(singleton_departments) * 2, len(distinct),
            msg=f"单例长尾部门 {len(singleton_departments)}/{len(distinct)} "
                f"应占多数（齐普夫长尾形状守卫——多数村民各守一门独特"
                f"生计，村庄不是同质蜂群）")
        top_sector = max(VILLAGE_SECTORS, key=lambda k: VILLAGE_SECTORS[k])
        self.assertEqual(top_sector, "farming",
                         msg="最大部门应为 farming（齐普夫头部：农业部门"
                             "规模居首，与农业核心三人组守卫互为印证）")


if __name__ == "__main__":
    unittest.main()

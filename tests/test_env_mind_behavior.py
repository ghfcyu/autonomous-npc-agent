"""T4-b 第二小步 · EnvMind 4D 行为消费出口验收测试（先红后绿）。

被测契约（PM 定稿，与实现子代理 A 的任务书逐字一致；本文件按契约先行
编写——A 合流前相关用例为跳过/红是「先红后绿」预期态，合流后应全绿）：

- ``village_genesis.env_mind_for(npc_id)``：按 npc_id 查 VILLAGE_ROSTER，
  复用 generate_villager 单一采样内核（crc32 确定性种子）；env NPC（如
  ``herder_ma``）返回 4D EnvMind，key NPC（chen/lily）与未知 id 返回
  None，两次调用逐字段相等；
- ``BackgroundNPC(config, world, env_mind=None)``：可选注入 4D 稳态——
  ① ``ENV_MIND_VARIANTS`` 状态带变体后缀拼进 npc_action 反应文案末尾
  （纯中文，优先级=元组序：p_fatigue/p_hunger/S_stress/e_P 各阈值
  0.65，每条反应至多一个后缀）；② ``apply_schedule(hour)`` 按作息状态
  每小时演化一步（``MIND_EVOLUTION_RATES``：WORKING(+0.08,+0.06,+0.00,
  +0.01) / IDLE(-0.05,-0.12,+0.02,-0.02) / SLEEPING(-0.15,-0.02,-0.03,
  -0.05)，元组序=p_fatigue,p_hunger,e_P,S_stress，clamp [0,1]，同 hour
  多次调用只演化一步）；③ ``mind`` property（frozen，演化生成新实例）；
  ④ ``to_dict()`` 新增 ``env_mind`` 键（None 或 4D 两位小数 dict）；
- 引擎默认构造 23 背景全员经 ``env_mind_for(config["id"])`` 注入；显式
  传 background_configs 且 id 不在 ROSTER 时 mind=None（行为与旧版一致）；
- 封印与零 LLM 维持（Tier 0/1）：EnvMind 数值与英文字段名不进任何
  LLM prompt，背景 NPC 全天零 LLM 调用。

演化语义口径（本文件断言依据）：作息状态「延续」——无表项小时沿用
上一表项状态（与既有作息移动语义一致，见 test_village_integration.
TestNightShift「午夜，无表项，状态延续」）。据此对 schedule
{"4": "WORKING", "12": "IDLE", "19": "SLEEPING"} 的 herder_ma：
apply_schedule(4..11) 为 8 个 WORKING 小时、每步各演化一次（任务书
口径：初值上界 0.30 + 8×0.08 = 0.94 ≥ 0.65 必然抵达变体带——防纸面
哲学：变体带必须在真实运转中可达）；防重用例恰用无表项的 hour 5，
验证「同 hour 多次调用只演化一步」。

编写纪律（沿 test_village_integration.py）：
- 不直接 import 可能未合入的新符号：模块级 getattr 软引用 +
  skipUnless 门控（A 合流前后本文件均可装载、红绿归因清晰）；
- 断言全部以玩家可感知为口径：npc_action 反应文案可见分叉 / 慢脑
  prompt 文本 / 零 LLM 调用计数，不做与玩家输出无关的纯数字锁死
  （反内卷纪律）；
- 引擎构造照既有测试惯例（MockLLMProvider / 显式 background_configs
  传参，见 test_background_npc.py 与 test_village_genesis.py）。
"""

import inspect
import unittest

from engine import background_npc as background_npc_module
from engine import village_genesis as village_genesis_module
from engine.background_npc import BackgroundNPC
from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.village_genesis import (EnvMind, generate_village,
                                    load_newcomer_configs)
from engine.world import Entity, World

# ---------------------------------------------------------------------- #
# 契约软引用（getattr 纪律：A 合流前置期本文件可装载；合流后门控自动放行）
# ---------------------------------------------------------------------- #
env_mind_for = getattr(village_genesis_module, "env_mind_for", None)
ENV_MIND_VARIANTS = getattr(background_npc_module, "ENV_MIND_VARIANTS", None)
MIND_EVOLUTION_RATES = getattr(background_npc_module, "MIND_EVOLUTION_RATES", None)


def _ctor_accepts_env_mind() -> bool:
    """BackgroundNPC.__init__ 是否已含契约构造参数 env_mind。"""
    try:
        params = inspect.signature(BackgroundNPC.__init__).parameters
    except (TypeError, ValueError):  # 签名不可读（理论不可达），按未合入处理
        return False
    return "env_mind" in params


GENESIS_READY = env_mind_for is not None
NPC_READY = all((
    ENV_MIND_VARIANTS is not None,
    MIND_EVOLUTION_RATES is not None,
    _ctor_accepts_env_mind(),
    hasattr(BackgroundNPC, "mind"),
))

_SKIP_GENESIS = "T4-b 契约件未合入：village_genesis.env_mind_for 缺失（PM 合入后统一合跑）"
_SKIP_NPC = ("T4-b 契约件未合入：BackgroundNPC 的 env_mind 构造参数 / "
             "ENV_MIND_VARIANTS / MIND_EVOLUTION_RATES / mind property "
             "缺其一（PM 合入后统一合跑）")


# ---------------------------------------------------------------------- #
# 测试脚手架
# ---------------------------------------------------------------------- #
# EnvMind 4D 字段序（契约：MIND_EVOLUTION_RATES 元组序与此一致）
ENV_MIND_FIELDS = ("p_fatigue", "p_hunger", "e_P", "S_stress")

# 契约步长表（WORKING/IDLE/SLEEPING × 4D，元组序=p_fatigue,p_hunger,e_P,S_stress）
WORKING_RATES = (0.08, 0.06, 0.00, 0.01)
IDLE_RATES = (-0.05, -0.12, 0.02, -0.02)
SLEEPING_RATES = (-0.15, -0.02, -0.03, -0.05)

# 四个状态带变体后缀的可感知锚词（契约文案逐字：哈欠/咕咕/眉头/笑意）
SUFFIX_MARKS = ("哈欠", "咕咕", "眉头", "笑意")

# 同地事件源叙事（分叉舞台共用：单元素事件池，每次触发必为同一事件）
EVENT_SUMMARY = "吆喝声由远及近"


def mind_values(mind):
    """EnvMind 4D 值元组（字段序=ENV_MIND_FIELDS；逐字段比较用）。"""
    return tuple(getattr(mind, field) for field in ENV_MIND_FIELDS)


def bg_config(npc_id, name, role, location_id, summary, reactions):
    """背景 NPC 配置 dict（G4 契约结构，沿 test_background_npc.py 同款）。"""
    return {"id": npc_id, "name": name, "role": role,
            "location_id": location_id, "summary": summary,
            "reactions": reactions}


def newcomer_config(npc_id):
    """从 configs/village_roster 动态取新住民配置（不硬编码清单）。"""
    for cfg in load_newcomer_configs():
        if cfg["id"] == npc_id:
            return cfg
    raise AssertionError(f"缺少新住民配置: {npc_id}")


def herder_ma_config():
    """herder_ma 配置：schedule {"4": "WORKING", "12": "IDLE", "19": "SLEEPING"}。"""
    return newcomer_config("herder_ma")


def stage_env_event_npc(config, mind, location):
    """同地 env_event 反应舞台：单元素事件池 + 同地事件源实体 chen。

    每次调用全新构造（反应计数器归零、模板同相位）——模板轮转噪声
    归零，分叉用例只随 mind 分叉。
    """
    world = World(env_events=[{"actor": "chen", "summary": EVENT_SUMMARY}])
    world.add_entity(Entity(id="chen", kind="npc", name="铁匠陈",
                            location_id=location), announce=False)
    npc = BackgroundNPC(config, world, env_mind=mind)
    return world, npc


def only_reaction_summaries(world, npc_id):
    """总线中该 NPC 产出的全部 npc_action summary（玩家可感知文本出口）。"""
    return [e.payload["summary"] for e in world.bus.history
            if e.kind == "npc_action" and e.actor == npc_id]


def fork_summary(mind):
    """单模板探针 NPC 发布一次同地 env_event，返回其 npc_action summary。"""
    config = bg_config("fork_probe", "探针村民", "idler", "forge",
                       "分叉探针村民",
                       {"env_event": ["{npc_name} 望着远处：{summary}"]})
    world, npc = stage_env_event_npc(config, mind, "forge")
    world.accumulate_event_slot(1.0)  # 步长=阈值，直接触发 env_event
    summaries = only_reaction_summaries(world, npc.id)
    if len(summaries) != 1:
        raise AssertionError(
            f"探针 NPC 应恰反应 1 次，实际 {len(summaries)} 次: {summaries}")
    return summaries[0]


def evolution_npc(mind):
    """herder_ma + 注入已知初值 EnvMind（演化用例的受控输入）。"""
    return BackgroundNPC(herder_ma_config(), World(), env_mind=mind)


# ========================================================================== #
# 1. TestEnvMindFor —— env_mind_for 确定性与单一采样内核
# ========================================================================== #
@unittest.skipUnless(GENESIS_READY, _SKIP_GENESIS)
class TestEnvMindFor(unittest.TestCase):
    """env_mind_for(npc_id)：env NPC 确定性 4D 初值、key/未知 id 返回 None。"""

    def test_herder_ma_deterministic_across_calls(self):
        # 确定性：两次调用 4D 逐字段相等（crc32(npc_id) 种子，跨调用稳定）
        mind_a = env_mind_for("herder_ma")
        mind_b = env_mind_for("herder_ma")
        self.assertIsInstance(mind_a, EnvMind,
                              msg="herder_ma 为 env NPC，应返回 EnvMind 实例")
        self.assertEqual(mind_values(mind_a), mind_values(mind_b),
                         msg="env_mind_for 两次调用应逐字段相等（确定性种子）")

    def test_single_sampling_kernel_same_as_generate_village(self):
        # 单一采样内核：与 generate_village()["herder_ma"].mind 逐字段一致
        # （严禁两套采样逻辑——漂移=共线性死穴）
        via_for = env_mind_for("herder_ma")
        via_village = generate_village()["herder_ma"].mind
        self.assertIsNotNone(via_village,
                             msg="generate_village 口径下 herder_ma 应有 EnvMind")
        self.assertEqual(mind_values(via_for), mind_values(via_village),
                         msg="env_mind_for 必须复用 generate_villager 单一采样内核")

    def test_key_npcs_return_none(self):
        # key NPC：8D 全量由运行时 InnerState 承载，无 4D 压缩稳态
        self.assertIsNone(env_mind_for("chen"),
                           msg="key NPC chen 不应有 EnvMind（8D 由引擎常驻账本接管）")
        self.assertIsNone(env_mind_for("lily"),
                           msg="key NPC lily 不应有 EnvMind（8D 由引擎常驻账本接管）")

    def test_unknown_id_returns_none(self):
        self.assertIsNone(env_mind_for("nobody_x"),
                          msg="未知 npc_id 应返回 None（花名册外无档案）")


# ========================================================================== #
# 2. TestMindInjection —— 引擎装配：默认 23 人全员注入 / 花名册外 None
# ========================================================================== #
@unittest.skipUnless(NPC_READY, _SKIP_NPC)
class TestMindInjection(unittest.TestCase):
    """引擎注入契约：默认构造 23 背景全员有 mind；显式传入 id 不在
    VILLAGE_ROSTER 时 mind=None（行为与旧版完全一致）；to_dict 观测出口。"""

    def test_default_engine_injects_all_23(self):
        engine = NPCEngine(llm=MockLLMProvider())
        self.assertEqual(len(engine.background_npcs), 23,
                         msg="默认构造（生产/演示路径）应 23 位背景村民（旧 8+新 15）")
        for bg_id, bg in engine.background_npcs.items():
            self.assertIsNotNone(
                bg.mind, msg=f"{bg_id} 默认构造应经 env_mind_for 注入 4D EnvMind")
            self.assertIsInstance(bg.mind, EnvMind, msg=bg_id)

    def test_explicit_outside_roster_mind_none(self):
        outsider = bg_config("outsider_q", "外乡来客", "wanderer", "plaza",
                             "路过借宿的外乡人",
                             {"env_event": ["{npc_name} 拱了拱手"]})
        engine = NPCEngine(llm=MockLLMProvider(),
                           background_configs=[outsider])
        self.assertEqual(set(engine.background_npcs), {"outsider_q"})
        self.assertIsNone(engine.background_npcs["outsider_q"].mind,
                          msg="显式传入 id 不在 VILLAGE_ROSTER → mind=None，"
                              "行为与旧版完全一致（无 mind 不演化、无变体）")

    def test_explicit_roster_id_still_minded(self):
        # 三路 bg_configs 统一经 env_mind_for 注入：显式传入花名册内 id
        # 同样注入，且初值与直调 env_mind_for 逐字段一致（注入源对账）
        engine = NPCEngine(llm=MockLLMProvider(),
                           background_configs=[herder_ma_config()])
        bg = engine.background_npcs["herder_ma"]
        self.assertIsNotNone(bg.mind,
                             msg="显式传入花名册内 id 应同样注入 EnvMind（三路统一）")
        self.assertEqual(mind_values(bg.mind), mind_values(env_mind_for("herder_ma")),
                         msg="注入初值应与 env_mind_for 直调逐字段一致（同源确定性）")

    def test_to_dict_env_mind_snapshot_key(self):
        # to_dict 观测出口：有 mind → 4D 两位小数 dict；无 mind → None
        engine = NPCEngine(llm=MockLLMProvider())
        snapshot = engine.background_npcs["herder_ma"].to_dict()["env_mind"]
        self.assertIsNotNone(snapshot, msg="to_dict 应含 env_mind 快照键")
        for field in ENV_MIND_FIELDS:
            self.assertIn(field, snapshot, msg=f"env_mind 快照缺 4D 字段 {field}")
            self.assertAlmostEqual(
                snapshot[field], getattr(engine.background_npcs["herder_ma"].mind, field),
                places=2,
                msg=f"{field} 快照应为两位小数观测值")
        outsider = bg_config("outsider_q", "外乡来客", "wanderer", "plaza",
                             "路过借宿的外乡人",
                             {"env_event": ["{npc_name} 拱了拱手"]})
        explicit = NPCEngine(llm=MockLLMProvider(),
                             background_configs=[outsider])
        self.assertIsNone(explicit.background_npcs["outsider_q"].to_dict()["env_mind"],
                          msg="无 mind 时 to_dict 的 env_mind 键应为 None")


# ========================================================================== #
# 3. TestVariantFork —— 验收①核心：状态带变体的玩家可感知文案分叉
# ========================================================================== #
@unittest.skipUnless(NPC_READY, _SKIP_NPC)
class TestVariantFork(unittest.TestCase):
    """注入不同 EnvMind 发布同地 env_event，npc_action 反应文案可见分叉：
    高疲劳→哈欠 / 高饥饿→咕咕 / 高压力→眉头 / 高愉悦→笑意；优先级=元组序
    （生理压过情绪，每条反应至多一个后缀）；平稳与无 mind 与旧版格式一致。"""

    def test_high_fatigue_yawns_in_reaction(self):
        summary = fork_summary(EnvMind(0.8, 0.2, 0.2, 0.2))
        # 后缀叠加在既有反应文本上（可见的是完整反应，不是孤立后缀）
        self.assertIn("探针村民", summary,
                       msg="变体后缀应叠加在既有反应文本上（模板先格式化）")
        self.assertIn(EVENT_SUMMARY, summary,
                      msg="事件 summary 应仍在反应文本中（后缀不替换内容）")
        self.assertIn("哈欠", summary,
                      msg="高疲劳（p_fatigue=0.8 ≥ 0.65）反应文案应可见『，一边打了个哈欠』")

    def test_calm_mind_keeps_plain_reaction(self):
        summary = fork_summary(EnvMind(0.2, 0.2, 0.2, 0.2))
        for mark in SUFFIX_MARKS:
            self.assertNotIn(mark, summary,
                              msg=f"平稳带（全 0.2 < 0.65）反应不得出现变体锚词『{mark}』")

    def test_high_hunger_stomach_rumbles(self):
        summary = fork_summary(EnvMind(0.2, 0.8, 0.2, 0.2))
        self.assertIn("咕咕", summary,
                      msg="高饥饿（p_hunger=0.8 ≥ 0.65）反应文案应可见『，肚子还咕咕叫着』")

    def test_high_stress_brow_furrowed(self):
        summary = fork_summary(EnvMind(0.2, 0.2, 0.2, 0.8))
        self.assertIn("眉头", summary,
                      msg="高压力（S_stress=0.8 ≥ 0.65）反应文案应可见『，眉头微微拧着』")

    def test_high_joy_smiling(self):
        summary = fork_summary(EnvMind(0.2, 0.2, 0.8, 0.2))
        self.assertIn("笑意", summary,
                      msg="高愉悦（e_P=0.8 ≥ 0.65）反应文案应可见『，嘴角带着笑意』")

    def test_fatigue_priority_over_joy(self):
        # 优先级=ENV_MIND_VARIANTS 元组序：疲劳在前压过愉悦，每条至多一个后缀
        summary = fork_summary(EnvMind(0.8, 0.2, 0.8, 0.2))
        self.assertIn("哈欠", summary,
                      msg="疲劳+愉悦双高时，元组序在前的 p_fatigue 应命中哈欠")
        self.assertNotIn("笑意", summary,
                         msg="生理压过情绪：命中疲劳后不得再拼接笑意（每条反应至多一个后缀）")

    def test_no_mind_matches_legacy_format(self):
        # env_mind=None：反应文本与旧版逐字一致（无后缀），向后兼容口径
        summary = fork_summary(None)
        self.assertEqual(summary, f"探针村民 望着远处：{EVENT_SUMMARY}",
                         msg="无 mind 时反应应与旧版格式逐字一致（不拼任何后缀）")
        for mark in SUFFIX_MARKS:
            self.assertNotIn(mark, summary, msg=f"旧版反应不得出现变体锚词『{mark}』")


# ========================================================================== #
# 4. TestMindEvolution —— 每小时步进 / clamp / 同 hour 防重 / frozen 替换
# ========================================================================== #
@unittest.skipUnless(NPC_READY, _SKIP_NPC)
class TestMindEvolution(unittest.TestCase):
    """apply_schedule 演化契约：状态延续语义下每小时按 MIND_EVOLUTION_RATES
    步进 4D（WORKING/IDLE/SLEEPING 逐维差值），clamp [0,1] 不越界，同 hour
    多次 tick 只演化一步，演化生成新实例不改旧引用。"""

    def test_working_hours_evolve_one_step_each(self):
        # 4..11 共 8 个 WORKING 小时（4 表项置 WORKING，5-11 状态延续），
        # 每步 p_fatigue +0.08 / p_hunger +0.06 / e_P 不变 / S_stress +0.01
        npc = evolution_npc(EnvMind(0.30, 0.30, 0.50, 0.20))
        prev = mind_values(npc.mind)
        for hour in (4, 5, 6, 7, 8, 9, 10, 11):
            npc.apply_schedule(hour)
            cur = mind_values(npc.mind)
            for i, (field, delta) in enumerate(zip(ENV_MIND_FIELDS, WORKING_RATES)):
                self.assertAlmostEqual(
                    cur[i], prev[i] + delta, places=6,
                    msg=f"hour={hour} {field} 应按 WORKING 步长 {delta:+.2f} 演化"
                        f"（状态延续：无表项小时沿用 WORKING）")
            prev = cur
        # 8 小时累计口径：初值 + 8×步长（0.30+0.64=0.94，未触 clamp）
        self.assertAlmostEqual(npc.mind.p_fatigue, 0.30 + 8 * 0.08, places=6,
                               msg="8 个 WORKING 小时累计 p_fatigue 应=0.30+8×0.08")
        self.assertAlmostEqual(npc.mind.p_hunger, 0.30 + 8 * 0.06, places=6,
                               msg="8 个 WORKING 小时累计 p_hunger 应=0.30+8×0.06")
        self.assertAlmostEqual(npc.mind.e_P, 0.50, places=6,
                               msg="WORKING e_P 步长 +0.00：8 小时后应不变")
        self.assertAlmostEqual(npc.mind.S_stress, 0.20 + 8 * 0.01, places=6,
                               msg="8 个 WORKING 小时累计 S_stress 应=0.20+8×0.01")

    def test_idle_hour_evolution_deltas(self):
        npc = evolution_npc(EnvMind(0.30, 0.30, 0.50, 0.20))
        before = mind_values(npc.mind)
        npc.apply_schedule(12)  # herder_ma schedule "12": "IDLE"
        after = mind_values(npc.mind)
        for i, (field, delta) in enumerate(zip(ENV_MIND_FIELDS, IDLE_RATES)):
            self.assertAlmostEqual(after[i], before[i] + delta, places=6,
                                   msg=f"IDLE {field} 应按步长 {delta:+.2f} 演化"
                                       f"（歇脚用餐恢复/闲适愉悦）")

    def test_sleeping_hour_evolution_deltas(self):
        npc = evolution_npc(EnvMind(0.30, 0.30, 0.50, 0.20))
        before = mind_values(npc.mind)
        npc.apply_schedule(19)  # herder_ma schedule "19": "SLEEPING"
        after = mind_values(npc.mind)
        for i, (field, delta) in enumerate(zip(ENV_MIND_FIELDS, SLEEPING_RATES)):
            self.assertAlmostEqual(after[i], before[i] + delta, places=6,
                                   msg=f"SLEEPING {field} 应按步长 {delta:+.2f} 演化"
                                       f"（睡眠大恢复/情绪归平/压力释放）")

    def test_working_clamps_at_upper_bound(self):
        # 0.95 + 2×0.08 = 1.11 → clamp 1.0（WORKING 两小时不越上界）
        npc = evolution_npc(EnvMind(0.95, 0.05, 0.05, 0.05))
        npc.apply_schedule(4)
        npc.apply_schedule(5)
        self.assertAlmostEqual(npc.mind.p_fatigue, 1.0, places=6,
                               msg="WORKING 两小时后 p_fatigue 应 clamp 到 1.0（不越界）")
        self.assertLessEqual(npc.mind.p_fatigue, 1.0,
                             msg="p_fatigue 不得越上界 1.0")
        self.assertGreaterEqual(npc.mind.p_fatigue, 0.0,
                                msg="p_fatigue 不得越下界 0.0")

    def test_sleeping_clamps_at_lower_bound(self):
        # SLEEPING 多步（19 表项 + 20-22 状态延续 = 4 小时）：
        # p_hunger 0.05-4×0.02=-0.03 → clamp 0.0，不越负
        npc = evolution_npc(EnvMind(0.05, 0.05, 0.50, 0.20))
        for hour in (19, 20, 21, 22):
            npc.apply_schedule(hour)
        self.assertAlmostEqual(npc.mind.p_hunger, 0.0, places=6,
                               msg="SLEEPING 多步后 p_hunger 应 clamp 到 0.0 附近不越负")
        self.assertGreaterEqual(npc.mind.p_hunger, 0.0,
                                msg="p_hunger 不得越下界 0.0（深度睡眠大恢复不越负）")
        self.assertGreaterEqual(npc.mind.p_fatigue, 0.0,
                                msg="p_fatigue 0.05-4×0.15 应 clamp 不越负")

    def test_same_hour_repeated_ticks_evolve_single_step(self):
        # 防重（10 分钟粒度 tick 一天 144 次的正确性前提）：hour 4 一步 +
        # hour 5 连续 3 次调用只演化一步（无表项小时状态延续，判重靠 hour）
        npc = evolution_npc(EnvMind(0.30, 0.30, 0.50, 0.20))
        npc.apply_schedule(4)
        after_hour4 = npc.mind.p_fatigue
        self.assertAlmostEqual(after_hour4, 0.30 + 0.08, places=6,
                               msg="hour 4 应恰演化一步（WORKING +0.08）")
        npc.apply_schedule(5)
        after_first_tick = npc.mind.p_fatigue
        self.assertAlmostEqual(after_first_tick, 0.30 + 2 * 0.08, places=6,
                               msg="hour 5 首次调用应演化一步（hour 变化放行）")
        npc.apply_schedule(5)
        npc.apply_schedule(5)
        self.assertAlmostEqual(npc.mind.p_fatigue, after_first_tick, places=9,
                               msg="同 hour 连续多次调用只演化一步（10 分钟粒度 "
                                   "一天 144 次 tick 的正确性前提）")

    def test_evolution_replaces_mind_without_mutating_old(self):
        # frozen 替换：演化后取值正确，且旧 EnvMind 引用逐字段不变
        npc = evolution_npc(EnvMind(0.30, 0.30, 0.50, 0.20))
        old_mind = npc.mind
        old_values = mind_values(old_mind)
        npc.apply_schedule(4)
        self.assertIsInstance(npc.mind, EnvMind,
                              msg="演化后 mind 仍应为 EnvMind 实例")
        self.assertAlmostEqual(npc.mind.p_fatigue, 0.30 + 0.08, places=6,
                               msg="演化后 mind.p_fatigue 应为初值+WORKING 步长"
                                   "（frozen 替换语义下取值正确）")
        self.assertEqual(mind_values(old_mind), old_values,
                          msg="演化生成新实例，旧 EnvMind 引用不得被修改（frozen）")


# ========================================================================== #
# 5. TestRuntimeVisibility —— 真实运转抵达变体带（防纸面哲学）
# ========================================================================== #
@unittest.skipUnless(GENESIS_READY and NPC_READY, _SKIP_NPC)
class TestRuntimeVisibility(unittest.TestCase):
    """真实作息演化抵达变体带：herder_ma 从 4 点放牧 8 小时后疲劳必过
    0.65，同地事件反应文案端到端可见『哈欠』；中午歇晌疲劳回落。"""

    def _pasture_herder(self):
        """牧场舞台：herder_ma 注入 env_mind_for 真实确定性初值。"""
        return stage_env_event_npc(herder_ma_config(),
                                   env_mind_for("herder_ma"), "pasture")

    def test_eight_working_hours_reach_yawn_variant_band(self):
        world, npc = self._pasture_herder()
        init = npc.mind.p_fatigue  # 平稳带初值 ∈ [0.10, 0.30]
        for hour in range(4, 12):  # 4..11 = 8 个 WORKING 小时
            npc.apply_schedule(hour)
        expected = init + 8 * 0.08  # ≤ 0.30+0.64=0.94，未触 clamp
        self.assertGreaterEqual(
            npc.mind.p_fatigue, 0.65,
            msg=f"8 个 WORKING 小时后 p_fatigue={npc.mind.p_fatigue:.4f} 应 ≥ 0.65 "
                f"（初值 {init:.4f}+8×0.08={expected:.4f}——变体带必须真实可达，"
                f"防纸面哲学）")
        self.assertAlmostEqual(npc.mind.p_fatigue, expected, places=6,
                               msg="11 点时值应=初值+8×0.08（初值动态计算口径）")
        # 端到端可感知（推荐口径）：此刻发布一次同地 env_event，
        # herder_ma 的 npc_action 反应文案带『哈欠』后缀（疲劳优先级第一）
        world.accumulate_event_slot(1.0)
        summaries = only_reaction_summaries(world, npc.id)
        self.assertEqual(len(summaries), 1,
                         msg="同地 env_event 应恰触发 herder_ma 一次规则反应")
        self.assertIn("哈欠", summaries[0],
                      msg="高疲劳状态带下 herder_ma 的反应文案应端到端可见『哈欠』"
                          "（疲劳在 ENV_MIND_VARIANTS 元组序最前）")

    def test_noon_idle_recovers_fatigue(self):
        world, npc = self._pasture_herder()
        for hour in range(4, 12):
            npc.apply_schedule(hour)
        peak = npc.mind.p_fatigue  # WORKING 单调递增，11 点即峰值
        for hour in range(12, 17):  # 12..16 中午 IDLE 歇晌
            npc.apply_schedule(hour)
        drop = peak - npc.mind.p_fatigue
        self.assertGreaterEqual(
            drop, 0.15,
            msg=f"中午 IDLE 歇晌后 p_fatigue 应比峰值回落 ≥ 0.15（实际回落 "
                f"{drop:.4f}——歇过晌午缓过来，变体消失的叙事对偶）")


# ========================================================================== #
# 6. TestZeroLLMMaintained —— 验收②：EnvMind 行为出口不破零 LLM 封印
# ========================================================================== #
class TestZeroLLMMaintained(unittest.TestCase):
    """验收②：全天 24 次 tick(60)（含 EnvMind 每小时演化）+ 4 句慢脑硬保护
    对话 → 恰 4 次 LLM 调用（仅核心对话）；23 个背景 NPC 不持有任何 LLM
    调用路径（Tier 0/1 架构封印维持）。"""

    def test_full_day_and_dialogue_background_zero_llm(self):
        engine = NPCEngine(llm=MockLLMProvider())
        self.assertEqual(len(engine.background_npcs), 23,
                         msg="默认引擎规模前置：23 位背景村民")
        self.assertEqual(engine.llm.call_count, 0,
                         msg="构造后调用计数前置为 0")
        for _ in range(24):
            engine.tick(60)
        self.assertEqual(engine.llm.call_count, 0,
                         msg="24 次全村庄 tick（背景作息+EnvMind 演化纯规则计算）"
                             "应零 LLM 调用")
        # 4 句核心对话（快脑硬保护四句必走慢脑，见 fast_brain.py 头注）
        engine.player_says("铁匠，打把剑", "chen")
        engine.player_says("有好铁矿石吗", "chen")
        engine.player_says("有新货吗", "lily")
        engine.player_says("有什么消息", "lily")
        self.assertEqual(engine.llm.call_count, 4,
                         msg="全天 tick + 4 句核心对话应恰 4 次 LLM 调用"
                             "（仅核心对话，背景 NPC 全程零调用）")
        # Tier 0/1 架构封印：背景 NPC 不持有 llm/provider/prompt 任何属性
        for bg_id, bg in engine.background_npcs.items():
            self.assertFalse(hasattr(bg, "llm") or hasattr(bg, "provider")
                              or hasattr(bg, "prompt"),
                              msg=f"{bg_id} 不得持有任何 LLM 调用路径"
                                  f"（Tier 0/1 架构封印）")
        # token 归属仅核心：背景 NPC 不进统计
        self.assertTrue(set(engine.token_stats) <= {"chen", "lily"},
                        msg=f"token 归属应仅核心 NPC，实际 {sorted(engine.token_stats)}")


# ========================================================================== #
# 7. TestTierSeal —— 封印：EnvMind 不进 prompt（<35token 红线）
# ========================================================================== #
class PromptCapturingLLM(MockLLMProvider):
    """捕获每次 chat 完整 messages 的 Mock（封印断言专用；沿
    test_village_integration.py 的 RecordingLLM 同款装配模式，本文件内
    自建以避免跨测试文件 import）。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.captured = []

    def chat(self, messages, temperature=0.7):
        self.captured.append([dict(m) for m in messages])
        return super().chat(messages, temperature)


class TestTierSeal(unittest.TestCase):
    """封印=EnvMind 不进 prompt：默认引擎 4 句慢脑对话，捕获的全部
    prompt 文本（system+user）不含 p_fatigue/p_hunger/S_stress/EnvMind
    字样——进 prompt 的只有离散中文标签（<35token 红线）。"""

    FORBIDDEN = ("p_fatigue", "p_hunger", "S_stress", "EnvMind")

    def test_slow_brain_prompts_free_of_envmind_tokens(self):
        engine = NPCEngine(llm=PromptCapturingLLM())
        engine.player_says("铁匠，打把剑", "chen")
        engine.player_says("有好铁矿石吗", "chen")
        engine.player_says("有新货吗", "lily")
        engine.player_says("有什么消息", "lily")
        self.assertEqual(len(engine.llm.captured), 4,
                         msg="4 句慢脑硬保护对话应产生 4 次可捕获的慢脑 prompt")
        for idx, messages in enumerate(engine.llm.captured, 1):
            full_text = "\n".join(str(m.get("content", "")) for m in messages)
            for token in self.FORBIDDEN:
                self.assertNotIn(
                    token, full_text,
                    msg=f"第 {idx} 次慢脑 prompt 泄露封印字段 {token}：EnvMind "
                        f"数值与英文字段名不得进 prompt（进 prompt 的只有"
                        f"离散中文标签）")


if __name__ == "__main__":
    unittest.main()

"""T2 标签库第二批 · engine/tag_mount.py + 接线单元测试（先红后绿之"红"）。

覆盖（任务书验收口径）：
- 5.2 必选挂载：mount 后缺陷 ≥1 且把柄 ≥1，标签中文与池一致，进 INNATE 桶；
- 分档纪律：缺陷 tier 全 ∈ {uncommon,rare,extreme}（零 common）、
  把柄 tier 全 ∈ {rare,extreme}（零 common/uncommon）——重归一化后
  金字塔频度序保持（uncommon > rare > extreme、rare > extreme）；
- 6.1 四时态：先天不可移除、acquire 入 ACQUIRED 桶、瞬态 TTL 耗尽
  自动注销、relational 带 scope 入 RELATIONAL 桶；
- 6.2 互斥锁：acquire 触发自动消除对立标签（含 INNATE），双向成立，
  mount 候选预检排除互斥冲突（圣人道德不与暗夜杀人并存）；
- 玩家可感知闭环（审查对抗升级执法条款）：挂载后决策上下文必含
  缺陷中文名；绝密把柄永不泄漏进决策上下文；
- 向后兼容：无 ledger 的 NPC 决策上下文与接线前字符串全等；
- 确定性：同 npc_id 两次 mount_tags 结果全等（跨引擎实例）；
- 正交纪律：tag_mount.py 不含金字塔权重数字字面量，必须 import 引用；
- 零第三方依赖：sys.modules 黑名单 + 源码/AST 检查；
- Mock 模式对话回归：挂载后 player_says 的 filler/reply/action 不炸。

统计断言不写防御性数字锁：频度只断言白名单集合与金字塔序
（罕见档 < 进阶档），不锁具体比例带。
"""

import ast
import json
import random
import sys
import unittest
from dataclasses import replace

import engine.tag_mount as tag_mount_module
from engine.decision import OUTPUT_CONTRACT, DecisionEngine
from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.npc import Persona
from engine.tag_genesis import generate_innate_attributes
from engine.tag_mount import (
    FLAW_POOL,
    MUTEX_RULES,
    SECRET_POOL,
    MountedTag,
    TagLedger,
    TagPhase,
)


# ---------------------------------------------------------------------- #
# 测试脚手架
# ---------------------------------------------------------------------- #
class RecordingLLM(MockLLMProvider):
    """记录完整 messages 的 Mock，用于断言决策上下文内容。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.captured = []

    def chat(self, messages, temperature=0.7):
        self.captured.append([dict(m) for m in messages])
        return super().chat(messages, temperature)


def make_persona(npc_id="chen"):
    return Persona(
        id=npc_id, name="铁匠陈" if npc_id == "chen" else "商人莉莉",
        role="blacksmith" if npc_id == "chen" else "merchant",
        location_id="forge" if npc_id == "chen" else "market",
        personality="沉稳", speech_style="简短", backstory="三代铁匠",
        greeting_bank=["炉子热着。"], fallback_bank=["嗯，你说。"],
        sleep_mumble="（鼾声）", schedule={"20": "SLEEPING"},
        topic_responses={"剑|刀": ["打剑？拿好铁来。"]},
    )


def make_engine(llm=None):
    personas = [make_persona("chen"), make_persona("lily")]
    return NPCEngine(llm=llm or MockLLMProvider(), npc_configs=personas)


def ledger_signature(ledger):
    """账本全量签章（确定性/全等断言口径）。"""
    return [(t.tag_id, t.label, t.phase.name, t.tier, t.secret, t.scope)
            for t in ledger.all_tags()]


# ========================================================================== #
# 1. 挂载强制（规范 5.2）
# ========================================================================== #
class TestMountForced(unittest.TestCase):
    def test_mount_returns_at_least_one_flaw_and_one_secret(self):
        for seed in range(50):
            ledger = TagLedger()
            mounted = ledger.mount_flaw_and_secret(random.Random(seed))
            flaws = [t for t in mounted if not t.secret]
            secrets = [t for t in mounted if t.secret]
            self.assertGreaterEqual(len(flaws), 1, msg=f"seed={seed} 缺陷缺失")
            self.assertGreaterEqual(len(secrets), 1, msg=f"seed={seed} 把柄缺失")

    def test_mounted_labels_match_pool_vocabulary(self):
        valid_flaws = {(e[0], e[1]) for e in FLAW_POOL}
        valid_secrets = {(e[0], e[1]) for e in SECRET_POOL}
        for seed in range(50):
            ledger = TagLedger()
            mounted = ledger.mount_flaw_and_secret(random.Random(seed))
            for tag in mounted:
                pair = (tag.tag_id, tag.label)
                if tag.secret:
                    self.assertIn(pair, valid_secrets, msg=f"seed={seed} {pair}")
                else:
                    self.assertIn(pair, valid_flaws, msg=f"seed={seed} {pair}")

    def test_mounted_tags_go_to_innate_bucket(self):
        ledger = TagLedger()
        mounted = ledger.mount_flaw_and_secret(random.Random(5))
        self.assertTrue(mounted)
        innate_ids = {t.tag_id for t in ledger.all_tags()
                      if t.phase is TagPhase.INNATE}
        for tag in mounted:
            self.assertIs(tag.phase, TagPhase.INNATE, msg=tag.tag_id)
            self.assertIn(tag.tag_id, innate_ids)

    def test_repeated_mount_never_duplicates_flaw_or_secret(self):
        ledger = TagLedger()
        ledger.mount_flaw_and_secret(random.Random(1))
        ledger.mount_flaw_and_secret(random.Random(1))
        flaw_ids = [t.tag_id for t in ledger.all_tags()
                    if t.tag_id.startswith("flaw_")]
        secret_ids = [t.tag_id for t in ledger.all_tags()
                      if t.tag_id.startswith("secret_")]
        self.assertEqual(len(flaw_ids), len(set(flaw_ids)),
                         msg=f"缺陷重复：{flaw_ids}")
        self.assertEqual(len(secret_ids), len(set(secret_ids)),
                         msg=f"把柄重复：{secret_ids}")
        self.assertEqual(len(flaw_ids), 2)   # 两次 mount 各强制挂 1 条缺陷
        self.assertEqual(len(secret_ids), 2)


# ========================================================================== #
# 2. 分档纪律（缺陷 uncommon 起步 / 把柄 rare 起步，import PYRAMID_TIERS 重归一化）
# ========================================================================== #
class TestTierPolicy(unittest.TestCase):
    def _collect(self, seeds):
        flaw_tiers, secret_tiers = [], []
        for seed in range(seeds):
            ledger = TagLedger()
            mounted = ledger.mount_flaw_and_secret(random.Random(seed))
            flaw_tiers.extend(t.tier for t in mounted if not t.secret)
            secret_tiers.extend(t.tier for t in mounted if t.secret)
        return flaw_tiers, secret_tiers

    def test_flaw_tiers_exclude_common(self):
        flaw_tiers, _ = self._collect(1000)
        self.assertTrue(flaw_tiers)
        self.assertTrue(set(flaw_tiers) <= {"uncommon", "rare", "extreme"},
                        msg=f"缺陷档越界：{set(flaw_tiers)}")

    def test_secret_tiers_exclude_common_and_uncommon(self):
        _, secret_tiers = self._collect(1000)
        self.assertTrue(secret_tiers)
        self.assertTrue(set(secret_tiers) <= {"rare", "extreme"},
                        msg=f"把柄档越界：{set(secret_tiers)}")

    def test_flaw_tier_frequency_keeps_pyramid_order(self):
        flaw_tiers, _ = self._collect(2000)
        counts = {tier: flaw_tiers.count(tier)
                  for tier in ("uncommon", "rare", "extreme")}
        self.assertGreater(counts["uncommon"], counts["rare"],
                           msg=f"重归一化后金字塔序被破坏：{counts}")
        self.assertGreater(counts["rare"], counts["extreme"], msg=f"{counts}")

    def test_secret_tier_frequency_rare_dominant(self):
        _, secret_tiers = self._collect(2000)
        counts = {tier: secret_tiers.count(tier) for tier in ("rare", "extreme")}
        self.assertGreater(counts["rare"], counts["extreme"], msg=f"{counts}")


# ========================================================================== #
# 3. 四时态（规范 6.1）
# ========================================================================== #
class TestFourPhases(unittest.TestCase):
    def test_unmount_rejects_innate_but_removes_acquired(self):
        ledger = TagLedger()
        ledger.add_innate(MountedTag(tag_id="flaw_limp", label="跛足",
                                     phase=TagPhase.INNATE))
        with self.assertRaises(ValueError):
            ledger.unmount("flaw_limp")  # 先天印记不可剥夺（抛错语义）
        ledger.acquire(MountedTag(tag_id="rep_hero", label="平民英雄",
                                  phase=TagPhase.ACQUIRED))
        self.assertTrue(ledger.unmount("rep_hero"))
        self.assertFalse(ledger.unmount("rep_hero"))  # 已不存在
        self.assertIn("flaw_limp", [t.tag_id for t in ledger.all_tags()])

    def test_acquire_puts_tag_in_acquired_bucket(self):
        ledger = TagLedger()
        removed = ledger.acquire(MountedTag(tag_id="rep_kinslayer",
                                            label="弑亲恶名",
                                            phase=TagPhase.ACQUIRED))
        self.assertEqual(removed, [])
        acquired = [t for t in ledger.all_tags() if t.phase is TagPhase.ACQUIRED]
        self.assertEqual([t.tag_id for t in acquired], ["rep_kinslayer"])
        self.assertIn("弑亲恶名", [t.label for t in ledger.visible_tags()])

    def test_transient_ttl_expiry_auto_registers_out(self):
        ledger = TagLedger()
        ledger.add_transient(MountedTag(tag_id="state_drunk", label="重度醉酒",
                                        phase=TagPhase.TRANSIENT), ttl_ticks=2)
        self.assertIn("state_drunk", [t.tag_id for t in ledger.visible_tags()])
        ledger.tick()
        self.assertIn("state_drunk", [t.tag_id for t in ledger.visible_tags()],
                      msg="TTL=2 第一轮 tick 后不应注销")
        expired = ledger.tick()
        self.assertEqual([t.tag_id for t in expired], ["state_drunk"])
        self.assertNotIn("state_drunk", [t.tag_id for t in ledger.visible_tags()])

    def test_relational_tag_stores_scope(self):
        ledger = TagLedger()
        ledger.add_relational(MountedTag(tag_id="bond_sworn_brother",
                                         label="金兰义兄弟",
                                         phase=TagPhase.RELATIONAL),
                              scope="player")
        tag = [t for t in ledger.all_tags()
               if t.tag_id == "bond_sworn_brother"][0]
        self.assertIs(tag.phase, TagPhase.RELATIONAL)
        self.assertEqual(tag.scope, "player")
        self.assertIn("金兰义兄弟", [t.label for t in ledger.visible_tags()])


# ========================================================================== #
# 4. 互斥锁（规范 6.2）
# ========================================================================== #
class TestMutex(unittest.TestCase):
    def test_stingy_removed_when_generous_acquired(self):
        ledger = TagLedger()
        ledger.add_innate(MountedTag(tag_id="flaw_stingy", label="极度吝啬",
                                    phase=TagPhase.INNATE))
        removed = ledger.acquire(MountedTag(tag_id="virtue_generous",
                                            label="慷慨散财",
                                            phase=TagPhase.ACQUIRED))
        self.assertIn("flaw_stingy", [t.tag_id for t in removed],
                      msg="acquire 互斥消除必须返回被消除的对立标签")
        self.assertNotIn("flaw_stingy", [t.tag_id for t in ledger.all_tags()])
        self.assertIn("virtue_generous", [t.tag_id for t in ledger.all_tags()])
        self.assertNotIn("极度吝啬", [t.label for t in ledger.visible_tags()])

    def test_generous_removed_when_stingy_acquired(self):
        ledger = TagLedger()
        ledger.acquire(MountedTag(tag_id="virtue_generous", label="慷慨散财",
                                  phase=TagPhase.ACQUIRED))
        removed = ledger.acquire(MountedTag(tag_id="flaw_stingy", label="极度吝啬",
                                           phase=TagPhase.ACQUIRED))
        self.assertIn("virtue_generous", [t.tag_id for t in removed])
        self.assertNotIn("virtue_generous", [t.tag_id for t in ledger.all_tags()])

    def test_beauty_mutex_pair(self):
        ledger = TagLedger()
        ledger.add_innate(MountedTag(tag_id="beauty_stunning", label="倾国倾城",
                                     phase=TagPhase.INNATE))
        removed = ledger.acquire(MountedTag(tag_id="beauty_horrifying",
                                            label="面目可怖",
                                            phase=TagPhase.ACQUIRED))
        self.assertIn("beauty_stunning", [t.tag_id for t in removed])

    def test_mount_excludes_secret_conflicting_with_ledger_saint(self):
        # 账本通道预检：先挂 morality_saint，mount 必不抽中暗夜杀人
        for seed in range(120):
            ledger = TagLedger()
            ledger.add_innate(MountedTag(tag_id="morality_saint", label="高义守节",
                                         phase=TagPhase.INNATE))
            mounted = ledger.mount_flaw_and_secret(random.Random(seed))
            for tag in mounted:
                self.assertNotEqual(tag.tag_id, "secret_murderer",
                                    msg=f"seed={seed} 圣人道德与暗夜杀人并存")

    def test_mount_excludes_secret_conflicting_with_innate_attributes(self):
        # innate 通道预检：不挂称号、仅传 innate，圣人道德同样排除
        base = generate_innate_attributes(random.Random(0))
        saint = replace(base, morality=10.0)
        for seed in range(120):
            ledger = TagLedger()
            mounted = ledger.mount_flaw_and_secret(random.Random(seed), innate=saint)
            for tag in mounted:
                self.assertNotEqual(tag.tag_id, "secret_murderer", msg=f"seed={seed}")

    def test_mutex_rules_shape_locked(self):
        self.assertEqual(set(MUTEX_RULES), {
            ("flaw_stingy", "virtue_generous"),
            ("morality_saint", "secret_murderer"),
            ("beauty_stunning", "beauty_horrifying"),
        })


# ========================================================================== #
# 5. 玩家可感知闭环（审查对抗升级执法条款）
# ========================================================================== #
class TestPlayerPerception(unittest.TestCase):
    def setUp(self):
        self.llm = RecordingLLM()
        self.engine = make_engine(self.llm)
        self.engine.mount_tags("chen", rng=random.Random(7))
        self.ledger = self.engine.npcs["chen"].tag_ledger

    def test_mounted_flaw_label_appears_in_decision_context(self):
        result = self.engine.player_says("帮我打一把剑", "chen")
        self.assertTrue(result["ok"])
        system = self.llm.captured[-1][0]["content"]
        self.assertIn("身份标签：", system)
        flaws = [t for t in self.ledger.visible_tags()
                 if t.tag_id.startswith("flaw_")]
        self.assertTrue(flaws, msg="mount 后账本应有显性缺陷")
        for tag in flaws:
            self.assertIn(tag.label, system,
                          msg=f"缺陷 {tag.label} 未进决策上下文（纸面哲学）")

    def test_identity_line_renders_visible_labels_without_floats(self):
        system = self.engine.npcs["chen"].decision._system_prompt(self.ledger)
        # 顿号连接、顺序与 visible_tags() 全等、只渲染中文名
        line = system.split("身份标签：", 1)[1].split("\n", 1)[0]
        self.assertEqual(line.split("、"),
                         [t.label for t in self.ledger.visible_tags()])
        self.assertNotRegex(line, r"\d+\.\d+", msg="身份标签行混入浮点权重")

    def test_secret_labels_never_leak_into_decision_context(self):
        result = self.engine.player_says("有好铁矿石吗", "chen")
        self.assertTrue(result["ok"])
        full_text = "".join(self.llm.captured[-1][0].values())
        self.assertTrue(self.ledger.secrets(), msg="挂载确实产生了把柄，排除假绿")
        for entry in SECRET_POOL:
            self.assertNotIn(entry[1], full_text,
                             msg=f"绝密把柄「{entry[1]}」泄漏进决策上下文")

    def test_mounted_npc_mock_dialogue_regression(self):
        self.engine.mount_tags("lily", rng=random.Random(4))
        for text, npc_id in [("帮我打一把剑", "chen"), ("有新货吗", "lily")]:
            result = self.engine.player_says(text, npc_id)
            self.assertTrue(result["ok"], msg=npc_id)
            self.assertTrue(result["reply"], msg=f"{npc_id} reply 为空")
            # filler 空串是合法语义（无脾气掩码向后兼容），此处只验不炸
            self.assertIsInstance(result["filler"], str, msg=f"{npc_id} filler 炸了")
            self.assertIn("action", result)


# ========================================================================== #
# 6. 向后兼容 + 确定性 + 挂载 API 边界
# ========================================================================== #
class TestBackwardCompatAndDeterminism(unittest.TestCase):
    def test_no_ledger_system_prompt_string_identical(self):
        # 接线前（T2 第二批之前）的 _system_prompt 输出，逐字符锁死：
        # 无 ledger 时决策上下文必须与不接线时完全一致
        persona = make_persona("chen")
        engine = DecisionEngine(persona, MockLLMProvider())
        self.assertEqual(engine._system_prompt(), engine._system_prompt(None))
        actual = engine._system_prompt()
        banks = {"greeting_bank": persona.greeting_bank,
                 "fallback_bank": persona.fallback_bank,
                 "topic_responses": persona.topic_responses}
        expected = (
            f"<!--persona:{persona.id}-->\n"
            f"你是游戏里的 NPC「{persona.name}」，职业：{persona.role}。\n"
            f"性格：{persona.personality}\n"
            f"说话风格：{persona.speech_style}\n"
            f"背景：{persona.backstory}\n"
            f"喜欢：{'、'.join(persona.likes)}；讨厌：{'、'.join(persona.dislikes)}\n"
            f"\n\n"
            f"{OUTPUT_CONTRACT}\n"
            f"<<<banks>>>{json.dumps(banks, ensure_ascii=False)}"
        )
        self.assertEqual(actual, expected)
        self.assertNotIn("身份标签", actual)

    def test_no_ledger_system_prompt_with_tags_identical(self):
        # persona.tags 非空（带浮点权重的既有性格标签行）也必须零变化
        persona = make_persona("chen")
        persona.tags = {"沉稳": 0.8}
        engine = DecisionEngine(persona, MockLLMProvider())
        actual = engine._system_prompt()
        self.assertIn("\n性格标签：沉稳(0.8)\n", actual)
        self.assertNotIn("身份标签", actual)

    def test_npcs_default_ledger_none(self):
        engine = make_engine()
        for npc in engine.npcs.values():
            self.assertIsNone(npc.tag_ledger)

    def test_mount_tags_deterministic_across_instances(self):
        sig1 = ledger_signature(make_engine().mount_tags("chen"))
        sig2 = ledger_signature(make_engine().mount_tags("chen"))
        self.assertEqual(sig1, sig2, msg="同 npc_id 两次 mount_tags 必须全等")
        self.assertTrue(sig1, msg="挂载账本不应为空")

    def test_mount_tags_unknown_npc_returns_none(self):
        self.assertIsNone(make_engine().mount_tags("nobody"))

    def test_background_npc_not_mountable(self):
        bg_config = {"id": "bg_farmer", "name": "农妇王氏",
                     "location_id": "plaza", "summary": "赶集卖菜"}
        engine = NPCEngine(llm=MockLLMProvider(),
                           npc_configs=[make_persona("chen")],
                           background_configs=[bg_config])
        # 背景 NPC 不在 npcs 登记：挂载 API 不可达（"非纯背景"语义）
        self.assertIsNone(engine.mount_tags("bg_farmer"))
        bg = engine.background_npcs["bg_farmer"]
        self.assertFalse(hasattr(bg, "tag_ledger"))


# ========================================================================== #
# 7. 正交纪律 + 零第三方依赖
# ========================================================================== #
class TestOrthogonalityAndDependencies(unittest.TestCase):
    def _source(self):
        with open(tag_mount_module.__file__, encoding="utf-8") as fh:
            return fh.read()

    def test_no_pyramid_weight_literals_in_tag_mount_source(self):
        source = self._source()
        for literal in ("0.70", "0.20", "0.08", "0.02"):
            self.assertNotIn(
                literal, source,
                msg=f"金字塔权重 {literal} 被复制进挂载层，违反正交纪律（必须 import 引用）")

    def test_tag_mount_imports_pyramid_tiers(self):
        source = self._source()
        self.assertIn("PYRAMID_TIERS", source,
                      msg="挂载层必须 import 引用 tag_genesis.PYRAMID_TIERS")
        self.assertIn("from .tag_genesis import", source)

    def test_no_third_party_dependency_loaded(self):
        # sys.modules 检查：tag_mount 及其依赖链未加载任何第三方包
        for name in ("numpy", "scipy", "pandas", "torch", "sklearn", "matplotlib"):
            self.assertNotIn(name, sys.modules, msg=f"第三方包 {name} 被加载")

    def test_tag_mount_imports_are_stdlib_or_project_only(self):
        # AST 检查：模块内所有 import 的顶层名都在标准库/项目白名单
        tree = ast.parse(self._source())
        allowed_top = {"random", "enum", "dataclasses", "typing", "__future__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], allowed_top,
                                  msg=f"越权 import: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                top = (node.module or "").split(".")[0]
                self.assertTrue(
                    node.level > 0 or top in allowed_top,
                    msg=f"越权 import from: {node.module}")


if __name__ == "__main__":
    unittest.main()

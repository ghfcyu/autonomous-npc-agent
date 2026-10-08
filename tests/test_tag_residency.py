"""T2 收口先行批 · chen/lily 挂载常驻化测试（审查第八次报告指令 1）。

背景：审查批评"NPC 默认状态下玩家尚感知不到标签人格"——标签挂载
此前是 NPCEngine.mount_tags 显式 API，默认不启用。常驻化后：configs
声明 tag_profile="genesis" 的核心 NPC 在引擎初始化（spawn）时自动
挂载标签账本，"断指的铁匠陈"成为 NPC 默认状态而非显式 API 调用结果。

覆盖（任务书验收口径 a-f）：
- a. 默认引擎初始化（Persona.load_all() + NPCEngine）后 chen/lily 的
     tag_ledger 非 None，缺陷 ≥1 + 把柄 ≥1，visible_tags 非空，
     secrets 与 visible_tags 不相交（把柄绝密隔离）；
- b. 确定性：两次独立初始化同一 NPC 账本 (tag_id, label, tier)
     序列全等（crc32(npc_id) 种子跨实例稳定）；
- c. 未声明 tag_profile 的 NPC（构造不含该字段的 Persona）spawn 后
     tag_ledger is None（向后兼容）；
- d. mount_tags 显式 API 向后兼容：对未声明 tag_profile 的 NPC 显式
     调用仍返回账本；
- e. 玩家可感知闭环（真实对话链路）：常驻化后对 chen 调 player_says
     （Mock LLM），发往 LLM 的 system prompt 含可见标签「断指」、
     不含把柄「私生血统」；lily 同理（「跛足」在、「私铸劣币」不在）；
- f. 引擎初始化自动加载 configs/background_npcs，全部背景 NPC 保持
     零挂载（声明式只作用于核心 NPC 配置）。

确定性锚点（crc32 种子实测，任务书给定）：chen 挂载缺陷「断指」
(uncommon) + 绝密把柄「私生血统」(rare)；lily 挂载「跛足」(rare) +
「私铸劣币」(rare)。
"""

import unittest

from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.npc import Persona
from engine.tag_mount import FLAW_POOL, SECRET_POOL


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


def make_plain_persona(npc_id="guard"):
    """构造不含 tag_profile 字段的 Persona（默认 None → 不自动挂载）。"""
    return Persona(id=npc_id, name="守卫", role="guard", location_id="plaza",
                   personality="尽职", speech_style="简短", backstory="老兵",
                   greeting_bank=["站住。"], fallback_bank=["嗯。"],
                   sleep_mumble="（鼾声）")


def ledger_signature(ledger):
    """账本签章：(tag_id, label, tier) 序列（任务书指定确定性口径）。"""
    return [(t.tag_id, t.label, t.tier) for t in ledger.all_tags()]


# ========================================================================== #
# a. 默认引擎初始化 → chen/lily 账本常驻 + 强制挂载 + 绝密隔离
# ========================================================================== #
class TestDefaultResidency(unittest.TestCase):
    """默认初始化（Persona.load_all + NPCEngine）后核心 NPC 自带标签人格。"""

    @classmethod
    def setUpClass(cls):
        cls.personas = Persona.load_all()
        cls.engine = NPCEngine(llm=MockLLMProvider())

    def test_configs_declare_genesis_for_core_npcs(self):
        # configs 声明口径：chen/lily 的 tag_profile == "genesis"，
        # 且是仅有的两个核心 NPC 配置
        profiles = {p.id: p.tag_profile for p in self.personas}
        self.assertEqual(profiles.get("chen"), "genesis")
        self.assertEqual(profiles.get("lily"), "genesis")

    def test_core_npcs_ledger_not_none_after_default_init(self):
        for npc_id in ("chen", "lily"):
            self.assertIsNotNone(self.engine.npcs[npc_id].tag_ledger,
                                 msg=f"{npc_id} 默认初始化应常驻标签账本")

    def test_forced_flaw_and_secret_mounted(self):
        # 规范 5.2：非背景 NPC 强制 ≥1 显性缺陷 + ≥1 绝密把柄
        for npc_id in ("chen", "lily"):
            ledger = self.engine.npcs[npc_id].tag_ledger
            flaws = [t for t in ledger.all_tags()
                     if t.tag_id.startswith("flaw_")]
            secrets = ledger.secrets()
            self.assertGreaterEqual(len(flaws), 1,
                                    msg=f"{npc_id} 缺陷缺失")
            self.assertGreaterEqual(len(secrets), 1,
                                    msg=f"{npc_id} 把柄缺失")
            # 标签中文与池一致（排除巧合文案）
            self.assertIn((flaws[0].tag_id, flaws[0].label),
                          {(e[0], e[1]) for e in FLAW_POOL})
            self.assertIn((secrets[0].tag_id, secrets[0].label),
                          {(e[0], e[1]) for e in SECRET_POOL})

    def test_visible_tags_non_empty_and_secret_isolated(self):
        for npc_id in ("chen", "lily"):
            ledger = self.engine.npcs[npc_id].tag_ledger
            # 可见标签非空：玩家默认状态下可感知标签人格
            self.assertTrue(ledger.visible_tags(),
                            msg=f"{npc_id} visible_tags 为空，玩家感知不到")
            visible_ids = {t.tag_id for t in ledger.visible_tags()}
            secret_ids = {t.tag_id for t in ledger.secrets()}
            # 把柄绝密隔离：secrets 与 visible_tags 不相交
            self.assertFalse(visible_ids & secret_ids,
                             msg=f"{npc_id} 把柄泄漏进可见标签")
            # secret 标记兜底：把柄条目全部 secret=True
            for tag in ledger.secrets():
                self.assertTrue(tag.secret)


# ========================================================================== #
# b. 确定性：两次独立初始化账本签章全等
# ========================================================================== #
class TestResidencyDeterminism(unittest.TestCase):
    def test_two_independent_inits_same_signature(self):
        engine_a = NPCEngine(llm=MockLLMProvider())
        engine_b = NPCEngine(llm=MockLLMProvider())
        for npc_id in ("chen", "lily"):
            sig_a = ledger_signature(engine_a.npcs[npc_id].tag_ledger)
            sig_b = ledger_signature(engine_b.npcs[npc_id].tag_ledger)
            self.assertTrue(sig_a, msg=f"{npc_id} 账本不应为空")
            self.assertEqual(sig_a, sig_b,
                             msg=f"{npc_id} 两次独立初始化账本不一致")

    def test_deterministic_anchor_labels(self):
        # crc32 种子确定性锚点（任务书给定）：chen「断指」+「私生血统」、
        # lily「跛足」+「私铸劣币」
        engine = NPCEngine(llm=MockLLMProvider())
        chen = engine.npcs["chen"].tag_ledger
        lily = engine.npcs["lily"].tag_ledger
        self.assertEqual([t.label for t in chen.visible_tags()], ["断指"])
        self.assertEqual([t.label for t in chen.secrets()], ["私生血统"])
        self.assertEqual([t.label for t in lily.visible_tags()], ["跛足"])
        self.assertEqual([t.label for t in lily.secrets()], ["私铸劣币"])
        # 与显式 API 同种子构建的账本一致（同一内核 _build_tag_ledger）
        explicit = NPCEngine(llm=MockLLMProvider(),
                              npc_configs=[make_plain_persona("chen")])
        self.assertEqual(ledger_signature(explicit.mount_tags("chen")),
                         ledger_signature(engine.npcs["chen"].tag_ledger))


# ========================================================================== #
# c/d. 向后兼容：未声明 tag_profile 零挂载 + mount_tags 显式 API 不变
# ========================================================================== #
class TestBackwardCompat(unittest.TestCase):
    def test_undeclared_persona_spawns_with_none_ledger(self):
        engine = NPCEngine(llm=MockLLMProvider(),
                           npc_configs=[make_plain_persona()])
        npc = engine.spawn(make_plain_persona("guard_b"))
        self.assertIsNone(npc.tag_ledger,
                          msg="未声明 tag_profile 的 NPC 不应自动挂载")
        self.assertIsNone(engine.npcs["guard"].tag_ledger)

    def test_persona_tag_profile_defaults_to_none(self):
        self.assertIsNone(make_plain_persona().tag_profile)

    def test_mount_tags_still_works_for_undeclared_npc(self):
        # 显式 API 向后兼容：未声明 tag_profile 的 NPC 显式挂载仍得账本
        engine = NPCEngine(llm=MockLLMProvider(),
                           npc_configs=[make_plain_persona()])
        ledger = engine.mount_tags("guard")
        self.assertIsNotNone(ledger)
        self.assertIs(engine.npcs["guard"].tag_ledger, ledger)
        self.assertGreaterEqual(
            len([t for t in ledger.all_tags() if t.tag_id.startswith("flaw_")]), 1)
        self.assertGreaterEqual(len(ledger.secrets()), 1)

    def test_mount_tags_unknown_npc_still_returns_none(self):
        engine = NPCEngine(llm=MockLLMProvider(),
                           npc_configs=[make_plain_persona()])
        self.assertIsNone(engine.mount_tags("nobody"))


# ========================================================================== #
# e. 玩家可感知闭环（真实对话链路）
# ========================================================================== #
class TestPlayerPerceptionAfterResidency(unittest.TestCase):
    """常驻化后无需任何显式调用，对话链路即可感知可见标签、隔离把柄。"""

    @classmethod
    def setUpClass(cls):
        cls.llm = RecordingLLM()
        cls.engine = NPCEngine(llm=cls.llm)
        cls.engine.player_says("铁匠，打把剑", "chen")
        cls.engine.player_says("有新货吗", "lily")

    def test_chen_visible_label_in_prompt(self):
        system = self.llm.captured[-2][0]["content"]
        self.assertIn("身份标签：断指", system)

    def test_chen_secret_label_never_in_prompt(self):
        full_text = "".join(self.llm.captured[-2][0].values()) + \
            "".join(self.llm.captured[-2][1].values())
        self.assertNotIn("私生血统", full_text,
                          msg="chen 绝密把柄泄漏进决策上下文")

    def test_lily_visible_label_in_prompt(self):
        system = self.llm.captured[-1][0]["content"]
        self.assertIn("身份标签：跛足", system)

    def test_lily_secret_label_never_in_prompt(self):
        full_text = "".join(self.llm.captured[-1][0].values()) + \
            "".join(self.llm.captured[-1][1].values())
        self.assertNotIn("私铸劣币", full_text,
                          msg="lily 绝密把柄泄漏进决策上下文")


# ========================================================================== #
# f. 背景 NPC 零挂载（声明式只作用于核心 NPC 配置）
# ========================================================================== #
class TestBackgroundZeroMount(unittest.TestCase):
    def test_default_engine_background_npcs_have_no_ledger(self):
        engine = NPCEngine(llm=MockLLMProvider())
        self.assertGreaterEqual(len(engine.background_npcs), 8)
        for bg_id, bg in engine.background_npcs.items():
            self.assertFalse(hasattr(bg, "tag_ledger"),
                             msg=f"背景 NPC {bg_id} 不应有标签账本")


if __name__ == "__main__":
    unittest.main()

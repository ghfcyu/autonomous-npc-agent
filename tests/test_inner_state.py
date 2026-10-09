"""T1 验收测试：8D 正交心智基底 + 荀子六情映射 + 亲缘度迁移关系网。

覆盖八块契约：
1. InnerState 数据类：8D 默认值、clamp、序列化、离散标签
   （8D 状态带离散化，2026-10-10：旧 8 参数浮点文本方法已删，
   【此刻内心】行改消费 to_discrete_tags 离散标签口径）
2. StateUpdater · item_given：送礼 → 六情"好"矢量 + 亲缘度写入关系网
3. StateUpdater · player_spoke：赞美"喜"/批评"怒" + 较为自负标签放大
4. DecisionEngine 提示词：8D inner_state 与 tags 进入决策上下文
   （inner_state 以 ≤4 个离散中文标签进入，无浮点）
5. DecisionEngine 硬约束：S_stress 过高 / p_fatigue 过高 → REFUSE
6. StateUpdater · time_passed：疲劳随时间变化（睡觉恢复、清醒累积）
7. StateUpdater · npc_action：行动消耗疲劳
8. NPCEngine.player_gives 防御：G1 遗留修复——给玩家送礼被拒
9. <35token 红线预防：【此刻内心】行无浮点、标签 ≤4、全常带单标签
"""

import re
import unittest

from engine.actions import ActionType
from engine.engine import NPCEngine
from engine.inner_state import InnerState
from engine.llm.mock import MockLLMProvider
from engine.npc import Persona
from engine.states import NPCState
from engine.world import World, WorldEvent


# --------------------------------------------------------------------------- #
# 测试固件
# --------------------------------------------------------------------------- #

def _make_npc(persona_kwargs=None, tags=None):
    """构造单个 NPC 测试固件（不经过 NPCEngine，直接 NPC 构造）。

    NPC 构造会自动创建关系网并注入 chen→player 边（客人，affinity=0.5）。
    返回 (npc, world, persona) 三元组。
    """
    from engine.npc import NPC

    persona = Persona(
        id="chen",
        name="铁匠陈",
        role="blacksmith",
        location_id="forge",
        personality="沉稳寡言",
        speech_style="简短",
        greeting_bank=["你好"],
        fallback_bank=["嗯"],
        topic_responses={"铁|矿石": ["好铁矿"]},
        tags=tags or {},
        **(persona_kwargs or {}),
    )
    world = World()
    npc = NPC(persona, MockLLMProvider(), world)
    return npc, world, persona


def _evt(kind, actor="player", **payload):
    """快捷构造 WorldEvent（tick=0）。"""
    return WorldEvent(tick=0, kind=kind, actor=actor, payload=payload)


# ============================================================================ #
# 1. InnerState 基础测试
# ============================================================================ #

class TestInnerState(unittest.TestCase):
    """InnerState 数据类：8D 默认值、clamp、序列化、提示词文本。"""

    def test_default_values(self):
        """默认值：p_fatigue=0.2, p_hunger=0.3, p_pain=0.1, p_drive=0.5,
        e_P=0.5, e_A=0.3, e_D=0.5, S_stress=0.2。"""
        s = InnerState()
        self.assertAlmostEqual(s.p_fatigue, 0.2)
        self.assertAlmostEqual(s.p_hunger, 0.3)
        self.assertAlmostEqual(s.p_pain, 0.1)
        self.assertAlmostEqual(s.p_drive, 0.5)
        self.assertAlmostEqual(s.e_P, 0.5)
        self.assertAlmostEqual(s.e_A, 0.3)
        self.assertAlmostEqual(s.e_D, 0.5)
        self.assertAlmostEqual(s.S_stress, 0.2)

    def test_apply_delta_clamp(self):
        """apply_delta 后值不超出 [0, 1]——测上界和下界。"""
        s = InnerState()
        # p_fatigue 上界：从 0.2 加 1.0 → clamp 到 1.0
        s.apply_delta("p_fatigue", 1.0)
        self.assertAlmostEqual(s.p_fatigue, 1.0)
        # p_fatigue 下界：从 1.0 减 2.0 → clamp 到 0.0
        s.apply_delta("p_fatigue", -2.0)
        self.assertAlmostEqual(s.p_fatigue, 0.0)
        # e_P 下界：从 0.5 减 1.0 → clamp 到 0.0
        s.apply_delta("e_P", -1.0)
        self.assertAlmostEqual(s.e_P, 0.0)
        # S_stress 上界：从 0.2 加 1.0 → clamp 到 1.0
        s.apply_delta("S_stress", 1.0)
        self.assertAlmostEqual(s.S_stress, 1.0)

    def test_to_discrete_tags_significant(self):
        """to_discrete_tags：显著偏离带 → 对应离散标签，按偏离幅度降序。

        功能变更（8D 状态带离散化，2026-10-10）：旧断言 8 参数
        中文浮点文本，随旧浮点文本方法删除失效，
        改写为离散标签口径。"""
        s = InnerState(S_stress=0.85, p_fatigue=0.9)
        # 偏离幅度：心烦意乱 0.85-0.6=0.25 > 疲惫不堪 0.9-0.7=0.20 → 降序
        self.assertEqual(s.to_discrete_tags(), ["心烦意乱", "疲惫不堪"])

    def test_to_discrete_tags_all_normal(self):
        """to_discrete_tags：全常带返回单标签 ["心境平稳"]（保持行存在）。"""
        self.assertEqual(InnerState().to_discrete_tags(), ["心境平稳"])

    def test_to_discrete_tags_band_boundary(self):
        """带边界锁：恰好等于阈值不命中（严格 >/<，与掩码口径一致）。"""
        self.assertEqual(InnerState(S_stress=0.6).to_discrete_tags(),
                         ["心境平稳"])        # 0.6 不> 0.6
        self.assertIn("心烦意乱",
                      InnerState(S_stress=0.61).to_discrete_tags())
        self.assertEqual(InnerState(e_P=0.4).to_discrete_tags(),
                         ["心境平稳"])        # 0.4 不< 0.4
        self.assertIn("情绪低落", InnerState(e_P=0.39).to_discrete_tags())

    def test_to_discrete_tags_top4_cap(self):
        """top-4 锁：7 带全命中也截断到 4 个，按偏离幅度降序。"""
        s = InnerState(S_stress=0.95, e_A=0.95, e_P=0.95, e_D=0.95,
                       p_fatigue=0.95, p_hunger=0.95, p_pain=0.95)
        tags = s.to_discrete_tags()
        self.assertEqual(len(tags), 4)
        # 偏离幅度：旧伤作痛 0.95-0.5=0.45 > 心烦意乱 0.95-0.6=0.35
        # > 其余并列 0.25（并列时保持 DISCRETE_BANDS 定义序，e_A 领先）
        self.assertEqual(tags[:2], ["旧伤作痛", "心烦意乱"])
        self.assertEqual(tags[2], "情绪激动")

    def test_to_dict(self):
        """to_dict 返回 8 个键，值为 float。"""
        s = InnerState()
        d = s.to_dict()
        self.assertEqual(len(d), 8)
        for key in InnerState.PARAMS:
            self.assertIn(key, d)
            self.assertIsInstance(d[key], float)


# ============================================================================ #
# 2. 送礼状态变化（六情"好" + 亲缘度迁移关系网）
# ============================================================================ #

class TestStateGift(unittest.TestCase):
    """验收：送礼 → PAD 增量精确断言 + 亲缘度在关系网中可查。"""

    def test_gift_increases_affinity_and_mood(self):
        """item_given：好矢量 e_P+0.05/e_A+0.03/e_D+0.02，S_stress-0.05，
        对 player 亲缘度 +0.1。"""
        npc, world, _ = _make_npc()
        world.bus.publish(_evt("item_given", to="chen", item="铁矿石"))
        self.assertAlmostEqual(npc.inner_state.e_P, 0.55)    # 0.5 + 0.05
        self.assertAlmostEqual(npc.inner_state.e_A, 0.33)    # 0.3 + 0.03
        self.assertAlmostEqual(npc.inner_state.e_D, 0.52)   # 0.5 + 0.02
        self.assertAlmostEqual(npc.inner_state.S_stress, 0.15)  # 0.2 - 0.05
        rel = npc.relationships.get_relation_to("chen", "player")
        self.assertIsNotNone(rel)
        self.assertAlmostEqual(rel.affinity, 0.6)            # 0.5 + 0.1

    def test_gift_with_shoucai_tag(self):
        """守财标签：亲缘度额外 +0.05*0.8 = 0.04，总增量 0.14 → 0.64；
        心智基底增量与无标签一致。"""
        npc, world, _ = _make_npc(tags={"守财": 0.8})
        world.bus.publish(_evt("item_given", to="chen", item="铁矿石"))
        rel = npc.relationships.get_relation_to("chen", "player")
        self.assertAlmostEqual(rel.affinity, 0.64)           # 0.5 + 0.1 + 0.04
        self.assertAlmostEqual(npc.inner_state.e_P, 0.55)
        self.assertAlmostEqual(npc.inner_state.e_A, 0.33)
        self.assertAlmostEqual(npc.inner_state.e_D, 0.52)
        self.assertAlmostEqual(npc.inner_state.S_stress, 0.15)

    def test_gift_to_other_npc_ignored(self):
        """to 指向其他 NPC 时，本 NPC 心智基底与亲缘度均不变。"""
        npc, world, _ = _make_npc()
        world.bus.publish(_evt("item_given", to="other", item="铁矿石"))
        self.assertAlmostEqual(npc.inner_state.e_P, 0.5)
        self.assertAlmostEqual(npc.inner_state.e_A, 0.3)
        self.assertAlmostEqual(npc.inner_state.S_stress, 0.2)
        rel = npc.relationships.get_relation_to("chen", "player")
        self.assertAlmostEqual(rel.affinity, 0.5)


# ============================================================================ #
# 3. 标签行为影响（赞美"喜"/批评"怒" + 较为自负放大）
# ============================================================================ #

class TestStateTagEffect(unittest.TestCase):
    """较为自负标签对赞美/批评唤醒与愉悦的放大效应。"""

    def test_self_esteem_amplifies_arousal_on_praise(self):
        """有标签 e_A = 0.3 + 0.05(基础) + 0.05(喜) + 0.15*0.7 = 0.505；
        无标签 = 0.40。"""
        npc_tagged, world_t, _ = _make_npc(tags={"较为自负": 0.7})
        npc_plain, world_p, _ = _make_npc()
        # 用包含赞美关键词（"手艺"）的文本
        world_t.bus.publish(_evt("player_spoke", to="chen", text="你的手艺真好"))
        world_p.bus.publish(_evt("player_spoke", to="chen", text="你的手艺真好"))
        self.assertAlmostEqual(npc_tagged.inner_state.e_A, 0.505)
        self.assertAlmostEqual(npc_plain.inner_state.e_A, 0.40)

    def test_self_esteem_deepens_mood_on_criticism(self):
        """有标签批评：e_P = 0.5 - 0.08(怒) - 0.15*0.7 = 0.315。"""
        npc, world, _ = _make_npc(tags={"较为自负": 0.7})
        world.bus.publish(_evt("player_spoke", to="chen", text="你打的东西真烂"))
        self.assertAlmostEqual(npc.inner_state.e_P, 0.315)

    def test_no_tag_no_amplification(self):
        """无标签赞美：e_A = 0.3 + 0.05(基础) + 0.05(喜) = 0.40。"""
        npc, world, _ = _make_npc()
        world.bus.publish(_evt("player_spoke", to="chen", text="你的手艺真好"))
        self.assertAlmostEqual(npc.inner_state.e_A, 0.40)


# ============================================================================ #
# 4. 状态进入决策上下文
# ============================================================================ #

class TestStateInPrompt(unittest.TestCase):
    """8D inner_state 与 tags 进入决策提示词。"""

    def test_user_prompt_contains_state(self):
        """_user_prompt 传入 inner_state 后【此刻内心】行只含离散标签。

        功能变更（8D 状态带离散化，2026-10-10）：旧断言"含参数名'唤醒'
        与浮点'0.77'"随旧浮点文本方法删除失效，改写为离散标签口径
        （显著标签名在、浮点与旧参数名不在）。"""
        npc, world, _ = _make_npc()
        npc.inner_state.e_A = 0.77
        memory_ctx = npc.memory.context_for("你好")
        snapshot = world.snapshot("chen")
        prompt = npc.decision._user_prompt(
            "你好", world, memory_ctx, snapshot, npc.inner_state)
        self.assertIn("此刻内心", prompt)
        self.assertIn("情绪激动", prompt)   # e_A=0.77 > 0.7 → 显著标签
        self.assertNotIn("0.77", prompt)    # 严禁浮点进入该行
        self.assertNotIn("唤醒", prompt)     # 旧参数名口径不复存在

    def test_system_prompt_contains_tags(self):
        """_system_prompt 在有 tags 时包含"性格标签"及标签名和值。"""
        npc, _, _ = _make_npc(tags={"较为自负": 0.7})
        prompt = npc.decision._system_prompt()
        self.assertIn("性格标签", prompt)
        self.assertIn("较为自负", prompt)
        self.assertIn("0.7", prompt)


# ============================================================================ #
# 5. 硬约束
# ============================================================================ #

class TestHardConstraints(unittest.TestCase):
    """decide() 在 LLM 调用前的 S_stress / p_fatigue 硬约束。"""

    def test_high_stress_refuses(self):
        """S_stress > 0.8 → REFUSE(reason="stress_too_high")。"""
        npc, world, _ = _make_npc()
        npc.inner_state.S_stress = 0.85
        action = npc.handle_player_input(world, "帮我打把剑")
        self.assertIs(action.type, ActionType.REFUSE)
        self.assertEqual(action.payload.get("reason"), "stress_too_high")

    def test_high_fatigue_refuses(self):
        """p_fatigue > 0.85 → REFUSE(reason="fatigue_too_high")。"""
        npc, world, _ = _make_npc()
        npc.inner_state.p_fatigue = 0.90
        action = npc.handle_player_input(world, "帮我打把剑")
        self.assertIs(action.type, ActionType.REFUSE)
        self.assertEqual(action.payload.get("reason"), "fatigue_too_high")

    def test_normal_state_does_not_refuse(self):
        """默认状态（S_stress=0.2, p_fatigue=0.2）不触发硬约束，应返回 SPEAK。"""
        npc, world, _ = _make_npc()
        action = npc.handle_player_input(world, "你好")
        self.assertIsNot(action.type, ActionType.REFUSE)
        self.assertIs(action.type, ActionType.SPEAK)


# ============================================================================ #
# 6. 疲劳随时间变化
# ============================================================================ #

class TestPhysiologyOnTime(unittest.TestCase):
    """time_passed 事件：睡觉恢复疲劳、清醒累积疲劳与饥饿。"""

    def test_fatigue_regen_when_sleeping(self):
        """SLEEPING 时 p_fatigue -0.05, S_stress -0.02。"""
        npc, world, _ = _make_npc()
        npc.state_machine.force(NPCState.SLEEPING)
        world.bus.publish(_evt("time_passed", actor="world", clock="08:00"))
        self.assertAlmostEqual(npc.inner_state.p_fatigue, 0.15)  # 0.2 - 0.05
        self.assertAlmostEqual(npc.inner_state.S_stress, 0.18)  # 0.2 - 0.02

    def test_fatigue_drain_when_awake(self):
        """非 SLEEPING（默认 WORKING）时 p_fatigue +0.01, p_hunger +0.01。"""
        npc, world, _ = _make_npc()
        world.bus.publish(_evt("time_passed", actor="world", clock="08:00"))
        self.assertAlmostEqual(npc.inner_state.p_fatigue, 0.21)  # 0.2 + 0.01
        self.assertAlmostEqual(npc.inner_state.p_hunger, 0.31)  # 0.3 + 0.01


# ============================================================================ #
# 7. NPC 行动消耗疲劳
# ============================================================================ #

class TestNpcActionFatigue(unittest.TestCase):
    """npc_action 事件：p_fatigue +0.02, e_A -0.01。"""

    def test_speak_costs_fatigue(self):
        """NPC 行动后 p_fatigue 上升、e_A 下降。"""
        npc, world, _ = _make_npc()
        world.bus.publish(_evt("npc_action", actor="chen",
                              npc="chen", summary="说了话"))
        self.assertAlmostEqual(npc.inner_state.p_fatigue, 0.22)  # 0.2 + 0.02
        self.assertAlmostEqual(npc.inner_state.e_A, 0.29)  # 0.3 - 0.01


# ============================================================================ #
# 8. G1 遗留修复
# ============================================================================ #

class TestPlayerGivesDefense(unittest.TestCase):
    """player_gives 检查改为 self.npcs，给玩家送礼被拒。"""

    def test_give_to_player_rejected(self):
        """player_gives("player", ...) → {"ok": False, "reason": "unknown npc"}。"""
        engine = NPCEngine(
            llm=MockLLMProvider(),
            npc_configs=[Persona(
                id="chen", name="铁匠陈", role="blacksmith", location_id="forge",
                greeting_bank=["你好"], fallback_bank=["嗯"],
            )],
        )
        result = engine.player_gives("player", "苹果")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unknown npc")


# ============================================================================ #
# 9. <35token 红线预防：【此刻内心】行离散化纪律（8D 状态带离散化，
#    2026-10-10 追加）
# ============================================================================ #

class TestPromptTokenRedline(unittest.TestCase):
    """<35token 红线预防：决策上下文【此刻内心】行只消费离散标签。

    (a) 显著状态下【此刻内心】行无浮点（正则 \\d\\.\\d 零匹配）；
    (b) 离散标签数 ≤4（7 带全命中也截断）；
    (c) 全常带时行内含"心境平稳"且无其他标签。
    """

    @staticmethod
    def _inner_line(**overrides):
        """构造 NPC 并按 overrides 预置 8D 后，提取【此刻内心】行内容。"""
        npc, world, _ = _make_npc()
        for key, value in overrides.items():
            setattr(npc.inner_state, key, value)
        memory_ctx = npc.memory.context_for("你好")
        snapshot = world.snapshot("chen")
        prompt = npc.decision._user_prompt(
            "你好", world, memory_ctx, snapshot, npc.inner_state)
        match = re.search(r"【此刻内心】(.+)", prompt)
        assert match is not None, "【此刻内心】行缺失"
        return match.group(1)

    def test_redline_no_float_in_inner_line(self):
        """(a) 显著状态下【此刻内心】行正则 \\d\\.\\d 零匹配。"""
        line = self._inner_line(S_stress=0.85, e_A=0.9, e_P=0.9,
                                e_D=0.9, p_fatigue=0.9, p_hunger=0.9,
                                p_pain=0.9)
        self.assertIsNone(re.search(r"\d\.\d", line),
                          msg=f"浮点泄漏进【此刻内心】行: {line}")

    def test_redline_tag_count_le_4(self):
        """(b) 7 带全命中，【此刻内心】行离散标签数 ≤4（顿号分隔计）。"""
        line = self._inner_line(S_stress=0.95, e_A=0.95, e_P=0.95,
                                e_D=0.95, p_fatigue=0.95, p_hunger=0.95,
                                p_pain=0.95)
        tags = line.split("、")
        self.assertLessEqual(len(tags), 4,
                             msg=f"离散标签数 {len(tags)} 超限: {line}")

    def test_redline_calm_single_tag_when_normal(self):
        """(c) 全常带时【此刻内心】行含"心境平稳"且无其他标签。"""
        line = self._inner_line()
        self.assertIn("心境平稳", line)
        self.assertEqual(line.split("、"), ["心境平稳"])


if __name__ == "__main__":
    unittest.main()

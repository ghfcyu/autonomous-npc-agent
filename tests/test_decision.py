"""决策层测试：状态机约束、动作校验、LLM 异常回退。"""

import unittest

from engine.actions import Action, ActionType, ActionValidator
from engine.llm.mock import MockLLMProvider
from engine.npc import NPC, Persona
from engine.states import NPCState, StateMachine
from engine.world import Entity, World


class BrokenLLM:
    """永远返回坏输出的假 LLM，用于验证安全回退。"""

    name = "broken"

    def chat(self, messages, temperature=0.7):
        return "我才不输出 JSON 呢~~~~"


class _StubNPC(NPC):
    """绕开世界注册，仅供校验器测试。"""


def make_persona():
    return Persona(
        id="chen", name="铁匠陈", role="blacksmith", location_id="forge",
        personality="沉稳", speech_style="简短", backstory="三代铁匠",
        greeting_bank=["炉子热着。"], fallback_bank=["嗯，你说。"],
        sleep_mumble="（鼾声）",
        schedule={"20": "SLEEPING"},
        topic_responses={"剑|刀": ["打剑？拿好铁来。"]},
    )


def make_npc(llm, state=NPCState.IDLE):
    world = World()
    npc = NPC(make_persona(), llm, world)
    npc.state_machine.force(state)
    return npc, world


class TestStateMachine(unittest.TestCase):
    def test_allowed_transitions(self):
        sm = StateMachine(NPCState.IDLE)
        self.assertTrue(sm.transition(NPCState.TALKING))
        self.assertTrue(sm.transition(NPCState.WORKING))
        self.assertTrue(sm.transition(NPCState.SLEEPING))

    def test_sleeping_is_sticky(self):
        sm = StateMachine(NPCState.SLEEPING)
        self.assertFalse(sm.transition(NPCState.TALKING))
        self.assertFalse(sm.enter_talking())

    def test_talking_exits_only_to_idle_or_working(self):
        sm = StateMachine(NPCState.TALKING)
        self.assertFalse(sm.transition(NPCState.SLEEPING))
        self.assertTrue(sm.exit_talking())


class TestActionValidator(unittest.TestCase):
    def test_reject_unknown_action(self):
        npc, world = make_npc(MockLLMProvider())
        action = Action(ActionType.MOVE, {})  # 借用枚举构造非法状态
        action.type = "hack"  # 模拟越权类型
        ok, reason = ActionValidator.validate(action, npc, world)
        self.assertFalse(ok)

    def test_reject_speaking_while_asleep(self):
        npc, world = make_npc(MockLLMProvider(), state=NPCState.SLEEPING)
        action = Action(ActionType.SPEAK, {"text": "我不困"})
        ok, reason = ActionValidator.validate(action, npc, world)
        self.assertFalse(ok)
        self.assertIn("sleeping", reason)

    def test_reject_giving_item_not_owned(self):
        npc, world = make_npc(MockLLMProvider())
        action = Action(ActionType.GIVE_ITEM, {"item": "屠龙宝刀"})
        ok, reason = ActionValidator.validate(action, npc, world)
        self.assertFalse(ok)
        self.assertIn("own", reason)

    def test_reject_speak_without_text(self):
        npc, world = make_npc(MockLLMProvider())
        ok, _ = ActionValidator.validate(Action(ActionType.SPEAK, {}), npc, world)
        self.assertFalse(ok)

    def test_accept_normal_speak(self):
        npc, world = make_npc(MockLLMProvider())
        ok, _ = ActionValidator.validate(Action(ActionType.SPEAK, {"text": "好"}), npc, world)
        self.assertTrue(ok)


class TestDecisionEngine(unittest.TestCase):
    def test_mock_llm_topic_match(self):
        npc, world = make_npc(MockLLMProvider())
        action = npc.handle_player_input(world, "帮我打一把剑")
        self.assertIs(action.type, ActionType.SPEAK)
        self.assertIn("剑", action.payload["text"])

    def test_greeting_on_first_contact(self):
        npc, world = make_npc(MockLLMProvider())
        action = npc.handle_player_input(world, "喂")
        self.assertIn(action.payload["text"], npc.persona.greeting_bank)

    def test_sleeping_npc_returns_mumble_without_llm(self):
        class BoomLLM(MockLLMProvider):
            def chat(self, messages, temperature=0.7):
                raise AssertionError("睡觉中不应调用 LLM")

        npc, world = make_npc(BoomLLM(), state=NPCState.SLEEPING)
        action = npc.handle_player_input(world, "醒醒！")
        self.assertEqual(action.payload["text"], npc.persona.sleep_mumble)

    def test_broken_llm_falls_back_safely(self):
        npc, world = make_npc(BrokenLLM())
        action = npc.handle_player_input(world, "你好")
        self.assertIs(action.type, ActionType.SPEAK)
        self.assertIn(action.payload["text"], npc.persona.fallback_bank)

    def test_chaotic_llm_falls_back_safely(self):
        npc, world = make_npc(MockLLMProvider(chaos_rate=1.0))
        action = npc.handle_player_input(world, "你好")
        self.assertIn(action.payload["text"], npc.persona.fallback_bank)


if __name__ == "__main__":
    unittest.main()

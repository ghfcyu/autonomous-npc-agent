"""G4 背景轻量 NPC 测试：零 LLM 的规则化反应 + 引擎装配。

覆盖三层：
1. ``BackgroundNPC`` 单元（创建注册 / to_dict 契约）；
2. 规则反应（env_event 只反应同地点 / entity_moved 只反应抵达 /
   模板按计数器轮转、确定性可预测）；
3. 引擎集成（核心+背景 NPC 混编 / status 可见 / 反应被同地点核心 NPC
   感知进记忆 / 背景 NPC 全程零 LLM 调用）。

背景 NPC 配置契约（dict 结构）：
    {"id": ..., "name": ..., "role": ..., "location_id": ...,
     "summary": ..., "reactions": {事件类型: [反应模板, ...]}}
模板中 ``{npc_name}`` 替换为 NPC 名字，其余占位符取自事件 payload；
反应以 ``npc_action`` 事件回流总线（actor 为背景 NPC 自身 id）。

环境事件池契约（engine/world.py 默认，按 trigger_count 取模）：
    trigger 1/3/5 → chen@forge；trigger 2/4/6 → lily@market。
默认事件槽步长 0.05、阈值 1.0：每 20 次 ``accumulate_event_slot()`` 触发一次。
"""

import unittest

from engine.background_npc import BackgroundNPC
from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.npc import Persona
from engine.world import Entity, World


def chen_persona():
    return Persona(id="chen", name="铁匠陈", role="blacksmith", location_id="forge",
                   personality="沉稳", speech_style="简短", backstory="三代铁匠",
                   greeting_bank=["炉子热着。"], fallback_bank=["嗯，你说。"],
                   sleep_mumble="（鼾声）", schedule={"20": "SLEEPING"},
                   topic_responses={"剑|刀": ["打剑？拿好铁来。"]})


def lily_persona():
    return Persona(id="lily", name="商人莉莉", role="merchant", location_id="market",
                   personality="热情", speech_style="快", backstory="跑过五个城镇",
                   greeting_bank=["贵客来啦！"], fallback_bank=["我听着呢！"],
                   sleep_mumble="（梦中谈价）", schedule={"21": "SLEEPING"})


def bg_config(npc_id, name, role, location_id, summary, reactions):
    """构造背景 NPC 配置 dict（G4 契约结构）。"""
    return {"id": npc_id, "name": name, "role": role,
            "location_id": location_id, "summary": summary,
            "reactions": reactions}


def old_zhang_config():
    """老张：集市闲人，对环境事件有两条轮转模板。"""
    return bg_config("old_zhang", "老张", "villager", "market",
                     "集市里的闲人，逢事必评一句",
                     {"env_event": ["{npc_name} 摇着蒲扇嘀咕：{summary}",
                                    "{npc_name} 抬眼看了看：{summary}"]})


def guard_wang_config():
    """守卫王：广场守卫，盯环境事件，对来人点头。"""
    return bg_config("guard_wang", "守卫王", "guard", "plaza",
                     "广场上巡逻的守卫",
                     {"env_event": ["{npc_name} 手按剑柄扫视四周：{summary}"],
                      "entity_moved": ["{npc_name} 朝走过来的方向点了点头"]})


def make_bg_engine(backgrounds):
    """核心（chen@forge、lily@market）+ 背景混编引擎。

    chen/lily 必须存在：默认环境事件池的 actor 是它们，
    背景 NPC 按 actor 实体位置判断是否同地。
    """
    return NPCEngine(llm=MockLLMProvider(),
                     npc_configs=[chen_persona(), lily_persona()],
                     background_configs=backgrounds)


def trigger_env_events(world, count):
    """按默认步长 0.05 累积事件槽：每 20 次触发 1 个环境事件。"""
    for _ in range(20 * count):
        world.accumulate_event_slot()


def npc_actions(bus, actor):
    """事件历史中某 actor 产出的全部 npc_action。"""
    return [e for e in bus.history if e.kind == "npc_action" and e.actor == actor]


class TestBackgroundNPCBasics(unittest.TestCase):
    """BackgroundNPC 单元：创建注册与 to_dict 契约。"""

    def test_creation_registers_entity_and_identity(self):
        world = World()
        npc = BackgroundNPC(old_zhang_config(), world)
        self.assertEqual(npc.id, "old_zhang")
        self.assertEqual(npc.name, "老张")
        self.assertEqual(npc.role, "villager")
        self.assertEqual(npc.summary, "集市里的闲人，逢事必评一句")
        # 实体注册进世界，位置正确
        self.assertIn("old_zhang", world.entities)
        self.assertEqual(npc.entity.id, "old_zhang")
        self.assertEqual(npc.entity.location_id, "market")
        self.assertEqual(world.entities["old_zhang"].location_id, "market")

    def test_to_dict_marks_background(self):
        world = World()
        npc = BackgroundNPC(old_zhang_config(), world)
        data = npc.to_dict()
        self.assertEqual(data["type"], "background")
        self.assertEqual(data["id"], "old_zhang")


class TestBackgroundNPCReactions(unittest.TestCase):
    """规则反应：地点过滤 + 模板轮转确定性，全程零 LLM。"""

    def test_reacts_to_env_event_at_same_location(self):
        engine = make_bg_engine([old_zhang_config()])
        # 触发 2 次：第 1 次 chen@forge（无反应），第 2 次 lily@market（反应）
        trigger_env_events(engine.world, 2)
        actions = npc_actions(engine.world.bus, "old_zhang")
        self.assertEqual(len(actions), 1)
        # 模板已格式化：{npc_name} 替换 + 事件 summary 注入
        summary = actions[0].payload["summary"]
        self.assertIn("摇着蒲扇", summary)  # 首次反应用第 1 条模板
        self.assertIn("老张", summary)
        self.assertIn("莉莉盘算着新货的报价", summary)

    def test_ignores_env_event_at_other_location(self):
        engine = make_bg_engine([guard_wang_config()])
        # 触发 1 次：首个事件是 chen@forge
        trigger_env_events(engine.world, 1)
        envs = [e for e in engine.world.bus.history if e.kind == "env_event"]
        self.assertEqual(len(envs), 1)
        self.assertEqual(envs[0].actor, "chen")
        # 对照：事件确实发生了，但 plaza 的守卫不反应 forge 的事
        self.assertEqual(npc_actions(engine.world.bus, "guard_wang"), [])

    def test_reacts_when_entity_moves_to_same_location(self):
        engine = make_bg_engine([guard_wang_config()])
        result = engine.move_player("market")  # 离开 plaza
        self.assertTrue(result["ok"])
        self.assertEqual(npc_actions(engine.world.bus, "guard_wang"), [])
        engine.move_player("plaza")            # 回到 plaza
        actions = npc_actions(engine.world.bus, "guard_wang")
        self.assertEqual(len(actions), 1)
        self.assertIn("点了点头", actions[0].payload["summary"])
        self.assertIn("守卫王", actions[0].payload["summary"])

    def test_ignores_departure_move(self):
        engine = make_bg_engine([guard_wang_config()])
        engine.move_player("market")  # 玩家从 plaza 离开
        moved = [e for e in engine.world.bus.history if e.kind == "entity_moved"]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].payload["from"], "plaza")
        self.assertEqual(moved[0].payload["to"], "market")
        # 离开不算"来到我这里"：不反应
        self.assertEqual(npc_actions(engine.world.bus, "guard_wang"), [])

    def test_reaction_templates_rotate_deterministically(self):
        # 直接构造世界：事件池全部是 chen@forge，广场旁的碎嘴逢事必反应，
        # 从而模板轮转序列不受"未反应事件是否计数"的实现差异影响。
        world = World(env_events=[
            {"actor": "chen", "summary": "炉火正旺"},
            {"actor": "chen", "summary": "有人吆喝"},
        ])
        world.add_entity(Entity(id="chen", kind="npc", name="铁匠陈",
                                location_id="forge"), announce=False)
        BackgroundNPC(bg_config(
            "gossip", "碎嘴闲人", "idler", "forge", "爱搭话的路人",
            {"env_event": ["{npc_name} 摇着头小声说：{summary}",
                           "{npc_name} 竖起了耳朵：{summary}"]}), world)
        for _ in range(4):
            self.assertTrue(world.accumulate_event_slot(1.0))
        actions = npc_actions(world.bus, "gossip")
        self.assertEqual(len(actions), 4)
        texts = [e.payload["summary"] for e in actions]
        # 按计数器取模轮转：模板 0 → 1 → 0 → 1
        self.assertIn("摇着头小声说", texts[0])
        self.assertIn("竖起了耳朵", texts[1])
        self.assertIn("摇着头小声说", texts[2])
        self.assertIn("竖起了耳朵", texts[3])
        self.assertNotEqual(texts[0], texts[1])
        # 同一模板 + 同一事件（事件池周期 2）→ 完全相同的输出（确定性）
        self.assertEqual(texts[0], texts[2])
        self.assertEqual(texts[1], texts[3])


class TestBackgroundNPCEngineIntegration(unittest.TestCase):
    """引擎集成：混编装配 / status 可见 / 零 LLM / 核心可感知。"""

    def test_engine_spawns_core_and_background(self):
        engine = make_bg_engine([old_zhang_config(), guard_wang_config()])
        self.assertEqual(set(engine.npcs), {"chen", "lily"})
        self.assertIn("old_zhang", engine.background_npcs)
        self.assertIn("guard_wang", engine.background_npcs)
        # 分层不混淆：背景 NPC 不进核心表，核心不进背景表
        self.assertNotIn("old_zhang", engine.npcs)
        self.assertNotIn("chen", engine.background_npcs)
        # 背景 NPC 实体注册进世界
        self.assertIn("old_zhang", engine.world.entities)
        self.assertIn("guard_wang", engine.world.entities)

    def test_background_visible_in_status(self):
        engine = make_bg_engine([old_zhang_config(), guard_wang_config()])
        status = engine.status()
        self.assertIn("npcs", status)
        entries = status["npcs"]
        all_ids = [n.get("id") for n in entries]
        self.assertIn("chen", all_ids)
        self.assertIn("lily", all_ids)
        # 背景 NPC 以 type=background 条目出现在 status 中
        bg = [n for n in entries if n.get("type") == "background"]
        self.assertGreaterEqual(len(bg), 2)
        bg_ids = {n["id"] for n in bg}
        self.assertIn("old_zhang", bg_ids)
        self.assertIn("guard_wang", bg_ids)

    def test_background_reactions_cost_zero_llm_calls(self):
        engine = make_bg_engine([old_zhang_config()])
        trigger_env_events(engine.world, 2)  # old_zhang 对 lily@market 事件反应 1 次
        self.assertEqual(len(npc_actions(engine.world.bus, "old_zhang")), 1)
        mock = engine.llm
        # 背景 NPC 的规则反应：零 LLM 调用
        self.assertEqual(mock.call_count, 0)
        self.assertEqual(mock.call_log, [])
        # 对照：核心 NPC 对话才走 LLM，且日志只含核心 NPC 的 persona id
        result = engine.player_says("你好呀", "lily")
        self.assertTrue(result["ok"])
        self.assertIn("lily", mock.call_log)
        self.assertNotIn("old_zhang", mock.call_log)

    def test_core_npc_perceives_background_reaction(self):
        engine = make_bg_engine([old_zhang_config()])
        trigger_env_events(engine.world, 2)  # lily@market 事件 → old_zhang 反应
        self.assertEqual(len(npc_actions(engine.world.bus, "old_zhang")), 1)
        # 同地点核心 NPC（lily@market）感知到背景 NPC 的动作并写入短期记忆
        lily = engine.npcs["lily"]
        lily_contents = " ".join(r.content for r in lily.memory.short.recent())
        self.assertIn("摇着蒲扇", lily_contents)
        # 对照：异地核心 NPC（chen@forge）听不见
        chen = engine.npcs["chen"]
        chen_contents = " ".join(r.content for r in chen.memory.short.recent())
        self.assertNotIn("摇着蒲扇", chen_contents)


if __name__ == "__main__":
    unittest.main()

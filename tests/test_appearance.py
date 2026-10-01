"""G5 验收测试：外观与环境状态扩展（appearance）。

覆盖七块契约：
1. ``Entity.appearance``：默认空 dict、实例独立；``World.snapshot`` 的
   nearby 每项新增 "appearance" 键（既有键不变，viewer 自身仍被排除，
   viewer_id=None 退化行为不变）。
2. ``World.set_appearance``：字段更新、部分更新按键合并、未知实体
   返回 False；发布 kind="appearance_change" 事件（payload 契约断言）。
3. ``MemorySystem.observe`` 模板："看到 {actor}{summary}"，importance=0.3
   低于 0.7 阈值不直写长期记忆；consolidate 排除清单含 appearance_change。
4. NPC 感知过滤（既有链路）：同地点 NPC 写短期记忆、异地 NPC 不写、
   actor==自己的换装写自己记忆。
5. ``DecisionEngine._user_prompt``：【周围的人】块仅在 nearby 非空时注入
   （token 经济性）；行渲染 f"{name}（{外观}）"，空外观仅名字无括号，
   多键按 "、" 连接且不带"变为"二字。
6. ``NPCEngine.player_changes_appearance``：引擎端到端——LLM 实际收到的
   user prompt 引用玩家外观；``status()`` 的 recent_events 含 summary。
7. ``Persona.appearance``：默认空 dict；注册进 World 后同步到
   ``Entity.appearance``（拷贝，不回写配置对象）。

G5 实现并行开发中，本文件用例允许暂时红，由 PM 统一验收。
"""

import unittest

from engine.decision import DecisionEngine
from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.memory import MemorySystem
from engine.npc import Persona
from engine.world import Entity, World, WorldEvent


# --------------------------------------------------------------------------- #
# 测试固件
# --------------------------------------------------------------------------- #

def make_engine(llm=None):
    """两 NPC 引擎：chen@forge（铁匠铺）、lily@market（集市），玩家@plaza。

    与 tests/test_event_slot.py 相同的构造方式：Mock LLM + 内联 Persona，
    显式传入 npc_configs 时不隐式注入背景 NPC，场景可控。
    """
    personas = [
        Persona(id="chen", name="铁匠陈", role="blacksmith", location_id="forge",
                personality="沉稳", speech_style="简短", backstory="三代铁匠",
                greeting_bank=["炉子热着。"], fallback_bank=["嗯，你说。"],
                sleep_mumble="（鼾声）", schedule={"20": "SLEEPING"},
                topic_responses={"剑|刀": ["打剑？拿好铁来。"]}),
        Persona(id="lily", name="商人莉莉", role="merchant", location_id="market",
                personality="热情", speech_style="快", backstory="跑过五个城镇",
                greeting_bank=["贵客来啦！"], fallback_bank=["我听着呢！"],
                sleep_mumble="（梦中谈价）", schedule={"21": "SLEEPING"},
                topic_responses={"货|买": ["新到的皮子！"]}),
    ]
    return NPCEngine(llm=llm or MockLLMProvider(), npc_configs=personas)


def _evt(kind, actor="player", **payload):
    """快捷构造 WorldEvent（tick=0）。"""
    return WorldEvent(tick=0, kind=kind, actor=actor, payload=payload)


class _PromptCaptureMock(MockLLMProvider):
    """在 MockLLMProvider 基础上捕获每次 chat 收到的 user 消息全文。

    既有 ``call_log`` 只记录 persona_id，无法断言 prompt 内容；为验证
    "对话能引用对方外观"的上下文通路，这里以子类记录 user prompt 全文
    （不改动引擎与既有测试文件，call_count / call_log 语义保持不变）。
    """

    def __init__(self, chaos_rate=0.0, seed=42):
        super().__init__(chaos_rate=chaos_rate, seed=seed)
        self.user_prompts = []

    def chat(self, messages, temperature=0.7):
        user = next((m["content"] for m in reversed(messages)
                     if m.get("role") == "user"), "")
        self.user_prompts.append(user)
        return super().chat(messages, temperature)


def appearance_events_in(bus):
    """从事件总线历史中筛出全部换装事件。"""
    return [e for e in bus.history if e.kind == "appearance_change"]


def short_contents(npc):
    """NPC 短期记忆的全部文本。"""
    return [r.content for r in npc.memory.short.recent()]


# ============================================================================ #
# 1. Entity 字段与感知快照
# ============================================================================ #

class TestEntityAppearance(unittest.TestCase):
    """Entity.appearance 字段默认值 + snapshot nearby 注入外观。"""

    def test_entity_appearance_defaults_to_empty_dict(self):
        """不传 appearance 时默认空 dict，且各实例互不共享。"""
        a = Entity(id="a", kind="npc", name="甲", location_id="plaza")
        b = Entity(id="b", kind="npc", name="乙", location_id="plaza")
        self.assertEqual(a.appearance, {})
        self.assertEqual(b.appearance, {})
        # default_factory=dict：两个实例不是同一个 dict 对象
        self.assertIsNot(a.appearance, b.appearance)

    def test_snapshot_nearby_contains_appearance(self):
        """nearby 每项新增 appearance 键：既有键不丢、内容正确、是副本。"""
        world = World()
        world.add_entity(Entity(id="player", kind="player", name="旅行者",
                                location_id="plaza",
                                appearance={"outfit": "皮围裙"}), announce=False)
        world.add_entity(Entity(id="guard", kind="npc", name="守卫",
                                location_id="plaza"), announce=False)
        snap = world.snapshot("player")
        # viewer 自身不在 nearby，只有同地点的守卫
        self.assertEqual([e["id"] for e in snap["nearby"]], ["guard"])
        entry = snap["nearby"][0]
        # 既有键不因新增 appearance 而丢失
        for key in ("id", "kind", "name", "inventory"):
            self.assertIn(key, entry)
        # 新增 appearance 键：默认空 dict 也显式存在
        self.assertIn("appearance", entry)
        self.assertEqual(entry["appearance"], {})
        # 反向视角：守卫看到玩家的外观内容
        guard_snap = world.snapshot("guard")
        p_entry = guard_snap["nearby"][0]
        self.assertEqual(p_entry["id"], "player")
        self.assertEqual(p_entry["appearance"], {"outfit": "皮围裙"})
        # 快照是副本（dict(e.appearance)）：篡改快照不影响实体真实外观
        p_entry["appearance"]["outfit"] = "被篡改"
        self.assertEqual(world.entities["player"].appearance,
                         {"outfit": "皮围裙"})

    def test_snapshot_none_viewer_sees_all(self):
        """viewer_id=None 退化：全部实体可见，appearance 键同样存在。"""
        world = World()
        world.add_entity(Entity(id="player", kind="player", name="旅行者",
                                location_id="plaza",
                                appearance={"outfit": "皮围裙"}), announce=False)
        world.add_entity(Entity(id="guard", kind="npc", name="守卫",
                                location_id="market"), announce=False)
        snap = world.snapshot(None)
        # 不同地点的实体也全部可见（退化行为不变）
        self.assertEqual({e["id"] for e in snap["nearby"]}, {"player", "guard"})
        self.assertTrue(all("appearance" in e for e in snap["nearby"]))


# ============================================================================ #
# 2. World.set_appearance 单元
# ============================================================================ #

class TestWorldSetAppearance(unittest.TestCase):
    """set_appearance：更新生效、按键合并、未知实体、事件发布契约。"""

    def _world_with_player(self):
        world = World()
        world.add_entity(Entity(id="player", kind="player", name="旅行者",
                                location_id="plaza"), announce=False)
        return world

    def test_set_appearance_updates_fields(self):
        """更新字段生效并返回 True；同键再次设置覆盖旧值。"""
        world = self._world_with_player()
        self.assertTrue(world.set_appearance("player", {"outfit": "皮围裙"}))
        self.assertEqual(world.entities["player"].appearance,
                         {"outfit": "皮围裙"})
        # 覆盖更新：同键新值替换旧值
        self.assertTrue(world.set_appearance("player", {"outfit": "节日礼服"}))
        self.assertEqual(world.entities["player"].appearance,
                         {"outfit": "节日礼服"})

    def test_set_appearance_partial_update_merges(self):
        """部分更新按键合并：先 outfit 再 posture，两个都在。"""
        world = self._world_with_player()
        world.set_appearance("player", {"outfit": "皮围裙"})
        world.set_appearance("player", {"posture": "扛着铁锤"})
        self.assertEqual(world.entities["player"].appearance,
                         {"outfit": "皮围裙", "posture": "扛着铁锤"})

    def test_set_appearance_unknown_entity_returns_false(self):
        """实体不存在返回 False，且不发布任何换装事件。"""
        world = self._world_with_player()
        self.assertFalse(world.set_appearance("nobody", {"outfit": "x"}))
        self.assertEqual(appearance_events_in(world.bus), [])
        # 既有实体不受影响
        self.assertEqual(world.entities["player"].appearance, {})

    def test_set_appearance_publishes_event(self):
        """发布 kind="appearance_change" 事件，payload 符合契约。"""
        world = self._world_with_player()
        self.assertTrue(world.set_appearance("player", {"outfit": "皮围裙"}))
        events = appearance_events_in(world.bus)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.kind, "appearance_change")
        self.assertEqual(event.actor, "player")
        # summary = "、".join("键中文名变为值")
        self.assertEqual(event.payload["summary"], "穿着变为皮围裙")
        self.assertEqual(event.payload["changes"], {"outfit": "皮围裙"})
        self.assertEqual(event.payload["location"], "plaza")
        # 多键 summary："、" 连接，按 changes 的键顺序
        self.assertTrue(world.set_appearance(
            "player", {"posture": "站立", "expression": "微笑"}))
        event2 = appearance_events_in(world.bus)[-1]
        self.assertEqual(event2.payload["summary"],
                         "姿势变为站立、神情变为微笑")
        self.assertEqual(event2.payload["changes"],
                         {"posture": "站立", "expression": "微笑"})
        # 未知键不在中英映射表内：键名原样进入 summary
        self.assertTrue(world.set_appearance("player", {"hat": "草帽"}))
        event3 = appearance_events_in(world.bus)[-1]
        self.assertIn("hat变为草帽", event3.payload["summary"])
        # changes 是发布时的拷贝（dict(changes)）：篡改 payload 不影响实体
        event2.payload["changes"]["posture"] = "篡改"
        self.assertEqual(world.entities["player"].appearance["posture"], "站立")


# ============================================================================ #
# 3. 记忆层：observe 模板、长期阈值与巩固排除
# ============================================================================ #

class TestAppearanceMemory(unittest.TestCase):
    """appearance_change 的记忆转写与感知过滤。"""

    def test_observe_template_and_importance(self):
        """模板 "看到 {actor}{summary}"，importance=0.3 不直写长期记忆。"""
        mem = MemorySystem("smith")
        mem.observe(_evt("appearance_change",
                         summary="穿着变为皮围裙",
                         changes={"outfit": "皮围裙"},
                         location="forge"))
        self.assertEqual(len(mem.short), 1)
        rec = mem.short.recent()[-1]
        # 文案含"看到"+summary
        self.assertTrue(rec.content.startswith("看到"))
        self.assertTrue(rec.content.endswith("穿着变为皮围裙"))
        self.assertIn("appearance_change", rec.tags)
        # importance 0.3 < 0.7 阈值：只写短期，不直写长期
        self.assertAlmostEqual(rec.importance, 0.3)
        self.assertEqual(mem.long.records, [])

    def test_consolidate_excludes_appearance_change(self):
        """巩固时换装事件不计入 actors 标签（排除清单含 appearance_change）。"""
        # 容量 2：两条换装记录恰好满，触发巩固
        mem = MemorySystem("smith", capacity=2)
        mem.observe(_evt("appearance_change", summary="穿着变为皮围裙",
                         changes={"outfit": "皮围裙"}, location="forge"))
        mem.observe(_evt("appearance_change", summary="姿势变为站立",
                         changes={"posture": "站立"}, location="forge"))
        record = mem.consolidate()
        self.assertIsNotNone(record)
        self.assertEqual(record.kind, "summary")
        # 若未加入排除清单，事件 kind 会作为 tag 混入 actors
        self.assertNotIn("appearance_change", record.tags)

    def test_same_location_npc_remembers(self):
        """同地点 NPC 写短期记忆（含"看到"+summary），不进长期记忆。"""
        engine = make_engine()
        engine.move_player("forge")  # 玩家走进铁匠铺
        self.assertTrue(engine.player_changes_appearance({"outfit": "皮围裙"}))
        chen = engine.npcs["chen"]
        self.assertTrue(any("看到" in c and "穿着变为皮围裙" in c
                            for c in short_contents(chen)))
        # importance 0.3 < 0.7：不沉淀长期记忆
        self.assertFalse(any("穿着变为皮围裙" in r.content
                             for r in chen.memory.long.records))

    def test_remote_npc_does_not_perceive(self):
        """异地 NPC（lily@market）听不见 forge 里发生的换装。"""
        engine = make_engine()
        engine.move_player("forge")  # 玩家在 forge 换装
        self.assertTrue(engine.player_changes_appearance({"outfit": "皮围裙"}))
        chen = engine.npcs["chen"]
        lily = engine.npcs["lily"]
        # 对照：事件确实发生且同地点的 chen 感知到了
        self.assertTrue(any("穿着变为皮围裙" in c for c in short_contents(chen)))
        self.assertFalse(any("穿着变为皮围裙" in c
                             for c in short_contents(lily)))

    def test_npc_own_change_writes_own_memory(self):
        """NPC 自己换装（actor==自己）必可闻，写自己记忆；同地点他人也感知。"""
        engine = make_engine()
        engine.spawn(Persona(id="guard", name="守卫", role="guard",
                             location_id="forge", schedule={}))
        self.assertTrue(engine.world.set_appearance("chen",
                                                    {"outfit": "打铁皮围裙"}))
        chen = engine.npcs["chen"]
        guard = engine.npcs["guard"]
        # 自己的换装写自己的记忆
        self.assertTrue(any("看到" in c and "穿着变为打铁皮围裙" in c
                            for c in short_contents(chen)))
        # 同地点的守卫同样感知
        self.assertTrue(any("穿着变为打铁皮围裙" in c
                            for c in short_contents(guard)))
        # 异地的 lily 不感知
        self.assertFalse(any("穿着变为打铁皮围裙" in c
                             for c in short_contents(engine.npcs["lily"])))
        # importance 0.3 < 0.7：不沉淀长期
        self.assertFalse(any("打铁皮围裙" in r.content
                             for r in chen.memory.long.records))


# ============================================================================ #
# 4. 决策上下文：【周围的人】块注入与渲染
# ============================================================================ #

class TestAppearanceInDecisionPrompt(unittest.TestCase):
    """_user_prompt 的外观注入：渲染格式与 token 经济性。"""

    def test_prompt_contains_nearby_block(self):
        """玩家换装后，同地点 NPC 的 prompt 注入【周围的人】与外观描述。"""
        engine = make_engine()
        engine.move_player("forge")
        engine.player_changes_appearance({"outfit": "皮围裙"})
        chen = engine.npcs["chen"]
        memory_ctx = chen.memory.context_for("你好")
        snapshot = engine.world.snapshot("chen")
        prompt = chen.decision._user_prompt("你好", engine.world, memory_ctx,
                                            snapshot, chen.inner_state)
        self.assertIn("【周围的人】", prompt)
        # 行渲染：名字（外观），外观不带"变为"二字
        self.assertIn("旅行者（穿着皮围裙）", prompt)
        # 注入位置：【当前世界】之后、【玩家说】之前
        self.assertLess(prompt.index("【当前世界】"), prompt.index("【周围的人】"))
        self.assertLess(prompt.index("【周围的人】"), prompt.index("【玩家说】"))
        # 记忆通路同样带回换装文案（短期记忆 → 【最近的经历】）
        self.assertIn("穿着变为皮围裙", prompt)

    def test_prompt_renders_bare_name_and_multiple_keys(self):
        """空外观仅名字无括号；多键 "、" 连接；未知键原样渲染。"""
        world = World()
        world.add_entity(Entity(id="viewer", kind="npc", name="观察者",
                                location_id="plaza"), announce=False)
        world.add_entity(Entity(id="guard", kind="npc", name="沉默守卫",
                                location_id="plaza"), announce=False)
        world.add_entity(Entity(id="wiz", kind="npc", name="老法师",
                                location_id="plaza",
                                appearance={"outfit": "灰色长袍",
                                            "posture": "拄杖"}), announce=False)
        world.add_entity(Entity(id="alch", kind="npc", name="炼金术士",
                                location_id="plaza",
                                appearance={"hat": "尖顶帽"}), announce=False)
        snap = world.snapshot("viewer")
        decision = DecisionEngine(
            Persona(id="viewer", name="观察者", role="watcher",
                    location_id="plaza", greeting_bank=["嗯"],
                    fallback_bank=["……"]),
            MockLLMProvider())
        prompt = decision._user_prompt("你好", world,
                                       {"long_term": [], "recent": []},
                                       snap, None)
        # 空外观：仅名字，名字后不紧跟括号
        self.assertIn("沉默守卫", prompt)
        self.assertNotIn("沉默守卫（", prompt)
        # 多键："、" 连接、无"变为"二字
        self.assertIn("老法师（穿着灰色长袍、姿势拄杖）", prompt)
        # 未知键（不在中英映射表内）：键名原样渲染
        self.assertIn("炼金术士（hat尖顶帽）", prompt)

    def test_prompt_without_nearby_has_no_block(self):
        """nearby 为空时整个块不出现（token 经济性）。"""
        engine = make_engine()  # 玩家@plaza、chen@forge、lily@market
        chen = engine.npcs["chen"]
        snapshot = engine.world.snapshot("chen")
        self.assertEqual(snapshot["nearby"], [])  # forge 只有 chen 自己
        memory_ctx = chen.memory.context_for("你好")
        prompt = chen.decision._user_prompt("你好", engine.world, memory_ctx,
                                            snapshot, chen.inner_state)
        self.assertNotIn("【周围的人】", prompt)


# ============================================================================ #
# 5. 引擎端到端：换装 → 对话上下文 → status
# ============================================================================ #

class TestEngineAppearanceEndToEnd(unittest.TestCase):
    """player_changes_appearance 的对话闭环与状态总览。"""

    def test_player_changes_appearance_end_to_end(self):
        """换装后对话：LLM 实际收到的 user prompt 引用玩家外观。"""
        mock = _PromptCaptureMock(chaos_rate=0.0, seed=42)
        engine = make_engine(llm=mock)
        engine.move_player("forge")
        self.assertTrue(engine.player_changes_appearance({"outfit": "皮围裙"}))
        result = engine.player_says("你觉得我这身怎么样", "chen")
        self.assertTrue(result["ok"])
        # LLM 确实被调用（既有断言模式）
        self.assertGreaterEqual(mock.call_count, 1)
        self.assertIn("chen", mock.call_log)
        # LLM 实际收到的 user prompt 含玩家外观描述——
        # 这证明"对话能引用对方外观"的上下文通路
        self.assertTrue(any("皮围裙" in p for p in mock.user_prompts))
        # 外观经【周围的人】块注入决策上下文
        self.assertTrue(any("【周围的人】" in p and "皮围裙" in p
                            for p in mock.user_prompts))

    def test_status_recent_events_contains_appearance(self):
        """status() 的 recent_events 出现含 summary 的换装条目。"""
        engine = make_engine()
        self.assertTrue(engine.player_changes_appearance({"outfit": "皮围裙"}))
        status = engine.status()
        matched = [e for e in status["recent_events"]
                   if e["kind"] == "appearance_change"]
        self.assertGreaterEqual(len(matched), 1)
        self.assertTrue(any("穿着变为皮围裙" in (e.get("summary") or "")
                            for e in matched))
        self.assertEqual(matched[0]["actor"], "player")


# ============================================================================ #
# 6. Persona 配置：外观的声明与同步
# ============================================================================ #

class TestPersonaAppearanceConfig(unittest.TestCase):
    """Persona.appearance 默认值与注册进 World 后的同步。"""

    def test_persona_appearance_default_and_sync(self):
        """默认空 dict；内联构造 NPC 后 Entity.appearance 与之一致（拷贝）。"""
        # 默认：不配置时外观为空 dict
        plain = Persona(id="p", name="路人", role="villager", location_id="plaza")
        self.assertEqual(plain.appearance, {})
        # 内联配置：注册进 World 后 Entity.appearance 同步
        engine = NPCEngine(llm=MockLLMProvider(), npc_configs=[Persona(
            id="smith", name="打铁匠", role="blacksmith", location_id="forge",
            greeting_bank=["嗯。"], fallback_bank=["……"],
            appearance={"outfit": "打铁皮围裙", "posture": "抡锤"})])
        entity = engine.world.entities["smith"]
        self.assertEqual(entity.appearance,
                         {"outfit": "打铁皮围裙", "posture": "抡锤"})
        # 拷贝隔离：运行期改实体外观不回写 Persona 配置
        entity.appearance["outfit"] = "被篡改"
        self.assertEqual(engine.npcs["smith"].persona.appearance["outfit"],
                         "打铁皮围裙")


if __name__ == "__main__":
    unittest.main()

"""G4 关系网测试：Relationship 数据结构 + RelationshipNetwork 全接口 + 决策链注入。

覆盖五层：
1. ``Relationship`` 数据类（字段默认值 / 显式赋值 / 位置参数顺序）；
2. ``RelationshipNetwork`` CRUD（add / query / query_by_relation / get_relation_to）；
3. 提示词渲染（有关系 / 无关系返回空串 / 敌意分级）；
4. 序列化与加载（from_file / from_list / to_dict 往返一致）；
5. 决策链集成（关系注入 system prompt / 关系影响对话 / 引擎自动加载
   configs/relationships.json）。

全部用例确定性：MockLLMProvider 单元素对话库，无随机依赖。
"""

import json
import os
import shutil
import tempfile
import unittest

from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.npc import Persona
from engine.relationships import Relationship, RelationshipNetwork


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


def father_network():
    """chen 的关系网：父亲老张（好感 0.6）。"""
    return RelationshipNetwork([
        Relationship(source_id="chen", target_id="old_zhang",
                     relation="父亲", affinity=0.6, target_name="老张"),
    ])


class TestRelationshipDataclass(unittest.TestCase):
    """Relationship 数据类基础：字段默认值与显式赋值。"""

    def test_relationship_defaults(self):
        rel = Relationship(source_id="chen", target_id="old_zhang", relation="父亲")
        self.assertEqual(rel.source_id, "chen")
        self.assertEqual(rel.target_id, "old_zhang")
        self.assertEqual(rel.relation, "父亲")
        self.assertEqual(rel.affinity, 0.0)   # 默认好感 0.0（中性）
        self.assertEqual(rel.target_name, "")  # 默认无名字

    def test_relationship_explicit_fields(self):
        rel = Relationship(source_id="chen", target_id="old_zhang",
                           relation="父亲", affinity=0.6, target_name="老张")
        self.assertEqual(rel.relation, "父亲")
        self.assertAlmostEqual(rel.affinity, 0.6)
        self.assertEqual(rel.target_name, "老张")
        # 契约字段顺序：source_id, target_id, relation, affinity, target_name
        rel2 = Relationship("lily", "chen", "老主顾", 0.4, "铁匠陈")
        self.assertEqual(rel2.source_id, "lily")
        self.assertEqual(rel2.target_id, "chen")
        self.assertEqual(rel2.relation, "老主顾")
        self.assertAlmostEqual(rel2.affinity, 0.4)
        self.assertEqual(rel2.target_name, "铁匠陈")


class TestRelationshipNetwork(unittest.TestCase):
    """RelationshipNetwork CRUD：add / query / query_by_relation / get_relation_to。"""

    def test_add_and_query(self):
        net = RelationshipNetwork()
        self.assertEqual(net.query("chen"), [])  # 空网络查询返回空列表
        net.add(Relationship("chen", "old_zhang", "父亲", 0.6, "老张"))
        net.add(Relationship("chen", "lily", "老主顾", 0.4))
        net.add(Relationship("lily", "chen", "供货商", 0.3))
        chen_rels = net.query("chen")
        self.assertEqual(len(chen_rels), 2)
        self.assertEqual({r.target_id for r in chen_rels}, {"old_zhang", "lily"})
        self.assertEqual(len(net.query("lily")), 1)
        self.assertEqual(net.query("nobody"), [])  # 未知来源返回空

    def test_query_by_relation(self):
        net = RelationshipNetwork()
        net.add(Relationship("chen", "old_zhang", "父亲", 0.6, "老张"))
        net.add(Relationship("chen", "lily", "邻居", 0.4))
        net.add(Relationship("chen", "guard_wang", "邻居", 0.35))
        fathers = net.query_by_relation("chen", "父亲")
        self.assertEqual(len(fathers), 1)
        self.assertEqual(fathers[0].target_id, "old_zhang")
        neighbors = net.query_by_relation("chen", "邻居")
        self.assertEqual({r.target_id for r in neighbors}, {"lily", "guard_wang"})
        self.assertEqual(net.query_by_relation("chen", "不存在"), [])
        # 只查自己出发的关系，不串别人的
        self.assertEqual(net.query_by_relation("lily", "邻居"), [])

    def test_get_relation_to(self):
        net = RelationshipNetwork()
        net.add(Relationship("chen", "old_zhang", "父亲", 0.6, "老张"))
        net.add(Relationship("lily", "chen", "老主顾", 0.4))
        rel = net.get_relation_to("chen", "old_zhang")
        self.assertIsNotNone(rel)
        self.assertEqual(rel.relation, "父亲")
        self.assertAlmostEqual(rel.affinity, 0.6)
        # 关系是有方向的：chen→lily 未定义，lily→chen 存在
        self.assertIsNone(net.get_relation_to("chen", "lily"))
        self.assertIsNotNone(net.get_relation_to("lily", "chen"))
        self.assertIsNone(net.get_relation_to("nobody", "chen"))


class TestPromptText(unittest.TestCase):
    """to_prompt_text：有关系渲染关键词、无关系返回空串、敌意分级。"""

    def test_prompt_text_with_relationships(self):
        net = RelationshipNetwork([
            Relationship("chen", "old_zhang", "父亲", 0.6, "老张"),
            Relationship("chen", "lily", "邻居", 0.4, "商人莉莉"),
        ])
        text = net.to_prompt_text("chen")
        self.assertIn("【人际关系】", text)
        self.assertIn("父亲", text)
        self.assertIn("邻居", text)
        self.assertIn("好感", text)  # affinity > 0.3 → 好感
        # 目标以名字或 id 至少出现其一
        self.assertTrue("老张" in text or "old_zhang" in text,
                        "关系文本应包含目标名或目标 id")

    def test_prompt_text_without_relationships(self):
        net = RelationshipNetwork()
        self.assertEqual(net.to_prompt_text("nobody"), "")  # 空网络
        net.add(Relationship("chen", "old_zhang", "父亲", 0.6, "老张"))
        # 无出边的来源：返回空字符串，不渲染段落
        self.assertEqual(net.to_prompt_text("lily"), "")

    def test_prompt_text_hostility(self):
        net = RelationshipNetwork([
            Relationship("chen", "bandit", "宿敌", -0.5, "山贼头子"),
        ])
        text = net.to_prompt_text("chen")
        self.assertIn("宿敌", text)
        self.assertIn("敌意", text)  # affinity < -0.3 → 敌意
        # 对照：中性好感（0.0）不算敌意
        neutral = RelationshipNetwork([
            Relationship("chen", "stranger", "路人", 0.0, "外乡人"),
        ])
        self.assertNotIn("敌意", neutral.to_prompt_text("chen"))


class TestRelationshipIO(unittest.TestCase):
    """序列化与加载：from_file / from_list / to_dict。"""

    def test_from_file(self):
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        path = os.path.join(tmpdir, "relationships.json")
        payload = [
            {"source_id": "chen", "target_id": "old_zhang", "relation": "父亲",
             "affinity": 0.6, "target_name": "老张"},
            {"source_id": "lily", "target_id": "chen", "relation": "老主顾",
             "affinity": 0.4, "target_name": "铁匠陈"},
        ]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        net = RelationshipNetwork.from_file(path)
        chen_rels = net.query("chen")
        self.assertEqual(len(chen_rels), 1)
        self.assertEqual(chen_rels[0].relation, "父亲")
        self.assertAlmostEqual(chen_rels[0].affinity, 0.6)
        self.assertEqual(chen_rels[0].target_name, "老张")
        self.assertEqual(len(net.query("lily")), 1)

    def test_from_list(self):
        net = RelationshipNetwork.from_list([
            {"source_id": "chen", "target_id": "old_zhang", "relation": "父亲",
             "affinity": 0.6, "target_name": "老张"},
            {"source_id": "lily", "target_id": "chen", "relation": "老主顾"},
        ])
        self.assertEqual(len(net.query("chen")), 1)
        lily_rels = net.query("lily")
        self.assertEqual(len(lily_rels), 1)
        # 缺省字段回落到数据类默认值
        self.assertEqual(lily_rels[0].relation, "老主顾")
        self.assertEqual(lily_rels[0].affinity, 0.0)
        self.assertEqual(lily_rels[0].target_name, "")

    def test_to_dict_roundtrip(self):
        net = RelationshipNetwork([
            Relationship("chen", "old_zhang", "父亲", 0.6, "老张"),
            Relationship("lily", "chen", "老主顾", 0.4),
        ])
        data = net.to_dict()
        self.assertEqual(len(data), 2)
        chen_entry = next(d for d in data if d["source_id"] == "chen")
        self.assertEqual(chen_entry["relation"], "父亲")
        self.assertAlmostEqual(chen_entry["affinity"], 0.6)
        # to_dict → from_list → to_dict 往返一致
        net2 = RelationshipNetwork.from_list(data)

        def sort_key(d):
            return (d["source_id"], d["target_id"], d["relation"])

        self.assertEqual(sorted(net2.to_dict(), key=sort_key),
                         sorted(data, key=sort_key))


class TestRelationshipInjection(unittest.TestCase):
    """决策链集成：关系注入 system prompt / 影响对话 / 引擎自动装配。"""

    def setUp(self):
        self.mock = MockLLMProvider()

    def test_relationships_injected_into_decision_prompt(self):
        engine = NPCEngine(llm=self.mock,
                           npc_configs=[chen_persona(), lily_persona()],
                           relationships=father_network())
        result = engine.player_says("最近怎么样？", "chen")
        self.assertTrue(result["ok"])
        # LLM 确实被调用（新增调用日志非空）
        self.assertGreaterEqual(self.mock.call_count, 1)
        self.assertIn("chen", self.mock.call_log)
        # chen 的决策 system prompt 包含关系上下文
        prompt = engine.npcs["chen"].decision._system_prompt()
        self.assertIn("【人际关系】", prompt)
        self.assertIn("父亲", prompt)
        self.assertIn("好感", prompt)
        self.assertTrue("老张" in prompt or "old_zhang" in prompt)
        # 对照：没有出边关系的 lily 不注入任何关系内容
        lily_prompt = engine.npcs["lily"].decision._system_prompt()
        self.assertNotIn("老张", lily_prompt)
        self.assertNotIn("old_zhang", lily_prompt)

    def test_relationship_colors_dialogue(self):
        # 话题库带"父亲"关键词：玩家问及其父时，回复带出关系人物
        chen = Persona(id="chen", name="铁匠陈", role="blacksmith", location_id="forge",
                       personality="沉稳", speech_style="简短", backstory="三代铁匠",
                       greeting_bank=["炉子热着。"], fallback_bank=["嗯，你说。"],
                       topic_responses={"父亲|爹": [
                           "我爹老张就在集市摆摊补锅，耳背，你说话大点声。"]})
        engine = NPCEngine(llm=MockLLMProvider(), npc_configs=[chen],
                           relationships=father_network())
        result = engine.player_says("你父亲最近怎么样？", "chen")
        self.assertTrue(result["ok"])
        # mock 命中"父亲"话题 → 回复包含"爹"和"老张"
        self.assertIn("爹", result["reply"])
        self.assertIn("老张", result["reply"])
        # 关系上下文同时已注入决策提示
        self.assertIn("父亲", engine.npcs["chen"].decision._system_prompt())

    def test_engine_autoloads_default_relationships(self):
        # 不传 relationships：引擎应自动加载 configs/relationships.json
        engine = NPCEngine(llm=MockLLMProvider())
        self.assertIsInstance(engine.relationships, RelationshipNetwork)
        data = engine.relationships.to_dict()
        self.assertGreater(len(data), 0,
                           "未传 relationships 时应自动加载 configs/relationships.json")
        # 加载结果可按来源查询、可渲染提示文本
        source = data[0]["source_id"]
        expected = [d for d in data if d["source_id"] == source]
        self.assertEqual(len(engine.relationships.query(source)), len(expected))
        self.assertNotEqual(engine.relationships.to_prompt_text(source), "")


if __name__ == "__main__":
    unittest.main()

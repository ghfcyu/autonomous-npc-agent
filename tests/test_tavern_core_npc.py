"""T4-b 收尾段 · tavern_sun 升级第 3 核心 NPC 验收测试。

被测契约（审查指令 2① 实施件，PM 任务书口径）：
- 酒馆掌柜孙老三（id=tavern_sun，村口酒馆公共空间枢纽）从背景 NPC
  升级为核心 NPC：默认引擎核心恰 3（chen/lily/tavern_sun）、背景恰
  22（tavern_sun 移出背景层）；
- 完整核心装配（configs/npcs/tavern_sun.json，schema 对齐
  blacksmith_chen.json）：temperament=cheerful 复用既有掩码（不新增
  掩码——共线性纪律）、tag_profile=genesis 引擎 spawn 自动挂载标签
  账本、residence=house_sun 夜间落位、tavern_owner 初始库存与快脑
  查价经营范围对齐（米酒/酱肉）；
- 花名册对齐（engine/village_genesis.VILLAGE_ROSTER）：tavern_sun
  kind=key；社会关系边（relationships.json 出边 3 条：老主顾
  doc_qin 0.7 / 供货商 fisher_zhou 0.6 / 酒友 guard_wang 0.5）、
  事件池 actor=tavern_sun 事件、花名册 id 全部不动——关系数据零改动；
- 核心能力全链路：记忆（G1 长期直写）/8D 心智（cheerful 垫话）/
  标签账本/快慢脑（查价秒回 0-token、复杂句慢脑关系注入）/作息
  （22 点归宅 SLEEPING，睡眠不放行快脑走慢脑梦呓）。

编写纪律：
- 引擎构造照既有测试惯例（默认 NPCEngine + MockLLMProvider，见
  test_village_integration.py；RecordingLLM 同款装配捕获慢脑 prompt）；
- 断言玩家可感知口径（回复文本 / prompt 行 / 实体落位 / 记忆内容），
  零第三方依赖（unittest 标准库）。
"""

import unittest

from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.states import NPCState


class RecordingLLM(MockLLMProvider):
    """记录完整 messages 的 Mock（断言慢脑 prompt 内容用，沿
    test_village_integration.py 同款装配模式）。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.captured = []

    def chat(self, messages, temperature=0.7):
        self.captured.append([dict(m) for m in messages])
        return super().chat(messages, temperature=temperature)


# cheerful 掩码语气前缀全档（filler.py / fast_brain.py 同款三档 + default）
CHEERFUL_PREFIXES = ("（热情）", "（笑盈盈）", "（勉强笑）")
# 慢脑必达句（16 字，超全部快脑句长上限闸门；含「消息/打听」命中话题库）
COMPLEX_SENTENCE = "最近村里有什么消息？我想打听打听"


class TestTavernCoreUpgrade(unittest.TestCase):
    """tavern_sun 升级核心九维验收：分层 / 账本 / 垫话 / 快脑查价 /
    快脑问候 / 记忆 / 关系注入 / 夜间落位 / SLEEPING 语义。"""

    def setUp(self):
        self.engine = NPCEngine(llm=MockLLMProvider())

    # ------------------------------------------------------------------ #
    # a. 分层口径：核心恰 3、背景恰 22
    # ------------------------------------------------------------------ #
    def test_default_engine_core3_background22(self):
        engine = self.engine
        # tavern_sun ∈ 核心表且不在背景表（升级后分层不混淆）
        self.assertIn("tavern_sun", engine.npcs)
        self.assertNotIn("tavern_sun", engine.background_npcs)
        self.assertEqual(set(engine.npcs), {"chen", "lily", "tavern_sun"})
        self.assertEqual(len(engine.npcs), 3)
        self.assertEqual(len(engine.background_npcs), 22)
        # 花名册 25 人全员在场（核心 3 + 背景 22）
        self.assertEqual(len(engine.world.entities), 26)  # 25 NPC + player

    # ------------------------------------------------------------------ #
    # b. 标签账本自动挂载（tag_profile="genesis"）
    # ------------------------------------------------------------------ #
    def test_tag_ledger_auto_mounted_with_flaw_and_secret(self):
        npc = self.engine.npcs["tavern_sun"]
        self.assertIsNotNone(npc.tag_ledger,
                              msg="tag_profile=genesis 应在 spawn 时自动挂载账本")
        flaws = [t for t in npc.tag_ledger.all_tags()
                 if t.tag_id.startswith("flaw_")]
        secrets = list(npc.tag_ledger.secrets())
        self.assertGreaterEqual(len(flaws), 1, msg="缺陷标签应 ≥1")
        self.assertGreaterEqual(len(secrets), 1, msg="绝密把柄应 ≥1")
        # 绝密把柄不进 visible_tags（决策上下文隔离纪律）
        secret_ids = {t.tag_id for t in secrets}
        visible_ids = {t.tag_id for t in npc.tag_ledger.visible_tags()}
        self.assertFalse(secret_ids & visible_ids,
                         msg="绝密把柄泄漏进玩家可见标签")

    # ------------------------------------------------------------------ #
    # c. temperament=cheerful：垫话非空（掩码 × 8D 状态带）
    # ------------------------------------------------------------------ #
    def test_cheerful_filler_nonempty(self):
        npc = self.engine.npcs["tavern_sun"]
        self.assertEqual(npc.persona.temperament, "cheerful")
        filler = npc.filler_engine.generate(npc.inner_state)
        self.assertTrue(filler, msg="cheerful 掩码垫话不应为空串")
        self.assertTrue(any(filler.startswith(p) for p in CHEERFUL_PREFIXES),
                        msg=f"垫话应带 cheerful 语气前缀，实际: {filler}")

    # ------------------------------------------------------------------ #
    # d. 快脑查价：tavern_owner 经营范围秒回，0 LLM 增量
    # ------------------------------------------------------------------ #
    def test_fast_brain_price_rice_wine_zero_llm(self):
        engine = self.engine
        self.assertEqual(engine.npcs["tavern_sun"].entity.inventory,
                         ["米酒", "酱肉"], msg="tavern_owner 初始库存应为米酒/酱肉")
        # 2026-10-16 禀赋溢价功能变更使旧断言失效（初始库存含「米酒」
        # → 问价常态回溢价句「五两」），保护语义平移为清库存场景：
        # 清出「米酒」后问价仍回标准报价句（有库存溢价态由
        # tests/test_fast_brain_endowment.py 锁「五两」）。
        engine.npcs["tavern_sun"].entity.inventory.remove("米酒")
        before = engine.llm.call_count
        result = engine.player_says("米酒多少钱", "tavern_sun")
        self.assertTrue(result["ok"])
        self.assertEqual(result["brain"], "fast")
        self.assertEqual(result["action"]["intent"], "ask_price")
        self.assertIn("米酒", result["reply"],
                      msg=f"查价回复应含商品名「米酒」，实际: {result['reply']}")
        self.assertIn("三钱一坛", result["reply"],
                      msg=f"查价回复应含米酒报价句，实际: {result['reply']}")
        self.assertEqual(engine.llm.call_count, before,
                         msg="快脑秒回不得产生 LLM 调用（0 增量）")

    # ------------------------------------------------------------------ #
    # e. 快脑问候：整句全等短语秒回，cheerful 语气前缀可见
    # ------------------------------------------------------------------ #
    def test_fast_brain_greet_cheerful_prefix(self):
        engine = self.engine
        before = engine.llm.call_count
        result = engine.player_says("你好", "tavern_sun")
        self.assertEqual(result["brain"], "fast")
        self.assertEqual(result["action"]["intent"], "greet")
        self.assertIn("孙老三", result["reply"],
                      msg=f"问候回复应自报家门，实际: {result['reply']}")
        self.assertTrue(any(result["reply"].startswith(p)
                            for p in CHEERFUL_PREFIXES),
                        msg=f"问候回复应带 cheerful 语气前缀，实际: "
                            f"{result['reply']}")
        self.assertEqual(engine.llm.call_count, before)

    # ------------------------------------------------------------------ #
    # f. 记忆行为：player_gives 赠予直写长期记忆（G1 模式）
    # ------------------------------------------------------------------ #
    def test_player_gives_writes_long_term_memory(self):
        engine = self.engine
        result = engine.player_gives("tavern_sun", "金币")
        self.assertTrue(result["ok"])
        self.assertTrue(result["from_inventory"],
                        msg="金币在玩家初始背包，应走真实库存转移路径")
        npc = engine.npcs["tavern_sun"]
        matches = [r for r in npc.memory.long.records if "金币" in r.content]
        self.assertGreaterEqual(len(matches), 1,
                                msg="赠予应沉淀为长期记忆（importance 0.8 直写）")
        self.assertGreaterEqual(matches[-1].importance, 0.7,
                                msg="赠予记忆重要度应达长期直写阈值")
        # 记忆检索命中（G1 模式：context_for 进入决策上下文）
        ctx = npc.memory.context_for("金币")
        self.assertTrue(any("金币" in c for c in ctx["long_term"]),
                        msg="context_for 检索应命中赠予长期记忆")

    # ------------------------------------------------------------------ #
    # g. 关系注入：复杂句走慢脑，system prompt 含关系行
    # ------------------------------------------------------------------ #
    def test_relationships_injected_into_slow_brain_prompt(self):
        engine = NPCEngine(llm=RecordingLLM())
        result = engine.player_says(COMPLEX_SENTENCE, "tavern_sun")
        self.assertEqual(result["brain"], "slow",
                         msg="16 字复杂句应超快脑句长闸门走慢脑")
        self.assertTrue(engine.llm.captured, msg="慢脑未被调用")
        system = engine.llm.captured[-1][0]["content"]
        self.assertIn("【人际关系】", system)
        # relationships.json 出边 3 条全进 top-3（老主顾 0.7 / 供货商
        # 0.6 / 酒友 0.5，均非亲缘保底类型按 |affinity| 降序）
        for word in ("老主顾", "供货商", "酒友"):
            self.assertIn(word, system,
                          msg=f"慢脑 prompt 应含关系行「{word}」"
                              f"（relationships.json 出边未注入）")

    # ------------------------------------------------------------------ #
    # h. 夜间落位：22 点 SLEEPING 归宅 house_sun
    # ------------------------------------------------------------------ #
    def test_night_schedule_moves_to_house_sun(self):
        engine = self.engine
        engine.world.game_minute = 21 * 60 + 50
        engine.tick(10)  # → 22:00，schedule "22": SLEEPING
        npc = engine.npcs["tavern_sun"]
        self.assertIs(npc.state_machine.state, NPCState.SLEEPING)
        self.assertEqual(npc.entity.location_id, "house_sun",
                         msg="tavern_sun 22 点后应归宅 house_sun")

    # ------------------------------------------------------------------ #
    # i. SLEEPING 语义：不放行快脑，慢脑硬规则回梦呓（0 LLM）
    # ------------------------------------------------------------------ #
    def test_sleeping_slow_brain_mumble_no_fast_path(self):
        engine = self.engine
        engine.world.game_minute = 21 * 60 + 50
        engine.tick(10)  # → 22:00 SLEEPING
        before = engine.llm.call_count
        # 用快脑可拦截句式探针：睡眠态必须不放行快脑（engine.py 既有
        # 语义），交慢脑硬规则回梦呓/拒绝
        result = engine.player_says("米酒多少钱", "tavern_sun")
        self.assertEqual(result["brain"], "slow",
                         msg="SLEEPING 的 NPC 不放行快脑，应走慢脑路径")
        self.assertEqual(engine.llm.call_count, before,
                        msg="睡眠硬规则回梦呓不应消耗 LLM")
        npc = engine.npcs["tavern_sun"]
        self.assertEqual(result["reply"], npc.persona.sleep_mumble,
                          msg=f"睡眠回复应为梦呓（sleep_mumble），实际: "
                              f"{result['reply']}")


if __name__ == "__main__":
    unittest.main()

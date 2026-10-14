"""审查指令 3① · 「孙掌柜记得你昨儿喝几坛」跨对话记忆引用测试。

被测契约（tavern_sun 升级核心 NPC 的核心卖点：跨对话记忆引用）：
- 人设 configs/npcs/tavern_sun.json：「记性好，熟客喝几坛他都记得」；
- 场景：玩家赠予米酒（player_gives → item_given 事件）→ 对话 →
  tick 推进时间（赠予成为"过往记忆"）→ 再次对话；
- 断言口径：**prompt 内容级**（第二次对话 user prompt 的
  【相关长期记忆】块出现赠予记忆模板产物），非 LLM 调用计数——
  防的是"记忆系统在但对话不用"的隐性回归（记忆已沉淀、检索已
  命中，却在决策上下文组装时被丢弃）；
- 对照组：同样两次对话但无赠予，第二次 prompt 的【相关长期记忆】
  块为（无）且整段 prompt 不含赠予模板文本——证明主链路命中来自
  赠予长期记忆而非探询句词面巧合（探询句本身含"米酒"二字，故
  对照断言必须锁定赠予模板稳定子串，而非裸"米酒"）；
- 长期沉淀：item_given importance=0.8 ≥ LONG_TERM_IMPORTANCE_
  THRESHOLD(0.7)，写入短期记忆的同时直接沉淀长期记忆（双写），
  不依赖 consolidate 的有损压缩。

player_gives → 记忆 → prompt 链路（事实核查锚点）：
1. engine/engine.py:218 player_gives 发布 item_given 事件（真实库存
   转移或宽松模式，两路都发布）；
2. engine/npc.py:155-157 点名事件无视距离 → MemorySystem.observe；
3. engine/memory.py:135 模板 "收到来自 {actor} 的物品：{item}"
   importance=0.8；memory.py:153-156 ≥0.7 双写长期；
4. engine/decision.py:150 context_for(player_input) → 长期检索
   top_k=3；decision.py:124 渲染进【相关长期记忆】块。

编写纪律：引擎构造沿 test_tavern_core_npc.py 惯例（默认 NPCEngine +
RecordingLLM 捕获慢脑完整 messages）；探询句均超快脑全部句长闸门
（≤12 字），稳定走慢脑产生 prompt；零第三方依赖（unittest 标准库）。
"""

import random
import unittest

from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.memory import LONG_TERM_IMPORTANCE_THRESHOLD


class RecordingLLM(MockLLMProvider):
    """记录完整 messages 的 Mock（慢脑 prompt 内容级断言用，沿
    test_tavern_core_npc.py 同款装配模式）。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.captured = []

    def chat(self, messages, temperature=0.7):
        self.captured.append([dict(m) for m in messages])
        return super().chat(messages, temperature=temperature)


def extract_block(prompt: str, marker: str) -> str:
    """提取 prompt 中 marker 标记块内容（marker 后到首个空行）。

    逻辑与 scripts/dialogue_quality.py extract_block 同口径（块级
    精确断言的既有模式），本文件自持一份避免跨包耦合。
    """
    if not prompt or marker not in prompt:
        return ""
    tail = prompt.split(marker, 1)[1].lstrip("\n")
    end = tail.find("\n\n")
    return tail[:end] if end != -1 else tail


# ---- 场景常量（确定性：固定文本 + 固定物品 + 固定推进节奏） ----
NPC_ID = "tavern_sun"
GIFT_ITEM = "米酒"
# 赠予记忆模板实际产物（memory.py observe item_given 模板渲染结果）
GIFT_MEMORY_TEXT = "收到来自 player 的物品：米酒"
# 第一次对话：普通寒暄（>12 字超快脑全部句长闸门，稳定走慢脑）
ASK_FIRST = "掌柜的，最近店里生意怎么样，客人多不多？"
# 第二次对话：跨对话探询昨儿喝的米酒（含"米酒"字样——对照组须靠
# 模板子串而非裸"米酒"排除词面巧合）
ASK_SECOND = "孙掌柜，昨儿我在这儿喝的那几坛米酒，你还记得吗？"
# 跨对话时间推进：3 次 tick × 60 分钟（8:00 → 11:00，仍营业时段）
TIME_ADVANCE_MINUTES = 60
TIME_ADVANCE_TICKS = 3


def _fresh_engine() -> tuple:
    """fresh 引擎 + 录制 LLM：独立世界与记忆，用例间零污染。

    random seed（PM 集成验收补位）：tick 推进时天气变化走全局
    random.random()（world.py WEATHER_CHANGE_PROB=0.08），天气事件
    写入短期记忆会挤占 RECENT_CONTEXT_WINDOW=4 窗口——不 seed 则
    单独运行绿、与其他测试组合运行时随机红（本文件用例 5 曾在
    全量组合中暴露）。seed 后全类对天气触发与否完全确定。
    """
    random.seed(20261014)
    llm = RecordingLLM()
    engine = NPCEngine(llm=llm)
    return llm, engine


def _run_two_dialogue_chain(engine, gift: bool):
    """跑完整跨对话场景链，返回两次对话结果。

    gift=True：对话前先给玩家背包放一瓶米酒并 player_gives 赠予
    （真实库存转移路径）；gift=False：对照组，同样两次对话同样
    文本同样时间推进，唯一差异是无赠予。
    """
    if gift:
        engine.world.entities["player"].inventory.append(GIFT_ITEM)
        engine.player_gives(NPC_ID, GIFT_ITEM)
    first = engine.player_says(ASK_FIRST, NPC_ID)
    for _ in range(TIME_ADVANCE_TICKS):
        engine.tick(TIME_ADVANCE_MINUTES)
    second = engine.player_says(ASK_SECOND, NPC_ID)
    return first, second


class TestTavernCrossDialogueMemory(unittest.TestCase):
    """「孙掌柜记得你昨儿喝几坛」：跨对话记忆引用（prompt 内容级）。"""

    # ------------------------------------------------------------------ #
    # 1. 主链路：赠予 → 对话 → 时间推进 → 再对话，第二次 prompt 引用记忆
    # ------------------------------------------------------------------ #
    def test_second_dialogue_prompt_references_gift_memory(self):
        """跨对话主链路：第二次对话的【相关长期记忆】块出现赠予模板
        产物——记忆穿过时间推进与中间对话仍送达决策点。"""
        llm, engine = _fresh_engine()
        first, second = _run_two_dialogue_chain(engine, gift=True)

        # 两段对话均走慢脑（prompt 存在性前置，非计数口径）
        self.assertEqual(second["brain"], "slow",
                         msg="探询句超快脑句长闸门，应走慢脑产生 prompt")
        self.assertGreaterEqual(len(llm.captured), 2,
                                msg="慢脑未产生可断言的 prompt")

        prompt2 = llm.captured[-1][1]["content"]
        block = extract_block(prompt2, "【相关长期记忆】")
        self.assertIn(GIFT_MEMORY_TEXT, block,
                      msg=f"第二次对话【相关长期记忆】块应含赠予记忆"
                          f"「{GIFT_MEMORY_TEXT}」，实际块内容: {block!r}")

        # 第一次对话（赠予后立即）的 prompt 同样已引用——即时注入
        prompt1 = llm.captured[0][1]["content"]
        block1 = extract_block(prompt1, "【相关长期记忆】")
        self.assertIn(GIFT_MEMORY_TEXT, block1,
                      msg=f"第一次对话【相关长期记忆】块应已含赠予记忆"
                          f"（直写后即时可检索），实际块内容: {block1!r}")

    # ------------------------------------------------------------------ #
    # 2. 对照组：无赠予，同样两次对话，第二次 prompt 无该记忆引用
    # ------------------------------------------------------------------ #
    def test_control_without_gift_no_memory_reference(self):
        """对照组：唯一差异是无赠予 → 第二次 prompt【相关长期记忆】为
        （无）且整段 prompt 不含赠予模板——证明主链路命中来自赠予
        长期记忆而非探询句词面巧合（探询句本身含"米酒"）。"""
        llm, engine = _fresh_engine()
        first, second = _run_two_dialogue_chain(engine, gift=False)

        self.assertEqual(second["brain"], "slow",
                         msg="对照组同样走慢脑，保证 prompt 可比")
        prompt2 = llm.captured[-1][1]["content"]

        # 【相关长期记忆】块为（无）：无赠予 → 长期记忆空 → 检索空
        block = extract_block(prompt2, "【相关长期记忆】")
        self.assertEqual(block, "（无）",
                         msg=f"对照组【相关长期记忆】块应为（无），"
                             f"实际: {block!r}")
        # 整段 user prompt 不含赠予模板稳定子串（玩家话语中的
        # "米酒"只应出现在【玩家说】/【最近的经历】，不构成记忆引用）
        self.assertNotIn("收到来自 player 的物品", prompt2,
                         msg="对照组 prompt 不应出现赠予记忆模板文本")
        # 排除假阴性：对照组记忆系统与慢脑链路真实在工作
        # （【最近的经历】块含说话条目，非（无））
        recent = extract_block(prompt2, "【最近的经历】")
        self.assertIn("玩家说：", recent,
                      msg=f"对照组【最近的经历】块应有说话短期记忆"
                          f"（链路活跃性前置），实际: {recent!r}")
        self.assertEqual(
            len(engine.npcs[NPC_ID].memory.long.records), 0,
            msg="对照组 tavern_sun 长期记忆应为空（无 ≥0.7 事件）")

    # ------------------------------------------------------------------ #
    # 3. 长期沉淀：importance 0.8 ≥ 阈值 0.7 双写，低价值事件不沉淀
    # ------------------------------------------------------------------ #
    def test_gift_sinks_to_long_term_at_threshold(self):
        """player_gives 赠予直写长期记忆（importance=0.8 ≥ 0.7 阈值
        双写短期+长期）；说话事件（0.6 < 阈值）不沉淀——阈值语义。"""
        llm, engine = _fresh_engine()
        engine.world.entities["player"].inventory.append(GIFT_ITEM)
        result = engine.player_gives(NPC_ID, GIFT_ITEM)

        # 真实库存转移路径（玩家背包确有米酒）
        self.assertTrue(result["ok"])
        self.assertTrue(result["from_inventory"],
                        msg="玩家背包有米酒，应走真实库存转移路径")
        self.assertIn(GIFT_ITEM, engine.npcs[NPC_ID].entity.inventory,
                      msg="赠予后米酒应落入 tavern_sun 库存")

        npc = engine.npcs[NPC_ID]
        # 长期记忆恰 1 条：模板产物 + importance 0.8 + interaction 类
        self.assertEqual(len(npc.memory.long.records), 1,
                         msg="赠予应直写恰 1 条长期记忆")
        record = npc.memory.long.records[0]
        self.assertEqual(record.content, GIFT_MEMORY_TEXT,
                         msg=f"长期记忆内容应为赠予模板产物，"
                             f"实际: {record.content!r}")
        self.assertGreaterEqual(record.importance,
                                LONG_TERM_IMPORTANCE_THRESHOLD,
                         msg=f"赠予 importance={record.importance} 应 ≥ "
                             f"长期直写阈值 {LONG_TERM_IMPORTANCE_THRESHOLD}")
        self.assertEqual(record.importance, 0.8)
        self.assertEqual(record.kind, "interaction")
        self.assertIn("item_given", record.tags)

        # 双写：短期记忆同文本（当前心绪通道同步可见）
        self.assertTrue(any(GIFT_MEMORY_TEXT in r.content
                            for r in npc.memory.short.recent()),
                        msg="赠予应同时写入短期记忆（双写）")

        # 阈值语义对照：说话事件 importance=0.6 < 0.7，不沉淀长期
        engine.player_says(ASK_FIRST, NPC_ID)
        long_contents = [r.content for r in npc.memory.long.records]
        self.assertEqual(len(long_contents), 1,
                         msg=f"说话（0.6 < 阈值）不应沉淀长期记忆，"
                             f"实际长期记忆: {long_contents}")
        self.assertEqual(long_contents[0], GIFT_MEMORY_TEXT,
                         msg="对话后长期记忆仍应只有赠予那一条")

    # ------------------------------------------------------------------ #
    # 4. 时间推进后召回：纯时间流逝（无中间对话）不丢长期记忆
    # ------------------------------------------------------------------ #
    def test_memory_survives_time_advance_and_recalled(self):
        """赠予 → tick×6（8:00→14:00 共 6 小时）→ 直接对话：长期记忆
        不随时间丢失，prompt 仍引用——"过往记忆"召回稳定性。"""
        llm, engine = _fresh_engine()
        engine.world.entities["player"].inventory.append(GIFT_ITEM)
        engine.player_gives(NPC_ID, GIFT_ITEM)

        for _ in range(6):
            engine.tick(TIME_ADVANCE_MINUTES)
        # 6 小时后：非睡眠时段、远离硬约束拒绝带（走慢脑即未被拦截）
        self.assertEqual(engine.world.clock, "14:00")
        npc = engine.npcs[NPC_ID]
        self.assertTrue(any(r.content == GIFT_MEMORY_TEXT
                            for r in npc.memory.long.records),
                        msg="时间推进不应丢失赠予长期记忆")

        result = engine.player_says(ASK_SECOND, NPC_ID)
        self.assertEqual(result["brain"], "slow",
                         msg="6 小时后营业时段对话应走慢脑（未被硬约束/"
                             "睡眠拦截，prompt 存在）")
        prompt = llm.captured[-1][1]["content"]
        block = extract_block(prompt, "【相关长期记忆】")
        self.assertIn(GIFT_MEMORY_TEXT, block,
                      msg=f"纯时间推进 6 小时后的对话 prompt 仍应引用"
                          f"赠予记忆，实际块内容: {block!r}")

    # ------------------------------------------------------------------ #
    # 5. 短期通道强化：主链路第二次 prompt【最近的经历】块同样携带
    # ------------------------------------------------------------------ #
    def test_recent_experiences_block_also_carries_gift(self):
        """短期通道携带：赠予后立即对话，【最近的经历】块与赠予
        记忆同块共存——"昨儿的米酒"经短期+长期双通道送达决策点。

        确定性修正（PM 集成验收补位）：原版复用 tick×3 跨对话链断言
        第二次对话的短期块，但 RECENT_CONTEXT_WINDOW=4 会被 tick 期
        间的天气事件（WEATHER_CHANGE_PROB=0.08 随机触发）与后续说话
        条目挤占，赠予恰为窗口第 5 条被挤出——单测绿/全量组合红。
        改为零 tick 场景：赠予后立即对话，窗口内容完全确定
        （赠予/玩家说/说了话，≤4 条），断言语义（短期通道携带
        赠予+与说话条目同块共存）不变。
        """
        llm, engine = _fresh_engine()
        engine.world.entities["player"].inventory.append(GIFT_ITEM)
        engine.player_gives(NPC_ID, GIFT_ITEM)

        result = engine.player_says(ASK_FIRST, NPC_ID)
        self.assertEqual(result["brain"], "slow",
                         msg="寒暄句超快脑句长闸门，应走慢脑产生 prompt")
        prompt = llm.captured[-1][1]["content"]
        recent = extract_block(prompt, "【最近的经历】")
        self.assertIn(GIFT_MEMORY_TEXT, recent,
                      msg=f"赠予后立即对话，【最近的经历】块（短期通道）"
                          f"应含赠予记忆「{GIFT_MEMORY_TEXT}」，"
                          f"实际块内容: {recent!r}")
        # 结构完整性：说话条目与赠予条目同块共存
        self.assertIn(f"玩家说：{ASK_FIRST}", recent)


if __name__ == "__main__":
    unittest.main()

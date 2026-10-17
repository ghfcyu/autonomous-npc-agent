"""T6 第二小步 · 勒索最小闭环验收测试（engine/blackmail.py + 全链接线）。

被测契约（实现已冻结；本文件只测不改）：
1. 知情登记：IntoxicationLedger._settle 第 5 步醉话半泄露——玩家同地
   在场 + victim 有 INNATE 把柄 → 发布 secret_mumbled（文案按把柄
   tag_id 查 DRUNK_LEAKS 池）；BlackmailLedger 订阅登记
   player_knowledge（知情即筹码，纯数据层 0-token）；玩家不同地/
   无把柄/tag_id 不在池 → 零行为（memory 无该 kind 模板，NPC 零记忆
   写入——泄底防护：唯一感知者是事件流中的玩家）；
2. 守卫链（顺序即语义）：unknown npc → 无知情 no_leverage → 知情但
   无账本把柄 no_leverage → 余波期 already_leveraged（知情保留不清
   除，余波 24h 熄灭后可再勒索）；
3. 结算四件套 + 双事件：封口物=估值最高者手动转移（沿 player_buys
   防双事件先例，绝不走 transfer_item）/apply_loss 估值联动（损失域
   基准粘性）/敌意边 0.5→-0.5 落入既有敌意渲染带/余波 secret 瞬态
   标签 TTL 24（不进 visible_tags——隐秘心理走显式注入通道）+
   blackmail 点名事件 + npc_action 成交动作；
4. 玩家可感知出口：慢脑 user prompt 【把柄】行（leverage 空时逐字节
   不变——基线不变性）/ 垫话敌意前缀（HOSTILE_PREFIXES 池复用冷哼
   轮转，drunk > hostile > 脾气常态）/ 未勒索 NPC 全 prompt 零把柄
   内容（泄底防护内容级断言）；
5. 架构边界：blackmail 源码零 inner_state 引用（ast 断言，沿
   tests/test_economics.py 先例）；blackmail 事件 8D 零增量（六情
   边界）；勒索全程 LLM call_count==0。

编写纪律：零第三方依赖（unittest 标准库）；引擎构造沿
tests/test_intoxication.py 的 RecordingLLM + random.seed 双保险模式；
烂醉碗数一律经 generate_village() 真实档案 + bowls_needed() 计算
（真实数据驱动，严禁手写 tol 数字）；行为断言一律用前后差值/集合
成员/内容包含口径，W₀ 等前置数字仅作场景锚。
"""

import ast
import random
import unittest

from engine.background_npc import HOSTILE_PREFIXES
from engine.blackmail import (ALREADY_LEVERAGED_REPLY, BLACKMAIL_ACTION_EMPTY,
                              BLACKMAIL_ACTION_WITH, BLACKMAIL_REPLY_EMPTY,
                              BLACKMAIL_REPLY_WITH, LEVERAGED_LABEL,
                              LEVERAGED_TAG_ID, LEVERAGED_TTL_HOURS,
                              LEVERAGE_SUFFIX, NO_LEVERAGE_REPLY,
                              BlackmailLedger, is_leveraged, leverage_line)
from engine.economics import WealthLedger, estimate_value
from engine.engine import NPCEngine
from engine.filler import FillerEngine
from engine.inner_state import InnerState
from engine.intoxication import DRUNK_FILLERS, DRUNK_LEAKS, IntoxicationLedger
from engine.intoxication import is_dead_drunk
from engine.llm.mock import MockLLMProvider
from engine.memory import LONG_TERM_IMPORTANCE_THRESHOLD
from engine.npc import NPC, Persona
from engine.relationships import RelationshipNetwork
from engine.tag_mount import SECRET_POOL, TagLedger, TagPhase
from engine.village_genesis import generate_village
from engine.world import Entity, World, WorldEvent

# 19 字慢脑句（tavern_sun 场景，沿 tests/test_intoxication 的 TAVERN_ASK 同款）。
TAVERN_ASK = "掌柜的，最近店里生意怎么样，客人多不多？"


class RecordingLLM(MockLLMProvider):
    """记录完整 messages 的 Mock（prompt 内容级断言用，沿
    tests/test_intoxication.py 同款装配模式；本文件自持一份，
    不改既有测试文件）。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.captured = []

    def chat(self, messages, temperature=0.7):
        self.captured.append([dict(m) for m in messages])
        return super().chat(messages, temperature=temperature)


def _fresh_engine(seed: int = 20261017):
    """fresh 引擎 + 录制 LLM：独立世界与账本，用例间零污染。

    random.seed 双保险（沿 tests/test_intoxication.py 模式）：tick 推进
    时天气变化走全局 random，seed 固定保证用例完全确定。
    """
    random.seed(seed)
    llm = RecordingLLM()
    engine = NPCEngine(llm=llm)
    return llm, engine


def _needed(npc_id: str) -> int:
    """真实数据驱动烂醉阈值：generate_village() 创生档案 → bowls_needed()
    （严禁手写 tol 数字——酒量分叉由属性驱动）。"""
    from engine.intoxication import bowls_needed
    return bowls_needed(generate_village()[npc_id].innate.alcohol_tol)


def _give_bowls(engine, npc_id: str, n: int) -> None:
    """宽松模式赠饮 n 碗（player_gives 玩家无该物品也发布 item_given）。"""
    for _ in range(n):
        engine.player_gives(npc_id, "米酒")


def _make_knowledgeable(engine) -> None:
    """端到端知情构造：玩家移步酒馆 → 赠酒满轮烂醉 → 醉话半泄露
    （secret_mumbled）→ BlackmailLedger 登记 player_knowledge。"""
    engine.move_player("tavern")
    _give_bowls(engine, "tavern_sun", _needed("tavern_sun"))


def _leak_events(engine):
    """事件历史中的 secret_mumbled 醉话半泄露事件列表。"""
    return [e for e in engine.world.bus.history if e.kind == "secret_mumbled"]


def _innate_secret(npc):
    """取该 NPC 的首条 INNATE 把柄（真实挂载产物，动态断言口径）。"""
    return [s for s in npc.tag_ledger.secrets()
            if s.phase == TagPhase.INNATE][0]


def _ledgerless_persona() -> Persona:
    """无 tag_profile 的自定义人格（spawn 不挂账本，守卫链 3 半分支用）。"""
    return Persona(id="wanderer", name="流浪客", role="villager",
                   location_id="plaza", greeting_bank=["……"],
                   fallback_bank=["……"])


# ============================================================================ #
# 1. 知情登记（secret_mumbled → player_knowledge）
# ============================================================================ #

class TestKnowledgeRegistry(unittest.TestCase):
    """醉话半泄露 → 知情登记：同地判定在发布方，订阅侧只认核心注册表。"""

    def test_same_location_drunk_leak_registers_knowledge(self):
        """①端到端：玩家移步酒馆 + 赠酒满轮 → secret_mumbled 恰一条
        （payload npc/actor==victim、text==DRUNK_LEAKS[把柄 tag_id]）→
        player_knowledge 登记该 NPC；victim 短期记忆零醉话写入
        （memory 无该 kind 模板——泄底防护：只有玩家听见）。"""
        llm, engine = _fresh_engine()
        _make_knowledgeable(engine)
        sun = engine.npcs["tavern_sun"]
        self.assertTrue(is_dead_drunk(sun))  # 前置：醉话只在烂醉结算时泄露
        leaks = _leak_events(engine)
        self.assertEqual(len(leaks), 1)
        self.assertEqual(leaks[0].kind, "secret_mumbled")
        self.assertEqual(leaks[0].actor, "tavern_sun")
        self.assertEqual(leaks[0].payload["npc"], "tavern_sun")
        tag_id = _innate_secret(sun).tag_id
        self.assertIn(tag_id, DRUNK_LEAKS)  # 前置：SECRET_POOL 五条全覆盖
        self.assertEqual(leaks[0].payload["text"], DRUNK_LEAKS[tag_id])
        self.assertIn("tavern_sun", engine.blackmail_ledger.player_knowledge)
        # 泄底防护：victim 与同地 NPC 的记忆都不得出现醉话内容
        self.assertFalse(any(DRUNK_LEAKS[tag_id] in r.content
                             for r in sun.memory.short.recent()))
        self.assertFalse(any(DRUNK_LEAKS[tag_id] in r.content
                             for r in sun.memory.long.records))

    def test_on_event_ignores_unknown_npc(self):
        """②非核心 NPC 不登记：直发 payload["npc"] 为背景 NPC（doc_qin
        有创生档案但不在 engine.npcs）与无名 id 的 secret_mumbled →
        player_knowledge 恒空；对照 npc=核心 id 即登记（过滤而非全禁）。"""
        _llm, engine = _fresh_engine()
        for bad in ("doc_qin", "nobody"):
            engine.world.bus.publish(WorldEvent(
                0, "secret_mumbled", "player", {"npc": bad, "text": "醉话"}))
        self.assertEqual(engine.blackmail_ledger.player_knowledge, set())
        engine.world.bus.publish(WorldEvent(
            0, "secret_mumbled", "player", {"npc": "tavern_sun", "text": "醉话"}))
        self.assertEqual(engine.blackmail_ledger.player_knowledge,
                         {"tavern_sun"})

    def test_on_event_ignores_other_kinds(self):
        """③kind 非 secret_mumbled 一律忽略（引擎按 kinds 订阅，on_event
        守卫双保险——带 npc 键的其他事件也不得误登记）。"""
        ledger = BlackmailLedger(World(), {}, WealthLedger(),
                                 RelationshipNetwork())
        for kind in ("item_given", "player_spoke", "npc_drunk",
                     "npc_action", "time_passed"):
            ledger.on_event(WorldEvent(0, kind, "player",
                                       {"npc": "chen", "text": "醉话"}))
        self.assertEqual(ledger.player_knowledge, set())


# ============================================================================ #
# 2. 守卫链（顺序即语义，4 条全拒）
# ============================================================================ #

class TestBlackmailGuardChain(unittest.TestCase):
    """守卫链：unknown / 无知情 / 知情无把柄 / 余波期——全部确定性回绝。"""

    def test_unknown_npc_rejected(self):
        """①unknown npc：无名 id 与背景 NPC id（baker_liu 在
        background_npcs 不在 npcs）一律拒绝（勒索域只进核心 NPC）。"""
        _llm, engine = _fresh_engine()
        self.assertIn("baker_liu", engine.background_npcs)
        self.assertNotIn("baker_liu", engine.npcs)
        self.assertEqual(engine.player_blackmails("nobody"),
                         {"ok": False, "reason": "unknown npc"})
        self.assertEqual(engine.player_blackmails("baker_liu"),
                         {"ok": False, "reason": "unknown npc"})

    def test_no_knowledge_rejected(self):
        """②无知情 no_leverage：默认引擎（玩家未听过醉话）→ 契约回绝句
        逐字；零 LLM、零事件、零库存变动（拒绝路径零副作用）。"""
        llm, engine = _fresh_engine()
        history_before = len(engine.world.bus.history)
        inventory_before = list(engine.world.entities["tavern_sun"].inventory)
        self.assertEqual(engine.player_blackmails("tavern_sun"),
                         {"ok": False, "reason": "no_leverage",
                          "reply": NO_LEVERAGE_REPLY})
        self.assertEqual(engine.blackmail_ledger.player_knowledge, set())
        self.assertEqual(engine.llm.call_count, 0)
        self.assertEqual(len(engine.world.bus.history), history_before)
        self.assertEqual(engine.world.entities["tavern_sun"].inventory,
                         inventory_before)

    def test_knowledge_without_secret_ledger_rejected(self):
        """③知情但无账本把柄 no_leverage：手动登记知情（模拟听到了
        醉话）的无账本 NPC 仍回绝——知情不等于可勒索，还要真有把柄
        （unmount 剥夺场景双保险的账本缺失半分支）。"""
        engine = NPCEngine(llm=MockLLMProvider(),
                           npc_configs=[_ledgerless_persona()])
        self.assertIsNone(engine.npcs["wanderer"].tag_ledger)
        engine.blackmail_ledger.player_knowledge.add("wanderer")
        self.assertEqual(engine.player_blackmails("wanderer"),
                         {"ok": False, "reason": "no_leverage",
                          "reply": NO_LEVERAGE_REPLY})

    def test_already_leveraged_rejected(self):
        """④余波期重复勒索 already_leveraged：勒索得手后 24h 内再勒索
        确定性回绝（回绝句逐字）；知情保留不清除；拒绝零事件零 LLM
        零库存变动——玩家不得重复榨取。"""
        llm, engine = _fresh_engine()
        _make_knowledgeable(engine)
        self.assertTrue(engine.player_blackmails("tavern_sun")["ok"])
        history_before = len(engine.world.bus.history)
        calls_before = engine.llm.call_count
        inventory_before = list(engine.world.entities["tavern_sun"].inventory)
        self.assertEqual(engine.player_blackmails("tavern_sun"),
                         {"ok": False, "reason": "already_leveraged",
                          "reply": ALREADY_LEVERAGED_REPLY})
        self.assertIn("tavern_sun",
                      engine.blackmail_ledger.player_knowledge,
                      msg="余波期回绝须保留知情（熄灭后可再勒索）")
        self.assertEqual(engine.llm.call_count, calls_before)
        self.assertEqual(len(engine.world.bus.history), history_before)
        self.assertEqual(engine.world.entities["tavern_sun"].inventory,
                         inventory_before)


# ============================================================================ #
# 3. 结算四件套 + 双事件
# ============================================================================ #

class TestBlackmailSettlement(unittest.TestCase):
    """勒索成交：封口物手动转移 / apply_loss 联动 / 敌意边 / 余波标签
    + blackmail/npc_action 双事件契约。"""

    def test_hush_money_highest_value_and_manual_transfer(self):
        """①封口物=估值最高（米酒 2 > 酱肉 1 → 取米酒）：NPC 库存少一件、
        玩家背包多一件（手动转移，不经 transfer_item——item_given 零新增，
        防双事件双记账）；成交回复 BLACKMAIL_REPLY_WITH 逐字。"""
        _llm, engine = _fresh_engine()
        _make_knowledgeable(engine)
        given_before = sum(1 for e in engine.world.bus.history
                           if e.kind == "item_given")
        player_before = list(engine.world.entities["player"].inventory)
        result = engine.player_blackmails("tavern_sun")
        self.assertTrue(result["ok"])
        self.assertEqual(result["hush_money"], "米酒")
        self.assertEqual(engine.world.entities["tavern_sun"].inventory,
                         ["酱肉"], msg="估值最高者被夺走，低值留下")
        self.assertIn("米酒", engine.world.entities["player"].inventory)
        self.assertEqual(len(engine.world.entities["player"].inventory),
                         len(player_before) + 1)
        self.assertEqual(result["reply"],
                         BLACKMAIL_REPLY_WITH.format(item="米酒"))
        given_after = sum(1 for e in engine.world.bus.history
                          if e.kind == "item_given")
        self.assertEqual(given_after, given_before,
                         msg="手动转移不得触发 item_given（双事件双记账）")

    def test_apply_loss_links_wealth_baseline_sticky(self):
        """②apply_loss 估值联动：封口物被夺是真实损失——W 恰减
        estimate_value("米酒")=2、基准 R 完全不动（损失域零吸收，与
        交易损失同口径——经济心理闭环）。注：account() 返回可变
        账目引用，前后值须先取标量快照（沿 test_trade 差值口径）。"""
        _llm, engine = _fresh_engine()
        _make_knowledgeable(engine)
        acc = engine.wealth_ledger.account("tavern_sun")
        before_wealth, before_baseline = acc.wealth, acc.baseline
        engine.player_blackmails("tavern_sun")
        after = engine.wealth_ledger.account("tavern_sun")
        self.assertEqual(after.wealth,
                         before_wealth - estimate_value("米酒"))
        self.assertEqual(after.baseline, before_baseline,
                         msg="损失域基准粘性：R 不为封口物损失留缓冲")

    def test_affinity_drops_into_hostile_band(self):
        """③敌意边 -1.0：update_affinity 后恰减 1.0（clamp 前后差值断
        口——端到端赠酒先行推高了好感，六情通道是独立既有行为）；
        单元级：默认客人 0.5 → -0.5 落入 to_prompt_text 既有敌意渲染
        带 <-0.3（零改动直接渲染"敌意 0.5"）；返回 dict 的 affinity
        字段同现值。"""
        from engine.relationships import Relationship
        # 单元级：默认客人边 0.5 → 勤索 -1.0 → -0.5（敌意渲染带内）
        net = RelationshipNetwork()
        net.add(Relationship(source_id="tavern_sun", target_id="player",
                             relation="客人", affinity=0.5))
        net.update_affinity("tavern_sun", "player", -1.0)
        self.assertEqual(net.get_relation_to("tavern_sun", "player").affinity,
                         -0.5)
        self.assertIn("敌意 0.5", net.to_prompt_text("tavern_sun"))
        # 端到端：恰减 1.0、方向必为敌意（< 0）
        _llm, engine = _fresh_engine()
        _make_knowledgeable(engine)
        before = engine.relationships.get_relation_to(
            "tavern_sun", "player").affinity
        result = engine.player_blackmails("tavern_sun")
        expected = max(-1.0, min(1.0, before - 1.0))
        self.assertEqual(result["affinity"], expected)
        rel = engine.relationships.get_relation_to("tavern_sun", "player")
        self.assertEqual(rel.affinity, expected)
        self.assertLess(rel.affinity, 0,
                        msg="勒索后关系必为敌意方向（一次勒索终身记仇）")

    def test_leveraged_tag_mounted_secret_transient(self):
        """④余波标签挂载：all_tags 含 state_leveraged（TRANSIENT、
        secret=True、TTL 24）；visible_tags 不含——隐秘心理不进身份
        标签行自动管道，唯一 prompt 出口是 leverage_line 显式注入
        （与烂醉公开体态两标签两通道各归其位）。"""
        _llm, engine = _fresh_engine()
        _make_knowledgeable(engine)
        engine.player_blackmails("tavern_sun")
        ledger = engine.npcs["tavern_sun"].tag_ledger
        mounted = [t for t in ledger.all_tags()
                   if t.tag_id == LEVERAGED_TAG_ID]
        self.assertEqual(len(mounted), 1)
        self.assertEqual(mounted[0].label, LEVERAGED_LABEL)
        self.assertEqual(mounted[0].phase, TagPhase.TRANSIENT)
        self.assertTrue(mounted[0].secret,
                        msg="余波标签必须 secret=True（不进 visible_tags）")
        self.assertNotIn(LEVERAGED_TAG_ID,
                         [t.tag_id for t in ledger.visible_tags()])
        self.assertEqual(ledger._ttl[LEVERAGED_TAG_ID],
                         LEVERAGED_TTL_HOURS)  # 沿既有测试触内部口径

    def test_empty_inventory_settlement(self):
        """⑤空库存结算：hush_money=None（诚实登记不发明数字）、
        apply_loss 未调用（W/R 双不变）、回复与双事件均走空口袋分支
        （BLACKMAIL_REPLY_EMPTY / 掏空口袋求饶 / 缩着脖子冷汗直流）。"""
        llm, engine = _fresh_engine()
        _make_knowledgeable(engine)
        engine.world.entities["tavern_sun"].inventory.clear()
        acc = engine.wealth_ledger.account("tavern_sun")
        before_wealth, before_baseline = acc.wealth, acc.baseline
        player_before = list(engine.world.entities["player"].inventory)
        result = engine.player_blackmails("tavern_sun")
        self.assertTrue(result["ok"])
        self.assertIsNone(result["hush_money"])
        self.assertEqual(result["reply"], BLACKMAIL_REPLY_EMPTY)
        after = engine.wealth_ledger.account("tavern_sun")
        self.assertEqual((after.wealth, after.baseline),
                         (before_wealth, before_baseline),
                         msg="空口袋无账面损失：apply_loss 未调用")
        self.assertEqual(engine.world.entities["player"].inventory,
                         player_before, msg="玩家不得从空口袋拿走东西")
        bm = [e for e in engine.world.bus.history if e.kind == "blackmail"][0]
        name = engine.npcs["tavern_sun"].persona.name
        player = engine.world.entities["player"].name
        self.assertEqual(bm.payload["summary"],
                         f"{player}拿住了{name}的把柄，{name}掏空了口袋求饶")
        actions = [e for e in engine.world.bus.history
                   if e.kind == "npc_action" and e.actor == "tavern_sun"]
        self.assertEqual(actions[-1].payload["summary"], BLACKMAIL_ACTION_EMPTY)

    def test_blackmail_event_payload_contract(self):
        """⑥blackmail 点名事件契约：恰一条、actor="player"、payload
        victim/summary/hush_money 齐全——summary 第三人称中立（victim
        与目击者双路径同文案），玩家名与 NPC 名动态拼接。"""
        _llm, engine = _fresh_engine()
        _make_knowledgeable(engine)
        engine.player_blackmails("tavern_sun")
        events = [e for e in engine.world.bus.history
                  if e.kind == "blackmail"]
        self.assertEqual(len(events), 1)
        bm = events[0]
        self.assertEqual(bm.actor, "player")
        self.assertEqual(bm.payload["victim"], "tavern_sun")
        self.assertEqual(bm.payload["hush_money"], "米酒")
        name = engine.npcs["tavern_sun"].persona.name
        player = engine.world.entities["player"].name
        self.assertEqual(bm.payload["summary"],
                         f"{player}拿住了{name}的把柄，"
                         f"{name}交出了米酒当封口物")

    def test_npc_action_event_published(self):
        """⑦成交动作事件：victim 发布 npc_action（actor/payload npc 均
        指向自己），summary==BLACKMAIL_ACTION_WITH 逐字（脸色煞白咬牙
        交出封口物——玩家经事件流可感知）。"""
        _llm, engine = _fresh_engine()
        _make_knowledgeable(engine)
        engine.player_blackmails("tavern_sun")
        actions = [e for e in engine.world.bus.history
                   if e.kind == "npc_action" and e.actor == "tavern_sun"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].payload["npc"], "tavern_sun")
        self.assertEqual(actions[0].payload["summary"],
                         BLACKMAIL_ACTION_WITH.format(item="米酒"))

    def test_victim_and_witness_memory(self):
        """⑧记忆双路径：victim 经 victim 键点名分支感知（0.9 ≥ 0.7
        长期直写——被勒索终身记得谁拿住过自己）；同地目击者 chen 经
        同地过滤同模板长期直写（村里大事瞒不过掌柜）；异地 lily 不含。"""
        _llm, engine = _fresh_engine()
        engine.world.move_entity("chen", "tavern")  # 目击者先行就位
        _make_knowledgeable(engine)
        engine.player_blackmails("tavern_sun")
        bm = [e for e in engine.world.bus.history
              if e.kind == "blackmail"][0]
        summary = bm.payload["summary"]
        matches = [r for r in
                   engine.npcs["tavern_sun"].memory.long.records
                   if r.content == summary]
        self.assertEqual(len(matches), 1,
                         msg="勒索事件应经 victim 点名直写长期记忆")
        self.assertEqual(matches[0].importance, 0.9)
        self.assertGreaterEqual(matches[0].importance,
                                LONG_TERM_IMPORTANCE_THRESHOLD)
        self.assertIn(summary,
                      [r.content for r in
                       engine.npcs["chen"].memory.long.records],
                      msg="同地目击者应长期记住这桩勒索")
        self.assertNotIn(summary,
                         [r.content for r in
                          engine.npcs["lily"].memory.long.records],
                         msg="异地 NPC 不应感知勒索（同地过滤）")


# ============================================================================ #
# 4. leverage_line / is_leveraged（知情≠余波）
# ============================================================================ #

class TestLeverageLineAndStates(unittest.TestCase):
    """【把柄】行出口：无账本/未勒索恒空（泄底防护），命中恰为
    离散定长行；is_leveraged 双态与 TTL 生命周期。"""

    def test_leverage_line_ledgerless_empty(self):
        """①无账本 NPC：leverage_line ""/is_leveraged False——向后兼容
        （getattr 缺省路径，自定义测试人格零行为）。"""
        npc = NPC(_ledgerless_persona(), MockLLMProvider(), World())
        self.assertIsNone(npc.tag_ledger)
        self.assertEqual(leverage_line(npc), "")
        self.assertFalse(is_leveraged(npc))

    def test_leverage_line_knowledge_only_empty(self):
        """②知情≠余波：醉话泄露登记知情后、勒索前——【把柄】行恒空
        （未勒索的 NPC 不知道玩家知道，prompt 零把柄行=泄底防护）。"""
        _llm, engine = _fresh_engine()
        _make_knowledgeable(engine)
        sun = engine.npcs["tavern_sun"]
        self.assertIn("tavern_sun",
                      engine.blackmail_ledger.player_knowledge)
        self.assertFalse(is_leveraged(sun))
        self.assertEqual(leverage_line(sun), "")

    def test_leverage_line_format_and_length_guard(self):
        """③命中格式：f"【把柄】{label}（命门被攥，惊惧交加）" 无换行；
        SECRET_POOL 五条 label 全 ≤4 字（把柄行 ≤20 字符护栏，
        <35token 纪律）；LEVERAGE_SUFFIX 单一来源拼接。"""
        _llm, engine = _fresh_engine()
        _make_knowledgeable(engine)
        engine.player_blackmails("tavern_sun")
        line = leverage_line(engine.npcs["tavern_sun"])
        label = _innate_secret(engine.npcs["tavern_sun"]).label
        self.assertEqual(line, f"【把柄】{label}（命门被攥，惊惧交加）")
        self.assertEqual(line, f"【把柄】{label}{LEVERAGE_SUFFIX}")
        self.assertNotIn("\n", line)
        for _tid, lbl, _desc in SECRET_POOL:
            self.assertLessEqual(len(lbl), 4,
                                 msg=f"把柄 label「{lbl}」超 4 字口径")
        self.assertLessEqual(len(line), 20,
                             msg=f"【把柄】行超 20 字符护栏: {len(line)}")

    def test_is_leveraged_lifecycle_extinguishes(self):
        """④TTL 生命周期：勒索后余波与烂醉双标签并存（is_leveraged +
        is_dead_drunk 双 True）；跨 25 小时界后余波熄灭（leverage_line
        归空）；知情保留 → 熄灭后可再勒索（复用 TTL 生命周期语义）。"""
        _llm, engine = _fresh_engine()
        _make_knowledgeable(engine)
        engine.player_blackmails("tavern_sun")
        sun = engine.npcs["tavern_sun"]
        self.assertTrue(is_leveraged(sun))
        self.assertTrue(is_dead_drunk(sun))  # 双瞬态并存（最坏叠加态）
        for _ in range(25):  # 跨 25 小时界：烂醉 4h 与余波 24h 均熄灭
            engine.tick(60)
        self.assertFalse(is_dead_drunk(sun))
        self.assertFalse(is_leveraged(sun))
        self.assertEqual(leverage_line(sun), "")
        self.assertIn("tavern_sun",
                      engine.blackmail_ledger.player_knowledge)
        again = engine.player_blackmails("tavern_sun")
        self.assertTrue(again["ok"], msg="余波熄灭后应可再勒索（知情保留）")
        self.assertEqual(again["hush_money"], "酱肉")  # 剩余库存最高值


# ============================================================================ #
# 5. 玩家可感知出口（prompt / 垫话）
# ============================================================================ #

class TestPerceptibleChannels(unittest.TestCase):
    """勒索可感知出口：【把柄】行 prompt 注入 + 垫话敌意前缀轮转。"""

    def test_leverage_prompt_injection_and_contrast(self):
        """①prompt 内容级：勒索后慢脑 user prompt 含【把柄】行（恰为
        leverage_line 产物）；对照——知情未勒索时全 prompt（system+
        user）零把柄 label 与零【把柄】行（泄底防护内容级断言：
        绝密不进身份标签行自动管道）。"""
        # 对照组：知情未勒索 → prompt 逐字节零把柄内容
        llm_a, engine_a = _fresh_engine()
        _make_knowledgeable(engine_a)
        engine_a.player_says(TAVERN_ASK, "tavern_sun")
        self.assertTrue(llm_a.captured)  # 前置：慢脑已产生可断言 prompt
        label = _innate_secret(engine_a.npcs["tavern_sun"]).label
        for msg in llm_a.captured[-1]:
            self.assertNotIn(label, msg["content"],
                             msg="知情未勒索：把柄 label 不得泄进任何 prompt")
            self.assertNotIn("【把柄】", msg["content"])
        # 实验组：勒索后 → user prompt 注入【把柄】行
        llm_b, engine_b = _fresh_engine()
        _make_knowledgeable(engine_b)
        engine_b.player_blackmails("tavern_sun")
        result = engine_b.player_says(TAVERN_ASK, "tavern_sun")
        self.assertEqual(result["brain"], "slow",
                         msg="长句须走慢脑产生 prompt（前置）")
        system_prompt = llm_b.captured[-1][0]["content"]
        user_prompt = llm_b.captured[-1][1]["content"]
        expected_line = f"【把柄】{label}（命门被攥，惊惧交加）"
        self.assertIn(expected_line, user_prompt)
        self.assertNotIn(label, system_prompt,
                          msg="secret=True 把柄不得进身份标签行（自动管道）")

    def test_hostile_filler_prefix_rotation(self):
        """②垫话敌意前缀：勒索余波期（烂醉先熄灭）每次说话甩冷脸——
        HOSTILE_PREFIXES 池按计数取模轮转（池复用背景层同一文案源，
        优先于脾气掩码：记仇的不再热络寒暄）。"""
        _llm, engine = _fresh_engine()
        _make_knowledgeable(engine)
        engine.player_blackmails("tavern_sun")
        for _ in range(5):  # 跨 5 小时界：烂醉 4h 熄灭，余波 24h 仍挂
            engine.tick(60)
        self.assertFalse(is_dead_drunk(engine.npcs["tavern_sun"]))
        self.assertTrue(is_leveraged(engine.npcs["tavern_sun"]))
        first = engine.player_says(TAVERN_ASK, "tavern_sun")["filler"]
        second = engine.player_says(TAVERN_ASK, "tavern_sun")["filler"]
        self.assertEqual(first, HOSTILE_PREFIXES[0])
        self.assertEqual(second, HOSTILE_PREFIXES[1])
        self.assertNotEqual(first, "（笑盈盈）客官来啦！",
                            msg="敌意前缀须覆盖 cheerful 脾气掩码热络带")

    def test_drunk_prefix_beats_hostile(self):
        """③优先级 drunk > hostile：烂醉 + 勒索余波双挂时垫话走醉话池
        （烂醉连话都说不清何谈记仇甩脸——PM 裁定优先级）。"""
        _llm, engine = _fresh_engine()
        _make_knowledgeable(engine)
        engine.player_blackmails("tavern_sun")
        filler = engine.player_says(TAVERN_ASK, "tavern_sun")["filler"]
        self.assertEqual(filler, DRUNK_FILLERS[0],
                         msg="烂醉优先：醉话池覆盖敌意前缀池")
        self.assertNotEqual(filler, HOSTILE_PREFIXES[0])

    def test_filler_hostile_unit_and_back_compat(self):
        """④单元级：generate(hostile=True) 轮转回卷、覆盖脾气掩码；
        双 False 路径逐字节不变（默认参数向后兼容，既有行为零回归）。"""
        filler = FillerEngine("cheerful")
        high_joy = InnerState(e_P=0.9)
        self.assertEqual(filler.generate(high_joy), "（笑盈盈）客官来啦！")
        self.assertEqual(filler.generate(high_joy, hostile=True),
                         HOSTILE_PREFIXES[0])
        self.assertEqual(filler.generate(high_joy, hostile=True),
                         HOSTILE_PREFIXES[1])
        self.assertEqual(filler.generate(high_joy, hostile=True),
                         HOSTILE_PREFIXES[2])
        self.assertEqual(filler.generate(high_joy, hostile=True),
                         HOSTILE_PREFIXES[0])  # 三条池取模回卷
        self.assertEqual(filler.generate(high_joy),
                         "（笑盈盈）客官来啦！",
                         msg="未勒索路径脾气掩码逐字节不变")


# ============================================================================ #
# 6. 醉话半泄露单元（intoxication 第 5 步守卫）
# ============================================================================ #

class TestDrunkLeakGuards(unittest.TestCase):
    """醉话半泄露守卫：玩家同地在场 + 有 INNATE 把柄 + tag_id 在池，
    三者缺一零行为。"""

    def test_leak_requires_player_same_location(self):
        """①玩家不同地 → 无泄露：玩家留 plaza、孙老三在 tavern，满轮
        烂醉只发 npc_drunk 失态不发 secret_mumbled → 知情恒空 →
        勒索亦 no_leverage（不同地没听到醉话，无筹码）。"""
        _llm, engine = _fresh_engine()
        self.assertEqual(engine.world.entities["player"].location_id,
                         "plaza")
        _give_bowls(engine, "tavern_sun", _needed("tavern_sun"))
        self.assertTrue(is_dead_drunk(engine.npcs["tavern_sun"]))
        self.assertEqual(_leak_events(engine), [])
        self.assertEqual(engine.blackmail_ledger.player_knowledge, set())
        self.assertEqual(engine.player_blackmails("tavern_sun")["reason"],
                         "no_leverage")

    def test_leak_requires_innate_secret(self):
        """②无 INNATE 把柄 → 无泄露：空账本 NPC（有账本零把柄）与
        玩家同地满轮烂醉 → 结算照常但零 secret_mumbled（醉汉说胡话
        未必句句见血）。"""
        world = World()
        world.add_entity(Entity(id="player", kind="player", name="旅行者",
                                location_id="plaza", inventory=[]))
        persona = Persona(id="wanderer", name="流浪客", role="villager",
                          location_id="plaza", greeting_bank=["……"],
                          fallback_bank=["……"])
        npc = NPC(persona, MockLLMProvider(), world)
        npc.tag_ledger = TagLedger()  # 有账本零把柄
        ledger = IntoxicationLedger(world, {"wanderer": npc})
        ledger.ensure_tolerance("wanderer", 1.0)  # bowls_needed(1.0)=1
        world.bus.subscribe(ledger.on_event, kinds=["item_given"])
        world.bus.publish(WorldEvent(0, "item_given", "player",
                                     {"to": "wanderer", "item": "米酒"}))
        self.assertTrue(is_dead_drunk(npc))  # 前置：结算照常发生
        self.assertEqual(
            [e for e in world.bus.history if e.kind == "secret_mumbled"],
            [], msg="无把柄的醉汉不得泄露任何醉话")


# ============================================================================ #
# 7. 架构边界（六情边界 / ast / 零 LLM）
# ============================================================================ #

class TestArchitectureBoundary(unittest.TestCase):
    """勒索链路架构边界：blackmail 事件 8D 零增量、源码零
    inner_state 引用、勒索全程零 LLM。"""

    def test_blackmail_event_zero_8d_delta(self):
        """①直发 blackmail 事件前后全村核心 NPC inner_state.to_dict()
        逐项全等（StateUpdater 只分发已知事件，blackmail 零分发——
        六情边界锁死，勒索严禁双写情绪）。"""
        _llm, engine = _fresh_engine()
        before = {nid: npc.inner_state.to_dict()
                  for nid, npc in engine.npcs.items()}
        engine.world.bus.publish(WorldEvent(
            engine.world.tick_count, "blackmail", "player",
            {"victim": "tavern_sun",
             "summary": "旅行者拿住了孙老三的把柄",
             "hush_money": "米酒"}))
        after = {nid: npc.inner_state.to_dict()
                 for nid, npc in engine.npcs.items()}
        self.assertEqual(before, after,
                         msg="blackmail 不得改任何 8D 心智参数"
                             "（同一事件双份情绪增量=两套情绪系统漂移）")

    def test_blackmail_source_no_inner_state_reference(self):
        """②六情边界结构断言：blackmail.py 代码不出现 "inner_state"
        引用（读源文件文本，剔除模块 docstring 后断言——沿
        tests/test_economics.py 先例写法）。"""
        import engine.blackmail as blackmail_mod
        with open(blackmail_mod.__file__, encoding="utf-8") as fh:
            source = fh.read()
        module_doc = ast.get_docstring(ast.parse(source), clean=False) or ""
        code_text = source.replace(module_doc, "", 1)
        self.assertNotIn("inner_state", code_text,
                         msg="blackmail.py 代码层不得引用 inner_state"
                             "（同一事件双份情绪增量=两套情绪系统漂移）")

    def test_blackmail_zero_llm_calls(self):
        """③勒索全程零 LLM：赠饮满轮（醉话半泄露+知情登记）→
        player_blackmails 结算 → 双事件发布——Tier 0 纯规则，
        call_count 恒零。"""
        llm, engine = _fresh_engine()
        self.assertEqual(engine.llm.call_count, 0)
        _make_knowledgeable(engine)
        result = engine.player_blackmails("tavern_sun")
        self.assertTrue(result["ok"])
        self.assertEqual(engine.llm.call_count, 0,
                         msg="勒索链路全程 Tier 0：不得触发任何 LLM 调用")


if __name__ == "__main__":
    unittest.main()

"""T6 第二小步 · 勒索最小闭环验收测试（玩家可感知口径）。

被测契约（PM 定死，engine/blackmail.py 由并行子代理 A 按同一契约
实现；本文件只测不改，不写任何 engine 代码）：

- 发现链路：move_player 同地 → player_gives(npc, "米酒")×阈值碗数 →
  烂醉结算时玩家同地 + victim 有 INNATE 把柄 → 发布 kind="secret_mumbled"
  （actor=victim，payload={"npc", "text"}，文案取 intoxication.DRUNK_LEAKS
  池按把柄 tag_id）+ engine.blackmail_ledger.player_knowledge 登记（知情
  即筹码）；玩家不同地/无 INNATE 把柄 → 无该事件（对照组双双锁定）；
- 勒索入口：engine.player_blackmails(npc_id) → unknown npc /
  no_leverage（含契约回绝句）/ already_leveraged（余波 24h 内重复，
  含契约回绝句）/ 成功（npc/npc_id/hush_money/reply/affinity 五键，
  回复「{item}拿去……」或空库存「值钱的都给你了……」）；
- 结算四件套：①封口物=victim 库存按 estimate_value 降序最高者手动
  转移（tavern_sun [米酒2, 酱肉1] → 米酒；空库存 hush_money=None
  诚实登记）②apply_loss(estimate_value(item)) ③update_affinity(
  victim→player, -1.0) ④add_transient("把柄被攥", ttl=24, secret=True)；
- 双事件：kind="blackmail"（actor="player"，payload={victim, summary,
  hush_money}）+ kind="npc_action"（actor=victim，payload={npc, summary}）；
- 记忆：kind="blackmail" → ("{summary}", 0.9)——victim 点名感知长期
  直写 + 同地目击者感知；secret_mumbled 无记忆模板（泄底防护）；
- 余波期玩家可感知出口：慢脑 user prompt 含 leverage_line(npc) 整串
  （"【把柄】{label}（命门被攥，惊惧交加）"，未勒索/知情未勒索/已
  熄灭恒零注入）；垫话敌意（HOSTILE_PREFIXES 池轮转，drunk 优先）；
  【人际关系】行敌意渲染；TTL 24 世界小时后熄灭、already_leveraged
  解除、知情保留。

断言口径（本文件全部用例遵守）：
1. **只断言玩家可感知行为**——prompt 内容级（RecordingLLM 捕获慢脑
   messages）、返回结构（ok/reason/reply/hush_money/affinity 键）、
   文案逐字（契约原文比对；契约未导出常量的回复句按 PM 契约字面
   锁定，池/标签类一律 import 契约常量或动态取 secrets()）、事件流
   （bus.history 过滤 kind/actor/payload）、账面与记忆数据，不锁内部
   dict 结构；
2. **真实数据驱动**——碗数经 generate_village()+bowls_needed() 计算，
   把柄 label 经 ledger.secrets() 动态取（确定性生成，不硬编码），
   封口物估值链经 estimate_value（不手写 2/1）；
3. **对照组唯一差异法**——每条主链路配对照组（唯一差异=玩家在否/
  有把柄否/勒索否），防词面巧合；
4. **时间推进口径**——engine.tick(minutes=...) 与 test_drunk_behavior/
   test_habit 一致；_fresh_engine 固定 random.seed（world.py 天气走
   全局 random），推进类用例对天气噪声完全确定。

契约疑点处置（两处任务书内部矛盾，如实上报不迁就，详见返回报告）：
- **affinity 数值口径**：任务书端到端场景写 "affinity==-0.5"，但该
  场景必经的 3 碗赠饮走既有六情通道（StateUpdater._on_gift 每次
  update_affinity +0.1，tavern_sun 无守财标签），实测 0.5→0.8→勒索
  -1.0→**-0.2**。本文件端到端按契约机制断言 delta==-1.0（前后差值
  口径，不锁赠饮侧耦合）；契约示例的 -0.5 与"敌意 0.5"渲染在
  TestLeveragePromptInjection.test_hostility_rendered_in_system_prompt
  以**最小注入引擎**（任务书明示"默认引擎（或最小注入）"）+ 公开
  API update_affinity 归位 0.5 覆盖——默认引擎不可用：tavern_sun 在
  configs/relationships.json 有 3 条 |affinity|≥0.5 出边，player 边
  按稳定排序并列垫底被 top-3 截断，"敌意 0.5"在默认村庄结构上不可
  能渲染；
- **8D 六情边界口径**：任务书要求"勒索前后 victim inner_state 逐项
  相等"，但 PM 契约自身规定勒索必发 npc_action 双事件（actor=victim，
  payload.npc=victim），既有 StateUpdater npc_action 通道必然 +0.02
  疲劳/-0.01 唤醒——两条款内在冲突。测试改为断言**勒索的 8D 增量
  恰等于既有 npc_action 通道增量**（参照增量经 StateUpdater.on_event
  公开订阅入口测得，与总线分发同一入口）：这是可区分的更强断言，
  任何六情双写（额外的 PAD 矢量）都会当场暴露。

并行时序说明：子代理 A 的 engine/blackmail.py 未就位时，本文件
import 即红（ModuleNotFoundError）——预期内的并行时序，不为迁就
现状改契约语义。
"""

import os
import random
import re
import unittest

from engine.background_npc import HOSTILE_PREFIXES
from engine.blackmail import (BlackmailLedger, LEVERAGED_LABEL,
                              LEVERAGED_TAG_ID, LEVERAGED_TTL_HOURS,
                              LEVERAGE_SUFFIX, is_leveraged, leverage_line)
from engine.economics import estimate_value
from engine.engine import NPCEngine
from engine.intoxication import (DRUNK_FILLERS, bowls_needed, is_dead_drunk)
from engine.llm.mock import MockLLMProvider
from engine.npc import Persona
from engine.relationships import RelationshipNetwork
from engine.tag_mount import SECRET_POOL
from engine.village_genesis import generate_village
from engine.world import WorldEvent


class RecordingLLM(MockLLMProvider):
    """记录完整 messages 的 Mock（慢脑 prompt 内容级断言用，沿
    test_drunk_behavior.py 同款装配模式，本文件自持一份避免跨测试耦合）。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.captured = []

    def chat(self, messages, temperature=0.7):
        self.captured.append([dict(m) for m in messages])
        return super().chat(messages, temperature=temperature)


def extract_block(prompt: str, marker: str) -> str:
    """提取 prompt 中 marker 标记块内容（marker 后到首个空行）。

    逻辑与 scripts/dialogue_quality.py / tests/test_drunk_behavior.py
    extract_block 同口径（块级精确断言的既有模式），本文件自持一份。
    """
    if not prompt or marker not in prompt:
        return ""
    tail = prompt.split(marker, 1)[1].lstrip("\n")
    end = tail.find("\n\n")
    return tail[:end] if end != -1 else tail


# ---- 场景常量（确定性：固定文本 + 固定推进节奏；均超快脑句长闸门） ----
WINE_ITEM = "米酒"
# 慢脑寒暄句（>12 字稳定走慢脑产生 prompt；不含快脑问候/问价/问路模式）
SLOW_ASK_TAVERN = "孙掌柜，你这儿最近生意如何，客人多不多呀？"
SLOW_ASK_CHEN = "陈师傅，跟您打听个事儿，您最近可有什么新活计？"
SLOW_ASK_LILY = "莉莉老板，你这儿最近都进些什么稀罕货色呀？"
# 无把柄对照 NPC 的酒量档案（ensure_tolerance 场景预置；碗数经
# bowls_needed API 计算，不手写阈值）
_CONTROL_TOL = 3.0
# random seed：tick 推进时天气变化走全局 random.random()（world.py
# WEATHER_CHANGE_PROB），不 seed 则组合运行时随机红（沿
# test_drunk_behavior.py _fresh_engine 既有纪律）。
_SCENARIO_SEED = 20261018

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TAVERN_PERSONA_PATH = os.path.join(_PROJECT_ROOT, "configs", "npcs",
                                    "tavern_sun.json")


def _fresh_engine(seed: int = _SCENARIO_SEED):
    """默认村庄 fresh 引擎 + 录制 LLM：独立世界与账本，用例间零污染。"""
    random.seed(seed)
    llm = RecordingLLM()
    engine = NPCEngine(llm=llm)
    return llm, engine


def _minimal_tavern_engine(seed: int = _SCENARIO_SEED):
    """最小注入引擎：仅 tavern_sun 人格 + 空关系网 + 零背景 NPC。

    任务书明示"默认引擎（或最小注入）"。本夹具服务 system prompt
    敌意渲染断言：默认村庄 tavern_sun 有 3 条 |affinity|≥0.5 文件边，
    player 边稳定排序并列垫底被【人际关系】top-3 截断——"敌意 0.5"
    在默认结构上不可渲染；最小注入下 player 边是唯一边，渲染无截断
    噪声。tolerance/财富档案仍单一来源 generate_village（引擎构造
    与 npc_configs 无关地建全村账），烂醉/勒索链路与默认引擎同构。
    """
    random.seed(seed)
    llm = RecordingLLM()
    persona = Persona.from_file(_TAVERN_PERSONA_PATH)
    engine = NPCEngine(llm=llm, npc_configs=[persona],
                       relationships=RelationshipNetwork(),
                       background_configs=[])
    return llm, engine


def _alcohol_tol(npc_id: str) -> float:
    """经 generate_village() 取真实档案酒量（单一来源，严禁手写数字）。"""
    return generate_village()[npc_id].innate.alcohol_tol


def _bowls(npc_id: str) -> int:
    """该 NPC 烂醉所需碗数（bowls_needed API 计算，真实数据驱动）。"""
    return bowls_needed(_alcohol_tol(npc_id))


def _give_bowls(engine: NPCEngine, npc_id: str, count: int) -> None:
    """赠饮 count 碗（宽松模式：玩家无库存也计数，契约口径）。"""
    for _ in range(count):
        result = engine.player_gives(npc_id, WINE_ITEM)
        assert result["ok"], f"player_gives 应成功（宽松模式）: {result}"


def _make_informed(engine: NPCEngine, npc_id: str = "tavern_sun") -> None:
    """发现链路场景：玩家挪到 victim 同地 → 灌至烂醉（醉话半泄露 +
    player_knowledge 登记随之发生，断言留给各用例）。"""
    engine.move_player("tavern")
    _give_bowls(engine, npc_id, _bowls(npc_id))


def _long_contents(engine: NPCEngine, npc_id: str) -> list:
    """读 NPC 长期记忆全部文本（勒索记忆直写断言口）。"""
    return [r.content for r in engine.npcs[npc_id].memory.long.records]


def _short_contents(engine: NPCEngine, npc_id: str) -> list:
    """读 NPC 短期记忆全部文本（泄底防护零写入断言口）。"""
    return [r.content for r in engine.npcs[npc_id].memory.short.recent()]


def _leak_events(engine: NPCEngine) -> list:
    """醉话半泄露事件（bus.history 过滤 kind）。"""
    return [e for e in engine.world.bus.history if e.kind == "secret_mumbled"]


def _blackmail_events(engine: NPCEngine) -> list:
    """勒索成交事件（bus.history 过滤 kind）。"""
    return [e for e in engine.world.bus.history if e.kind == "blackmail"]


def _victim_action_events(engine: NPCEngine, npc_id: str) -> list:
    """victim 产出的 npc_action 事件（勒索成交动作的唯一事件出口）。"""
    return [e for e in engine.world.bus.history
            if e.kind == "npc_action" and e.actor == npc_id]


def _assert_no_secret_leak(testcase: unittest.TestCase, texts) -> None:
    """泄底防护共用断言：texts 中任何一段都不得含 SECRET_POOL 任何
    一条把柄的 label 与 description（遍历 import 的池常量，不手写清单）。"""
    for tag_id, label, description in SECRET_POOL:
        for text in texts:
            testcase.assertNotIn(
                label, text,
                msg=f"泄底：prompt/文案含把柄 label「{label}」，"
                    f"文本片段: {text[:120]!r}")
            testcase.assertNotIn(
                description, text,
                msg=f"泄底：prompt/文案含把柄 description「{description}」，"
                    f"文本片段: {text[:120]!r}")


# ========================================================================== #
# 0. TestBlackmailWiring —— 契约接线与常量锁
# ========================================================================== #
class TestBlackmailWiring(unittest.TestCase):
    """契约接线：engine.blackmail_ledger 属性暴露 + 契约常量值锁。"""

    def test_engine_exposes_blackmail_ledger(self):
        """①引擎暴露勒索账本属性（契约点名 engine.blackmail_ledger，
        BlackmailLedger 实例；player_knowledge 为 set 且默认恒空——
        默认村庄无赠饮零知情）——属性名接线错误当场暴露。"""
        _llm, engine = _fresh_engine()
        self.assertIsInstance(engine.blackmail_ledger, BlackmailLedger,
                              msg="NPCEngine 应暴露 blackmail_ledger "
                                  "属性（BlackmailLedger 实例）")
        self.assertIsInstance(engine.blackmail_ledger.player_knowledge, set,
                              msg="player_knowledge 应为 set（知情 npc_id 集合）")
        self.assertEqual(engine.blackmail_ledger.player_knowledge, set(),
                         msg="默认村庄无赠饮，知情集合应为空")

    def test_contract_constants_locked(self):
        """②契约常量锁：余波标签"把柄被攥"、TTL 24 世界小时、【把柄】
        行括注"（命门被攥，惊惧交加）"（PM 契约字面）；未勒索 NPC 的
        is_leveraged=False / leverage_line=""（空路径零注入的根）。"""
        self.assertEqual(LEVERAGED_LABEL, "把柄被攥")
        self.assertEqual(LEVERAGED_TTL_HOURS, 24)
        self.assertEqual(LEVERAGE_SUFFIX, "（命门被攥，惊惧交加）")
        _llm, engine = _fresh_engine()
        tavern = engine.npcs["tavern_sun"]
        self.assertGreaterEqual(
            len(tavern.tag_ledger.secrets()), 1,
            msg="前置：tavern_sun 创生账本应挂 ≥1 绝密把柄")
        self.assertFalse(is_leveraged(tavern),
                         msg="未勒索的 tavern_sun 不应处于余波期")
        self.assertEqual(leverage_line(tavern), "",
                         msg="未勒索的 leverage_line 应为空串（零注入）")


# ========================================================================== #
# 1. TestBlackmailEndToEnd —— 端到端主链路（发现→知情→要挟→得手）
# ========================================================================== #
class TestBlackmailEndToEnd(unittest.TestCase):
    """灌醉→醉话半泄露→知情→勒索得手：封口物转移/损失联动/敌意边/
    余波标签/双事件全链玩家可感知。"""

    def test_blackmail_full_chain(self):
        """①主链路：玩家同地灌醉 tavern_sun（阈值碗数真实数据驱动）→
        secret_mumbled 恰一条（payload npc/text 非空）+ player_knowledge
        登记 → player_blackmails 成功：hush_money=="米酒"（库存按
        estimate_value 降序最高者：米酒 2 > 酱肉 1）、库存真实转移、
        wealth 恰减 estimate_value(米酒)、affinity 恰减 1.0、契约回复
        逐字、blackmail+npc_action 双事件、余波标签挂载且不进可见
        标签、全程零 LLM。

        affinity 口径说明：任务书示例 "-0.5" 未计入 3 碗赠饮的六情
        通道 +0.3（实测 0.5→0.8→-0.2）；本断言按契约机制锁 delta
        ==-1.0，契约示例值在 TestLeveragePromptInjection 归位预置
        场景单独覆盖。
        """
        llm, engine = _fresh_engine()
        tavern = engine.npcs["tavern_sun"]
        tavern_name = tavern.persona.name
        # 前置：默认库存与把柄（真实数据驱动，估值序前提）
        self.assertIn(WINE_ITEM, engine.world.entities["tavern_sun"].inventory,
                      msg="前置：tavern_sun 默认库存应含米酒")
        self.assertIn("酱肉", engine.world.entities["tavern_sun"].inventory,
                      msg="前置：tavern_sun 默认库存应含酱肉")
        self.assertGreaterEqual(len(tavern.tag_ledger.secrets()), 1,
                                msg="前置：创生账本应挂 ≥1 绝密把柄")
        # 1) 发现：玩家同地 + 灌至烂醉 → 醉话半泄露 + 知情登记
        _make_informed(engine, "tavern_sun")
        self.assertTrue(is_dead_drunk(tavern), msg="前置：已烂醉")
        leaks = _leak_events(engine)
        self.assertEqual(len(leaks), 1,
                         msg="烂醉结算应恰发布一条 secret_mumbled 事件")
        self.assertEqual(leaks[0].actor, "tavern_sun",
                         msg="醉话泄露事件 actor 应为 victim")
        self.assertEqual(leaks[0].payload["npc"], "tavern_sun",
                         msg="醉话泄露 payload['npc'] 应为 victim id")
        self.assertTrue(leaks[0].payload["text"],
                        msg="醉话文案应为非空字符串（DRUNK_LEAKS 池产物，"
                            "文案属 A 契约不硬编码，断言非空与发布次数）")
        self.assertIn("tavern_sun", engine.blackmail_ledger.player_knowledge,
                      msg="醉话泄露后 player_knowledge 应登记 victim")
        # 2) 要挟得手：结算四件套全链
        wealth_before = engine.wealth_ledger.account("tavern_sun").wealth
        rel_before = engine.relationships.get_relation_to(
            "tavern_sun", "player").affinity
        result = engine.player_blackmails("tavern_sun")
        self.assertTrue(result["ok"], msg=f"知情后勒索应成功: {result}")
        self.assertEqual(result["npc"], tavern_name)
        self.assertEqual(result["npc_id"], "tavern_sun")
        self.assertEqual(result["hush_money"], WINE_ITEM,
                         msg="封口物应为库存估值最高者（米酒 2 > 酱肉 1，"
                             "经 estimate_value 单一估值源）")
        self.assertEqual(
            result["reply"],
            f"「{WINE_ITEM}拿去……这事，天知地知，你知我知。」",
            msg="成交回复应与契约文案逐字相等")
        # 封口物真实转移：NPC 库存无米酒、玩家背包有米酒
        self.assertNotIn(WINE_ITEM,
                         engine.world.entities["tavern_sun"].inventory,
                         msg="勒索后 victim 库存不应再有米酒")
        self.assertIn(WINE_ITEM, engine.world.entities["player"].inventory,
                      msg="勒索后玩家背包应获得米酒")
        # 损失联动：wealth 恰减封口物估值（apply_loss 契约）
        self.assertEqual(
            engine.wealth_ledger.account("tavern_sun").wealth,
            wealth_before - estimate_value(WINE_ITEM),
            msg="勒索应联动 apply_loss(estimate_value(封口物))")
        # 敌意边：affinity 恰减 1.0（update_affinity 契约；返回值与
        # 关系网一致——delta 口径见本用例 docstring）
        self.assertAlmostEqual(result["affinity"], rel_before - 1.0,
                               places=6,
                               msg="勒索后敌意应恰减 1.0（契约 "
                                   "update_affinity(victim→player, -1.0)）")
        rel_after = engine.relationships.get_relation_to(
            "tavern_sun", "player").affinity
        self.assertAlmostEqual(rel_after, result["affinity"], places=6,
                               msg="返回 affinity 应与关系网账面一致")
        # 余波标签：挂载成功且 secret=True 不进可见标签
        self.assertTrue(is_leveraged(tavern), msg="勒索后应处于余波期")
        self.assertFalse(
            any(t.tag_id == LEVERAGED_TAG_ID
                for t in tavern.tag_ledger.visible_tags()),
            msg="余波标签是隐秘心理（secret），不得进玩家可见标签集")
        line = leverage_line(tavern)
        self.assertTrue(line, msg="余波期 leverage_line 应非空")
        self.assertTrue(
            any(t.label in line for t in tavern.tag_ledger.secrets()),
            msg=f"【把柄】行应含 victim 把柄 label（动态取 secrets()），"
                f"实际: {line!r}")
        # 双事件：blackmail（player 视角第三人称）+ npc_action（victim 动作）
        blackmail_events = _blackmail_events(engine)
        self.assertEqual(len(blackmail_events), 1,
                         msg="勒索成交应恰发布一条 blackmail 事件")
        self.assertEqual(blackmail_events[0].actor, "player")
        self.assertEqual(blackmail_events[0].payload["victim"], "tavern_sun")
        self.assertEqual(blackmail_events[0].payload["hush_money"], WINE_ITEM)
        self.assertIn("拿住了", blackmail_events[0].payload["summary"],
                      msg="blackmail summary 应含契约关键词「拿住了」")
        self.assertIn("把柄", blackmail_events[0].payload["summary"],
                      msg="blackmail summary 应含契约关键词「把柄」")
        self.assertIn(tavern_name, blackmail_events[0].payload["summary"],
                      msg="blackmail summary 应点名 victim")
        action_events = _victim_action_events(engine, "tavern_sun")
        self.assertEqual(len(action_events), 1,
                         msg="勒索成交应恰发布一条 victim 的 npc_action 事件")
        self.assertEqual(action_events[0].payload["npc"], "tavern_sun")
        self.assertTrue(action_events[0].payload["summary"],
                        msg="成交动作 summary 应为非空字符串")
        # 勒索链路零 LLM（发现+要挟全程 Tier 0）
        self.assertEqual(llm.call_count, 0, msg="勒索全链应零 LLM 调用")
        self.assertEqual(llm.total_tokens_used, 0, msg="勒索全链应零 token")

    def test_leak_requires_player_same_location(self):
        """②对照组（玩家不在场）：唯一差异=玩家留在广场不同地 → 同样
        灌至烂醉（结算真实发生），零 secret_mumbled、知情恒空、随后
        勒索被 no_leverage 回绝——醉话只有玩家在场听得到。"""
        _llm, engine = _fresh_engine()
        self.assertEqual(engine.world.entities["player"].location_id, "plaza",
                         msg="前置：玩家默认在广场（不同地）")
        _give_bowls(engine, "tavern_sun", _bowls("tavern_sun"))
        self.assertTrue(is_dead_drunk(engine.npcs["tavern_sun"]),
                        msg="前置：烂醉结算真实发生（对照组只差玩家在否）")
        self.assertEqual(_leak_events(engine), [],
                         msg="玩家不同地：烂醉结算不得发布醉话泄露")
        self.assertEqual(engine.blackmail_ledger.player_knowledge, set(),
                         msg="玩家不同地：知情恒空")
        rejected = engine.player_blackmails("tavern_sun")
        self.assertEqual(
            rejected,
            {"ok": False, "reason": "no_leverage",
             "reply": "「你在说什么？老听不明白。」"},
            msg="不知情勒索应被 no_leverage 契约回绝")

    def test_leak_requires_innate_secret(self):
        """③对照组（无把柄）：唯一差异=victim 无 INNATE 把柄（自定义
        无 genesis 账本 NPC，玩家同地灌至烂醉）→ 零 secret_mumbled、
        知情恒空——醉汉说胡话，没有把柄就无可泄露。"""
        persona = Persona(id="drunkard_wu", name="吴酒鬼", role="villager",
                          location_id="tavern")
        llm = RecordingLLM()
        engine = NPCEngine(llm=llm, npc_configs=[persona],
                           background_configs=[])
        engine.intoxication_ledger.ensure_tolerance("drunkard_wu", _CONTROL_TOL)
        engine.move_player("tavern")
        self.assertIsNone(engine.npcs["drunkard_wu"].tag_ledger,
                          msg="前置：无 genesis 声明的 NPC 不挂账本（无把柄）")
        _give_bowls(engine, "drunkard_wu", bowls_needed(_CONTROL_TOL))
        drunk_events = [e for e in engine.world.bus.history
                        if e.kind == "npc_drunk"]
        self.assertEqual(len(drunk_events), 1,
                         msg="前置：烂醉结算真实发生（npc_drunk 已发布）")
        self.assertEqual(_leak_events(engine), [],
                         msg="无 INNATE 把柄：同地烂醉也不得发布醉话泄露")
        self.assertEqual(engine.blackmail_ledger.player_knowledge, set(),
                         msg="无把柄：知情恒空")


# ========================================================================== #
# 2. TestLeveragePromptInjection —— 【把柄】行与敌意渲染（prompt 内容级）
# ========================================================================== #
class TestLeveragePromptInjection(unittest.TestCase):
    """余波期 prompt 内容级：user prompt 注入 leverage_line 整串（含
    把柄 label 与契约括注）；system prompt【人际关系】行渲染敌意。"""

    def test_leverage_line_injected_after_blackmail(self):
        """①主链路（默认引擎）：烂醉前基线 prompt 零"【把柄】"→ 勒索后
        tavern_sun 慢脑 user prompt 含 leverage_line(npc) 整串（"【把柄】"
        + 把柄 label + 契约括注三组件齐全）——【把柄】行是余波期唯一
        的把柄 prompt 出口。"""
        llm, engine = _fresh_engine()
        # 烂醉前基线：正常寒暄，prompt 零把柄行
        result0 = engine.player_says(SLOW_ASK_TAVERN, "tavern_sun")
        self.assertEqual(result0["brain"], "slow",
                         msg="长句须走慢脑产生 prompt（前置）")
        prompt0 = llm.captured[-1][1]["content"]
        self.assertNotIn("【把柄】", prompt0,
                         msg="勒索前 user prompt 不应含【把柄】行")
        # 发现 + 要挟
        _make_informed(engine, "tavern_sun")
        engine.player_blackmails("tavern_sun")
        self.assertTrue(is_leveraged(engine.npcs["tavern_sun"]),
                        msg="前置：余波期已生效")
        result1 = engine.player_says(SLOW_ASK_TAVERN, "tavern_sun")
        self.assertEqual(result1["brain"], "slow",
                         msg="余波期长句仍走慢脑（前置）")
        prompt1 = llm.captured[-1][1]["content"]
        line = leverage_line(engine.npcs["tavern_sun"])
        secret_labels = [t.label
                         for t in engine.npcs["tavern_sun"].tag_ledger.secrets()]
        self.assertIn(line, prompt1,
                      msg=f"勒索后 user prompt 应含 leverage_line 整串"
                          f"（契约：慢脑 prompt 含 leverage_line(npc)），"
                          f"期望: {line!r}")
        self.assertIn("【把柄】", prompt1)
        self.assertIn(LEVERAGE_SUFFIX, prompt1,
                      msg=f"【把柄】行应含契约括注 {LEVERAGE_SUFFIX!r}")
        self.assertTrue(any(label in prompt1 for label in secret_labels),
                        msg="【把柄】行应含 victim 把柄 label（动态取 "
                            "secrets()，不硬编码）")

    def test_knowledge_without_blackmail_zero_leverage_line(self):
        """②对照组（知情未勒索）：唯一差异=不勒索 → 同样灌醉知情，慢脑
        user prompt 零"【把柄】"与零余波标签——知情≠余波（未勒索的
        NPC 不知道玩家知道，泄底防护对照）。"""
        llm, engine = _fresh_engine()
        _make_informed(engine, "tavern_sun")
        self.assertIn("tavern_sun", engine.blackmail_ledger.player_knowledge,
                      msg="前置：知情已登记（对照组只差勒索与否）")
        self.assertFalse(is_leveraged(engine.npcs["tavern_sun"]),
                         msg="前置：未勒索不在余波期")
        result = engine.player_says(SLOW_ASK_TAVERN, "tavern_sun")
        self.assertEqual(result["brain"], "slow",
                         msg="知情未勒索长句走慢脑，保证 prompt 可比")
        prompt = llm.captured[-1][1]["content"]
        self.assertNotIn("【把柄】", prompt,
                          msg="知情未勒索：user prompt 不得含【把柄】行")
        self.assertNotIn(LEVERAGED_LABEL, prompt,
                          msg=f"知情未勒索：user prompt 不得含余波标签"
                              f" {LEVERAGED_LABEL!r}")

    def test_hostility_rendered_in_system_prompt(self):
        """③敌意渲染（最小注入引擎）：player 边为唯一关系边（默认引擎
        被【人际关系】top-3 截断，见模块 docstring），勒索前 system
        prompt 渲染"好感 0.5"→ 赠饮六情通道抬高后经公开 API 归位 0.5
        （场景预置，任务书示例口径）→ 勒索后 affinity==-0.5 且 system
        prompt【人际关系】行渲染"敌意 0.5"（relationships.py 既有负值
        分支零改动直接消费）。"""
        llm, engine = _minimal_tavern_engine()
        # 勒索前基线：好感渲染
        result0 = engine.player_says(SLOW_ASK_TAVERN, "tavern_sun")
        self.assertEqual(result0["brain"], "slow", msg="前置：慢脑可达")
        system0 = llm.captured[-1][0]["content"]
        self.assertIn("好感 0.5", system0,
                      msg="前置：勒索前 player 边按默认客人 0.5 渲染好感")
        self.assertNotIn("敌意", system0,
                         msg="勒索前 system prompt 不应渲染敌意")
        # 发现 + 归位 0.5（赠饮六情通道 +0.3 的场景预置回契约口径）
        _make_informed(engine, "tavern_sun")
        rel = engine.relationships.get_relation_to("tavern_sun", "player")
        engine.relationships.update_affinity("tavern_sun", "player",
                                             0.5 - rel.affinity)
        self.assertAlmostEqual(
            engine.relationships.get_relation_to("tavern_sun", "player").affinity,
            0.5, places=6, msg="前置：affinity 已归位契约口径 0.5")
        result = engine.player_blackmails("tavern_sun")
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["affinity"], -0.5, places=6,
                               msg="勒索后敌意应为契约示例值 -0.5")
        # 勒索后：敌意渲染
        result1 = engine.player_says(SLOW_ASK_TAVERN, "tavern_sun")
        self.assertEqual(result1["brain"], "slow", msg="前置：慢脑可达")
        system1 = llm.captured[-1][0]["content"]
        self.assertIn("敌意 0.5", system1,
                      msg="勒索后 system prompt【人际关系】行应渲染"
                          "「敌意 0.5」（affinity=-0.5 落入既有敌意带）")
        self.assertNotIn("好感", system1,
                         msg="敌意渲染后不应再出现好感文案")


# ========================================================================== #
# 3. TestHostileFiller —— 垫话敌意（余波期玩家可感知出口）
# ========================================================================== #
class TestHostileFiller(unittest.TestCase):
    """勒索余波期垫话走 HOSTILE_PREFIXES 敌意池（复用背景层同一文案
    池），冷哼轮转确定性；烂醉优先于敌意。均配对照组（唯一差异法）。"""

    def test_hostile_filler_pool_and_rotation(self):
        """①余波期（醒酒后）连续 3 轮对话垫话 == HOSTILE_PREFIXES 池
        序列（池[0]→池[1]→池[2]，import 契约池断言轮转确定性）。"""
        _llm, engine = _fresh_engine()
        _make_informed(engine, "tavern_sun")
        engine.player_blackmails("tavern_sun")
        # 等烂醉熄灭（4 个小时界，DRUNK_TTL 口径）——敌意垫话须在
        # 清醒时观测（烂醉优先级在③单独锁定）
        for _ in range(4):
            engine.tick(60)
        self.assertFalse(is_dead_drunk(engine.npcs["tavern_sun"]),
                         msg="前置：已醒酒（hostile 路径可观测）")
        self.assertTrue(is_leveraged(engine.npcs["tavern_sun"]),
                        msg="前置：仍处于 24h 余波期")
        fillers = []
        for _ in range(3):
            result = engine.player_says(SLOW_ASK_TAVERN, "tavern_sun")
            self.assertEqual(result["brain"], "slow", msg="长句走慢脑（前置）")
            fillers.append(result["filler"])
        self.assertEqual(fillers, list(HOSTILE_PREFIXES),
                         msg=f"余波期连续 3 轮垫话应按 HOSTILE_PREFIXES 池"
                             f"确定性轮转，期望 {list(HOSTILE_PREFIXES)!r}，"
                             f"实际 {fillers!r}")

    def test_control_without_blackmail_filler_not_hostile(self):
        """②对照组（知情未勒索）：唯一差异=不勒索 → 同样灌醉知情并等
        醒酒，垫话 ∉ HOSTILE_PREFIXES（脾气常态路径）——敌意垫话确来自
        勒索余波而非醒酒本身。"""
        _llm, engine = _fresh_engine()
        _make_informed(engine, "tavern_sun")
        self.assertIn("tavern_sun", engine.blackmail_ledger.player_knowledge,
                      msg="前置：知情已登记（对照组只差勒索与否）")
        for _ in range(4):
            engine.tick(60)
        self.assertFalse(is_dead_drunk(engine.npcs["tavern_sun"]),
                         msg="前置：已醒酒（与主链路同观测点）")
        result = engine.player_says(SLOW_ASK_TAVERN, "tavern_sun")
        self.assertEqual(result["brain"], "slow", msg="长句走慢脑（前置）")
        self.assertNotIn(result["filler"], HOSTILE_PREFIXES,
                         msg=f"知情未勒索的垫话不应是敌意前缀，实际: "
                             f"{result['filler']!r}")

    def test_drunk_takes_precedence_over_hostile(self):
        """③优先级：烂醉+被勒索 → 垫话仍为醉变体（DRUNK_FILLERS，
        drunk 优先于 hostile——烂醉连话都说不清，何谈记仇甩冷脸）。"""
        _llm, engine = _fresh_engine()
        _make_informed(engine, "tavern_sun")
        engine.player_blackmails("tavern_sun")
        self.assertTrue(is_dead_drunk(engine.npcs["tavern_sun"]),
                        msg="前置：烂醉态（与余波期并存）")
        self.assertTrue(is_leveraged(engine.npcs["tavern_sun"]),
                        msg="前置：余波期已生效")
        result = engine.player_says(SLOW_ASK_TAVERN, "tavern_sun")
        self.assertEqual(result["brain"], "slow", msg="长句走慢脑（前置）")
        self.assertIn(result["filler"], DRUNK_FILLERS,
                      msg=f"烂醉+被勒索垫话应为醉变体（drunk 优先），"
                          f"实际: {result['filler']!r}")
        self.assertNotIn(result["filler"], HOSTILE_PREFIXES,
                         msg="醉变体不得同时是敌意前缀（两池互斥）")


# ========================================================================== #
# 4. TestBlackmailGuards —— 守卫链（确定性回绝）
# ========================================================================== #
class TestBlackmailGuards(unittest.TestCase):
    """勒索入口守卫链：unknown npc / no_leverage / already_leveraged /
    空库存诚实登记——全部确定性文案、零副作用。"""

    def test_unknown_npc_rejected(self):
        """①未知 id 与背景 NPC id（baker_liu）一律 unknown npc（背景
        NPC 不开放玩家勒索，与交易 API 同口径）。"""
        _llm, engine = _fresh_engine()
        self.assertIn("baker_liu", engine.background_npcs,
                      msg="前置：baker_liu 应为背景 NPC")
        self.assertNotIn("baker_liu", engine.npcs)
        self.assertEqual(engine.player_blackmails("nobody"),
                         {"ok": False, "reason": "unknown npc"})
        self.assertEqual(engine.player_blackmails("baker_liu"),
                         {"ok": False, "reason": "unknown npc"})

    def test_no_knowledge_no_leverage_rejected(self):
        """②无知情直接勒索：no_leverage + 契约回绝句逐字；零事件、
        零库存转移、零关系变化（拒绝路径零副作用）。"""
        _llm, engine = _fresh_engine()
        inventory_before = list(
            engine.world.entities["tavern_sun"].inventory)
        affinity_before = engine.relationships.get_relation_to(
            "tavern_sun", "player").affinity
        result = engine.player_blackmails("tavern_sun")
        self.assertEqual(
            result,
            {"ok": False, "reason": "no_leverage",
             "reply": "「你在说什么？老听不明白。」"},
            msg="无知情勒索应被 no_leverage 契约回绝（回复逐字）")
        self.assertEqual(_blackmail_events(engine), [],
                         msg="拒绝路径不得发布 blackmail 事件")
        self.assertEqual(_victim_action_events(engine, "tavern_sun"), [],
                         msg="拒绝路径不得发布成交动作事件")
        self.assertEqual(engine.world.entities["tavern_sun"].inventory,
                         inventory_before, msg="拒绝不得转移库存")
        self.assertEqual(engine.relationships.get_relation_to(
            "tavern_sun", "player").affinity, affinity_before,
            msg="拒绝不得改关系")

    def test_already_leveraged_second_call_rejected(self):
        """③余波期内二连勒索：already_leveraged + 契约回绝句逐字；
        不再取第二件封口物（库存不动、blackmail 事件计数不增）。"""
        _llm, engine = _fresh_engine()
        _make_informed(engine, "tavern_sun")
        first = engine.player_blackmails("tavern_sun")
        self.assertTrue(first["ok"], msg="前置：首次勒索成功")
        inventory_after_first = list(
            engine.world.entities["tavern_sun"].inventory)
        second = engine.player_blackmails("tavern_sun")
        self.assertEqual(
            second,
            {"ok": False, "reason": "already_leveraged",
             "reply": "「值钱的你都拿走了，还想怎样？」"},
            msg="余波期重复勒索应被 already_leveraged 契约回绝（回复逐字）")
        self.assertEqual(engine.world.entities["tavern_sun"].inventory,
                         inventory_after_first,
                         msg="回绝不得再取第二件封口物")
        self.assertEqual(len(_blackmail_events(engine)), 1,
                         msg="回绝路径不得新增 blackmail 事件")

    def test_empty_inventory_honest_none_hush_money(self):
        """④空库存：结算前清空 victim 库存 → ok=True、hush_money=None
        （诚实登记不发明数字）、空库存契约回复逐字、玩家背包无所得、
        账面零损失、余波标签照常挂载（把柄被攥与封口物有无无关）。"""
        _llm, engine = _fresh_engine()
        _make_informed(engine, "tavern_sun")
        engine.world.entities["tavern_sun"].inventory.clear()
        wealth_before = engine.wealth_ledger.account("tavern_sun").wealth
        player_inventory_before = list(
            engine.world.entities["player"].inventory)
        result = engine.player_blackmails("tavern_sun")
        self.assertTrue(result["ok"], msg="知情在手，空库存也勒索成立")
        self.assertIsNone(result["hush_money"],
                          msg="空库存封口物应为 None（不发明数字）")
        self.assertEqual(result["reply"],
                         "「值钱的都给你了……你、你嘴下留情。」",
                         msg="空库存回复应与契约文案逐字相等")
        self.assertEqual(engine.world.entities["player"].inventory,
                         player_inventory_before,
                         msg="空库存勒索玩家无所得")
        self.assertEqual(engine.wealth_ledger.account("tavern_sun").wealth,
                         wealth_before,
                         msg="无封口物则无账面损失（诚实登记）")
        self.assertTrue(is_leveraged(engine.npcs["tavern_sun"]),
                        msg="空库存勒索后余波标签照常挂载")
        events = _blackmail_events(engine)
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0].payload["hush_money"],
                          msg="blackmail 事件应诚实登记 hush_money=None")


# ========================================================================== #
# 5. TestBlackmailMemory —— 勒索记忆（victim 直写 + 目击者 + 次晨引用）
# ========================================================================== #
class TestBlackmailMemory(unittest.TestCase):
    """「被要挟终身记得」：victim importance 0.9 长期直写、同地目击者
    感知、次晨对话【相关长期记忆】检索引用；醉话零记忆写入（泄底）。"""

    def test_victim_and_witness_remember_blackmail(self):
        """①victim 长期记忆含勒索 summary（"拿住了"+"把柄"关键词，
        importance 0.9 契约值）；同地目击者 chen（移到酒馆）同样记住；
        异地对照 lily（集市）零感知——村里大事瞒不过同地的人。"""
        _llm, engine = _fresh_engine()
        engine.world.move_entity("chen", "tavern")
        self.assertEqual(engine.npcs["chen"].entity.location_id, "tavern",
                         msg="前置：chen 已挪到酒馆（同地目击）")
        _make_informed(engine, "tavern_sun")
        engine.player_blackmails("tavern_sun")
        # victim：点名感知长期直写，importance 0.9
        victim_records = [r for r in engine.npcs["tavern_sun"].memory.long.records
                          if "拿住了" in r.content and "把柄" in r.content]
        self.assertTrue(victim_records,
                        msg="victim 长期记忆应含勒索 summary（拿住了+把柄），"
                            f"实际: {_long_contents(engine, 'tavern_sun')!r}")
        self.assertEqual(victim_records[0].importance, 0.9,
                         msg="勒索记忆 importance 应为契约值 0.9"
                             "（重大事件长期直写）")
        # 同地目击者：记住同一 summary
        witness_contents = _long_contents(engine, "chen")
        self.assertTrue(any("拿住了" in c and "把柄" in c
                            for c in witness_contents),
                        msg="同地目击者 chen 应记住勒索（同地过滤感知），"
                            f"实际长期记忆: {witness_contents!r}")
        # 异地对照：lily 零感知
        self.assertFalse(any("拿住了" in c
                             for c in _long_contents(engine, "lily")),
                         msg="异地 lily 不应感知酒馆勒索事件")

    def test_control_without_blackmail_no_summary_memory(self):
        """②对照组（无勒索）：唯一差异=不勒索 → victim 长期记忆只有
        赠饮/失态条目，零"拿住了"记录（链路活跃性经赠予条目前置）——
        勒索记忆确来自勒索事件而非灌醉场景词面巧合。"""
        _llm, engine = _fresh_engine()
        _make_informed(engine, "tavern_sun")
        self.assertIn("tavern_sun", engine.blackmail_ledger.player_knowledge,
                      msg="前置：知情已登记（对照组只差勒索与否）")
        contents = _long_contents(engine, "tavern_sun")
        self.assertTrue(contents,
                        msg="前置：灌醉链路记忆活跃（赠予/失态条目在）")
        self.assertFalse(any("拿住了" in c for c in contents),
                         msg="无勒索则长期记忆不得出现勒索 summary 关键词")

    def test_next_day_memory_reference_in_prompt(self):
        """③次晨跨对话引用：勒索 → 24×tick(60)（次晨 8:00 营业时段）→
        探询句（引用 summary 连续片段——中文分词按连续串，裸关键词
        跨串 token 重合为 0，沿 test_drunk_behavior MORNING_PROBE 纪律
        动态取片段）→ 慢脑 prompt【相关长期记忆】块含勒索 summary；
        对照组（同探询句、无勒索）块内零勒索关键词——「被要挟的事
        次日还被记起」的玩家可感知闭环。"""
        llm, engine = _fresh_engine()
        _make_informed(engine, "tavern_sun")
        engine.player_blackmails("tavern_sun")
        summary = next(r.content
                       for r in engine.npcs["tavern_sun"].memory.long.records
                       if "拿住了" in r.content)
        runs = [x for x in re.findall(r"[\w\u4e00-\u9fff]+", summary)
                if "拿住了" in x]
        self.assertTrue(runs,
                        msg="前置：summary 应含「拿住了」连续片段（契约"
                            "第三人称文案），实际: {summary!r}")
        probe = f"孙掌柜，昨儿的事我可听说了——{runs[0]}，到底怎么回事？"
        for _ in range(24):  # 8:00 → 次日 8:00（跨余波 TTL 到次晨）
            engine.tick(60)
        result = engine.player_says(probe, "tavern_sun")
        self.assertEqual(result["brain"], "slow",
                         msg="次晨营业时段长句应走慢脑产生 prompt")
        prompt = llm.captured[-1][1]["content"]
        block = extract_block(prompt, "【相关长期记忆】")
        self.assertIn(summary, block,
                      msg=f"次晨对话【相关长期记忆】块应含勒索 summary"
                          f"（长期直写后跨对话可检索），实际块内容: "
                          f"{block!r}")
        # 对照组：同探询句、唯一差异=无勒索 → 块内零勒索关键词
        llm2, engine2 = _fresh_engine()
        _make_informed(engine2, "tavern_sun")
        self.assertIn("tavern_sun", engine2.blackmail_ledger.player_knowledge,
                      msg="前置：对照组同样知情（只差勒索与否）")
        for _ in range(24):
            engine2.tick(60)
        control = engine2.player_says(probe, "tavern_sun")
        self.assertEqual(control["brain"], "slow",
                         msg="对照组同样走慢脑，保证 prompt 可比")
        control_block = extract_block(llm2.captured[-1][1]["content"],
                                      "【相关长期记忆】")
        self.assertNotIn("拿住了", control_block,
                         msg="对照组（无勒索）【相关长期记忆】块不应出现"
                             "勒索 summary 关键词")
        self.assertNotIn("把柄", control_block,
                         msg="对照组（无勒索）【相关长期记忆】块不应出现"
                             "把柄字样")
        # 排除假阴性：对照组记忆与慢脑链路真实在工作
        recent = extract_block(llm2.captured[-1][1]["content"],
                               "【最近的经历】")
        self.assertIn("玩家说：", recent,
                      msg=f"对照组【最近的经历】块应有说话短期记忆"
                          f"（链路活跃性前置），实际: {recent!r}")

    def test_leak_text_zero_memory_write(self):
        """④泄底防护（记忆通道）：醉话泄露事件文本零写入 victim 任何
        记忆（memory 无 secret_mumbled 模板=结构性保证）——醉话只有
        玩家听到，NPC 自己与旁人都不知道说了什么。"""
        _llm, engine = _fresh_engine()
        _make_informed(engine, "tavern_sun")
        leaks = _leak_events(engine)
        self.assertEqual(len(leaks), 1, msg="前置：醉话泄露真实发生")
        leak_text = leaks[0].payload["text"]
        contents = (_short_contents(engine, "tavern_sun")
                    + _long_contents(engine, "tavern_sun"))
        self.assertTrue(contents,
                        msg="前置：victim 记忆链路活跃（赠予/失态条目在）")
        self.assertFalse(any(leak_text in c for c in contents),
                         msg="醉话文本不得写入 victim 任何记忆"
                             "（泄底防护：memory 无该模板）")


# ========================================================================== #
# 6. TestBlackmailBoundaries —— 架构边界与纪律
# ========================================================================== #
class TestBlackmailBoundaries(unittest.TestCase):
    """勒索层架构边界：零 LLM 封印、8D 六情边界（无六情双写）、
    泄底防护（无勒索史 prompt 零把柄内容）。"""

    def test_full_blackmail_chain_zero_llm(self):
        """①发现→知情→要挟得手全链（含醉话泄露与双事件发布）零 LLM
        调用零 token——Tier 0 纯规则封印（对齐 WealthLedger/
        IntoxicationLedger 纪律；各步前置断言真实发生，非空转）。"""
        llm, engine = _fresh_engine()
        self.assertEqual(llm.call_count, 0, msg="前置：全新引擎零调用")
        _make_informed(engine, "tavern_sun")
        self.assertEqual(len(_leak_events(engine)), 1,
                         msg="前置：醉话泄露真实发生")
        self.assertEqual(llm.call_count, 0,
                         msg="发现链路（灌醉+泄露+知情登记）应零 LLM")
        result = engine.player_blackmails("tavern_sun")
        self.assertTrue(result["ok"], msg="前置：勒索结算真实发生")
        self.assertEqual(len(_blackmail_events(engine)), 1,
                         msg="前置：双事件真实发布")
        self.assertEqual(llm.call_count, 0,
                         msg="勒索结算应零 LLM 调用")
        self.assertEqual(llm.total_tokens_used, 0,
                         msg="勒索全链零 token")

    def test_blackmail_inner_state_matches_npc_action_channel_only(self):
        """②8D 六情边界（可区分强断言）：勒索调用的 8D 增量必须**恰
        等于**既有 npc_action 通道增量（参照增量经 StateUpdater.on_event
        公开订阅入口投递一条 npc_action 事件测得）——勒索结算自身的
        npc_action 双事件（契约规定）必然过既有通道（疲劳 +0.02/唤醒
        -0.01），任何**额外**的六情/PAD 双写都会使增量超出当场暴露。

        口径说明：任务书"勒索前后 8D 逐项相等"与契约"npc_action 双
        事件"内在冲突（见模块 docstring 契约疑点处置）——本断言是
        两条条款各自成立时的最强可满足形式。
        """
        _llm, engine = _fresh_engine()
        _make_informed(engine, "tavern_sun")
        npc = engine.npcs["tavern_sun"]
        before = dict(npc.inner_state.to_dict())
        result = engine.player_blackmails("tavern_sun")
        self.assertTrue(result["ok"], msg="前置：勒索结算真实发生")
        after = dict(npc.inner_state.to_dict())
        # 参照引擎：同 seed 全新构造，投递一条 npc_action 事件测既有
        # 通道增量（与总线分发同一订阅入口；无 clamp 边界差异——参照
        # 与主链路的初始 8D 同为创生默认值）
        _llm2, engine2 = _fresh_engine()
        npc2 = engine2.npcs["tavern_sun"]
        before2 = dict(npc2.inner_state.to_dict())
        npc2.state_updater.on_event(WorldEvent(
            0, "npc_action", "tavern_sun",
            {"npc": "tavern_sun", "summary": "（参照事件）"}))
        after2 = dict(npc2.inner_state.to_dict())
        for key in before:
            channel_delta = after2[key] - before2[key]
            self.assertAlmostEqual(
                after[key] - before[key], channel_delta, places=4,
                msg=f"勒索的 8D 增量应恰等于既有 npc_action 通道增量"
                    f"（{key}: 期望 {channel_delta}，实际 "
                    f"{after[key] - before[key]}——勒索不得六情双写）")

    def test_no_blackmail_history_prompts_no_secret_leak(self):
        """③泄底防护（无勒索史三核心）：chen/lily 正常寒暄 + tavern_sun
        知情未勒索（醉话已泄露但仍零把柄行）的慢脑 prompt（system+
        user 全量扫描）不含 SECRET_POOL 任何一条的 label 与 description
        ——知情≠余波，绝密把柄永不被 LLM 先知（沿 test_drunk_behavior
        P6 对抗预告条款口径）。"""
        llm, engine = _fresh_engine()
        _make_informed(engine, "tavern_sun")
        self.assertIn("tavern_sun", engine.blackmail_ledger.player_knowledge,
                      msg="前置：tavern_sun 已知情（泄底断言在知情态下检查）")
        dialogues = (("chen", SLOW_ASK_CHEN),
                     ("lily", SLOW_ASK_LILY),
                     ("tavern_sun", SLOW_ASK_TAVERN))
        for npc_id, ask in dialogues:
            self.assertGreaterEqual(
                len(engine.npcs[npc_id].tag_ledger.secrets()), 1,
                msg=f"前置：{npc_id} 创生账本应挂 ≥1 绝密把柄"
                    f"（泄底断言非空转）")
            result = engine.player_says(ask, npc_id)
            self.assertEqual(result["brain"], "slow",
                             msg=f"{npc_id} 长句应走慢脑（前置）")
        self.assertGreaterEqual(len(llm.captured), 3,
                                msg="三次慢脑对话应捕获 ≥3 组 messages")
        all_prompts = [message["content"] for pair in llm.captured
                       for message in pair]
        _assert_no_secret_leak(self, all_prompts)


# ========================================================================== #
# 7. TestLeveragedTTL —— 余波生命周期（24h TTL 泵）
# ========================================================================== #
class TestLeveragedTTL(unittest.TestCase):
    """余波 24 世界小时到期熄灭：is_leveraged/leverage_line/prompt 三
    出口同步恢复；already_leveraged 随之解除（可再勒索，封口物取剩余
    库存最高者）；知情保留（player_knowledge 不随 TTL 清除）。"""

    def test_leverage_expires_after_ttl_and_re_blackmail(self):
        """①勒索后跨 23 个小时界仍余波，第 24 个小时界熄灭（LEVERAGED_
        TTL_HOURS 常量口径，同 hour 判重泵与烂醉共用）→ leverage_line
        空串、prompt 恢复零"【把柄】"→ 再勒索 ok=True（already_leveraged
        解除，hush_money=="酱肉"——库存剩酱肉估值 1）→ 知情保留。"""
        _llm, engine = _fresh_engine()
        _make_informed(engine, "tavern_sun")
        first = engine.player_blackmails("tavern_sun")
        self.assertTrue(first["ok"], msg="前置：首次勒索成功")
        self.assertTrue(is_leveraged(engine.npcs["tavern_sun"]),
                        msg="前置：余波期已生效")
        # 跨 (TTL-1) 个小时界仍余波（若 TTL 误为 23 或泵双发则此刻已灭）
        for _ in range(LEVERAGED_TTL_HOURS - 1):
            engine.tick(60)
        self.assertTrue(is_leveraged(engine.npcs["tavern_sun"]),
                        msg=f"跨 {LEVERAGED_TTL_HOURS - 1} 个小时界"
                            f"应仍处于余波期")
        # 第 TTL 个小时界熄灭
        engine.tick(60)
        self.assertFalse(is_leveraged(engine.npcs["tavern_sun"]),
                         msg=f"跨满 {LEVERAGED_TTL_HOURS} 个小时界余波"
                             f"应自动熄灭")
        self.assertEqual(leverage_line(engine.npcs["tavern_sun"]), "",
                         msg="熄灭后 leverage_line 应为空串")
        self.assertFalse(
            any(t.tag_id == LEVERAGED_TAG_ID
                for t in engine.npcs["tavern_sun"].tag_ledger.all_tags()),
            msg="熄灭后账本不应再含余波标签")
        # prompt 恢复零把柄行（次晨 8:00 营业时段慢脑可达；【相关长期
        # 记忆】可能合法含 summary（含"把柄"字样）——断言锁定带括号
        # 的【把柄】行标记与余波标签，不误伤记忆引用）
        result = engine.player_says(SLOW_ASK_TAVERN, "tavern_sun")
        self.assertEqual(result["brain"], "slow",
                         msg="次晨营业时段长句应走慢脑（前置）")
        prompt = _llm.captured[-1][1]["content"]
        self.assertNotIn("【把柄】", prompt,
                         msg="余波熄灭后 prompt 应恢复零【把柄】行")
        self.assertNotIn(LEVERAGED_LABEL, prompt,
                         msg=f"余波熄灭后 prompt 不得含 {LEVERAGED_LABEL!r}")
        # 知情保留：TTL 只管余波，不清筹码
        self.assertIn("tavern_sun", engine.blackmail_ledger.player_knowledge,
                      msg="余波熄灭后知情应保留（player_knowledge 不清除）")
        # 再勒索：already_leveraged 解除，封口物取剩余库存最高者
        second = engine.player_blackmails("tavern_sun")
        self.assertTrue(second["ok"],
                        msg="余波熄灭后可再勒索（already_leveraged 解除）")
        self.assertEqual(second["hush_money"], "酱肉",
                         msg="再勒索封口物应为剩余库存估值最高者酱肉"
                             "（米酒已取走）")
        self.assertTrue(is_leveraged(engine.npcs["tavern_sun"]),
                        msg="再勒索后进入新一轮余波期")


if __name__ == "__main__":
    unittest.main()

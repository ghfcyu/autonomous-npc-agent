"""T6 第二小步：勒索最小闭环（醉话半泄露知情登记 + 要挟结算 + 余波）。

三条纪律（模块存在依据，tests/test_blackmail.py ast 断言守护）
----------------------------------------------------------------
1. 六情边界（共线性死穴）——勒索判定的情绪语义严禁 import/修改任何
   8D 心智参数：同一事件双份情绪增量 = 两套情绪系统漂移的共线性
   死穴（economics.WealthLedger / intoxicication.IntoxicationLedger
   同一封印口径）。本模块零 inner_state 引用，敌意的表达走关系网
   affinity 离散值（relationships.update_affinity）与文案池，不走
   情绪参数。
2. 纸面哲学砍除——知情即筹码是纯数据层 set（player_knowledge），
   不建"怀疑度/信任崩塌曲线"；勒索结算全 Tier 0 确定性：守卫链
   顺序即语义，无概率掷骰、无 LLM 参与成败判定。封口物取估值最高
   者经 estimate_value 单一估值源（禁止发明第二张价目表）。
3. Tier 0 纯规则零 token——不组装 prompt、不持有 LLM 引用、不进快脑
   意图域。对决策链的唯一出口是 leverage_line() 的离散【把柄】行
   （对齐 WealthLedger.fortune_tag 签名先例——decision 层显式注入，
   空串时 prompt 逐字节不变）；对文案层的唯一出口是
   engine.player_says 垫话经 is_leveraged 消费 HOSTILE_PREFIXES
   敌意前缀（filler.py 复用背景层同一文案池，零平行机制）。

与 IntoxicationLedger 的结构对齐（T6 第一小步先例）
------------------------------------------------------------
- EventBus 订阅者：on_event 只消费 secret_mumbled（engine 按
  kinds 订阅，守卫双保险）；发布方（IntoxicationLedger._settle
  第 5 步醉话半泄露）已判定玩家同地在场，订阅侧不再重复判定。
- world 引用 + 核心 NPC 注册表引用（engine.npcs 的同一 dict 对象
  ——构造先于 spawn，事件到来时注册表已填充）+ 依赖注入
  （WealthLedger / RelationshipNetwork 均为引擎既有组件，零新建
  全局状态）。
- 余波标签走 TagLedger.add_transient 既有瞬态管道（TTL 24 小时，
  与烂醉 4 小时共用 tick 泵）；secret=True 不进 visible_tags——
  隐秘心理走 decision 层 leverage_line 显式注入通道，与烂醉公开
  体态走自动管道两标签两通道各归其位。

泄底防护（结构性保证，沿 tag_mount 模块 docstring 设计意图）
------------------------------------------------------------
知情 ≠ 余波：未勒索的 NPC 不知道玩家知道——player_knowledge 只
登记"玩家听到了醉话"，【把柄】行仅在余波标签挂载后才出现；未
勒索/已熄灭的 NPC prompt 零把柄内容，绝密把柄永不被 LLM 先知。

默认村庄零行为：无赠饮 → 无醉话 → 知情恒空 → 【把柄】行恒不
出现 → prompt 逐字节不变（token 基线 4519 不动）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from .economics import estimate_value
from .tag_mount import MountedTag, TagPhase
from .world import WorldEvent

if TYPE_CHECKING:  # 仅为类型注解服务，避免运行期循环导入
    from .economics import WealthLedger
    from .npc import NPC
    from .relationships import RelationshipNetwork
    from .world import World

LEVERAGED_TAG_ID = "state_leveraged"
LEVERAGED_LABEL = "把柄被攥"
LEVERAGED_TTL_HOURS = 24
LEVERAGE_SUFFIX = "（命门被攥，惊惧交加）"   # 余波【把柄】行括注（与 leverage_line 同一来源）
NO_LEVERAGE_REPLY = "「你在说什么？老听不明白。」"            # 无把柄回绝句
ALREADY_LEVERAGED_REPLY = "「值钱的你都拿走了，还想怎样？」"   # 余波期重复勒索回绝句
BLACKMAIL_REPLY_WITH = "「{item}拿去……这事，天知地知，你知我知。」"   # 成交回复（有封口物）
BLACKMAIL_REPLY_EMPTY = "「值钱的都给你了……你、你嘴下留情。」"       # 成交回复（口袋空空）
BLACKMAIL_ACTION_WITH = "（脸色煞白，咬牙交出了{item}）"      # 成交动作事件文案（有封口物）
BLACKMAIL_ACTION_EMPTY = "（缩着脖子，两手一摊，冷汗直流）"   # 成交动作事件文案（口袋空空）


def is_leveraged(npc) -> bool:
    """该 NPC 当前处于勒索余波期：账本 all_tags() 存在 state_leveraged。

    无账本（直接构造/未挂 genesis 账本的 NPC）→ False；只用 TagLedger
    公开 API all_tags()，不触内部桶（与 is_dead_drunk 同一封印口径）。
    """
    ledger = getattr(npc, "tag_ledger", None)
    if ledger is None:
        return False
    return any(tag.tag_id == LEVERAGED_TAG_ID for tag in ledger.all_tags())


def leverage_line(npc) -> str:
    """余波期【把柄】行（decision 层消费，对齐 fortune_tag 签名先例）。

    1. tag_ledger 为 None → ""（无账本零注入）；
    2. all_tags() 无 state_leveraged → ""（未勒索/已熄灭——知情≠余波，
       未勒索的 NPC 不知道玩家知道，prompt 零把柄行=泄底防护）；
    3. 命中 → 从 secrets() 中取 phase==TagPhase.INNATE 的第一条把柄
       label，返回"【把柄】{label}（命门被攥，惊惧交加）"（无换行；
       实测 19 字符 =【把柄】4 + label ≤4 + 括注 11，提案 3 节"20 字符"
       为预估口径勘误为实测 19——最坏叠加烂醉标签 +5+19=24 字符仍在
       <35token 红线内；浮点/参数表严禁进 prompt，<35token 纪律）；
    4. secrets() 无 INNATE 把柄（理论不可达，防御性）→ ""。
    """
    ledger = getattr(npc, "tag_ledger", None)
    if ledger is None:
        return ""
    if not is_leveraged(npc):
        return ""
    innate_secrets = [s for s in ledger.secrets() if s.phase == TagPhase.INNATE]
    if not innate_secrets:
        return ""
    return f"【把柄】{innate_secrets[0].label}{LEVERAGE_SUFFIX}"


class BlackmailLedger:
    """勒索账本：知情登记（事件订阅）+ 勒索结算（Tier 0 纯规则，零 token）。

    结构对齐 IntoxicationLedger：world 引用 + 核心 NPC 注册表引用
    （engine.npcs 的同一 dict 对象——构造先于 spawn，事件到来时注册表
    已填充）+ 依赖注入（WealthLedger / RelationshipNetwork）。两个内部
    结构：

    - player_knowledge：知情 npc_id 集合（知情即筹码，纯数据层 set——
      唯一写入通道是 secret_mumbled 醉话半泄露事件，零概率零 LLM）；
    - _settle_counts：npc_id → 勒索结算计数（保留字段，两分支文案无
      轮转，供未来扩充——当前无消费点，不做无消费的记账操作）。

    纪律：严禁 import/修改任何 8D 心智参数（六情边界见模块 docstring）；
    严禁组装 prompt——勒索余波对决策链的唯一出口是 leverage_line 的
    离散【把柄】行（decision 层显式注入），敌意文案出口是垫话层
    HOSTILE_PREFIXES 复用（engine.player_says 传 is_leveraged）。
    """

    def __init__(self, world: "World", npcs: Dict[str, "NPC"],
                 wealth_ledger: "WealthLedger",
                 relationships: "RelationshipNetwork") -> None:
        self._world = world
        self._npcs = npcs
        self._wealth_ledger = wealth_ledger
        self._relationships = relationships
        self.player_knowledge: set = set()      # 知情 npc_id 集合（知情即筹码，纯数据层）
        self._settle_counts: Dict[str, int] = {}  # （保留字段，两分支文案无轮转，供未来扩充）

    def on_event(self, event: WorldEvent) -> None:
        """EventBus 订阅入口：只消费 secret_mumbled（engine 按 kinds 订阅，
        守卫双保险）。

        kind 非 secret_mumbled → return；payload["npc"] 在核心注册表 →
        player_knowledge.add(npc_id)。发布方（IntoxicationLedger._settle
        第 5 步）已判定玩家同地在场，订阅侧不再重复判定；背景 NPC 不在
        engine.npcs（其醉话即便泄露也不进勒索域，天然守卫）。
        """
        if event.kind != "secret_mumbled":
            return
        npc_id = event.payload.get("npc")
        if npc_id in self._npcs:
            self.player_knowledge.add(npc_id)

    def blackmail(self, npc_id: str) -> dict:
        """勒索结算主入口（engine.player_blackmails 薄壳调用）。

        守卫链（顺序即语义，对齐 player_buys 拒绝先例）：
        1. npc_id 不在核心注册表 → unknown npc；
        2. npc_id 不在 player_knowledge → no_leverage（玩家没听到过
           醉话，NPC 无把柄可被拿：不知情者无筹码）；
        3. tag_ledger 为 None 或 secrets() 无 INNATE 把柄 → no_leverage
           （知情但把柄已不存在——unmount 剥夺场景双保险）；
        4. is_leveraged → already_leveraged（余波期重复勒索确定性回绝；
           知情保留不清除——余波 24h 熄灭后可再勒索，复用 TTL 生命周期）。

        结算四件套 + 双事件（Tier 0 确定性，详见各步注释）。
        """
        if npc_id not in self._npcs:
            return {"ok": False, "reason": "unknown npc"}
        if npc_id not in self.player_knowledge:
            return {"ok": False, "reason": "no_leverage",
                    "reply": NO_LEVERAGE_REPLY}
        victim = self._npcs[npc_id]
        ledger = getattr(victim, "tag_ledger", None)
        innate_secrets = ([s for s in ledger.secrets()
                          if s.phase == TagPhase.INNATE]
                         if ledger is not None else [])
        if not innate_secrets:
            return {"ok": False, "reason": "no_leverage",
                    "reply": NO_LEVERAGE_REPLY}
        if is_leveraged(victim):
            return {"ok": False, "reason": "already_leveraged",
                    "reply": ALREADY_LEVERAGED_REPLY}
        # ① 封口物：库存非空 → 估值最高者手动转移给玩家（稳定：同分取
        #    首项；沿 player_buys 手动转移防双事件先例，绝不走
        #    transfer_item——它会发布 item_given 造成双事件双记账）；
        #    库存空 → item=None（诚实登记不发明数字）。
        src = self._world.entities[npc_id]
        if src.inventory:
            item = max(src.inventory, key=estimate_value)
            src.inventory.remove(item)
            self._world.entities["player"].inventory.append(item)
        else:
            item = None
        # ② 封口物被夺是 NPC 真实损失：apply_loss 与交易损失同口径
        #    （损失域基准零吸收，联动"最近手头紧"标签零新机制）；item
        #    为 None 时跳过——口袋空空不产生账面损失。
        if item is not None:
            self._wealth_ledger.apply_loss(npc_id, estimate_value(item))
        # ③ 敌意边 -1.0（提案冻结版 delta 语义：一次勒索 -1.0，N 次酒桌
        #    交情也抵不住——但默认场景知情必经赠酒×3，六情"好"矢量先行
        #    +0.3（0.5→0.8），勒索后落 -0.2 弱可达敌意渲染带 <-0.3（已知
        #    边界，B 子代理实测发现：敌意渲染出口由垫话冷脸+【把柄】行
        #    双通道覆盖，敌意 floor 语义留待后续裁定，PM 10-17 登记不修补）；
        #    玩家不灌酒场景（最小注入引擎）0.5→-0.5 正常入带）。
        self._relationships.update_affinity(npc_id, "player", -1.0)
        # ④ 余波标签：TRANSIENT 瞬态 TTL 24 世界小时，secret=True 不进
        #    visible_tags——隐秘心理走 leverage_line 显式注入通道（与
        #    烂醉公开体态走自动管道两标签两通道各归其位）。
        ledger.add_transient(
            MountedTag(tag_id=LEVERAGED_TAG_ID, label=LEVERAGED_LABEL,
                       phase=TagPhase.TRANSIENT, secret=True),
            LEVERAGED_TTL_HOURS)
        # ⑤ 双事件发布（第三人称中立 summary，victim 与目击者双路径
        #    同文案；{player}=玩家实体名"旅行者"）。
        player = self._world.entities["player"].name
        name = victim.persona.name
        if item is not None:
            summary_text = (f"{player}拿住了{name}的把柄，"
                            f"{name}交出了{item}当封口物")
            action_text = BLACKMAIL_ACTION_WITH.format(item=item)
            reply_text = BLACKMAIL_REPLY_WITH.format(item=item)
        else:
            summary_text = f"{player}拿住了{name}的把柄，{name}掏空了口袋求饶"
            action_text = BLACKMAIL_ACTION_EMPTY
            reply_text = BLACKMAIL_REPLY_EMPTY
        self._world.bus.publish(WorldEvent(
            self._world.tick_count, "blackmail", "player",
            {"victim": npc_id, "summary": summary_text, "hush_money": item}))
        self._world.bus.publish(WorldEvent(
            self._world.tick_count, "npc_action", npc_id,
            {"npc": npc_id, "summary": action_text}))
        return {"ok": True, "npc": name, "npc_id": npc_id,
                "hush_money": item, "reply": reply_text,
                "affinity": self._relationships.get_relation_to(
                    npc_id, "player").affinity}

"""T2 标签库第二批 · 标签挂载层：缺陷+把柄强制挂载与四时态账本。

规范依据：docs/NPC_TAG_DATABASE.md 5.1/5.2（必选挂载）、6.1（四时态）、
6.2（互斥锁）、7.7（把柄库）、3.1（金字塔频度法则）。

分档规则（书面记录，出处：审查指令 + 规范 3.1 金字塔频度法则）：
- 显性缺陷 uncommon 起步：抽 tier 时从 tag_genesis.PYRAMID_TIERS 取权重，
  剔除 common 档后按剩余权重重归一化加权抽样（缺陷是戏剧性来源，太普通
  的缺陷玩家记不住）；
- 绝密把柄 rare 起步：仅保留 rare/extreme 两档重归一化（把柄越稀有，
  勒索价值越高，7.7 节"核心玩法博弈"语义）。
- 正交纪律：本模块严禁复制/新造分布参数，档位权重全部 import 引用
  tag_genesis.PYRAMID_TIERS（挂载层只消费生成器产物，T4 复用时零改动）。

把柄不进决策上下文的设计理由（书面记录）：
- 绝密语义：把柄是玩家的博弈筹码，NPC 若在决策 prompt 里"知道"自己的
  把柄，等于系统向 LLM 泄底，勒索叙事被破坏（T6 勒索玩法埋点）；
- 纸面哲学砍除纪律：不可被玩家感知的机制不注入 prompt。TagLedger 的
  visible_tags()（排除 secret=True）是决策层唯一消费入口，保证只渲染
  显性标签；把柄仅经 secrets() 供玩法层读取。

与规范的偏离说明（PM 裁定口径）：
1. SECRET_POOL 五条采用规范 5.2 节罪行清单（杀人/私铸/私生/勾结/亏空），
   其中"勾结外敌""巨额亏空"在 7.7 节无对应 SECRET_* 条目，以 5.2 为准；
2. 互斥锁消除允许命中 INNATE 桶的对立标签：规范 6.2 mermaid
   "获得后天标签→互斥锁检验→自动消除对立标签"是生命周期机制，凌驾
   "先天印记不可移除"保护——否则 5.2 强制挂载进 INNATE 桶的
   flaw_stingy 与后天 virtue_generous 永远无法互斥，机制沦为纸面摆设。
   unmount() 显式剥夺 API 仍拒绝 INNATE（抛 ValueError）；
3. FLAW_POOL 七条之间均匀抽取（规范未给缺陷间频度），金字塔频度仅
   用于 tier 分档，不用于缺陷选择；
4. "同一 NPC 不重复抽到同一缺陷"（任务书要求）对称扩展到把柄：重复
   调用 mount_flaw_and_secret 时已挂载的缺陷/把柄均从候选池剔除，
   增量挂载不产生重复条目。

零第三方依赖，仅 enum/random/dataclasses/typing 标准库。
"""
from __future__ import annotations

import enum
import random
from dataclasses import dataclass, replace
from typing import Optional

from .tag_genesis import PYRAMID_TIERS, attribute_label, beauty_label


class TagPhase(enum.Enum):
    """标签四时态（规范 6.1）。"""

    INNATE = "innate"          # 先天印记：创生时烙定，不可显式剥夺
    ACQUIRED = "acquired"      # 后天经历：事件驱动获得，触发互斥锁
    TRANSIENT = "transient"    # 瞬态情境：带 TTL，tick 耗尽自动注销
    RELATIONAL = "relational"  # 关系羁绊：依附 scope（如 "player"）


# 标签池：每条 = (tag_id, 中文名, 简述)，提取自规范 5.2/7.7 节。
FLAW_POOL: tuple[tuple[str, str, str], ...] = (
    ("flaw_missing_fingers", "断指", "右手断指，弓箭与精细工艺大打折扣"),
    ("flaw_limp", "跛足", "跛足瘸腿，行走迟缓"),
    ("flaw_smoker", "烟瘾", "烟斗不离手，工歇必摸烟斗深吸一口"),
    ("flaw_alcoholic", "贪杯", "嗜酒如命，不喝则手抖心慌"),
    ("flaw_rat_phobia", "恐鼠", "魁梧大汉见鼠也吓瘫，惊叫失措"),
    ("flaw_stingy", "极度吝啬", "一文钱掰成两半花"),
    ("flaw_compulsive_washer", "强迫洁癖", "触摸脏污后疯狂搓手"),
)

SECRET_POOL: tuple[tuple[str, str, str], ...] = (
    ("secret_murderer", "暗夜杀人", "曾在枯井掐死同僚，可被骨骸信物勒索"),
    ("secret_forged_coin", "私铸劣币", "地窖熔铅铸假币，告发即绞刑"),
    ("secret_illegitimate_heir", "私生血统", "抚养的农女实为先皇独女"),
    ("secret_collusion", "勾结外敌", "暗通敌国，传递边防布防图"),
    ("secret_deficit", "巨额亏空", "账面亏空巨款，东窗事发即家破人亡"),
)

# 互斥锁（规范 6.2 三组，按 tag_id 对）：后天获取标签时自动消除对立标签。
MUTEX_RULES: tuple[tuple[str, str], ...] = (
    ("flaw_stingy", "virtue_generous"),        # 极度吝啬 ⇔ 慷慨散财
    ("morality_saint", "secret_murderer"),     # 良知圣人 ⇔ 暗夜凶手
    ("beauty_stunning", "beauty_horrifying"),  # 倾国倾城 ⇔ 面目可怖
)

_MUTEX_OPPOSITES: dict[str, str] = {}
for _left, _right in MUTEX_RULES:
    _MUTEX_OPPOSITES[_left] = _right
    _MUTEX_OPPOSITES[_right] = _left

# 分档白名单（任务书分档规则，重归一化见 _sample_tier）：
#   缺陷 uncommon 起步：剔除 common 后按剩余金字塔权重归一化；
#   把柄 rare 起步：仅保留 rare/extreme 归一化。
_FLAW_TIERS: tuple[str, ...] = ("uncommon", "rare", "extreme")
_SECRET_TIERS: tuple[str, ...] = ("rare", "extreme")


@dataclass(frozen=True)
class MountedTag:
    """一条已挂载标签（不可变值对象）。

    tier：金字塔分档 id（tag_genesis.PYRAMID_TIERS 首列）。先天属性
    称号类标签可为 None——它们是连续属性分档产物，不是金字塔抽样产物。
    scope：仅 RELATIONAL 时有意义，依附对象（如 "player"）。
    """

    tag_id: str
    label: str
    phase: TagPhase
    tier: Optional[str] = None
    secret: bool = False
    scope: str = ""


def _pyramid_weight(tier_id: str) -> float:
    """从 tag_genesis.PYRAMID_TIERS 查档位权重（import 引用，不复制数字）。"""
    for tid, _name, weight in PYRAMID_TIERS:
        if tid == tier_id:
            return weight
    raise ValueError(f"未知金字塔档位: {tier_id}")


def _sample_tier(rng: random.Random, allowed: tuple[str, ...]) -> str:
    """白名单档位内按金字塔权重重归一化抽样（累积分布法）。

    权重全部 import 自 tag_genesis.PYRAMID_TIERS：剔除被禁档位后按
    剩余权重比例归一化，保证罕见档仍比进阶档稀少（金字塔序不破坏）。
    """
    weights = [(tid, _pyramid_weight(tid)) for tid in allowed]
    threshold = rng.random() * sum(weight for _tid, weight in weights)
    cumulative = 0.0
    for tid, weight in weights:
        cumulative += weight
        if threshold < cumulative:
            return tid
    return allowed[-1]


def _innate_mutex_ids(innate) -> set[str]:
    """从先天属性推导互斥预检 id（公开 API 端点探测档位，不复制阈值）。

    - morality 传奇档（attribute_label 与 10.0 端点同文案）→ morality_saint；
    - beauty 顶级/底级（beauty_label 与 10.0/1.0 端点同文案）→
      beauty_stunning / beauty_horrifying。
    """
    ids: set[str] = set()
    if attribute_label("morality", innate.morality) == attribute_label("morality", 10.0):
        ids.add("morality_saint")
    beauty = beauty_label(innate.beauty)
    if beauty == beauty_label(10.0):
        ids.add("beauty_stunning")
    elif beauty == beauty_label(1.0):
        ids.add("beauty_horrifying")
    return ids


class TagLedger:
    """每 NPC 一本标签账：四时态分桶存储 + 瞬态 TTL 独立计数器。

    移除保护语义（书面说明）：
    - unmount()（显式剥夺 API，未来"剥夺标签"玩法入口）拒绝 INNATE
      标签并抛 ValueError。选择抛错而非静默拒绝并记录：显式契约让
      调用方当场知道先天印记不可剥夺；静默拒绝会把玩法级 bug 吞掉。
    - 互斥锁自动消除（acquire 触发）是唯一能移除 INNATE 标签的通道
      ——生命周期机制（规范 6.2 mermaid）凌驾移除保护，见模块
      docstring 偏离 2。
    """

    def __init__(self) -> None:
        # 四时态分桶：dict[tag_id → MountedTag]（保插入序；同 id 重挂=覆盖）
        self._buckets: dict[TagPhase, dict[str, MountedTag]] = {
            phase: {} for phase in TagPhase
        }
        # 瞬态 TTL 独立计数器：tag_id → 剩余 tick 数
        self._ttl: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # 创生挂载（规范 5.2 必选挂载）
    # ------------------------------------------------------------------ #
    def mount_flaw_and_secret(self, rng: random.Random,
                              innate=None) -> tuple[MountedTag, ...]:
        """非背景 NPC 强制挂载 ≥1 显性缺陷 + ≥1 绝密把柄（规范 5.2）。

        - 分档：缺陷 uncommon 起步 / 把柄 rare 起步（_sample_tier 重归一化）；
        - 桶位：缺陷与把柄都是创生时印记，进 INNATE 桶；
        - 去重：已挂载的缺陷/把柄从候选剔除（同一 NPC 不重复抽到）；
        - 互斥预检：账本已挂标签 id 与 innate 属性推导出的互斥 id 共同
          构成黑名单——候选自身在黑名单、或候选的对立标签在黑名单时，
          该候选被剔除（圣人道德不与暗夜杀人并存）。innate=None 时仅做
          账本级预检（独立调用/测试场景）。

        返回本次新挂载的标签元组（缺陷在前，把柄在后）。
        """
        blacklist = {tag.tag_id for bucket in self._buckets.values()
                     for tag in bucket.values()}
        if innate is not None:
            blacklist |= _innate_mutex_ids(innate)
        mounted: list[MountedTag] = []

        flaw_candidates = [
            flaw for flaw in FLAW_POOL
            if flaw[0] not in blacklist
            and _MUTEX_OPPOSITES.get(flaw[0]) not in blacklist
        ]
        if flaw_candidates:
            tag_id, label, _desc = rng.choice(flaw_candidates)
            tag = MountedTag(tag_id=tag_id, label=label, phase=TagPhase.INNATE,
                             tier=_sample_tier(rng, _FLAW_TIERS))
            self.add_innate(tag)
            mounted.append(tag)

        secret_candidates = [
            secret for secret in SECRET_POOL
            if secret[0] not in blacklist
            and _MUTEX_OPPOSITES.get(secret[0]) not in blacklist
        ]
        if secret_candidates:
            tag_id, label, _desc = rng.choice(secret_candidates)
            tag = MountedTag(tag_id=tag_id, label=label, phase=TagPhase.INNATE,
                             tier=_sample_tier(rng, _SECRET_TIERS), secret=True)
            self.add_innate(tag)
            mounted.append(tag)
        return tuple(mounted)

    # ------------------------------------------------------------------ #
    # 四时态写入 API
    # ------------------------------------------------------------------ #
    def add_innate(self, tag: MountedTag) -> MountedTag:
        """挂入 INNATE 桶（先天印记，创生语义；挂载后不可显式剥夺）。

        不触发互斥锁：先天挂载的一致性由调用方经 mount_flaw_and_secret
        的候选预检保证（创生顺序不可知时强行自动消除会破坏印记语义）。
        """
        tag = replace(tag, phase=TagPhase.INNATE, scope="")
        self._buckets[TagPhase.INNATE][tag.tag_id] = tag
        return tag

    def acquire(self, tag: MountedTag) -> list[MountedTag]:
        """后天事件驱动获得标签（规范 6.1 后天经历 + 6.2 互斥锁）。

        触发互斥锁：若对立标签已在账本任意桶，自动消除之（含 INNATE，
        见模块 docstring 偏离 2）。返回被消除的对立标签列表；
        无互斥冲突时返回空列表。
        """
        tag = replace(tag, phase=TagPhase.ACQUIRED, scope="")
        removed: list[MountedTag] = []
        opponent_id = _MUTEX_OPPOSITES.get(tag.tag_id)
        if opponent_id is not None:
            for bucket in self._buckets.values():
                opponent = bucket.pop(opponent_id, None)
                if opponent is not None:
                    removed.append(opponent)
            self._ttl.pop(opponent_id, None)
        self._buckets[TagPhase.ACQUIRED][tag.tag_id] = tag
        return removed

    def add_transient(self, tag: MountedTag, ttl_ticks: int) -> MountedTag:
        """挂入 TRANSIENT 桶并登记 TTL（规范 6.1 瞬态情境，带生存时效）。

        同 tag_id 重复挂载 = 覆盖并重置 TTL（又一轮买醉刷新醉酒时长）。
        """
        tag = replace(tag, phase=TagPhase.TRANSIENT, scope="")
        self._buckets[TagPhase.TRANSIENT][tag.tag_id] = tag
        self._ttl[tag.tag_id] = ttl_ticks
        return tag

    def add_relational(self, tag: MountedTag, scope: str) -> MountedTag:
        """挂入 RELATIONAL 桶并绑定 scope（规范 6.1 关系羁绊，依附对象）。"""
        tag = replace(tag, phase=TagPhase.RELATIONAL, scope=scope)
        self._buckets[TagPhase.RELATIONAL][tag.tag_id] = tag
        return tag

    # ------------------------------------------------------------------ #
    # 生命周期推进与查询
    # ------------------------------------------------------------------ #
    def tick(self) -> tuple[MountedTag, ...]:
        """世界推进一步：瞬态 TTL 减一，归零自动注销（规范 6.1）。

        返回本次因 TTL 耗尽而注销的瞬态标签（按挂载序）。
        """
        expired: list[MountedTag] = []
        for tag_id in list(self._ttl):
            self._ttl[tag_id] -= 1
            if self._ttl[tag_id] <= 0:
                del self._ttl[tag_id]
                tag = self._buckets[TagPhase.TRANSIENT].pop(tag_id, None)
                if tag is not None:
                    expired.append(tag)
        return tuple(expired)

    def visible_tags(self) -> tuple[MountedTag, ...]:
        """玩家可见的显性标签（按 INNATE→ACQUIRED→TRANSIENT→RELATIONAL 桶序）。

        绝密把柄（secret=True）永不出现——本方法是决策层唯一消费入口，
        保证把柄不进决策上下文（见模块 docstring）。
        """
        return tuple(
            tag
            for phase in TagPhase
            for tag in self._buckets[phase].values()
            if not tag.secret
        )

    def secrets(self) -> tuple[MountedTag, ...]:
        """绝密把柄列表（仅 T6 勒索等玩法可消费，不进决策上下文）。"""
        return tuple(
            tag
            for bucket in self._buckets.values()
            for tag in bucket.values()
            if tag.secret
        )

    def all_tags(self) -> tuple[MountedTag, ...]:
        """全部已挂载标签（测试/调试/存档口径，含绝密）。"""
        return tuple(
            tag
            for phase in TagPhase
            for tag in self._buckets[phase].values()
        )

    def unmount(self, tag_id: str) -> bool:
        """显式剥夺一条标签（未来"剥夺标签"玩法入口，规范 6.1 衰减与稀释）。

        INNATE 先天印记不可剥夺：抛 ValueError（见类 docstring 移除保护）。
        ACQUIRED/TRANSIENT/RELATIONAL 可剥夺；tag_id 不存在返回 False。
        互斥锁内部消除不走本方法（生命周期机制允许命中 INNATE）。
        """
        if tag_id in self._buckets[TagPhase.INNATE]:
            raise ValueError(f"先天印记不可剥夺: {tag_id}")
        removed = False
        for phase in (TagPhase.ACQUIRED, TagPhase.TRANSIENT, TagPhase.RELATIONAL):
            if self._buckets[phase].pop(tag_id, None) is not None:
                removed = True
        self._ttl.pop(tag_id, None)
        return removed

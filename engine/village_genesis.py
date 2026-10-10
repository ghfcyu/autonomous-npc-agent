"""T4 人口学村庄生成器第一小步：25 人花名册与离线人口学生成。

- 25 人花名册按齐普夫职业阶层（提案 A 人口学版）：farming 7 / hunt 1 /
  craft 5 / trade 2 / medicine 2 / tavern 2 / education 2 / guard 2 / idle 2；
- 关键 NPC 2 人（chen/lily，8D 全量，运行时由既有 InnerState 承载）
  vs 环境 NPC 23 人（4D 压缩稳态 EnvMind，Tier 0/1 封印零 LLM）；
- 本模块为离线创生层：产物（先天属性/EnvMind/账本摘要）不进运行时
  prompt，<35token 红线天然不涉及；
- 单一挂载内核纪律：缺陷+把柄+高显著度称号挂载全部复用
  tag_mount.build_genesis_ledger，严禁在本模块复制挂载逻辑
  （两套挂载逻辑漂移=共线性死穴）。

零第三方依赖，仅 os/random/zlib/json/dataclasses/typing 标准库。
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from typing import Optional

from .tag_genesis import (InnateAttributes, attribute_label,
                          deterministic_seed, generate_innate_attributes)
from .tag_mount import (FLAW_POOL, SECRET_POOL, MountedTag, TagLedger,
                        build_genesis_ledger)

# 齐普夫职业阶层表（表本身为分布断言对象）：合计 25。
VILLAGE_SECTORS: dict[str, int] = {
    "farming": 7, "hunt": 1, "craft": 5, "trade": 2,
    "medicine": 2, "tavern": 2, "education": 2, "guard": 2, "idle": 2,
}

# 新 NPC 配置目录（T4-a 花名册落地）：15 份新环境 NPC JSON。
NEWCOMER_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "configs", "village_roster")

# 花名册引用、T4-a 待创建的地点 id（14 个；既有 17 地点之外的引用清单）。
NEW_LOCATION_IDS: tuple[str, ...] = (
    "pasture", "mill", "carpentry", "school", "shrine",
    "house_cao", "house_herder", "house_miller", "house_carpenter",
    "house_cobbler", "house_peddler", "house_watchman", "house_yi", "house_school",
)


@dataclass(frozen=True)
class VillagerSeed:
    npc_id: str      # 如 "chen" / "herder_ma"
    name: str        # 中文名
    kind: str        # "key" | "env"
    sector: str      # VILLAGE_SECTORS 键之一
    occupation: str  # 职业中文名（叙事层）


# 25 人花名册（PM 世界观设定，人名与阶层归属不得改动）：
# key 2 人（既有引擎核心）+ env 23 人（farming 7 / hunt 1 / craft 4 /
# trade 1 / medicine 2 / tavern 2 / education 2 / guard 2 / idle 2）。
# sector 总计数（key 计入所属 sector）：craft 5 = 4 env + chen；
# trade 2 = 1 env + lily —— 与 VILLAGE_SECTORS 逐键对账。
VILLAGE_ROSTER: tuple[VillagerSeed, ...] = (
    # 关键 NPC（kind="key"，2 人，8D 全量由运行时 InnerState 承载）
    VillagerSeed(npc_id="chen", name="铁匠陈", kind="key", sector="craft", occupation="铁匠"),
    VillagerSeed(npc_id="lily", name="行商莉莉", kind="key", sector="trade", occupation="行商"),
    # 环境 NPC（kind="env"，23 人，Tier 0/1 封印零 LLM）
    VillagerSeed(npc_id="farmer_zhao", name="赵老汉", kind="env", sector="farming", occupation="农夫"),
    VillagerSeed(npc_id="farmer_wang", name="王氏", kind="env", sector="farming", occupation="农妇"),
    VillagerSeed(npc_id="zhao_daniu", name="赵大牛", kind="env", sector="farming", occupation="农夫"),
    VillagerSeed(npc_id="cao_widow", name="曹寡妇", kind="env", sector="farming", occupation="农妇"),
    VillagerSeed(npc_id="farmhand_sun", name="孙小满", kind="env", sector="farming", occupation="雇农"),
    VillagerSeed(npc_id="herder_ma", name="马二叔", kind="env", sector="farming", occupation="牧人"),
    VillagerSeed(npc_id="miller_qian", name="钱多福", kind="env", sector="farming", occupation="磨坊主"),
    VillagerSeed(npc_id="fisher_zhou", name="周渔夫", kind="env", sector="hunt", occupation="渔夫"),
    VillagerSeed(npc_id="weaver_yang", name="杨大姐", kind="env", sector="craft", occupation="织布"),
    VillagerSeed(npc_id="baker_liu", name="刘婶", kind="env", sector="craft", occupation="面包师"),
    VillagerSeed(npc_id="carpenter_lu", name="鲁一刀", kind="env", sector="craft", occupation="木匠"),
    VillagerSeed(npc_id="cobbler_jia", name="贾三针", kind="env", sector="craft", occupation="修鞋匠"),
    VillagerSeed(npc_id="peddler_luo", name="罗四方", kind="env", sector="trade", occupation="货郎"),
    VillagerSeed(npc_id="doc_qin", name="秦大夫", kind="env", sector="medicine", occupation="大夫"),
    VillagerSeed(npc_id="herb_boy", name="小蓟", kind="env", sector="medicine", occupation="药童"),
    VillagerSeed(npc_id="tavern_sun", name="孙老三", kind="env", sector="tavern", occupation="酒馆老板"),
    VillagerSeed(npc_id="tavern_aman", name="阿满", kind="env", sector="tavern", occupation="酒馆帮工"),
    VillagerSeed(npc_id="master_wen", name="文先生", kind="env", sector="education", occupation="私塾先生"),
    VillagerSeed(npc_id="boy_shitou", name="小石头", kind="env", sector="education", occupation="学童"),
    VillagerSeed(npc_id="guard_wang", name="王守卫", kind="env", sector="guard", occupation="守卫"),
    VillagerSeed(npc_id="watchman_diao", name="刁更头", kind="env", sector="guard", occupation="更夫"),
    VillagerSeed(npc_id="old_zhang", name="老张头", kind="env", sector="idle", occupation="长老"),
    VillagerSeed(npc_id="widow_yi", name="易婆婆", kind="env", sector="idle", occupation="孤寡老人"),
)


@dataclass(frozen=True)
class EnvMind:
    """4D 压缩稳态（环境 NPC）：8D 基底的投影子集，字段名与 InnerState 8D 严格同名（共线性纪律：不新增维度）。
    Tier 0/1 封印：不进任何 prompt（环境 NPC 零 LLM），仅数据层。"""
    p_fatigue: float
    p_hunger: float
    e_P: float
    S_stress: float


@dataclass(frozen=True)
class VillagerProfile:
    seed: VillagerSeed
    innate: InnateAttributes  # tag_genesis.InnateAttributes（6D+财富），与账本挂载依据同源
    mind: Optional[EnvMind]   # env NPC → EnvMind；key NPC → None（8D 全量由运行时 InnerState 承载）
    ledger: TagLedger         # 复用 build_genesis_ledger 的完整账本（缺陷+把柄+高显著称号）
    flaw_label: str           # 账本中的显性缺陷中文名
    secret_label: str         # 账本中的绝密把柄中文名（不进 prompt 的数据层玩法资产）


# 池 id 匹配集合：从账本提取缺陷/把柄时按 FLAW_POOL/SECRET_POOL 的既有
# tag_id 集合匹配（不复制前缀命名约定，扩池后自动生效）。
_FLAW_IDS = frozenset(flaw[0] for flaw in FLAW_POOL)
_SECRET_IDS = frozenset(secret[0] for secret in SECRET_POOL)


def _flaw_and_secret(ledger: TagLedger) -> tuple[MountedTag, MountedTag]:
    """从账本提取缺陷/把柄标签（挂载内核 fail-fast 保证至少各一）。"""
    flaw = next(tag for tag in ledger.all_tags() if tag.tag_id in _FLAW_IDS)
    secret = next(tag for tag in ledger.secrets() if tag.tag_id in _SECRET_IDS)
    return flaw, secret


def generate_villager(seed: VillagerSeed,
                      rng: Optional[random.Random] = None) -> VillagerProfile:
    """生成一位村民的完整人口学档案（离线创生层，产物不进运行时 prompt）。

    rng=None 时经 tag_genesis.deterministic_seed(npc_id) 取 crc32 种子
    ——与引擎常驻化同一确定性口径（跨进程跨实例一致）。流程：先生成
    先天属性（同一 rng 流的最初消费）→ build_genesis_ledger(npc_id,
    rng, innate) 复用单一挂载内核（传入 innate 保证 VillagerProfile.
    innate 与账本挂载依据同源；三参调用与两参路径的 rng 流消费顺序
    逐次一致，确定性产物不变）→ 从账本提取缺陷/把柄中文名 → env NPC
    继续用同一 rng 流采样 EnvMind 初值（常人平稳带：“普通人 70%
    时间平稳”的设计语义）；key NPC mind=None 不采样。
    """
    if rng is None:
        rng = deterministic_seed(seed.npc_id)
    innate = generate_innate_attributes(rng)
    ledger = build_genesis_ledger(seed.npc_id, rng, innate)
    flaw, secret = _flaw_and_secret(ledger)

    mind: Optional[EnvMind] = None
    if seed.kind == "env":
        mind = EnvMind(
            p_fatigue=rng.uniform(0.10, 0.30),
            p_hunger=rng.uniform(0.10, 0.30),
            e_P=rng.uniform(0.40, 0.60),
            S_stress=rng.uniform(0.05, 0.25),
        )
    return VillagerProfile(seed=seed, innate=innate, mind=mind, ledger=ledger,
                           flaw_label=flaw.label, secret_label=secret.label)


def generate_village() -> dict[str, VillagerProfile]:
    """25 人全量生成（键=npc_id）：每人独立 crc32 种子，跨进程跨实例确定性一致。"""
    return {seed.npc_id: generate_villager(seed) for seed in VILLAGE_ROSTER}


def load_newcomer_configs() -> list[dict]:
    """读取 village_roster 目录下全部 .json（sorted 排序，与 engine._load_bg_configs 同风格）。

    目录不存在返回 []。
    """
    configs: list[dict] = []
    if os.path.isdir(NEWCOMER_CONFIG_DIR):
        for fname in sorted(os.listdir(NEWCOMER_CONFIG_DIR)):
            if fname.endswith(".json"):
                with open(os.path.join(NEWCOMER_CONFIG_DIR, fname), encoding="utf-8") as fh:
                    configs.append(json.load(fh))
    return configs


def describe_villager(profile: VillagerProfile) -> str:
    """中文叙事摘要一行（离线数据层的人类可读出口，不进运行时 prompt）。

    格式示例（内容为确定性生成的真实产物）：
      "马二叔（farming/牧人）：体魄寻常，强迫洁癖（uncommon），绝密把柄：私铸劣币（rare）"
    必含：name、occupation、flaw_label（含 tier）、secret_label（含 tier）；
    key NPC 附"关键NPC（8D全量）"。体魄称号来自 attribute_label 公开
    端点（tag_genesis 既有四档文案，非新造缺陷/把柄条目）。头部不做
    "职业+姓名"前缀拼接（括号内已含职业；name 自带职业前缀时避免
    "铁匠铁匠陈"式重复——PM 验收补位修复，叙事可读性口径）。
    """
    seed = profile.seed
    head = f"{seed.name}（{seed.sector}/{seed.occupation}）"
    if seed.kind == "key":
        head += "【关键NPC（8D全量）】"
    flaw, secret = _flaw_and_secret(profile.ledger)
    strength = attribute_label("strength", profile.innate.strength)
    return (f"{head}：{strength}，{flaw.label}（{flaw.tier}），"
            f"绝密把柄：{secret.label}（{secret.tier}）")

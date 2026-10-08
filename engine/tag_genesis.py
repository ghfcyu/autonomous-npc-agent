"""先天属性创生层（T2 标签库 v2 首批）。

规范依据：docs/NPC_TAG_DATABASE.md 2.1/2.5/3.1/3.2/4.1/4.2/4.3/7.2 节。
三处偏离书面记录（PM 裁定）：
1. alcohol_tol σ=1.5 依 4.3 节第 5 条明示，偏离 2.1 节总述 σ=1.25；
2. 财富按 2.1 节分位语义参数化（P5≈10/P95≈50 铜板→中位数√500），
   4.3 节 "LogNormal(10,80)" 记法语义不明不采用；"1% 寡头持 80% 金币"
   与"90% 人口 10~50"在对数正态集中度上数学不兼容（前者需 Gini>0.9），
   取 2.1 节可测试硬约束，长尾方向由对数正态自然呈现；
3. "双向软截断"实现口径=拒绝采样式截断正态（重采样直至落入 [1,10]，
   避免硬 clamp 在边界堆积概率尖峰）。
零第三方依赖，仅 random/math/dataclasses 标准库。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

INNATE_ATTRIBUTE_SPECS = {  # attr_id → (中文名, mu, sigma)，4.3 节
    "beauty": ("美貌度", 5.0, 1.25),
    "strength": ("体魄力量", 5.0, 1.25),
    "savvy": ("悟性智商", 5.0, 1.25),
    "courage": ("胆识勇气", 5.0, 1.25),
    "morality": ("道德良知", 5.0, 1.25),
    "alcohol_tol": ("酒量耐受", 5.0, 1.5),
}
ATTRIBUTE_RANGE = (1.0, 10.0)
WEALTH_LOG_MU = math.log(500) / 2          # ≈3.107
WEALTH_LOG_SIGMA = math.log(5) / (2 * 1.6449)  # ≈0.489
PYRAMID_TIERS = [("common", "普通人基底", 0.70), ("uncommon", "进阶特质", 0.20), ("rare", "罕见特质", 0.08), ("extreme", "极端长尾", 0.02)]
YAO_STYLE_ARCHETYPES = ["随和", "较真", "进取", "谨慎", "给予", "主导", "反叛", "孤僻"]  # 7.2 节
YAO_INTENSITY_TIERS = ["萌芽", "显性", "执念", "病态"]  # 2.5 节一品~四品，频度=金字塔 70/20/8/2

# 拒绝采样安全上界：超过后回退 clamp，防病态参数死循环。
_MAX_RESAMPLE = 200

# 4.2 节美貌六档称号：(下界, 称号)，降序排列（下界从高到低），命中第一个 value>=下界即返回。
_BEAUTY_LABELS = [  # 规范出处：NPC_TAG_DATABASE.md 4.2 节美貌评分系统
    (9.5, "倾国倾城"),
    (8.5, "惊艳俊美"),
    (7.0, "清秀端正"),
    (4.0, "相貌平平"),
    (2.5, "粗陋枯槁"),
    (0.0, "面目可怖"),
]

# 通用四档称号表（档界 8.75/7.5/2.5，依 4.1 节概率分位）：
# [8.75,10]=0.13% 传奇档 / [7.5,8.75)=2.14% 翘楚档 / [2.5,7.5)=95.44% 寻常档 / [1.0,2.5)=长尾档。
_ATTRIBUTE_LABELS = {  # 规范出处：NPC_TAG_DATABASE.md 4.1/4.3 节
    "strength": ("天生神力", "体魄精壮", "体魄寻常", "弱不禁风"),
    "savvy": ("天资聪颖", "心思活络", "心思寻常", "心智迟钝"),
    "courage": ("胆识过人", "胆气较壮", "胆气寻常", "胆小如鼠"),
    "morality": ("高义守节", "心存善念", "遵纪守法", "心术不正"),
    "alcohol_tol": ("千杯不醉", "酒量尚可", "酒量寻常", "一杯即倒"),
}

# 四档分界：value ≥ legendary → 传奇档；≥ notable → 翘楚档；≥ ordinary → 寻常档；否则长尾档。
_TIER_LEGENDARY = 8.75
_TIER_NOTABLE = 7.5
_TIER_ORDINARY = 2.5


@dataclass(frozen=True)
class InnateAttributes:
    """NPC 先天 6D 正态属性 + 财富（2.1 节），创生后终身不变（6.1 节先天印记）。"""

    beauty: float
    strength: float
    savvy: float
    courage: float
    morality: float
    alcohol_tol: float
    wealth: int

    def to_dict(self) -> dict:
        return {
            "beauty": self.beauty,
            "strength": self.strength,
            "savvy": self.savvy,
            "courage": self.courage,
            "morality": self.morality,
            "alcohol_tol": self.alcohol_tol,
            "wealth": self.wealth,
        }


def _truncated_gauss(rng: random.Random, mu: float, sigma: float) -> float:
    """拒绝采样式截断正态：重采样直至落入 ATTRIBUTE_RANGE，超上界回退 clamp。"""
    lo, hi = ATTRIBUTE_RANGE
    value = mu
    for _ in range(_MAX_RESAMPLE):
        value = rng.gauss(mu, sigma)
        if lo <= value <= hi:
            return value
    return min(max(value, lo), hi)


def generate_innate_attributes(rng: random.Random) -> InnateAttributes:
    """按 4.3 节规格采样全部先天属性。

    6 属性各自独立截断正态；财富对数正态（2.1 节分位语义），
    max(1, round(...)) 保证下界 ≥1 铜板。
    """
    values = {
        attr_id: _truncated_gauss(rng, mu, sigma)
        for attr_id, (_name, mu, sigma) in INNATE_ATTRIBUTE_SPECS.items()
    }
    wealth = max(1, round(rng.lognormvariate(WEALTH_LOG_MU, WEALTH_LOG_SIGMA)))
    return InnateAttributes(
        beauty=values["beauty"],
        strength=values["strength"],
        savvy=values["savvy"],
        courage=values["courage"],
        morality=values["morality"],
        alcohol_tol=values["alcohol_tol"],
        wealth=wealth,
    )


def _weighted_index(rng: random.Random, weights: list[float]) -> int:
    """按权重列表返回命中下标（累积分布法，确定性依赖 rng）。"""
    threshold = rng.random() * sum(weights)
    cumulative = 0.0
    for index, weight in enumerate(weights):
        cumulative += weight
        if threshold < cumulative:
            return index
    return len(weights) - 1


def sample_pyramid_tier(rng: random.Random) -> str:
    """金字塔频度法则抽层（3.1 节）：70/20/8/2，返回 tier_id。"""
    index = _weighted_index(rng, [tier[2] for tier in PYRAMID_TIERS])
    return PYRAMID_TIERS[index][0]


def sample_yao_style(rng: random.Random) -> tuple[str, str]:
    """尧氏 8 风格 × 4 阶抽样（2.5/7.2 节）。

    风格原型均匀抽取；执念深度按金字塔频度 70/20/8/2 分层。
    返回 (风格名, 阶名)，如 ("较真", "执念")。
    """
    style = rng.choice(YAO_STYLE_ARCHETYPES)
    tier_index = _weighted_index(rng, [tier[2] for tier in PYRAMID_TIERS])
    return style, YAO_INTENSITY_TIERS[tier_index]


def zipf_probabilities(n: int, s: float = 1.15) -> list[float]:
    """齐普夫分布概率表（3.2 节）：P(k)=k^-s 归一化，k=1..n。"""
    if n < 1:
        raise ValueError(f"n 必须 ≥1，收到 {n}")
    raw = [rank ** -s for rank in range(1, n + 1)]
    total = sum(raw)
    return [weight / total for weight in raw]


def sample_zipf_rank(rng: random.Random, n: int, s: float = 1.15) -> int:
    """按齐普夫定律抽取名次（3.2 节，职业/身份标签宏观分布），1-based。"""
    if n < 1:
        raise ValueError(f"n 必须 ≥1，收到 {n}")
    threshold = rng.random()
    cumulative = 0.0
    for rank, probability in enumerate(zipf_probabilities(n, s), start=1):
        cumulative += probability
        if threshold < cumulative:
            return rank
    return n


def beauty_label(value: float) -> str:
    """美貌六档称号（4.2 节美貌评分系统）。"""
    for lower_bound, label in _BEAUTY_LABELS:
        if value >= lower_bound:
            return label
    return _BEAUTY_LABELS[-1][1]


def attribute_label(attr_id: str, value: float) -> str:
    """通用四档称号（4.1 节概率分位 8.75/7.5/2.5）。

    beauty 有专属六档（beauty_label），传入即 ValueError；
    未知 attr_id 同样 ValueError。
    """
    if attr_id == "beauty":
        raise ValueError("beauty 请使用专属六档 beauty_label()")
    if attr_id not in _ATTRIBUTE_LABELS:
        raise ValueError(f"未知属性 attr_id: {attr_id}")
    labels = _ATTRIBUTE_LABELS[attr_id]
    if value >= _TIER_LEGENDARY:
        return labels[0]
    if value >= _TIER_NOTABLE:
        return labels[1]
    if value >= _TIER_ORDINARY:
        return labels[2]
    return labels[3]

"""T2 标签库 · 脾气掩码触发条件双分区表：8 大显性脾气 × PAD 双值域。

规范依据：docs/NPC_TAG_DATABASE.md 2.4 节（林传鼎 8 核心显性脾气的
PAD 分区触发条件）、7.2 节（TEMP_* 标签 ID 与中文名）。

定位：本模块是 8 脾气掩码的**前置条件层**——只回答"当前 8D 状态
是否落入某脾气的 PAD 触发分区"，不生产动作/垫话模板（那是
engine/filler.py 的现行职责；主人裁定值域后，T2 掩码实施批用本表
替换 filler 的粗粒度 3 掩码状态测试，接线不在本批）。

① 双值域背景与等待管理策略（主人未裁定的防空等设计）
--------------------------------------------------------
PAD 心境值域存在架构级未裁定分歧（2026-10-07 提请主人裁定，见
PROGRESS.md"待主人裁定事项"）：
- v01 = [0,1]：engine/inner_state.py 现实现值域（apply_delta 自动
  clamp 至 [0,1]，中点 0.5 为中性）；
- v11 = [-1,1]：docs 规范 2.4 节值域（中点 0.0 为中性，负面情绪
  需要负愉悦域，如惭辱 e_P < -0.2）。
裁定前的等待管理策略：两套分区表**并行成对维护**，由模块级激活开关
ACTIVE_PAD_VERSION 选边（默认 "v01"，与 inner_state.py 现实现一致）。
裁定 [-1,1] 后：改 ACTIVE_PAD_VERSION 为 "v11" + inner_state.py 值域
放宽至 [-1,1] 即完成接线，8 个判定函数与其余消费方零改动——双表
设计的核心收益（防空等卡死）。

② 两套表的语义映射关系（防漂移硬约束）
------------------------------------------
两套表互为仿射线性映射的像，映射公式：
    t_11 = 2 * t_01 - 1      （[0,1] → [-1,1]，如 v01 的 0.7 ↔ v11 的 0.4）
    t_01 = (t_11 + 1) / 2    （[-1,1] → [0,1]）
PAD_PARTITION_V11 是规范权威源，PAD_PARTITION_V01 的每个阈值 = v11
对应阈值经上式的换算值，故同一逻辑状态在两套激活版本下的判定行为
严格一致。v01_to_v11/v11_to_v01 内含 round(10) 浮点噪声消除（阈值
均 ≤2 位小数），保证"恰在阈值上"的边界状态跨版本判定一致。配套
测试以表级映射全等 + 行为级边界用例双重锁死该等价，防两套表设计漂移。

分区表键位设计说明：任务书建议 {high/low/mid} 三键结构，但规范 2.4
各脾气阈值互不相同（喜悦 e_A > 0.3 ≠ 惊恐 e_A > 0.5），单一高/低带
无法表达；故每键以**所服务的脾气角色**命名（joy_floor/fear_ceil 等），
每个阈值都能溯源到规范 2.4 的一行条件；mid 为各版本中性点锚
（v01=0.5 / v11=0.0），非脾气条件。

③ 8 脾气触发条件的分区设计依据（哪个脾气看哪个维度的高低带）
----------------------------------------------------------------
全部 PAD 条件以规范 2.4 节为权威口径，维度选择与 PAD 理论
（Mehrabian 三维情绪空间 / Russell 环形模型象限）一致：
- 安静 TEMP_SERENE       e_A 安静带 [serene_low, serene_high]
                         ——低唤醒平静态（规范 |e_A| ≤ 0.25）；
- 喜悦 TEMP_JOYFUL       e_P 高带(> joy_floor) + e_A 中高带(> joy_floor)
                         ——正愉悦 + 中高唤醒（"兴奋的喜"，区别于
                         低唤醒松弛的"乐"——后者落安静带）；
- 暴躁 TEMP_WRATHFUL     e_P 低带(< negative_ceil) + e_A 高带
                         (> wrath_floor) + e_D 高带(> wrath_floor)
                         ——怒 = 负愉悦+高唤醒+高支配（怒象限 -P+A+D）；
- 哀戚 TEMP_MELANCHOLIC  e_P 极低带(< grief_ceil) + e_A 低带
                         (< grief_ceil) + e_D 低带(< nonpositive_ceil)
                         ——哀 = 强负愉悦+低唤醒+低支配（退缩象限 -P-A-D）；
- 惊恐 TEMP_FEARFUL      e_P 低带(< negative_ceil) + e_A 高带
                         (> fear_floor) + e_D 极低带(< fear_ceil)
                         ——惧 = 负愉悦+高唤醒+低支配（惧象限 -P+A-D），
                         与暴躁同向高唤醒，以支配维高低分"攻"与"逃"；
- 恭顺 TEMP_REVERENT     e_P 非负带(≥ reverent_floor) + e_D 低带
                         (< nonpositive_ceil)
                         ——敬 = 非负愉悦+低支配（驯服信赖象限 +P-D）；
- 傲慢 TEMP_ARROGANT     e_D 高带(> arrogant_floor)
                         ——傲 = 高支配（规范口径：支配维单条件）；
- 惭辱 TEMP_ASHAMED      e_P 低带(< negative_ceil) + e_D 低带
                         (< ashamed_ceil)
                         ——辱 = 负愉悦+低支配（羞耻象限 -P-D）。

与规范 2.4 / 任务书参考摘要的偏离记录（书面留痕，接线批复核）：
1. 恭顺的"或面对高声望"、傲慢的"且财富/地位高于对方"、惭辱的
   "罪行/把柄被触及"是社交/事件比较条件，不在 PAD 空间表达范围内；
   本表只落 PAD 分区条件，社交支线留待 T2 掩码实施批与标签层
   （声望/财富标签、把柄账本 tag_mount.TagLedger.secrets）组合判定；
2. 安静采用规范 2.4 带状条件 |e_A| ≤ 0.25（v01 版为以中性点 0.5 为
   中心、半宽 0.125 的带），未采用任务书参考摘要的"e_A 低带"表述
   ——以规范为准；带状条件拆为 serene_low/serene_high 两个位置阈值，
   使全表阈值统一服从 ② 的仿射映射；
3. 暴躁/惊恐保留规范 2.4 的 e_P 低带条件（任务书参考摘要仅列
   e_A/e_D 两维）——PAD 理论怒/惧均伴随负愉悦，以规范为准；
4. 傲慢未采纳任务书参考摘要的"e_P 中高带"附加条件——规范 2.4 仅给
   e_D > 0.5 单条件，以规范为准。

激活开关语义
----------------
ACTIVE_PAD_VERSION 为模块级常量；8 个判定函数每次调用动态读取
active_partition()，运行期改写该常量即时全局生效（一处切换）。
状态值域契约：判定函数假定入参 InnerState 的 PAD 值与激活版本同域
——v01 配 [0,1] 状态（inner_state.py 现实现），v11 配 [-1,1] 状态
（主人裁定后接线）；生理四维与 S_stress 在两个版本下均为 [0,1]，
不参与映射。

零第三方依赖，仅 dataclasses/typing 标准库。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict

if TYPE_CHECKING:  # 仅为类型注解服务，避免运行期循环导入
    from .inner_state import InnerState

# 激活开关：当前生效的 PAD 值域版本，一处切换全局生效。
# ⏳ 待主人裁定激活哪套——裁定 [-1,1] 后改此常量为 "v11" +
#    inner_state.py 值域放宽至 [-1,1] 即完成接线（详见 docstring ①）。
#    仅允许 "v01"（[0,1]）/ "v11"（[-1,1]）二值，非法值由
#    active_partition() 快速失败。
ACTIVE_PAD_VERSION = "v01"

# 浮点噪声消除位数：全部阈值 ≤2 位小数，round(10) 安全且无损。
_FP_DIGITS = 10


def v01_to_v11(value: float) -> float:
    """[0,1] → [-1,1] 仿射映射：t_11 = 2*t_01 - 1（含浮点噪声消除）。"""
    return round(2.0 * value - 1.0, _FP_DIGITS)


def v11_to_v01(value: float) -> float:
    """[-1,1] → [0,1] 仿射映射：t_01 = (t_11 + 1) / 2（含浮点噪声消除）。"""
    return round((value + 1.0) / 2.0, _FP_DIGITS)


# [-1,1] 分区表（docs 规范 2.4 节权威值域版，中点 0.0 为中性）。
# 每键以所服务的脾气角色命名，行注释即规范 2.4 对应条件。
PAD_PARTITION_V11: Dict[str, Dict[str, float]] = {
    "e_P": {
        "mid": 0.0,               # 中性点锚
        "joy_floor": 0.4,         # 喜悦：e_P > 0.4
        "reverent_floor": 0.0,    # 恭顺：e_P ≥ 0.0（非负愉悦）
        "negative_ceil": -0.2,    # 暴躁/惊恐/惭辱：e_P < -0.2
        "grief_ceil": -0.3,       # 哀戚：e_P < -0.3
    },
    "e_A": {
        "mid": 0.0,               # 中性点锚
        "serene_low": -0.25,      # 安静带下沿：|e_A| ≤ 0.25
        "serene_high": 0.25,      # 安静带上沿
        "joy_floor": 0.3,         # 喜悦：e_A > 0.3
        "wrath_floor": 0.4,       # 暴躁：e_A > 0.4
        "fear_floor": 0.5,        # 惊恐：e_A > 0.5
        "grief_ceil": 0.1,        # 哀戚：e_A < 0.1
    },
    "e_D": {
        "mid": 0.0,               # 中性点锚
        "wrath_floor": 0.2,       # 暴躁：e_D > 0.2
        "arrogant_floor": 0.5,    # 傲慢：e_D > 0.5
        "nonpositive_ceil": 0.0,  # 哀戚/恭顺：e_D < 0.0
        "fear_ceil": -0.3,        # 惊恐：e_D < -0.3
        "ashamed_ceil": -0.2,     # 惭辱：e_D < -0.2
    },
}

# [0,1] 分区表（inner_state.py 现实现值域版，中点 0.5 为中性）。
# 全部阈值 = PAD_PARTITION_V11 对应键经 t_01 = (t_11 + 1)/2 的换算值，
# 与 v11 表语义严格等价（仿射镜像；映射全等锁见 tests）。
PAD_PARTITION_V01: Dict[str, Dict[str, float]] = {
    "e_P": {
        "mid": 0.5,               # 中性点锚
        "joy_floor": 0.7,         # ← v11 0.4
        "reverent_floor": 0.5,    # ← v11 0.0
        "negative_ceil": 0.4,     # ← v11 -0.2
        "grief_ceil": 0.35,        # ← v11 -0.3
    },
    "e_A": {
        "mid": 0.5,               # 中性点锚
        "serene_low": 0.375,      # ← v11 -0.25
        "serene_high": 0.625,     # ← v11 0.25
        "joy_floor": 0.65,        # ← v11 0.3
        "wrath_floor": 0.7,       # ← v11 0.4
        "fear_floor": 0.75,       # ← v11 0.5
        "grief_ceil": 0.55,       # ← v11 0.1
    },
    "e_D": {
        "mid": 0.5,               # 中性点锚
        "wrath_floor": 0.6,       # ← v11 0.2
        "arrogant_floor": 0.75,   # ← v11 0.5
        "nonpositive_ceil": 0.5,  # ← v11 0.0
        "fear_ceil": 0.35,        # ← v11 -0.3
        "ashamed_ceil": 0.4,      # ← v11 -0.2
    },
}

_PAD_PARTITIONS: Dict[str, Dict[str, Dict[str, float]]] = {
    "v01": PAD_PARTITION_V01,
    "v11": PAD_PARTITION_V11,
}


def active_partition() -> Dict[str, Dict[str, float]]:
    """当前激活值域的 PAD 分区表（随 ACTIVE_PAD_VERSION 运行期切换）。"""
    try:
        return _PAD_PARTITIONS[ACTIVE_PAD_VERSION]
    except KeyError:
        raise ValueError(
            f"ACTIVE_PAD_VERSION 只允许 'v01' 或 'v11'，当前为 "
            f"{ACTIVE_PAD_VERSION!r}") from None


# ---------------------------------------------------------------------- #
# 8 脾气判定函数：统一签名 (inner_state) -> bool；阈值全部引用分区表
# 常量，函数体零硬编码数字（正交纪律，AST 锁见 tests）。
# ---------------------------------------------------------------------- #
def is_serene(inner_state: "InnerState") -> bool:
    """安静：e_A 落在安静带内（规范 2.4：|e_A| ≤ 0.25）。

    单维条件：安静是唤醒维的带状属性（平静但不嗜睡），与愉悦/支配无关。
    """
    p = active_partition()
    return (p["e_A"]["serene_low"] <= inner_state.e_A
            <= p["e_A"]["serene_high"])


def is_joyful(inner_state: "InnerState") -> bool:
    """喜悦：e_P 高带 + e_A 中高带（规范 2.4：e_P > 0.4 且 e_A > 0.3）。

    双维条件：正愉悦是底色，中高唤醒区分"兴奋的喜"与低唤醒松弛的"乐"
    （后者落安静带，见 is_serene）。
    """
    p = active_partition()
    return (inner_state.e_P > p["e_P"]["joy_floor"]
            and inner_state.e_A > p["e_A"]["joy_floor"])


def is_wrathful(inner_state: "InnerState") -> bool:
    """暴躁：e_P 低带 + e_A 高带 + e_D 高带（规范 2.4 三条件全取）。

    怒 = 负愉悦+高唤醒+高支配（怒象限 -P+A+D）；缺 e_P 低带会把
    "亢奋好斗"误判为怒，故保留规范的愉悦条件（偏离记录 3）。
    """
    p = active_partition()
    return (inner_state.e_P < p["e_P"]["negative_ceil"]
            and inner_state.e_A > p["e_A"]["wrath_floor"]
            and inner_state.e_D > p["e_D"]["wrath_floor"])


def is_melancholic(inner_state: "InnerState") -> bool:
    """哀戚：e_P 极低带 + e_A 低带 + e_D 低带（规范 2.4 三条件全取）。

    哀 = 强负愉悦+低唤醒+低支配（抑郁退缩象限 -P-A-D）。
    """
    p = active_partition()
    return (inner_state.e_P < p["e_P"]["grief_ceil"]
            and inner_state.e_A < p["e_A"]["grief_ceil"]
            and inner_state.e_D < p["e_D"]["nonpositive_ceil"])


def is_fearful(inner_state: "InnerState") -> bool:
    """惊恐：e_P 低带 + e_A 高带 + e_D 极低带（规范 2.4 三条件全取）。

    惧 = 负愉悦+高唤醒+低支配（惧象限 -P+A-D）：与暴躁同向高唤醒，
    以支配维高低区分"攻"（怒）与"逃"（惧）。
    """
    p = active_partition()
    return (inner_state.e_P < p["e_P"]["negative_ceil"]
            and inner_state.e_A > p["e_A"]["fear_floor"]
            and inner_state.e_D < p["e_D"]["fear_ceil"])


def is_reverent(inner_state: "InnerState") -> bool:
    """恭顺：e_P 非负带 + e_D 低带（规范 2.4：e_P ≥ 0.0 且 e_D < 0.0）。

    敬 = 非负愉悦+低支配（驯服信赖象限 +P-D）；"或面对高声望"是
    社交支线，不在本表（偏离记录 1）。
    """
    p = active_partition()
    return (inner_state.e_P >= p["e_P"]["reverent_floor"]
            and inner_state.e_D < p["e_D"]["nonpositive_ceil"])


def is_arrogant(inner_state: "InnerState") -> bool:
    """傲慢：e_D 高带（规范 2.4：e_D > 0.5，支配维单条件）。

    "且财富/地位高于对方"是社交支线，不在本表（偏离记录 1、4）。
    """
    p = active_partition()
    return inner_state.e_D > p["e_D"]["arrogant_floor"]


def is_ashamed(inner_state: "InnerState") -> bool:
    """惭辱：e_P 低带 + e_D 低带（规范 2.4：e_P < -0.2 且 e_D < -0.2）。

    辱 = 负愉悦+低支配（羞耻象限 -P-D）；"把柄被触及"是事件支线，
    不在本表（偏离记录 1）。
    """
    p = active_partition()
    return (inner_state.e_P < p["e_P"]["negative_ceil"]
            and inner_state.e_D < p["e_D"]["ashamed_ceil"])


@dataclass(frozen=True)
class TemperamentEntry:
    """一条显性脾气的掩码定义（规范 7.2 节标签 + 2.4 节 PAD 触发条件）。

    - temp_id：规范 7.2 节标签 ID（如 "TEMP_SERENE"）；
    - name_cn：中文名（如 "安静"）；
    - predicate：PAD 触发条件，统一签名 (inner_state) -> bool，
      阈值引用 active_partition()，随激活版本切换；
    - spec_v11：规范 2.4 节 [-1,1] 值域的条件表达式（溯源用）。
    """

    temp_id: str
    name_cn: str
    predicate: Callable[["InnerState"], bool]
    spec_v11: str


# 8 大显性脾气注册表（规范 7.2 节 ID/中文名 + 2.4 节 PAD 条件），
# 按规范 2.4 表序 1-8 排列。
TEMPERAMENT_TABLE: Dict[str, TemperamentEntry] = {
    "TEMP_SERENE": TemperamentEntry(
        temp_id="TEMP_SERENE", name_cn="安静", predicate=is_serene,
        spec_v11="|e_A| ≤ 0.25"),
    "TEMP_JOYFUL": TemperamentEntry(
        temp_id="TEMP_JOYFUL", name_cn="喜悦", predicate=is_joyful,
        spec_v11="e_P > 0.4 且 e_A > 0.3"),
    "TEMP_WRATHFUL": TemperamentEntry(
        temp_id="TEMP_WRATHFUL", name_cn="暴躁", predicate=is_wrathful,
        spec_v11="e_P < -0.2 且 e_A > 0.4 且 e_D > 0.2"),
    "TEMP_MELANCHOLIC": TemperamentEntry(
        temp_id="TEMP_MELANCHOLIC", name_cn="哀戚", predicate=is_melancholic,
        spec_v11="e_P < -0.3 且 e_A < 0.1 且 e_D < 0.0"),
    "TEMP_FEARFUL": TemperamentEntry(
        temp_id="TEMP_FEARFUL", name_cn="惊恐", predicate=is_fearful,
        spec_v11="e_P < -0.2 且 e_A > 0.5 且 e_D < -0.3"),
    "TEMP_REVERENT": TemperamentEntry(
        temp_id="TEMP_REVERENT", name_cn="恭顺", predicate=is_reverent,
        spec_v11="e_P ≥ 0.0 且 e_D < 0.0（“或面对高声望”社交支线除外）"),
    "TEMP_ARROGANT": TemperamentEntry(
        temp_id="TEMP_ARROGANT", name_cn="傲慢", predicate=is_arrogant,
        spec_v11="e_D > 0.5（“且财富/地位高于对方”社交支线除外）"),
    "TEMP_ASHAMED": TemperamentEntry(
        temp_id="TEMP_ASHAMED", name_cn="惭辱", predicate=is_ashamed,
        spec_v11="e_P < -0.2 且 e_D < -0.2（“把柄被触及”事件支线除外）"),
}

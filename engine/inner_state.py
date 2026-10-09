"""内状态层：8D 正交心智基底 + 荀子六情映射器 + 事件驱动规则引擎更新器。

设计要点
--------
- InnerState 是 NPC 的 8D 正交心智基底（唯一心智变量系统，无双轨）：
  * 4D 生理稳态：p_fatigue / p_hunger / p_pain / p_drive
  * 3D PAD 心境：e_P（愉悦）/ e_A（唤醒）/ e_D（支配）
  * 1D 压力负荷：S_stress
  值域 [0, 1]，apply_delta 自动 clamp。
- to_discrete_tags() 把 8D 映射为 ≤4 个高显著度离散中文标签
  （阈值常量 BAND_* 是全仓单一来源，filler.py 掩码同源引用），
  是决策上下文消费 8D 的唯一通道——进慢脑 prompt 的只有离散
  标签，浮点向量/参数表严禁注入（<35token 极简组装规范）。
- 荀子六情（好、恶、喜、怒、哀、乐）映射为 PAD 增量矢量，
  是"情 → 心境"的确定性通道：XUNZI_EMOTION_VECTORS。
- 亲缘度（affinity）不属于心智基底：它由关系网络
  （RelationshipNetwork.update_affinity）维护，送礼改写关系而非改写内状态。
- StateUpdater 作为事件总线订阅者，按确定性规则更新基底。
- 规则引擎零第三方依赖，全部逻辑可单测。
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from .world import WorldEvent

# --------------------------------------------------------------------------- #
# 离散状态带阈值（单一来源，<35token 极简 Prompt 组装规范）
# --------------------------------------------------------------------------- #
# 进慢脑（LLM 决策链）prompt 的只有 3-4 个高显著度离散中文标签，
# 严禁注入浮点向量/完整参数表。本组常量是全仓状态带阈值的唯一权威：
# engine/filler.py 脾气掩码的状态带同源引用（消除"同一心理现象
# 两套阈值"的共线性），数值与掩码既有阈值严格一致（行为零变化）。
# p_hunger / p_pain 两条带为本批新增（掩码未消费，是决策上下文
# 离散标签新增的显著带）；p_pain 阈值 0.5 低于生理稳态高带 0.7——
# 痛感对行为显著性更高（"断指"缺陷的状态传导通道）。
BAND_STRESS_HIGH = 0.6      # S_stress > 0.6 → 心烦意乱
BAND_AROUSAL_HIGH = 0.7     # e_A > 0.7 → 情绪激动
BAND_PLEASURE_HIGH = 0.7    # e_P > 0.7 → 心情愉悦
BAND_PLEASURE_LOW = 0.4     # e_P < 0.4 → 情绪低落
BAND_DOMINANCE_HIGH = 0.7   # e_D > 0.7 → 盛气凌人
BAND_FATIGUE_HIGH = 0.7     # p_fatigue > 0.7 → 疲惫不堪
BAND_HUNGER_HIGH = 0.7      # p_hunger > 0.7 → 饥肠辘辘
BAND_PAIN_HIGH = 0.5        # p_pain > 0.5 → 旧伤作痛

# 8D → 离散中文标签映射表：(参数, 方向, 阈值, 标签)。
# 方向 ">"：value > 阈值命中，偏离幅度 = value - 阈值；
# 方向 "<"：value < 阈值命中，偏离幅度 = 阈值 - value。
# e_P 的两条带天然互斥（一个值不可能同时 >0.7 且 <0.4）。
DISCRETE_BANDS = (
    ("S_stress", ">", BAND_STRESS_HIGH, "心烦意乱"),
    ("e_A", ">", BAND_AROUSAL_HIGH, "情绪激动"),
    ("e_P", ">", BAND_PLEASURE_HIGH, "心情愉悦"),
    ("e_P", "<", BAND_PLEASURE_LOW, "情绪低落"),
    ("e_D", ">", BAND_DOMINANCE_HIGH, "盛气凌人"),
    ("p_fatigue", ">", BAND_FATIGUE_HIGH, "疲惫不堪"),
    ("p_hunger", ">", BAND_HUNGER_HIGH, "饥肠辘辘"),
    ("p_pain", ">", BAND_PAIN_HIGH, "旧伤作痛"),
)

PRAISE_KEYWORDS = ("好", "厉害", "棒", "了不起", "手艺", "牛", "真行")
CRITICISM_KEYWORDS = ("不对", "不好", "差", "烂", "质疑", "垃圾", "假")

# 荀子六情 → PAD 增量矢量 (d_e_P, d_e_A, d_e_D)。
# 《荀子·天论》："形具而神生，好恶喜怒哀乐臧焉。"
#
# 缩放理由（书面记录）：本表为权威规范 docs/NPC_TAG_DATABASE.md 2.3 节
# 六情 PAD 矢量偏置增量约 1/6 幅度的 [0,1] 基底小步进缩放版——
# InnerState 值域 [0,1] 且 apply_delta 自动 clamp，该基底下单事件
# 不跳变满格、多事件可叠加逼近，故各分量幅度按约 1/6 缩放
# （如"怒" e_A 代码 0.10 / 规范 0.6，"乐" e_P 代码 0.06 / 规范 0.4）。
# 两处方向已于 2026-10-07 按审查裁定归正跟随规范：
#   * "怒" e_D：规范 +0.5（愤怒=高支配，PAD/Russell 环形模型经典象限），
#     代码由 -0.05 归正为 +0.08（按 e_A 0.10/规范 0.6 ≈ 1/6 缩放比取 0.08）；
#   * "乐" e_A：规范 -0.3（乐=Contentment 身心松弛低唤醒），
#     代码由 +0.04 归正为 -0.05（按 e_P 0.06/规范 0.4 ≈ 1/6 缩放比取 -0.05）。
XUNZI_EMOTION_VECTORS = {
    "好": (0.05, 0.03, 0.02),     # 获得喜好之物
    "恶": (-0.05, 0.03, -0.02),   # 厌恶/失去
    "喜": (0.08, 0.05, 0.0),      # 喜悦
    "怒": (-0.08, 0.10, 0.08),    # 愤怒（e_D 已归正为正：高支配）
    "哀": (-0.06, -0.04, -0.03),  # 悲伤
    "乐": (0.06, -0.05, 0.03),    # 快乐（e_A 已归正为负：低唤醒松弛）
}


@dataclass
class InnerState:
    """NPC 的 8D 正交心智基底，值域 [0, 1]。

    4D 生理稳态 + 3D PAD 心境 + 1D 压力负荷，八维彼此正交，
    是引擎中唯一的心智变量系统。
    """
    # 4D 生理稳态
    p_fatigue: float = 0.2   # 疲劳（高=累）
    p_hunger: float = 0.3    # 饥饿
    p_pain: float = 0.1      # 痛感
    p_drive: float = 0.5     # 驱力（行动意愿）
    # 3D PAD 心境
    e_P: float = 0.5         # 愉悦 Pleasure
    e_A: float = 0.3         # 唤醒 Arousal
    e_D: float = 0.5         # 支配 Dominance
    # 1D 压力负荷
    S_stress: float = 0.2    # 压力

    PARAMS = ("p_fatigue", "p_hunger", "p_pain", "p_drive",
              "e_P", "e_A", "e_D", "S_stress")

    def apply_delta(self, param: str, delta: float) -> None:
        """安全增量，自动 clamp 到 [0, 1]。"""
        current = getattr(self, param)
        setattr(self, param, max(0.0, min(1.0, current + delta)))

    def to_dict(self) -> dict:
        """序列化为字典（8 键），值保留 4 位小数。"""
        return {p: round(getattr(self, p), 4) for p in self.PARAMS}

    def to_discrete_tags(self) -> List[str]:
        """8D → 显著偏离带的离散中文标签（<35token 极简组装唯一口径）。

        - 只输出显著偏离带的项（带内微差不注入 prompt）；
        - 按偏离幅度降序取 top 4（幅度并列时保持 DISCRETE_BANDS
          定义序，排序稳定、输出确定）；
        - 全常带返回 ["心境平稳"]（单标签保持【此刻内心】行存在）。
        本方法是决策上下文消费 8D 的唯一通道：旧 8 参数浮点文本
        方法（"名: 值"格式，约 71 字符）已按无双轨纪律删除，
        严禁浮点数字进入慢脑 prompt。
        """
        hits = []
        for param, direction, threshold, label in DISCRETE_BANDS:
            value = getattr(self, param)
            if direction == ">":
                if value > threshold:
                    hits.append((value - threshold, label))
            elif value < threshold:
                hits.append((threshold - value, label))
        if not hits:
            return ["心境平稳"]
        hits.sort(key=lambda item: item[0], reverse=True)
        return [label for _, label in hits[:4]]


class StateUpdater:
    """事件驱动心智基底更新器（规则引擎）。零第三方依赖。

    作为 EventBus 的订阅者，按确定性规则更新 NPC 的 8D 心智基底；
    情绪事件经荀子六情映射器落为 PAD 增量；亲缘度变化直接写入
    关系网络。每条规则确定性可测——给定相同事件和标签，参数增量恒定。
    """

    def __init__(self, npc) -> None:
        self.npc = npc

    def on_event(self, event: "WorldEvent") -> None:
        """处理事件，更新 NPC 心智基底（四类事件分发不变）。"""
        npc = self.npc
        state = npc.inner_state
        npc_id = npc.persona.id
        tags = npc.persona.tags

        if event.kind == "item_given" and event.payload.get("to") == npc_id:
            self._on_gift(state, tags)
        elif event.kind == "player_spoke" and event.payload.get("to") == npc_id:
            text = event.payload.get("text", "")
            self._on_player_speak(state, tags, text)
        elif event.kind == "npc_action" and event.payload.get("npc") == npc_id:
            self._on_npc_action(state)
        elif event.kind == "time_passed":
            self._on_time_pass(state)

    def _apply_xunzi(self, state: InnerState, emotion: str) -> None:
        """应用荀子六情矢量：取 (d_e_P, d_e_A, d_e_D) 施加到 PAD 三维。"""
        d_e_P, d_e_A, d_e_D = XUNZI_EMOTION_VECTORS[emotion]
        state.apply_delta("e_P", d_e_P)
        state.apply_delta("e_A", d_e_A)
        state.apply_delta("e_D", d_e_D)

    def _on_gift(self, state: InnerState, tags: dict) -> None:
        # 送礼 → 六情"好"（获得喜好之物）
        self._apply_xunzi(state, "好")
        state.apply_delta("S_stress", -0.05)
        # 亲缘度写入关系网络（心智基底不含亲缘维度）
        affinity_delta = 0.1
        if "守财" in tags:
            affinity_delta += 0.05 * tags["守财"]
        self.npc.relationships.update_affinity(
            self.npc.persona.id, "player", affinity_delta)

    def _on_player_speak(self, state: InnerState, tags: dict, text: str) -> None:
        # 基础：任何玩家说话都提升唤醒与压力负荷
        state.apply_delta("e_A", 0.05)
        state.apply_delta("S_stress", 0.05)
        # 互斥判断，先赞后批
        if any(w in text for w in PRAISE_KEYWORDS):
            # 赞美 → 六情"喜"
            self._apply_xunzi(state, "喜")
            if "较为自负" in tags:
                state.apply_delta("e_A", 0.15 * tags["较为自负"])
        elif any(w in text for w in CRITICISM_KEYWORDS):
            # 批评 → 六情"怒"
            self._apply_xunzi(state, "怒")
            if "较为自负" in tags:
                state.apply_delta("e_P", -0.15 * tags["较为自负"])

    def _on_npc_action(self, state: InnerState) -> None:
        state.apply_delta("p_fatigue", 0.02)
        state.apply_delta("e_A", -0.01)

    def _on_time_pass(self, state: InnerState) -> None:
        from .states import NPCState
        if self.npc.state_machine.state == NPCState.SLEEPING:
            state.apply_delta("p_fatigue", -0.05)
            state.apply_delta("S_stress", -0.02)
        else:
            state.apply_delta("p_fatigue", 0.01)
            state.apply_delta("p_hunger", 0.01)

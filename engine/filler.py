"""垫话引擎原型：脾气掩码 + 8D 当下状态 → 同步垫话占位。

设计要点
--------
- 垫话是慢脑 LLM 调用前同步返回的「反应性开场白」，0-token 本地计算，
  不进 LLM prompt（T3 快脑将让部分意图只返回垫话、跳过慢脑）。
- 脾气掩码粗粒度先行（基于现有 NPC 近似），T2 落地 8 脾气掩码后替换。
- 垫话必须同时消费「脾气掩码」与「8D 当下状态」——只查脾气不查状态
  即为「性格与状态两张皮」，审查点名打回。
- 状态带阈值同源：lambda 全部引用 engine/inner_state.py 的 BAND_*
  常量（同一心理现象一套阈值，消除阈值共线性；数值不变，行为零变化）。
- 每条垫话模板 ≤15 token（按字符数计）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, List, Tuple

from .inner_state import (BAND_AROUSAL_HIGH, BAND_DOMINANCE_HIGH,
                          BAND_FATIGUE_HIGH, BAND_PLEASURE_HIGH,
                          BAND_PLEASURE_LOW, BAND_STRESS_HIGH)

if TYPE_CHECKING:  # 仅为类型注解服务，避免运行期循环导入
    from .inner_state import InnerState

# 脾气掩码：id → 规则列表。每条规则 = (状态测试函数, 垫话模板)。
# 状态测试函数接收 InnerState，返回 bool；按顺序匹配首个命中。
# 状态带阈值引用 inner_state.BAND_*（单一来源，非本模块私有数字）。
TEMPERAMENT_MASKS: Dict[str, List[Tuple[Callable[["InnerState"], bool], str]]] = {
    "irritable": [   # 暴躁倾向 — 铁匠陈
        (lambda s: s.S_stress > BAND_STRESS_HIGH, "（皱眉）找老夫何事？"),   # 高压烦躁
        (lambda s: s.e_A > BAND_AROUSAL_HIGH,     "（抡锤）说！"),            # 高唤醒激动
        # default
        (lambda s: True,                          "（抬眼）何事？"),
    ],
    "cheerful": [    # 喜悦倾向 — 商人莉莉
        (lambda s: s.e_P > BAND_PLEASURE_HIGH,    "（笑盈盈）客官来啦！"),    # 愉悦
        (lambda s: s.e_P < BAND_PLEASURE_LOW,     "（勉强笑）有何吩咐？"),    # 低愉悦
        (lambda s: True,                          "（热情）里边请！"),
    ],
    "aloof": [       # 沉稳/冷淡 — 预留第 3 掩码（测试用）
        (lambda s: s.e_D > BAND_DOMINANCE_HIGH,   "（淡淡一瞥）说吧。"),      # 高支配傲慢
        (lambda s: s.p_fatigue > BAND_FATIGUE_HIGH, "（揉眉）……有事？"),      # 疲惫
        (lambda s: True,                          "（平静）何事？"),
    ],
}


class FillerEngine:
    """单个 NPC 的垫话引擎：脾气掩码 + 8D 状态 → 垫话字符串。"""

    def __init__(self, temperament_id: str = "") -> None:
        self.temperament_id = temperament_id

    def generate(self, inner_state: "InnerState") -> str:
        """根据脾气掩码与 8D 当下状态生成垫话。

        - 无掩码（空串或未注册）返回空串（向后兼容，不影响既有 NPC）。
        - 命中掩码后按规则顺序匹配首个 True 的状态带，返回对应垫话。
        - 垫话 ≤15 字符（token）。
        """
        if not self.temperament_id:
            return ""
        rules = TEMPERAMENT_MASKS.get(self.temperament_id)
        if not rules:
            return ""
        for test, template in rules:
            if test(inner_state):
                return template
        return ""  # 理论不可达（每表都有 default True），保底

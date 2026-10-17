"""垫话引擎原型：脾气掩码 + 8D 当下状态 → 同步垫话占位。

设计要点
--------
- 垫话是慢脑 LLM 调用前同步返回的「反应性开场白」，0-token 本地计算，
  不进 LLM prompt（T3 快脑将让部分意图只返回垫话、跳过慢脑）。
- 脾气掩码粗粒度先行（基于现有 NPC 近似），T2 落地 8 脾气掩码后替换。
- 垫话必须同时消费「脾气掩码」与「8D 当下状态」——只查脾气不查状态
  即为「性格与状态两张皮」，审查点名打回。
- T6 第一小步：烂醉 NPC 走醉变体池 DRUNK_FILLERS（engine/intoxication
   提供，每条 ≤15 字符），醉态覆盖脾气常态（PM 裁定：烂醉的 NPC 不再
   分暴躁喜悦，只有醉话）；未醉路径逐字节不变（默认参数向后兼容）。
- T6 第二小步：勒索余波期敌意前缀——HOSTILE_PREFIXES 池复用
   （engine/background_npc 同一文案池零平行机制），冷哼轮转每次
   说话甩冷脸；PM 裁定优先级 drunk > hostile > 脾气常态：烂醉连话
   都说不清何谈记仇甩脸，记仇的不再热络寒暄只有冷脸；未 hostile
   路径逐字节不变（默认参数向后兼容）。
- 状态带阈值同源：lambda 全部引用 engine/inner_state.py 的 BAND_*
  常量（同一心理现象一套阈值，消除阈值共线性；数值不变，行为零变化）。
- 每条垫话模板 ≤15 token（按字符数计）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, List, Tuple

from .inner_state import (BAND_AROUSAL_HIGH, BAND_DOMINANCE_HIGH,
                           BAND_FATIGUE_HIGH, BAND_PLEASURE_HIGH,
                           BAND_PLEASURE_LOW, BAND_STRESS_HIGH)
from .intoxication import DRUNK_FILLERS
from .background_npc import HOSTILE_PREFIXES

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
        # T6 第一小步：醉变体轮转计数（DRUNK_FILLERS 池按取模轮转）
        self._drunk_count = 0
        # T6 第二小步：敌意前缀轮转计数（HOSTILE_PREFIXES 池按取模轮转）
        self._hostile_count = 0

    def generate(self, inner_state: "InnerState", drunk: bool = False,
                 hostile: bool = False) -> str:
        """根据脾气掩码与 8D 当下状态生成垫话。

        - drunk=True：从 DRUNK_FILLERS 按计数取模轮转（返回后计数 +1），
          忽略脾气掩码（PM 裁定：醉态覆盖脾气常态——烂醉的 NPC 不再
          分暴躁喜悦，只有醉话）；
        - hostile=True（且未烂醉）：从 HOSTILE_PREFIXES 按计数取模轮转
          （返回后计数 +1），冷哼轮转每次说话甩冷脸（T6 第二小步：
          勒索余波期敌意前缀，池复用背景层同一文案源）；
        - drunk=False 且 hostile=False：掩码路径逐字节不变（默认参数
          向后兼容，既有调用与测试零改动）；
        - 无掩码（空串或未注册）返回空串（向后兼容，不影响既有 NPC）。
        - 命中掩码后按规则顺序匹配首个 True 的状态带，返回对应垫话。
        - 垫话 ≤15 字符（token）。
        """
        if drunk:
            text = DRUNK_FILLERS[self._drunk_count % len(DRUNK_FILLERS)]
            self._drunk_count += 1
            return text
        if hostile:
            # T6 第二小步：勒索余波期敌意前缀（HOSTILE_PREFIXES 池复用，冷哼轮转
            # ——每次说话甩冷脸）。PM 裁定优先级 drunk > hostile > 脾气常态：
            # 烂醉连话都说不清何谈记仇甩脸；记仇的不再热络寒暄，只有冷脸。
            # 每条前缀 ≤7 字符（实测池最大"（把脸别过去）"=7；垫话 ≤15 字符纪律内）。
            text = HOSTILE_PREFIXES[self._hostile_count % len(HOSTILE_PREFIXES)]
            self._hostile_count += 1
            return text
        if not self.temperament_id:
            return ""
        rules = TEMPERAMENT_MASKS.get(self.temperament_id)
        if not rules:
            return ""
        for test, template in rules:
            if test(inner_state):
                return template
        return ""  # 理论不可达（每表都有 default True），保底

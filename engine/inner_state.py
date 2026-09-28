"""内状态层：量化内心状态 + 事件驱动规则引擎更新器。

设计要点
--------
- InnerState 是 NPC 的实时心理参数向量，值域 [0, 1]。
- StateUpdater 作为事件总线订阅者，按确定性规则更新参数。
- 规则引擎零第三方依赖，全部逻辑可单测。
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .world import WorldEvent

PARAM_NAMES = {
    "arousal": "兴奋度",
    "mood": "心情",
    "energy": "精力",
    "stress": "压力",
    "trust": "信任",
}

PRAISE_KEYWORDS = ("好", "厉害", "棒", "了不起", "手艺", "牛", "真行")
CRITICISM_KEYWORDS = ("不对", "不好", "差", "烂", "质疑", "垃圾", "假")


@dataclass
class InnerState:
    """NPC 实时心理参数向量，值域 [0, 1]。"""
    arousal: float = 0.3   # 兴奋度
    mood: float = 0.5       # 心情
    energy: float = 0.8     # 精力
    stress: float = 0.2     # 压力
    trust: float = 0.5      # 信任

    PARAMS = ("arousal", "mood", "energy", "stress", "trust")

    def apply_delta(self, param: str, delta: float) -> None:
        """安全增量，自动 clamp 到 [0, 1]。"""
        current = getattr(self, param)
        setattr(self, param, max(0.0, min(1.0, current + delta)))

    def to_dict(self) -> dict:
        """序列化为字典，值保留 4 位小数。"""
        return {p: round(getattr(self, p), 4) for p in self.PARAMS}

    def to_prompt_text(self) -> str:
        """生成注入决策上下文的中文文本。"""
        return "，".join(f"{PARAM_NAMES[p]}: {getattr(self, p):.2f}" for p in self.PARAMS)


class StateUpdater:
    """事件驱动内状态更新器（规则引擎）。零第三方依赖。

    作为 EventBus 的订阅者，按确定性规则更新 NPC 的 InnerState。
    每条规则确定性可测——给定相同事件和标签，参数增量恒定。
    """

    def __init__(self, npc) -> None:
        self.npc = npc

    def on_event(self, event: "WorldEvent") -> None:
        """处理事件，更新 NPC 内状态。"""
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

    def _on_gift(self, state: InnerState, tags: dict) -> None:
        trust_delta = 0.1
        if "守财" in tags:
            trust_delta += 0.05 * tags["守财"]
        state.apply_delta("trust", trust_delta)
        state.apply_delta("mood", 0.05)
        state.apply_delta("stress", -0.05)

    def _on_player_speak(self, state: InnerState, tags: dict, text: str) -> None:
        state.apply_delta("arousal", 0.05)
        state.apply_delta("stress", 0.05)
        if "较为自负" in tags:
            if any(w in text for w in PRAISE_KEYWORDS):
                state.apply_delta("arousal", 0.15 * tags["较为自负"])
            elif any(w in text for w in CRITICISM_KEYWORDS):
                state.apply_delta("mood", -0.15 * tags["较为自负"])

    def _on_npc_action(self, state: InnerState) -> None:
        state.apply_delta("energy", -0.02)
        state.apply_delta("arousal", -0.01)

    def _on_time_pass(self, state: InnerState) -> None:
        from .states import NPCState
        if self.npc.state_machine.state == NPCState.SLEEPING:
            state.apply_delta("energy", 0.05)
            state.apply_delta("stress", -0.02)
        else:
            state.apply_delta("energy", -0.01)

"""行动层：动作定义、白名单校验与执行。

设计要点
--------
- 动作类型是**封闭枚举**（白名单），LLM 只能从中选择。
- ``ActionValidator`` 做两级校验：状态机一致性 + 世界一致性。
- ``ActionExecutor`` 执行动作并产出新的 ``WorldEvent`` 回流事件总线，
  其他 NPC 因此能感知到彼此的行为（多智能体的地基）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from .states import NPCState
from .world import World, WorldEvent


class ActionType(str, Enum):
    SPEAK = "speak"          # 说话
    MOVE = "move"            # 移动到地点
    GIVE_ITEM = "give_item"  # 赠送物品
    EMOTE = "emote"          # 表情/动作描述
    REFUSE = "refuse"        # 拒绝回应


ACTION_WHITELIST = {a.value for a in ActionType}


@dataclass
class Action:
    """一个待执行的动作。"""

    type: ActionType
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"action": self.type.value, **self.payload}


class ActionValidator:
    """白名单 + 一致性校验，防止 LLM 输出破坏游戏规则。"""

    @staticmethod
    def validate(action: Action, npc, world: World) -> Tuple[bool, str]:
        # 1) 类型白名单
        if action.type not in ActionType:
            return False, "unknown action type"

        # 2) 状态机一致性：睡觉中不能主动说话/移动/送东西
        if npc.state_machine.state is NPCState.SLEEPING and action.type in (
                ActionType.SPEAK, ActionType.MOVE, ActionType.GIVE_ITEM):
            return False, "sleeping npc cannot act"

        # 3) 世界一致性
        if action.type is ActionType.GIVE_ITEM:
            item = action.payload.get("item")
            if not item or item not in npc.entity.inventory:
                return False, "npc does not own the item"

        if action.type is ActionType.MOVE:
            to = action.payload.get("to")
            if to and to not in world.locations:
                return False, "unknown location"

        if action.type is ActionType.SPEAK and not action.payload.get("text"):
            return False, "speak without text"

        return True, "ok"


class ActionExecutor:
    """执行动作并把效果写回世界。"""

    def __init__(self, world: World) -> None:
        self.world = world

    def execute(self, action: Action, npc) -> Optional[str]:
        """执行动作；返回面向玩家的展示文本。"""
        world = self.world
        tick = world.tick_count
        npc_id = npc.persona.id

        if action.type is ActionType.SPEAK:
            text = str(action.payload.get("text", ""))
            world.bus.publish(WorldEvent(
                tick, "npc_action", npc_id,
                {"summary": f"{npc.persona.name} 说了话", "npc": npc_id}))
            npc.state_machine.enter_talking()
            return text

        if action.type is ActionType.EMOTE:
            emote = str(action.payload.get("text", ""))
            world.bus.publish(WorldEvent(
                tick, "npc_action", npc_id,
                {"summary": f"{npc.persona.name} {emote}", "npc": npc_id}))
            return f"（{emote}）"

        if action.type is ActionType.GIVE_ITEM:
            item = action.payload.get("item", "")
            if world.transfer_item(npc_id, "player", item):
                return f"{npc.persona.name} 把 {item} 递了过来。"
            return f"{npc.persona.name} 想给你点什么，但没有成功。"

        if action.type is ActionType.MOVE:
            to = action.payload.get("to", "")
            if world.move_entity(npc_id, to):
                return f"{npc.persona.name} 走向了 {world.locations[to].name}。"
            return None

        if action.type is ActionType.REFUSE:
            return (f"{npc.persona.name} 没有回应。"
                    f"（{npc.persona.name} 正在{npc.state_machine.state.value}）")

        return None

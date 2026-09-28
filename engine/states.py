"""NPC 行为状态机：确定性硬规则层。

状态机与 LLM 的分工（本项目核心设计）：
- 状态机负责"能不能做"——可测试、可断言的硬约束；
- LLM 负责"怎么说、怎么做"——柔性表达。
"""

from __future__ import annotations

from enum import Enum
from typing import List, Set


class NPCState(str, Enum):
    IDLE = "发呆"
    WORKING = "干活"
    TALKING = "交谈"
    SLEEPING = "睡觉"


# 允许的状态迁移（睡觉只能自然醒来）
TRANSITIONS: dict = {
    NPCState.IDLE: {NPCState.TALKING, NPCState.WORKING, NPCState.SLEEPING},
    NPCState.WORKING: {NPCState.IDLE, NPCState.TALKING, NPCState.SLEEPING},
    NPCState.TALKING: {NPCState.IDLE, NPCState.WORKING},
    NPCState.SLEEPING: {NPCState.IDLE},
}


class StateMachine:
    """极简状态机：只放行 TRANSITIONS 中声明的迁移。"""

    def __init__(self, initial: NPCState = NPCState.IDLE) -> None:
        self.state = initial

    def can(self, target: NPCState) -> bool:
        return target in TRANSITIONS.get(self.state, set())

    def transition(self, target: NPCState) -> bool:
        if self.can(target):
            self.state = target
            return True
        return False

    def force(self, target: NPCState) -> None:
        """外部调度强制置位（如作息表），绕过迁移约束。"""
        self.state = target

    # 常用便捷迁移 -------------------------------------------------------
    def enter_talking(self) -> bool:
        return self.transition(NPCState.TALKING)

    def exit_talking(self) -> bool:
        return self.transition(NPCState.IDLE) or self.transition(NPCState.WORKING)

    # 状态对动作的约束 ---------------------------------------------------
    def blocks_speech(self) -> bool:
        return self.state is NPCState.SLEEPING

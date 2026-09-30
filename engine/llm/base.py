"""LLM Provider 抽象接口。

引擎上层只依赖 ``chat(messages) -> str`` 这一个方法，
因此任何模型/协议都可以通过实现本接口接入。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Dict, Optional


class LLMError(RuntimeError):
    """LLM 调用异常（网络/鉴权/超时等）。"""


class BaseLLMProvider(ABC):
    """所有 Provider 的基类。"""

    name: str = "base"
    base_url: Optional[str] = None
    model: Optional[str] = None

    @property
    def available(self) -> bool:
        return True

    @abstractmethod
    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
        """输入 OpenAI 格式的消息列表，返回 assistant 文本。"""
        raise NotImplementedError

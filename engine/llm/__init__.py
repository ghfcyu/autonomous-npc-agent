"""可插拔 LLM Provider。"""

from __future__ import annotations

import os
from typing import List, Optional

from .base import BaseLLMProvider
from .mock import MockLLMProvider
from .openai_compat import OpenAICompatProvider

__all__ = ["BaseLLMProvider", "MockLLMProvider", "OpenAICompatProvider", "create_provider"]


def create_provider(kind: Optional[str] = None) -> BaseLLMProvider:
    """工厂：按名称或环境变量选择 Provider。

    优先级：显式 kind > 环境变量 NPC_LLM_PROVIDER > 默认 mock。
    """
    kind = (kind or os.environ.get("NPC_LLM_PROVIDER") or "mock").lower()
    if kind == "openai":
        return OpenAICompatProvider()
    return MockLLMProvider()

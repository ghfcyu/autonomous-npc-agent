"""OpenAI 协议兼容 Provider（纯标准库 urllib 实现）。

环境变量：
    NPC_LLM_BASE_URL  端点，如 https://api.openai.com/v1（必填才启用）
    NPC_LLM_API_KEY   鉴权 Key
    NPC_LLM_MODEL     模型名
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from .base import BaseLLMProvider, LLMError

DEFAULT_TIMEOUT = 30


class OpenAICompatProvider(BaseLLMProvider):
    name = "openai-compat"

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self.base_url = (base_url or os.environ.get("NPC_LLM_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("NPC_LLM_API_KEY", "")
        self.model = model or os.environ.get("NPC_LLM_MODEL", "gpt-4o-mini")

    @property
    def available(self) -> bool:
        return bool(self.base_url)

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
        if not self.available:
            raise LLMError("NPC_LLM_BASE_URL 未配置")
        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:  # 网络层异常统一包装
            raise LLMError(f"LLM request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError("LLM returned non-JSON body") from exc

        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("unexpected LLM response shape") from exc

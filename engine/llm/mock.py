"""离线 Mock Provider：基于人格对话库的确定性实现。

用途：
- 无 API Key 的本地开发；
- 单元/集成测试的稳定依赖（同输入必同输出）。

实现：关键词匹配 persona.topic_responses，未命中则回落到
fallback_bank；首次交互用 greeting_bank。输出严格符合决策层的 JSON 契约，
也会按小概率故意输出**坏 JSON**（模拟真实 LLM 的解析失败场景，
用于验证决策层的安全回退路径）——可通过 ``chaos_rate=0`` 关闭。
"""

from __future__ import annotations

import json
import random
import re
from typing import Dict, List

from .base import BaseLLMProvider

_TOPIC_RE_CACHE: Dict[str, "re.Pattern"] = {}


class MockLLMProvider(BaseLLMProvider):
    name = "mock"

    def __init__(self, chaos_rate: float = 0.0, seed: int = 42) -> None:
        self.chaos_rate = chaos_rate
        self._rng = random.Random(seed)
        self._seen: Dict[str, int] = {}  # npc_id -> 交互次数
        self.call_count: int = 0
        self.call_log: List[str] = []  # 记录每次 chat 调用的 persona_id

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
        self.call_count += 1
        system = messages[0]["content"] if messages else ""
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")

        # 从 system 提示里解析人格标记（决策层写入的 <!--persona:xxx--> 注释）
        match = re.search(r"<!--persona:([\w-]+)-->", system)
        persona_id = match.group(1) if match else "unknown"
        if match:
            self.call_log.append(match.group(1))
        # 提取人格对话库（决策层以 JSON 附在 system 末尾）
        banks = self._extract_banks(system)

        if self.chaos_rate and self._rng.random() < self.chaos_rate:
            return "这不是JSON {{{ 我随便说说"

        count = self._seen.get(persona_id, 0)
        self._seen[persona_id] = count + 1

        # 话题匹配优先；首次交互且未命中话题时才打招呼
        text = self._match_topics(user, banks)
        if not text:
            if count == 0 and banks.get("greeting_bank"):
                text = self._pick(banks["greeting_bank"])
            else:
                text = self._pick(banks.get("fallback_bank") or ["……"])
        return json.dumps({"action": "speak", "text": text}, ensure_ascii=False)

    # ------------------------------------------------------------------ #
    def _extract_banks(self, system: str) -> Dict[str, List[str]]:
        marker = "<<<banks>>>"
        if marker not in system:
            return {}
        try:
            tail = system.split(marker, 1)[1]
            return json.loads(tail.strip())
        except json.JSONDecodeError:
            return {}

    def _match_topics(self, user: str, banks: Dict[str, List[str]]) -> str:
        for pattern, responses in (banks.get("topic_responses") or {}).items():
            regex = _TOPIC_RE_CACHE.setdefault(pattern, re.compile(pattern))
            if regex.search(user):
                return self._pick(responses)
        return ""

    def _pick(self, options: List[str]) -> str:
        return options[self._rng.randrange(len(options))] if options else "……"

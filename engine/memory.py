"""记忆层：短期记忆、长期记忆、检索与巩固。

设计要点
--------
- 短期记忆是定容 deque，模拟"当前心绪"。
- 长期记忆持久化为 JSON，检索评分 = 标签重合 + 关键词重合 + 重要度 + 时间衰减。
- ``MemorySystem.consolidate`` 在短期记忆溢出时把最旧的一批压缩成摘要，
  这是 NPC "记得你上次来过" 的机制基础。
- 检索接口刻意保持简单（输入查询串，返回条目列表），L2 阶段可整体替换为
  向量检索而不影响上层调用方。
"""

from __future__ import annotations

import json
import os
import re
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from .world import WorldEvent

SHORT_TERM_CAPACITY = 30
CONSOLIDATE_BATCH = 8
# 重要度达到该阈值的事件，在写入短期记忆的同时直接沉淀为长期记忆
# （如 item_given，importance=0.8），无需等待短期溢出后的 consolidate 压缩。
LONG_TERM_IMPORTANCE_THRESHOLD = 0.7
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")


@dataclass
class MemoryRecord:
    """一条记忆。"""

    content: str
    tick: int
    importance: float = 0.5  # 0.0 ~ 1.0
    tags: List[str] = field(default_factory=list)
    kind: str = "fact"  # fact / interaction / emotion / summary


def _tokens(text: str) -> set:
    return {t.lower() for t in _TOKEN_RE.findall(text)}


class ShortTermMemory:
    """定容的近期记忆。"""

    def __init__(self, capacity: int = SHORT_TERM_CAPACITY) -> None:
        self._deque: Deque[MemoryRecord] = deque(maxlen=capacity)

    def add(self, record: MemoryRecord) -> None:
        self._deque.append(record)

    def recent(self, n: Optional[int] = None) -> List[MemoryRecord]:
        items = list(self._deque)
        return items if n is None else items[-n:]

    def pop_oldest(self, n: int) -> List[MemoryRecord]:
        popped: List[MemoryRecord] = []
        for _ in range(min(n, len(self._deque))):
            popped.append(self._deque.popleft())
        return popped

    def __len__(self) -> int:
        return len(self._deque)


class LongTermMemory:
    """持久化长期记忆 + 评分检索。"""

    def __init__(self, path: Optional[str] = None, npc_id: str = "default") -> None:
        self.path = path
        self.npc_id = npc_id
        self.records: List[MemoryRecord] = []
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            self.records = [MemoryRecord(**item) for item in raw.get("records", [])]

    # ------------------------------------------------------------------ #
    def add(self, record: MemoryRecord) -> None:
        self.records.append(record)
        self._persist()

    def _persist(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"npc_id": self.npc_id,
                       "records": [asdict(r) for r in self.records]}, fh,
                      ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------ #
    def retrieve(self, query: str, top_k: int = 5) -> List[MemoryRecord]:
        """评分检索：标签重合 * 2 + 关键词重合 + 重要度 + 时间衰减。"""
        if not self.records:
            return []
        q_tokens = _tokens(query)
        q_tags = q_tokens
        max_tick = max(r.tick for r in self.records) or 1

        scored: List[Tuple[float, MemoryRecord]] = []
        for rec in self.records:
            tag_overlap = len(set(t.lower() for t in rec.tags) & q_tags)
            token_overlap = len(_tokens(rec.content) & q_tokens)
            recency = rec.tick / max_tick  # 0~1，越新越高
            score = tag_overlap * 2.0 + token_overlap * 1.0 + rec.importance * 0.5 + recency * 0.3
            scored.append((score, rec))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [rec for _, rec in scored[:top_k]]


class MemorySystem:
    """NPC 的完整记忆系统。"""

    def __init__(self, npc_id: str, store_dir: Optional[str] = None,
                 capacity: int = SHORT_TERM_CAPACITY) -> None:
        path = os.path.join(store_dir, f"{npc_id}.json") if store_dir else None
        self.short = ShortTermMemory(capacity)
        self.long = LongTermMemory(path, npc_id)
        self._npc_id = npc_id

    # ------------------------------------------------------------------ #
    # 感知写入
    # ------------------------------------------------------------------ #
    def observe(self, event: WorldEvent) -> None:
        """把世界事件转写为短期记忆；重要度达阈值的事件同时沉淀为长期记忆。"""
        templates = {
            "player_spoke": ("玩家说：{text}", 0.6),
            "item_given": ("收到来自 {actor} 的物品：{item}", 0.8),
            "player_entered": ("{actor}（{name}）来到了 {location}", 0.4),
            "entity_moved": ("{actor} 离开了", 0.2),
            "weather_changed": ("天气变成了 {weather}", 0.2),
            "npc_action": ("自己做了：{summary}", 0.3),
        }
        if event.kind not in templates:
            return
        template, importance = templates[event.kind]
        content = template.format(actor=event.actor, **event.payload)
        tags = [event.kind] + list(_tokens(event.actor))[:2]
        self.short.add(MemoryRecord(content=content, tick=event.tick,
                                    importance=importance, tags=tags, kind="interaction"))
        # 事件驱动长期写入：高价值事件（>= 阈值）直接沉淀为长期记忆，
        # 保留明细而不等待 consolidate 的有损压缩。
        if importance >= LONG_TERM_IMPORTANCE_THRESHOLD:
            self.long.add(MemoryRecord(content=content, tick=event.tick,
                                       importance=importance, tags=tags,
                                       kind="interaction"))

    # ------------------------------------------------------------------ #
    # 巩固
    # ------------------------------------------------------------------ #
    def consolidate(self) -> Optional[MemoryRecord]:
        """短期记忆接近满时，把最旧一批压缩成一条长期摘要。"""
        if len(self.short) < self.short._deque.maxlen:
            return None
        batch = self.short.pop_oldest(CONSOLIDATE_BATCH)
        if not batch:
            return None
        actors: Dict[str, int] = {}
        items, speaks = [], 0
        for rec in batch:
            if "收到来自" in rec.content:
                items.append(rec.content)
            if "玩家说" in rec.content:
                speaks += 1
            for tag in rec.tags:
                if tag not in ("player_spoke", "item_given", "player_entered",
                               "entity_moved", "weather_changed", "npc_action"):
                    actors[tag] = actors.get(tag, 0) + 1
        parts = []
        if speaks:
            parts.append(f"与玩家交谈了 {speaks} 次")
        if items:
            parts.append("、".join(items))
        summary = "这段经历里：" + "；".join(parts) if parts else "平静地过了一段时间"
        record = MemoryRecord(content=summary, tick=batch[-1].tick,
                               importance=0.7, tags=list(actors.keys()) or ["日常"],
                               kind="summary")
        self.long.add(record)
        return record

    # ------------------------------------------------------------------ #
    # 决策上下文
    # ------------------------------------------------------------------ #
    def context_for(self, query: str) -> Dict[str, List[str]]:
        """为决策层组装记忆上下文。"""
        related = self.long.retrieve(query, top_k=3)
        return {
            "long_term": [r.content for r in related],
            "recent": [r.content for r in self.short.recent(6)],
        }

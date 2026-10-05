"""关系网络：NPC 之间的社会关系存储与查询。"""
from __future__ import annotations
import json, os
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Relationship:
    source_id: str       # 拥有此关系的 NPC ID
    target_id: str       # 关系指向的 NPC ID
    relation: str        # 关系类型：父亲/熟人/ rival 等
    affinity: float = 0.0   # -1.0~1.0，正=好感，负=敌意
    target_name: str = ""   # 显示名，空则用 target_id


class RelationshipNetwork:
    def __init__(self, relationships: Optional[List[Relationship]] = None) -> None:
        self._relationships: List[Relationship] = list(relationships) if relationships else []

    def add(self, rel: Relationship) -> None:
        """添加一条关系。"""
        self._relationships.append(rel)

    def query(self, source_id: str) -> List[Relationship]:
        """查询某 NPC 的所有关系。"""
        return [r for r in self._relationships if r.source_id == source_id]

    def query_by_relation(self, source_id: str, relation: str) -> List[Relationship]:
        """查询某 NPC 指定类型的关系。"""
        return [r for r in self._relationships
                if r.source_id == source_id and r.relation == relation]

    def get_relation_to(self, source_id: str, target_id: str) -> Optional[Relationship]:
        """查询两个 NPC 之间的特定关系。"""
        for r in self._relationships:
            if r.source_id == source_id and r.target_id == target_id:
                return r
        return None

    def update_affinity(self, source_id: str, target_id: str, delta: float) -> None:
        """更新 source→target 的亲缘度，clamp 到 [-1.0, 1.0]。关系不存在则忽略。"""
        rel = self.get_relation_to(source_id, target_id)
        if rel is not None:
            rel.affinity = max(-1.0, min(1.0, rel.affinity + delta))

    def to_prompt_text(self, source_id: str) -> str:
        """生成注入 LLM 决策上下文的关系描述文本。无关系时返回空字符串。

        格式示例：
        【人际关系】
        你的父亲是老张（好感 0.9）
        你的熟人是王守卫（关系一般 0.3）
        """
        rels = self.query(source_id)
        if not rels:
            return ""
        lines = []
        for r in rels:
            name = r.target_name or r.target_id
            if r.affinity > 0.3:
                desc = f"好感 {r.affinity:.1f}"
            elif r.affinity < -0.3:
                desc = f"敌意 {abs(r.affinity):.1f}"
            else:
                desc = f"关系一般 {r.affinity:.1f}"
            lines.append(f"你的{r.relation}是{name}（{desc}）")
        return "【人际关系】\n" + "\n".join(lines)

    @classmethod
    def from_file(cls, path: str) -> "RelationshipNetwork":
        """从 JSON 文件加载（JSON 数组格式）。"""
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return cls([Relationship(**item) for item in data])

    @classmethod
    def from_list(cls, data: List[Dict[str, Any]]) -> "RelationshipNetwork":
        """从字典列表加载。"""
        return cls([Relationship(**item) for item in data])

    def to_dict(self) -> List[Dict[str, Any]]:
        return [asdict(r) for r in self._relationships]

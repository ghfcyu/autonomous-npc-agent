"""决策层：状态机（硬规则）+ LLM（柔性表达）的混合决策。

流程
----
1. 硬规则前置：睡觉中的 NPC 直接产出梦呓回退动作，不调用 LLM。
2. 上下文组装：人格卡 → 记忆上下文 → 世界快照 → 玩家输入 → 输出契约。
3. LLM 生成结构化 JSON 动作。
4. 解析 + 白名单校验；任何失败都落到人格 fallback_bank 的安全回退。

不变式：**LLM 的输出永远不直接生效**，必须经过 ActionValidator。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from .actions import Action, ActionType, ActionValidator
from .llm.base import BaseLLMProvider, LLMError
from .relationships import RelationshipNetwork
from .world import APPEARANCE_KEY_CN, World

OUTPUT_CONTRACT = """你只能输出一个 JSON 对象（不要输出任何其他文字），格式：
{"action": "speak", "text": "你说的话"}
可选动作：
- {"action": "speak", "text": "..."}            说话（最常用）
- {"action": "emote", "text": "动作描写"}        做一个动作/表情
- {"action": "give_item", "item": "物品名"}      送玩家物品（必须是你拥有的）
- {"action": "refuse"}                          拒绝回应
禁止编造不存在的物品和地点。保持角色的说话风格。"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class DecisionEngine:
    """单个 NPC 的决策引擎。"""

    def __init__(self, persona, llm: BaseLLMProvider,
                 relationships: Optional[RelationshipNetwork] = None) -> None:
        self.persona = persona
        self.llm = llm
        self.relationships = relationships

    # ------------------------------------------------------------------ #
    # 提示词组装
    # ------------------------------------------------------------------ #
    def _system_prompt(self, ledger=None) -> str:
        p = self.persona
        banks = {
            "greeting_bank": p.greeting_bank,
            "fallback_bank": p.fallback_bank,
            "topic_responses": p.topic_responses,
        }
        tag_line = ""
        if p.tags:
            tag_line = f"\n性格标签：{'、'.join(f'{k}({v})' for k, v in p.tags.items())}"
        # T2 身份标签行：仅当账本存在且可见标签非空时注入（空账本零注入，
        # 与【周围的人】块空不注入同一纪律）。渲染 visible_tags() 的
        # 离散中文名，顿号连接，严禁浮点权重（<35token 极简组装规范）；
        # 绝密把柄被 visible_tags() 排除，永不进入决策上下文。
        identity_line = ""
        if ledger is not None:
            labels = [t.label for t in ledger.visible_tags()]
            if labels:
                identity_line = f"\n身份标签：{'、'.join(labels)}"
        rel_line = ""
        if self.relationships:
            rel_text = self.relationships.to_prompt_text(self.persona.id)
            if rel_text:
                rel_line = f"\n{rel_text}\n"
        return (
            f"<!--persona:{p.id}-->\n"
            f"你是游戏里的 NPC「{p.name}」，职业：{p.role}。\n"
            f"性格：{p.personality}\n"
            f"说话风格：{p.speech_style}\n"
            f"背景：{p.backstory}\n"
            f"喜欢：{'、'.join(p.likes)}；讨厌：{'、'.join(p.dislikes)}\n"
            f"{tag_line}{identity_line}{rel_line}\n\n"
            f"{OUTPUT_CONTRACT}\n"
            f"<<<banks>>>{json.dumps(banks, ensure_ascii=False)}"
        )

    def _user_prompt(self, player_input: str, world: World,
                     memory_ctx: Dict[str, list], snapshot: Dict[str, Any],
                     inner_state=None) -> str:
        long_term = "\n".join(f"- {m}" for m in memory_ctx.get("long_term", [])) or "（无）"
        recent = "\n".join(f"- {m}" for m in memory_ctx.get("recent", [])) or "（无）"
        state_line = ""
        if inner_state is not None:
            state_line = f"【此刻内心】{inner_state.to_prompt_text()}\n"
        # 周围的人：同地点可见的实体及其外观（空 nearby 不注入，控 token）
        nearby_line = ""
        if snapshot.get("nearby"):
            rows = []
            for e in snapshot["nearby"]:
                if e.get("appearance"):
                    look = "、".join(f"{APPEARANCE_KEY_CN.get(k, k)}{v}"
                                     for k, v in e["appearance"].items())
                    rows.append(f"{e['name']}（{look}）")
                else:
                    rows.append(e["name"])
            nearby_line = "【周围的人】\n" + "\n".join(rows) + "\n"
        return (
            f"【相关长期记忆】\n{long_term}\n\n"
            f"【最近的经历】\n{recent}\n\n"
            f"【当前世界】时间 {snapshot.get('clock')}，天气 {snapshot.get('weather')}，"
            f"你在 {snapshot.get('my_location')}\n"
            f"{nearby_line}"
            f"{state_line}"
            f"【玩家说】{player_input}"
        )

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def decide(self, npc, world: World, player_input: str) -> Action:
        sm = npc.state_machine

        # 1) 硬规则：睡觉 → 梦呓回退，不消耗 LLM
        if sm.blocks_speech():
            return Action(ActionType.SPEAK, {"text": self.persona.sleep_mumble})

        # 1.5) 硬规则：内状态硬约束（压力负荷过高拒绝接单，疲劳过高提前收摊）
        istate = npc.inner_state
        if istate.S_stress > 0.8:
            return Action(ActionType.REFUSE, {"reason": "stress_too_high"})
        if istate.p_fatigue > 0.85:
            return Action(ActionType.REFUSE, {"reason": "fatigue_too_high"})

        memory_ctx = npc.memory.context_for(player_input)
        snapshot = world.snapshot(npc.persona.id)
        # T2：挂载了标签账本的 NPC 注入身份标签行（getattr 兼容未挂载实例）
        messages = [
            {"role": "system", "content": self._system_prompt(getattr(npc, "tag_ledger", None))},
            {"role": "user", "content": self._user_prompt(player_input, world, memory_ctx, snapshot, npc.inner_state)},
        ]

        # 2) LLM 生成 + 解析
        action = self._generate(messages)
        # 3) 白名单校验，失败即安全回退
        ok, _ = ActionValidator.validate(action, npc, world)
        if not ok:
            return self._fallback()
        return action

    def _generate(self, messages) -> Action:
        try:
            raw = self.llm.chat(messages, temperature=0.8)
            parsed = self._parse_json(raw)
            action_type = parsed.get("action")
            if action_type not in {a.value for a in ActionType}:
                raise ValueError(f"action {action_type!r} not in whitelist")
            payload = {k: v for k, v in parsed.items() if k != "action"}
            return Action(ActionType(action_type), payload)
        except (LLMError, ValueError, TypeError, json.JSONDecodeError):
            return self._fallback()

    @staticmethod
    def _parse_json(raw: str) -> Dict[str, Any]:
        match = _JSON_RE.search(raw)
        if not match:
            raise ValueError("no JSON object in LLM output")
        return json.loads(match.group(0))

    def _fallback(self) -> Action:
        text = self.persona.fallback_bank[0] if self.persona.fallback_bank else "……"
        return Action(ActionType.SPEAK, {"text": text})

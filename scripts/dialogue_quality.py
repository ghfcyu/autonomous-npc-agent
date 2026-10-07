"""对话质量首批 2 指标：记忆引用率 + 状态影响可见性（Mock 确定性基线）。

项目审查指令（10-08 22:00 交付）：让"对话真实"可度量——
① NPC 回复是否引用了与玩家的历史记忆（而非复读话术库）；
② NPC 的 8D 内状态变化是否在玩家可感知的输出中可见。

指标口径（精确）
================
指标① 记忆引用率 memory_reference_rate
    分母：记忆场景链数。每链 = ``player_gives(npc, item)`` 送礼建立长期记忆
          → ``player_says(探询文本, npc)`` 探询该记忆。送礼后校验 NPC 长期
          记忆确实包含该物品（校验失败的链仍计入分母，但分子必不计——
          记忆都没写进去，谈不上"引用"）。
    分子：命中链数。命中 = J1 或 J2 任一成立：
      J1 注入层：本次决策 user prompt 的【相关长期记忆】块内出现该场景
         的核心词（core_terms）——证明记忆被检索并送达决策点；
      J2 回复层：NPC 最终回复文本（result["reply"]）出现核心词——玩家
         直接可感知的表面引用。
    细分子分：J1 = inject 子分（管道健全性）；J2 = surface 子分。
    Mock 局限（docstring 必读）：``engine/llm/mock.py`` 的话题匹配只消费
    【玩家说】段，不读【相关记忆】块，故 J2 命中只能来自话术库模板与
    探询文本的词面巧合。脚本附"无记忆对照"：不送礼直接探询同样的话，
    若 J2 仍命中，即证明该命中与记忆无关、是话术库复读——J2 因此正是
    "是否复读话术库"的度量，真实 LLM 接入后应以 J2 提升为对照目标。

指标② 状态影响可见性 state_visibility_rate
    分母：状态分叉场景对数。每对 = 同一 NPC、相同玩家输入，仅预置 8D
          内状态不同（预置键必须是 InnerState.PARAMS 中的 8D 字段，
          旧 5D 字段直接抛 ValueError 拒绝）。
    分子：两侧玩家可感知输出可区分的对数。可区分 = C1 或 C2 或 C3 任一：
      C1 动作分叉：result["action"] 字典不同（含 type / reason）——
         硬约束（S_stress>0.8 → REFUSE "stress_too_high"；
         p_fatigue>0.85 → REFUSE "fatigue_too_high"）落在这里；
      C2 垫话分叉：result["filler"] 文本不同——player_says 直接返回给
         玩家，FillerEngine 消费事件后 8D（irritable 掩码 S_stress>0.6
         分带、cheerful 掩码 e_P 分带）；
      C3 内心注入分叉：决策 user prompt 的【此刻内心】行不同——真实
         LLM 下该差异传导为回复差异的通道；硬约束 REFUSE 侧不调用
         LLM、无 prompt，C3 记 N/A（不算命中也不算失败）。
    维度覆盖：S_stress 硬约束对、p_fatigue 硬约束对（chen/lily 各一），
    非硬约束可见带对（chen S_stress 压力带 → C3；lily e_P 愉悦带 → C2+C3）。

结果确定性：所有场景使用 ``MockLLMProvider(seed=42, chaos_rate=0)``，
每链/每侧独立 fresh engine，相同输入恒同分，连续运行可复现。

用法：
    python3 scripts/dialogue_quality.py                  # Mock 基线
    python3 scripts/dialogue_quality.py --provider real  # 真实 LLM 对照
    #   （接入真实端点，产出五口径 Mock vs Real 对照报告+差异归因；
    #    10-08 落地：J2 回复层 1/6→2/6 刺破 Mock 话术库巧合命中）

零第三方依赖：仅标准库 + engine/ 公开接口。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.engine import NPCEngine                      # noqa: E402
from engine.inner_state import InnerState                # noqa: E402
from engine.llm.mock import MockLLMProvider              # noqa: E402
from engine.npc import Persona                           # noqa: E402

# ------------------------------------------------------------------ #
# 探针 LLM：ProbeLLM（子类化 Mock）/ RealProbeLLM（组合真实 Provider）
# —— 都只捕获 messages、不改变行为，指标不被测量行为本身污染
# ------------------------------------------------------------------ #


class ProbeLLM(MockLLMProvider):
    """MockLLMProvider 探针：chat 时记录决策层发来的完整 messages。

    只捕获、不改变行为（super().chat 照常执行），保证 Mock 输出序列
    与未插桩时完全一致，指标不被测量行为本身污染。
    """

    def __init__(self) -> None:
        super().__init__(chaos_rate=0.0, seed=42)
        self.last_messages: Optional[List[Dict[str, str]]] = None

    def chat(self, messages, temperature: float = 0.7) -> str:
        self.last_messages = [dict(m) for m in messages]
        return super().chat(messages, temperature)

    def last_user_prompt(self) -> Optional[str]:
        """最近一次决策的 user prompt（未调用过 LLM 时为 None）。"""
        if not self.last_messages:
            return None
        return next((m["content"] for m in reversed(self.last_messages)
                     if m["role"] == "user"), None)


class RealProbeLLM:
    """真实 LLM 探针：包装真实 Provider，捕获 messages 供 prompt 分析。

    与 ProbeLLM（子类化 Mock）不同，RealProbeLLM 通过组合而非继承，
    因为真实 Provider 的 chat() 发 HTTP 请求。每次创建实例用于单个场景，
    捕获该场景的 messages；底层 real provider 可跨场景共享。

    不继承 BaseLLMProvider：避免 ABC 抽象方法约束和 __init__ 中
    last_usage/total_tokens_used 与只读 property 的冲突。NPCEngine 用
    getattr 读取这些属性、只调用 chat()，不检查 isinstance（鸭子类型）。
    """

    def __init__(self, inner) -> None:
        self.inner = inner
        self.last_messages: Optional[List[Dict[str, str]]] = None

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def available(self) -> bool:
        return getattr(self.inner, "available", True)

    @property
    def last_usage(self):
        return self.inner.last_usage

    @property
    def total_tokens_used(self):
        return self.inner.total_tokens_used

    @property
    def call_count(self):
        return getattr(self.inner, "call_count", 0)

    def chat(self, messages, temperature: float = 0.7) -> str:
        self.last_messages = [dict(m) for m in messages]
        return self.inner.chat(messages, temperature)

    def last_user_prompt(self) -> Optional[str]:
        if not self.last_messages:
            return None
        return next((m["content"] for m in reversed(self.last_messages)
                     if m["role"] == "user"), None)


# ------------------------------------------------------------------ #
# 口径纯函数（测试直接断言）
# ------------------------------------------------------------------ #


def hits_terms(text: str, terms: List[str]) -> bool:
    """判定文本是否命中任一核心词（子串匹配，空文本/空词表恒 False）。"""
    if not text or not terms:
        return False
    return any(t and t in text for t in terms)


def extract_block(prompt: str, marker: str) -> str:
    """提取 prompt 中 marker 标记块的内容（marker 后到首个空行）。"""
    if not prompt or marker not in prompt:
        return ""
    tail = prompt.split(marker, 1)[1].lstrip("\n")
    end = tail.find("\n\n")
    return tail[:end] if end != -1 else tail


_INNER_RE = re.compile(r"【此刻内心】(.+)")


def extract_inner_line(prompt: Optional[str]) -> Optional[str]:
    """提取【此刻内心】行；prompt 为 None（未调用 LLM）返回 None。"""
    if prompt is None:
        return None
    m = _INNER_RE.search(prompt)
    return m.group(1).strip() if m else None


@dataclass
class SideRecord:
    """一次 player_says 的玩家可感知输出切片（指标②比较单元）。"""

    action: Dict[str, Any]
    filler: str
    inner: Optional[str]   # 【此刻内心】行；未调用 LLM（硬约束拒绝）时 None


def distinguish(a: SideRecord, b: SideRecord) -> Tuple[bool, Dict[str, Optional[bool]]]:
    """判定状态分叉对两侧输出是否可区分。

    通道（可区分 = 任一 True）：
    - C1 action：动作字典不同（type/payload/reason，玩家最直接感知）
    - C2 filler：垫话文本不同（player_says 直接返回玩家）
    - C3 inner：【此刻内心】注入行不同（真实 LLM 传导通道）；
      任一侧 inner 为 None（未调 LLM）时 C3 = None（N/A）。
    """
    c1: bool = a.action != b.action
    c2: bool = a.filler != b.filler
    c3: Optional[bool]
    if a.inner is None or b.inner is None:
        c3 = None
    else:
        c3 = a.inner != b.inner
    visible = bool(c1 or c2 or (c3 is True))
    return visible, {"C1_action": c1, "C2_filler": c2, "C3_inner": c3}


def score(numerator: int, denominator: int) -> float:
    """分数 = 分子/分母，保留 4 位小数；分母为 0 记 0.0。"""
    return round(numerator / denominator, 4) if denominator else 0.0


# ------------------------------------------------------------------ #
# 指标结果结构
# ------------------------------------------------------------------ #


@dataclass
class MetricResult:
    """单指标结果：分子/分母/分数 + 细分子分 + pass/fail 明细。"""

    name: str
    numerator: int = 0
    denominator: int = 0
    sub: Dict[str, str] = field(default_factory=dict)   # 细分子分 "命中/总数"
    details: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def score(self) -> float:
        return score(self.numerator, self.denominator)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "numerator": self.numerator,
                "denominator": self.denominator, "score": self.score,
                "sub": dict(self.sub), "details": list(self.details)}


# ------------------------------------------------------------------ #
# 场景数据（确定性：固定配置 + 固定探询文本 + 固定核心词表）
# ------------------------------------------------------------------ #

# 指标①场景链：送礼建立记忆 → 探询。core_terms = 该记忆的实体核心词表。
# 探询文本均不含 PRAISE/CRITICISM 关键词，避免荀子矢量额外扰动 8D。
MEMORY_SCENARIOS: List[Dict[str, Any]] = [
    {"npc": "chen", "item": "铁矿石",
     "ask": "上次送你的矿石，成色怎么样？",
     "core": ["铁矿石", "矿石", "铁矿"],
     "label": "chen-铁矿石"},
    {"npc": "chen", "item": "苹果",
     "ask": "苹果甜不甜？",
     "core": ["苹果"],
     "label": "chen-苹果"},
    {"npc": "chen", "item": "武器图纸",
     "ask": "那张图纸看懂了吗？",
     "core": ["图纸"],
     "label": "chen-武器图纸"},
    {"npc": "lily", "item": "香料",
     "ask": "上次给你的香料，卖得怎么样？",
     "core": ["香料"],
     "label": "lily-香料"},
    {"npc": "lily", "item": "丝绸",
     "ask": "那匹丝绸卖掉了吗？",
     "core": ["丝绸"],
     "label": "lily-丝绸"},
    {"npc": "lily", "item": "苹果",
     "ask": "苹果吃了没？",
     "core": ["苹果"],
     "label": "lily-苹果"},
]

# 指标①对照（不计分）：不送礼直接探询同一句话。若 reply 层仍命中，
# 证明 Mock 的表面引用是话术库关键词匹配，与记忆无关。
MEMORY_CONTROL: Dict[str, Any] = {
    "npc": "chen",
    "ask": "上次送你的矿石，成色怎么样？",
    "core": ["铁矿石", "矿石", "铁矿"],
    "label": "对照-chen不送礼问矿石",
}

# 指标②状态分叉对：同 NPC 同输入，仅预置 8D 状态不同。
# expect_reason：高压侧应触发的硬约束 reason（None = 非硬约束对）。
STATE_PAIRS: List[Dict[str, Any]] = [
    {"npc": "chen", "input": "最近生意怎么样？",
     "side_a": {"S_stress": 0.2}, "side_b": {"S_stress": 0.85},
     "expect_reason": "stress_too_high", "label": "chen-S_stress硬约束"},
    {"npc": "lily", "input": "最近生意怎么样？",
     "side_a": {"S_stress": 0.2}, "side_b": {"S_stress": 0.85},
     "expect_reason": "stress_too_high", "label": "lily-S_stress硬约束"},
    {"npc": "chen", "input": "最近生意怎么样？",
     "side_a": {"p_fatigue": 0.2}, "side_b": {"p_fatigue": 0.9},
     "expect_reason": "fatigue_too_high", "label": "chen-p_fatigue硬约束"},
    {"npc": "lily", "input": "最近生意怎么样？",
     "side_a": {"p_fatigue": 0.2}, "side_b": {"p_fatigue": 0.9},
     "expect_reason": "fatigue_too_high", "label": "lily-p_fatigue硬约束"},
    # 非硬约束可见带：两侧均不触发 REFUSE（事件后 S_stress 0.25 / 0.55）
    {"npc": "chen", "input": "最近生意怎么样？",
     "side_a": {"S_stress": 0.2}, "side_b": {"S_stress": 0.5},
     "expect_reason": None, "label": "chen-S_stress内心可见带"},
    # e_P 愉悦带：cheerful 垫话掩码 e_P>0.7 / e_P<0.4 两带分叉
    {"npc": "lily", "input": "最近生意怎么样？",
     "side_a": {"e_P": 0.9}, "side_b": {"e_P": 0.1},
     "expect_reason": None, "label": "lily-e_P垫话可见带"},
]

# 指标②对照（不计分）：同状态同输入跑两次，输出应完全一致（确定性）。
DETERMINISM_CONTROL: Dict[str, Any] = {
    "npc": "chen", "input": "最近生意怎么样？", "preset": {"S_stress": 0.2},
    "label": "对照-同状态同输入两次",
}


# ------------------------------------------------------------------ #
# 指标①：记忆引用率
# ------------------------------------------------------------------ #


def _build_engine(probe: Any,
                  personas: Optional[List[Persona]] = None) -> NPCEngine:
    """fresh 引擎：独立世界 + 独立记忆 + 独立关系网，场景间零污染。

    probe 为鸭子类型探针（ProbeLLM / RealProbeLLM）：引擎只用
    getattr 读取可选属性、调用 chat()，不检查 isinstance。
    """
    if personas is None:
        personas = Persona.load_all()
    return NPCEngine(llm=probe, npc_configs=personas)


def _run_memory_scenario(personas: List[Persona], sc: Dict[str, Any],
                         gift: bool = True,
                         probe_factory=ProbeLLM) -> Dict[str, Any]:
    """跑单条记忆场景链，返回含 J1/J2 判定的明细 dict。

    gift=False 为"无记忆对照"：不送礼直接探询（valid 恒 False，不计分）。
    probe_factory：callable，每链创建独立探针（默认 Mock ProbeLLM）。
    """
    probe = probe_factory()
    engine = _build_engine(probe, personas)
    npc_id = sc["npc"]

    valid = True
    if gift:
        engine.player_gives(npc_id, sc["item"])
        npc = engine.npcs[npc_id]
        # 前置校验：送礼必须沉淀出含该物品的长期记忆，否则链无效
        valid = any(sc["item"] in r.content for r in npc.memory.long.records)

    result = engine.player_says(sc["ask"], npc_id)
    prompt = probe.last_user_prompt() or ""
    memory_block = extract_block(prompt, "【相关长期记忆】")
    reply_text = str(result.get("reply") or "")

    inject_hit = valid and hits_terms(memory_block, sc["core"])
    reply_hit = valid and hits_terms(reply_text, sc["core"])
    return {
        "label": sc["label"],
        "valid": valid,
        "memory_block": memory_block,
        "reply": reply_text,
        "inject_hit": inject_hit,
        "reply_hit": reply_hit,
        "hit": inject_hit or reply_hit,
    }


def run_memory_scenarios(
        scenarios: Optional[List[Dict[str, Any]]] = None,
        personas: Optional[List[Persona]] = None,
        probe_factory=ProbeLLM) -> MetricResult:
    """计算指标①记忆引用率（口径见模块 docstring）。"""
    if scenarios is None:
        scenarios = MEMORY_SCENARIOS
    if personas is None:
        personas = Persona.load_all()

    result = MetricResult(name="记忆引用率")
    inject_hits = 0
    for sc in scenarios:
        detail = _run_memory_scenario(personas, sc, gift=True,
                                      probe_factory=probe_factory)
        result.denominator += 1
        result.numerator += 1 if detail["hit"] else 0
        inject_hits += 1 if detail["inject_hit"] else 0
        detail["pass"] = detail["hit"]
        result.details.append(detail)
    result.sub = {
        "J1_注入层": f"{inject_hits}/{result.denominator}",
        "J2_回复层": (f"{sum(1 for d in result.details if d['reply_hit'])}"
                      f"/{result.denominator}"),
    }
    return result


def run_memory_control(
        personas: Optional[List[Persona]] = None,
        probe_factory=ProbeLLM) -> Dict[str, Any]:
    """指标①对照：不送礼直接探询，检验 reply 层命中是否与记忆无关。"""
    if personas is None:
        personas = Persona.load_all()
    probe = probe_factory()
    engine = _build_engine(probe, personas)
    result = engine.player_says(MEMORY_CONTROL["ask"], MEMORY_CONTROL["npc"])
    prompt = probe.last_user_prompt() or ""
    block = extract_block(prompt, "【相关长期记忆】")
    return {
        "label": MEMORY_CONTROL["label"],
        "memory_block": block,
        "reply": str(result.get("reply") or ""),
        "inject_hit": hits_terms(block, MEMORY_CONTROL["core"]),
        "reply_hit": hits_terms(str(result.get("reply") or ""),
                                MEMORY_CONTROL["core"]),
    }


# ------------------------------------------------------------------ #
# 指标②：状态影响可见性
# ------------------------------------------------------------------ #


def _run_state_side(personas: List[Persona], npc_id: str, text: str,
                    preset: Dict[str, float],
                    probe_factory=ProbeLLM) -> SideRecord:
    """预置 8D 状态后跑一次 player_says，返回玩家可感知输出切片。

    预置键必须是 InnerState.PARAMS 内的 8D 字段——出现旧 5D 字段
    直接抛 ValueError，把"指标消费 8D 语义"做成硬纪律。
    probe_factory：callable，每侧创建独立探针（默认 Mock ProbeLLM）。
    """
    bad = set(preset) - set(InnerState.PARAMS)
    if bad:
        raise ValueError(f"非 8D 字段: {sorted(bad)}；"
                         f"合法字段: {list(InnerState.PARAMS)}")
    probe = probe_factory()
    engine = _build_engine(probe, personas)
    npc = engine.npcs[npc_id]
    for key, value in preset.items():
        setattr(npc.inner_state, key, value)
    result = engine.player_says(text, npc_id)
    prompt = probe.last_user_prompt()
    return SideRecord(action=dict(result.get("action") or {}),
                      filler=str(result.get("filler") or ""),
                      inner=extract_inner_line(prompt))


def run_state_scenarios(
        pairs: Optional[List[Dict[str, Any]]] = None,
        personas: Optional[List[Persona]] = None,
        probe_factory=ProbeLLM) -> MetricResult:
    """计算指标②状态影响可见性（口径见模块 docstring）。"""
    if pairs is None:
        pairs = STATE_PAIRS
    if personas is None:
        personas = Persona.load_all()

    result = MetricResult(name="状态影响可见性")
    c1_hits = c2_hits = 0
    c3_hits = c3_avail = 0
    for pair in pairs:
        a = _run_state_side(personas, pair["npc"], pair["input"],
                            pair["side_a"], probe_factory=probe_factory)
        b = _run_state_side(personas, pair["npc"], pair["input"],
                            pair["side_b"], probe_factory=probe_factory)
        visible, channels = distinguish(a, b)
        result.denominator += 1
        result.numerator += 1 if visible else 0
        c1_hits += 1 if channels["C1_action"] else 0
        c2_hits += 1 if channels["C2_filler"] else 0
        if channels["C3_inner"] is not None:
            c3_avail += 1
            c3_hits += 1 if channels["C3_inner"] else 0
        # 硬约束对的高压侧 reason（玩家可读的拒绝理由）附进明细
        reason_b = b.action.get("reason")
        result.details.append({
            "label": pair["label"],
            "action_a": a.action, "action_b": b.action,
            "filler_a": a.filler, "filler_b": b.filler,
            "inner_a": a.inner, "inner_b": b.inner,
            "channels": channels,
            "reason_b": reason_b,
            "expect_reason": pair["expect_reason"],
            "reason_match": (pair["expect_reason"] is None
                             or reason_b == pair["expect_reason"]),
            "pass": visible,
        })
    result.sub = {
        "C1_动作分叉": f"{c1_hits}/{result.denominator}",
        "C2_垫话分叉": f"{c2_hits}/{result.denominator}",
        "C3_内心注入分叉": f"{c3_hits}/{c3_avail}（其余 {result.denominator - c3_avail} 对 N/A）",
    }
    return result


def run_state_determinism_control(
        personas: Optional[List[Persona]] = None,
        probe_factory=ProbeLLM) -> Dict[str, Any]:
    """指标②对照：同状态同输入跑两次，输出应完全一致。"""
    if personas is None:
        personas = Persona.load_all()
    ctrl = DETERMINISM_CONTROL
    a = _run_state_side(personas, ctrl["npc"], ctrl["input"],
                        ctrl["preset"], probe_factory=probe_factory)
    b = _run_state_side(personas, ctrl["npc"], ctrl["input"],
                        ctrl["preset"], probe_factory=probe_factory)
    visible, channels = distinguish(a, b)
    return {"label": ctrl["label"], "channels": channels,
            "distinguishable": visible}


# ------------------------------------------------------------------ #
# 汇总
# ------------------------------------------------------------------ #


def run_baseline(provider: str = "mock") -> Dict[str, Any]:
    """跑全部场景，返回结构化基线结果（纯数据 dict，可整体相等比较）。

    provider="mock"：默认 ProbeLLM 探针（离线确定性基线）。
    provider="real"：加载 .env → create_provider("openai") → 每场景用
    RealProbeLLM 包装共享的真实 provider 跑五口径；端点不可用时
    provider_note 标注"API 不可用"，场景照跑——决策层把 LLMError 回退为
    fallback 文本（engine/decision.py），指标不因端点故障中断。
    """
    personas = Persona.load_all()
    if provider == "real":
        from scripts.llm_smoke import load_env
        load_env(os.path.join(ROOT, ".env"))
        from engine.llm import create_provider
        real = create_provider("openai")
        probe_factory = lambda: RealProbeLLM(real)  # noqa: E731
        provider_note = (f"真实 LLM: {real.model}"
                         if getattr(real, "available", False)
                         else "API 不可用：NPC_LLM_BASE_URL 未配置")
    else:
        probe_factory = ProbeLLM
        provider_note = ""

    memory = run_memory_scenarios(personas=personas, probe_factory=probe_factory)
    state = run_state_scenarios(personas=personas, probe_factory=probe_factory)
    control_memory = run_memory_control(personas, probe_factory=probe_factory)
    control_determinism = run_state_determinism_control(
        personas, probe_factory=probe_factory)

    baseline = {
        "provider": provider,
        "provider_note": provider_note,
        "memory_reference_rate": memory.to_dict(),
        "state_visibility_rate": state.to_dict(),
        "controls": {
            "no_memory": control_memory,
            "state_determinism": control_determinism,
        },
        "total_scenarios": memory.denominator + state.denominator,
        "baseline": {
            "memory_reference_rate": memory.score,
            "state_visibility_rate": state.score,
        },
    }
    return baseline


def _parse_sub(sub: str) -> Tuple[int, int]:
    """把 "命中/总数" 前缀解析为 (命中, 总数)；无法解析返回 (0, 0)。

    C3 子分形如 "2/2（其余 4 对 N/A）"，前缀解析同样适用。
    """
    m = re.match(r"(\d+)\s*/\s*(\d+)", sub or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def run_comparison() -> Dict[str, Any]:
    """双基线对照：Mock 基线 + Real 基线，返回 {"mock": ..., "real": ...}。"""
    mock_baseline = run_baseline(provider="mock")
    real_baseline = run_baseline(provider="real")
    return {"mock": mock_baseline, "real": real_baseline}


# ------------------------------------------------------------------ #
# 报告打印
# ------------------------------------------------------------------ #


def _print_report(baseline: Dict[str, Any]) -> None:
    mem = baseline["memory_reference_rate"]
    st = baseline["state_visibility_rate"]
    ctrl_m = baseline["controls"]["no_memory"]
    ctrl_d = baseline["controls"]["state_determinism"]
    line = "=" * 68
    print(line)
    print(f"对话质量基线报告（provider={baseline['provider']}"
          f"{'，' + baseline['provider_note'] if baseline['provider_note'] else ''}）")
    print(line)

    pct = lambda n, d: f"{n / d * 100:.1f}%" if d else "N/A"  # noqa: E731
    # ---- 指标① ----
    print(f"\n指标① {mem['name']}：{mem['numerator']}/{mem['denominator']}"
          f" = {pct(mem['numerator'], mem['denominator'])}")
    print(f"  注入层 J1（记忆进入【相关长期记忆】块）：{mem['sub']['J1_注入层']}")
    print(f"  回复层 J2（回复文本出现记忆实体词）：{mem['sub']['J2_回复层']}")
    print("  明细：")
    for d in mem["details"]:
        flag = "PASS" if d["pass"] else "FAIL"
        print(f"    [{flag}] {d['label']:<10} 注入={'命中' if d['inject_hit'] else '未中'}"
              f" 回复={'命中' if d['reply_hit'] else '未中'}"
              f"  记忆块[{d['memory_block']}]"
              f" 回复[{d['reply']}]")
    print(f"  对照（不计分）{ctrl_m['label']}：注入={'命中' if ctrl_m['inject_hit'] else '未中'}"
          f" 回复={'命中' if ctrl_m['reply_hit'] else '未中'}")
    if ctrl_m["reply_hit"]:
        print("    → 不送礼 reply 层同样命中：Mock 表面引用是话术库关键词匹配，"
              "与记忆无关（J2 即复读话术库度量）")
    print("  口径：命中 = 注入层或回复层任一出现场景核心词；"
          "分母 = 送礼建立记忆后探询的场景链数")

    # ---- 指标② ----
    print(f"\n指标② {st['name']}：{st['numerator']}/{st['denominator']}"
          f" = {pct(st['numerator'], st['denominator'])}")
    print(f"  通道命中：动作分叉 {st['sub']['C1_动作分叉']}｜"
          f"垫话分叉 {st['sub']['C2_垫话分叉']}｜"
          f"内心注入分叉 {st['sub']['C3_内心注入分叉']}")
    print("  明细：")
    for d in st["details"]:
        flag = "PASS" if d["pass"] else "FAIL"
        ch = d["channels"]
        c3_txt = ("N/A" if ch["C3_inner"] is None
                  else ("分叉" if ch["C3_inner"] else "相同"))
        reason_txt = f" 硬约束reason={d['reason_b']}" if d["reason_b"] else ""
        print(f"    [{flag}] {d['label']:<14} C1动作={'分叉' if ch['C1_action'] else '相同'}"
              f" C2垫话={'分叉' if ch['C2_filler'] else '相同'}"
              f" C3内心={c3_txt}{reason_txt}")
        if d["expect_reason"] and not d["reason_match"]:
            print(f"           ✗ 硬约束 reason 不符：期望 {d['expect_reason']}，"
                  f"实际 {d['reason_b']}")
    print(f"  对照（不计分）{ctrl_d['label']}："
          f"可区分={'是' if ctrl_d['distinguishable'] else '否'}（确定性要求=否）")
    print("  口径：可区分 = 动作/垫话/内心注入任一通道分叉；"
          "分母 = 同输入仅 8D 预置状态不同的场景对数")

    # ---- 汇总 ----
    tag = "Mock" if baseline["provider"] == "mock" else "Real"
    print(f"\n总场景数：{baseline['total_scenarios']}"
          f"（另对照 {len(baseline['controls'])} 项）")
    print(line)
    print(f"{tag} 基线分：记忆引用率 {pct(mem['numerator'], mem['denominator'])}"
          f"（{mem['numerator']}/{mem['denominator']}）｜"
          f"状态影响可见性 {pct(st['numerator'], st['denominator'])}"
          f"（{st['numerator']}/{st['denominator']}）")
    print(line)


def _print_comparison(mock_b: Dict[str, Any], real_b: Dict[str, Any]) -> None:
    """Mock vs Real 五口径对照报告 + 差异归因（归因为口径结构预期）。"""
    line = "=" * 68
    pct = lambda n, d: f"{n / d * 100:.1f}%" if d else "N/A"  # noqa: E731
    rate = lambda n, d: (n / d) if d else 0.0                 # noqa: E731

    mm, rm = mock_b["memory_reference_rate"], real_b["memory_reference_rate"]
    ms, rs = mock_b["state_visibility_rate"], real_b["state_visibility_rate"]

    print(line)
    print("Mock vs Real 对照报告（五口径 J1/J2/C1/C2/C3）")
    print(f"Real 端点：{real_b['provider_note'] or '（无标注）'}")
    print(line)

    # ---- 指标① 记忆引用率 ----
    print(f"\n指标① 记忆引用率：Mock {mm['numerator']}/{mm['denominator']}"
          f"（{pct(mm['numerator'], mm['denominator'])}）"
          f" vs Real {rm['numerator']}/{rm['denominator']}"
          f"（{pct(rm['numerator'], rm['denominator'])}）"
          f"｜分数差值 {rm['score'] - mm['score']:+.4f}")
    for key, label in [("J1_注入层", "J1 注入层"), ("J2_回复层", "J2 回复层")]:
        mn, md = _parse_sub(mm["sub"][key])
        rn, rd = _parse_sub(rm["sub"][key])
        print(f"  {label}：Mock {mn}/{md} vs Real {rn}/{rd}"
              f"｜差值 {rate(rn, rd) - rate(mn, md):+.4f}")

    # ---- 指标② 状态影响可见性 ----
    print(f"\n指标② 状态影响可见性：Mock {ms['numerator']}/{ms['denominator']}"
          f"（{pct(ms['numerator'], ms['denominator'])}）"
          f" vs Real {rs['numerator']}/{rs['denominator']}"
          f"（{pct(rs['numerator'], rs['denominator'])}）"
          f"｜分数差值 {rs['score'] - ms['score']:+.4f}")
    for key, label in [("C1_动作分叉", "C1 动作分叉"),
                       ("C2_垫话分叉", "C2 垫话分叉"),
                       ("C3_内心注入分叉", "C3 内心注入分叉")]:
        mn, md = _parse_sub(ms["sub"][key])
        rn, rd = _parse_sub(rs["sub"][key])
        print(f"  {label}：Mock {mn}/{md} vs Real {rn}/{rd}"
              f"｜差值 {rate(rn, rd) - rate(mn, md):+.4f}")

    # ---- 差异归因（由口径结构决定，硬编码）----
    print("\n差异归因（由口径结构决定，非实测推断）：")
    print("  J1 注入层：两侧预期一致——prompt 结构由记忆检索管道决定，"
          "不依赖 LLM provider")
    print("  J2 回复层：真实 LLM 基于注入的【相关长期记忆】生成回复 vs "
          "Mock 话术库关键词匹配（Mock 1/6 是词面巧合，无记忆对照已证与记忆无关）")
    print("  C1 动作分叉：硬约束对（4 对）两侧一致——规则引擎判定，不调 LLM；"
          "非硬约束对真实侧可能因回复文本不同而出现动作分叉")
    print("  C2 垫话分叉：两侧一致——FillerEngine 纯规则 0-token，不依赖 LLM")
    print("  C3 内心注入分叉：两侧一致——【此刻内心】文本由 8D 状态决定，"
          "不依赖 LLM 回复")

    # ---- 结论：J2 是否提升（Mock 自嗨是否被刺破）----
    mj2n, mj2d = _parse_sub(mm["sub"]["J2_回复层"])
    rj2n, rj2d = _parse_sub(rm["sub"]["J2_回复层"])
    improved = rate(rj2n, rj2d) > rate(mj2n, mj2d)
    verdict = ("提升——Mock 自嗨被刺破：真实 LLM 的表面引用确实来自注入记忆"
               if improved else
               "未提升——Mock 自嗨未被刺破：真实回复未引用记忆实体词")
    print(f"\n结论：J2 回复层 {verdict}")
    print(line)


# ------------------------------------------------------------------ #
# 入口
# ------------------------------------------------------------------ #


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="对话质量首批 2 指标：记忆引用率 + 状态影响可见性")
    parser.add_argument(
        "--provider", choices=["mock", "real"], default="mock",
        help="LLM provider：mock=离线确定性基线；"
             "real=真实 LLM 对照（调用真实端点）")
    args = parser.parse_args(argv)

    if args.provider == "real":
        # 先 Mock 基线，再 Real 基线，打印对照 + Real 单独报告
        comparison = run_comparison()
        mock_baseline = comparison["mock"]
        baseline = comparison["real"]
        _print_comparison(mock_baseline, baseline)
        _print_report(baseline)
        if "不可用" in baseline["provider_note"]:
            print("\n[提示] 真实端点不可用：Real 基线实际为决策层安全回退"
                  "（fallback）输出。请检查 .env 的 NPC_LLM_BASE_URL 配置。")
            return 0
    else:
        baseline = run_baseline(provider=args.provider)
        _print_report(baseline)

    # 校验失败信号：任一硬约束对 reason 不符，或确定性对照意外分叉
    st = baseline["state_visibility_rate"]
    hard_violation = any(d["expect_reason"] and not d["reason_match"]
                         for d in st["details"])
    determinism_broken = baseline["controls"]["state_determinism"]["distinguishable"]
    return 1 if (hard_violation or determinism_broken) else 0


if __name__ == "__main__":
    raise SystemExit(main())

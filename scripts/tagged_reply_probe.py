"""真实 LLM 挂载-未挂载对照实验脚本（审查指令 1：回复可区分证据落日志）。

用途：在真实 LLM 下对同一 NPC（铁匠陈）以两种标签账本状态重问同样的
两个问题，产出"标签挂载是否传导为玩家可感知的回复差异"的证据：

- 场景 1 · 挂载态（常驻默认）：tag_profile="genesis" 的 chen 在
  NPCEngine 初始化后默认带账本（npc.tag_ledger 非 None，子代理 A
  接口契约）；crc32 确定性下可见标签「断指」、绝密「私生血统」
  （绝密不进决策上下文，本脚本仅计数展示）。
- 场景 2 · 对照态：将 chen.tag_ledger 置 None（决策上下文零身份
  标签注入，与 T2 接线前行为一致），重问同样两问。

输出（stdout 对照报告，供审查"附命令输出"存档）：
1. 挂载前提校验（chen.tag_ledger 状态、可见标签、绝密计数）；
2. 两种状态 system prompt 的身份标签行差异（挂载态含
   「身份标签：断指」行、对照态无该行）；
3. 四条真实回复全文 + 每条回复延迟秒数；
4. 辅助判读（仅输出事实标记，不下结论）：回复文本是否含
   「指/伤/手/铁匠」字样；
5. 失败记录：API 不可用/调用异常时输出原因（状态码/异常类），
   不崩溃、不阻塞（决策层会把 LLMError 回退为 fallback 文本，
   本脚本据探针 last_error 把"安全回退"与"真实回复"区分开）。

用法：
    python3 scripts/tagged_reply_probe.py

返回码：0 = 四次调用全部获得真实 LLM 回复；1 = 存在失败记录；
2 = 挂载前提不满足（tag_profile 常驻接线未落地）。
零第三方依赖：真实端点经 engine 的 urllib Provider 直连。
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 实验问题（固定文本：两状态重问同样两问，唯一变量是标签账本）
QUESTIONS: Tuple[Tuple[str, str], ...] = (
    ("Q1 定向试探", "你的手最近还好吗？干你们这行的，手上难免有伤吧。"),
    ("Q2 开放自述", "说说你自己吧，你是个什么样的人？"),
)

# 辅助判读字样（仅输出事实标记，不下结论——审查要求）
MARKERS: Tuple[str, ...] = ("指", "伤", "手", "铁匠")

LINE = "=" * 68


# ------------------------------------------------------------------ #
# 构建与替换（engine import 全部延迟到函数内，模块级零副作用）
# ------------------------------------------------------------------ #
def _build_engine():
    """构造引擎：Persona.load_all() + Mock provider 初始化。"""
    from engine.engine import NPCEngine
    from engine.llm.mock import MockLLMProvider
    return NPCEngine(llm=MockLLMProvider())


def _build_real_provider():
    """加载 .env 并创建真实 LLM provider（openai 兼容，urllib 直连）。"""
    from scripts.llm_smoke import load_env
    from engine.llm import create_provider
    load_env(os.path.join(ROOT, ".env"))
    return create_provider("openai")


def _make_tracking_probe(real):
    """复用 scripts.dialogue_quality.RealProbeLLM 探针 + 失败追踪。

    chat 异常记录到 last_error 后原样上抛：决策层会把 LLMError 回退为
    fallback 文本（安全回退），探针记录让本脚本能把"安全回退"与
    "真实回复"区分开，失败原因（状态码/异常类）可落日志。
    """
    from scripts.dialogue_quality import RealProbeLLM

    class TrackingProbeLLM(RealProbeLLM):
        """真实探针子类：只追加 last_error 追踪，不改变请求行为。"""

        def __init__(self, inner) -> None:
            super().__init__(inner)
            self.last_error: Optional[BaseException] = None

        def chat(self, messages, temperature: float = 0.7) -> str:
            self.last_error = None
            try:
                return super().chat(messages, temperature)
            except Exception as exc:
                self.last_error = exc
                raise

    return TrackingProbeLLM(real)


def _swap_provider(engine, probe) -> None:
    """实验前把引擎 LLM 全量替换为探针（facade + 各 NPC 决策引擎）。"""
    engine.llm = probe
    for npc in engine.npcs.values():
        npc.decision.llm = probe


# ------------------------------------------------------------------ #
# 单问执行与展示
# ------------------------------------------------------------------ #
def _ask(engine, probe, npc_id: str, text: str) -> Dict[str, Any]:
    """问一个问题，记录回复全文、延迟秒数、身份标签行、失败原因。

    任何异常（API 不可用/网络层/引擎层）都记录不阻塞，返回结构恒定。
    """
    probe.last_error = None
    start = time.perf_counter()
    try:
        result = engine.player_says(text, npc_id)
    except Exception as exc:  # 引擎层意外异常：记录失败原因，不崩溃
        return {"reply": "", "latency": time.perf_counter() - start,
                "identity_line": "",
                "llm_error": f"{type(exc).__name__}: {exc}"}
    latency = time.perf_counter() - start

    llm_error = ""
    if probe.last_error is not None:
        llm_error = f"{type(probe.last_error).__name__}: {probe.last_error}"
    system = next((m["content"] for m in reversed(probe.last_messages or [])
                   if m["role"] == "system"), "")
    identity_line = ""
    for line in system.splitlines():
        if line.startswith("身份标签："):
            identity_line = line
            break
    return {"reply": str(result.get("reply") or ""), "latency": latency,
            "identity_line": identity_line, "llm_error": llm_error}


def _marker_text(reply: str) -> str:
    """事实标记：列出回复命中的字样（仅事实，不下结论）。"""
    hits = [m for m in MARKERS if m in reply]
    if not hits:
        return "无命中"
    return "含" + "".join(f"「{m}」" for m in hits)


def _print_answer(state: str, label: str, text: str, rec: Dict[str, Any]) -> None:
    print(f"\n[{state} · {label}] 玩家：{text}")
    print(f"  延迟：{rec['latency']:.2f}s")
    if rec["llm_error"]:
        print(f"  ✗ LLM 失败：{rec['llm_error']}")
        print("    （下方回复为决策层 fallback 安全回退，非真实 LLM 输出）")
    print(f"  身份标签行：{rec['identity_line'] or '（system prompt 无「身份标签：」行）'}")
    print(f"  回复全文：{rec['reply'] or '（空）'}")
    print(f"  字样标记（事实，不下结论）：{_marker_text(rec['reply'])}")


# ------------------------------------------------------------------ #
# 主流程
# ------------------------------------------------------------------ #
def main() -> int:
    print(LINE)
    print("真实 LLM 挂载-未挂载对照实验（tagged_reply_probe）")
    print(LINE)

    # 1) 构造引擎（Mock provider 初始化）+ 挂载前提校验（子代理 A 契约）
    try:
        engine = _build_engine()
    except Exception as exc:
        print(f"✗ 引擎构造失败：{type(exc).__name__}: {exc}")
        return 1
    chen = engine.npcs.get("chen")
    if chen is None or chen.tag_ledger is None:
        print("✗ 挂载前提不满足：chen.tag_ledger 为 None。")
        print("  契约：configs tag_profile=\"genesis\" 的核心 NPC 在 NPCEngine")
        print("  初始化后默认带账本（子代理 A 并行实现中）——确认常驻挂载")
        print("  接线落地后重跑本脚本。")
        return 2
    visible = [t.label for t in chen.tag_ledger.visible_tags()]
    secret_count = len(chen.tag_ledger.secrets())
    print("挂载前提校验：chen.tag_ledger 非 None（tag_profile 常驻默认）")
    print(f"  可见标签：{'、'.join(visible) if visible else '（无）'}")
    print(f"  绝密把柄：{secret_count} 条（不进决策上下文，仅计数展示）")

    # 2) 实验前替换为真实 provider（探针包装）
    real = _build_real_provider()
    print(f"\n真实端点：model={getattr(real, 'model', '?')}"
          f" base_url={getattr(real, 'base_url', '?')}")
    if not getattr(real, "available", False):
        print("✗ API 不可用：NPC_LLM_BASE_URL 未配置——实验继续跑，每条回复")
        print("  将标记失败原因（决策层 LLMError→fallback，记录不阻塞）。")
    probe = _make_tracking_probe(real)
    _swap_provider(engine, probe)

    # 3) 场景 1：挂载态（常驻默认账本）
    print(f"\n{'-' * 68}")
    print("场景 1 · 挂载态（常驻默认账本，决策上下文注入身份标签行）")
    print("-" * 68)
    mounted: List[Tuple[str, str, Dict[str, Any]]] = []
    for label, text in QUESTIONS:
        rec = _ask(engine, probe, "chen", text)
        mounted.append((label, text, rec))
        _print_answer("挂载态", label, text, rec)

    # 4) 场景 2：对照态（chen.tag_ledger 置 None）
    chen.tag_ledger = None
    print(f"\n{'-' * 68}")
    print("场景 2 · 对照态（chen.tag_ledger 置 None，零身份标签注入）")
    print("-" * 68)
    control: List[Tuple[str, str, Dict[str, Any]]] = []
    for label, text in QUESTIONS:
        rec = _ask(engine, probe, "chen", text)
        control.append((label, text, rec))
        _print_answer("对照态", label, text, rec)

    # 5) 对照报告
    print(f"\n{LINE}")
    print("对照报告（挂载-未挂载可区分证据）")
    print(LINE)

    print("\n[A] system prompt 身份标签行差异")
    m_identity = mounted[0][2]["identity_line"]
    c_identity = control[0][2]["identity_line"]
    print(f"  挂载态：{m_identity or '（未捕获——检查挂载前提）'}")
    print(f"  对照态：{c_identity or '（无「身份标签：」行，与 T2 接线前行为一致）'}")
    if m_identity and not c_identity:
        print("  差异：可区分——挂载态多出身份标签行，对照态无")
    else:
        print("  差异：需人工判读（见上方各问身份标签行）")

    print("\n[B] 延迟与回复字样对照（回复全文见上方场景段）")
    for label, _text, rec in mounted:
        print(f"  挂载态 {label}：{rec['latency']:.2f}s  字样={_marker_text(rec['reply'])}")
    for label, _text, rec in control:
        print(f"  对照态 {label}：{rec['latency']:.2f}s  字样={_marker_text(rec['reply'])}")

    failures = [(state, label, rec)
                for state, group in (("挂载态", mounted), ("对照态", control))
                for label, _text, rec in group if rec["llm_error"]]
    print("\n[C] 失败记录（输出原因不阻塞）")
    if not failures:
        print("  无——四次调用全部获得真实 LLM 回复。")
    else:
        for state, label, rec in failures:
            print(f"  [{state} · {label}] {rec['llm_error']}")
    print(LINE)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

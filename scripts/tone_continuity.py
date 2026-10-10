"""真实 LLM 语气连续性采样脚本（审查第九次指令 1 第二项·对抗升级条款）。

用途：高压垫话后慢脑回复的语气连续性验收采样。垫话（filler）是慢脑
LLM 调用前同步返回的反应性开场白（0-token 本地计算，不进 LLM
prompt）。铁匠陈（temperament="irritable" 暴躁掩码）处于高压状态
（InnerState.S_stress > BAND_STRESS_HIGH=0.6）时垫话为
「（皱眉）找老夫何事？」。本脚本预置高压后对 chen 连发 6 条慢脑
复杂问句，记录每条实际垫话与慢脑回复全文，按确定性关键词口径给出
语气连续性预标注初判，产出 PM 终审
「高压垫话后慢脑不接续样本 ≤20%（真实 LLM 口径）」验收证据。

高压预置口径（对任务书 0.85 的修正，重要——PM 请阅）：
    任务书原文预置 S_stress=0.85，但 engine/decision.py decide() 的
    内状态硬规则是 S_stress > 0.8 即返回 REFUSE 拒绝接单、**不调
    LLM**（decision.py:128）。0.85 叠加 player_spoke 的 +0.05 后
    为 0.90 > 0.8，全部样本会被硬规则拦截为「没有回应」——慢脑
    LLM 根本不产生回复，语气连续性证据失效（本脚本 --mock 自测
    曾实证 6/6 REFUSE）。故预置修正为 0.70：
    - 垫话/决策实测值 = 0.70 + 0.05（player_spoke 增量）= 0.75；
    - 0.75 > 0.6 → 高压带垫话「（皱眉）找老夫何事？」命中；
    - 0.75 ≤ 0.8 → 不触发拒单硬规则，慢脑 LLM 正常产生回复；
    - 0.75 > 0.6 → 慢脑 prompt【此刻内心】行注入「心烦意乱」离散
      标签（to_discrete_tags 同源阈值），语气接续有了传导通道。
    另：player_spoke 每条对话 +0.05 只升不降，单次预置下第 6 条
    会累积 +0.30、必然击穿 0.8 拒单线——数学上无法让 6 条样本全
    部落在 (0.6, 0.8] 窗口。故每条对话前重置 S_stress=0.70，保证
    每条样本初始条件一致（唯一变量 = 问句文本）。

采样集（6 条慢脑复杂问句；其中「铁匠，打把剑 / 有好铁矿石吗 /
有什么消息」为快脑 token 基线硬保护句，见 engine/fast_brain.py
头注与 tests/test_fast_brain.py 锁死断言。若运行中某条
brain=="fast" 则该条标"无效样本"不计入）：
    1. 铁匠，打把剑
    2. 有好铁矿石吗
    3. 有什么消息
    4. 聊聊你这些年的手艺
    5. 最近生意怎么样
    6. 你这铁匠铺都有什么规矩

预标注初判口径（确定性关键词，按序规则）——初判仅供参考，
最终判定以 PM 人工终审为准：
    - 接续特征词（高压烦躁语气保持）：忙、烦、没空、没工夫、不耐烦、
      皱眉、哼、催、快说、少啰嗦、别啰嗦、赶紧、出去、等着
    - 不接续特征词（热情/殷勤与高压矛盾）：客官、欢迎、里边请、
      请坐、您里边、热情、笑、和气、慢挑、上茶
    - 规则：命中不接续词且无接续词 → 初判「不接续」；命中接续词 →
      初判「接续」（双命中时烦躁词压过热情词）；两者皆无 → 初判「中性」。

有效样本口径（防假证据，三重剔除）：
    - LLM 失败：探针 last_error 非空（决策层已回退 fallback 文本），
      标记「LLM 失败（fallback 回复，不计入有效样本）」，输出原因
      不阻塞不崩溃；
    - 快脑命中：brain=="fast"（0 LLM 调用秒回，非慢脑回复）；
    - 无 LLM 调用 / 非 SPEAK：探针调用计数未增加（决策层硬规则
      拦截，如 SLEEPING 梦呓、S_stress>0.8 拒单）或 LLM 选择非
      SPEAK 动作（无语气文本可判）——一律剔除并打印原因。若不剔
      除，模板化 fallback 文本会被误判为「中性」样本，污染 ≤20%
      验收比例。

用法：
    python3 scripts/tone_continuity.py          # 真实 LLM 采样（PM 验收亲跑）
    python3 scripts/tone_continuity.py --mock   # 自测口径：保留 Mock provider
                                                 # 走通主流程，不消耗真实 API

返回码：0 = 采样完成且有有效样本；1 = 全部失败或有效样本为 0。
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

# 采样问题（固定 6 条慢脑复杂问句，全部问 chen）
QUESTIONS: Tuple[str, ...] = (
    "铁匠，打把剑",
    "有好铁矿石吗",
    "有什么消息",
    "聊聊你这些年的手艺",
    "最近生意怎么样",
    "你这铁匠铺都有什么规矩",
)

# irritable 高压带垫话（engine/filler.py TEMPERAMENT_MASKS["irritable"]
# 首规则：S_stress > BAND_STRESS_HIGH → 该模板），前提校验基准
EXPECTED_FILLER = "（皱眉）找老夫何事？"

# 预置压力（对任务书 0.85 的修正，理由见模块 docstring「高压预置口径」）：
# 0.70 + 0.05（player_spoke）= 0.75 ∈（0.6 高压带, 0.8 拒单线]，
# 垫话与慢脑 LLM 回复双前提同时成立。每条对话前重置（见主循环）。
HIGH_STRESS_PRESET = 0.70

# 预标注初判词表（确定性关键词口径）。
# 再次声明：初判仅供参考，最终判定以 PM 人工终审为准。
CONTINUE_WORDS: Tuple[str, ...] = (   # 接续特征词：高压烦躁语气保持
    "忙", "烦", "没空", "没工夫", "不耐烦", "皱眉", "哼", "催",
    "快说", "少啰嗦", "别啰嗦", "赶紧", "出去", "等着",
)
BREAK_WORDS: Tuple[str, ...] = (      # 不接续特征词：热情/殷勤与高压矛盾
    "客官", "欢迎", "里边请", "请坐", "您里边", "热情", "笑",
    "和气", "慢挑", "上茶",
)

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
    """复用 scripts.dialogue_quality.RealProbeLLM 探针 + 失败/计数追踪。

    chat 异常记录到 last_error 后原样上抛：决策层会把 LLMError 回退为
    fallback 文本（安全回退），探针记录让本脚本能把"安全回退"与
    "真实回复"区分开，失败原因（状态码/异常类）可落日志。calls 计数
    让本脚本能识别"本轮根本没有 LLM 调用"（决策层硬规则拦截）。
    """
    from scripts.dialogue_quality import RealProbeLLM

    class TrackingProbeLLM(RealProbeLLM):
        """真实探针子类：追加 last_error / calls 追踪，不改变请求行为。"""

        def __init__(self, inner) -> None:
            super().__init__(inner)
            self.last_error: Optional[BaseException] = None
            self.calls: int = 0

        def chat(self, messages, temperature: float = 0.7) -> str:
            self.last_error = None
            self.calls += 1
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


def _llm_call_count(engine) -> int:
    """读当前决策层 LLM 的调用计数（真实探针 calls / Mock call_count）。

    NPCEngine 构造时 facade 与各 NPC 决策引擎共用同一 provider 实例
    （已实测 engine.llm is npc.decision.llm），替换后亦同——读
    engine.llm 即读决策层。计数不可得返回 -1（视为"无法判定"）。
    """
    llm = engine.llm
    for attr in ("calls", "call_count"):
        val = getattr(llm, attr, None)
        if isinstance(val, int):
            return val
    return -1


# ------------------------------------------------------------------ #
# 单问执行、预标注初判与展示
# ------------------------------------------------------------------ #
def _ask(engine, probe, npc_id: str, text: str) -> Dict[str, Any]:
    """问一个问题，记录实际垫话/回复全文/brain/延迟/LLM 调用/失败原因。

    任何异常（API 不可用/网络层/引擎层）都记录不阻塞，返回结构恒定。
    """
    if probe is not None:
        probe.last_error = None
    calls_before = _llm_call_count(engine)
    start = time.perf_counter()
    rec: Dict[str, Any] = {"reply": "", "filler": "", "brain": "",
                           "action": "", "latency": 0.0, "llm_error": "",
                           "llm_called": False, "stress_post": None}
    try:
        result = engine.player_says(text, npc_id)
    except Exception as exc:  # 引擎层意外异常：记录失败原因，不崩溃
        rec["latency"] = time.perf_counter() - start
        rec["llm_error"] = f"{type(exc).__name__}: {exc}"
        npc = engine.npcs.get(npc_id)
        if npc is not None:
            rec["stress_post"] = npc.inner_state.S_stress
        return rec
    rec["latency"] = time.perf_counter() - start
    npc = engine.npcs.get(npc_id)
    if npc is not None:
        rec["stress_post"] = npc.inner_state.S_stress
    calls_after = _llm_call_count(engine)
    if calls_after > calls_before:
        rec["llm_called"] = True
    if not result.get("ok"):
        rec["llm_error"] = f"engine: {result.get('error', 'ok=False')}"
        return rec
    rec["reply"] = str(result.get("reply") or "")
    rec["filler"] = str(result.get("filler") or "")
    rec["brain"] = str(result.get("brain") or "")
    action = result.get("action") or {}
    rec["action"] = str(action.get("action") or "")
    if probe is not None and probe.last_error is not None:
        rec["llm_error"] = f"{type(probe.last_error).__name__}: {probe.last_error}"
    return rec


def _prejudge(reply: str) -> Tuple[str, List[str], List[str]]:
    """确定性关键词初判：返回 (初判, 接续命中词, 不接续命中词)。

    初判仅供参考，最终判定以 PM 人工终审为准。规则按序：
    1. 命中不接续词且无接续词 → 「不接续」；
    2. 命中接续词 → 「接续」（含双命中：烦躁语气词压过热情词）；
    3. 两者皆无 → 「中性」。
    """
    c_hits = [w for w in CONTINUE_WORDS if w in reply]
    b_hits = [w for w in BREAK_WORDS if w in reply]
    if b_hits and not c_hits:
        return "不接续", c_hits, b_hits
    if c_hits:
        return "接续", c_hits, b_hits
    return "中性", c_hits, b_hits


def _classify(rec: Dict[str, Any]) -> None:
    """样本分类（就地写入）：failed / fast / no_llm / no_speak / valid。

    剔除顺序即证据优先级：失败 > 快脑 > 无 LLM 调用 > 非 SPEAK。
    后两类若不剔除，模板化 fallback/拒接文本会被误判「中性」，
    污染 ≤20% 验收比例（防假证据）。
    """
    if rec["llm_error"]:
        rec["cls"] = "failed"
    elif rec["brain"] == "fast":
        rec["cls"] = "fast"
    elif not rec["llm_called"]:
        rec["cls"] = "no_llm"
    elif rec["action"] != "speak":
        rec["cls"] = "no_speak"
    else:
        rec["cls"] = "valid"
        verdict, c_hits, b_hits = _prejudge(rec["reply"])
        rec["verdict"] = verdict
        rec["c_hits"] = c_hits
        rec["b_hits"] = b_hits


def _print_record(rec: Dict[str, Any]) -> None:
    """打印单条样本：序号/问句/实际垫话/reply 全文/延迟/brain/初判。"""
    print(f"\n[样本 {rec['idx']}/{len(QUESTIONS)}] 玩家：{rec['question']}")
    print(f"  实际垫话：「{rec['filler'] or '（未捕获）'}」")
    if rec["filler"] and rec["filler"] != EXPECTED_FILLER:
        print(f"  ⚠ 垫话与预期高压垫话「{EXPECTED_FILLER}」不符"
              "（语气连续性前提存疑，供 PM 终审核对）")
    stress_pre = rec["stress_pre"]
    stress_post = rec["stress_post"]
    post_text = f"{stress_post:.2f}" if isinstance(stress_post, float) else "?"
    print(f"  brain：{rec['brain'] or '?'}    动作：{rec['action'] or '?'}"
          f"    延迟：{rec['latency']:.2f}s"
          f"    S_stress 前={stress_pre:.2f} 后={post_text}")
    if rec["llm_error"]:
        print(f"  ✗ LLM 失败：{rec['llm_error']}")
        print("    （下方回复为决策层 fallback 安全回退，非真实 LLM 输出）")
    print(f"  回复全文：{rec['reply'] or '（空）'}")
    if rec["cls"] == "failed":
        print("  标记：LLM 失败（fallback 回复，不计入有效样本）")
    elif rec["cls"] == "fast":
        print("  标记：无效样本（快脑命中 brain==\"fast\"，不计入）")
    elif rec["cls"] == "no_llm":
        print("  标记：无效样本（本轮无 LLM 调用——决策层硬规则拦截，"
              "如 S_stress>0.8 拒单/梦呓回退，无慢脑回复可判）")
    elif rec["cls"] == "no_speak":
        print(f"  标记：无效样本（LLM 选择非 SPEAK 动作「{rec['action']}」，"
              "无语气文本可判）")
    else:
        parts: List[str] = []
        if rec["c_hits"]:
            parts.append("接续词「" + "、".join(rec["c_hits"]) + "」")
        if rec["b_hits"]:
            parts.append("不接续词「" + "、".join(rec["b_hits"]) + "」")
        detail = f"（{'；'.join(parts)}）" if parts else "（无特征词命中）"
        print(f"  预标注初判：{rec['verdict']}{detail}")


# ------------------------------------------------------------------ #
# 主流程
# ------------------------------------------------------------------ #
def main() -> int:
    mock = "--mock" in sys.argv
    print(LINE)
    print("真实 LLM 语气连续性采样（tone_continuity）")
    print("审查第九次指令 1 第二项：高压垫话后慢脑不接续样本 ≤20%（真实口径采样）")
    print(LINE)

    # 1) 构造引擎（Mock provider 初始化）
    try:
        engine = _build_engine()
    except Exception as exc:
        print(f"✗ 引擎构造失败：{type(exc).__name__}: {exc}")
        return 1
    chen = engine.npcs.get("chen")
    if chen is None:
        print("✗ 前提不满足：engine.npcs 无 chen。")
        return 1
    from engine.inner_state import BAND_STRESS_HIGH

    # 2) 高压预置说明 + 前提校验（脾气掩码 + 采样前实测垫话）
    print(f"\n高压预置口径（对任务书 0.85 的修正，理由见脚本 docstring）：")
    print(f"  decision.decide() 硬规则 S_stress > 0.8 即 REFUSE 拒单、不调 LLM")
    print(f"  （engine/decision.py:128）——0.85 会让全部样本无 LLM 回复。")
    eff = HIGH_STRESS_PRESET + 0.05  # player_spoke 增量后实测值
    print(f"  故每条对话前重置 S_stress={HIGH_STRESS_PRESET}，叠加 player_spoke"
          f" 的 +0.05 后实测 {eff:.2f} ∈（{BAND_STRESS_HIGH} 高压带, 0.8 拒单线]，")
    print("  垫话高压与慢脑 LLM 回复双前提同时成立。")
    chen.inner_state.S_stress = HIGH_STRESS_PRESET
    tid = chen.filler_engine.temperament_id
    print(f"脾气掩码：{tid or '（无）'}")
    preview = chen.filler_engine.generate(chen.inner_state)
    print(f"前提校验（采样前实测垫话，S_stress={HIGH_STRESS_PRESET}）：「{preview}」")
    if preview != EXPECTED_FILLER:
        print(f"  ⚠ 实测垫话与预期高压垫话「{EXPECTED_FILLER}」不符——继续采样，")
        print("    每条样本以实际垫话为准，PM 终审时请核对该前提。")
    else:
        print("  ✓ 高压带命中（irritable · S_stress > 0.6）")

    # 3) 替换真实 provider（--mock 自测口径除外，不消耗真实 API）
    probe = None
    if mock:
        print("\n[自测口径] --mock：保留 MockLLMProvider，不替换真实 provider，")
        print("  不消耗真实 API 调用——仅验证脚本主流程完整性与样本集 brain 口径。")
    else:
        real = _build_real_provider()
        print(f"\n真实端点：model={getattr(real, 'model', '?')}"
              f" base_url={getattr(real, 'base_url', '?')}")
        if not getattr(real, "available", False):
            print("✗ API 不可用：NPC_LLM_BASE_URL 未配置——采样继续跑，每条")
            print("  回复将标记失败原因（决策层 LLMError→fallback，记录不阻塞）。")
        probe = _make_tracking_probe(real)
        _swap_provider(engine, probe)

    # 4) 采样循环：每条对话前重置高压，6 条慢脑问句逐条记录
    #    （重置理由：player_spoke 每条 +0.05 只升不降，单次预置下第 6 条
    #     累积 +0.30 必然击穿 0.8 拒单线；重置保证每条样本初始条件一致）
    print(f"\n{'-' * 68}")
    print(f"采样循环（{len(QUESTIONS)} 条慢脑复杂问句，全部问 chen，"
          f"每条前重置 S_stress={HIGH_STRESS_PRESET}）")
    print("-" * 68)
    records: List[Dict[str, Any]] = []
    for idx, text in enumerate(QUESTIONS, 1):
        chen.inner_state.S_stress = HIGH_STRESS_PRESET
        stress_pre = chen.inner_state.S_stress
        if stress_pre <= BAND_STRESS_HIGH:
            print(f"⚠ 样本 {idx} 前 S_stress={stress_pre:.2f} 已跌破 {BAND_STRESS_HIGH}"
                  "——高压前提失效，本条以实际垫话为准（PM 终审核对）")
        rec = _ask(engine, probe, "chen", text)
        rec["idx"] = idx
        rec["question"] = text
        rec["stress_pre"] = stress_pre
        _classify(rec)
        records.append(rec)
        _print_record(rec)

    # 5) 汇总（初判口径；终审以 PM 人工判读为准）
    valid = [r for r in records if r["cls"] == "valid"]
    failed = [r for r in records if r["cls"] == "failed"]
    fast = [r for r in records if r["cls"] == "fast"]
    no_llm = [r for r in records if r["cls"] == "no_llm"]
    no_speak = [r for r in records if r["cls"] == "no_speak"]
    n_break = sum(1 for r in valid if r["verdict"] == "不接续")

    print(f"\n{LINE}")
    print("采样汇总（语气连续性验收·初判口径）")
    print(LINE)
    print(f"  采样总数：{len(records)} 条")
    print(f"  有效样本数：{len(valid)}"
          f"（剔除：LLM 失败 {len(failed)} 条、快脑命中 {len(fast)} 条、"
          f"无 LLM 调用 {len(no_llm)} 条、非 SPEAK {len(no_speak)} 条）")
    for r in failed:
        print(f"    - 样本 {r['idx']}「{r['question']}」：{r['llm_error']}")
    for r in fast:
        print(f"    - 样本 {r['idx']}「{r['question']}」：brain==\"fast\" 快脑命中")
    for r in no_llm:
        print(f"    - 样本 {r['idx']}「{r['question']}」：无 LLM 调用"
              f"（决策层硬规则拦截，动作={r['action'] or '?'}）")
    for r in no_speak:
        print(f"    - 样本 {r['idx']}「{r['question']}」：LLM 选择非 SPEAK"
              f" 动作「{r['action']}」")
    if not valid:
        print("  ✗ 有效样本为 0——无法给出初判比例（全部失败或全部无效）。")
        print(LINE)
        return 1
    ratio = n_break / len(valid)
    print(f"  初判「不接续」：{n_break} 条 / 有效 {len(valid)} 条 = {ratio:.1%}")
    for r in valid:
        print(f"    - 样本 {r['idx']}「{r['question']}」初判：{r['verdict']}")
    print("  不接续样本 ≤20% 验收线（初判口径，终审以 PM 人工判读为准）")
    if ratio <= 0.20:
        print(f"  初判结论：{ratio:.1%} ≤ 20%——初判口径达标，待 PM 人工终审")
    else:
        print(f"  初判结论：{ratio:.1%} > 20%——初判口径超标，需 PM 人工终审复核")
    print(LINE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

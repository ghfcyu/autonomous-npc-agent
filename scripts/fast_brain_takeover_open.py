"""快脑接管率非固化二次采样脚本（审查第九次指令 2 口径）。

用途：在固化 12 条口径（fast_brain_takeover.py）之外，用非固化分布
再采一次快慢脑分流结果——固化样本的接管率不再单独作为达标证据，
本脚本提供独立的第二采样口径，并把采样口径与结果一并落日志。

样本设计（13 条非固化分布，四类）：
    A 冒烟复杂   3 条：llm_smoke.py 同款复杂句（问候后带话题/
                      打剑意图/称谓+问新货），期望 slow——快脑
                      不得误拦复杂对话；
    B 已覆盖口语 4 条：规则表已覆盖的口语变体（问候语气助词
                      「你好呀/早上好啊」、问时带前缀「现在几点了」、
                      问价句尾锚定「铁剑多少钱」），期望 fast；
    C GAP 候选   4 条：「应拦未拦」候选——「老板在吗/在吗/有人吗/
                      请问有人在吗」类高频封闭句式，语义上可秒回；
                      2026-10-10 22:00 PM 裁定 presence（确认在场）
                      扩充进快脑规则表后，C 类 GAP 候选预期改为
                      fast（缺口收口，0 LLM 调用秒回）；
    D 复杂寒暄   2 条：含问候/称谓但带后续内容的长句（「你好，
                      请问……」「大爷您好，跟您……」），期望
                      slow——复杂寒暄不应拦。

边界声明：GAP 候选扩充已由 PM 于 2026-10-10 22:00 裁定落地
（engine/fast_brain.py 新增 presence 意图，非固化接管率 30.8%
→ 61.5%）；本脚本只采样不改引擎的定位不变——规则表后续再扩充
仍由 PM 另行裁定，本次同步的仅是 C 类预期口径与注释。

确定性：main() 首行 random.seed(42)（与 fast_brain_takeover.py 同
口径）；MockLLMProvider 默认 seed=42、chaos_rate=0，不 tick 天气
不扰动。快慢脑分流是纯规则判定（在 LLM 调用之前），mock 口径下
确定性成立，输出不随运行波动。

契约依赖（与固化脚本同款，不感知快脑内部细节）：
    - 主口径：player_says 返回结构含 "brain" 字段（"fast"|"slow"）；
    - 回退口径：引擎属性 fast_brain_stats = {"hits": n, "total": m}，
      逐条取前后差值判定；
    - 两条口径皆不可用 = 快脑未合入：打印待联调说明，退出码 2。

返回码：0 = 采样完成（不判达标——非固化口径的定位是证据采样，
          固化样本接管率不单独作达标证据，本脚本同样不设验收线）；
        2 = 快脑未合入（brain 口径缺失，同固化脚本处理）。

用法：
    python3 scripts/fast_brain_takeover_open.py
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider

# ------------------------------------------------------------------ #
# 13 条非固化样本：(玩家输入, npc_id, 类别代号)。
# 类别代号 → (类别名, 当前口径下预期 brain)。
#   - A/B/D 的预期是"规则表应然"（不误拦复杂 / 覆盖口语 / 不误拦
#     复杂寒暄）；
#   - C 的预期同为"规则表应然"——2026-10-10 22:00 PM 裁定
#     presence（确认在场）扩充进快脑规则表后，C 类 GAP 候选
#     预期改为 fast（缺口收口：4/4 拦截，0 LLM 调用秒回）。
# ------------------------------------------------------------------ #
CATEGORY_LABELS = {
    "A": ("A·冒烟复杂", "slow"),
    "B": ("B·已覆盖口语", "fast"),
    "C": ("C·GAP候选", "fast"),
    "D": ("D·复杂寒暄", "slow"),
}

SAMPLES = [
    # --- 类别 A：冒烟复杂（llm_smoke.py 同款，期望 slow——不误拦）---
    ("你好，听说你是这条街上手艺最好的铁匠？", "chen", "A"),
    ("帮我打一把剑，钱不是问题！", "chen", "A"),
    ("老板娘，今天有什么新货吗？", "lily", "A"),
    # --- 类别 B：已覆盖口语变体（期望 fast——规则表已覆盖）---
    ("你好呀", "lily", "B"),
    ("早上好啊", "chen", "B"),
    ("现在几点了", "chen", "B"),
    ("铁剑多少钱", "chen", "B"),
    # --- 类别 C：GAP 候选（presence 扩充后预期 fast——2026-10-10
    #     22:00 PM 裁定，4/4 拦截即缺口收口）---
    ("老板在吗", "lily", "C"),
    ("在吗", "chen", "C"),
    ("有人吗", "chen", "C"),
    ("请问有人在吗", "lily", "C"),
    # --- 类别 D：复杂寒暄不应拦（期望 slow——含问候/称谓但带内容）---
    ("你好，请问铁匠铺还开着吗？", "chen", "D"),
    ("大爷您好，跟您打听个事儿", "chen", "D"),
]

GAP_COUNT = 4        # 类别 C 样本数（GAP 候选）
REPLY_PREVIEW = 20   # 明细中 reply 预览字符数


def _fast_stats(engine: NPCEngine) -> dict:
    """引擎快脑统计快照（fast_brain_stats 未合入时返回空 dict）。"""
    stats = getattr(engine, "fast_brain_stats", None)
    return dict(stats) if isinstance(stats, dict) else {}


def _read_brain(result: dict, engine: NPCEngine, stats_before: dict):
    """读单条对话的分流路径：主口径 result["brain"]，回退差值口径。

    与固化脚本同款：result["brain"] in ("fast", "slow") 直接返回；
    否则若 fast_brain_stats.total 前移：hits 前移判 "fast"，否则判
    "slow"；两条口径皆不可用 → None（快脑未合入）。
    """
    brain = result.get("brain")
    if brain in ("fast", "slow"):
        return brain
    after = _fast_stats(engine)
    if after.get("total", 0) > stats_before.get("total", 0):
        return "fast" if after.get("hits", 0) > stats_before.get("hits", 0) else "slow"
    return None


def main() -> int:
    # 固定随机种子：与 fast_brain_takeover.py / token_baseline.py 同
    # 口径（seed=42）。分流判定在 LLM 调用之前纯规则完成，mock 口径
    # 下确定性成立；本脚本不 tick（无天气随机扰动）。
    random.seed(42)

    engine = NPCEngine(llm=MockLLMProvider())

    print("=" * 64)
    print("  快脑接管率非固化二次采样  ·  MockLLMProvider  ·  13 条非固化分布")
    print("  （A 冒烟复杂 3 + B 已覆盖口语 4 + C GAP候选 4 + D 复杂寒暄 2）")
    print("  审查第九次指令 2：固化样本接管率不再单独作达标证据")
    print("=" * 64)
    print()

    # 1) 逐条采样：读分流路径 + LLM 调用差值（mock.call_count 口径）
    rows = []  # (序号, 类别, brain, npc_id, llm_calls, 文本, reply 预览)
    for idx, (text, npc_id, cat) in enumerate(SAMPLES, 1):
        stats_before = _fast_stats(engine)
        calls_before = getattr(engine.llm, "call_count", 0)
        result = engine.player_says(text, npc_id)
        calls_delta = getattr(engine.llm, "call_count", 0) - calls_before
        brain = _read_brain(result, engine, stats_before)

        if brain is None:
            # 快脑未合入：两条口径皆缺失，打印待联调说明并返回（不判失败）
            reply = result.get("reply", "")
            print(f"  首条采样「{text}」→ {npc_id}：reply = {reply[:REPLY_PREVIEW]}")
            print("  （引擎本身运转正常，player_says 闭环无恙）")
            print()
            print("-" * 64)
            print("快脑未合入：player_says 返回无 \"brain\" 字段，且引擎无")
            print("fast_brain_stats 属性——engine/fast_brain.py（快脑批 A 侧）")
            print("尚未接入 player_says。待 A 合入后由 PM 联调复跑本脚本，")
            print("脚本自身无需改动。")
            print("=" * 64)
            return 2

        rows.append((idx, cat, brain, npc_id, calls_delta,
                     text, result.get("reply", "")[:REPLY_PREVIEW]))

    # 2) 逐条明细：# / NPC / brain / LLM 调用差值 / 类别标记 / 文本 → reply 预览
    print("逐条明细：")
    print(f"  {'#':>2}  {'NPC':<6} {'brain':<6} {'LLM':>3}  "
          f"[类别]「对话文本」→ reply（前 {REPLY_PREVIEW} 字符）")
    print("  " + "-" * 60)
    for idx, cat, brain, npc_id, calls, text, reply in rows:
        print(f"  {idx:>2}  {npc_id:<6} {brain:<6} {calls:>3}  "
              f"[{cat}]「{text}」→ {reply}")
    print()

    # 3) 预期对照（当前口径下：A/B/D 应然 + C 实然缺口）
    mismatch = [(r[0], r[1], r[2]) for r in rows
                if r[2] != CATEGORY_LABELS[r[1]][1]]
    if mismatch:
        print("预期-实际偏差（当前口径下，联调定位用）：")
        for idx, cat, brain in mismatch:
            expect = CATEGORY_LABELS[cat][1]
            print(f"  第 {idx} 条 [{cat}·{CATEGORY_LABELS[cat][0][2:]}]："
                  f"预期 {expect}，实际 {brain}")
        print()
    else:
        print(f"预期对照：{len(rows)}/{len(rows)} 条与当前口径预期一致"
              f"（A/B/D 应然口径成立；C presence 扩充后全走 fast）")
        print()

    # 4) 汇总统计
    total = len(rows)
    fast_hits = sum(1 for r in rows if r[2] == "fast")
    slow_hits = total - fast_hits
    takeover = fast_hits / total if total else 0.0

    # GAP 候选（类别 C）口径：被快脑拦截数与白付 LLM 调用数
    gap_rows = [r for r in rows if r[1] == "C"]
    gap_fast = sum(1 for r in gap_rows if r[2] == "fast")
    gap_missed = len(gap_rows) - gap_fast
    gap_llm_wasted = sum(r[4] for r in gap_rows if r[2] == "slow")

    # 各类别 fast/slow 分布表
    cat_stats = {c: {"fast": 0, "slow": 0} for c in CATEGORY_LABELS}
    for r in rows:
        cat_stats[r[1]][r[2]] += 1

    print("-" * 64)
    print(f"样本总数         ：{total}（A 冒烟复杂 3 + B 已覆盖口语 4 "
          f"+ C GAP候选 {len(gap_rows)} + D 复杂寒暄 2）")
    print(f"快脑命中（fast） ：{fast_hits} 条")
    print(f"慢脑处理（slow） ：{slow_hits} 条")
    print(f"非固化总接管率   ：{fast_hits}/{total} = {takeover:.1%}")
    print(f"GAP 候选拦截数   ：{gap_fast}/{len(gap_rows)}"
          f"（presence 扩充后预期 4/4 拦截；{gap_missed} 条仍走慢脑白付"
          f" LLM 调用 {gap_llm_wasted} 次）")
    print("各类别 fast/slow 分布：")
    for cat, (label, _) in CATEGORY_LABELS.items():
        cs = cat_stats[cat]
        print(f"  {label:<8}：fast {cs['fast']} / slow {cs['slow']}"
              f"（当前口径预期全 {CATEGORY_LABELS[cat][1]}）")

    # 5) 结论行（审查指令 2 口径：只呈证据不判达标）
    print(f"结论             ：非固化口径接管率 {takeover:.1%}"
          f"（固化 66.7% 口径不单独作达标证据；GAP 候选扩充后拦截 "
          f"{gap_fast}/{GAP_COUNT}——presence 入表缺口收口）")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

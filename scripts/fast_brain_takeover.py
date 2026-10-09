"""快脑接管率实测脚本（T3 快慢脑批验收度量）。

用途：实测快慢脑分流的快脑接管率，对照对抗升级验收线（≥50%）。
审查指令 2 要求"快脑接管率实测"，本脚本固化 12 条混合对话口径，
一键复跑即可看到接管率、快脑命中条 LLM 调用数（期望 0）与结论行。

场景：
    1. 默认 NPCEngine(llm=MockLLMProvider())，加载全部 configs
       （chen/lily 核心 NPC，tag_profile 常驻挂载默认生效）；
    2. 12 条混合对话逐条 player_says：
       - 前 8 条 = 快脑可接管的高频日常意图，覆盖 FastBrain
         全部 5 类意图（greet 招呼 / ask_direction 问路 /
         ask_price 问价 / farewell 道别 / ask_time 问时）；
       - 后 4 条 = 复杂话题（token_baseline.py 同款 4 句，
         Mock 话题库可命中，期望走慢脑 LLM 决策链）；
    3. 逐条读分流路径，统计：
       - 接管率 = fast 命中数 / 对话总数（期望 8/12 ≈ 66.7%）；
       - 快脑命中条的 LLM 调用数（期望 0——mock.call_count 逐条
         差值口径，status() llm_calls 全局口径对照）；
       - 结论行：接管率是否 ≥50%（对抗升级验收线）。

契约依赖（T3 快脑批 A 侧实现，本脚本不感知其内部细节）：
    - 主口径：player_says 返回结构含 "brain" 字段（"fast"|"slow"）；
    - 回退口径：引擎属性 fast_brain_stats = {"hits": n, "total": m}，
      逐条取前后差值判定（total 前移即该条计入，hits 前移判 fast）；
    - 两条口径皆不可用 = 快脑未合入：脚本打印"待 A 合入后联调"
      说明并以退出码 2 返回（不判失败——合入后 PM 联调复跑即可）。

用法：
    python3 scripts/fast_brain_takeover.py

确定性：
    main() 首行 random.seed(42)（与 token_baseline.py 同口径）；
    MockLLMProvider 默认 seed=42、chaos_rate=0，不 tick 天气不扰动，
    输出不随运行波动（快脑命中条 reply 为规则路径亦确定）。
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider

# 12 条混合对话：(玩家输入, npc_id, 预期意图)。
# 前 8 条快脑可接管（覆盖 5 意图全部），后 4 条复杂话题期望走慢脑。
DIALOGUES = [
    # --- 快脑高频日常意图（期望 brain == "fast"）---
    # 边界记录（第九次审查指令 1 裁定更新）：「你好啊/你好呀」等高频
    # 带语气助词问候已入 GREET_PHRASES 快脑秒回（原「保守走慢脑」口径
    # 被推翻；test_background_npc.py 的对照句已改为慢脑必达复杂句），
    # 本条由「您好」恢复为「你好啊」实测该边界。
    ("你好", "chen", "greet"),
    ("你好啊", "lily", "greet"),
    ("铁匠铺怎么走", "lily", "ask_direction"),
    ("多少钱", "chen", "ask_price"),
    ("这个多少钱", "lily", "ask_price"),
    ("再见", "chen", "farewell"),
    ("几点了", "chen", "ask_time"),
    ("现在几点了", "lily", "ask_time"),
    # --- 复杂话题（期望 brain == "slow"，token_baseline.py 同款 4 句）---
    ("铁匠，打把剑", "chen", "topic"),
    ("有好铁矿石吗", "chen", "topic"),
    ("有新货吗", "lily", "topic"),
    ("有什么消息", "lily", "topic"),
]

FAST_EXPECTED = 8            # 期望快脑命中条数（8/12 ≈ 66.7%）
TAKEOVER_ACCEPT_LINE = 0.50   # 对抗升级验收线：快脑接管率 ≥ 50%
REPLY_PREVIEW = 20            # 明细中 reply 预览字符数


def _fast_stats(engine: NPCEngine) -> dict:
    """引擎快脑统计快照（fast_brain_stats 未合入时返回空 dict）。"""
    stats = getattr(engine, "fast_brain_stats", None)
    return dict(stats) if isinstance(stats, dict) else {}


def _read_brain(result: dict, engine: NPCEngine, stats_before: dict):
    """读单条对话的分流路径：主口径 result["brain"]，回退差值口径。

    - result["brain"] in ("fast", "slow") → 直接返回（契约主口径）；
    - 否则若 fast_brain_stats.total 前移：hits 前移判 "fast"，
      否则判 "slow"（回退差值口径）；
    - 两条口径皆不可用 → None（快脑未合入）。
    """
    brain = result.get("brain")
    if brain in ("fast", "slow"):
        return brain
    after = _fast_stats(engine)
    if after.get("total", 0) > stats_before.get("total", 0):
        return "fast" if after.get("hits", 0) > stats_before.get("hits", 0) else "slow"
    return None


def main() -> int:
    # 固定随机种子：与 token_baseline.py 同口径（seed=42）。
    # 常驻挂载走 crc32(npc_id) 确定性种子，Mock 走自带 seed=42，
    # 本脚本不 tick（无天气随机扰动），输出确定性可复现。
    random.seed(42)

    engine = NPCEngine(llm=MockLLMProvider())

    print("=" * 64)
    print("  快脑接管率实测  ·  MockLLMProvider  ·  12 条混合对话")
    print("  （8 条五意图高频日常 + 4 条复杂话题，对抗升级验收线 ≥50%）")
    print("=" * 64)
    print()

    # 1) 逐条对话：读分流路径 + LLM 调用差值（mock.call_count 口径）
    rows = []  # (序号, 意图, brain, npc_id, llm_calls, 文本, reply 预览)
    for idx, (text, npc_id, intent) in enumerate(DIALOGUES, 1):
        stats_before = _fast_stats(engine)
        calls_before = getattr(engine.llm, "call_count", 0)
        result = engine.player_says(text, npc_id)
        calls_delta = getattr(engine.llm, "call_count", 0) - calls_before
        brain = _read_brain(result, engine, stats_before)

        if brain is None:
            # 快脑未合入：两条口径皆缺失，记录待联调并返回（不判失败）
            reply = result.get("reply", "")
            print(f"  引擎自检对话「{text}」→ {npc_id}：reply = {reply[:REPLY_PREVIEW]}")
            print("  （引擎本身运转正常，player_says 闭环无恙）")
            print()
            print("-" * 64)
            print("快脑未合入：player_says 返回无 \"brain\" 字段，且引擎无")
            print("fast_brain_stats 属性——engine/fast_brain.py（快脑批 A 侧）")
            print("尚未接入 player_says。待 A 合入后由 PM 联调复跑本脚本，")
            print("脚本自身无需改动。")
            print("=" * 64)
            return 2

        rows.append((idx, intent, brain, npc_id, calls_delta,
                     text, result.get("reply", "")[:REPLY_PREVIEW]))

    # 2) 逐条明细
    print("逐条明细：")
    print(f"  {'#':>2}  {'NPC':<6} {'brain':<6} {'LLM':>3}  "
          f"[预期意图]「对话文本」→ reply（前 {REPLY_PREVIEW} 字符）")
    print("  " + "-" * 60)
    for idx, intent, brain, npc_id, calls, text, reply in rows:
        print(f"  {idx:>2}  {npc_id:<6} {brain:<6} {calls:>3}  "
              f"[{intent}]「{text}」→ {reply}")
    print()

    # 3) 预期-实际偏差（联调定位用：哪条意图没被快脑接管/误接管）
    expected_fast = {r[0] for r in rows if r[0] <= FAST_EXPECTED}
    mismatch = [(r[0], r[1], r[2]) for r in rows
                if (r[0] in expected_fast) != (r[2] == "fast")]
    if mismatch:
        print("预期-实际偏差（联调定位用）：")
        for idx, intent, brain in mismatch:
            expect = "fast" if idx <= FAST_EXPECTED else "slow"
            print(f"  第 {idx} 条 [{intent}]：预期 {expect}，实际 {brain}")
        print()

    # 4) 汇总统计
    total = len(rows)
    fast_hits = sum(1 for r in rows if r[2] == "fast")
    slow_hits = total - fast_hits
    takeover = fast_hits / total if total else 0.0
    fast_llm_calls = sum(r[4] for r in rows if r[2] == "fast")
    slow_llm_calls = sum(r[4] for r in rows if r[2] == "slow")
    status_llm_calls = engine.status()["token_stats"]["llm_calls"]

    print("-" * 64)
    print(f"对话总数         ：{total}（快脑意图 {FAST_EXPECTED} 条 + 复杂话题 {total - FAST_EXPECTED} 条）")
    print(f"快脑命中（fast） ：{fast_hits} 条")
    print(f"慢脑处理（slow） ：{slow_hits} 条")
    print(f"快脑接管率       ：{fast_hits}/{total} = {takeover:.1%}"
          f"（期望 {FAST_EXPECTED}/{total} ≈ {FAST_EXPECTED / total:.1%}）")
    print(f"快脑命中条 LLM 调用：{fast_llm_calls}（期望 0——0-token 秒回，"
          f"mock.call_count 逐条差值口径）")
    print(f"慢脑条 LLM 调用   ：{slow_llm_calls}（每条 1 次 Mock 调用）")
    print(f"LLM 调用总数     ：{status_llm_calls}（status() llm_calls 全局口径，"
          f"= 快脑 {fast_llm_calls} + 慢脑 {slow_llm_calls}）")

    # 引擎口径对照（fast_brain_stats / status()["fast_brain"] 可用时打印）
    engine_stats = _fast_stats(engine)
    if engine_stats:
        engine_takeover = (engine_stats.get("hits", 0)
                           / engine_stats["total"]) if engine_stats.get("total") else 0.0
        consistent = engine_stats.get("hits", 0) == fast_hits and \
            engine_stats.get("total", 0) == total
        flag = "✓ 一致" if consistent else "✗ 不一致（联调核查）"
        print(f"引擎口径对照     ：fast_brain_stats = "
              f"{{\"hits\": {engine_stats.get('hits', 0)}, \"total\": {engine_stats.get('total', 0)}}}"
              f"（接管率 {engine_takeover:.1%}）{flag}")
    fast_brain_status = engine.status().get("fast_brain")
    if fast_brain_status is not None:
        print(f"status() 对照    ：fast_brain = {fast_brain_status}")

    # 5) 结论行（对抗升级验收线）
    passed = takeover >= TAKEOVER_ACCEPT_LINE
    verdict = "✓ 达标" if passed else "✗ 未达标"
    print(f"结论             ：接管率 {takeover:.1%} "
          f"{'≥' if passed else '<'} {TAKEOVER_ACCEPT_LINE:.0%}（对抗升级验收线）{verdict}")
    print("=" * 64)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Token 基线一键复现脚本。

用途：固化"全天运转 + 4 次核心对话"的 token 基线场景，
审查者/迭代者一键复跑即可看到 ~4400-4800 量级数字，
用于卡住 token 回归（审查硬标准 4400-4800）。

场景：
    1. 默认 NPCEngine(llm=MockLLMProvider())，加载全部 10 NPC（2 核心 + 8 背景）；
    2. 24 次 tick(60) 模拟一整天（24 小时），NPC 作息驱动运转；
    3. 4 次核心对话（2 chen + 2 lily，用 Mock 话题库能命中的输入）；
    4. 打印 token 统计：按 NPC 分解（prompt/completion/total/calls）、
       核心合计、背景 NPC 合计（应为 0）、LLM 调用次数。

用法：
    python3 scripts/token_baseline.py

确定性：
    main() 首行 random.seed(42) 固定天气随机（world.py 天气变化走
    random.random()），24 次 tick 的天气事件序列可复现，
    token 总消耗恒定（4769，T2 收口先行批 chen/lily 挂载常驻化后口径），
    不随运行波动。
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import NPCEngine
from engine.llm.mock import MockLLMProvider

# 核心 NPC id（持有 LLM 引用，对话产生 token）
CORE_NPCS = {"chen", "lily"}
# 4 次核心对话：(输入, npc_id)，均命中 Mock 话题库
CORE_DIALOGUES = [
    ("铁匠，打把剑", "chen"),
    ("有好铁矿石吗", "chen"),
    ("有新货吗", "lily"),
    ("有什么消息", "lily"),
]


def main() -> int:
    # 固定随机种子：world.py 天气变化用 random.random() 触发，
    # 24 次 tick 中随机天气事件写入 NPC 记忆会影响 context_for 输出，
    # 导致 token 数字每次运行波动。固定 seed=42 后输出确定性可复现。
    # T1 8D 心智基底迁移后口径：【此刻内心】5 参数→8 参数 + 系统 prompt
    # 注入玩家亲缘度边，token 总消耗旧基线 4553 → 新基线 4737
    # （两次完整场景实测严格相等，仍在审查硬标准 4400-4800 内）。
    # T2 收口先行批（2026-10-09）：configs 声明 tag_profile="genesis" 的
    # chen/lily 挂载常驻化，system prompt 增身份标签行
    # （"\n身份标签：断指"/"\n身份标签：跛足"，8 字符/次调用），
    # 常驻化身份标签行带来的合法增量 4737 → 4769（+32，每 NPC 2 次
    # 调用×8 字符；结构性增量非单点裁剪，实测仍在硬标准内）。
    random.seed(42)
    # 1) 默认引擎：MockLLMProvider + 全部 10 NPC（2 核心 + 8 背景）
    engine = NPCEngine(llm=MockLLMProvider())
    core_ids = sorted(n for n in engine.npcs if n in CORE_NPCS)
    bg_ids = sorted(n for n in engine.background_npcs)
    total_npcs = len(engine.npcs) + len(engine.background_npcs)

    print("=" * 64)
    print("  Token 基线复现  ·  MockLLMProvider  ·  全天运转 + 4 核心对话")
    print("=" * 64)
    print(f"NPC 总数：{total_npcs}（核心 {len(core_ids)}：{core_ids}；"
          f"背景 {len(bg_ids)}：{bg_ids}）")
    print(f"模拟：24 次 tick(60) = 一整天（24h）")
    print()

    # 2) 24 次 tick(60) 模拟一整天（作息驱动，规则路径，零 LLM）
    for _ in range(24):
        engine.tick(60)
    print(f"运转完成，时钟回到 {engine.world.clock}，NPC 已在工作地。")
    print()

    # 3) 4 次核心对话（2 chen + 2 lily）
    print("核心对话：")
    for text, npc_id in CORE_DIALOGUES:
        result = engine.player_says(text, npc_id)
        ok = "✓" if result.get("ok") else "✗"
        reply = result.get("reply", "")
        print(f"  {ok} 玩家 → {npc_id:<6}「{text}」 → {reply}")
    print()

    # 4) Token 统计
    stats = engine.status()["token_stats"]
    by_npc = stats["by_npc"]
    total_tokens = stats["total_tokens"]
    llm_calls = stats["llm_calls"]

    # 按 NPC 分解
    print("-" * 64)
    print("按 NPC 分解：")
    header = f"  {'NPC':<14} {'prompt':>8} {'completion':>10} {'total':>8} {'calls':>6}"
    print(header)
    print("  " + "-" * 52)
    for npc_id in sorted(by_npc):
        s = by_npc[npc_id]
        tag = "核心" if npc_id in CORE_NPCS else "背景"
        print(f"  {npc_id:<14} {s['prompt_tokens']:>8} {s['completion_tokens']:>10} "
              f"{s['total_tokens']:>8} {s['calls']:>6}  [{tag}]")
    print("  " + "-" * 52)

    # 核心合计 / 背景合计
    core_total = sum(s["total_tokens"] for n, s in by_npc.items() if n in CORE_NPCS)
    core_calls = sum(s["calls"] for n, s in by_npc.items() if n in CORE_NPCS)
    bg_total = sum(s["total_tokens"] for n, s in by_npc.items() if n not in CORE_NPCS)
    bg_calls = sum(s["calls"] for n, s in by_npc.items() if n not in CORE_NPCS)

    print(f"  {'核心合计':<14} {'':>8} {'':>10} {core_total:>8} {core_calls:>6}")
    print(f"  {'背景合计':<14} {'':>8} {'':>10} {bg_total:>8} {bg_calls:>6}  （应为 0）")
    print("-" * 64)
    print()

    # 汇总
    print(f"LLM 调用次数   ：{llm_calls}")
    print(f"Token 总消耗   ：{total_tokens}")
    in_range = 4400 <= total_tokens <= 4800
    flag = "✓ 在审查硬标准 4400-4800 内" if in_range else "✗ 超出审查硬标准 4400-4800"
    print(f"基线校验       ：{flag}")
    print(f"背景 NPC token ：{bg_total}（架构上不持有 LLM，应为 0）")
    print("=" * 64)
    return 0 if in_range else 1


if __name__ == "__main__":
    raise SystemExit(main())

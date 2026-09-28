"""CLI REPL 演示：零依赖跑通 感知→记忆→决策→行动 闭环。

用法：
    python3 scripts/run_demo.py                 # Mock LLM（离线）
    python3 scripts/run_demo.py --llm openai    # 接真实 OpenAI 兼容端点
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import NPCEngine
from engine.llm import create_provider

BANNER = """
=====================================================
   autonomous-npc-agent  ·  AI NPC 引擎 CLI 演示
=====================================================
命令：
  look                         查看世界状态
  goto <地点id>                移动（forge/market/plaza）
  talk <npc_id> <一句话>        和 NPC 对话
  tick                         推进时间 10 分钟
  status                       查看 NPC 状态与记忆量
  quit                         退出
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="AI NPC 引擎 CLI 演示")
    parser.add_argument("--llm", default=None, help="mock / openai")
    parser.add_argument("--store", default=None, help="记忆持久化目录")
    args = parser.parse_args()

    engine = NPCEngine(llm=create_provider(args.llm), store_dir=args.store)
    print(BANNER)
    status = engine.status()
    for loc in status["world"]["locations"]:
        who = "、".join(loc["entities"]) or "（空无一人）"
        print(f"  [{loc['id']:>7}] {loc['name']}：{who}")
    print()

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("quit", "exit", "q"):
            break

        parts = line.split(maxsplit=2)
        cmd = parts[0].lower()

        if cmd == "look":
            s = engine.status()
            w = s["world"]
            print(f"时间 {w['clock']} | 天气 {w['weather']} | 你在 {w['player']['location']}")
            for loc in w["locations"]:
                who = "、".join(loc["entities"]) or "（空无一人）"
                print(f"  [{loc['id']:>7}] {loc['name']}：{who}")
        elif cmd == "goto" and len(parts) >= 2:
            result = engine.move_player(parts[1])
            print(f"你走到了 {result['location']}" if result["ok"]
                  else "去不了那里。可选：forge / market / plaza")
        elif cmd == "talk" and len(parts) >= 3:
            npc_id, text = parts[1], parts[2]
            result = engine.player_says(text, npc_id)
            if result.get("ok"):
                print(f"[{result['npc']} · {result['state']}] {result['reply']}")
            else:
                print(result.get("error", "对话失败"))
        elif cmd == "tick":
            result = engine.tick()
            changes = result.get("schedule_applied") or {}
            extra = "；" + "、".join(f"{k}→{v}" for k, v in changes.items()) if changes else ""
            print(f"时间推进到 {result['clock']}{extra}")
        elif cmd == "status":
            for npc in engine.status()["npcs"]:
                m = npc["memory_size"]
                print(f"  {npc['name']}（{npc['role']}）：{npc['state']} | "
                      f"短期记忆 {m['short']} 条 / 长期记忆 {m['long']} 条")
        else:
            print("看不懂。可用：look / goto <地点> / talk <npc> <话> / tick / status / quit")
        print()


if __name__ == "__main__":
    main()

"""真实 LLM 连通性冒烟测试（可选工具，不进 unittest）。

用途：验证 .env 里配置的真实大模型端点能否驱动引擎完成
"感知→记忆→决策→行动"全链路，并遵守 JSON 动作契约。

用法：
    1. 复制 .env.example 为 .env，填入真实接口地址/密钥/模型名
    2. python3 scripts/llm_smoke.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load_env(path: str) -> None:
    """极简 .env 加载器：KEY=VALUE 行，# 注释，不覆盖已有环境变量。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    load_env(os.path.join(ROOT, ".env"))

    from engine.engine import NPCEngine
    from engine.llm import create_provider

    provider = create_provider(os.environ.get("NPC_LLM_PROVIDER", "openai"))
    print(f"Provider : {provider.name}")
    if getattr(provider, "name", "") == "openai-compat":
        print(f"Endpoint : {provider.base_url}")
        print(f"Model    : {provider.model}")
        if not provider.available:
            print("✗ 未配置 NPC_LLM_BASE_URL，请检查 .env")
            return 1

    engine = NPCEngine(llm=provider)
    cases = [
        ("chen", "你好，听说你是这条街上手艺最好的铁匠？"),
        ("chen", "帮我打一把剑，钱不是问题！"),
        ("lily", "老板娘，今天有什么新货吗？"),
    ]
    failures = 0
    for npc_id, text in cases:
        result = engine.player_says(text, npc_id)
        print(f"\n玩家 → {result.get('npc')}: {text}")
        print(f"回复 [{result.get('state')}]: {result.get('reply')}")
        print(f"动作契约: {result.get('action')}")
        if not result.get("ok") or not result.get("reply"):
            failures += 1

    print(f"\n{'✓ 全部用例通过' if failures == 0 else f'✗ {failures} 个用例失败'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

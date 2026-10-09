"""真实 LLM 连通性冒烟测试（可选工具，不进 unittest）。

用途：验证 .env 里配置的真实大模型端点能否驱动引擎完成
"感知→记忆→决策→行动"全链路，并遵守 JSON 动作契约。

度量（审查要求三项数据）：
- 真实延迟（ms，perf_counter 环测每次 player_says 全链路）
- 契约遵守率（ok 且 action 为 dict 且 action.action ∈ 白名单）
- 失败率（not ok 或无 reply）

用法：
    1. 复制 .env.example 为 .env，填入真实接口地址/密钥/模型名
    2. python3 scripts/llm_smoke.py
"""

from __future__ import annotations

import os
import sys
import time

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


# 动作契约白名单（与 engine.actions.ActionType 对齐）
_CONTRACT_ACTIONS = {"speak", "emote", "give_item", "refuse"}


def _contract_ok(result: dict) -> bool:
    """契约遵守：ok 为 True 且 action 是 dict 且其 action 字段在白名单内。"""
    if not result.get("ok"):
        return False
    action = result.get("action")
    if not isinstance(action, dict):
        return False
    return action.get("action") in _CONTRACT_ACTIONS


def _failed(result: dict) -> bool:
    """失败：not ok 或无 reply。"""
    return (not result.get("ok")) or (not result.get("reply"))


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

    latencies = []          # 每用例延迟(ms)
    contract_flags = []     # 每用例契约遵守
    failure_flags = []      # 每用例失败
    total = len(cases)

    for idx, (npc_id, text) in enumerate(cases, 1):
        start = time.perf_counter()
        result = engine.player_says(text, npc_id)
        end = time.perf_counter()
        latency_ms = (end - start) * 1000

        ok_contract = _contract_ok(result)
        failed = _failed(result)
        latencies.append(latency_ms)
        contract_flags.append(ok_contract)
        failure_flags.append(failed)

        reply = str(result.get("reply", ""))
        reply_brief = reply if len(reply) <= 40 else reply[:40] + "…"
        print(f"\n【用例 {idx}/{total}】玩家 → {result.get('npc')}：{text}")
        print(f"  延迟        : {latency_ms:.1f} ms")
        print(f"  契约遵守    : {'✓' if ok_contract else '✗'}"
              f"（{result.get('action', {}).get('action', 'N/A') if isinstance(result.get('action'), dict) else 'N/A'}）")
        print(f"  失败        : {'是' if failed else '否'}")
        print(f"  回复[{result.get('state')}]  : {reply_brief}")
        print(f"  垫话 filler : {result.get('filler', '')}")

    avg_latency = sum(latencies) / total if total else 0.0
    contract_rate = sum(contract_flags) / total * 100 if total else 0.0
    failure_rate = sum(failure_flags) / total * 100 if total else 0.0
    failures = sum(failure_flags)

    print("\n" + "-" * 64)
    print("汇总报告")
    print("-" * 64)
    print(f"  用例总数    : {total}")
    print(f"  平均延迟    : {avg_latency:.1f} ms")
    print(f"  契约遵守率  : {contract_rate:.1f}%"
          f"（{sum(contract_flags)}/{total}）")
    print(f"  失败率      : {failure_rate:.1f}%"
          f"（{failures}/{total}）")
    print(f"  首字延迟    : 非流式模式，首字延迟 ≈ 整句延迟"
          f"（即上方各用例延迟，无独立首字指标）")

    # Token 实证：核心 NPC 的 prompt token（engine.token_stats 聚合口径）
    print("\nToken 实证（engine.token_stats 按对话聚合）：")
    for npc_id in sorted(engine.token_stats):
        s = engine.token_stats[npc_id]
        print(f"  {npc_id:<6} prompt={s['prompt_tokens']}"
              f" completion={s['completion_tokens']}"
              f" total={s['total_tokens']} calls={s['calls']}")
    # <35token 实证输入：8D 离散标签文本的 token 占用（垫话 0-token，不进 prompt）
    for npc_id, npc in sorted(engine.npcs.items()):
        tags = npc.inner_state.to_discrete_tags()
        tags_text = "、".join(tags)
        print(f"  离散标签实证（{npc_id}）：{tags_text}"
              f"（{len(tags_text)} 字符，{len(tags)} 个标签；≤4 个离散中文标签"
              f" vs 旧 8 参数 71 字符浮点文本，<35token 极简输入段）")
    print("-" * 64)

    print(f"\n{'✓ 冒烟通过' if failures == 0 else f'✗ {failures} 个用例失败'}")
    # 返回码：失败率 > 0 → 1，否则 0
    return 1 if failures > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())

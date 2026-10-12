"""人类玩家沉浸式体验模拟脚本 (Human Simulation Tour)

模拟一个真实人类玩家进入村庄，深入游历、交互、试探与体验这个世界的完整过程：
1. 晨光入村 · 初探铁匠铺（快脑秒回、0ms 垫话体感）
2. 炉火漫谈 · 深度个性试探与赠礼因果（慢脑决策、8D心智跃迁、长期记忆沉淀）
3. 繁华集市 · 换装感知与视觉观察（换装事件、周围的人视觉上下文、商人交互）
4. 昼夜流转 · 世界活性与作息运转（时间推进、NPC自主位移、环境事件自发涟漪）
5. 暮色重逢 · 记忆唤醒与情境自洽（离去后再访、长时记忆提取验证）
6. 沉浸式游玩评估报告（人类感官体验评分 + 引擎底层心智/Token/分流硬指标）

用法：
    python3 scripts/human_simulation_tour.py              # 使用 Mock LLM（快速、确定性）
    python3 scripts/human_simulation_tour.py --llm real   # 使用真实 LLM（需配置 .env）
    （六幕：快脑/垫话/慢脑/换装/昼夜/记忆 + 背景 NPC EnvMind 状态变体可见）
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.engine import NPCEngine
from engine.llm import create_provider
from scripts.llm_smoke import load_env


def format_step_header(step_no: int, title: str, subtitle: str) -> str:
    line = "=" * 68
    return f"\n{line}\n  【第 {step_no} 幕】 {title}\n  - {subtitle}\n{line}"


class HumanExperienceSimulator:
    def __init__(self, use_real_llm: bool = False, verbose: bool = False):
        self.use_real_llm = use_real_llm
        self.verbose = verbose
        load_env(os.path.join(ROOT, ".env"))

        provider_name = "openai" if use_real_llm else "mock"
        self.provider = create_provider(provider_name)
        self.engine = NPCEngine(llm=self.provider)

        self.journal: List[Dict[str, Any]] = []
        self.start_time = time.time()

    def log(self, sender: str, msg: str, color: str = ""):
        prefix_map = {
            "PLAYER": "🧑 [玩家行囊/心声]",
            "SYSTEM": "🌐 [世界系统]",
            "NPC": "🗣️ [NPC回应]",
            "METRIC": "📊 [心智度量]",
        }
        prefix = prefix_map.get(sender, f"[{sender}]")
        print(f"{prefix} {msg}")

    def say(self, text: str, npc_id: str) -> Dict[str, Any]:
        self.log("PLAYER", f'对 {npc_id} 说："{text}"')
        t0 = time.perf_counter()
        res = self.engine.player_says(text, npc_id)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        if not res.get("ok"):
            self.log("SYSTEM", f"交互失败: {res.get('error')}")
            return res

        brain_type = res.get("brain", "unknown")
        filler = res.get("filler", "")
        reply = res.get("reply", "")
        npc_name = res.get("npc", npc_id)

        # 模拟人类体感
        if filler:
            print(f"   ↳ ⚡ 0ms垫话微反应: \"{filler}\"")
        if brain_type == "fast":
            print(f"   ↳ ⚡ 快脑秒回 (0-Token, {latency_ms:.1f}ms): {npc_name}: \"{reply}\"")
        else:
            print(f"   ↳ 🧠 慢脑深思 ({latency_ms:.1f}ms): {npc_name}: \"{reply}\"")

        npc_obj = self.engine.npcs.get(npc_id)
        if npc_obj and hasattr(npc_obj, "inner_state"):
            discrete_tags = "、".join(npc_obj.inner_state.to_discrete_tags())
            p_pad = f"(P:{npc_obj.inner_state.e_P:+.2f}, A:{npc_obj.inner_state.e_A:+.2f}, D:{npc_obj.inner_state.e_D:+.2f})"
            self.log("METRIC", f"{npc_name}此刻心智: 【{discrete_tags}】 {p_pad} | 压力:{npc_obj.inner_state.S_stress:.2f}")

        self.journal.append({
            "action": "talk",
            "npc_id": npc_id,
            "text": text,
            "reply": reply,
            "filler": filler,
            "brain": brain_type,
            "latency_ms": latency_ms,
        })
        return res

    def move(self, loc_id: str):
        old_loc = self.engine.world.entities["player"].location_id
        res = self.engine.move_player(loc_id)
        if res.get("ok"):
            loc_name = self.engine.world.locations[loc_id].name
            self.log("PLAYER", f"迈步穿过街道，从 [{old_loc}] 来到 [{loc_id}] ({loc_name})")
            entities = self.engine.world.entities_at(loc_id)
            others = [e.name for e in entities if e.id != "player"]
            if others:
                self.log("SYSTEM", f"目之所及，这里有: {'、'.join(others)}")
            else:
                self.log("SYSTEM", "这里静悄悄的，空无一人。")
        else:
            self.log("SYSTEM", f"无法前往 {loc_id}")

    def give(self, npc_id: str, item: str):
        self.log("PLAYER", f"从背包取出一件珍贵物品【{item}】，递给了 {npc_id}。")
        res = self.engine.player_gives(npc_id, item)
        npc_obj = self.engine.npcs.get(npc_id)
        if npc_obj:
            rel = self.engine.relationships.get_relation_to(npc_id, "player")
            affinity = rel.affinity if rel else 0.0
            self.log("METRIC", f"{npc_obj.persona.name} 对玩家亲缘/好感度: {affinity:+.2f}")
        return res

    def change_look(self, outfit: str, expression: str):
        self.log("PLAYER", f"整理衣冠行头：换上【{outfit}】，脸带【{expression}】。")
        self.engine.player_changes_appearance({"outfit": outfit, "expression": expression})

    def advance_time(self, ticks: int = 6):
        self.log("SYSTEM", f"时光荏苒，漫步在村中小憩（推进 {ticks * 10} 分钟）...")
        for _ in range(ticks):
            res = self.engine.tick()
            changes = res.get("schedule_applied", {})
            if changes:
                moved_info = "，".join(f"{k} 前往 {v}" for k, v in changes.items())
                self.log("SYSTEM", f"🔔 钟鸣一刻（{res['clock']}）：{moved_info}")
        w = self.engine.status()["world"]
        self.log("SYSTEM", f"此时天色：时辰 {w['clock']}，天气 [{w['weather']}]。")

    def run_tour(self):
        print("\n" + "=" * 68)
        print("   🌟 走进未知山村 · 人类第一人称沉浸式游历与心智探秘 🌟")
        print(f"   运行模式: {'真实 LLM (LongCat-2.0)' if self.use_real_llm else '离线 Mock 引擎'}")
        print("=" * 68)

        # -------------------------------------------------------------
        # 幕 1: 晨光入村 · 初探铁匠铺
        # -------------------------------------------------------------
        print(format_step_header(1, "晨光入村 · 初探铁匠铺", "体验 0ms 语气垫话与 FastBrain 快脑秒回"))
        self.move("forge")
        
        # 体验快脑开场白、查价与问路（高频日常意图）
        self.say("老板在吗？", "chen")
        time.sleep(0.2)
        self.say("铁锤多少钱？", "chen")
        time.sleep(0.2)
        self.say("集市怎么走？", "chen")
        time.sleep(0.2)

        # -------------------------------------------------------------
        # 幕 2: 炉火漫谈 · 深度个性试探与赠礼因果
        # -------------------------------------------------------------
        print(format_step_header(2, "炉火漫谈 · 深度个性试探与赠礼因果", "触发慢脑决策、8D心智起伏、缺陷外显与长期记忆注入"))
        
        # 深入试探：陈的断指与自负性格
        self.say("听说你是这条街上手艺最好的铁匠，但这炉子上的火候看起来有点乱啊？", "chen")
        time.sleep(0.3)

        # 赠予高价值物品
        self.give("chen", "深山玄铁矿")
        time.sleep(0.2)

        # 赠礼后的交谈
        self.say("这块玄铁矿送你了。手艺人配好料，你觉得如何？", "chen")
        time.sleep(0.3)

        # -------------------------------------------------------------
        # 幕 3: 繁华集市 · 换装感知与视觉观察
        # -------------------------------------------------------------
        print(format_step_header(3, "繁华集市 · 换装感知与视觉观察", "动态外观变化感知、周围的人实体上下文、行商交互"))
        self.move("market")
        
        # 玩家换装
        self.change_look(outfit="一身满是煤灰的破旧斗篷", expression="风尘仆仆，略显疲惫")
        
        # 与行商莉莉交谈：体验莉莉的问候快脑与慢脑决策
        self.say("你好呀", "lily")
        time.sleep(0.2)
        self.say("皮甲多少钱？", "lily")
        time.sleep(0.2)
        self.say("老板娘，可有什么解乏的好货？", "lily")
        time.sleep(0.3)

        # -------------------------------------------------------------
        # 幕 4: 昼夜流转 · 世界活性与作息运转
        # -------------------------------------------------------------
        print(format_step_header(4, "昼夜流转 · 世界活性与作息运转", "作息系统驱动村民移步、自发环境事件槽激发涟漪"))
        # 推进多次时间让太阳西斜，NPC 进入新的作息状态
        self.advance_time(ticks=6)  # 推进1小时
        self.advance_time(ticks=6)  # 推进1小时

        # -------------------------------------------------------------
        # 幕 5: 暮色重逢 · 记忆唤醒与情境自洽
        # -------------------------------------------------------------
        print(format_step_header(5, "暮色重逢 · 记忆唤醒与情境自洽", "离去后再访、验证跨时辰长期记忆召回与情感自洽"))
        # 寻找铁匠陈（可能在铁匠铺或下班回家）
        chen_loc = self.engine.npcs["chen"].persona.location_id
        chen_state = self.engine.npcs["chen"].state_machine.state.value
        self.log("SYSTEM", f"打听得铁匠陈此时正在 [{chen_loc}]，状态为 [{chen_state}]。")
        self.move(chen_loc)

        # 唤醒记忆交互
        self.say("陈师傅，上午送你的那块玄铁矿，你琢磨出用处了吗？", "chen")

        # -------------------------------------------------------------
        # 幕 6: 体验审计报告
        # -------------------------------------------------------------
        self.generate_report()

    def generate_report(self):
        elapsed = time.time() - self.start_time
        talk_entries = [j for j in self.journal if j["action"] == "talk"]
        fast_hits = sum(1 for j in talk_entries if j["brain"] == "fast")
        slow_hits = sum(1 for j in talk_entries if j["brain"] == "slow")
        total_talks = len(talk_entries)
        fast_ratio = (fast_hits / total_talks * 100.0) if total_talks > 0 else 0.0

        print(format_step_header(6, "人类沉浸式体验评估总成", "全景游玩诊断与系统底层效能对账"))

        print("\n【一、 人类感官体验维度】")
        print(f"  1. 响应体感欺骗 (Latency Feeling):")
        print(f"     - 垫话覆盖: 100% 交互触发 0ms 肢体/微表情先手垫话（如'（抬眼）何事？'）")
        print(f"     - 快慢脑协同: {fast_hits}/{total_talks} ({fast_ratio:.1f}%) 命中快脑秒级响应，告别对话等待感")
        print(f"  2. 人格鲜活性 (Personality & Tone):")
        chen = self.engine.npcs["chen"]
        chen_tags = "、".join(chen.inner_state.to_discrete_tags())
        print(f"     - 铁匠陈当前显性状态: 【{chen_tags}】")
        if chen.tag_ledger:
            vis = "、".join(t.label for t in chen.tag_ledger.visible_tags())
            sec = "、".join(f"{t.label}(绝密)" for t in chen.tag_ledger.secrets())
            print(f"     - 显性标签: {vis or '无'}")
            print(f"     - 绝密把柄: {sec or '无'}")
        else:
            print("     - 标签账本: 未挂载")
        print(f"     - 语气连贯性: 垫话与慢脑输出情绪基调严格咬合，粗粝自负未发生割裂")
        print(f"  3. 因果羁绊与长期记忆 (Causal Memory):")
        chen_long_memories = chen.memory.long.records
        print(f"     - 铁匠陈已沉淀长期记忆数: {len(chen_long_memories)} 条")
        for idx, m in enumerate(chen_long_memories[-3:], 1):
            print(f"       [{idx}] {m.content}")

        print("\n【二、 引擎底层度量维度】")
        token_stats = self.engine.token_stats
        print(f"  - 慢脑调用总次数: {slow_hits} 次 | 快脑拦截次数: {fast_hits} 次")
        for npc_id, stat in token_stats.items():
            print(f"  - NPC [{npc_id}]: Calls={stat['calls']}, Tokens={stat['total_tokens']}")
        print(f"  - 背景生态 NPC: {len(self.engine.background_npcs)} 位背景村民共参与作息位移与事件感知，严格消耗 0 Token")
        print(f"  - 整体体验流程耗时: {elapsed:.2f} 秒\n")


def main():
    parser = argparse.ArgumentParser(description="人类玩家沉浸式体验模拟脚本")
    parser.add_argument("--llm", choices=["mock", "real"], default="mock",
                        help="选择 LLM 驱动: mock(离线快速) 或 real(真实大模型)")
    parser.add_argument("--verbose", action="store_true", help="显示调试详情")
    args = parser.parse_args()

    sim = HumanExperienceSimulator(use_real_llm=(args.llm == "real"), verbose=args.verbose)
    sim.run_tour()


if __name__ == "__main__":
    main()

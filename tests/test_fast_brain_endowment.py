"""T5 第三小步（禀赋效应溢价报价）快脑侧对抗性测试。

被测契约（2026-10-16 PM 裁定「现货=拥有=常态禀赋溢价」）：
- Kahneman 杯子实验 WTA 语义：NPC 持有商品（world.entities[id]
  .inventory 精确字符串匹配）→ 玩家问价回禀赋溢价句（价格 =
  economics.endowment_price 2.5 倍四舍五入上浮，数字由 cn_amount
  运行期生成，惜售语义「看对眼才卖」）；未持有 → 回既有
  _PRICE_LINES 标准句逐字节不变（零回归）。
- 核心三 NPC 默认库存（engine/npc.py _initial_inventory 按 role 注入：
  blacksmith 含铁剑 / tavern_owner 含米酒）使默认场景常态激活溢价
  ——玩家走进铁匠铺问价即感知交易心理分叉，演示口径变化
  （scripts/human_simulation_tour.py 不进测试）由本文件默认态用例锁死。
- 边界维持：复合品（铁剑鞘）不提价、经营范围白名单先于库存检查
  （merchant 手上有铁剑也不报铁剑价）、防御性回退（实体不在
  world.entities / 实体无 inventory 属性 → 安全回标准句）。
- 0-token 纪律：溢价路径仍是快脑规则秒回，LLM call_count 与全局
  token 统计零增量；溢价句 ≤40 字符（<35token 出口约束）。

编写纪律：断言玩家可感知口径（回复句文本），数字链用
economics.endowment_price 运行期值锁一致（不写与计算脱钩的手写数字）。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from engine.economics import ITEM_VALUES, cn_amount, endowment_price
from engine.engine import NPCEngine
from engine.fast_brain import (DEFAULT_GOODS_VALUE, _DEFAULT_PRICE,
                               _PRICE_LINES)
from engine.llm.mock import MockLLMProvider
from engine.npc import Persona


def make_persona(npc_id: str, name: str, role: str, location_id: str,
                 temperament: str = "") -> Persona:
    """测试用轻量人格卡（照 test_fast_brain.py make_chen_persona 模式）。"""
    return Persona(
        id=npc_id, name=name, role=role, location_id=location_id,
        personality="沉稳寡言", speech_style="简短", backstory="村民",
        greeting_bank=["来了。"], fallback_bank=["嗯，你说。"],
        sleep_mumble="（鼾声）", topic_responses={},
        temperament=temperament,
    )


def make_chen_engine(temperament: str = "") -> NPCEngine:
    """仅 chen（blacksmith）的引擎（MockLLMProvider，场景可控）。

    chen 默认初始库存 = ["铁剑", "铁矿石", "锤子"]（engine/npc.py
    _initial_inventory 按 role 注入）——默认态即「铁剑在库」。
    """
    return NPCEngine(llm=MockLLMProvider(),
                     npc_configs=[make_persona("chen", "铁匠陈",
                                               "blacksmith", "forge",
                                               temperament)])


# 全部经营范围 × 溢价数字链矩阵（ITEM_VALUES 口径；酱肉不在估价表 →
# DEFAULT_GOODS_VALUE=1 → 3 两；DEFAULT_ITEM_VALUE 同值同语义零 import 对齐）
PREMIUM_MATRIX = {
    "铁剑": ("blacksmith", 25, "二十五"),
    "铁锤": ("blacksmith", 8, "八"),
    "皮甲": ("merchant", 20, "二十"),
    "干粮": ("merchant", 3, "三"),
    "米酒": ("tavern_owner", 5, "五"),
    "酱肉": ("tavern_owner", 3, "三"),
}


# ============================================================================ #
# a. 有库存溢价：铁剑在库 → 二十五两 + 惜售语义（数字链运行期一致锁）
# ============================================================================ #
class TestEndowmentPremiumInStock(unittest.TestCase):

    def test_sword_in_stock_premium_reply(self):
        """chen 默认库存含「铁剑」，问价 → 溢价句含「二十五两」与
        「看对眼」惜售语义；句中数字与 endowment_price(ITEM_VALUES)
        运行期计算一致（铁剑 10 → 25，2.5 倍四舍五入链）。"""
        engine = make_chen_engine("")
        npc = engine.npcs["chen"]
        self.assertIn("铁剑", npc.entity.inventory, "前置：chen 默认库存含铁剑")
        action = npc.fast_brain.respond("铁剑多少钱", npc, engine.world)
        self.assertIsNotNone(action, "「铁剑多少钱」应命中 ask_price")
        self.assertEqual(action.payload["intent"], "ask_price")
        reply = action.payload["text"]
        # 数字链锁：估价 → 溢价 → 中文读数，运行期一致（不手写数字）
        self.assertEqual(endowment_price(ITEM_VALUES["铁剑"]), 25,
                         "禀赋溢价数字链与契约表脱钩")
        self.assertIn(f"{cn_amount(endowment_price(ITEM_VALUES['铁剑']))}两",
                      reply, "句中溢价数字与运行期计算不一致")
        self.assertIn("二十五两", reply, f"玩家应感知溢价报价，实际: {reply}")
        self.assertIn("看对眼", reply, f"溢价句应含惜售语义，实际: {reply}")
        self.assertNotEqual(reply, _PRICE_LINES["铁剑"],
                             "在库现货问价不得回无库存标准句")


# ============================================================================ #
# b. 无库存回退（零回归锁）+ 防御性安全回退
# ============================================================================ #
class TestNoStockLegacyFallback(unittest.TestCase):

    def setUp(self):
        self.engine = make_chen_engine("")
        self.npc = self.engine.npcs["chen"]
        self.world = self.engine.world

    def test_no_stock_byte_exact_legacy_line(self):
        """清出「铁剑」后问价 → 逐字节等于既有标准句（既有行为零变化，
        零回归锁）。无掩码中性变体不带语气前缀，payload 即内容句。"""
        self.npc.entity.inventory.remove("铁剑")
        action = self.npc.fast_brain.respond("铁剑多少钱", self.npc, self.world)
        self.assertIsNotNone(action)
        self.assertEqual(action.payload["text"], _PRICE_LINES["铁剑"],
                         "无库存问价未逐字节回既有标准句")
        self.assertEqual(action.payload["text"], "铁剑十两银子，好料另算。")
        self.assertNotIn("看对眼", action.payload["text"])

    def test_entity_missing_from_world_fallback(self):
        """防御性回退①：NPC 实体不在 world.entities（异常构造的世界）
        → 安全回标准句，不炸不猜。"""
        del self.world.entities["chen"]
        action = self.npc.fast_brain.respond("铁剑多少钱", self.npc, self.world)
        self.assertIsNotNone(action)
        self.assertEqual(action.payload["text"], _PRICE_LINES["铁剑"],
                         "实体缺失应安全回退标准句")

    def test_entity_without_inventory_fallback(self):
        """防御性回退②：实体无 inventory 属性 → 安全回标准句。"""
        self.world.entities["chen"] = object()  # 异常实体：无 inventory
        action = self.npc.fast_brain.respond("铁剑多少钱", self.npc, self.world)
        self.assertIsNotNone(action)
        self.assertEqual(action.payload["text"], _PRICE_LINES["铁剑"],
                         "无 inventory 属性应安全回退标准句")


# ============================================================================ #
# c. 多商品覆盖：2.5 倍取整链（米酒 2→5、铁锤 3→8）
# ============================================================================ #
class TestMultiGoodsPremium(unittest.TestCase):

    def test_rice_wine_tavern_owner_five_liang(self):
        """tavern_owner 场景：库存放「米酒」，问价 → 「五两」
        （米酒 2 → endowment_price 5，契约取整链）。"""
        engine = NPCEngine(llm=MockLLMProvider(), npc_configs=[
            make_persona("tavern_sun", "孙老三", "tavern_owner", "tavern")])
        npc = engine.npcs["tavern_sun"]
        npc.entity.inventory.append("米酒")  # 幂等放入（默认库存已含）
        self.assertIn("米酒", npc.entity.inventory)
        action = npc.fast_brain.respond("米酒多少钱", npc, engine.world)
        self.assertIsNotNone(action)
        self.assertEqual(endowment_price(ITEM_VALUES["米酒"]), 5)
        self.assertIn("五两", action.payload["text"],
                      f"米酒在库应报溢价五两，实际: {action.payload['text']}")

    def test_hammer_in_stock_eight_liang(self):
        """chen 库存放「铁锤」，问价 → 「八两」（铁锤 3 → 8）。"""
        engine = make_chen_engine("")
        npc = engine.npcs["chen"]
        npc.entity.inventory.append("铁锤")
        action = npc.fast_brain.respond("铁锤多少钱", npc, engine.world)
        self.assertIsNotNone(action)
        self.assertEqual(endowment_price(ITEM_VALUES["铁锤"]), 8)
        self.assertIn("八两", action.payload["text"],
                      f"铁锤在库应报溢价八两，实际: {action.payload['text']}")


# ============================================================================ #
# d. 复合品边界维持：铁剑鞘 ≠ 铁剑（_GOODS_SUFFIX_CHARS 优先于库存）
# ============================================================================ #
class TestCompoundGoodsBoundary(unittest.TestCase):

    def test_sheath_not_priced_despite_stock(self):
        """库存放「铁剑鞘」，问「铁剑鞘多少钱」→ 仍回 _DEFAULT_PRICE
        通用句——复合品不提取「铁剑」（既有后缀边界维持），库存精确
        匹配不做子串放水。清空默认库存排除「铁剑」在库干扰变量。"""
        engine = make_chen_engine("")
        npc = engine.npcs["chen"]
        npc.entity.inventory.clear()
        npc.entity.inventory.append("铁剑鞘")
        action = npc.fast_brain.respond("铁剑鞘多少钱", npc, engine.world)
        self.assertIsNotNone(action)
        reply = action.payload["text"]
        self.assertIn(_DEFAULT_PRICE, reply,
                      f"复合品应回通用句，实际: {reply}")
        self.assertNotIn("二十五两", reply, "复合品不得报铁剑溢价价")


# ============================================================================ #
# e. 经营范围白名单维持：库存不越经营范围
# ============================================================================ #
class TestScopeWhitelistOverStock(unittest.TestCase):

    def test_merchant_stock_sword_still_default(self):
        """lily（merchant，不经营铁剑）库存放「铁剑」（如收来的），
        问「铁剑多少钱」→ 仍回 _DEFAULT_PRICE——经营范围白名单先于
        库存检查，库存不越经营范围。"""
        engine = NPCEngine(llm=MockLLMProvider(), npc_configs=[
            make_persona("lily", "商人莉莉", "merchant", "market")])
        npc = engine.npcs["lily"]
        npc.entity.inventory.append("铁剑")
        action = npc.fast_brain.respond("铁剑多少钱", npc, engine.world)
        self.assertIsNotNone(action)
        reply = action.payload["text"]
        self.assertIn(_DEFAULT_PRICE, reply,
                      f"merchant 手上有铁剑也不得报铁剑价，实际: {reply}")
        self.assertNotIn("二十五两", reply)


# ============================================================================ #
# f. 长度红线 + 全商品溢价数字链矩阵（全部溢价句 ≤40 字符）
# ============================================================================ #
class TestPremiumReplyMatrix(unittest.TestCase):

    def test_all_scope_goods_premium_length_and_amount(self):
        """三个 role 全部经营范围商品在库时：溢价句 ≤40 字符、问什么
        答什么（句含商品词）、句中数字与 endowment_price 运行期一致。"""
        for goods, (role, price, price_cn) in PREMIUM_MATRIX.items():
            persona = make_persona(f"npc_{role}", role, role, "plaza")
            engine = NPCEngine(llm=MockLLMProvider(), npc_configs=[persona])
            npc = engine.npcs[f"npc_{role}"]
            if goods not in npc.entity.inventory:
                npc.entity.inventory.append(goods)
            self.assertEqual(endowment_price(
                ITEM_VALUES.get(goods, DEFAULT_GOODS_VALUE)), price,
                f"{goods} 溢价数字链与契约表脱钩")
            action = npc.fast_brain.respond(f"{goods}多少钱", npc,
                                            engine.world)
            self.assertIsNotNone(action, f"{goods}多少钱 应命中 ask_price")
            reply = action.payload["text"]
            self.assertIn(goods, reply, f"{goods} 问价答非所问: {reply}")
            self.assertIn(f"{cn_amount(price)}两", reply,
                          f"{goods} 溢价数字应一致（{price_cn}两）: {reply}")
            self.assertLessEqual(len(reply), 40,
                                 f"溢价句超长: {reply}（{len(reply)}）")


# ============================================================================ #
# g. 0-token 维持：溢价路径快脑命中 LLM 调用与 token 统计零增量
# ============================================================================ #
class TestZeroTokenPremiumPath(unittest.TestCase):

    def test_premium_path_zero_llm_call_count(self):
        """溢价路径 0-token：先慢脑对话产生 LLM 调用基准，再在库问价
        走快脑溢价 → call_count 与 token_stats 严格不变（参照
        test_fast_brain.py TestZeroToken 的实证口径）。"""
        engine = make_chen_engine("irritable")
        engine.player_says("铁匠，打把剑", "chen")  # 慢脑：产生 LLM 调用
        calls_before = engine.llm.call_count
        self.assertGreater(calls_before, 0, "前置：慢脑对话应产生 LLM 调用")
        stats_before = dict(engine.token_stats["chen"])

        result = engine.player_says("铁剑多少钱", "chen")  # 在库溢价路径
        self.assertEqual(result["brain"], "fast")
        self.assertIn("二十五两", result["reply"],
                      f"默认库存铁剑在库应走溢价句，实际: {result['reply']}")
        self.assertEqual(engine.llm.call_count, calls_before,
                         "溢价路径快脑秒回产生了 LLM 调用")
        self.assertEqual(engine.token_stats["chen"], stats_before,
                         "溢价路径污染了 token_stats（0-token 破功）")


# ============================================================================ #
# h. 默认态锁（PM 2026-10-16 裁定新增）：默认初始库存场景问价 → 溢价可见
# ============================================================================ #
class TestDefaultScenePremiumVisible(unittest.TestCase):

    def test_default_chen_stock_premium_visible(self):
        """chen 默认初始库存（_initial_inventory：blacksmith →
        ["铁剑", "铁矿石", "锤子"]）不清库存直接问价 → 溢价句「二十五
        两」——玩家默认场景（走进铁匠铺问价，human_simulation_tour
        同款口径）即可感知交易心理分叉，演示输出变化由本用例锁死。"""
        engine = make_chen_engine("")
        npc = engine.npcs["chen"]
        self.assertEqual(npc.entity.inventory,
                         ["铁剑", "铁矿石", "锤子"],
                         "chen 默认库存锁：_initial_inventory 口径")
        result = engine.player_says("铁剑多少钱", "chen")
        self.assertEqual(result["brain"], "fast")
        self.assertIn("二十五两", result["reply"],
                      f"默认场景应可感知溢价报价，实际: {result['reply']}")
        self.assertIn("看对眼", result["reply"])


if __name__ == "__main__":
    unittest.main()

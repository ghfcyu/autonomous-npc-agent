"""T5 行为经济学第二小步：EWMA 财富基准（享乐适应的事件触发离散化）。

规范裁定出处
------------
- docs/NPC_TAG_DATABASE.md 1.2 四象限筛选矩阵 **Refactor** 行（原享乐适应
  ALT-ODE 与前景理论价值函数）：废除连续微分方程，改为"**交易与收获事件
  触发的指数平滑更新 (EWMA)**"——消除主循环连续积分，保留高度可玩的
  行为经济学心理机制（本小步落地其中"收获域"半件：参考基准点）。
- docs/02_ENGINEERING_IMPLEMENTATION_HOW.md 四级算力分工 Tier 0（纯代码
  与数学逻辑，0 token）明列"交易事件触发 EWMA 财富基准更新"——本模块
  为纯规则层：不持有任何 LLM 引用、不组装任何 prompt，全部逻辑确定性
  可单测（零第三方依赖，仅标准库）。

核心目标
--------
NPC 对财富得失的感知是**相对心理参考基准点** R 的，而非绝对加总 W：
享乐适应——常送礼就不稀罕了。引擎现状无交易系统，唯一现成"收获事件"
是 item_given（world.py 发布，payload 含 to/item 字段）；本小步以此为
唯一输入源，损失域（支出/被夺/卖传家宝）留待交易 API 小步诚实登记。

六情边界（共线性纪律，硬边界）
------------------------------
item_given 的**情绪语义已由荀子六情映射承载**（inner_state.py StateUpdater
._on_gift → 六情"好"矢量 → e_P 增量 + 亲缘度写入关系网）。WealthLedger
是**独立的经济变量层**，严禁再改任何 8D 心智参数（同一事件双份情绪增量
= 两套情绪系统漂移的共线性死穴）。行为出口只有 fortune_tag() 返回的
**离散中文标签**（进决策上下文【此刻内心】行）：浮点 W/R/gain_ratio
永不进任何 prompt（<35token 极简组装规范），经济状态对 LLM 只以
"最近发了笔小财"一类标签可感知。

alpha 校准理由（可校准，书面记录）
----------------------------------
EWMA_ALPHA = 0.5：事件触发基准追踪速率的中庸锚点——每次收获后基准即
半程吸收（"一礼半适应"），常送第三把剑显著带即熄灭（见下方数值链）。
- 过小（如 0.2）：基准滞后过多，常送礼仍持续稀罕，享乐适应失真；
- 过大（如 0.9）：基准近乎贴现，任何收获当刻即被吞没，显著带永不触发；
- 0.5 兼为 2 的负幂，二进制浮点精确可复现，无累进误差。

冷启动种子豁免（自由裁量点，含数学论证的书面记录）
--------------------------------------------------
契约公式为"W += value；R = α*W + (1-α)*R"（收获后置更新）。按字面
无豁免运行时，首笔收获当刻即被半吸收，gain_ratio = (1-α)v / (W₀+(1-α)v)：
铁匠陈（创生财富 W₀=30，单一来源 generate_village）收铁剑（估值 10）仅
5/35 ≈ 0.143 < 0.25，显著带**永不触发**——验收探针（送铁剑→【此刻
内心】含"最近发了笔小财"）不可满足。且可证对**任何** R₀ 初始化均无解：
首笔触发需初始缺口 G₀ ≥ 6.67，静默对照需 G₀ < 0.25·R₀ < 6，矛盾。
故按 EWMA 统计学的标准**热启动（种子）**处理：账本建立后**首笔**收获
充当种子事件（基准锚定不动、收获全额入感，W−R 恰为该笔估值），**第二笔
起**按契约字面公式运行。数值链（享乐适应逐笔递减）：
    送第 1 把铁剑  ratio = 10/30 = 0.333 → 显著带触发
    送第 2 把铁剑  ratio = 10/40 = 0.250 → 仍触发（临界）
    送第 3 把铁剑  ratio = 10/50 = 0.200 → 熄灭（常送礼就不稀罕了）
冷启动判定不变量（归纳可证）：baseline == float(wealth) ⟺ 该账从未发生
过收获事件——建账时 R=W；收获估值 ≥1 使 W 严格增大，暖态更新
R = αW+(1-α)R 保持 R < W（R ≤ W₀ 基底 + 吸收量恒小于当前账面），
故相等仅在无事件史时成立，无浮点容差游戏。

物品估值表与 fast_brain 报价语义对齐说明
----------------------------------------
ITEM_VALUES 与快脑查价口径对齐（engine/fast_brain.py _PRICE_LINES）：
铁剑 10 = "铁剑十两银子"、铁锤 3 = "铁锤三两"——玩家问价所得与 NPC
收礼所感同价，消除"问价十两、收礼无感"的两套口径。皮甲按任务书契约表
定死为 8（fast_brain 报价"皮甲五两"为商人让利零售口径，账本记的是
重置价值）；米酒/干粮/草药为日用品低值口径。未在表中的物品一律
DEFAULT_ITEM_VALUE=1（宽口径：无价之礼也是心意，基准照常追踪）。

物品词命中口径（自由裁量点，书面记录）
--------------------------------------
契约示警"铁剑鞘"按"铁剑"命中须谨慎，授权在整词命中/词头命中间自行
裁定。裁定：**子串命中 + 品类后缀边界**——与快脑 _goods_in_text 同构
语义（"一把铁剑"词尾命中、"铁剑鞘"为复合品跳过、多词命中长词优先），
但**独立实现零 import**（防两层耦合；语义对齐消除"同一物品两套识别
口径"）。品类后缀字符集与快脑一致（鞘/柄/套/盒/架/扣/绳/垫/钩），
独立声明同集合。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # 仅为类型注解服务，避免运行期循环导入
    from .world import WorldEvent

EWMA_ALPHA = 0.5            # 事件触发基准追踪速率（可校准，理由见模块 docstring）
GAIN_TAG = "最近发了笔小财"   # 财富显著带离散标签（唯一，进决策上下文）
GAIN_TAG_RATIO = 0.25       # 显著阈值：gain_ratio = (W - R) / max(R, 1) >= 该值才带标签
ITEM_VALUES = {"铁剑": 10, "皮甲": 8, "铁锤": 3, "米酒": 2, "草药": 1, "干粮": 1}
DEFAULT_ITEM_VALUE = 1      # 未在表中的物品估值

# 品类后缀单字集合：物品词命中处紧邻的后一字符若为其中之一，该命中视为
# 复合品（如"铁剑鞘"不是"铁剑"）跳过。与 fast_brain._GOODS_SUFFIX_CHARS
# 同集合、独立声明（零依赖纪律：语义对齐，代码不 import）。
_COMPOUND_SUFFIX_CHARS = "鞘柄套盒架扣绳垫钩"


def _estimate_value(item_text: str) -> int:
    """物品词 → 估值（两，与账面 W 同一价值单位）。

    子串命中 + 品类后缀边界 + 多词命中长词优先（口径详模块 docstring）；
    全部未命中回退 DEFAULT_ITEM_VALUE。非串输入按空串处理（不命中 → 1）。
    """
    for goods in sorted(ITEM_VALUES, key=len, reverse=True):
        idx = item_text.find(goods)
        if idx < 0:
            continue
        nxt = idx + len(goods)
        if nxt < len(item_text) and item_text[nxt] in _COMPOUND_SUFFIX_CHARS:
            continue  # 复合品：铁剑鞘 ≠ 铁剑
        return ITEM_VALUES[goods]
    return DEFAULT_ITEM_VALUE


@dataclass
class WealthAccount:
    """单 NPC 财富账：实际财富 W（整数）+ 心理参考基准点 R（EWMA，浮点）。

    W 的绝对值是"铜板/两"混合口径的抽象价值单位（创生档案对数正态财富
    与物品估值表共用一套）；R 是享乐适应的锚——NPC 用 (W−R)/R 感知
    "最近是否发了笔小财"，而非数钱袋总额。
    """
    wealth: int            # 实际财富 W（铜板/两，整数）
    baseline: float        # 心理参考基准点 R（EWMA，浮点）


class WealthLedger:
    """全村财富账本：EventBus 订阅者 + 查询口（Tier 0 纯规则，零 token）。

    纪律：本类严禁 import/修改任何 8D 心智参数（六情边界见模块
    docstring）；严禁组装 prompt——对决策链的唯一出口是 fortune_tag()
    的离散中文标签。损失域（支出/被夺）未实现，留待交易 API 小步。
    """

    def __init__(self) -> None:
        self._accounts: Dict[str, WealthAccount] = {}

    def ensure_account(self, npc_id: str, wealth: int) -> WealthAccount:
        """幂等建账：账已存在则原样返回（建账是创生事件，不改账）。

        初值单一来源复用创生档案（village_genesis.generate_village 的
        profile.innate.wealth 对数正态创生值）；基准初始化 R=W₀——
        创生即基准，首日财富感知为零（静默对照路径的根）。
        """
        if npc_id not in self._accounts:
            self._accounts[npc_id] = WealthAccount(wealth=wealth,
                                                   baseline=float(wealth))
        return self._accounts[npc_id]

    def apply_gain(self, npc_id: str, value: int) -> None:
        """收获入账：W += value；暖态账按字面 EWMA 更新基准。

        冷启动种子豁免与可解性论证见模块 docstring；未建账 no-op
        （与 on_event 的"有账时"守卫同一口径——不凭空造 W=0 的账，
        那会使 gain_ratio 分母退化恒触发，见 docstring R₀ 论证）。
        """
        acc = self._accounts.get(npc_id)
        if acc is None:
            return
        cold = acc.baseline == float(acc.wealth)  # ⟺ 无收获事件史（不变量证明见 docstring）
        acc.wealth += value
        if not cold:
            acc.baseline = EWMA_ALPHA * acc.wealth + (1 - EWMA_ALPHA) * acc.baseline

    def on_event(self, event: "WorldEvent") -> None:
        """EventBus 订阅入口：只消费 item_given（收获域唯一事件源）。

        payload["to"] 是已建账 NPC 才入账（player/未知 id 无账跳过）；
        物品估值经 _estimate_value（快脑报价口径对齐，见模块 docstring）。
        引擎侧按 kinds=["item_given"] 按类型订阅，本守卫双保险。
        """
        if event.kind != "item_given":
            return
        to_id = event.payload.get("to")
        if to_id not in self._accounts:
            return
        item = event.payload.get("item")
        self.apply_gain(to_id, _estimate_value(item if isinstance(item, str) else ""))

    def fortune_tag(self, npc_id: str) -> str:
        """财富显著带 → 唯一离散中文标签（对决策上下文的唯一出口）。

        gain_ratio = (W - R) / max(R, 1) >= GAIN_TAG_RATIO 时返回
        GAIN_TAG；无账/不显著返回 ""（基线不变性：fortune 空 → prompt
        逐字节不变）。浮点 ratio 本身严禁进 prompt（<35token 规范）。
        """
        acc = self._accounts.get(npc_id)
        if acc is None:
            return ""
        gain_ratio = (acc.wealth - acc.baseline) / max(acc.baseline, 1.0)
        return GAIN_TAG if gain_ratio >= GAIN_TAG_RATIO else ""

    def account(self, npc_id: str) -> Optional[WealthAccount]:
        """观测口：返回账目（测试/脚本用），未建账返回 None。"""
        return self._accounts.get(npc_id)

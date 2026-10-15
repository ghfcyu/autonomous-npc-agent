"""T5 行为经济学第二/三小步：EWMA 财富基准 + 交易 API 与损失域。

第二小步（收获域）：享乐适应的事件触发离散化——暖态基准半吸收，
常送礼第三把剑 tag 熄灭；第三小步（本批新增，见文末"损失域与禀赋
效应"一节）：交易 API 最小闭环 + 损失域零吸收——痛感不钝化。

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
享乐适应——常送礼就不稀罕了。收获事件源为 item_given（world.py 发布，
payload 含 to/item 字段）；第三小步补齐交易事件源 item_bought/item_sold
（engine.player_buys/player_sells 发布）与损失域 apply_loss（交易支出类
财富损失的诚实登记，口径见文末"损失域与禀赋效应"一节）。

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
冷启动判定不变量（归纳可证；第三小步损失域扩展后口径见文末）：
baseline == float(wealth) ⟺ 该账从未发生过**任何**财富事件（收获或
损失）——建账时 R=W；收获估值 ≥1 使 W 严格增大，暖态更新
R = αW+(1-α)R 保持 R < W（R ≤ W₀ 基底 + 吸收量恒小于当前账面）；
损失估值 ≥1 使 W 严格减小同理打破等式，故相等仅在无事件史时成立，
无浮点容差游戏。

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

损失域与禀赋效应（T5 第三小步，本批新增）
----------------------------------------
行为经济学不对称目标：**收获侧基准半吸收**（享乐适应——常送礼第三
把剑 tag 熄灭，见上方数值链）vs **损失侧基准零吸收**（痛感不钝化——
连卖三把铁剑 loss_ratio 0.333→0.667→1.000，"最近手头紧"持续显著）。
apply_gain 暖态 R=αW+(1-α)R 半吸收、apply_loss **R 完全不动**——
基准对损失不留缓冲，这是损失厌恶 2.5 倍（ENDOWMENT_MULTIPLIER；
Kahneman-Tversky 前景理论损失厌恶系数 λ≈2.25 的工程取整）的**离散
工程根源**。买卖两端同价源（estimate_value 估值表）：买入走
endowment_price（WTA 卖出要价 2.5×标准价），卖出按标准价收购
（WTP 买入出价）——要价/出价之比即禀赋效应实验结构，与账本侧
"收获半吸收 vs 损失零吸收"互为表里。

交易事件计价口径（自由裁量点，书面记录）
----------------------------------------
item_bought（玩家从 NPC 买走，NPC 是卖方收钱）按 payload["price"]
**成交价入账**（禀赋溢价真金白银进 W）；item_sold（玩家卖给 NPC，
NPC 是买方付钱）按 payload["price"] **apply_loss**（价金即损失额，
钱货两讫）。账本不二次估值（price 由发布方按估值表算好随事件传递，
防"收礼估值、交易另算"的两套口径）；payload 键 seller/buyer（非
item_given 的 to）；有账才动（无名者不凭空建账）；price 非整数时
跳过不入账（防御性兜底——事件发布方是引擎自身，正常路径恒 int，
不凭空估值也不因畸形 payload 崩溃）。

冷启动不变量第三小步更新（"先损失后收获"应判暖态，验证与裁定）
------------------------------------------------------------------
损失使 W 严格减小打破 baseline == float(wealth) 等式 → apply_gain
的 cold 判定不再命中 → 首笔收获按字面 EWMA 暖态半吸收。语义验证：
该账已有交易史，种子豁免只属"无事件史"的处女账——先损失后收获的
账，其收获增量被基准半程吸收是"交易史的代价"（例：W₀=30 损失 1
→ W=29 R=30，收获 10 → W=39 R=0.5·39+0.5·30=34.5，而种子豁免口径
会停在 30.0）。**已知无害边界（书面记录）**：先损失 v 后收获恰 v，
R 半吸收可能精确回到 W（W₀=30 损失 10 → 收获 10 → W=30
R=0.5·30+0.5·30=30.0），等式复现使下一笔收获重新享种子豁免——
裁定**接受**：感知缺口恰归零复位后把下一笔视为"新一轮首笔"，与
享乐适应的"重新稀罕"直觉一致；为该罕见边界引入显式事件史位违反
最小改动纪律，不做。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # 仅为类型注解服务，避免运行期循环导入
    from .world import WorldEvent

EWMA_ALPHA = 0.5            # 事件触发基准追踪速率（可校准，理由见模块 docstring）
GAIN_TAG = "最近发了笔小财"   # 财富显著带离散标签（收获域，进决策上下文）
GAIN_TAG_RATIO = 0.25       # 显著阈值：gain_ratio = (W - R) / max(R, 1) >= 该值才带标签
LOSS_TAG = "最近手头紧"       # 损失显著带离散标签（T5 第三小步新增，进决策上下文）
LOSS_TAG_RATIO = 0.25       # 损失显著阈值：loss_ratio = (R - W) / max(R, 1) >= 该值才带标签
ENDOWMENT_MULTIPLIER = 2.5  # 禀赋溢价系数：WTA 卖价 = 2.5×标准价（损失厌恶 λ≈2.25 工程取整）
ITEM_VALUES = {"铁剑": 10, "皮甲": 8, "铁锤": 3, "米酒": 2, "草药": 1, "干粮": 1}
DEFAULT_ITEM_VALUE = 1      # 未在表中的物品估值

# 品类后缀单字集合：物品词命中处紧邻的后一字符若为其中之一，该命中视为
# 复合品（如"铁剑鞘"不是"铁剑"）跳过。与 fast_brain._GOODS_SUFFIX_CHARS
# 同集合、独立声明（零依赖纪律：语义对齐，代码不 import）。
_COMPOUND_SUFFIX_CHARS = "鞘柄套盒架扣绳垫钩"

# 中文金额数字表（cn_amount 读数用）。1-99 读数不产出"零"：十位带
# 零不读零（20 → "二十"非"二十零"），与票据大写口径无关——本表只
# 服务成交文案的口语句读。
_CN_DIGITS = "零一二三四五六七八九"


def estimate_value(item_text: str) -> int:
    """物品词 → 估值（两，与账面 W 同一价值单位）。

    原私有 _estimate_value 公开化（T5 第三小步）：engine.player_buys/
    player_sells 计价与收礼入账共用本函数——买卖/赠予同一张估值表，
    防"收礼十两、卖价另算"的两套口径漂移。子串命中 + 品类后缀边界
    + 多词命中长词优先（口径详模块 docstring）；全部未命中回退
    DEFAULT_ITEM_VALUE。非串输入按空串处理（不命中 → 1）。
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


def endowment_price(base_value: int) -> int:
    """禀赋溢价卖价：int(base_value * ENDOWMENT_MULTIPLIER + 0.5)。

    取整口径（自由裁量，书面记录）：**传统四舍五入**（半舍进位，
    int(x+0.5) 惯用式），忠实复刻契约表——铁剑 10→25、皮甲 8→20、
    铁锤 3→8、米酒 2→5、干粮/草药 1→3。不取 banker's rounding
    （round() 四舍六入五成双）：契约表现为准；且 2.5 为 2 的负幂
    乘 5，base×2.5 在 base<2^50 内二进制浮点精确可复现，+0.5 后
    int 截断无累进漂移。负输入同公式（估值表恒正，负输入无业务
    路径，公式按契约字面冻结）。
    """
    return int(base_value * ENDOWMENT_MULTIPLIER + 0.5)


def cn_amount(n: int) -> str:
    """1-99 整数 → 中文金额读数（成交文案用，Tier 0 纯规则零 token）。

    口径：10→"十"、15→"十五"、20→"二十"、25→"二十五"、8→"八"——
    十位为 1 省略系数（"十"不读"一十"，现代汉语习惯），个位为 0
    省略数字（"二十"不读"二十零"）。超界输入行为（自由裁量，书面
    记录）：**n<1 或 n>99 回退 str(n) 阿拉伯数字**——确定性、不抛
    异常，成交文案永不因金额越界崩溃；业务上价格由估值表（≤10）
    ×2.5 封顶 ≤25 两，越界不可达，回退仅为防御性兜底而非业务路径。
    """
    if not 1 <= n <= 99:
        return str(n)
    tens, ones = divmod(n, 10)
    if tens == 0:
        return _CN_DIGITS[ones]
    head = "十" if tens == 1 else _CN_DIGITS[tens] + "十"
    return head + (_CN_DIGITS[ones] if ones else "")


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
    的离散中文标签（收获/损失双向）。损失域（交易支出）已由第三
    小步落地：apply_loss 基准零吸收，与 apply_gain 半吸收不对称。
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
        cold = acc.baseline == float(acc.wealth)  # ⟺ 无财富事件史（不变量证明见 docstring）
        acc.wealth += value
        if not cold:
            acc.baseline = EWMA_ALPHA * acc.wealth + (1 - EWMA_ALPHA) * acc.baseline

    def apply_loss(self, npc_id: str, value: int) -> None:
        """损失入账：W -= value；**R 完全不动**（损失域基准零吸收）。

        本步灵魂语义（与 apply_gain 的不对称，两域对照）：
        - 收获侧基准**半吸收**（暖态 R=αW+(1-α)R）——享乐适应，
          常送礼第三把剑 tag 熄灭；
        - 损失侧基准**零吸收**（R 恒定）——痛感不钝化，"最近手头紧"
          持续显著（连卖三把铁剑 loss_ratio 0.333→0.667→1.000，
          标签不随次数衰减）。
        基准对损失不留缓冲，正是损失厌恶 2.5 倍（ENDOWMENT_MULTIPLIER）
        的离散工程根源。W 允许减到负数（**账面赤字**裁定：赊账/负债
        是账面事实，账本如实登记不粉饰；感知分母 max(R,1) 已防退化，
        负 W 时 loss_ratio>1 仅使标签更显著，无除零风险）。未建账
        no-op（与 apply_gain 同一"有账才动"守卫口径——不凭空造账）。
        """
        acc = self._accounts.get(npc_id)
        if acc is None:
            return
        acc.wealth -= value

    def on_event(self, event: "WorldEvent") -> None:
        """EventBus 订阅入口：消费三类财富事件（收获域 1 + 交易域 2）。

        - item_given（赠予，收获域）：payload["to"] 有账 → apply_gain
          （估值经 estimate_value，快脑报价口径对齐）；
        - item_bought（玩家从 NPC 买走，NPC 卖方收钱）：payload["seller"]
          有账 → apply_gain（**成交价入账**，禀赋溢价真金白银进 W）；
        - item_sold（玩家卖给 NPC，NPC 买方付钱）：payload["buyer"]
          有账 → apply_loss（价金即损失额，钱货两讫）。
        交易事件计价不走二次估值（price 由发布方按估值表算好随 payload
        传递，计价口径见模块 docstring）；payload.price 非整数时跳过
        不入账（防御性兜底，不凭空估值）。引擎侧按 kinds 三类订阅，
        本守卫双保险。
        """
        if event.kind == "item_given":
            to_id = event.payload.get("to")
            if to_id not in self._accounts:
                return
            item = event.payload.get("item")
            self.apply_gain(to_id, estimate_value(item if isinstance(item, str) else ""))
        elif event.kind == "item_bought":
            seller_id = event.payload.get("seller")
            if seller_id not in self._accounts:
                return
            price = event.payload.get("price")
            if isinstance(price, int):
                self.apply_gain(seller_id, price)
        elif event.kind == "item_sold":
            buyer_id = event.payload.get("buyer")
            if buyer_id not in self._accounts:
                return
            price = event.payload.get("price")
            if isinstance(price, int):
                self.apply_loss(buyer_id, price)

    def fortune_tag(self, npc_id: str) -> str:
        """财富显著带 → 离散中文标签（对决策上下文的唯一出口，双向）。

        gain_ratio = (W - R)/max(R,1) >= GAIN_TAG_RATIO → GAIN_TAG
        （最近发了笔小财）；loss_ratio = (R - W)/max(R,1) >=
        LOSS_TAG_RATIO → LOSS_TAG（最近手头紧）；无账/均不显著返回
        ""（基线不变性：fortune 空 → prompt 逐字节不变）。两带数学上
        互斥（W>R 与 R>W 不能同时成立，W==R 时双零），判定顺序**先
        收获带后损失带**：收获侧与既有单侧实现行为逐字节一致（回归
        保护），损失带仅在收获带未触发（W≤R 或不显著）时才可能命中，
        与任意判定顺序数学等价。浮点 ratio 严禁进 prompt（<35token
        规范），出口恒为定长中文短标签。
        """
        acc = self._accounts.get(npc_id)
        if acc is None:
            return ""
        base = max(acc.baseline, 1.0)
        if (acc.wealth - acc.baseline) / base >= GAIN_TAG_RATIO:
            return GAIN_TAG
        if (acc.baseline - acc.wealth) / base >= LOSS_TAG_RATIO:
            return LOSS_TAG
        return ""

    def account(self, npc_id: str) -> Optional[WealthAccount]:
        """观测口：返回账目（测试/脚本用），未建账返回 None。"""
        return self._accounts.get(npc_id)

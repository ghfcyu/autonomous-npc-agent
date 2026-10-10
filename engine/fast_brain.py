"""快脑引擎：高频日常意图的 0-token 规则分流（T3 快慢脑）。

设计要点
--------
- 快慢脑分工：高频、句式封闭的日常意图（打招呼/确认在场/问路/查价/
  告别/报时）由本引擎纯规则秒回，构造 SPEAK Action 直接执行——
  **0 次 LLM 调用**；
  未命中一律落回慢脑（决策层 LLM），既有行为零变化（纯旁路向后兼容）。
- 匹配必须保守：全部意图都带句长上限闸门，且用「整句全匹配/句尾锚定」
  而非裸子串——句子长或含复杂内容一律不命中，防误伤复杂对话走慢脑。
  硬保护：token 基线 4 句（铁匠，打把剑 / 有好铁矿石吗 / 有新货吗 /
  有什么消息）必须全部不命中（tests/test_fast_brain.py 有锁死断言）。
- 回复必须同时消费「脾气掩码」（persona.temperament，掩码 id 与
  filler.py 同款：irritable/cheerful/aloof）与「8D 当下状态」
  （npc.inner_state）——只查一方即为「性格与状态两张皮」，审查点名打回。
  实现三层叠加：意图内容模板（消费世界信息）× 掩码语气前缀（按 8D
  状态带选前缀，状态带口径与 filler.py 完全一致）。
- 意图内容模板全部玩家可感知（问路给真实方向、报时给 world.clock、
  查价按商品词对齐经营范围给报价、presence 确认在场答「在，何事？」），
  无掩码用中性变体（向后兼容）。
- 回复文本 ≤40 字符（<35token 极简 Prompt 组装规范的出口约束），
  不注入浮点向量。
- 快脑只消费既有 InnerState 8D 与脾气掩码，不新增任何心理变量系统。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

from .actions import Action, ActionType

if TYPE_CHECKING:  # 仅为类型注解服务，避免运行期循环导入
    from .inner_state import InnerState
    from .npc import NPC
    from .world import World, Location

# ------------------------------------------------------------------ #
# 匹配前的句子清洗：去首尾空白 + 剥离句尾标点（？/。/！等）
# ------------------------------------------------------------------ #
_TRAILING_PUNCT = "？！?！。，,、；;…～~"


def _strip(text: str) -> str:
    """去首尾空白并剥离句尾标点（匹配口径统一在清洗后的纯文本上）。"""
    t = text.strip()
    while t and t[-1] in _TRAILING_PUNCT:
        t = t[:-1]
    return t


# ------------------------------------------------------------------ #
# 意图规则表（模块级常量，风格对齐 filler.py 的 TEMPERAMENT_MASKS）
#
# 六条规则 = (intent_id, 句长上限, 匹配函数)。匹配函数接收清洗后的
# 玩家整句与 world，命中返回 True；按表顺序取首个命中。
# ------------------------------------------------------------------ #

# a) greet 打招呼：整句本身就是问候语（≤6 字）。用「整句全等」而非
#    子串匹配——含问候词但带后续内容的句子（如「你好，听说你是……」
#    这种长句）一律走慢脑，保守防误伤。「你好呀/你好啊」等高频带
#    语气助词的口语变体为整句全等短语，纳入快脑秒回（第九次审查
#    指令 1 裁定，推翻此前「带语气助词问候保守走慢脑」口径）。
GREET_PHRASES = (
    "你好", "你好呀", "你好啊",
    "您好",
    "你们好", "你们好呀",
    "大家好", "大家好呀",
    "早", "早啊", "早上好", "早上好啊", "早安", "中午好", "下午好", "晚上好", "晚安",
    "喂", "嗨", "嘿", "哈喽", "哈喽啊",
)

# f) presence 确认在场：整句本身就是「在吗」类高频封闭问句（≤6 字），
#    同 greet 用整句全等。2026-10-10 22:00 PM 裁定扩充：非固化采样
#    （scripts/fast_brain_takeover_open.py 13 条）暴露快脑覆盖缺口
#    ——「老板在吗/在吗/有人吗/请问有人在吗」4 条 GAP 候选全走慢脑，
#    每条白付一次约 1200 prompt token 的 LLM 调用只为回一个「在」字；
#    presence 是高频封闭的「确认在场」句式，扩充进快脑秒回是数据
#    驱动的核心体验交付（快脑是 T3 主战场），非防御性数字修补。
PRESENCE_PHRASES = (
    "在吗", "有人吗", "老板在吗", "请问有人在吗", "你在吗", "师父在吗",
)

# d) farewell 告别：整句本身就是告别语（≤6 字），同 greet 用整句全等。
FAREWELL_PHRASES = (
    "再见", "告辞", "回见", "失陪", "拜拜",
    "走了", "我走了", "我先走了", "下次见", "回头见", "告退",
)

# c) ask_price 查价：句尾锚定问价模式（≤8 字）。「多少钱」必须落在
#    句尾——「剑要多少钱」命中，「你说多少钱合适」这类复杂议价句
#    不命中，走慢脑。
PRICE_TAILS = ("多少钱", "什么价", "啥价", "咋卖", "贵不贵", "几钱")

# e) ask_time 报时：整句全匹配时间问法（≤8 字）。可选前缀「现在/这
#    会儿」+ 时间问词 + 可选语气尾。
_TIME_RE = re.compile(r"^(现在|这会儿)?(什么时辰|什么时侯|什么时后|几点|几时)(了|钟|啊)?$")

# b) ask_direction 问路：整句全匹配「（地点）怎么走/在哪/在哪里」
#    （≤12 字）。地点名从 world.locations 的键与中文名动态取，
#    允许「请问/劳驾/敢问」礼貌前缀。
_DIRECTION_SUFFIXES = ("怎么走", "怎么去", "在哪里", "在哪儿", "在哪边", "在哪")
_DIRECTION_PREFIXES = ("请问", "劳驾", "敢问")


def _location_names(world: "World") -> List[str]:
    """世界全部地点的可指称名（id + 中文名），按长度降序保证长名优先匹配。"""
    names: List[str] = []
    for loc in world.locations.values():
        names.extend((loc.id, loc.name))
    return sorted(set(names), key=len, reverse=True)


def _match_greet(text: str, world: "World") -> bool:
    return text in GREET_PHRASES


def _match_presence(text: str, world: "World") -> bool:
    return text in PRESENCE_PHRASES


def _match_farewell(text: str, world: "World") -> bool:
    return text in FAREWELL_PHRASES


def _match_price(text: str, world: "World") -> bool:
    return any(text.endswith(p) for p in PRICE_TAILS)


def _match_time(text: str, world: "World") -> bool:
    return _TIME_RE.fullmatch(text) is not None


def _match_direction(text: str, world: "World") -> bool:
    names = _location_names(world)
    if not names:
        return False
    loc_alt = "|".join(re.escape(n) for n in names)
    suffix_alt = "|".join(_DIRECTION_SUFFIXES)  # 已按长度降序排列
    prefix_alt = "|".join(_DIRECTION_PREFIXES)
    pattern = rf"^(?:{prefix_alt})?(?:{loc_alt})(?:{suffix_alt})$"
    return re.fullmatch(pattern, text) is not None


# 规则表：句长上限是第一道保守闸门（超长一律不命中）。
# presence 插在 greet 之后：同为「交互开场」类整句全等短语（O(1)
# 集合查找），先于正则/动态查表类意图（问路/查价）执行；既有五意图
# 相对次序不变，且 presence 六短语与五意图匹配域零交集（既非问候/
# 告别短语、不以问价尾巴结尾、不匹配时间/问路正则），无跨意图抢占。
IntentRule = Tuple[str, int, Callable[[str, "World"], bool]]
INTENT_RULES: Tuple[IntentRule, ...] = (
    ("greet", 6, _match_greet),
    ("presence", 6, _match_presence),
    ("ask_direction", 12, _match_direction),
    ("ask_price", 8, _match_price),
    ("farewell", 6, _match_farewell),
    ("ask_time", 8, _match_time),
)


# ------------------------------------------------------------------ #
# 语气前缀表：脾气掩码 × 8D 状态带 → 语气前缀。
# 状态带口径与 filler.py 的 TEMPERAMENT_MASKS 完全一致（同维度同阈值，
# 同「排在前优先」次序）——快慢脑共用一套性格-状态分带，不重复建模。
# ------------------------------------------------------------------ #
FAST_REPLY_TONES: Dict[str, List[Tuple[Callable[["InnerState"], bool], str]]] = {
    "irritable": [   # 暴躁倾向 — 铁匠陈
        (lambda s: s.S_stress > 0.6, "（皱眉）"),   # 高压烦躁：回复更冲
        (lambda s: s.e_A > 0.7,     "（抡锤）"),   # 高唤醒激动
        (lambda s: True,            "（抬眼）"),   # default
    ],
    "cheerful": [    # 喜悦倾向 — 商人莉莉
        (lambda s: s.e_P > 0.7,     "（笑盈盈）"),  # 愉悦：回复更热情
        (lambda s: s.e_P < 0.4,     "（勉强笑）"),  # 低愉悦
        (lambda s: True,            "（热情）"),   # default
    ],
    "aloof": [       # 沉稳/冷淡 — 预留第 3 掩码
        (lambda s: s.e_D > 0.7,     "（淡淡一瞥）"),  # 高支配傲慢
        (lambda s: s.p_fatigue > 0.7, "（揉眉）"),  # 疲惫
        (lambda s: True,            "（平静）"),   # default
    ],
}


def _tone_prefix(temperament_id: Optional[str],
                 inner_state: "InnerState") -> str:
    """按脾气掩码 + 8D 当下状态取语气前缀；无掩码/未注册返回空串（中性变体）。"""
    tid = temperament_id or ""
    if not tid:
        return ""
    rules = FAST_REPLY_TONES.get(tid)
    if not rules:
        return ""
    for test, prefix in rules:
        if test(inner_state):
            return prefix
    return ""  # 理论不可达（每表都有 default True），保底


# ------------------------------------------------------------------ #
# 意图内容模板：消费真实世界信息，全部玩家可感知。
# ------------------------------------------------------------------ #

# 查价：商品词-keyed 报价表 + role→经营范围白名单（第九次审查指令 1
#    重构：原 role-keyed 表不管玩家问什么都回职业默认报价，问「铁锤
#    多少钱」答「铁剑十两」——答非所问）。语义规则：**问什么答什么**——
#      1. 从玩家问句提取商品词（子串匹配，多商品词命中按长度降序优先）；
#      2. 命中且属于该 NPC 经营范围 → 回该商品报价句（问铁锤答铁锤价）；
#      3. 命中但不属经营范围（铁匠不卖皮甲）、或商品词不在表中
#         （如「锄头」）→ 一律回 _DEFAULT_PRICE 通用句，严禁答非所问。
_PRICE_LINES: Dict[str, str] = {
    "铁剑": "铁剑十两银子，好料另算。",
    "铁锤": "铁锤三两，包砸不卷刃。",
    "皮甲": "皮甲五两，看料加价。",
    "干粮": "干粮一钱一包。",
}
# role → 经营范围商品集合：白名单外的商品一律不得报出报价（越界走
# 通用句），无经营范围的职业（如 villager）全部走通用句。
_ROLE_GOODS: Dict[str, frozenset] = {
    "blacksmith": frozenset({"铁剑", "铁锤"}),
    "merchant": frozenset({"皮甲", "干粮"}),
}
_DEFAULT_PRICE = "看货定价，童叟无欺。"


def _compass_word(dx: int, dy: int) -> str:
    """坐标差 → 中文方位（东/南/西/北/东南/东北/西南/西北）。"""
    ew = "东" if dx > 0 else "西" if dx < 0 else ""
    ns = "南" if dy > 0 else "北" if dy < 0 else ""
    return ew + ns


def _locate_in_text(text: str, world: "World") -> Optional["Location"]:
    """从玩家问句中提取目标地点（长名优先，id 与中文名均可指称）。"""
    ordered = sorted(world.locations.values(),
                     key=lambda loc: max(len(loc.id), len(loc.name)),
                     reverse=True)
    for loc in ordered:
        if loc.id in text or loc.name in text:
            return loc
    return None


def _content_greet(npc: "NPC", world: "World", query: str) -> str:
    return f"你好，我是{npc.persona.name}。"


def _content_presence(npc: "NPC", world: "World", query: str) -> str:
    """确认在场内容模板：中性短句（语气前缀由 _tone_prefix 掩码×8D
    状态带机制叠加——如 chen 高压时「（皱眉）在，何事？」，本模板
    不自建语气逻辑）。"""
    return "在，何事？"


def _content_farewell(npc: "NPC", world: "World", query: str) -> str:
    return "慢走，不送。"


def _goods_in_text(text: str) -> Optional[str]:
    """从玩家问句提取商品词：子串匹配，多商品词命中按长度降序优先。"""
    for goods in sorted(_PRICE_LINES, key=len, reverse=True):
        if goods in text:
            return goods
    return None


def _content_price(npc: "NPC", world: "World", query: str) -> str:
    """查价回复：问什么答什么；无法对齐商品回通用句，严禁答非所问。

    商品词命中且属于该 NPC 经营范围（persona.role → _ROLE_GOODS
    白名单）→ 回对应报价句；越界商品（问铁匠「皮甲多少钱」）、
    未知商品（「锄头多少钱」）与无商品词问句 → 一律回 _DEFAULT_PRICE
    通用句，绝不把职业默认报价顶在任意问价句上（问铁锤答铁剑=审查
    点名的答非所问缺陷）。
    """
    goods = _goods_in_text(query)
    if goods is not None and goods in _ROLE_GOODS.get(npc.persona.role, frozenset()):
        return _PRICE_LINES[goods]
    return _DEFAULT_PRICE


def _content_time(npc: "NPC", world: "World", query: str) -> str:
    return f"现在是{world.clock}。"


def _content_direction(npc: "NPC", world: "World",
                       query: str) -> str:
    """问路回复：以玩家当前位置为原点给真实方位 + 地点中文名。"""
    target = _locate_in_text(query, world)
    if target is None:
        # reply() 被独立调用（未经 respond 留下问句）时的确定性兜底：
        # 报 NPC 自己的铺子方位，仍消费真实世界地点。
        target = world.locations.get(npc.persona.location_id)
    if target is None:
        return "这地方我说不上来。"
    origin_entity = world.entities.get("player") or npc.entity
    origin = world.locations.get(origin_entity.location_id)
    ox, oy = (origin.x, origin.y) if origin is not None else (0, 0)
    word = _compass_word(target.x - ox, target.y - oy)
    if not word:
        return f"{target.name}就在这儿。"
    return f"{target.name}在{word}边。"


# 内容模板分发表：query 仅 ask_direction 需要（玩家问句中含目标地点）
ContentMaker = Callable[["NPC", "World", str], str]
_CONTENT_MAKERS: Dict[str, ContentMaker] = {
    "greet": _content_greet,
    "presence": _content_presence,
    "ask_direction": _content_direction,
    "ask_price": _content_price,
    "farewell": _content_farewell,
    "ask_time": _content_time,
}


class FastBrain:
    """单个 NPC 的快脑引擎：高频日常意图规则匹配 → 0-token 秒回。

    匹配只看玩家文本与世界状态，与脾气掩码无关（无掩码 NPC 同样持有
    并可用）；脾气掩码只决定命中后回复的语气变体。实例无跨调用共享：
    每个持有它的 NPC 各一份（对齐 filler_engine 的装配模式）。
    """

    def __init__(self) -> None:
        # 最近一次命中的清洗后问句：ask_direction 的回复生成需要从中
        # 提取目标地点（respond 每次命中前刷新，确定性可复现）。
        self.last_query: str = ""

    # ------------------------------------------------------------------ #
    # 对外主 API：命中返回 SPEAK Action，未命中返回 None（走慢脑）
    # ------------------------------------------------------------------ #
    def respond(self, text: str, npc: "NPC", world: "World") -> Optional[Action]:
        """规则匹配玩家整句；命中构造 SPEAK Action（0 LLM 调用），未命中 None。

        SLEEPING 的 NPC 不拦截（返回 None 交慢脑既有路径处理梦呓/REFUSE），
        引擎侧 player_says 亦有不放行快脑的前置检查，此处为双保险。
        """
        if npc.state_machine.blocks_speech():
            return None
        stripped = _strip(text)
        if not stripped:
            return None
        for intent_id, max_len, matcher in INTENT_RULES:
            if len(stripped) <= max_len and matcher(stripped, world):
                self.last_query = stripped
                reply_text = self.reply(intent_id, npc, world)
                return Action(ActionType.SPEAK,
                              {"text": reply_text, "intent": intent_id})
        return None

    # ------------------------------------------------------------------ #
    # 回复生成：意图内容 × 脾气掩码语气变体（消费 8D 当下状态选带）
    # ------------------------------------------------------------------ #
    def reply(self, intent_id: str, npc: "NPC", world: "World") -> str:
        """生成快脑回复（≤40 字符）：内容模板消费世界信息，语气前缀
        同时消费脾气掩码与 8D 当下状态（无掩码用中性变体）。"""
        maker = _CONTENT_MAKERS.get(intent_id)
        if maker is None:
            return ""
        content = maker(npc, world, self.last_query)
        prefix = _tone_prefix(npc.persona.temperament, npc.inner_state)
        return prefix + content

"""T4-b 第一小步 · 村庄关系网 8→44 扩容蓝图契约测试（先红后绿）。

本文件对应审查第十一次指令 1（T4-b 第一小步关系网密度补齐）四项验收，
蓝图契约断言，数据落地后合跑：

1. 密度带断言 —— TestDensityBand（生产 configs/relationships.json 直载）：
   总条数 35-45、25 NPC 全员出边、出边度数中位数 ≤ 2（长尾形状）、
   枢纽三人（tavern_sun/doc_qin/guard_wang）∈ [3,5] 且严格大于中位数、
   孤寡/学童/学徒组（widow_yi/cao_widow/boy_shitou/herb_boy/tavern_aman）
   恰 1 条出边；
2. top-3 兑现 —— TestTop3GuardrailFulfillment（真引擎链路）：核心 NPC
   spawn 自动注入 player 边（客人 0.5，engine/npc.py），chen 配置 3 出边
   +player=运行时 4 条 → top-3 截断首次真实激活（恰 3 行、保底桶父亲
   居首、老主顾 0.4 被截断）；lily 同理（同行 0.35 被截断）；
3. 敌意对抗性 —— TestHostilityEdges（直载）+ TestHostilityAdversarial
   （真引擎）：敌意边恰 2 条且全部 affinity<0，miller_qian 与
   fisher_zhou 互为反向敌意对；fisher_zhou 4 出边中敌意边 -0.55 必须凭
   |affinity| 口径竞争进 top-3——若引擎改为按 affinity 原值排序或过滤
   负值，断言必红；
4. 零回归 —— 本文件纯新增，不改 engine/、configs/ 与任何既有测试；
   全量合跑由 PM 在实现子代理落地后执行验收。

编写纪律：
- 断言锁 PM 蓝图契约的关键不变量（密度带形状/12 条保底边/敌意对/
  top-3 截断），不锁浮点全量 JSON、不做 PINNED 类数字锁（反内卷）；
- 断言逻辑以模块级「断言内核」单一承载，双数据源复用：
  TestBlueprintSelfCheck 用 from_list 在内存构造 44 条蓝图数据自证
  断言逻辑正确（不依赖生产文件落地时序），生产直载/真引擎类验收
  数据落地状态，红绿归因清晰；
- 【第十二次审查 CONDITIONAL 清偿】夹具原享有 11 条长尾边分布自由
  度，与生产 configs/relationships.json 实测存在 13+ 处边漂移（方向
  反转 1 处 / 关系词 2 处 / affinity 10 处）；现按「生产数据是唯一
  真源（蓝图即契约）」将 BLUEPRINT_EDGES 逐字对齐生产文件（44 条
  按生产顺序排列、五字段逐字相同），并新增 TestBlueprintProductionSync
  把「夹具 = 生产」固化为对账断言——生产未来变更而夹具未同步时必红。
"""

import json
import unittest
from pathlib import Path
from statistics import median

from engine.engine import DEFAULT_RELATIONSHIPS_PATH, NPCEngine
from engine.llm.mock import MockLLMProvider
from engine.npc import Persona
from engine.relationships import RelationshipNetwork


# ========================================================================== #
# 蓝图契约常量
# ========================================================================== #
# 25 NPC 全员 id（审查第十一次蓝图名册：2 核心 + 8 旧背景 + 15 新住民）
ALL_NPC_IDS = (
    "chen", "lily", "farmer_zhao", "farmer_wang", "zhao_daniu",
    "farmhand_sun", "widow_yi", "cao_widow", "boy_shitou", "herb_boy",
    "tavern_aman", "cobbler_jia", "master_wen", "carpenter_lu",
    "peddler_luo", "weaver_yang", "watchman_diao", "herder_ma",
    "old_zhang", "baker_liu", "doc_qin", "miller_qian", "tavern_sun",
    "guard_wang", "fisher_zhou",
)
# 枢纽三人：出边数 ∈ [3,5] 且严格大于全村中位数
HUB_TRIO = ("tavern_sun", "doc_qin", "guard_wang")
# 孤寡/学童/学徒组：恰 1 条出边（蓝图契约组共 5 人，含 tavern_aman）
LONELY_GROUP = ("widow_yi", "cao_widow", "boy_shitou", "herb_boy",
                "tavern_aman")
HOSTILITY_RELATION = "冤家"
# 12 条血缘/师徒保底边：(source_id, relation, target_id)
PRIORITY_TWELVE = (
    ("chen", "父亲", "old_zhang"),
    ("old_zhang", "儿子", "chen"),
    ("farmer_zhao", "妻子", "farmer_wang"),
    ("farmer_zhao", "儿子", "zhao_daniu"),
    ("farmer_wang", "丈夫", "farmer_zhao"),
    ("farmer_wang", "儿子", "zhao_daniu"),
    ("zhao_daniu", "父亲", "farmer_zhao"),
    ("zhao_daniu", "母亲", "farmer_wang"),
    ("boy_shitou", "师父", "master_wen"),
    ("master_wen", "徒弟", "boy_shitou"),
    ("herb_boy", "师父", "doc_qin"),
    ("doc_qin", "徒弟", "herb_boy"),
)


# ========================================================================== #
# 蓝图 44 条内存数据（第十二次审查清偿：与 configs/relationships.json
# 逐字镜像——生产数据是唯一真源，蓝图即契约；44 条边按生产文件顺序排列）
# ========================================================================== #
BLUEPRINT_EDGES = [
    # ---- chen：恰 3 条出边（父亲/酒友/老主顾）—— top-3 截断场景 ----
    {"source_id": "chen", "target_id": "old_zhang", "target_name": "老张",
     "relation": "父亲", "affinity": 0.9},
    {"source_id": "chen", "target_id": "tavern_sun", "target_name": "孙老三",
     "relation": "酒友", "affinity": 0.6},
    {"source_id": "chen", "target_id": "baker_liu", "target_name": "刘婶",
     "relation": "老主顾", "affinity": 0.4},
    # ---- lily：恰 3 条出边（货源/供货商/同行）—— top-3 截断场景 ----
    {"source_id": "lily", "target_id": "weaver_yang", "target_name": "杨大姐",
     "relation": "货源", "affinity": 0.6},
    {"source_id": "lily", "target_id": "tavern_sun", "target_name": "孙老三",
     "relation": "供货商", "affinity": 0.4},
    {"source_id": "lily", "target_id": "peddler_luo", "target_name": "罗四方",
     "relation": "同行", "affinity": 0.35},
    # ---- farmer_zhao：3 条出边（妻子/儿子血缘保底 + 主顾 miller_qian）----
    #   （按生产 relationships.json 口径修正：补回 miller_qian 主顾 0.5 边，
    #     蓝图原缺，farmer_zhao 度数 2→3）
    {"source_id": "farmer_zhao", "target_id": "farmer_wang",
     "target_name": "王氏", "relation": "妻子", "affinity": 0.9},
    {"source_id": "farmer_zhao", "target_id": "zhao_daniu",
     "target_name": "赵大牛", "relation": "儿子", "affinity": 0.85},
    {"source_id": "farmer_zhao", "target_id": "miller_qian",
     "target_name": "钱多福", "relation": "主顾", "affinity": 0.5},
    # ---- farmer_wang / zhao_daniu：赵家血缘其余 4 条保底 ----
    {"source_id": "farmer_wang", "target_id": "farmer_zhao",
     "target_name": "赵老汉", "relation": "丈夫", "affinity": 0.9},
    {"source_id": "farmer_wang", "target_id": "zhao_daniu",
     "target_name": "赵大牛", "relation": "儿子", "affinity": 0.85},
    {"source_id": "zhao_daniu", "target_id": "farmer_zhao",
     "target_name": "赵老汉", "relation": "父亲", "affinity": 0.85},
    {"source_id": "zhao_daniu", "target_id": "farmer_wang",
     "target_name": "王氏", "relation": "母亲", "affinity": 0.85},
    # ---- farmhand_sun：恰 1 条出边（长工投东家）----
    #   （按生产 relationships.json 口径修正：东家 affinity 0.7→0.6；
    #     删除蓝图自设计的 herder_ma 工友边——度数 2→1）
    {"source_id": "farmhand_sun", "target_id": "farmer_zhao",
     "target_name": "赵老汉", "relation": "东家", "affinity": 0.6},
    # ---- 孤寡/学童/学徒组：各恰 1 条出边 ----
    #   （widow_yi 按生产 relationships.json 口径修正：邻居 0.6→恩人 0.8；
    #     cao_widow 按生产口径修正：weaver_yang 邻居 0.5→doc_qin 恩人 0.7；
    #     tavern_aman 按生产口径修正：东家 affinity 0.8→0.7）
    {"source_id": "widow_yi", "target_id": "baker_liu",
     "target_name": "刘婶", "relation": "恩人", "affinity": 0.8},
    {"source_id": "cao_widow", "target_id": "doc_qin",
     "target_name": "秦大夫", "relation": "恩人", "affinity": 0.7},
    {"source_id": "boy_shitou", "target_id": "master_wen",
     "target_name": "文先生", "relation": "师父", "affinity": 0.75},
    {"source_id": "herb_boy", "target_id": "doc_qin",
     "target_name": "秦大夫", "relation": "师父", "affinity": 0.85},
    {"source_id": "tavern_aman", "target_id": "tavern_sun",
     "target_name": "孙老三", "relation": "东家", "affinity": 0.7},
    # ---- cobbler_jia：恰 1 条出边（邻居 carpenter_lu）----
    #   （按生产 relationships.json 口径修正：蓝图自设计的 chen 老主顾
    #     0.7 + peddler_luo 供货商 0.4 两条边收敛为 1 条邻居边——度数 2→1）
    {"source_id": "cobbler_jia", "target_id": "carpenter_lu",
     "target_name": "鲁一刀", "relation": "邻居", "affinity": 0.4},
    # ---- 枢纽 doc_qin：恰 3 条出边（徒弟/老友/病人）----
    #   （病人边按生产 relationships.json 口径修正：目标 weaver_yang→cao_widow）
    {"source_id": "doc_qin", "target_id": "herb_boy",
     "target_name": "小蓟", "relation": "徒弟", "affinity": 0.8},
    {"source_id": "doc_qin", "target_id": "old_zhang",
     "target_name": "老张", "relation": "老友", "affinity": 0.7},
    {"source_id": "doc_qin", "target_id": "cao_widow",
     "target_name": "曹寡妇", "relation": "病人", "affinity": 0.5},
    # ---- master_wen：师徒反向保底 1 条 ----
    {"source_id": "master_wen", "target_id": "boy_shitou",
     "target_name": "小石头", "relation": "徒弟", "affinity": 0.7},
    # ---- miller_qian：2 条出边（主顾 + 冤家敌意边）—— 敌意对正向半边 ----
    #   （按生产 relationships.json 口径修正：补回 farmer_zhao 主顾 0.5
    #     边，蓝图原仅 1 条冤家边——度数 1→2）
    {"source_id": "miller_qian", "target_id": "farmer_zhao",
     "target_name": "赵老汉", "relation": "主顾", "affinity": 0.5},
    {"source_id": "miller_qian", "target_id": "fisher_zhou",
     "target_name": "周渔夫", "relation": "冤家", "affinity": -0.6},
    # ---- carpenter_lu / peddler_luo / weaver_yang：各恰 1 条出边 ----
    #   （carpenter_lu 按生产 relationships.json 口径修正：cobbler_jia
    #     同行 0.6→farmer_zhao 主顾 0.5；peddler_luo 同行 0.5→0.4；
    #     weaver_yang 关系词 老主顾→主顾、affinity 0.6→0.5）
    {"source_id": "carpenter_lu", "target_id": "farmer_zhao",
     "target_name": "赵老汉", "relation": "主顾", "affinity": 0.5},
    {"source_id": "peddler_luo", "target_id": "lily",
     "target_name": "商人莉莉", "relation": "同行", "affinity": 0.4},
    {"source_id": "weaver_yang", "target_id": "lily",
     "target_name": "商人莉莉", "relation": "主顾", "affinity": 0.5},
    # ---- 枢纽 tavern_sun：恰 3 条出边 ----
    {"source_id": "tavern_sun", "target_id": "fisher_zhou",
     "target_name": "周渔夫", "relation": "供货商", "affinity": 0.6},
    {"source_id": "tavern_sun", "target_id": "guard_wang",
     "target_name": "王守卫", "relation": "酒友", "affinity": 0.5},
    {"source_id": "tavern_sun", "target_id": "doc_qin",
     "target_name": "秦大夫", "relation": "老主顾", "affinity": 0.7},
    # ---- 枢纽 guard_wang：恰 3 条出边 ----
    {"source_id": "guard_wang", "target_id": "doc_qin",
     "target_name": "秦大夫", "relation": "恩人", "affinity": 0.8},
    {"source_id": "guard_wang", "target_id": "watchman_diao",
     "target_name": "刁更头", "relation": "同僚", "affinity": 0.5},
    {"source_id": "guard_wang", "target_id": "fisher_zhou",
     "target_name": "周渔夫", "relation": "酒友", "affinity": 0.3},
    # ---- watchman_diao：恰 1 条出边 ----
    {"source_id": "watchman_diao", "target_id": "guard_wang",
     "target_name": "王守卫", "relation": "同僚", "affinity": 0.5},
    # ---- fisher_zhou：4 条出边（含敌意边）—— 敌意 top-3 对抗场景 ----
    {"source_id": "fisher_zhou", "target_id": "tavern_sun",
     "target_name": "孙老三", "relation": "老主顾", "affinity": 0.6},
    {"source_id": "fisher_zhou", "target_id": "miller_qian",
     "target_name": "钱多福", "relation": "冤家", "affinity": -0.55},
    {"source_id": "fisher_zhou", "target_id": "baker_liu",
     "target_name": "刘婶", "relation": "主顾", "affinity": 0.4},
    {"source_id": "fisher_zhou", "target_id": "guard_wang",
     "target_name": "王守卫", "relation": "酒友", "affinity": 0.3},
    # ---- herder_ma：恰 1 条出边 ----
    #   （按生产 relationships.json 口径修正：farmer_wang 邻居 0.4→
    #     tavern_sun 主顾 0.4——目标换人）
    {"source_id": "herder_ma", "target_id": "tavern_sun",
     "target_name": "孙老三", "relation": "主顾", "affinity": 0.4},
    # ---- old_zhang：陈家父子反向保底 1 条 ----
    {"source_id": "old_zhang", "target_id": "chen",
     "target_name": "铁匠陈", "relation": "儿子", "affinity": 0.9},
    # ---- baker_liu：2 条出边（farmer_zhao 邻居 + miller_qian 供货商）----
    #   （方向反转修正：蓝图 baker_liu→chen 老主顾 0.4 在生产不存在——
    #     生产 chen→baker_liu 才是老主顾方向（已列 chen 组）；按生产
    #     relationships.json 口径修正，baker_liu 第二条出边为 miller_qian
    #     供货商 0.5）
    {"source_id": "baker_liu", "target_id": "farmer_zhao",
     "target_name": "赵老汉", "relation": "邻居", "affinity": 0.6},
    {"source_id": "baker_liu", "target_id": "miller_qian",
     "target_name": "钱多福", "relation": "供货商", "affinity": 0.5},
]


def blueprint_network() -> RelationshipNetwork:
    """44 条蓝图数据 → 全新 RelationshipNetwork（每次调用独立实例）。"""
    return RelationshipNetwork.from_list(BLUEPRINT_EDGES)


# ========================================================================== #
# 测试脚手架
# ========================================================================== #
def relation_lines(text):
    """to_prompt_text 输出中的关系行（剥掉「【人际关系】」段落头）。

    与 tests/test_village_integration.py 的同名辅助函数同一契约。
    """
    return [line for line in text.splitlines()
            if line and not line.startswith("【")]


def median_out_degree(net):
    """25 NPC 出边度数的中位数（statistics.median，奇数个取正中值）。"""
    return median(len(net.query(npc_id)) for npc_id in ALL_NPC_IDS)


def minimal_chen_persona():
    """最小人格 chen（test_relationships.py 同款，仅 spawn 注边用）。"""
    return Persona(id="chen", name="铁匠陈", role="blacksmith",
                   location_id="forge", personality="沉稳", speech_style="简短",
                   backstory="三代铁匠", greeting_bank=["炉子热着。"],
                   fallback_bank=["嗯，你说。"], sleep_mumble="（鼾声）",
                   schedule={"20": "SLEEPING"},
                   topic_responses={"剑|刀": ["打剑？拿好铁来。"]})


def minimal_lily_persona():
    """最小人格 lily（test_relationships.py 同款，仅 spawn 注边用）。"""
    return Persona(id="lily", name="商人莉莉", role="merchant",
                   location_id="market", personality="热情", speech_style="快",
                   backstory="跑过五个城镇", greeting_bank=["贵客来啦！"],
                   fallback_bank=["我听着呢！"], sleep_mumble="（梦中谈价）",
                   schedule={"21": "SLEEPING"})


# ========================================================================== #
# 断言内核（单一逻辑 · 双数据源复用：蓝图内存自证 + 生产文件验收）
# ========================================================================== #
def assert_total_in_band(tc, net):
    """密度带 ①：总条数 35 ≤ n ≤ 45（蓝图 44 居中，两侧留容纳带）。"""
    total = len(net.to_dict())
    tc.assertTrue(35 <= total <= 45,
                  msg=f"关系网总条数 {total} 应落在密度带 [35, 45] 内")


def assert_all_npcs_have_out_edges(tc, net):
    """密度带 ②：25 NPC 全员 query(id) 非空（无人是关系孤岛）。"""
    for npc_id in ALL_NPC_IDS:
        rels = net.query(npc_id)
        tc.assertTrue(rels,
                      msg=f"NPC {npc_id} 应至少有 1 条出边（25/25 全员覆盖）")


def assert_median_le_two(tc, net):
    """密度带 ③：出边度数中位数 ≤ 2（长尾形状：多数人少边）。"""
    med = median_out_degree(net)
    tc.assertLessEqual(med, 2,
                        msg=f"全村出边度数中位数 {med} 应 ≤ 2（长尾形状）")


def assert_hub_trio_in_band_above_median(tc, net):
    """密度带 ④：枢纽三人出边数 ∈ [3,5] 且严格大于全村中位数。"""
    med = median_out_degree(net)
    for hub in HUB_TRIO:
        degree = len(net.query(hub))
        tc.assertTrue(3 <= degree <= 5,
                      msg=f"枢纽 {hub} 出边数 {degree} 应落在 [3, 5]")
        tc.assertGreater(degree, med,
                         msg=f"枢纽 {hub} 出边数 {degree} 应严格大于"
                             f"全村中位数 {med}")


def assert_lonely_group_exactly_one(tc, net):
    """密度带 ⑤：孤寡/学童/学徒组恰 1 条出边（蓝图契约组含 tavern_aman）。"""
    for npc_id in LONELY_GROUP:
        degree = len(net.query(npc_id))
        tc.assertEqual(degree, 1,
                       msg=f"孤寡/学童/学徒 {npc_id} 出边数应恰 1，"
                           f"实际 {degree}")


def assert_hostility_exactly_two_negative(tc, net):
    """敌意 ①：敌意边恰 2 条且全部 affinity < 0。"""
    hostile = [d for d in net.to_dict() if d["affinity"] < 0]
    tc.assertEqual(len(hostile), 2,
                   msg=f"敌意边应恰 2 条且全部为负，实际 {hostile}")


def assert_hostility_mutual_pair(tc, net):
    """敌意 ②：磨坊主与渔夫互为敌意对（两条边方向相反、同为冤家）。"""
    forward = net.get_relation_to("miller_qian", "fisher_zhou")
    backward = net.get_relation_to("fisher_zhou", "miller_qian")
    tc.assertIsNotNone(forward, msg="缺 miller_qian→fisher_zhou 敌意边")
    tc.assertIsNotNone(backward, msg="缺 fisher_zhou→miller_qian 敌意边")
    if forward is None or backward is None:
        return
    tc.assertEqual(forward.relation, HOSTILITY_RELATION,
                   msg=f"miller_qian→fisher_zhou 关系应为"
                       f"{HOSTILITY_RELATION}，实际 {forward.relation}")
    tc.assertEqual(backward.relation, HOSTILITY_RELATION,
                   msg=f"fisher_zhou→miller_qian 关系应为"
                       f"{HOSTILITY_RELATION}，实际 {backward.relation}")
    tc.assertLess(forward.affinity, 0, msg="正向敌意边 affinity 应为负")
    tc.assertLess(backward.affinity, 0, msg="反向敌意边 affinity 应为负")


def assert_priority_twelve_present(tc, net):
    """保底 ①：12 条血缘/师徒保底边逐一 query_by_relation 非空且指向契约目标。"""
    for source_id, relation, target_id in PRIORITY_TWELVE:
        matches = net.query_by_relation(source_id, relation)
        tc.assertTrue(matches,
                      msg=f"保底边缺失：{source_id} 的「{relation}」出边为空")
        tc.assertTrue(any(r.target_id == target_id for r in matches),
                      msg=f"保底边目标不符：{source_id} 的「{relation}」应指向"
                          f"{target_id}，实际 {[r.target_id for r in matches]}")


def assert_chen_father_affinity_0_9(tc, net):
    """保底 ②：chen 的「父亲」边 affinity 为 0.9（保底桶原型边）。"""
    rel = net.get_relation_to("chen", "old_zhang")
    tc.assertIsNotNone(rel, msg="缺 chen→old_zhang 父亲边")
    if rel is None:
        return
    tc.assertEqual(rel.relation, "父亲")
    tc.assertAlmostEqual(rel.affinity, 0.9,
                          msg=f"chen 父亲边好感应为 0.9，实际 {rel.affinity}")


def assert_top3_chen(tc, net):
    """top-3 ①-④：chen 恰 3 行 / 保底桶父亲居首 / 酒友+客人在 / 老主顾截断。

    net 须为引擎运行时关系网（spawn 已注入 player 客人边）：chen 配置
    3 出边 + player 1 = 运行时 4 条，top-3 截断激活。
    """
    lines = relation_lines(net.to_prompt_text("chen"))
    tc.assertEqual(len(lines), 3,
                   msg=f"chen 运行时 4 出边（配置 3 + player 客人边），top-3 "
                       f"截断应恰 3 行，实际 {len(lines)}: {lines}")
    joined = "\n".join(lines)
    # 保底桶优先：父亲边排首行（血缘不因 |affinity| 竞争被挤出）
    tc.assertIn("父亲", lines[0], msg=f"首行应为保底桶父亲边: {lines}")
    tc.assertIn("老张", lines[0], msg=f"父亲边目标应为老张: {lines}")
    # 高值普通边（酒友 0.6）与 player 边（客人 0.5）共存于截断后的 3 行
    tc.assertIn("酒友", joined, msg=f"3 行应含酒友（0.6 挤入 top-3）: {lines}")
    tc.assertIn("客人", joined, msg=f"3 行应含客人（player 边 0.5）: {lines}")
    # 0.4 的老主顾被截断——截断真实发生，不是「恰好只有 3 条」
    tc.assertNotIn("老主顾", joined,
                   msg=f"老主顾 0.4 应被 top-3 截断挤出: {lines}")


def assert_top3_lily(tc, net):
    """top-3 ⑤：lily 恰 3 行 / 货源+客人在 / 同行 0.35 被截断。"""
    lines = relation_lines(net.to_prompt_text("lily"))
    tc.assertEqual(len(lines), 3,
                   msg=f"lily 运行时 4 出边，top-3 截断应恰 3 行，"
                       f"实际 {len(lines)}: {lines}")
    joined = "\n".join(lines)
    tc.assertIn("货源", joined, msg=f"3 行应含货源（0.6 居首）: {lines}")
    tc.assertIn("客人", joined, msg=f"3 行应含客人（player 边 0.5）: {lines}")
    tc.assertNotIn("同行", joined,
                   msg=f"同行 0.35 应被 top-3 截断挤出: {lines}")


def assert_fisher_zhou_top3_contains_hostility(tc, net):
    """敌意对抗 ①：fisher_zhou 恰 3 行且含「敌意」行。

    对抗性核心：fisher_zhou 配置 4 出边（背景 NPC 无 player 边），敌意边
    -0.55 必须按 |affinity| 口径与 0.6/0.4/0.3 竞争进 top-3——若引擎改为
    按 affinity 原值排序（负值垫底）或直接过滤负值，本断言必红。
    """
    lines = relation_lines(net.to_prompt_text("fisher_zhou"))
    tc.assertEqual(len(lines), 3,
                   msg=f"fisher_zhou 4 出边 top-3 截断应恰 3 行，"
                       f"实际 {len(lines)}: {lines}")
    tc.assertTrue(any("敌意" in line for line in lines),
                  msg=f"敌意边应凭 |affinity|=0.55 竞争进 top-3: {lines}")


def assert_hostility_line_names_miller_qian(tc, net):
    """敌意对抗 ②：该敌意行点名钱多福（磨坊主）。"""
    lines = relation_lines(net.to_prompt_text("fisher_zhou"))
    hostile_lines = [line for line in lines if "敌意" in line]
    tc.assertTrue(hostile_lines,
                  msg=f"fisher_zhou top-3 应含敌意行: {lines}")
    tc.assertTrue(any("钱多福" in line for line in hostile_lines),
                  msg=f"敌意行应点名钱多福（磨坊主）: {hostile_lines}")


# ========================================================================== #
# 0. TestBlueprintSelfCheck —— 蓝图 44 条内存构造自证（断言逻辑正确性）
# ========================================================================== #
class TestBlueprintSelfCheck(unittest.TestCase):
    """蓝图内存自证：不依赖生产文件落地时序，先行验证断言逻辑本身。

    from_list 构造的 44 条蓝图数据满足全部契约不变量；五个用例与生产
    五类完全同一断言内核——本类全绿证明「断言逻辑」正确，届时生产类
    的红绿仅反映「数据落地」状态，红绿归因清晰。
    """

    def setUp(self):
        # 纯蓝图网络（无 player 边）：验收密度带/敌意对/保底边等数据不变量
        self.net = blueprint_network()
        # 真引擎链路：最小人格 chen/lily spawn 时自动注入 player 客人边
        # （engine/npc.py 同一注入路径），fisher_zhou 不 spawn 无 player 边
        self.engine = NPCEngine(
            llm=MockLLMProvider(),
            npc_configs=[minimal_chen_persona(), minimal_lily_persona()],
            relationships=blueprint_network())

    def test_blueprint_fixture_completeness(self):
        # 蓝图夹具自洽：恰 44 条边、25 个来源全员覆盖（防装配缺漏）
        data = self.net.to_dict()
        self.assertEqual(len(data), 44)
        self.assertEqual({d["source_id"] for d in data}, set(ALL_NPC_IDS))

    def test_density_band_contract(self):
        assert_total_in_band(self, self.net)
        assert_all_npcs_have_out_edges(self, self.net)
        assert_median_le_two(self, self.net)
        assert_hub_trio_in_band_above_median(self, self.net)
        assert_lonely_group_exactly_one(self, self.net)

    def test_hostility_contract(self):
        assert_hostility_exactly_two_negative(self, self.net)
        assert_hostility_mutual_pair(self, self.net)

    def test_priority_guarantee_contract(self):
        assert_priority_twelve_present(self, self.net)
        assert_chen_father_affinity_0_9(self, self.net)

    def test_top3_guardrail_contract(self):
        # 运行时网络 = 蓝图 + chen/lily 两条 player 边 → 截断场景激活
        assert_top3_chen(self, self.engine.relationships)
        assert_top3_lily(self, self.engine.relationships)

    def test_hostility_adversarial_contract(self):
        assert_fisher_zhou_top3_contains_hostility(self, self.engine.relationships)
        assert_hostility_line_names_miller_qian(self, self.engine.relationships)


# ========================================================================== #
# 1. TestDensityBand —— 生产直载 · 密度带契约（验收 1：密度带断言）
# ========================================================================== #
class TestDensityBand(unittest.TestCase):
    """configs/relationships.json 直载验收：44 条扩容后的村庄密度带形状。

    ① 总条数 35-45；② 25 NPC 全员出边；③ 出边度数中位数 ≤ 2；
    ④ 枢纽三人 ∈ [3,5] 且严格大于中位数；⑤ 孤寡/学童/学徒恰 1 条。
    """

    def setUp(self):
        self.net = RelationshipNetwork.from_file(DEFAULT_RELATIONSHIPS_PATH)

    def test_total_edges_in_density_band_35_to_45(self):
        assert_total_in_band(self, self.net)

    def test_all_25_npcs_have_out_edges(self):
        assert_all_npcs_have_out_edges(self, self.net)

    def test_median_out_degree_le_two(self):
        assert_median_le_two(self, self.net)

    def test_hub_trio_in_band_and_above_median(self):
        assert_hub_trio_in_band_above_median(self, self.net)

    def test_lonely_group_exactly_one_out_edge(self):
        assert_lonely_group_exactly_one(self, self.net)


# ========================================================================== #
# 2. TestHostilityEdges —— 生产直载 · 敌意对契约（验收 3：敌意对抗性）
# ========================================================================== #
class TestHostilityEdges(unittest.TestCase):
    """configs/relationships.json 直载验收：敌意边恰 2 条且互为反向对。

    磨坊主钱多福与渔夫周渔夫结怨：两条冤家边方向相反、affinity 均为负
    ——长尾村庄不是全员和气，真实社会图须有敌意埋点。
    """

    def setUp(self):
        self.net = RelationshipNetwork.from_file(DEFAULT_RELATIONSHIPS_PATH)

    def test_hostility_edges_exactly_two_all_negative(self):
        assert_hostility_exactly_two_negative(self, self.net)

    def test_miller_qian_fisher_zhou_mutual_hostility_pair(self):
        assert_hostility_mutual_pair(self, self.net)


# ========================================================================== #
# 3. TestTop3GuardrailFulfillment —— 真引擎链路 · top-3 截断兑现（验收 2）
# ========================================================================== #
class TestTop3GuardrailFulfillment(unittest.TestCase):
    """NPCEngine 默认构造（全量 configs 自动装配，mock provider，见
    test_village.py 同款构造）：核心 chen/lily spawn 自动注入 player 客人
    边 → 运行时 4 出边，T4-a 落地的 top-3 截断护栏首次获得真实生效场景。

    - chen：恰 3 行、保底桶父亲居首、酒友+客人在、老主顾 0.4 被截断；
    - lily：恰 3 行、货源+客人在、同行 0.35 被截断。
    """

    def setUp(self):
        self.engine = NPCEngine(llm=MockLLMProvider())

    def test_chen_top3_priority_bucket_and_truncation(self):
        assert_top3_chen(self, self.engine.relationships)

    def test_lily_top3_and_truncation(self):
        assert_top3_lily(self, self.engine.relationships)


# ========================================================================== #
# 4. TestHostilityAdversarial —— 真引擎链路 · 敌意 top-3 对抗（验收 3）
# ========================================================================== #
class TestHostilityAdversarial(unittest.TestCase):
    """fisher_zhou（背景 NPC，无 player 边）4 出边的 top-3 竞争对抗：

    敌意边 -0.55 必须凭 |affinity| 口径挤入 top-3（排在 0.4 主顾之前、
    0.3 酒友被截断）。对抗性：若引擎改为按 affinity 原值排序（负值垫底
    出局）或直接过滤负值关系，本类断言必须红——关系网扩容不是把敌意
    数据藏进配置里，而是真的让它进入 NPC 的决策上下文。
    """

    def setUp(self):
        self.engine = NPCEngine(llm=MockLLMProvider())

    def test_fisher_zhou_top3_contains_hostility_line(self):
        assert_fisher_zhou_top3_contains_hostility(self, self.engine.relationships)

    def test_hostility_line_names_miller_qian(self):
        assert_hostility_line_names_miller_qian(self, self.engine.relationships)


# ========================================================================== #
# 5. TestPriorityGuaranteeCrossCheck —— 生产直载 · 保底边交叉对账
# ========================================================================== #
class TestPriorityGuaranteeCrossCheck(unittest.TestCase):
    """configs/relationships.json 直载验收：12 条血缘/师徒保底边对账。

    T4-a 的 top-3 保底桶（relation ∈ 亲缘类型集合）以这 12 条边为原型：
    密度扩容到 44 条后保底边一个都不能丢（逐一 query_by_relation 交叉
    对账），chen 的父亲边好感 0.9 是保底桶的原型值。
    """

    def setUp(self):
        self.net = RelationshipNetwork.from_file(DEFAULT_RELATIONSHIPS_PATH)

    def test_twelve_priority_edges_all_present(self):
        assert_priority_twelve_present(self, self.net)

    def test_chen_father_edge_affinity_is_0_9(self):
        assert_chen_father_affinity_0_9(self, self.net)


# ========================================================================== #
# 6. TestBlueprintProductionSync —— 夹具与生产逐字对账（第十二次审查清偿）
# ========================================================================== #
PRODUCTION_RELATIONSHIPS_PATH = (
    Path(__file__).resolve().parent.parent / "configs" / "relationships.json")
# 边对账字段序（逐字段比较，任何字段漂移都进入集合差集）
EDGE_FIELDS = ("source_id", "target_id", "target_name", "relation", "affinity")


class TestBlueprintProductionSync(unittest.TestCase):
    """夹具与生产零漂移守卫：BLUEPRINT_EDGES == configs/relationships.json。

    第十二次审查（CONDITIONAL）清偿条件：BLUEPRINT_EDGES 曾与生产
    configs/relationships.json 存在 13+ 处边漂移（方向反转 1 处 /
    关系词 2 处 / affinity 10 处），漂移夹具放行进仓库=测试资产与
    生产数据的隐性分叉。生产数据是唯一真源，夹具已逐字对齐；本类
    把对账固化为断言——生产文件未来任何变更而夹具未同步时，本测试
    必红，漂移不再依赖人工对账发现。

    对账口径：每条边按 EDGE_FIELDS 五字段转元组做集合比较；两个
    用例分别守卫条数与集合内容——缺边/多边/重边红 ①，任何一条边
    任何字段改值红 ②，两用例合起来无漂移形态能漏网。
    """

    @classmethod
    def setUpClass(cls):
        with open(PRODUCTION_RELATIONSHIPS_PATH, encoding="utf-8") as f:
            cls.production_edges = json.load(f)

    def test_edge_count_matches_production(self):
        """对账 ①：夹具条数 == 生产条数（缺边/多边/重边先在此报警）。"""
        self.assertEqual(
            len(BLUEPRINT_EDGES), len(self.production_edges),
            msg=(f"夹具 {len(BLUEPRINT_EDGES)} 条 ≠ 生产 "
                 f"{len(self.production_edges)} 条——生产文件变更后 "
                 f"BLUEPRINT_EDGES 未同步"))

    def test_every_edge_field_matches_production(self):
        """对账 ②：两集合逐字段一致，任何一条边任何字段漂移必红。"""
        production_keys = [tuple(e[f] for f in EDGE_FIELDS)
                           for e in self.production_edges]
        fixture_keys = [tuple(e[f] for f in EDGE_FIELDS)
                        for e in BLUEPRINT_EDGES]
        # 漂移边定位（按各自文件顺序取第一条）：仅生产有=夹具漏改，
        # 仅夹具有=夹具残留旧值——msg 直接给出漂移边全文，便于排查
        production_only = [k for k in production_keys
                           if k not in set(fixture_keys)]
        fixture_only = [k for k in fixture_keys
                        if k not in set(production_keys)]
        self.assertEqual(
            set(fixture_keys), set(production_keys),
            msg=(f"夹具与生产存在漂移边（字段序 {EDGE_FIELDS}）："
                 f"第一条仅生产有 "
                 f"{production_only[0] if production_only else '无'}；"
                 f"第一条仅夹具有 "
                 f"{fixture_only[0] if fixture_only else '无'}"
                 f"——按生产 configs/relationships.json 口径同步夹具"))


if __name__ == "__main__":
    unittest.main()

"""T2 标签库 · engine/temperament_table.py 单元测试（先红后绿之"红"）。

覆盖（任务书验收口径）：
- 双表齐全性锁：两套分区表结构对等（同维度同键集）、8 脾气注册表与
  规范 7.2 节全等、8 判定函数在两套激活版本下全部可求值（无缺失阈值）；
- 语义等价锁（核心·双层）：表级——v01 表逐阈值 = v11 表的仿射像
  t_01=(t_11+1)/2；行为级——每脾气构造恰在阈值上的边界状态，
  两版本判定行为一致（防两套表设计漂移）；
- 规范契约锁：v11 阈值与 docs 2.4 节硬编码规范表全等（含键集，
  项目惯例同 TestXunziSpecContractLock）；
- 激活开关锁：ACTIVE_PAD_VERSION ∈ {"v01","v11"} 且默认 "v01"；
  切换后判定引用的阈值随之变化（同一原始值跨版本判定翻转）；
  非法版本快速失败；v01 触发态线性映射后在 v11 下同样触发；
- 8 脾气判定正确性：每脾气 ≥1 正例 + ≥1 负例（v01 激活），
  并核对注册表 predicate 接线与直接函数调用一致；
- 确定性：同状态多次判定全等；
- 零第三方依赖：sys.modules 黑名单 + AST import 白名单；
- 正交纪律：8 判定函数体零浮点字面量（阈值必须引用分区表常量）；
- 与 inner_state 现状一致性：默认基线态零掩码触发（负向脾气不误触，
  任务书硬要求）、v01 阈值全部落在 [0,1] clamp 值域内、单次六情
  事件不跳变负向脾气分区（1/6 小步进设计的真实语义；安静带属温和
  带型掩码，单次怒事件把嗜睡基线唤醒入带属规范带语义，豁免见用例）。

反内卷纪律：边界用例断言"两版本行为一致 + 规范语义"（契约锁），
不写防御性数字带锁。
"""

import ast
import sys
import unittest
from contextlib import contextmanager

import engine.temperament_table as tt
from engine.inner_state import InnerState, XUNZI_EMOTION_VECTORS
from engine.temperament_table import (
    PAD_PARTITION_V01,
    PAD_PARTITION_V11,
    TEMPERAMENT_TABLE,
    v01_to_v11,
    v11_to_v01,
)


# ---------------------------------------------------------------------- #
# 测试脚手架
# ---------------------------------------------------------------------- #
@contextmanager
def _activate(version):
    """临时切换激活版本，结束后恢复默认 "v01"。"""
    tt.ACTIVE_PAD_VERSION = version
    try:
        yield
    finally:
        tt.ACTIVE_PAD_VERSION = "v01"


def make_state(e_P=0.5, e_A=0.3, e_D=0.5):
    """构造测试用 InnerState（未给维取类默认，生理维不动）。"""
    return InnerState(e_P=e_P, e_A=e_A, e_D=e_D)


def map_state_to_v11(state):
    """把 v01 状态的 PAD 三维仿射映射为同语义 v11 状态（生理维不动）。"""
    return InnerState(
        p_fatigue=state.p_fatigue, p_hunger=state.p_hunger,
        p_pain=state.p_pain, p_drive=state.p_drive,
        e_P=v01_to_v11(state.e_P), e_A=v01_to_v11(state.e_A),
        e_D=v01_to_v11(state.e_D), S_stress=state.S_stress,
    )


# docs/NPC_TAG_DATABASE.md 2.4 节 8 脾气 PAD 分区触发条件（[-1,1] 值域，
# 硬编码进测试作契约锁源；"mid" 为中性点锚，不计入规范条件）。
SPEC_2_4_V11 = {
    "e_P": {
        "joy_floor": 0.4,         # 喜悦 e_P > 0.4
        "reverent_floor": 0.0,    # 恭顺 e_P ≥ 0.0
        "negative_ceil": -0.2,    # 暴躁/惊恐/惭辱 e_P < -0.2
        "grief_ceil": -0.3,       # 哀戚 e_P < -0.3
    },
    "e_A": {
        "serene_low": -0.25,      # 安静 |e_A| ≤ 0.25
        "serene_high": 0.25,
        "joy_floor": 0.3,         # 喜悦 e_A > 0.3
        "wrath_floor": 0.4,       # 暴躁 e_A > 0.4
        "fear_floor": 0.5,        # 惊恐 e_A > 0.5
        "grief_ceil": 0.1,        # 哀戚 e_A < 0.1
    },
    "e_D": {
        "wrath_floor": 0.2,        # 暴躁 e_D > 0.2
        "arrogant_floor": 0.5,     # 傲慢 e_D > 0.5
        "nonpositive_ceil": 0.0,   # 哀戚/恭顺 e_D < 0.0
        "fear_ceil": -0.3,        # 惊恐 e_D < -0.3
        "ashamed_ceil": -0.2,     # 惭辱 e_D < -0.2
    },
}

# 规范 7.2 节 8 大显性脾气（temp_id → 中文名）。
SPEC_7_2_NAMES = {
    "TEMP_SERENE": "安静",
    "TEMP_JOYFUL": "喜悦",
    "TEMP_WRATHFUL": "暴躁",
    "TEMP_MELANCHOLIC": "哀戚",
    "TEMP_FEARFUL": "惊恐",
    "TEMP_REVERENT": "恭顺",
    "TEMP_ARROGANT": "傲慢",
    "TEMP_ASHAMED": "惭辱",
}

# 边界等价用例：每脾气 (e_P, e_A, e_D) 的 v01 状态 → 规范语义期望。
# 期望值来自规范 2.4 的比较算子（> / < 严格，≥ / ≤ 含端点）：
# 恰在阈值上的状态锁的是算子语义，过阈/未过阈状态锁的是阈值位置。
BOUNDARY_CASES = {
    "TEMP_SERENE": [
        (0.5, 0.625, 0.5, True),   # 安静带上沿（≤ 含端点）
        (0.5, 0.375, 0.5, True),   # 安静带下沿（≤ 含端点）
        (0.5, 0.63, 0.5, False),   # 上沿之外
        (0.5, 0.37, 0.5, False),   # 下沿之外
        (0.5, 0.5, 0.5, True),     # 带中心
    ],
    "TEMP_JOYFUL": [
        (0.7, 0.8, 0.5, False),    # e_P 恰在阈值（> 严格，不触发）
        (0.71, 0.8, 0.5, True),    # e_P 过阈
        (0.8, 0.65, 0.5, False),   # e_A 恰在阈值
        (0.8, 0.66, 0.5, True),    # e_A 过阈
        (0.8, 0.8, 0.5, True),     # 双维过阈
    ],
    "TEMP_WRATHFUL": [
        (0.4, 0.8, 0.7, False),    # e_P 恰在 negative_ceil
        (0.39, 0.8, 0.7, True),
        (0.3, 0.7, 0.7, False),    # e_A 恰在 wrath_floor
        (0.3, 0.71, 0.7, True),
        (0.3, 0.8, 0.6, False),    # e_D 恰在 wrath_floor
        (0.3, 0.8, 0.61, True),
    ],
    "TEMP_MELANCHOLIC": [
        (0.35, 0.4, 0.4, False),   # e_P 恰在 grief_ceil
        (0.34, 0.4, 0.4, True),
        (0.3, 0.55, 0.4, False),   # e_A 恰在 grief_ceil
        (0.3, 0.54, 0.4, True),
        (0.3, 0.4, 0.5, False),    # e_D 恰在 nonpositive_ceil
        (0.3, 0.4, 0.49, True),
    ],
    "TEMP_FEARFUL": [
        (0.4, 0.8, 0.3, False),    # e_P 恰在 negative_ceil
        (0.39, 0.8, 0.3, True),
        (0.3, 0.75, 0.3, False),   # e_A 恰在 fear_floor
        (0.3, 0.76, 0.3, True),
        (0.3, 0.8, 0.35, False),   # e_D 恰在 fear_ceil
        (0.3, 0.8, 0.34, True),
    ],
    "TEMP_REVERENT": [
        (0.5, 0.5, 0.4, True),      # e_P 恰在 reverent_floor（≥ 含端点）
        (0.49, 0.5, 0.4, False),
        (0.6, 0.5, 0.5, False),     # e_D 恰在 nonpositive_ceil（< 严格）
        (0.6, 0.5, 0.49, True),
    ],
    "TEMP_ARROGANT": [
        (0.5, 0.5, 0.75, False),    # e_D 恰在 arrogant_floor
        (0.5, 0.5, 0.76, True),
        (0.5, 0.5, 0.9, True),
    ],
    "TEMP_ASHAMED": [
        (0.4, 0.5, 0.3, False),     # e_P 恰在 negative_ceil
        (0.39, 0.5, 0.3, True),
        (0.3, 0.5, 0.4, False),     # e_D 恰在 ashamed_ceil
        (0.3, 0.5, 0.39, True),
    ],
}


# ---------------------------------------------------------------------- #
# 1. 双表齐全性锁 + 注册表规范全等
# ---------------------------------------------------------------------- #
class TestRegistryAndTableCompleteness(unittest.TestCase):
    def test_registry_matches_spec_7_2(self):
        self.assertEqual(set(TEMPERAMENT_TABLE), set(SPEC_7_2_NAMES))
        for temp_id, name_cn in SPEC_7_2_NAMES.items():
            entry = TEMPERAMENT_TABLE[temp_id]
            self.assertEqual(entry.temp_id, temp_id)
            self.assertEqual(entry.name_cn, name_cn)
            self.assertTrue(callable(entry.predicate))
            self.assertTrue(entry.spec_v11.strip(),
                            msg=f"{temp_id} 缺规范溯源字段 spec_v11")

    def test_partition_tables_structure_parity(self):
        # 两套表同维度同键集：任一表单侧增删键即在此抓出
        self.assertEqual(set(PAD_PARTITION_V01), set(PAD_PARTITION_V11))
        for dim in PAD_PARTITION_V11:
            self.assertEqual(set(PAD_PARTITION_V01[dim]),
                             set(PAD_PARTITION_V11[dim]),
                             msg=f"{dim} 两版本键集不一致（双表漂移）")

    def test_all_predicates_evaluable_under_both_versions(self):
        # 8 判定函数在两套激活版本下引用得到全部阈值（无 KeyError=完整定义）
        probe = make_state(0.4, 0.5, 0.4)
        for version in ("v01", "v11"):
            with _activate(version):
                for temp_id, entry in TEMPERAMENT_TABLE.items():
                    self.assertIsInstance(
                        entry.predicate(probe), bool,
                        msg=f"{temp_id} 在 {version} 下不可求值（缺阈值键）")

    def test_predicates_reference_partition_constants_not_literals(self):
        # 正交纪律：8 判定函数体零浮点字面量（阈值必须引用分区表常量）
        with open(tt.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        predicate_names = {e.predicate.__name__
                           for e in TEMPERAMENT_TABLE.values()}
        scanned = set()
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in predicate_names:
                scanned.add(node.name)
                floats = [sub.value for sub in ast.walk(node)
                          if isinstance(sub, ast.Constant)
                          and isinstance(sub.value, float)]
                self.assertEqual(
                    floats, [],
                    msg=f"{node.name} 硬编码浮点阈值，违反分区表引用纪律")
        self.assertEqual(scanned, predicate_names,
                        msg="有判定函数未被扫描到（函数名与注册表不一致）")


# ---------------------------------------------------------------------- #
# 2. 规范契约锁（v11 表 = docs 2.4 硬编码规范表）
# ---------------------------------------------------------------------- #
class TestSpecContractLock(unittest.TestCase):
    def test_v11_thresholds_match_spec_2_4(self):
        for dim, bands in SPEC_2_4_V11.items():
            self.assertEqual(
                set(PAD_PARTITION_V11[dim]), set(bands) | {"mid"},
                msg=f"{dim} 键集应恰为规范条件键 + mid 中性锚")
            for key, value in bands.items():
                self.assertEqual(
                    PAD_PARTITION_V11[dim][key], value,
                    msg=f"{dim}.{key} 偏离 docs 2.4 规范阈值 {value}")
        for dim in PAD_PARTITION_V11:  # v11 中性点锚
            self.assertEqual(PAD_PARTITION_V11[dim]["mid"], 0.0)


# ---------------------------------------------------------------------- #
# 3. 语义等价锁（表级）：v01 表 = v11 表的仿射像
# ---------------------------------------------------------------------- #
class TestAffineMappingLock(unittest.TestCase):
    def test_v01_table_is_affine_image_of_v11(self):
        for dim in PAD_PARTITION_V11:
            for key, v11_value in PAD_PARTITION_V11[dim].items():
                self.assertEqual(
                    PAD_PARTITION_V01[dim][key], v11_to_v01(v11_value),
                    msg=(f"{dim}.{key} 不满足线性映射 t_01=(t_11+1)/2："
                         f"v01={PAD_PARTITION_V01[dim][key]}，"
                         f"应={v11_to_v01(v11_value)}（v11={v11_value}）"))

    def test_mapping_helpers_roundtrip_on_all_table_values(self):
        for dim in PAD_PARTITION_V01:
            for key, v01_value in PAD_PARTITION_V01[dim].items():
                self.assertEqual(v11_to_v01(v01_to_v11(v01_value)), v01_value,
                                 msg=f"{dim}.{key} v01 往返映射不还原")
        for dim in PAD_PARTITION_V11:
            for key, v11_value in PAD_PARTITION_V11[dim].items():
                self.assertEqual(v01_to_v11(v11_to_v01(v11_value)), v11_value,
                                 msg=f"{dim}.{key} v11 往返映射不还原")


# ---------------------------------------------------------------------- #
# 4. 语义等价锁（行为级·核心）：每脾气边界状态两版本判定一致
# ---------------------------------------------------------------------- #
class TestSemanticEquivalenceLock(unittest.TestCase):
    """v01 边界状态在 v01 激活下判定 → 仿射映射为同语义 v11 状态在
    v11 激活下判定 → 两者必须都等于规范语义期望。

    任何一套表被单独改动（阈值漂移或比较算子漂移）都会在此炸出，
    与表级仿射全等锁（TestAffineMappingLock）构成双保险。
    """

    def _assert_both_versions(self, temp_id, pad, expected):
        predicate = TEMPERAMENT_TABLE[temp_id].predicate
        state_v01 = make_state(*pad)
        with _activate("v01"):
            self.assertEqual(
                predicate(state_v01), expected,
                msg=f"{temp_id} v01 版判定 {pad} 应为 {expected}")
        state_v11 = map_state_to_v11(state_v01)
        with _activate("v11"):
            self.assertEqual(
                predicate(state_v11), expected,
                msg=(f"{temp_id} v11 版判定映射态 "
                     f"(e_P={state_v11.e_P}, e_A={state_v11.e_A}, "
                     f"e_D={state_v11.e_D}) 应为 {expected}"))

    def test_boundary_equivalence_serene(self):
        for e_P, e_A, e_D, expected in BOUNDARY_CASES["TEMP_SERENE"]:
            with self.subTest(pad=(e_P, e_A, e_D)):
                self._assert_both_versions(
                    "TEMP_SERENE", (e_P, e_A, e_D), expected)

    def test_boundary_equivalence_joyful(self):
        for e_P, e_A, e_D, expected in BOUNDARY_CASES["TEMP_JOYFUL"]:
            with self.subTest(pad=(e_P, e_A, e_D)):
                self._assert_both_versions(
                    "TEMP_JOYFUL", (e_P, e_A, e_D), expected)

    def test_boundary_equivalence_wrathful(self):
        for e_P, e_A, e_D, expected in BOUNDARY_CASES["TEMP_WRATHFUL"]:
            with self.subTest(pad=(e_P, e_A, e_D)):
                self._assert_both_versions(
                    "TEMP_WRATHFUL", (e_P, e_A, e_D), expected)

    def test_boundary_equivalence_melancholic(self):
        for e_P, e_A, e_D, expected in BOUNDARY_CASES["TEMP_MELANCHOLIC"]:
            with self.subTest(pad=(e_P, e_A, e_D)):
                self._assert_both_versions(
                    "TEMP_MELANCHOLIC", (e_P, e_A, e_D), expected)

    def test_boundary_equivalence_fearful(self):
        for e_P, e_A, e_D, expected in BOUNDARY_CASES["TEMP_FEARFUL"]:
            with self.subTest(pad=(e_P, e_A, e_D)):
                self._assert_both_versions(
                    "TEMP_FEARFUL", (e_P, e_A, e_D), expected)

    def test_boundary_equivalence_reverent(self):
        for e_P, e_A, e_D, expected in BOUNDARY_CASES["TEMP_REVERENT"]:
            with self.subTest(pad=(e_P, e_A, e_D)):
                self._assert_both_versions(
                    "TEMP_REVERENT", (e_P, e_A, e_D), expected)

    def test_boundary_equivalence_arrogant(self):
        for e_P, e_A, e_D, expected in BOUNDARY_CASES["TEMP_ARROGANT"]:
            with self.subTest(pad=(e_P, e_A, e_D)):
                self._assert_both_versions(
                    "TEMP_ARROGANT", (e_P, e_A, e_D), expected)

    def test_boundary_equivalence_ashamed(self):
        for e_P, e_A, e_D, expected in BOUNDARY_CASES["TEMP_ASHAMED"]:
            with self.subTest(pad=(e_P, e_A, e_D)):
                self._assert_both_versions(
                    "TEMP_ASHAMED", (e_P, e_A, e_D), expected)


# ---------------------------------------------------------------------- #
# 5. 激活开关锁
# ---------------------------------------------------------------------- #
class TestActiveVersionSwitch(unittest.TestCase):
    def test_active_version_valid_and_defaults_to_v01(self):
        self.assertIn(tt.ACTIVE_PAD_VERSION, ("v01", "v11"))
        self.assertEqual(
            tt.ACTIVE_PAD_VERSION, "v01",
            msg="默认激活版必须与 inner_state.py [0,1] 现实现一致")

    def test_switch_flips_threshold_resolution(self):
        # 同一原始值 e_D=0.7：v01 下未过傲慢阈（需 >0.75），切 v11 后
        # 过阈（需 >0.5）——证明判定函数引用的阈值随开关切换而变化
        state = make_state(e_D=0.7)
        with _activate("v01"):
            self.assertFalse(tt.is_arrogant(state))
        with _activate("v11"):
            self.assertTrue(tt.is_arrogant(state))
        # 恢复默认后回到 v01 行为
        self.assertFalse(tt.is_arrogant(state))

    def test_mapped_trigger_state_agrees_across_versions(self):
        # 任务书口径：v01 下触发的状态，线性映射后在 v11 下同样触发
        state = make_state(e_P=0.8, e_A=0.8)  # 喜悦触发态
        with _activate("v01"):
            self.assertTrue(tt.is_joyful(state))
        with _activate("v11"):
            self.assertTrue(tt.is_joyful(map_state_to_v11(state)))

    def test_active_partition_rejects_unknown_version(self):
        with _activate("v99"):
            with self.assertRaises(ValueError):
                tt.active_partition()


# ---------------------------------------------------------------------- #
# 6. 8 脾气判定正确性（v01 激活）：每脾气 ≥1 正例 + ≥1 负例
# ---------------------------------------------------------------------- #
class TestTemperamentDetectionV01(unittest.TestCase):
    def _check(self, temp_id, fn, positive, negative):
        for pad in positive:
            self.assertTrue(fn(make_state(*pad)),
                            msg=f"{temp_id} 正例未触发: {pad}")
        for pad in negative:
            self.assertFalse(fn(make_state(*pad)),
                             msg=f"{temp_id} 负例误触发: {pad}")
        # 注册表 predicate 接线与直接函数调用一致
        entry = TEMPERAMENT_TABLE[temp_id]
        self.assertTrue(entry.predicate(make_state(*positive[0])))
        self.assertFalse(entry.predicate(make_state(*negative[0])))

    def test_serene_detection(self):
        self._check("TEMP_SERENE", tt.is_serene,
                    positive=[(0.5, 0.5, 0.5)],       # 带中心：平静
                    negative=[(0.5, 0.9, 0.5),        # 高唤醒
                              (0.5, 0.1, 0.5)])       # 深睡态（带外）

    def test_joyful_detection(self):
        self._check("TEMP_JOYFUL", tt.is_joyful,
                    positive=[(0.8, 0.7, 0.5)],       # 正愉悦+中高唤醒
                    negative=[(0.8, 0.6, 0.5),        # e_A 不足
                              (0.6, 0.8, 0.5)])       # e_P 不足

    def test_wrathful_detection(self):
        self._check("TEMP_WRATHFUL", tt.is_wrathful,
                    positive=[(0.3, 0.8, 0.7)],       # 怒象限 -P+A+D
                    negative=[(0.5, 0.8, 0.7),        # e_P 未跌入低带
                              (0.3, 0.6, 0.7),        # e_A 不足
                              (0.3, 0.8, 0.5)])       # e_D 不足

    def test_melancholic_detection(self):
        self._check("TEMP_MELANCHOLIC", tt.is_melancholic,
                    positive=[(0.3, 0.4, 0.4)],      # 退缩象限 -P-A-D
                    negative=[(0.5, 0.4, 0.4),       # e_P 不足
                              (0.3, 0.6, 0.4),       # e_A 未落低带
                              (0.3, 0.4, 0.6)])       # e_D 不足

    def test_fearful_detection(self):
        self._check("TEMP_FEARFUL", tt.is_fearful,
                    positive=[(0.3, 0.8, 0.3)],       # 惧象限 -P+A-D
                    negative=[(0.5, 0.8, 0.3),       # e_P 不足
                              (0.3, 0.7, 0.3),       # e_A 不足
                              (0.3, 0.8, 0.5)])       # e_D 未跌入极低带

    def test_reverent_detection(self):
        self._check("TEMP_REVERENT", tt.is_reverent,
                    positive=[(0.6, 0.5, 0.4)],       # 驯服信赖 +P-D
                    negative=[(0.4, 0.5, 0.4),       # e_P 跌破非负带
                              (0.6, 0.5, 0.6)])       # e_D 不足

    def test_arrogant_detection(self):
        self._check("TEMP_ARROGANT", tt.is_arrogant,
                    positive=[(0.5, 0.5, 0.8)],       # 高支配
                    negative=[(0.5, 0.5, 0.7)])       # 支配未过阈

    def test_ashamed_detection(self):
        self._check("TEMP_ASHAMED", tt.is_ashamed,
                    positive=[(0.3, 0.5, 0.3)],       # 羞耻象限 -P-D
                    negative=[(0.5, 0.5, 0.3),        # e_P 不足
                              (0.3, 0.5, 0.5)])       # e_D 不足


# ---------------------------------------------------------------------- #
# 7. 确定性
# ---------------------------------------------------------------------- #
class TestDeterminism(unittest.TestCase):
    def test_repeated_evaluation_stable(self):
        # 多脾气边界混合态：同状态重复判定恒定（掩码判定无随机性）
        state = make_state(0.35, 0.65, 0.45)
        for temp_id, entry in TEMPERAMENT_TABLE.items():
            first = entry.predicate(state)
            for _ in range(5):
                self.assertEqual(entry.predicate(state), first,
                                 msg=f"{temp_id} 重复判定不稳定")


# ---------------------------------------------------------------------- #
# 8. 零第三方依赖
# ---------------------------------------------------------------------- #
class TestZeroThirdPartyDependency(unittest.TestCase):
    def _source(self):
        with open(tt.__file__, encoding="utf-8") as fh:
            return fh.read()

    def test_no_third_party_package_loaded(self):
        # sys.modules 检查：temperament_table 及其依赖链未加载任何第三方包
        for name in ("numpy", "scipy", "pandas", "torch", "sklearn",
                     "matplotlib"):
            self.assertNotIn(name, sys.modules, msg=f"第三方包 {name} 被加载")

    def test_imports_are_stdlib_or_project_only(self):
        # AST 检查：模块内所有 import 的顶层名都在标准库/项目白名单
        tree = ast.parse(self._source())
        allowed_top = {"dataclasses", "typing", "__future__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], allowed_top,
                                  msg=f"越权 import: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                top = (node.module or "").split(".")[0]
                self.assertTrue(node.level > 0 or top in allowed_top,
                                msg=f"越权 import from: {node.module}")


# ---------------------------------------------------------------------- #
# 9. 与 inner_state.py 现状一致性（[0,1] 值域 + 默认值 + 小步进动态）
# ---------------------------------------------------------------------- #
class TestInnerStateCompatibility(unittest.TestCase):
    def test_default_baseline_triggers_no_mask(self):
        # 任务书硬要求：默认 e_P=0.5 不误触发任何负向脾气；
        # 设计契约：基线平静态不触发任何显性脾气（掩码是偏离驱动）
        state = InnerState()
        for temp_id in ("TEMP_WRATHFUL", "TEMP_MELANCHOLIC",
                        "TEMP_FEARFUL", "TEMP_ASHAMED"):
            self.assertFalse(
                TEMPERAMENT_TABLE[temp_id].predicate(state),
                msg=f"默认基线态误触发负向脾气 {temp_id}")
        for temp_id, entry in TEMPERAMENT_TABLE.items():
            self.assertFalse(entry.predicate(state),
                             msg=f"默认基线态误触发 {temp_id}")

    def test_v01_thresholds_within_inner_state_domain(self):
        # v01 阈值须全部落在 inner_state [0,1] clamp 值域内才可用
        for dim, bands in PAD_PARTITION_V01.items():
            for key, value in bands.items():
                self.assertTrue(0.0 <= value <= 1.0,
                                msg=f"v01 阈值 {dim}.{key}={value} 越出 [0,1]")

    def test_single_xunzi_event_flips_no_negative_mask(self):
        # 1/6 小步进设计语义：默认基线上单次六情事件（任何一支）不足以
        # 把 NPC 推进任何负向脾气分区——负向脾气（暴躁/哀戚/惊恐/惭辱）
        # 全部要求 e_P 跌入低带，单事件 e_P 步进 ≤0.08，从 0.5 最低到
        # 0.42，须多事件叠加逼近，与 inner_state "单事件不跳变满格"
        # 的缩放设计自洽。
        #
        # 安静带豁免（书面记录）：TEMP_SERENE 是带型温和掩码（唤醒维
        # [serene_low, serene_high]），inner_state 默认 e_A=0.3 本就在
        # 带下沿之外（嗜睡态），单次"怒"（e_A +0.10）把唤醒推至 0.40
        # 落入带内即触发——这是规范 2.4 |e_A| ≤ 0.25 带状条件的忠实
        # 行为（被轻搅醒后镇定应对），非双表设计缺陷，故不在本契约内。
        negative_ids = ("TEMP_WRATHFUL", "TEMP_MELANCHOLIC",
                        "TEMP_FEARFUL", "TEMP_ASHAMED")
        for emotion, vector in XUNZI_EMOTION_VECTORS.items():
            state = InnerState()
            state.apply_delta("e_P", vector[0])
            state.apply_delta("e_A", vector[1])
            state.apply_delta("e_D", vector[2])
            for temp_id in negative_ids:
                self.assertFalse(
                    TEMPERAMENT_TABLE[temp_id].predicate(state),
                    msg=(f"单次六情事件 '{emotion}'（{vector}）即触发负向 "
                         f"脾气 {temp_id}，违背小步进叠加设计"))


if __name__ == "__main__":
    unittest.main()

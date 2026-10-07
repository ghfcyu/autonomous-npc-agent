"""T2 标签库首批 · engine/tag_genesis.py 单元测试（先红后绿之"红"）。

被测模块：engine/tag_genesis.py（先天属性创生模块，由并行子代理 A 实现）。
本文件由子代理 B 独立编写，只 import PM 契约定的公开 API，与实现内部结构解耦。

分布断言依据 docs/NPC_TAG_DATABASE.md：
- §2.1  6D 先天正态量化属性矩阵（X ~ N(5.0, 1.25^2)，截断于 [1.0, 10.0]）
- §3.1  离散特质金字塔阶梯频度法则（common/uncommon/rare/extreme = 70/20/8/2）
- §3.2  齐普夫幂律抽样（s≈1.15）
- §4.1  连续量化标签的正态分布数学模型（美貌六档、属性四档）
- §2.5 / §7.2  尧谷子 8 处事风格原型 × 4 阶执念深度（70/20/8/2）

偏离记录：alcohol_tol σ=1.5（其余属性 1.25）与财富对数正态参数化
（WEALTH_LOG_MU / WEALTH_LOG_SIGMA）的偏离说明见 engine/tag_genesis.py 模块 docstring。

统计带宽严格按任务书给定值执行（N=5000/10000/20000 下均留有 ≥4σ 安全余量），
不因实现凑绿而放宽，也不无故收紧到统计不稳定区间。
"""

import math
import random
import statistics
import unittest
from collections import Counter

import engine.tag_genesis as tag_genesis_module
from engine.tag_genesis import (
    ATTRIBUTE_RANGE,
    INNATE_ATTRIBUTE_SPECS,
    PYRAMID_TIERS,
    WEALTH_LOG_MU,
    WEALTH_LOG_SIGMA,
    YAO_INTENSITY_TIERS,
    YAO_STYLE_ARCHETYPES,
    InnateAttributes,
    attribute_label,
    beauty_label,
    generate_innate_attributes,
    sample_pyramid_tier,
    sample_yao_style,
    sample_zipf_rank,
    zipf_probabilities,
)


# ========================================================================== #
# 1. 先天 6D 属性：截断正态分布（docs §2.1）
# ========================================================================== #
class TestInnateDistribution(unittest.TestCase):
    """N=5000, seed=42：均值 5.0、σ 1.25（alcohol_tol 1.5）、值域 [1,10]。"""

    N = 5000
    FLOAT_ATTRS = ("beauty", "strength", "savvy", "courage", "morality", "alcohol_tol")

    @classmethod
    def setUpClass(cls):
        rng = random.Random(42)
        cls.samples = [generate_innate_attributes(rng) for _ in range(cls.N)]

    def test_all_values_within_attribute_range(self):
        for s in self.samples:
            for attr in self.FLOAT_ATTRS:
                v = getattr(s, attr)
                self.assertGreaterEqual(v, ATTRIBUTE_RANGE[0], msg=f"{attr}={v}")
                self.assertLessEqual(v, ATTRIBUTE_RANGE[1], msg=f"{attr}={v}")

    def test_means_centered_at_five(self):
        for attr in self.FLOAT_ATTRS:
            mean = statistics.fmean(getattr(s, attr) for s in self.samples)
            self.assertLess(abs(mean - 5.0), 0.12, msg=f"{attr} mean={mean:.4f}")

    def test_sample_standard_deviations(self):
        for attr in self.FLOAT_ATTRS:
            values = [getattr(s, attr) for s in self.samples]
            sd = statistics.stdev(values)
            target, band = (1.5, 0.08) if attr == "alcohol_tol" else (1.25, 0.06)
            self.assertLess(abs(sd - target), band, msg=f"{attr} sd={sd:.4f}")

    def test_beauty_one_sigma_band_share(self):
        inside = sum(1 for s in self.samples if 3.75 <= s.beauty <= 6.25)
        share = inside / self.N
        self.assertGreaterEqual(share, 0.64, msg=f"share={share:.4f}")
        self.assertLessEqual(share, 0.72, msg=f"share={share:.4f}")


# ========================================================================== #
# 2. 财富：对数正态（铜板，≥1，int）
# ========================================================================== #
class TestWealthDistribution(unittest.TestCase):
    """N=5000, seed=7：中位数、核心带占比、P95 与最大值。"""

    N = 5000

    @classmethod
    def setUpClass(cls):
        rng = random.Random(7)
        cls.wealths = [generate_innate_attributes(rng).wealth for _ in range(cls.N)]

    def test_wealth_integers_at_least_one(self):
        for w in self.wealths:
            self.assertIsInstance(w, int)
            self.assertNotIsInstance(w, bool)
            self.assertGreaterEqual(w, 1)

    def test_wealth_median_band(self):
        median = statistics.median(self.wealths)
        self.assertGreaterEqual(median, 20, msg=f"median={median}")
        self.assertLessEqual(median, 26, msg=f"median={median}")

    def test_wealth_core_band_share(self):
        inside = sum(1 for w in self.wealths if 10 <= w <= 50)
        self.assertGreaterEqual(inside / self.N, 0.85, msg=f"share={inside / self.N:.4f}")

    def test_wealth_p95_and_max(self):
        ordered = sorted(self.wealths)
        p95 = ordered[4749]  # 排序后第 4750 个
        self.assertGreaterEqual(p95, 40, msg=f"p95={p95}")
        self.assertGreaterEqual(ordered[-1], 50, msg=f"max={ordered[-1]}")


# ========================================================================== #
# 3. 特质金字塔：70/20/8/2（docs §3.1）
# ========================================================================== #
class TestPyramidDistribution(unittest.TestCase):
    """N=10000, seed=11：四档占比带宽。"""

    N = 10000

    @classmethod
    def setUpClass(cls):
        rng = random.Random(11)
        cls.tiers = [sample_pyramid_tier(rng) for _ in range(cls.N)]

    def test_pyramid_tier_shares(self):
        counts = Counter(self.tiers)
        self.assertGreaterEqual(counts["common"] / self.N, 0.675)
        self.assertLessEqual(counts["common"] / self.N, 0.725)
        self.assertGreaterEqual(counts["uncommon"] / self.N, 0.18)
        self.assertLessEqual(counts["uncommon"] / self.N, 0.22)
        self.assertGreaterEqual(counts["rare"] / self.N, 0.065)
        self.assertLessEqual(counts["rare"] / self.N, 0.095)
        self.assertGreaterEqual(counts["extreme"] / self.N, 0.01)
        self.assertLessEqual(counts["extreme"] / self.N, 0.03)
        # 只允许四种合法档位
        self.assertEqual(set(counts), {"common", "uncommon", "rare", "extreme"})


# ========================================================================== #
# 4. 尧谷子风格：8 风格 × 4 阶（docs §2.5 / §7.2）
# ========================================================================== #
class TestYaoStyleDistribution(unittest.TestCase):
    """N=5000, seed=13：风格均衡、32 组合全覆盖、四阶 70/20/8/2。"""

    N = 5000

    @classmethod
    def setUpClass(cls):
        rng = random.Random(13)
        cls.pairs = [sample_yao_style(rng) for _ in range(cls.N)]

    def test_all_eight_styles_present_and_balanced(self):
        counts = Counter(pair[0] for pair in self.pairs)
        self.assertEqual(set(counts), set(YAO_STYLE_ARCHETYPES))
        for style in YAO_STYLE_ARCHETYPES:
            share = counts.get(style, 0) / self.N
            self.assertGreaterEqual(share, 0.08, msg=f"{style} share={share:.4f}")
            self.assertLessEqual(share, 0.17, msg=f"{style} share={share:.4f}")

    def test_all_32_style_tier_combinations_present(self):
        combos = {tuple(pair) for pair in self.pairs}
        expected = {
            (style, tier)
            for style in YAO_STYLE_ARCHETYPES
            for tier in YAO_INTENSITY_TIERS
        }
        self.assertEqual(combos, expected)

    def test_intensity_tier_shares(self):
        counts = Counter(pair[1] for pair in self.pairs)

        def share(tier):
            return counts.get(tier, 0) / self.N

        self.assertGreaterEqual(share("萌芽"), 0.67)
        self.assertLessEqual(share("萌芽"), 0.73)
        self.assertGreaterEqual(share("显性"), 0.17)
        self.assertLessEqual(share("显性"), 0.23)
        self.assertGreaterEqual(share("执念"), 0.05)
        self.assertLessEqual(share("执念"), 0.11)
        self.assertGreaterEqual(share("病态"), 0.005)
        self.assertLessEqual(share("病态"), 0.035)
        self.assertEqual(set(counts), set(YAO_INTENSITY_TIERS))


# ========================================================================== #
# 5. 齐普夫幂律（docs §3.2，s≈1.15）
# ========================================================================== #
class TestZipf(unittest.TestCase):
    def test_zipf_probabilities_normalized_and_strictly_decreasing(self):
        probs = zipf_probabilities(10)
        self.assertEqual(len(probs), 10)
        self.assertLess(abs(sum(probs) - 1.0), 1e-9, msg=f"sum={sum(probs)!r}")
        for i in range(len(probs) - 1):
            self.assertGreater(probs[i], probs[i + 1], msg=f"i={i}")

    def test_rank1_probability_band(self):
        probs = zipf_probabilities(10)
        self.assertGreater(probs[0], 0.25, msg=f"p1={probs[0]:.4f}")
        self.assertLess(probs[0], 0.40, msg=f"p1={probs[0]:.4f}")

    def test_sample_zipf_rank_within_bounds(self):
        rng = random.Random(3)
        for _ in range(2000):
            rank = sample_zipf_rank(rng, 10)
            self.assertGreaterEqual(rank, 1)
            self.assertLessEqual(rank, 10)

    def test_sample_zipf_rank_frequency_matches_theory(self):
        rng = random.Random(17)
        n, total = 10, 20000
        counts = [0] * n
        for _ in range(total):
            counts[sample_zipf_rank(rng, n) - 1] += 1
        probs = zipf_probabilities(n)
        for k in range(n):
            freq = counts[k] / total
            self.assertLess(abs(freq - probs[k]), 0.03, msg=f"rank={k + 1} freq={freq:.4f} p={probs[k]:.4f}")


# ========================================================================== #
# 6. 标签文案：美貌六档 / 属性四档（docs §4.1）
# ========================================================================== #
class TestLabels(unittest.TestCase):
    def test_beauty_label_boundaries(self):
        cases = [
            (9.5, "倾国倾城"),
            (8.499, "清秀端正"),  # <8.5 落入 ≥7.0 档
            (8.5, "惊艳俊美"),
            (7.0, "清秀端正"),
            (6.9, "相貌平平"),
            (4.0, "相貌平平"),
            (2.5, "粗陋枯槁"),
            (2.499, "面目可怖"),
            (1.0, "面目可怖"),
        ]
        for value, expected in cases:
            self.assertEqual(beauty_label(value), expected, msg=f"beauty={value}")

    def test_attribute_label_four_tier_boundaries(self):
        # 四档界 8.75 / 7.5 / 2.5：边界值归属上档（≥ 含边界），同档同文案、跨档异文案
        for attr_id in ("strength", "courage"):
            top = attribute_label(attr_id, 10.0)
            self.assertEqual(attribute_label(attr_id, 8.75), top, msg=f"{attr_id} 8.75")
            second = attribute_label(attr_id, 8.7)
            self.assertEqual(attribute_label(attr_id, 7.5), second, msg=f"{attr_id} 7.5")
            third = attribute_label(attr_id, 5.0)
            self.assertEqual(attribute_label(attr_id, 2.5), third, msg=f"{attr_id} 2.5")
            bottom = attribute_label(attr_id, 2.4)
            self.assertEqual(attribute_label(attr_id, 1.0), bottom, msg=f"{attr_id} 1.0")
            self.assertEqual(len({top, second, third, bottom}), 4, msg=attr_id)
            for label in (top, second, third, bottom):
                self.assertIsInstance(label, str)
                self.assertTrue(label)

    def test_attribute_label_beauty_and_unknown_raise(self):
        with self.assertRaises(ValueError):
            attribute_label("beauty", 5.0)
        with self.assertRaises(ValueError):
            attribute_label("no_such_attribute", 5.0)

    def test_labels_are_nonempty_strings(self):
        for value in (9.9, 8.9, 7.2, 5.0, 3.0, 1.2):
            label = beauty_label(value)
            self.assertIsInstance(label, str)
            self.assertTrue(label)


# ========================================================================== #
# 7. 确定性：同 seed 同输出
# ========================================================================== #
class TestDeterminism(unittest.TestCase):
    def test_same_seed_same_innate_attributes(self):
        a = generate_innate_attributes(random.Random(99)).to_dict()
        b = generate_innate_attributes(random.Random(99)).to_dict()
        self.assertEqual(a, b)

    def test_same_seed_same_yao_style_sequence(self):
        rng_a = random.Random(99)
        seq_a = [sample_yao_style(rng_a) for _ in range(100)]
        rng_b = random.Random(99)
        seq_b = [sample_yao_style(rng_b) for _ in range(100)]
        self.assertEqual(seq_a, seq_b)


# ========================================================================== #
# 8. 规范契约锁：把 docs 规范参数硬编码进测试
# ========================================================================== #
class TestSpecContractLock(unittest.TestCase):
    SPEC_KEYS = {"beauty", "strength", "savvy", "courage", "morality", "alcohol_tol"}
    SPEC_YAO_STYLES = ["随和", "较真", "进取", "谨慎", "给予", "主导", "反叛", "孤僻"]

    def test_attribute_specs_keys_and_parameters(self):
        self.assertEqual(set(INNATE_ATTRIBUTE_SPECS), self.SPEC_KEYS)
        for attr_id, spec in INNATE_ATTRIBUTE_SPECS.items():
            self.assertEqual(len(spec), 3, msg=attr_id)
            name_cn, mu, sigma = spec
            self.assertIsInstance(name_cn, str)
            self.assertTrue(name_cn, msg=attr_id)
            self.assertEqual(mu, 5.0, msg=attr_id)
            if attr_id == "alcohol_tol":
                self.assertEqual(sigma, 1.5, msg=attr_id)
            else:
                self.assertEqual(sigma, 1.25, msg=attr_id)

    def test_module_docstring_records_sigma_deviation(self):
        doc = tag_genesis_module.__doc__
        self.assertIsNotNone(doc)
        self.assertTrue(("σ=1.5" in doc) or ("1.5" in doc))

    def test_attribute_range_locked(self):
        self.assertEqual(ATTRIBUTE_RANGE, (1.0, 10.0))

    def test_pyramid_tiers_locked(self):
        self.assertEqual(len(PYRAMID_TIERS), 4)
        for tier in PYRAMID_TIERS:
            self.assertEqual(len(tier), 3)
            self.assertIsInstance(tier[1], str)
            self.assertTrue(tier[1])
        self.assertEqual([t[0] for t in PYRAMID_TIERS], ["common", "uncommon", "rare", "extreme"])
        probs = [t[2] for t in PYRAMID_TIERS]
        self.assertEqual(probs, [0.70, 0.20, 0.08, 0.02])
        self.assertLess(abs(sum(probs) - 1.0), 1e-12)

    def test_yao_style_archetypes_locked(self):
        self.assertEqual(YAO_STYLE_ARCHETYPES, self.SPEC_YAO_STYLES)

    def test_yao_intensity_tiers_locked(self):
        self.assertEqual(YAO_INTENSITY_TIERS, ["萌芽", "显性", "执念", "病态"])

    def test_wealth_log_parameters_locked(self):
        self.assertLess(abs(WEALTH_LOG_MU - math.log(500) / 2), 1e-12)
        self.assertLess(abs(WEALTH_LOG_SIGMA - math.log(5) / (2 * 1.6449)), 1e-12)

    def test_source_has_no_third_party_dependencies(self):
        with open(tag_genesis_module.__file__, encoding="utf-8") as fh:
            source = fh.read()
        for banned in ("numpy", "scipy", "pandas"):
            self.assertNotIn(banned, source)


# ========================================================================== #
# 9. InnateAttributes 数据类
# ========================================================================== #
class TestInnateAttributesDataclass(unittest.TestCase):
    FIELD_NAMES = {"beauty", "strength", "savvy", "courage", "morality", "alcohol_tol", "wealth"}

    @classmethod
    def setUpClass(cls):
        cls.attrs = generate_innate_attributes(random.Random(42))

    def test_instance_and_to_dict_contains_all_seven_fields(self):
        self.assertIsInstance(self.attrs, InnateAttributes)
        d = self.attrs.to_dict()
        self.assertEqual(set(d), self.FIELD_NAMES)
        for field in self.FIELD_NAMES - {"wealth"}:
            self.assertIsInstance(d[field], float, msg=field)
        self.assertIsInstance(d["wealth"], int)

    def test_frozen_dataclass_rejects_assignment(self):
        with self.assertRaises(Exception):
            self.attrs.beauty = 9.9
        with self.assertRaises(Exception):
            self.attrs.wealth = 999


if __name__ == "__main__":
    unittest.main()

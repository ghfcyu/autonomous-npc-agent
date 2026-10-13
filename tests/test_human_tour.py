"""scripts/human_simulation_tour.py 正式化验收测试（T4-b 第二小步）。

被测契约（PM 定稿）：
- 报告行动态数字：``背景生态 NPC: {len(engine.background_npcs)} 位背景村民...``
  ——Mock 默认引擎 = 22 位背景村民，不再硬编码 8；
- 机制可见性（玩家可感知口径）：「快脑秒回」至少一次且 journal 中
  brain=="fast" 条目 ≥1、「垫话」（0ms 反应性开场白）、「长期记忆」
  （chen 记忆沉淀）、「0 Token」（背景生态 NPC 全程零消耗）。

import 路径先例：tests/test_dialogue_quality.py 已直接
``from scripts.dialogue_quality import ...``（unittest discover 自项目根
装载，scripts 为命名空间包），本文件沿该先例，不重复 sys.path 注入；
HumanExperienceSimulator 按任务书延迟到 setUpClass 导入，经
contextlib.redirect_stdout 捕获全量 stdout 供断言（整幕游历约 2-3 秒，
类级 setUpClass 只跑一次，可接受）。

Mock 模式无外部依赖：.env 缺失时 scripts/llm_smoke.load_env 文件不
存在直接 return（容错已验证），create_provider("mock") 不触碰任何真实
API，测试确定性可复现。
"""

import contextlib
import io
import os
import unittest


class TestHumanSimulationTour(unittest.TestCase):
    """人类第一人称游历脚本正式化：动态规模数字 + 四机制可见性。"""

    @classmethod
    def setUpClass(cls):
        from scripts.human_simulation_tour import HumanExperienceSimulator
        # .env 加载隔离：模拟器构造经 llm_smoke.load_env 以 setdefault 把
        # .env 写入进程环境——字母序上本类先于 test_llm_provider 运行，
        # 不恢复会污染其后 OpenAICompatProvider 的"无 base_url 不可用"
        # 断言（测试顺序依赖）。此处快照/恢复 os.environ，零残留。
        saved_environ = dict(os.environ)
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                sim = HumanExperienceSimulator(use_real_llm=False)
                sim.run_tour()
        finally:
            os.environ.clear()
            os.environ.update(saved_environ)
        cls.output = buffer.getvalue()
        cls.sim = sim
        cls.journal = sim.journal

    def test_background_count_line_dynamic_not_hardcoded(self):
        # 前置对账：模拟器所持引擎的背景规模（动态数字的真实来源）
        self.assertEqual(len(self.sim.engine.background_npcs), 22,
                         msg="Mock 默认引擎应为 22 位背景村民"
                             "（旧 7 + T4-a 新住民 15，3 核心+22 背景=25 人村庄；"
                             "tavern_sun 升级核心后移出背景层）")
        self.assertIn("22 位背景村民", self.output,
                      msg="报告行应为动态数字：『背景生态 NPC: 22 位背景村民...』"
                          "（取 len(engine.background_npcs)，玩家可感知真实规模）")
        self.assertNotIn("8 位背景村民", self.output,
                          msg="旧硬编码『8 位背景村民』报告行应彻底清除"
                              "（T4-a 扩容后早已是 22 人）")

    def test_fast_brain_takeover_visible(self):
        self.assertIn("快脑秒回", self.output,
                      msg="游历输出应至少一次可见「快脑秒回」"
                          "（玩家可感知的秒级响应体感文案）")
        fast_entries = [j for j in self.journal if j.get("brain") == "fast"]
        self.assertGreaterEqual(len(fast_entries), 1,
                                msg="journal 中 brain==fast 的条目应 ≥1"
                                    "（快脑接管在数据层同样可见——"
                                    "幕 1 打招呼/查价/问路均为高频日常意图）")

    def test_filler_visible(self):
        self.assertIn("垫话", self.output,
                      msg="游历输出应可见「垫话」（0ms 肢体/微表情先手垫话"
                          "机制——幕 6 报告垫话覆盖行）")

    def test_long_term_memory_visible(self):
        self.assertIn("长期记忆", self.output,
                      msg="游历输出应可见「长期记忆」（幕 6 报告 chen 已沉淀"
                          "长期记忆条数——赠礼因果 importance=0.8 入长时记忆）")

    def test_background_zero_token_visible(self):
        self.assertIn("0 Token", self.output,
                      msg="游历输出应可见「0 Token」（背景生态 NPC 全程零"
                          "消耗——Tier 0/1 封印的对外叙事口径）")


if __name__ == "__main__":
    unittest.main()

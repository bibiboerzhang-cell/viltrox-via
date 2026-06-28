"""Marketing Brain Eval Harness 单测:run_eval 汇总命中率/平均分正确,打分函数可注入。

不依赖活 DB / 外部模型:skill_fn 与 metric 全用内存桩。覆盖:
  - 全命中 -> hit_rate=1.0 / avg_score=1.0;
  - 部分命中 -> hit_rate / avg_score 与手算一致(set_overlap_metric 的 Jaccard);
  - skill_fn 抛异常 -> 该条 miss(score=0),不打断整批;
  - 注入自定义 default_metric 生效;
  - 空 cases -> 汇总不除零;
  - 示例 CREATOR_MATCH_CASES 结构可跑通。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.marketing_brain.evals import (  # noqa: E402
    CREATOR_MATCH_CASES,
    EvalCase,
    exact_match_metric,
    run_eval,
    set_overlap_metric,
)


class RunEvalSummaryTests(unittest.TestCase):
    def test_all_hit(self):
        cases = [
            EvalCase(input=1, expected=2),
            EvalCase(input=2, expected=4),
        ]
        rep = run_eval(lambda x: x * 2, cases)
        self.assertEqual(rep.total, 2)
        self.assertEqual(rep.hits, 2)
        self.assertEqual(rep.hit_rate, 1.0)
        self.assertEqual(rep.avg_score, 1.0)

    def test_partial_hit_exact(self):
        cases = [
            EvalCase(input=1, expected=2),   # skill 给 2 -> hit
            EvalCase(input=2, expected=99),  # skill 给 4 -> miss
        ]
        rep = run_eval(lambda x: x * 2, cases)
        self.assertEqual(rep.hits, 1)
        self.assertEqual(rep.hit_rate, 0.5)
        self.assertEqual(rep.avg_score, 0.5)

    def test_set_overlap_partial_score(self):
        # expected {a,b}, actual {a,c} -> 交1 并3 -> score=1/3, 非子集 -> miss
        case = EvalCase(input="q", expected=["a", "b"], metric=set_overlap_metric)
        rep = run_eval(lambda x: ["a", "c"], [case])
        self.assertEqual(rep.hits, 0)
        self.assertAlmostEqual(rep.avg_score, 1.0 / 3.0, places=4)

    def test_set_overlap_full_cover_hits(self):
        # expected {a,b} ⊆ actual {a,b,c} -> hit; score=2/3
        case = EvalCase(input="q", expected=["a", "b"], metric=set_overlap_metric)
        rep = run_eval(lambda x: ["a", "b", "c"], [case])
        self.assertEqual(rep.hits, 1)
        self.assertAlmostEqual(rep.avg_score, 2.0 / 3.0, places=4)

    def test_skill_exception_is_miss(self):
        def boom(_):
            raise ValueError("nope")

        rep = run_eval(boom, [EvalCase(input=1, expected=1)])
        self.assertEqual(rep.hits, 0)
        self.assertEqual(rep.avg_score, 0.0)
        self.assertIn("skill_error", rep.results[0].detail)

    def test_injected_default_metric(self):
        # 自定义 metric:永远满分命中
        always = lambda exp, act: (True, 1.0)  # noqa: E731
        rep = run_eval(lambda x: "whatever", [EvalCase(input=1, expected="x")], default_metric=always)
        self.assertEqual(rep.hit_rate, 1.0)
        self.assertEqual(rep.avg_score, 1.0)

    def test_empty_cases_no_div_zero(self):
        rep = run_eval(lambda x: x, [])
        self.assertEqual(rep.total, 0)
        self.assertEqual(rep.hit_rate, 0.0)
        self.assertEqual(rep.avg_score, 0.0)

    def test_to_dict_shape(self):
        rep = run_eval(lambda x: x, [EvalCase(input=1, expected=1)])
        d = rep.to_dict()
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["hits"], 1)
        self.assertIn("results", d)
        self.assertEqual(d["results"][0]["score"], 1.0)

    def test_exact_match_metric_direct(self):
        self.assertEqual(exact_match_metric(5, 5), (True, 1.0))
        self.assertEqual(exact_match_metric(5, 6), (False, 0.0))


class CreatorMatchExampleSetTests(unittest.TestCase):
    def test_example_set_runs(self):
        # 桩 skill:对每条 input 返回其 expected -> 全命中,验证示例集结构合法可跑
        lookup = {id(c.input): c.expected for c in CREATOR_MATCH_CASES}
        rep = run_eval(lambda inp: lookup[id(inp)], CREATOR_MATCH_CASES)
        self.assertEqual(rep.total, len(CREATOR_MATCH_CASES))
        self.assertEqual(rep.hit_rate, 1.0)
        self.assertEqual(rep.avg_score, 1.0)

    def test_example_set_has_metrics(self):
        self.assertGreaterEqual(len(CREATOR_MATCH_CASES), 3)
        self.assertLessEqual(len(CREATOR_MATCH_CASES), 5)
        for c in CREATOR_MATCH_CASES:
            self.assertIsNotNone(c.metric)
            self.assertTrue(c.name)


if __name__ == "__main__":
    unittest.main()

"""Marketing Brain④ Eval Harness —— 给 skill 函数跑离线评测,汇总命中率/平均分。

设计原则:
  - 不依赖外部模型/活 DB:打分函数(metric)可注入,纯函数即可跑;
  - skill_fn 是被测函数 input -> output,run_eval 喂每条 case 拿 output,
    用 case 自带 metric(或全局 default_metric)对照 expected 打 [0,1] 分;
  - metric 返回 (hit: bool, score: float)。hit 用于命中率,score 用于平均分;
  - 单条 skill_fn 抛异常视为 miss(score=0),不打断整批;
  - 红线:本文件零触 viltrox_fit_score;评测只读跑。

复用参考:app/domains/platform/evals.py(内置业务套件,跑活系统断言)。
本文件是它的「离线 / 可注入打分」补集——给单个 skill 函数做样本级回归。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# metric 签名:(expected, actual) -> (hit, score)
Metric = Callable[[Any, Any], tuple[bool, float]]
# skill 签名:input -> output
SkillFn = Callable[[Any], Any]


def exact_match_metric(expected: Any, actual: Any) -> tuple[bool, float]:
    """默认打分:相等即命中满分,否则 miss 零分。"""
    hit = expected == actual
    return hit, 1.0 if hit else 0.0


def set_overlap_metric(expected: Any, actual: Any) -> tuple[bool, float]:
    """集合重叠打分:score = |交集| / |并集|(Jaccard);全中才算 hit。

    适合 creator_match 这类「返回一组候选 id,期望覆盖某几个」的场景。
    expected/actual 任意可迭代;空并集判 hit 满分(都空=一致)。
    """
    exp = set(expected or [])
    act = set(actual or [])
    union = exp | act
    if not union:
        return True, 1.0
    score = len(exp & act) / len(union)
    hit = exp.issubset(act) and bool(exp)
    return hit, score


@dataclass
class EvalCase:
    """一条评测样本。

    input:    喂给 skill_fn 的入参;
    expected: 期望输出(交给 metric 对照);
    metric:   本条专用打分函数,缺省走 run_eval 的 default_metric;
    name:     可读标识,缺省自动编号。
    """

    input: Any
    expected: Any
    metric: Optional[Metric] = None
    name: str = ""


@dataclass
class CaseResult:
    name: str
    hit: bool
    score: float
    detail: str = ""


@dataclass
class EvalReport:
    suite: str
    total: int
    hits: int
    hit_rate: float
    avg_score: float
    results: list[CaseResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "total": self.total,
            "hits": self.hits,
            "hit_rate": round(self.hit_rate, 4),
            "avg_score": round(self.avg_score, 4),
            "results": [
                {"name": r.name, "hit": r.hit, "score": round(r.score, 4), "detail": r.detail}
                for r in self.results
            ],
        }


def run_eval(
    skill_fn: SkillFn,
    cases: list[EvalCase],
    *,
    default_metric: Metric = exact_match_metric,
    suite: str = "marketing_brain_eval",
) -> EvalReport:
    """跑一批 EvalCase,汇总命中率 + 平均分。

    skill_fn:       被测函数 input -> output;
    cases:          EvalCase 列表;
    default_metric: case 未带 metric 时的兜底打分函数(可注入,默认精确匹配);
    返回 EvalReport(可 .to_dict() 序列化)。
    """
    results: list[CaseResult] = []
    for i, case in enumerate(cases):
        name = case.name or f"case_{i}"
        metric = case.metric or default_metric
        try:
            actual = skill_fn(case.input)
        except Exception as exc:  # skill 抛错 = miss,不打断整批
            results.append(CaseResult(name=name, hit=False, score=0.0, detail=f"skill_error: {str(exc)[:160]}"))
            continue
        try:
            hit, score = metric(case.expected, actual)
        except Exception as exc:  # metric 抛错 = miss
            results.append(CaseResult(name=name, hit=False, score=0.0, detail=f"metric_error: {str(exc)[:160]}"))
            continue
        score = max(0.0, min(1.0, float(score)))
        results.append(CaseResult(name=name, hit=bool(hit), score=score, detail=f"expected={case.expected!r} actual={actual!r}"[:240]))

    total = len(results)
    hits = sum(1 for r in results if r.hit)
    avg = (sum(r.score for r in results) / total) if total else 0.0
    rate = (hits / total) if total else 0.0
    return EvalReport(suite=suite, total=total, hits=hits, hit_rate=rate, avg_score=avg, results=results)


# ---------------------------------------------------------------------------
# 示例 eval set:creator_match(纯结构示例,不接活系统)
# input:  {"sku": ..., "audience": ...} —— 检索意图;
# expected: 期望命中的 creator id 集合。
# 用 set_overlap_metric:返回的候选只要覆盖 expected 即 hit,score 看重叠度。
# ---------------------------------------------------------------------------
CREATOR_MATCH_CASES: list[EvalCase] = [
    EvalCase(
        name="camera_tech_reviewer",
        input={"sku": "viltrox-af-85mm", "audience": "camera_enthusiast"},
        expected=["creator_101", "creator_102"],
        metric=set_overlap_metric,
    ),
    EvalCase(
        name="drone_outdoor_creator",
        input={"sku": "viltrox-drone-gimbal", "audience": "outdoor_video"},
        expected=["creator_210"],
        metric=set_overlap_metric,
    ),
    EvalCase(
        name="portrait_lens_lifestyle",
        input={"sku": "viltrox-af-35mm", "audience": "portrait_lifestyle"},
        expected=["creator_330", "creator_331", "creator_332"],
        metric=set_overlap_metric,
    ),
    EvalCase(
        name="vlog_compact_prime",
        input={"sku": "viltrox-af-27mm", "audience": "vlogger"},
        expected=["creator_410"],
        metric=set_overlap_metric,
    ),
]

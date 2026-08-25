"""库内召回候选循环的分层记账(车道 4·A8)。

只做**加法**:把 ``recall_kol_profiles`` 候选循环里原本散在局部变量里的计数收进一个
账本对象,并补上此前缺席的三件事——

1. 每道闸各自进入了多少 / 各自杀了多少(``stage_funnel``),这样「458 → 0」下次一眼可见;
2. 硬筛驳回拆成「未知驳回」和「值不匹配驳回」两本账(``hard_filter_rejected_unknown_by`` /
   ``hard_filter_rejected_mismatch_by``)。用户裁令:国家筛选是真语义,不许把未知偷偷放行;
   但**未知必须让操作员看得见**——看得见的前提就是先把它数出来;
3. 「只因这一道闸被杀」的独因分布(``hard_filter_sole_reason_by``),用于判断某道闸是不是
   单独把结果清零的那一刀。

本模块**不做任何过滤决策**:调用方照旧自己判断 ``continue``,只是把计数托管给账本。
因此同一输入下的通过集合与记账前完全一致(契约测试:
``tests/test_search_session_observability_ledger.py::test_ledger_does_not_change_pass_set``)。

red line:零触 ``viltrox_fit_score``,零触 rule_v0,不写库。
"""
from __future__ import annotations

from typing import Any, Iterable

RECALL_FUNNEL_SCHEMA = "recall_stage_funnel_v1"

# ``_candidate_filter_verdict`` 的 rejected 名(复数,过滤器名)与 unknown 名(单数,字段名)
# 不同族。要判断一次驳回究竟是「值不匹配」还是「字段本身未知」,必须先把两族名字对齐。
_UNKNOWN_FIELD_FOR: dict[str, str] = {
    "platforms": "platform",
    "countries": "country",
    "languages": "language",
    "followers_min": "followers",
    "followers_max": "followers",
    "verticals": "verticals",
    "gear_content": "gear_content",
}


def _bump(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _names(values: Iterable[Any] | None) -> list[str]:
    return [str(value) for value in (values or ()) if str(value)]


class RecallStageLedger:
    """候选循环的分层账本。纯计数,零过滤决策。"""

    __slots__ = (
        "fallback_used",
        "missing_type",
        "excluded_region",
        "low_reach",
        "unknown_reach",
        "hard_filter_evaluated",
        "hard_filter_rejected",
        "no_match_evidence",
        "rejected_by",
        "rejected_unknown_by",
        "rejected_mismatch_by",
        "sole_reason_by",
        "unknown_field_counts",
    )

    def __init__(self) -> None:
        self.fallback_used = 0
        self.missing_type = 0
        self.excluded_region = 0
        self.low_reach = 0
        self.unknown_reach = 0
        self.hard_filter_evaluated = 0
        self.hard_filter_rejected = 0
        self.no_match_evidence = 0
        self.rejected_by: dict[str, int] = {}
        self.rejected_unknown_by: dict[str, int] = {}
        self.rejected_mismatch_by: dict[str, int] = {}
        self.sole_reason_by: dict[str, int] = {}
        self.unknown_field_counts: dict[str, int] = {}

    def note_hard_filter(
        self,
        rejected_fields: Iterable[Any] | None,
        unknown_fields: Iterable[Any] | None,
        *,
        passed: bool,
    ) -> None:
        """记一次硬筛判定。``passed`` 由调用方给出,本方法绝不参与判定。"""

        self.hard_filter_evaluated += 1
        unknown = set(_names(unknown_fields))
        for field in sorted(unknown):
            _bump(self.unknown_field_counts, field)
        if passed:
            return
        self.hard_filter_rejected += 1
        rejected = sorted(set(_names(rejected_fields)))
        for field in rejected:
            _bump(self.rejected_by, field)
            if _UNKNOWN_FIELD_FOR.get(field, field) in unknown:
                _bump(self.rejected_unknown_by, field)
            else:
                _bump(self.rejected_mismatch_by, field)
        if len(rejected) == 1:
            _bump(self.sole_reason_by, rejected[0])

    def stage_funnel(self, *, deduped_candidate_count: int) -> dict[str, Any]:
        """每道闸的进 / 出。进入量按上游减去上游丢弃量推算,零额外循环开销。"""

        entered_rows = max(0, int(deduped_candidate_count))
        entered_region = max(0, entered_rows - self.missing_type)
        entered_reach = max(0, entered_region - self.excluded_region)
        entered_hard = max(0, entered_reach - self.low_reach - self.unknown_reach)
        entered_evidence = max(0, entered_hard - self.hard_filter_rejected)
        return {
            "schema": RECALL_FUNNEL_SCHEMA,
            "entered_row_lookup": entered_rows,
            "entered_region_gate": entered_region,
            "entered_reach_gate": entered_reach,
            "entered_hard_filter": entered_hard,
            "entered_evidence_gate": entered_evidence,
            "survivors": max(0, entered_evidence - self.no_match_evidence),
            "dropped_by_gate": {
                "row_missing": self.missing_type,
                "excluded_region": self.excluded_region,
                "low_reach": self.low_reach,
                "unknown_reach": self.unknown_reach,
                "hard_filter": self.hard_filter_rejected,
                "no_match_evidence": self.no_match_evidence,
            },
        }

    def as_diagnostics(self, *, deduped_candidate_count: int) -> dict[str, Any]:
        """既有 diagnostics 键原样保留(前端/契约依赖),再叠加 A8 分层明细。"""

        return {
            "missing_type_count": self.missing_type,
            "filtered_low_reach": self.low_reach,
            "filtered_unknown_reach": self.unknown_reach,
            "filtered_excluded_region": self.excluded_region,
            "hard_filter_rejected_count": self.hard_filter_rejected,
            "hard_filter_rejected_by": dict(self.rejected_by),
            "filtered_no_match_evidence": self.no_match_evidence,
            "stage_funnel": self.stage_funnel(
                deduped_candidate_count=deduped_candidate_count,
            ),
            "hard_filter_evaluated_count": self.hard_filter_evaluated,
            "hard_filter_rejected_unknown_by": dict(self.rejected_unknown_by),
            "hard_filter_rejected_mismatch_by": dict(self.rejected_mismatch_by),
            "hard_filter_sole_reason_by": dict(self.sole_reason_by),
            "unknown_field_counts": dict(self.unknown_field_counts),
        }

"""库内召回硬筛的**三态**语义原语(2026-08-25)。

背景与用户裁令:池里 ``country`` 空 995/2034、``language`` 空 1448/2034。历史硬筛把
「字段未知」当成「不匹配」一并驳回(``profile_recall_projection._candidate_filter_verdict``),
于是勾一个「英语」就把整个 Instagram(language 全空)删干净了。用户已拍板:

    「勾『美国』就是要美国人」—— 国家筛选是真语义,**不许**改成「未知一律放行」。
    正解是把数据补齐让未知变少;剩下的未知要让操作员**看得见、能选择**,
    而不是偷偷放行或偷偷杀掉。

因此本模块只做三件小事,零过滤策略上的自作主张:

1. ``tri_state_outcome``:把「未知」和「确认不符」判成两种**不同**的结果,
   并给出三种可由操作员显式选择的模式——``require``(缺省;必须确认是)/
   ``include_unknown``(确认是 + 未知)/ ``exclude``(排除确认是的)。
   **缺省 require 与历史行为逐字节一致**,由测试钉死。
2. ``CandidateFilterVerdict``:仍然是 ``(passes, rejected, unknown)`` 三元组
   (解包、索引、与普通 tuple 比较全部不变),但旁挂了
   ``rejected_known_mismatch`` / ``rejected_unknown`` 两本分开的账,
   让诊断能区分「他不是美国人」和「我们不知道他是不是」。
3. ``unknown_field_candidates``:定点补齐钩子——把「其他维度都合格、只差
   country/language 未知」的候选**标记**出来。**只标记,不抓取**,不触发任何采集。

red line:本模块纯函数、不读库、不写库,零触 ``viltrox_fit_score``,零触 rule_v0。
"""
from __future__ import annotations

from typing import Any

#: 三态模式。``require`` 为缺省 —— 与本模块出现之前的硬筛行为完全一致。
TRI_STATE_MODES: tuple[str, ...] = ("require", "include_unknown", "exclude")

#: 支持三态的过滤器名 -> 其在候选行上的字段名(单数)。
#: 仅 countries / languages;platforms / verticals 不开三态(它们不是「人的属性未知」问题)。
TRI_STATE_FILTER_FIELDS: dict[str, str] = {"countries": "country", "languages": "language"}

#: ``tri_state_outcome`` 的返回值域。空串 = 通过。
OUTCOME_PASS = ""
OUTCOME_UNKNOWN = "unknown"
OUTCOME_MISMATCH = "mismatch"


def normalize_mode(value: Any) -> str:
    """把任意入参收敛成合法模式;不认识的一律退回 ``require``(失败方向 = 保持现状)。"""
    mode = str(value or "require").strip().lower()
    return mode if mode in TRI_STATE_MODES else "require"


def tri_state_outcome(value: str, requested: set[str], mode: Any = "require") -> str:
    """单个字段的三态硬筛判定。

    * ``requested`` 为空 —— 没人筛这个字段,直接通过;
    * ``value`` 为空(字段未知):
      ``require`` -> ``unknown``(**驳回**,历史行为,用户裁令保留);
      ``include_unknown`` / ``exclude`` -> 通过;
    * ``value`` 有值:``exclude`` 命中即 ``mismatch``,其余模式未命中即 ``mismatch``。

    注意:三种模式下「未知」都仍然会被调用方计进 ``unknown`` 诚实清单——放行不等于
    假装知道。这里只决定「拦不拦」,不决定「说不说」。
    """
    normalized = normalize_mode(mode)
    if not requested:
        return OUTCOME_PASS
    if not value:
        return OUTCOME_UNKNOWN if normalized == "require" else OUTCOME_PASS
    if normalized == "exclude":
        return OUTCOME_MISMATCH if value in requested else OUTCOME_PASS
    return OUTCOME_PASS if value in requested else OUTCOME_MISMATCH


def normalize_tri_state_filter(value: Any) -> tuple[Any, str, bool]:
    """拆开 ``{"values": [...], "mode": "..."}`` 形态的三态入参。

    返回 ``(取值源, 模式, 模式是否非法)``。**非 dict 入参 = 历史形态**(裸串 / 数组),
    一律 ``require`` —— 老调用方零行为漂移。模式写错不静默吞掉:第三位返回 True,
    由调用方登记进 ``unsupported`` 让操作员看见,同时按 ``require`` 保守执行。
    """
    if not isinstance(value, dict):
        return value, "require", False
    raw_mode = str(value.get("mode") or "require").strip().lower()
    invalid = raw_mode not in TRI_STATE_MODES
    return value.get("values"), ("require" if invalid else raw_mode), invalid


def unknown_field_candidates(
    row: dict[str, Any],
    reasons: dict[str, str],
) -> list[dict[str, Any]]:
    """定点补齐钩子:标记「只差 country/language 未知」的候选。**只标记,不抓取。**

    命中条件(全部满足):驳回原因非空、**全部**是 ``unknown``、且**全部**落在
    ``TRI_STATE_FILTER_FIELDS`` 里。也就是说这个人其它维度都合格,唯一挡住他的
    是「我们不知道他是哪国人 / 说什么语言」—— 正是后续「搜索时按需补齐」该去
    定点补的那一批。任何一条 ``mismatch``(确认不符)都会让它落空:补齐数据
    救不了一个确认不是美国人的人。

    字段契约(稳定,下游按名取):``kol_pool_id`` / ``handle`` / ``platform`` /
    ``missing_fields``(排序后的字段名,取值 ``country`` / ``language``)。
    """
    if not reasons or set(reasons.values()) != {OUTCOME_UNKNOWN}:
        return []
    fields = sorted(TRI_STATE_FILTER_FIELDS[key] for key in reasons if key in TRI_STATE_FILTER_FIELDS)
    if len(fields) != len(reasons):
        return []
    pool_id = row.get("kol_pool_id")
    if pool_id is None:
        pool_id = row.get("id")
    try:
        pool_id = int(pool_id) if pool_id is not None else None
    except (TypeError, ValueError):
        pool_id = None
    return [
        {
            "kol_pool_id": pool_id,
            "handle": str(row.get("handle") or ""),
            "platform": str(row.get("platform") or ""),
            "missing_fields": fields,
        }
    ]


class CandidateFilterVerdict(tuple):
    """``(passes, rejected, unknown)`` —— 与历史三元组逐字节兼容,只是旁挂了分账。

    旁挂字段(不参与解包 / 迭代 / 相等比较):

    * ``rejected_known_mismatch``:确认值不匹配而被拒的过滤器名(「他不是美国人」);
    * ``rejected_unknown``:因字段未知而被拒的过滤器名(「我们不知道他是不是」);
    * ``unknown_field_candidates``:定点补齐候选(见 ``unknown_field_candidates``)。

    之所以做成 tuple 子类而不是新返回类型:唯一的线上调用方
    (``profile_recall.recall_kol_profiles``)按 ``a, b, c = ...`` 解包,契约测试按
    ``[0]`` 取值。加法式扩展 = 零行为漂移,分账数据同时已经流到了调用点上,
    将来接诊断时不必再改判定层。

    (tuple 是变长内建类型,子类不允许非空 ``__slots__``,因此旁挂字段走实例字典。)
    """

    def __new__(
        cls,
        reasons: dict[str, str],
        unknown: list[str],
        candidates: list[dict[str, Any]] | None = None,
    ) -> "CandidateFilterVerdict":
        verdict = super().__new__(cls, (not reasons, sorted(reasons), sorted(set(unknown))))
        verdict.rejected_known_mismatch = sorted(
            key for key, why in reasons.items() if why == OUTCOME_MISMATCH
        )
        verdict.rejected_unknown = sorted(
            key for key, why in reasons.items() if why == OUTCOME_UNKNOWN
        )
        verdict.unknown_field_candidates = list(candidates or [])
        return verdict

    @property
    def passes(self) -> bool:
        return bool(self[0])

    @property
    def rejected(self) -> list[str]:
        return list(self[1])

    @property
    def unknown(self) -> list[str]:
        return list(self[2])


__all__ = [
    "CandidateFilterVerdict",
    "OUTCOME_MISMATCH",
    "OUTCOME_PASS",
    "OUTCOME_UNKNOWN",
    "TRI_STATE_FILTER_FIELDS",
    "TRI_STATE_MODES",
    "normalize_mode",
    "normalize_tri_state_filter",
    "tri_state_outcome",
    "unknown_field_candidates",
]

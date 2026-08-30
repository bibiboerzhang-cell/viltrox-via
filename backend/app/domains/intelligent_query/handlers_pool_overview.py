"""KOL Pool overview 装配层(2026-08-30 从 handlers.kol_pool_overview 提出,行为不变)。

职责分段:
- pool_source_unavailable / requested_filter_unavailable:两类诚实错误响应;
- assemble_overview:池聚合 → 视频/深析覆盖 → facts → coverage/status → 双语 answer
  → evidence → freshness → actions。

协作符号(table_columns / table_present / as_dict / int0 / where_sql / _fact /
_localized / _missing / _is_en / _freshness)一律经门面 handlers 在调用时解析——
tests 对门面的 monkeypatch 原样生效。红线:纯读聚合,零写库,不碰 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _h() -> Any:
    """调用时解析门面模块:门面上的 monkeypatch / 运行时替换一律生效。"""
    from app.domains.intelligent_query import handlers

    return handlers


def pool_source_unavailable(response: dict[str, Any], request: Any) -> dict[str, Any]:
    h = _h()
    response.update(
        {
            "status": "error",
            "answer": "KOL Pool data source is unavailable." if h._is_en(request) else "KOL Pool 数据源不可用。",
            "degraded_reason": "kol_pool_source_unavailable",
        }
    )
    response["coverage"].update(
        status="unknown",
        notes=[h._localized(request, "vkpi_kol_pool 数据源不可用。", "vkpi_kol_pool is unavailable.")],
    )
    response["missing_fields"].append(
        h._missing(
            request,
            "vkpi_kol_pool",
            "数据源表不可用",
            "source table is unavailable",
            "无法核验 KOL 数量",
            "KOL counts cannot be verified",
        )
    )
    return response


def requested_filter_unavailable(
    response: dict[str, Any], request: Any, unavailable_filters: list[dict[str, Any]]
) -> dict[str, Any]:
    h = _h()
    response.update(
        {
            "status": "error",
            "answer": h._localized(
                request,
                "请求的 KOL 筛选字段不可用，本次未返回宽范围或零值结论。",
                "A requested KOL filter is unavailable; no broad or zero-valued result was returned.",
            ),
            "degraded_reason": "requested_filter_unavailable",
        }
    )
    response["missing_fields"] = unavailable_filters
    response["coverage"].update(
        status="unknown",
        notes=[
            h._localized(
                request,
                "筛选条件未被静默忽略。",
                "The requested filter was not silently ignored.",
            )
        ],
    )
    return response


def _pool_totals(
    conn: Any, pool_columns: set[str], clauses: list[str], params: list[Any]
) -> tuple[dict[str, Any], int, bool]:
    h = _h()
    has_duplicate_column = "duplicate_of_id" in pool_columns
    canonical_count_expr = "COUNT(*)"
    duplicate_expr = "0"
    if has_duplicate_column:
        # pool_predicates has already restricted rows to canonical records.  A
        # separate raw count below keeps the duplicate fact auditable.
        duplicate_expr = "(SELECT COUNT(*) FROM vkpi_kol_pool WHERE duplicate_of_id IS NOT NULL)"
    updated_expr = "MAX(p.updated_at)" if "updated_at" in pool_columns else "NULL"
    pool_row = h.as_dict(
        conn.execute(
            f"SELECT {canonical_count_expr} AS total_kols, {duplicate_expr} AS duplicate_rows, "
            f"{updated_expr} AS data_updated_at FROM vkpi_kol_pool p{h.where_sql(clauses)}",
            tuple(params),
        ).fetchone()
    )
    return pool_row, h.int0(pool_row.get("total_kols")), has_duplicate_column


def _video_stats(
    conn: Any,
    request: Any,
    clauses: list[str],
    params: list[Any],
    missing: list[dict[str, Any]],
) -> tuple[bool, int, int, Any]:
    h = _h()
    video_kols = 0
    video_rows = 0
    video_updated_at: Any = None
    has_video_source = h.table_present(conn, "vkpi_kol_video_evidence")
    if has_video_source:
        evidence_columns = h.table_columns(conn, "vkpi_kol_video_evidence")
        video_clauses = list(clauses)
        if "is_active" in evidence_columns:
            video_clauses.append("e.is_active IS NOT FALSE")
        video_updated_expr = (
            "MAX(COALESCE(e.scraped_at, e.updated_at, e.created_at))"
            if {"scraped_at", "updated_at", "created_at"}.issubset(evidence_columns)
            else ("MAX(e.updated_at)" if "updated_at" in evidence_columns else "NULL")
        )
        video_row = h.as_dict(
            conn.execute(
                "SELECT COUNT(DISTINCT e.kol_pool_id) AS video_kols, COUNT(DISTINCT e.id) AS video_rows, "
                f"{video_updated_expr} AS data_updated_at "
                "FROM vkpi_kol_video_evidence e JOIN vkpi_kol_pool p ON p.id=e.kol_pool_id"
                + h.where_sql(video_clauses),
                tuple(params),
            ).fetchone()
        )
        video_kols = h.int0(video_row.get("video_kols"))
        video_rows = h.int0(video_row.get("video_rows"))
        video_updated_at = video_row.get("data_updated_at")
    else:
        missing.append(
            h._missing(
                request,
                "video_evidence",
                "视频证据表不可用",
                "video evidence table is unavailable",
                "视频覆盖率未知",
                "video coverage is unknown",
            )
        )
    return has_video_source, video_kols, video_rows, video_updated_at


def _deep_stats(
    conn: Any,
    request: Any,
    clauses: list[str],
    params: list[Any],
    missing: list[dict[str, Any]],
) -> int | None:
    h = _h()
    if h.table_present(conn, "vkpi_kol_llm_deep_analysis_results"):
        deep_clauses = list(clauses) + ["d.status = ?"]
        deep_row = h.as_dict(
            conn.execute(
                "SELECT COUNT(DISTINCT d.kol_pool_id) AS deep_kols "
                "FROM vkpi_kol_llm_deep_analysis_results d "
                "JOIN vkpi_kol_pool p ON p.id=d.kol_pool_id"
                + h.where_sql(deep_clauses),
                (*params, "ready"),
            ).fetchone()
        )
        return h.int0(deep_row.get("deep_kols"))
    missing.append(
        h._missing(
            request,
            "deep_analysis",
            "深度分析表不可用",
            "deep-analysis table is unavailable",
            "深度分析覆盖率未知",
            "deep-analysis coverage is unknown",
        )
    )
    return None


def _total_fact(request: Any, has_duplicate_column: bool, total_kols: int) -> dict[str, Any]:
    h = _h()
    return h._fact(
        "kol.total" if has_duplicate_column else "kol.raw_records",
        "有效去重 KOL" if has_duplicate_column else "原始 KOL 记录",
        "Canonical KOLs" if has_duplicate_column else "Raw KOL records",
        total_kols,
        request=request,
        basis=(
            (
                "按请求筛选后，统计 vkpi_kol_pool 中 duplicate_of_id 为空的主记录"
                if has_duplicate_column
                else "按请求范围统计原始 vkpi_kol_pool 记录；当前无法核验主从去重"
            ),
            (
                "COUNT(vkpi_kol_pool WHERE duplicate_of_id IS NULL + request filters)"
                if has_duplicate_column
                else "raw COUNT(vkpi_kol_pool + request filters); canonical column unavailable"
            ),
        ),
        confidence="high" if has_duplicate_column else "medium",
    )


def _append_facts(
    response: dict[str, Any],
    request: Any,
    *,
    scope_context: dict[str, Any],
    pool_row: dict[str, Any],
    total_kols: int,
    has_duplicate_column: bool,
    has_video_source: bool,
    video_kols: int,
    video_rows: int,
    deep_kols: int | None,
) -> None:
    h = _h()
    response["facts"] = [_total_fact(request, has_duplicate_column, total_kols)]
    if has_video_source:
        response["facts"].extend(
            [
                h._fact(
                    "kol.with_video_evidence",
                    "有视频证据的 KOL",
                    "KOLs with video evidence",
                    video_kols,
                    request=request,
                    basis=(
                        "统计有效视频证据覆盖的 KOL",
                        "COUNT(DISTINCT active vkpi_kol_video_evidence.kol_pool_id)",
                    ),
                ),
                h._fact(
                    "video.evidence_rows",
                    "有效视频证据",
                    "Active video evidence",
                    video_rows,
                    request=request,
                    basis=(
                        "统计有效且去重的视频证据记录",
                        "COUNT(DISTINCT active vkpi_kol_video_evidence.id)",
                    ),
                ),
            ]
        )
    if deep_kols is not None:
        response["facts"].append(
            h._fact(
                "kol.deep_analyzed",
                "完成深度分析的 KOL",
                "Deep-analyzed KOLs",
                deep_kols,
                request=request,
                basis=(
                    "统计完成态深度分析覆盖的去重 KOL",
                    "COUNT(DISTINCT ready vkpi_kol_llm_deep_analysis_results.kol_pool_id)",
                ),
            )
        )
    if (
        scope_context.get("applied_mode") in {"auto", "all", "team"}
        and not any(request.filters.get(key) for key in ("platform", "country"))
        and has_duplicate_column
    ):
        response["facts"].append(
            h._fact(
                "kol.merged_duplicates",
                "已归并重复行",
                "Merged duplicate rows",
                h.int0(pool_row.get("duplicate_rows")),
                request=request,
                basis=(
                    "统计 vkpi_kol_pool 中已归并的重复从记录",
                    "COUNT(vkpi_kol_pool WHERE duplicate_of_id IS NOT NULL)",
                ),
            )
        )


def _apply_coverage(
    response: dict[str, Any],
    request: Any,
    *,
    missing: list[dict[str, Any]],
    total_kols: int,
    has_duplicate_column: bool,
    has_video_source: bool,
    video_kols: int,
    video_rows: int,
    deep_kols: int | None,
) -> None:
    h = _h()
    coverage_ratio = (
        round(video_kols / total_kols, 4) if has_video_source and total_kols else None
    )
    response["coverage"].update(
        {
            "status": "partial" if missing else "complete",
            "matched_entities": total_kols,
            "evidence_count": video_rows if has_video_source else 0,
            "total_scope": total_kols,
            "analyzed_count": deep_kols,
            "ratio": coverage_ratio,
            "notes": [
                (
                    "KOL total excludes rows already merged through duplicate_of_id."
                    if h._is_en(request)
                    else "KOL 总数排除 duplicate_of_id 已归并从行。"
                )
                if has_duplicate_column
                else h._localized(
                    request,
                    "当前仅能提供原始记录数；主从去重口径不可用。",
                    "Only a raw record count is available; canonical deduplication is unavailable.",
                )
            ],
        }
    )
    response["missing_fields"] = missing
    response["status"] = "partial" if missing else ("empty" if total_kols == 0 else "ready")


def _apply_answer(
    response: dict[str, Any],
    request: Any,
    *,
    total_kols: int,
    has_duplicate_column: bool,
    has_video_source: bool,
    video_kols: int,
    deep_kols: int | None,
) -> None:
    h = _h()
    if h._is_en(request):
        answer_parts = [
            (
                f"There are {total_kols:,} canonical KOLs"
                if has_duplicate_column
                else f"There are {total_kols:,} raw KOL records; canonical deduplication is unavailable"
            )
        ]
        if has_video_source:
            answer_parts.append(f"{video_kols:,} have active video evidence")
        if deep_kols is not None:
            answer_parts.append(f"{deep_kols:,} have ready deep analysis")
        response["answer"] = "; ".join(answer_parts) + "."
    else:
        answer_parts = [
            (
                f"当前有效去重 KOL 共 {total_kols:,} 个"
                if has_duplicate_column
                else f"当前原始 KOL 记录共 {total_kols:,} 条；主从去重口径不可用"
            )
        ]
        if has_video_source:
            answer_parts.append(f"{video_kols:,} 个已有有效视频证据")
        if deep_kols is not None:
            answer_parts.append(f"{deep_kols:,} 个已有完成态深度分析")
        response["answer"] = "；".join(answer_parts) + "。"


def _apply_evidence(
    response: dict[str, Any],
    request: Any,
    *,
    pool_row: dict[str, Any],
    has_duplicate_column: bool,
    has_video_source: bool,
    video_updated_at: Any,
) -> None:
    h = _h()
    response["evidence"] = [
        {
            "id": "kol-pool-aggregate",
            "kind": "aggregate",
            "source": "vkpi_kol_pool",
            "title": h._localized(
                request,
                "KOL Pool 去重汇总" if has_duplicate_column else "KOL Pool 原始记录汇总",
                "KOL Pool canonical aggregate" if has_duplicate_column else "KOL Pool raw-record aggregate",
            ),
            "observed_at": str(pool_row.get("data_updated_at") or "") or None,
            "confidence": "high" if has_duplicate_column else "medium",
        }
    ]
    if has_video_source:
        response["evidence"].append(
            {
                "id": "kol-video-aggregate",
                "kind": "aggregate",
                "source": "vkpi_kol_video_evidence",
                "title": h._localized(request, "有效视频证据覆盖", "Active video evidence coverage"),
                "observed_at": str(video_updated_at or "") or None,
                "confidence": "high",
            }
        )


def assemble_overview(
    conn: Any,
    request: Any,
    response: dict[str, Any],
    *,
    scope_context: dict[str, Any],
    pool_columns: set[str],
    clauses: list[str],
    params: list[Any],
    missing: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    h = _h()
    pool_row, total_kols, has_duplicate_column = _pool_totals(conn, pool_columns, clauses, params)
    has_video_source, video_kols, video_rows, video_updated_at = _video_stats(
        conn, request, clauses, params, missing
    )
    deep_kols = _deep_stats(conn, request, clauses, params, missing)
    _append_facts(
        response,
        request,
        scope_context=scope_context,
        pool_row=pool_row,
        total_kols=total_kols,
        has_duplicate_column=has_duplicate_column,
        has_video_source=has_video_source,
        video_kols=video_kols,
        video_rows=video_rows,
        deep_kols=deep_kols,
    )
    _apply_coverage(
        response,
        request,
        missing=missing,
        total_kols=total_kols,
        has_duplicate_column=has_duplicate_column,
        has_video_source=has_video_source,
        video_kols=video_kols,
        video_rows=video_rows,
        deep_kols=deep_kols,
    )
    _apply_answer(
        response,
        request,
        total_kols=total_kols,
        has_duplicate_column=has_duplicate_column,
        has_video_source=has_video_source,
        video_kols=video_kols,
        deep_kols=deep_kols,
    )
    _apply_evidence(
        response,
        request,
        pool_row=pool_row,
        has_duplicate_column=has_duplicate_column,
        has_video_source=has_video_source,
        video_updated_at=video_updated_at,
    )
    newest = video_updated_at or pool_row.get("data_updated_at")
    h._freshness(response, request, now=now, updated_at=newest)
    response["actions"] = [
        {
            "type": "navigate",
            "label": "Open KOL Pool" if h._is_en(request) else "打开 KOL Pool",
            "route": "kol-pool",
            "params": {"scope": scope_context.get("applied_mode")},
            "requires_approval": False,
        }
    ]
    return response

"""Read-only deterministic handlers for the first Ask & Find v2 intents."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.domains.access import scope as access_scope
from app.domains.intelligent_query.common import (
    fact as _fact,
    freshness as _freshness,
    is_en as _is_en,
    localized as _localized,
    missing as _missing,
)
from app.domains.intelligent_query.contracts import NormalizedRequest, empty_response
from app.domains.intelligent_query.intent import extract_project_keyword, extract_video_topic
from app.domains.intelligent_query.repository import (
    actual_scope_context,
    as_dict,
    int0,
    pool_predicates,
    table_columns,
    table_present,
    text,
    where_sql,
)
from app.domains.intelligent_query.weekly_voice import market_weekly_voice


def kol_pool_overview(
    conn: Any,
    request: NormalizedRequest,
    staff: dict[str, Any] | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    scope_context = actual_scope_context(request, staff)
    response = empty_response(request, intent="kol.pool.overview", scope=scope_context)
    pool_columns = table_columns(conn, "vkpi_kol_pool")
    if not pool_columns:
        response.update(
            {
                "status": "error",
                "answer": "KOL Pool data source is unavailable." if _is_en(request) else "KOL Pool 数据源不可用。",
                "degraded_reason": "kol_pool_source_unavailable",
            }
        )
        response["coverage"].update(
            status="unknown",
            notes=[_localized(request, "vkpi_kol_pool 数据源不可用。", "vkpi_kol_pool is unavailable.")],
        )
        response["missing_fields"].append(
            _missing(
                request,
                "vkpi_kol_pool",
                "数据源表不可用",
                "source table is unavailable",
                "无法核验 KOL 数量",
                "KOL counts cannot be verified",
            )
        )
        return response

    clauses, params, missing = pool_predicates(conn, request, staff, alias="p")
    unavailable_filters = [
        item for item in missing if str(item.get("field") or "").startswith("filters.")
    ]
    if unavailable_filters:
        response.update(
            {
                "status": "error",
                "answer": _localized(
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
                _localized(
                    request,
                    "筛选条件未被静默忽略。",
                    "The requested filter was not silently ignored.",
                )
            ],
        )
        return response
    has_duplicate_column = "duplicate_of_id" in pool_columns
    canonical_count_expr = "COUNT(*)"
    duplicate_expr = "0"
    if has_duplicate_column:
        # pool_predicates has already restricted rows to canonical records.  A
        # separate raw count below keeps the duplicate fact auditable.
        duplicate_expr = "(SELECT COUNT(*) FROM vkpi_kol_pool WHERE duplicate_of_id IS NOT NULL)"
    updated_expr = "MAX(p.updated_at)" if "updated_at" in pool_columns else "NULL"
    pool_row = as_dict(
        conn.execute(
            f"SELECT {canonical_count_expr} AS total_kols, {duplicate_expr} AS duplicate_rows, "
            f"{updated_expr} AS data_updated_at FROM vkpi_kol_pool p{where_sql(clauses)}",
            tuple(params),
        ).fetchone()
    )
    total_kols = int0(pool_row.get("total_kols"))

    video_kols = 0
    video_rows = 0
    video_updated_at: Any = None
    has_video_source = table_present(conn, "vkpi_kol_video_evidence")
    if has_video_source:
        evidence_columns = table_columns(conn, "vkpi_kol_video_evidence")
        video_clauses = list(clauses)
        if "is_active" in evidence_columns:
            video_clauses.append("e.is_active IS NOT FALSE")
        video_updated_expr = (
            "MAX(COALESCE(e.scraped_at, e.updated_at, e.created_at))"
            if {"scraped_at", "updated_at", "created_at"}.issubset(evidence_columns)
            else ("MAX(e.updated_at)" if "updated_at" in evidence_columns else "NULL")
        )
        video_row = as_dict(
            conn.execute(
                "SELECT COUNT(DISTINCT e.kol_pool_id) AS video_kols, COUNT(DISTINCT e.id) AS video_rows, "
                f"{video_updated_expr} AS data_updated_at "
                "FROM vkpi_kol_video_evidence e JOIN vkpi_kol_pool p ON p.id=e.kol_pool_id"
                + where_sql(video_clauses),
                tuple(params),
            ).fetchone()
        )
        video_kols = int0(video_row.get("video_kols"))
        video_rows = int0(video_row.get("video_rows"))
        video_updated_at = video_row.get("data_updated_at")
    else:
        missing.append(
            _missing(
                request,
                "video_evidence",
                "视频证据表不可用",
                "video evidence table is unavailable",
                "视频覆盖率未知",
                "video coverage is unknown",
            )
        )

    deep_kols: int | None = None
    if table_present(conn, "vkpi_kol_llm_deep_analysis_results"):
        deep_clauses = list(clauses) + ["d.status = ?"]
        deep_row = as_dict(
            conn.execute(
                "SELECT COUNT(DISTINCT d.kol_pool_id) AS deep_kols "
                "FROM vkpi_kol_llm_deep_analysis_results d "
                "JOIN vkpi_kol_pool p ON p.id=d.kol_pool_id"
                + where_sql(deep_clauses),
                (*params, "ready"),
            ).fetchone()
        )
        deep_kols = int0(deep_row.get("deep_kols"))
    else:
        missing.append(
            _missing(
                request,
                "deep_analysis",
                "深度分析表不可用",
                "deep-analysis table is unavailable",
                "深度分析覆盖率未知",
                "deep-analysis coverage is unknown",
            )
        )

    response["facts"] = [
        _fact(
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
    ]
    if has_video_source:
        response["facts"].extend(
            [
                _fact(
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
                _fact(
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
            _fact(
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
            _fact(
                "kol.merged_duplicates",
                "已归并重复行",
                "Merged duplicate rows",
                int0(pool_row.get("duplicate_rows")),
                request=request,
                basis=(
                    "统计 vkpi_kol_pool 中已归并的重复从记录",
                    "COUNT(vkpi_kol_pool WHERE duplicate_of_id IS NOT NULL)",
                ),
            )
        )

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
                    if _is_en(request)
                    else "KOL 总数排除 duplicate_of_id 已归并从行。"
                )
                if has_duplicate_column
                else _localized(
                    request,
                    "当前仅能提供原始记录数；主从去重口径不可用。",
                    "Only a raw record count is available; canonical deduplication is unavailable.",
                )
            ],
        }
    )
    response["missing_fields"] = missing
    response["status"] = "partial" if missing else ("empty" if total_kols == 0 else "ready")
    if _is_en(request):
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
    response["evidence"] = [
        {
            "id": "kol-pool-aggregate",
            "kind": "aggregate",
            "source": "vkpi_kol_pool",
            "title": _localized(
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
                "title": _localized(request, "有效视频证据覆盖", "Active video evidence coverage"),
                "observed_at": str(video_updated_at or "") or None,
                "confidence": "high",
            }
        )
    newest = video_updated_at or pool_row.get("data_updated_at")
    _freshness(response, request, now=now, updated_at=newest)
    response["actions"] = [
        {
            "type": "navigate",
            "label": "Open KOL Pool" if _is_en(request) else "打开 KOL Pool",
            "route": "kol-pool",
            "params": {"scope": scope_context.get("applied_mode")},
            "requires_approval": False,
        }
    ]
    return response


def _product_topic_terms(conn: Any, topic: str) -> list[str]:
    terms = [topic.lower()]
    if not table_present(conn, "vkpi_product_aliases"):
        return terms
    try:
        # Reuse the catalog's canonical normalizer.  In particular this keeps
        # aperture and SKU forms aligned (f/1.2 -> f12, f1.8 -> f18,
        # AF-85MM-F18 -> af 85mm f18) instead of maintaining a second dialect.
        from app.domains.products.product_aliases import normalize_alias

        alias_norm = normalize_alias(topic)
        rows = conn.execute(
            "SELECT sku, alias FROM vkpi_product_aliases "
            "WHERE alias_norm=? AND confidence>=? ORDER BY confidence DESC LIMIT ?",
            (alias_norm, 0.75, 8),
        ).fetchall()
        for row in rows:
            item = as_dict(row)
            for key in ("sku", "alias"):
                value = text(item.get(key), 120).lower()
                if value and value not in terms:
                    terms.append(value)
    except Exception:
        return terms
    return terms[:9]


def kol_video_topic_count(
    conn: Any,
    request: NormalizedRequest,
    staff: dict[str, Any] | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    scope_context = actual_scope_context(request, staff)
    response = empty_response(request, intent="kol.video_topic.count", scope=scope_context)
    topic = extract_video_topic(request.query, request.filters)
    if not topic:
        response.update(
            {
                "status": "needs_clarification",
                "answer": (
                    "Which product model or video topic should I count? For example: 26mm EVO."
                    if _is_en(request)
                    else "请补充要统计的产品型号或视频主题，例如“26mm EVO”。"
                ),
                "degraded_reason": "video_topic_missing",
            }
        )
        response["missing_fields"] = [
            _missing(
                request,
                "filters.topic",
                "未识别出视频主题",
                "video topic was not identified",
                "无法计算确认命中的 KOL 数量",
                "the confirmed KOL count cannot be calculated",
            )
        ]
        response["coverage"].update(
            status="unknown",
            notes=[_localized(request, "缺少主题，未执行 SQL 查询。", "No topic; no SQL executed.")],
        )
        return response
    if not table_present(conn, "vkpi_kol_video_evidence") or not table_present(conn, "vkpi_kol_pool"):
        response.update(
            {
                "status": "error",
                "answer": "Video evidence data is unavailable." if _is_en(request) else "视频证据数据源不可用。",
                "degraded_reason": "video_evidence_source_unavailable",
            }
        )
        response["missing_fields"] = [
            _missing(
                request,
                "video_evidence",
                "必需的数据源表不可用",
                "required source table is unavailable",
                "无法计算确认命中",
                "confirmed matches cannot be calculated",
            )
        ]
        return response

    ecols = table_columns(conn, "vkpi_kol_video_evidence")
    clauses, params, missing = pool_predicates(conn, request, staff, alias="p")
    unavailable_filters = [
        item for item in missing if str(item.get("field") or "").startswith("filters.")
    ]
    if unavailable_filters:
        response.update(
            {
                "status": "error",
                "answer": _localized(
                    request,
                    "请求的 KOL 筛选字段不可用，本次未返回宽范围或零值结论。",
                    "A requested KOL filter is unavailable; no broad or zero-valued result was returned.",
                ),
                "degraded_reason": "requested_filter_unavailable",
            }
        )
        response["missing_fields"] = unavailable_filters
        response["coverage"].update(status="unknown")
        return response
    if "is_active" in ecols:
        clauses.append("e.is_active IS NOT FALSE")
    terms = _product_topic_terms(conn, topic)
    searchable_columns = [column for column in ("video_title", "title") if column in ecols]
    if not searchable_columns:
        response.update(
            {
                "status": "error",
                "answer": "Video title fields are unavailable." if _is_en(request) else "视频标题字段不可用。",
                "degraded_reason": "video_title_fields_unavailable",
            }
        )
        response["missing_fields"] = [
            _missing(
                request,
                "video_title",
                "没有可检索的视频标题字段",
                "no searchable title column exists",
                "无法核验主题匹配",
                "topic matching cannot be verified",
            )
        ]
        return response
    # Keep this expression aligned with the proposed trigram functional index.
    # NULL and empty legacy fields both fall through to the second title field.
    title_search_expr = (
        "LOWER(COALESCE(e.video_title, '') || ' ' || COALESCE(e.title, ''))"
        if {"video_title", "title"}.issubset(ecols)
        else f"LOWER(COALESCE(e.{searchable_columns[0]}, ''))"
    )
    topic_clauses: list[str] = []
    topic_params: list[Any] = []
    for term in terms:
        # Percent/underscore are normalized away instead of being allowed to
        # turn user text into LIKE wildcards.
        safe_term = " ".join(term.replace("%", " ").replace("_", " ").split())
        if not safe_term:
            continue
        topic_clauses.append(f"{title_search_expr} LIKE ?")
        topic_params.append(f"%{safe_term}%")
    if not topic_clauses:
        response.update(
            status="needs_clarification",
            answer=_localized(request, "主题过于宽泛，请补充具体产品或内容关键词。", "Topic is too broad to search."),
            degraded_reason="topic_not_searchable",
        )
        return response
    clauses.append("(" + " OR ".join(topic_clauses) + ")")
    params.extend(topic_params)

    deep_join = ""
    analyzed_expr = "NULL"
    if table_present(conn, "vkpi_kol_llm_deep_analysis_results"):
        deep_join = (
            " LEFT JOIN vkpi_kol_llm_deep_analysis_results d "
            "ON d.source_evidence_id=e.id AND d.status='ready'"
        )
        analyzed_expr = "COUNT(DISTINCT d.source_evidence_id)"
    else:
        missing.append(
            _missing(
                request,
                "deep_analysis",
                "深度分析表不可用",
                "deep-analysis table is unavailable",
                "完整视频分析覆盖率未知",
                "full-video analysis coverage is unknown",
            )
        )
    date_columns = [column for column in ("published_at_norm", "posted_at", "publish_date", "updated_at", "created_at") if column in ecols]
    date_expr = "COALESCE(" + ", ".join(f"e.{column}" for column in date_columns) + ")" if date_columns else "NULL"
    summary = as_dict(
        conn.execute(
            "SELECT COUNT(DISTINCT e.kol_pool_id) AS confirmed_kols, "
            "COUNT(DISTINCT e.id) AS confirmed_videos, "
            f"{analyzed_expr} AS analyzed_videos, MAX({date_expr}) AS data_updated_at "
            "FROM vkpi_kol_video_evidence e JOIN vkpi_kol_pool p ON p.id=e.kol_pool_id"
            + deep_join
            + where_sql(clauses),
            tuple(params),
        ).fetchone()
    )
    confirmed_kols = int0(summary.get("confirmed_kols"))
    confirmed_videos = int0(summary.get("confirmed_videos"))
    analyzed_raw = summary.get("analyzed_videos")
    analyzed_videos = int0(analyzed_raw) if analyzed_raw is not None else None

    title_expr = (
        "COALESCE(NULLIF(e.video_title, ''), NULLIF(e.title, ''), '')"
        if {"video_title", "title"}.issubset(ecols)
        else f"COALESCE(e.{searchable_columns[0]}, '')"
    )
    order_expr = date_expr if date_columns else "e.id"
    rows = conn.execute(
        "SELECT e.id AS evidence_id, e.kol_pool_id, e.platform, e.content_url, "
        f"{title_expr} AS title, {date_expr} AS observed_at, "
        "p.display_name, p.handle "
        "FROM vkpi_kol_video_evidence e JOIN vkpi_kol_pool p ON p.id=e.kol_pool_id"
        + where_sql(clauses)
        + f" ORDER BY {order_expr} DESC, e.id DESC LIMIT ?",
        (*params, int(request.filters.get("limit") or 20)),
    ).fetchall()
    response["evidence"] = [
        {
            "id": f"video-{int0(item.get('evidence_id'))}",
            "kind": "video_topic_match",
            "source": "vkpi_kol_video_evidence",
            "title": text(item.get("title"), 180),
            "snippet": text(item.get("display_name") or item.get("handle"), 100),
            "url": text(item.get("content_url"), 500) or None,
            "entity_id": int0(item.get("kol_pool_id")) or None,
            "observed_at": str(item.get("observed_at") or "") or None,
            "confidence": "high",
        }
        for item in (as_dict(row) for row in rows)
    ]
    response["facts"] = [
        _fact(
            "kol.confirmed_topic_match",
            "确认命中的 KOL",
            "Confirmed matching KOLs",
            confirmed_kols,
            request=request,
            basis=(
                "有效视频标题命中已核验主题或产品别名后，统计去重 KOL",
                "COUNT(DISTINCT KOL) where an active evidence title contains a verified topic/alias",
            ),
        ),
        _fact(
            "video.confirmed_topic_match",
            "确认命中的视频",
            "Confirmed matching videos",
            confirmed_videos,
            request=request,
            basis=(
                "仅按标题字段匹配并统计去重的有效视频证据",
                "COUNT(DISTINCT active video evidence) matched only on title fields",
            ),
        ),
    ]
    if analyzed_videos is not None:
        response["facts"].append(
            _fact(
                "video.deep_analyzed_match",
                "已完整深析的命中视频",
                "Deep-analyzed matching videos",
                analyzed_videos,
                request=request,
                basis=(
                    "统计同时具有完成态深度分析的命中视频",
                    "COUNT(DISTINCT matched evidence with ready deep-analysis result)",
                ),
            )
        )
    response["coverage"].update(
        {
            "status": "partial" if missing else "complete",
            "matched_entities": confirmed_kols,
            "evidence_count": confirmed_videos,
            "analyzed_count": analyzed_videos,
            "ratio": round(analyzed_videos / confirmed_videos, 4) if analyzed_videos is not None and confirmed_videos else None,
            "notes": [
                "Confirmed means title evidence only; profile similarity is never counted."
                if _is_en(request)
                else "“确认命中”只计算标题证据；Profile 相似度绝不混入确定人数。",
                "Transcript/body semantic matches are not indexed in v1 and remain a known recall gap."
                if _is_en(request)
                else "v1 尚未索引字幕/正文语义命中，属于已知召回缺口。",
            ],
        }
    )
    response["missing_fields"] = missing + [
        _missing(
            request,
            "transcript_topic_index",
            "v1 仅通过视频证据标题确认命中",
            "v1 confirms matches from evidence titles only",
            "只在字幕或正文中提到主题的视频可能漏检",
            "videos mentioning the topic only in transcript/body may be missed",
        )
    ]
    response["status"] = "empty" if confirmed_kols == 0 else ("partial" if response["missing_fields"] else "ready")
    if _is_en(request):
        response["answer"] = (
            f"{confirmed_kols:,} KOLs are confirmed by {confirmed_videos:,} active video-title records for “{topic}”."
        )
    else:
        response["answer"] = f"“{topic}”目前确认命中 {confirmed_kols:,} 位 KOL、{confirmed_videos:,} 条有效视频标题证据。"
    _freshness(response, request, now=now, updated_at=summary.get("data_updated_at"))
    response["actions"] = [
        {
            "type": "navigate",
            "label": "Open matching KOLs" if _is_en(request) else "查看命中 KOL",
            "route": "kol-pool",
            "params": {"query": topic},
            "requires_approval": False,
        },
        {
            "type": "propose_analysis",
            "label": "Request full-video analysis" if _is_en(request) else "申请完整视频分析",
            "route": "dashboard",
            "params": {"topic": topic, "matched_videos": confirmed_videos},
            "requires_approval": True,
        },
    ]
    return response


def project_search(
    conn: Any,
    request: NormalizedRequest,
    staff: dict[str, Any] | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    scope_context = actual_scope_context(request, staff)
    response = empty_response(request, intent="project.search", scope=scope_context)
    pcols = table_columns(conn, "vkpi_projects")
    if not pcols:
        response.update(
            {
                "status": "error",
                "answer": "Project data source is unavailable." if _is_en(request) else "Project 数据源不可用。",
                "degraded_reason": "project_source_unavailable",
            }
        )
        response["missing_fields"] = [
            _missing(
                request,
                "vkpi_projects",
                "项目数据源表不可用",
                "source table is unavailable",
                "无法搜索项目",
                "projects cannot be searched",
            )
        ]
        return response
    keyword = extract_project_keyword(request.query, request.filters)
    if "stage_status" not in pcols:
        response.update(
            {
                "status": "error",
                "answer": _localized(
                    request,
                    "项目删除状态字段不可用，本次未返回可能包含已删除项目的结果。",
                    "The project deletion-status field is unavailable; potentially deleted projects were not returned.",
                ),
                "degraded_reason": "project_deletion_filter_unavailable",
            }
        )
        response["missing_fields"] = [
            _missing(
                request,
                "stage_status",
                "无法核验项目删除状态",
                "project deletion state cannot be verified",
                "项目检索按安全策略不可用",
                "project search is intentionally unavailable",
            )
        ]
        return response
    requested_staff_id = (
        int(scope_context["effective_staff_id"])
        if scope_context.get("applied_mode") == "own" and scope_context.get("effective_staff_id")
        else request.scope.requested_staff_id
    )
    scope_sql, scope_params = access_scope.project_filter("p", staff, requested_staff_id)
    clauses = ["COALESCE(p.stage_status, '') <> ?"]
    params: list[Any] = ["deleted"]
    if scope_sql:
        clauses.append(scope_sql)
        params.extend(scope_params)
    searchable = [column for column in ("project_name", "product_name", "product_sku", "platform") if column in pcols]
    if keyword and not searchable:
        response.update(
            {
                "status": "error",
                "answer": _localized(
                    request,
                    "项目关键词字段不可用，本次未把关键词静默忽略。",
                    "Project keyword fields are unavailable; the keyword was not silently ignored.",
                ),
                "degraded_reason": "project_keyword_fields_unavailable",
            }
        )
        response["missing_fields"] = [
            _missing(
                request,
                "filters.keyword",
                "没有可检索的项目关键词字段",
                "no searchable project keyword columns exist",
                "关键词检索结果不可用",
                "keyword search result is unavailable",
            )
        ]
        return response
    if keyword:
        clauses.append("(" + " OR ".join(f"LOWER(COALESCE(p.{column}, '')) LIKE ?" for column in searchable) + ")")
        safe_keyword = " ".join(keyword.lower().replace("%", " ").replace("_", " ").split())
        params.extend([f"%{safe_keyword}%"] * len(searchable))
    stage = text(request.filters.get("stage"), 80).lower()
    if stage:
        if "stage" not in pcols:
            response.update(
                {
                    "status": "error",
                    "answer": _localized(
                        request,
                        "项目阶段字段不可用，本次未把阶段筛选静默忽略。",
                        "The project stage field is unavailable; the stage filter was not silently ignored.",
                    ),
                    "degraded_reason": "project_stage_filter_unavailable",
                }
            )
            response["missing_fields"] = [
                _missing(
                    request,
                    "filters.stage",
                    "项目阶段字段不可用",
                    "project stage column is unavailable",
                    "阶段筛选结果不可用",
                    "stage-filtered result is unavailable",
                )
            ]
            return response
        clauses.append("LOWER(COALESCE(p.stage, '')) = ?")
        params.append(stage)
    updated_expr = "MAX(p.updated_at)" if "updated_at" in pcols else "NULL"
    total_row = as_dict(
        conn.execute(
            f"SELECT COUNT(*) AS n, {updated_expr} AS data_updated_at FROM vkpi_projects p"
            + where_sql(clauses),
            tuple(params),
        ).fetchone()
    )
    total = int0(total_row.get("n"))
    select_columns = [
        column
        for column in (
            "id",
            "project_uid",
            "project_name",
            "product_sku",
            "product_name",
            "platform",
            "stage",
            "stage_status",
            "priority",
            "assigned_staff_id",
            "updated_at",
        )
        if column in pcols
    ]
    rows = conn.execute(
        "SELECT " + ", ".join(f"p.{column}" for column in select_columns) + " FROM vkpi_projects p"
        + where_sql(clauses)
        + (
            " ORDER BY p.updated_at DESC, p.id DESC LIMIT ?"
            if {"updated_at", "id"}.issubset(pcols)
            else (" ORDER BY p.id DESC LIMIT ?" if "id" in pcols else " LIMIT ?")
        ),
        (*params, int(request.filters.get("limit") or 20)),
    ).fetchall()
    response["evidence"] = [
        {
            "id": f"project-{int0(item.get('id'))}",
            "kind": "project",
            "source": "vkpi_projects",
            "title": text(item.get("project_name"), 160),
            "snippet": " · ".join(
                part
                for part in (
                    text(item.get("product_name") or item.get("product_sku"), 80),
                    text(item.get("stage"), 40),
                    text(item.get("platform"), 40),
                )
                if part
            ),
            "entity_id": int0(item.get("id")) or None,
            "observed_at": str(item.get("updated_at") or "") or None,
            "confidence": "high",
        }
        for item in (as_dict(row) for row in rows)
    ]
    response["facts"] = [
        _fact(
            "project.match_count",
            "可见匹配项目",
            "Visible matching projects",
            total,
            request=request,
            basis=(
                "在服务端项目权限与删除状态过滤后统计匹配项目",
                "COUNT(vkpi_projects after server-side project scope and deleted-state filters)",
            ),
        )
    ]
    response["coverage"].update(
        {
            "status": "complete",
            "matched_entities": total,
            "evidence_count": len(response["evidence"]),
            "total_scope": total,
            "notes": [
                "Deleted projects are excluded before search; project RBAC is applied in SQL."
                if _is_en(request)
                else "已删除项目在搜索前排除；项目权限在 SQL 查询前置生效。"
            ],
        }
    )
    response["status"] = "empty" if total == 0 else "ready"
    if _is_en(request):
        response["answer"] = f"Found {total:,} visible active projects" + (f" matching “{keyword}”." if keyword else ".")
    else:
        response["answer"] = f"找到 {total:,} 个当前账号可见的有效项目" + (f"，匹配“{keyword}”。" if keyword else "。")
    _freshness(response, request, now=now, updated_at=total_row.get("data_updated_at"))
    response["actions"] = [
        {
            "type": "navigate",
            "label": "Open Projects" if _is_en(request) else "打开 Project",
            "route": "projects",
            "params": {"query": keyword},
            "requires_approval": False,
        }
    ]
    return response

HANDLERS = {
    "kol.pool.overview": kol_pool_overview,
    "kol.video_topic.count": kol_video_topic_count,
    "project.search": project_search,
    "market.viltrox.weekly_voice": market_weekly_voice,
}

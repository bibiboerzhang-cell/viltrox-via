"""Database-backed candidate loading for KOL profile recall."""
from __future__ import annotations

from typing import Any, Callable

from app.db.connection import get_conn
from app.domains.kol.discovery_filters import LOW_REACH_FLAG_LIKE_PATTERN
from app.domains.kol.pool_common import _table_columns
from app.domains.kol.profile_recall_contract import (
    COLLECTION_NAME,
    MAX_CANDIDATE_LIMIT,
    METHOD,
    RecallHit,
)
from app.domains.kol.profile_recall_country_gate import country_hard_filter
from app.domains.kol.profile_recall_language_gate import (
    INFERRED_POOL_COLUMNS,
    language_hard_filter,
)
from app.domains.kol.profile_recall_precision import (
    LEXICAL_METHOD,
    lexical_recall_candidates,
)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _entry_rows(
    kol_pool_ids: list[int],
    *,
    get_connection: Callable[[], Any] = get_conn,
    table_columns: Callable[[Any, str], set[str]] = _table_columns,
) -> dict[int, dict[str, Any]]:
    if not kol_pool_ids:
        return {}
    placeholders = ",".join(["?"] * len(kol_pool_ids))
    conn = get_connection()
    pool_columns = table_columns(conn, "vkpi_kol_pool")
    optional_columns = (
        "avg_likes",
        "source_type",
        "source_ref",
        "real_er",
        "real_er_sample_n",
        "real_er_computed_at",
        "real_er_method",
        "last_seen_at",
        "updated_at",
        # 垂类多路取证的真信号(迁移 291 回填)。列未迁移的旧布局自动退成 NULL =
        # 这一路没有信号,判定照样跑,只是这个人在这一路上算未知。
        "topic_details_json",
        "tagged_brands_json",
        # 迁移 305 的推断语言四列(与自报 language 分列存)。同上:列没迁移就退 NULL,
        # 那个人只是在语言这一路上算未知 —— 读不到绝不等于「不合格」。
        *INFERRED_POOL_COLUMNS,
    )
    optional_select = ",\n               ".join(
        f"p.{column}" if column in pool_columns else f"NULL AS {column}"
        for column in optional_columns
    )
    metric_columns = [
        column
        for column in ("followers", "avg_views", "avg_likes", "avg_comments", "engagement_rate")
        if column in pool_columns
    ]
    truth_raw_select = (
        "CASE WHEN "
        + " OR ".join(f"p.{column} = 0" for column in metric_columns)
        + " THEN p.raw_platform_data ELSE NULL END AS metric_truth_raw_platform_data"
        if metric_columns and "raw_platform_data" in pool_columns
        else "NULL AS metric_truth_raw_platform_data"
    )
    low_reach_select = (
        "(p.raw_platform_data LIKE ?) AS low_reach_flagged"
        if "raw_platform_data" in pool_columns
        else "0 AS low_reach_flagged"
    )
    prefix_params: tuple[Any, ...] = (LOW_REACH_FLAG_LIKE_PATTERN,) if "raw_platform_data" in pool_columns else ()
    rows = conn.execute(
        f"""
        SELECT e.kol_pool_id,
               e.profile_type,
               e.creator_type_score,
               e.reviewer_type_score,
               e.type_reason,
               e.type_method,
               e.sufficiency,
               e.profile_text,
               p.handle,
               p.display_name,
               p.platform,
               p.profile_url,
               p.avatar_url,
               p.followers,
               p.avg_views,
               p.avg_comments,
               p.engagement_rate,
               {optional_select},
               p.bio,
               p.country,
               p.language,
               p.primary_topic,
               p.content_style,
               p.secondary_topics_json,
               p.brand_collaborations_json,
               {truth_raw_select},
               {low_reach_select}
        FROM vkpi_kol_profile_index_entries e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE e.collection_name = ?
          AND e.method = ?
          AND e.status = 'ready'
          AND e.profile_type IN ('creator', 'reviewer', 'mixed')
          AND p.duplicate_of_id IS NULL
          AND e.kol_pool_id IN ({placeholders})
        """,
        (*prefix_params, COLLECTION_NAME, METHOD, *kol_pool_ids),
    ).fetchall()
    return {int(row["kol_pool_id"]): dict(row) for row in rows}


def _pool_rows_fallback(
    kol_pool_ids: list[int],
    *,
    get_connection: Callable[[], Any] = get_conn,
    table_columns: Callable[[Any, str], set[str]] = _table_columns,
) -> dict[int, dict]:
    """硬兜底(2026-07-02):索引表缺行(新 KOL 未入索引/表漂移)时按 pool 行合成可展示候选。

    背景:此前索引行缺失的命中被整批丢弃,索引表一旦空表整个文本搜索静默归零(本日事故)。
    合成行 profile_type 留空 -> 展示为「未分类」,type 分 0,profile_text 用 bio 顶,绝不触碰 fit。
    """
    if not kol_pool_ids:
        return {}
    placeholders = ", ".join(["?"] * len(kol_pool_ids))
    conn = get_connection()
    pool_columns = table_columns(conn, "vkpi_kol_pool")
    optional_columns = (
        "avg_likes",
        "source_type",
        "source_ref",
        "real_er",
        "real_er_sample_n",
        "real_er_computed_at",
        "real_er_method",
        "last_seen_at",
        "updated_at",
        # 垂类多路取证的真信号(迁移 291 回填)。列未迁移的旧布局自动退成 NULL =
        # 这一路没有信号,判定照样跑,只是这个人在这一路上算未知。
        "topic_details_json",
        "tagged_brands_json",
        # 迁移 305 的推断语言四列(与自报 language 分列存)。同上:列没迁移就退 NULL,
        # 那个人只是在语言这一路上算未知 —— 读不到绝不等于「不合格」。
        *INFERRED_POOL_COLUMNS,
    )
    optional_select = ", ".join(
        f"p.{column}" if column in pool_columns else f"NULL AS {column}"
        for column in optional_columns
    )
    metric_columns = [
        column
        for column in ("followers", "avg_views", "avg_likes", "avg_comments", "engagement_rate")
        if column in pool_columns
    ]
    truth_raw_select = (
        "CASE WHEN "
        + " OR ".join(f"p.{column} = 0" for column in metric_columns)
        + " THEN p.raw_platform_data ELSE NULL END AS metric_truth_raw_platform_data"
        if metric_columns and "raw_platform_data" in pool_columns
        else "NULL AS metric_truth_raw_platform_data"
    )
    low_reach_select = (
        "(p.raw_platform_data LIKE ?) AS low_reach_flagged"
        if "raw_platform_data" in pool_columns
        else "0 AS low_reach_flagged"
    )
    prefix_params: tuple[Any, ...] = (LOW_REACH_FLAG_LIKE_PATTERN,) if "raw_platform_data" in pool_columns else ()
    rows = conn.execute(
        f"""
        SELECT p.id AS kol_pool_id, p.platform, p.handle, p.display_name, p.profile_url,
               p.avatar_url, p.followers, p.avg_views, p.avg_comments, p.engagement_rate,
               {optional_select},
               p.bio, p.country, p.language, p.primary_topic, p.content_style,
               p.secondary_topics_json, p.brand_collaborations_json,
               {truth_raw_select},
               {low_reach_select}
        FROM vkpi_kol_pool p
        WHERE p.duplicate_of_id IS NULL AND p.id IN ({placeholders})
        """,
        (*prefix_params, *(int(x) for x in kol_pool_ids)),
    ).fetchall()
    out: dict[int, dict] = {}
    for row in rows:
        d = dict(row)
        d.setdefault("profile_type", "unknown")
        d.setdefault("profile_text", str(d.get("bio") or "")[:600])
        d.setdefault("type_reason", "索引未覆盖,按池内资料兜底展示")
        # Missing type evidence is unknown, never a fabricated zero score.
        d.setdefault("creator_type_score", None)
        d.setdefault("reviewer_type_score", None)
        d.setdefault("sufficiency", "missing_profile_index")
        out[int(d["kol_pool_id"])] = d
    return out


def _pool_text_fallback_hits(
    query_text: str,
    candidate_limit: int,
    *,
    include_relevance_backfill: bool = False,
    operator_query_text: str = "",
    filters: dict[str, Any] | None = None,
    get_connection: Callable[[], Any] = get_conn,
    lexical_recall: Callable[..., dict[str, Any]] = lexical_recall_candidates,
) -> list[RecallHit]:
    """召回永不零红线兜底(记忆 vkpi-text-search-resurrection「recall 加 pool 兜底永不零结果」)。

    向量召回不可用时——本地内嵌 Qdrant 单实例文件锁被并发召回撞锁(实测 6 并发下有请求返
    'Storage folder ... already accessed by another instance')、库缺失、embedding 失败/预算
    超限,或该 query 向量库零命中——按 pool 文本(handle/display_name/bio/primary_topic/
    content_style)直接召回可展示候选,保证并发/降级下召回不返 0。

    兜底 hit vector_score=0(排在真向量命中之后),进与向量命中同一展示管线(索引缺行由
    _pool_rows_fallback 合成);绝不触 viltrox_fit_score。文本无命中(空 query / 生僻词)时退到
    池内头部行,红线永不零。
    """
    limit = max(1, min(MAX_CANDIDATE_LIMIT, int(candidate_limit or 50)))
    conn = get_connection()
    hits: list[RecallHit] = []
    seen: set[int] = set()

    def _collect(rows: list[Any], *, source: str) -> None:
        for row in rows:
            try:
                kid = int(dict(row).get("kol_pool_id") or 0)
            except (TypeError, ValueError):
                kid = 0
            if kid > 0 and kid not in seen:
                seen.add(kid)
                hits.append(
                    RecallHit(
                        kol_pool_id=kid,
                        vector_score=None,
                        qdrant_point_id=source,
                        retrieval_method="pool_backfill",
                        retrieval_tier="backfill",
                    )
                )

    text = " ".join(str(query_text or "").split()).strip()
    if text:
        lexical = lexical_recall(
            text,
            operator_query=operator_query_text,
            candidate_limit=limit,
            conn=conn,
            hard_filters=filters,
        )
        for row in lexical.get("items") or []:
            try:
                kid = int(row.get("kol_pool_id") or 0)
            except (TypeError, ValueError):
                kid = 0
            if kid <= 0 or kid in seen:
                continue
            seen.add(kid)
            lexical_score = _optional_float(row.get("lexical_score"))
            hits.append(
                RecallHit(
                    kol_pool_id=kid,
                    vector_score=None,
                    qdrant_point_id="",
                    lexical_score=lexical_score,
                    retrieval_score=lexical_score,
                    retrieval_method=LEXICAL_METHOD,
                    retrieval_tier=str(row.get("retrieval_tier") or "relaxed"),
                    retrieval_meta={
                        key: row.get(key)
                        for key in (
                            "relaxed_reason",
                            "matched_terms",
                            "factual_matched_terms",
                            "factual_anchor_terms",
                            "required_factual_anchor_groups",
                            "factual_scene_terms",
                            "matched_term_sources",
                            "source_scores",
                            "derived_profile_strict_eligible",
                        )
                    },
                )
            )
    if not include_relevance_backfill:
        return hits
    # A requested result count is a contract, not a reason to disguise broad
    # pool rows as query matches.  When enabled, append deterministic pool
    # candidates with a distinct source marker.  The selection stage exposes
    # them as match_tier=backfill and never relaxes explicit hard filters.
    hard_filters = filters if isinstance(filters, dict) else {}
    broad_clauses: list[str] = []
    broad_params: list[Any] = []
    requested_platforms = [
        str(value).strip().lower()
        for value in hard_filters.get("platforms") or []
        if str(value).strip()
    ]
    if requested_platforms:
        broad_clauses.append(
            "LOWER(COALESCE(p.platform, '')) IN (" + ",".join("?" for _ in requested_platforms) + ")"
        )
        broad_params.extend(requested_platforms)
    if hard_filters.get("followers_min") not in (None, ""):
        broad_clauses.append("p.followers IS NOT NULL AND p.followers >= ?")
        broad_params.append(int(hard_filters["followers_min"]))
    if hard_filters.get("followers_max") not in (None, ""):
        broad_clauses.append("p.followers IS NOT NULL AND p.followers <= ?")
        broad_params.append(int(hard_filters["followers_max"]))
    # 国家下推与词法腿共用归一化闭包(见 profile_recall_country_gate.country_hard_filter):
    # 只按原值字面量筛,库里写「美国」而操作员点了国家码 US 的人一个都捞不回来。
    country_clause, country_params = country_hard_filter(hard_filters)
    if country_clause:
        broad_clauses.append(country_clause)
        broad_params.extend(country_params)
    # 语言下推同时认「自报」与「推断」两列(见 profile_recall_language_gate.language_hard_filter):
    # 只按 p.language 筛,会在取数腿就把 language 为空的人剔光,推断值再准也进不了搜索。
    language_clause, language_params = language_hard_filter(hard_filters, conn, _table_columns)
    if language_clause:
        broad_clauses.append(language_clause)
        broad_params.extend(language_params)
    broad_where = "".join(f" AND {clause}" for clause in broad_clauses)
    _collect(
        conn.execute(
            f"""
            SELECT p.id AS kol_pool_id
            FROM vkpi_kol_pool p
            WHERE p.duplicate_of_id IS NULL
              {broad_where}
            ORDER BY COALESCE(p.followers, 0) DESC, p.id DESC
            LIMIT ?
            """,
            (*broad_params, max(limit, min(MAX_CANDIDATE_LIMIT, limit * 4))),
        ).fetchall(),
        source="pool_relevance_backfill",
    )
    return hits[:limit]

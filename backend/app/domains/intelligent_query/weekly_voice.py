"""Strict seven-day Viltrox market-voice deterministic query."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from app.domains.intelligent_query.common import (
    fact as _fact,
    freshness as _freshness,
    is_en as _is_en,
    latest_observed_at as _latest_observed_at,
    localized as _localized,
    missing as _missing,
)
from app.domains.intelligent_query.contracts import NormalizedRequest, empty_response
from app.domains.intelligent_query.repository import (
    actual_scope_context,
    as_dict,
    int0,
    table_columns,
    table_present,
    text,
    where_sql,
)

_POSITIVE_CUES = (
    "love",
    "great",
    "excellent",
    "amazing",
    "sharp",
    "beautiful",
    "good value",
    "recommend",
    "喜欢",
    "很好",
    "优秀",
    "锐利",
    "性价比",
    "推荐",
)

_WEEKLY_COMPLAINT_RULES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("autofocus", "对焦", "Autofocus", ("autofocus", "auto focus", "focus", "hunting", "对焦", "跑焦", "追焦")),
    ("image_quality", "画质", "Image quality", ("sharp", "soft", "blurry", "image quality", "fringing", "flare", "画质", "锐度", "紫边", "眩光")),
    ("build_quality", "做工/品控", "Build quality", ("build quality", "quality control", "loose", "rattle", "broken", "做工", "品控", "松动", "坏了")),
    ("price", "价格", "Price", ("price", "expensive", "overpriced", "价格", "太贵", "性价比")),
    ("compatibility", "兼容性/固件", "Compatibility / firmware", ("compatib", "firmware", "not supported", "update", "兼容", "固件", "适配", "卡口")),
    ("size_weight", "体积/重量", "Size / weight", ("heavy", "bulky", "weight", "huge", "太重", "重量", "笨重", "太大")),
)

_WEEKLY_NEGATIVE_CUES = (
    "not ", "n't", "issue", "problem", "bad", "poor", "terrible", "disappoint",
    "slow", "broken", "fail", "hunting", "too expensive", "too heavy", "too big",
    "不行", "不好", "不能", "问题", "坏了", "太贵", "太重", "太大", "失望", "跑焦", "差",
)

_WEEKLY_WISH_CUES = (
    "wish", "hope", "please make", "please release", "would love", "we need", "i need",
    "can you make", "when will", "希望", "求出", "出一个", "什么时候出", "需要",
)

_COMPLAINT_LABEL_EN = {
    "对焦": "Autofocus",
    "画质": "Image quality",
    "做工/品控": "Build quality",
    "价格": "Price",
    "兼容性/固件": "Compatibility / firmware",
    "体积/重量": "Size / weight",
}


def _weekly_rule_signals(docs: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Small dependency-free bilingual signal rules for the real-time path."""
    categories: list[dict[str, Any]] = []
    matched_doc_ids: set[int] = set()
    for key, label_zh, label_en, topic_terms in _WEEKLY_COMPLAINT_RULES:
        hits = [
            doc
            for doc in docs
            if any(term in doc["lower"] for term in topic_terms)
            and any(cue in doc["lower"] for cue in _WEEKLY_NEGATIVE_CUES)
        ]
        matched_doc_ids.update(id(doc) for doc in hits)
        categories.append(
            {
                "key": key,
                "label": label_zh,
                "label_en": label_en,
                "count": len(hits),
                "quotes": [
                    {
                        "text": text(doc.get("text"), 220),
                        "platform": doc.get("platform") or "",
                        "at": doc.get("at") or "",
                        "source": doc.get("source") or "",
                    }
                    for doc in sorted(
                        hits,
                        key=lambda item: (item.get("likes", 0), item.get("at", "")),
                        reverse=True,
                    )[:3]
                ],
            }
        )
    categories.sort(key=lambda item: (-int0(item.get("count")), str(item.get("key") or "")))
    wish_docs = [doc for doc in docs if any(cue in doc["lower"] for cue in _WEEKLY_WISH_CUES)]
    return (
        {
            "status": "ready" if matched_doc_ids else "empty",
            "total_matched": len(matched_doc_ids),
            "categories": categories,
        },
        {
            "status": "ready" if wish_docs else "empty",
            "total": len(wish_docs),
            "items": wish_docs[:10],
        },
    )


def _brand_match_sql(column: str, terms: list[str]) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append(f"LOWER({column}) LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped}%")
    return "(" + " OR ".join(clauses) + ")", params


def _voice_docs(
    conn: Any,
    request: NormalizedRequest,
    brand_terms: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any], Any, int, bool]:
    docs: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}
    newest: Any = None
    excluded_count = 0
    any_truncated = False
    start, end = request.window.start_iso, request.window.end_iso
    comment_columns = table_columns(conn, "vkpi_comments")
    required_comment_columns = {"id", "comment_text", "created_at"}
    if comment_columns and required_comment_columns.issubset(comment_columns):
        brand_sql, brand_params = _brand_match_sql("comment_text", brand_terms)
        count_row = as_dict(
            conn.execute(
                "SELECT COUNT(*) AS candidate_count, "
                f"SUM(CASE WHEN {brand_sql} THEN 1 ELSE 0 END) AS matched_count "
                "FROM vkpi_comments WHERE comment_text IS NOT NULL AND comment_text <> '' "
                "AND created_at>=? AND created_at<?",
                (*brand_params, start, end),
            ).fetchone()
        )
        candidate_count = int0(count_row.get("candidate_count"))
        matched_count = int0(count_row.get("matched_count"))
        row_limit = 5000
        platform_expr = "platform" if "platform" in comment_columns else "''"
        likes_expr = "likes_count" if "likes_count" in comment_columns else "0"
        rows = conn.execute(
            f"SELECT id, {platform_expr} AS platform, comment_text, "
            f"{likes_expr} AS likes_count, created_at AS at_ts FROM vkpi_comments "
            "WHERE comment_text IS NOT NULL AND comment_text <> '' "
            "AND created_at>=? AND created_at<? "
            f"AND {brand_sql} "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (start, end, *brand_params, row_limit),
        ).fetchall()
        for row in rows:
            item = as_dict(row)
            body = text(item.get("comment_text"), 500)
            if body:
                docs.append(
                    {
                        "id": int0(item.get("id")),
                        "text": body,
                        "lower": body.lower(),
                        "author": "",
                        "platform": text(item.get("platform"), 40),
                        "at": str(item.get("at_ts") or "")[:25],
                        "likes": int0(item.get("likes_count")),
                        "source": "vkpi_comments",
                        "intent_tag": "",
                    }
                )
        newest = docs[0].get("at") if docs else None
        truncated = matched_count > row_limit
        any_truncated = any_truncated or truncated
        excluded_count += max(0, candidate_count - matched_count)
        sources["comments"] = {
            "status": "ready" if rows else "empty",
            "count": len(rows),
            "matched_count": matched_count,
            "candidate_count": candidate_count,
            "truncated": truncated,
        }
    elif comment_columns:
        sources["comments"] = {
            "status": "unavailable",
            "reason": "content_event_time_or_text_column_unavailable",
            "required_columns": sorted(required_comment_columns),
            "count": 0,
            "matched_count": 0,
            "candidate_count": 0,
            "truncated": False,
        }
    else:
        sources["comments"] = {
            "status": "absent",
            "count": 0,
            "matched_count": 0,
            "candidate_count": 0,
            "truncated": False,
        }
    # reply_queue.created_at is queue-ingestion time, not a market content
    # event timestamp.  Keep it visible in source status, but never mix those
    # rows into a "this week in the market" count.
    if table_present(conn, "vkpi_reply_queue"):
        sources["intent_queue"] = {
            "status": "unavailable",
            "reason": "ingestion_time_only_not_market_event_time",
            "time_semantics": "internal_queue_ingestion",
            "count": 0,
            "matched_count": 0,
            "candidate_count": 0,
            "truncated": False,
        }
    else:
        sources["intent_queue"] = {
            "status": "absent",
            "count": 0,
            "matched_count": 0,
            "candidate_count": 0,
            "truncated": False,
        }
    docs.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
    newest = docs[0].get("at") if docs else newest
    return docs, sources, newest, excluded_count, any_truncated


def _viltrox_market_terms(conn: Any) -> list[str]:
    """Return bounded, explicit Viltrox brand terms for brand scoping.

    A weekly market answer must never treat every collected comment as a
    Viltrox comment.  High-confidence product aliases still describe a product,
    not necessarily the brand being evaluated, so aliases alone are not used as
    Viltrox proof in this fail-closed first version.
    """
    del conn
    terms: list[str] = [
        "viltrox",
        "@viltrox",
        "viltrox official",
        "viltroxofficial",
        "viltrox.global",
        "viltrox_global",
        "viltrox studio",
        "weeylite",
        "唯卓仕",
    ]
    return list(
        dict.fromkeys(
            term.strip()
            for term in terms
            if len(term.strip()) >= 4 or any(ord(ch) > 127 for ch in term.strip())
        )
    )[:40]


def _viltrox_video_mentions(
    conn: Any,
    request: NormalizedRequest,
    terms: list[str],
) -> tuple[int, list[dict[str, Any]], Any, dict[str, Any]]:
    if not table_present(conn, "vkpi_kol_video_evidence"):
        return 0, [], None, {"status": "absent", "count": 0, "reason": "video_source_absent"}
    if not table_present(conn, "vkpi_kol_pool"):
        return 0, [], None, {"status": "unavailable", "count": 0, "reason": "kol_pool_dependency_absent"}
    columns = table_columns(conn, "vkpi_kol_video_evidence")
    title_cols = [column for column in ("video_title", "title") if column in columns]
    # updated_at/created_at are ingestion or enrichment timestamps and must not
    # be used to claim a video occurred in this market week.
    date_cols = [
        column
        for column in ("published_at_norm", "posted_at", "publish_date")
        if column in columns
    ]
    required = {"id", "kol_pool_id"}
    if not title_cols or not date_cols or not required.issubset(columns):
        return 0, [], None, {
            "status": "unavailable",
            "count": 0,
            "reason": "video_title_or_content_event_time_unavailable",
            "content_time_columns": date_cols,
        }
    title_expr = (
        "COALESCE(NULLIF(e.video_title, ''), NULLIF(e.title, ''), '')"
        if {"video_title", "title"}.issubset(columns)
        else f"COALESCE(e.{title_cols[0]}, '')"
    )
    title_search_expr = (
        "LOWER(COALESCE(e.video_title, '') || ' ' || COALESCE(e.title, ''))"
        if {"video_title", "title"}.issubset(columns)
        else f"LOWER(COALESCE(e.{title_cols[0]}, ''))"
    )
    # These are typed DATE/TIMESTAMP columns in Postgres.  Do not compare them
    # with an empty string via NULLIF; that is invalid for typed dates.
    date_expr = "COALESCE(" + ", ".join(f"e.{column}" for column in date_cols) + ")"
    title_terms = list(terms)[:40]
    title_match_sql = "(" + " OR ".join(f"{title_search_expr} LIKE ? ESCAPE '\\'" for _ in title_terms) + ")"
    clauses = [title_match_sql, f"{date_expr}>=?", f"{date_expr}<?"]
    if "is_active" in columns:
        clauses.append("e.is_active IS NOT FALSE")
    params: list[Any] = [
        *(
            "%"
            + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            + "%"
            for term in title_terms
        ),
        request.window.start_iso,
        request.window.end_iso,
    ]
    count_row = as_dict(
        conn.execute(
            f"SELECT COUNT(DISTINCT e.id) AS n, MAX({date_expr}) AS newest "
            "FROM vkpi_kol_video_evidence e JOIN vkpi_kol_pool p ON p.id=e.kol_pool_id"
            + where_sql(
                clauses
                + (["p.duplicate_of_id IS NULL"] if "duplicate_of_id" in table_columns(conn, "vkpi_kol_pool") else [])
            ),
            tuple(params),
        ).fetchone()
    )
    content_url_expr = "e.content_url" if "content_url" in columns else "NULL"
    rows = conn.execute(
        f"SELECT e.id, e.kol_pool_id, {content_url_expr} AS content_url, {title_expr} AS title, {date_expr} AS observed_at "
        "FROM vkpi_kol_video_evidence e JOIN vkpi_kol_pool p ON p.id=e.kol_pool_id"
        + where_sql(
            clauses
            + (["p.duplicate_of_id IS NULL"] if "duplicate_of_id" in table_columns(conn, "vkpi_kol_pool") else [])
        )
        + f" ORDER BY {date_expr} DESC, e.id DESC LIMIT ?",
        (*params, 6),
    ).fetchall()
    count = int0(count_row.get("n"))
    return (
        count,
        [as_dict(row) for row in rows],
        count_row.get("newest"),
        {
            "status": "ready" if count else "empty",
            "count": count,
            "time_semantics": "content_publication_event",
        },
    )


def market_weekly_voice(
    conn: Any,
    request: NormalizedRequest,
    staff: dict[str, Any] | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    scope_context = actual_scope_context(request, staff)
    if request.scope.mode == "own" or request.scope.requested_staff_id is not None:
        from app.domains.intelligent_query.contracts import QueryScopeDenied

        raise QueryScopeDenied(
            "weekly market sources have no verified staff ownership linkage"
            if _is_en(request)
            else "周度市场声音源尚无可核验的员工归属关系"
        )
    # Market voice is an organization-wide shared read asset in the existing
    # product.  Its source rows have no staff ownership columns, so the trace
    # must say shared_global rather than pretending an own-scope filter ran.
    scope_context = {
        **scope_context,
        "applied_mode": "shared_global",
        "effective_staff_id": None,
        "scope_basis": "organization_shared_market_asset",
    }
    # This first version is contractually a strict seven-day report.  A caller
    # may provide a custom range for other future intents, but it must not make
    # this endpoint silently reuse the legacy 30-day aggregate.
    if request.window.preset not in {"7d", "custom"} or request.window.end - request.window.start != timedelta(days=7):
        from app.domains.intelligent_query.contracts import QueryValidationError

        raise QueryValidationError("market.viltrox.weekly_voice requires an exact seven-day time_range")
    brand_terms = _viltrox_market_terms(conn)
    response = empty_response(request, intent="market.viltrox.weekly_voice", scope=scope_context)
    docs, sources, docs_newest, excluded_count, source_truncated = _voice_docs(
        conn, request, brand_terms
    )
    for source_key, source_state in sources.items():
        source_state["scope"] = (
            _localized(
                request,
                "内部回复队列，仅有入队时间，已排除在市场周口径之外",
                "internal reply queue; ingestion time only, excluded from weekly market scope",
            )
            if source_key == "intent_queue"
            else _localized(
                request,
                "明确命中 Viltrox 品牌词且带内容发生时间的文本",
                "explicit Viltrox brand-term text with content-event time",
            )
        )
    video_mentions, video_rows, video_newest, video_source = _viltrox_video_mentions(
        conn, request, brand_terms
    )
    sources["videos"] = video_source
    text_source_available = sources["comments"].get("status") in {"ready", "empty"}
    video_source_available = video_source.get("status") in {"ready", "empty"}
    if not text_source_available and not video_source_available:
        response.update(
            {
                "status": "error",
                "answer": _localized(
                    request,
                    "本周市场声音的内容时间数据源不可用，本次没有把缺源误报为零。",
                    "Content-event-time sources for this week's market voice are unavailable; missing sources were not reported as zero.",
                ),
                "degraded_reason": "weekly_market_sources_unavailable",
            }
        )
        response["coverage"].update(
            status="unknown",
            notes=[
                _localized(
                    request,
                    "评论必须有内容发生时间，视频必须有发布时间；入库时间不参与周度口径。",
                    "Comments require content-event time and videos require publication time; ingestion timestamps are excluded.",
                )
            ],
        )
        response["missing_fields"] = [
            _missing(
                request,
                "weekly_market_event_sources",
                "没有可用且带内容发生时间的周度市场数据源",
                "no queryable weekly market source has content-event timestamps",
                "无法核验本周样本与信号",
                "weekly samples and signals cannot be verified",
            )
        ]
        response["trace"]["source_status"] = sources
        _freshness(response, request, now=now, updated_at=None, windowed=True)
        return response

    complaints, wishlist = _weekly_rule_signals(docs)
    negative_count = int0(complaints.get("total_matched"))
    wishlist_count = int0(wishlist.get("total"))
    positive_docs = [doc for doc in docs if any(token in doc["lower"] for token in _POSITIVE_CUES)]
    category_counts = Counter()
    for category in complaints.get("categories") or []:
        count = int0(category.get("count"))
        if count:
            category_counts[text(category.get("label"), 60)] += count

    response["facts"] = []
    if text_source_available:
        response["facts"].extend(
            [
                _fact(
                    "market.voice_sample",
                    "本周内部声音样本",
                    "Internal voice samples this week",
                    len(docs),
                    request=request,
                    basis=(
                        "严格七天窗口内，统计明确命中 Viltrox 品牌词且带内容发生时间的评论",
                        "weekly comments with explicit Viltrox brand evidence and content-event timestamps",
                    ),
                ),
                _fact(
                    "market.positive_rule_signals",
                    "规则识别正面信号",
                    "Rule-detected positive signals",
                    len(positive_docs),
                    request=request,
                    basis=(
                        "对周内内部评论样本执行中英文正面线索词表匹配",
                        "bilingual positive cue lexicon over weekly internal comment samples",
                    ),
                    confidence="low",
                ),
                _fact(
                    "market.complaint_rule_signals",
                    "规则识别抱怨信号",
                    "Rule-detected complaint signals",
                    negative_count,
                    request=request,
                    basis=(
                        "话题词与负面线索双命中（ask_find_weekly_lexicon_v1）",
                        "topic term + negative cue double-match (ask_find_weekly_lexicon_v1)",
                    ),
                    confidence="medium" if negative_count >= 3 else "low",
                ),
                _fact(
                    "market.wishlist_rule_signals",
                    "明确愿望信号",
                    "Explicit wishlist signals",
                    wishlist_count,
                    request=request,
                    basis=(
                        "愿望线索词命中（ask_find_weekly_lexicon_v1）",
                        "wishlist cue match (ask_find_weekly_lexicon_v1)",
                    ),
                    confidence="medium" if wishlist_count >= 3 else "low",
                ),
            ]
        )
    if video_source_available:
        response["facts"].append(
            _fact(
                "market.viltrox_video_mentions",
                "标题明确提及 Viltrox 的视频",
                "Videos explicitly mentioning Viltrox in title",
                video_mentions,
                request=request,
                basis=(
                    "严格七天窗口内按内容发布时间统计标题明确含 Viltrox 品牌词的有效视频证据",
                    "COUNT(active video titles containing Viltrox by content publication time in the exact seven-day window)",
                ),
            )
        )

    evidence: list[dict[str, Any]] = []
    for index, doc in enumerate(sorted(positive_docs, key=lambda item: (item.get("likes", 0), item.get("at", "")), reverse=True)[:3]):
        evidence.append(
            {
                "id": f"voice-positive-{index + 1}",
                "kind": "positive_rule_signal",
                "source": doc.get("source") or "",
                "title": "Positive cue" if _is_en(request) else "正面线索",
                "snippet": text(doc.get("text"), 180),
                "observed_at": doc.get("at") or None,
                "confidence": "low",
            }
        )
    negative_quotes: list[dict[str, Any]] = []
    for category in complaints.get("categories") or []:
        for quote in category.get("quotes") or []:
            label = text(category.get("label"), 80)
            negative_quotes.append(
                {
                    "category": _COMPLAINT_LABEL_EN.get(label, label) if _is_en(request) else label,
                    **quote,
                }
            )
    for index, quote in enumerate(negative_quotes[:3]):
        evidence.append(
            {
                "id": f"voice-complaint-{index + 1}",
                "kind": "complaint_rule_signal",
                "source": text(quote.get("source"), 60),
                "title": text(quote.get("category"), 80),
                "snippet": text(quote.get("text"), 180),
                "observed_at": quote.get("at") or None,
                "confidence": "medium",
            }
        )
    for item in video_rows:
        evidence.append(
            {
                "id": f"weekly-video-{int0(item.get('id'))}",
                "kind": "viltrox_video_mention",
                "source": "vkpi_kol_video_evidence",
                "title": text(item.get("title"), 180),
                "url": text(item.get("content_url"), 500) or None,
                "entity_id": int0(item.get("kol_pool_id")) or None,
                "observed_at": str(item.get("observed_at") or "") or None,
                "confidence": "high",
            }
        )
    represented_snippets = {str(item.get("snippet") or "") for item in evidence}
    for doc in docs:
        snippet = text(doc.get("text"), 180)
        if not snippet or snippet in represented_snippets:
            continue
        evidence.append(
            {
                "id": f"voice-sample-{doc.get('source')}-{int0(doc.get('id'))}",
                "kind": "viltrox_voice_sample",
                "source": doc.get("source") or "",
                "title": _localized(request, "Viltrox 相关声音（情绪未分类）", "Viltrox-related voice (sentiment unclassified)"),
                "snippet": snippet,
                "observed_at": doc.get("at") or None,
                "confidence": "high",
            }
        )
        represented_snippets.add(snippet)
        if len(evidence) >= 12:
            break
    response["evidence"] = evidence[:12]

    top_complaint = category_counts.most_common(1)
    top_text_raw = top_complaint[0][0] if top_complaint else ""
    top_text = _COMPLAINT_LABEL_EN.get(top_text_raw, top_text_raw) if _is_en(request) else top_text_raw
    if _is_en(request):
        answer_parts: list[str] = []
        if text_source_available:
            answer_parts.append(
                f"The exact seven-day internal comment sample contains {len(docs):,} Viltrox voices; "
                f"rules found {len(positive_docs):,} positive, {negative_count:,} complaint and {wishlist_count:,} wishlist cues"
            )
        if video_source_available:
            answer_parts.append(
                f"{video_mentions:,} published videos explicitly mention Viltrox in the title"
            )
        response["answer"] = "; ".join(answer_parts) + "."
        if top_text:
            response["answer"] += f" The leading complaint topic was {top_text}."
    else:
        answer_parts = []
        if text_source_available:
            answer_parts.append(
                f"严格七天窗口内，明确提及 Viltrox 且带内容发生时间的内部评论样本 {len(docs):,} 条；"
                f"规则识别正面线索 {len(positive_docs):,} 条、抱怨线索 {negative_count:,} 条、愿望线索 {wishlist_count:,} 条"
            )
        if video_source_available:
            answer_parts.append(f"按内容发布时间统计，标题明确提及 Viltrox 的视频 {video_mentions:,} 条")
        response["answer"] = "；".join(answer_parts) + "。"
        if top_text:
            response["answer"] += f"抱怨最高频主题为“{top_text}”。"
    queryable_market_sources = int(text_source_available) + int(video_source_available)
    sample_total = len(docs) + video_mentions
    internal_source_gap = not text_source_available or not video_source_available
    response["coverage"].update(
        {
            "status": "partial" if sample_total or internal_source_gap else "empty",
            "matched_entities": sample_total,
            "evidence_count": len(response["evidence"]),
            "total_scope": sample_total,
            "analyzed_count": 0,
            "ratio": None,
            "notes": [
                "Strict seven-day UTC window; legacy 30-day market_voice output is not reused."
                if _is_en(request)
                else "严格使用七天 UTC 窗口，未复用现有近30天 market_voice 结果。",
                (
                    f"Excluded {excluded_count} weekly voices without explicit Viltrox brand evidence."
                    if _is_en(request)
                    else f"已排除 {excluded_count} 条缺少明确 Viltrox 品牌证据的周内声音。"
                ),
                (
                    "Brand-matched source rows exceeded the bounded retrieval cap; counts are a partial sample."
                    if _is_en(request)
                    else "品牌命中记录超过有界读取上限；当前数量仅代表部分样本。"
                )
                if source_truncated
                else _localized(
                    request,
                    "品牌过滤已在 SQL LIMIT 前执行，未因通用评论截断而漏掉品牌样本。",
                    "Brand filtering ran before SQL LIMIT, avoiding generic-comment truncation loss.",
                ),
                f"Market-event sources queryable: {queryable_market_sources}/2. Reply queue ingestion time is excluded. External market coverage is not connected."
                if _is_en(request)
                else f"带内容发生时间的市场源可查询 {queryable_market_sources}/2；回复队列入队时间不计入周度市场口径；外部市场覆盖尚未接通。",
            ],
        }
    )
    response["missing_fields"] = [
        _missing(
            request,
            "external_market_sources",
            "Reddit、Google Trends、零售商评论和竞品站点数据尚未全部接通",
            "Reddit, Google Trends, retailer reviews and competitor-site feeds are not all connected",
            "本结论仅代表内部样本，不代表全市场",
            "this is an internal-sample summary, not a full-market conclusion",
        ),
        _missing(
            request,
            "sentiment_gold_validation",
            "正面与抱怨数量仍是保守规则信号，尚无通过验证的人工金标集",
            "positive/complaint counts are conservative rule signals without a validated gold-label set",
            "不能把这些数量解释为完整市场情绪",
            "do not interpret these counts as complete market sentiment",
        ),
    ]
    if source_truncated:
        response["missing_fields"].append(
            _missing(
                request,
                "weekly_voice_retrieval_cap",
                "品牌命中记录超过单次有界读取上限",
                "brand-matched rows exceeded the bounded retrieval cap",
                "本次规则信号与去重样本可能不完整",
                "deduplicated samples and rule signals may be incomplete",
            )
        )
    if not text_source_available:
        response["missing_fields"].append(
            _missing(
                request,
                "weekly_comment_event_source",
                "评论源缺失或没有可核验的内容发生时间",
                "comment source is absent or lacks verifiable content-event time",
                "评论样本与规则情绪信号不可用",
                "comment samples and rule-based sentiment signals are unavailable",
            )
        )
    if not video_source_available:
        response["missing_fields"].append(
            _missing(
                request,
                "weekly_video_publication_source",
                "视频源缺失或没有可核验的内容发布时间",
                "video source is absent or lacks verifiable publication time",
                "本周 Viltrox 标题视频数量不可用",
                "the weekly Viltrox-title video count is unavailable",
            )
        )
    response["status"] = "partial" if sample_total or internal_source_gap else "empty"
    newest = _latest_observed_at(video_newest, docs_newest)
    _freshness(response, request, now=now, updated_at=newest, windowed=True)
    response["actions"] = [
        {
            "type": "navigate",
            "label": "Open Market Brain" if _is_en(request) else "打开 Market Brain",
            "route": "marketVoice",
            "params": {"window": "7d"},
            "requires_approval": False,
        }
    ]
    response["trace"]["source_status"] = sources
    return response

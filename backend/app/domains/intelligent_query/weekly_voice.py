"""Strict seven-day Viltrox market-voice deterministic query."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from app.domains.intelligent_query.common import is_en as _is_en
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
from app.domains.intelligent_query.weekly_voice_response import (
    annotate_source_scopes,
    available_response,
    unavailable_response,
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

# The weekly signal path intentionally stays dependency-free.  These bounded
# windows suppress a positive cue only when a nearby negator applies to that
# cue; another affirmed cue in the same comment can still make the comment a
# genuine mixed-sentiment positive signal.  This remains a conservative rule,
# not a replacement for a gold-label sentiment model.
_ENGLISH_NEGATION_WINDOW_TOKENS = 5
_ENGLISH_NEGATORS = frozenset(
    {
        "ain't",
        "aren't",
        "barely",
        "can't",
        "cannot",
        "couldn't",
        "didn't",
        "doesn't",
        "don't",
        "hardly",
        "isn't",
        "never",
        "no",
        "not",
        "wasn't",
        "weren't",
        "without",
        "won't",
        "wouldn't",
    }
)
_ENGLISH_TOKEN_RE = re.compile(r"[a-z]+(?:'[a-z]+)?")
_ENGLISH_SCOPE_BOUNDARY_RE = re.compile(
    r"[.!?;:\n]|\b(?:but|however|nevertheless|though|yet)\b"
)
_ENGLISH_NEGATED_NEGATIVE_COMPLEMENTS = frozenset(
    {
        "bad",
        "complaint",
        "complaints",
        "doubt",
        "issue",
        "issues",
        "poor",
        "problem",
        "problems",
        "terrible",
    }
)
_CHINESE_SCOPE_BOUNDARY_RE = re.compile(r"[。！？；：，,\n]|(?:但是|不过|然而|可是|但)")
_CHINESE_NEGATORS = (
    "并不是",
    "并没有",
    "并不",
    "尚未",
    "从未",
    "未能",
    "不是",
    "没有",
    "不怎么",
    "不太",
    "不能",
    "不会",
    "无法",
    "不要",
    "别再",
    "别去",
    "毫无",
    "并无",
    "没",
    "不",
)
_CHINESE_POST_NEGATION_RE = re.compile(
    r"^(?:度|感|表现)?(?:并)?(?:不够|不高|不好|不行|不明显|不稳定|不起来|不了)"
)
_POSITIVE_CUE_PATTERNS = tuple(
    (
        cue,
        re.compile(rf"(?<![a-z0-9]){re.escape(cue)}")
        if cue.isascii()
        else re.compile(re.escape(cue)),
    )
    for cue in _POSITIVE_CUES
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

def _after_last_scope_boundary(value: str, pattern: re.Pattern[str]) -> str:
    matches = list(pattern.finditer(value))
    return value[matches[-1].end() :] if matches else value


def _english_negator_applies(value: str, cue_start: int, cue_end: int, cue: str) -> bool:
    prefix = _after_last_scope_boundary(value[max(0, cue_start - 120) : cue_start], _ENGLISH_SCOPE_BOUNDARY_RE)
    tokens = _ENGLISH_TOKEN_RE.findall(prefix)
    window = tokens[-_ENGLISH_NEGATION_WINDOW_TOKENS:]
    negated_positions = [
        index for index, token in enumerate(window) if token in _ENGLISH_NEGATORS
    ]
    if not negated_positions:
        return False

    # "not only sharp" is additive, while a second negator in the same window
    # still wins (for example, "not only not sharp").
    meaningful_negators = [
        index
        for index in negated_positions
        if not (
            window[index] in {"no", "not", "without"}
            and index + 1 < len(window)
            and window[index + 1]
            in {
                "just",
                "merely",
                "only",
                *_ENGLISH_NEGATED_NEGATIVE_COMPLEMENTS,
            }
        )
    ]
    if not meaningful_negators:
        return False

    if cue == "recommend":
        suffix_tokens = _ENGLISH_TOKEN_RE.findall(value[cue_end : cue_end + 50])[:5]
        # Common positive idioms contain a grammatical negator but express a
        # strong recommendation: "can't recommend ... highly enough/more".
        if (
            any(window[index] in {"can't", "cannot", "couldn't"} for index in meaningful_negators)
            and any(token in {"enough", "more"} for token in suffix_tokens)
        ):
            return False
        if re.search(r"\b(?:do|would)\s+not\s+hesitate\s+to\s*$", prefix):
            return False
    return True


def _chinese_negator_applies(value: str, cue_start: int, cue_end: int) -> bool:
    prefix = _after_last_scope_boundary(value[max(0, cue_start - 32) : cue_start], _CHINESE_SCOPE_BOUNDARY_RE)
    compact_prefix = re.sub(r"\s+", "", prefix)
    window = compact_prefix[-10:]
    # Additive and recommendation idioms are affirmative despite containing
    # the character 不: "不但很锐利" and "不得不推荐".
    if re.search(
        r"(?:不但|不仅|不只|不光)(?:是|也|还|很|非常|十分|特别|相当|更|强烈|真心)*$",
        window,
    ):
        return False
    if re.search(r"不得不(?:很|非常|十分|特别|强烈|真心)*$", window):
        return False
    if re.search(r"(?:不愧(?:是|为|有)?|不失为)(?:很|非常|十分|特别|相当)*$", window):
        return False
    if any(marker in window for marker in _CHINESE_NEGATORS):
        return True

    compact_suffix = re.sub(r"\s+", "", value[cue_end : cue_end + 12])
    return bool(_CHINESE_POST_NEGATION_RE.match(compact_suffix))


def _has_affirmed_positive_cue(value: str) -> bool:
    """Return true when at least one positive cue is not locally negated."""
    lower = value.lower()
    # Avoid matching English cues inside an unrelated word (for example,
    # ``love`` inside ``glove``), while retaining useful suffixes such as
    # ``recommended`` and ``sharpness``.
    for cue, pattern in _POSITIVE_CUE_PATTERNS:
        for match in pattern.finditer(lower):
            negated = (
                _english_negator_applies(lower, match.start(), match.end(), cue)
                if cue.isascii()
                else _chinese_negator_applies(lower, match.start(), match.end())
            )
            if not negated:
                return True
    return False


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
    annotate_source_scopes(request, sources)
    video_mentions, video_rows, video_newest, video_source = _viltrox_video_mentions(
        conn, request, brand_terms
    )
    sources["videos"] = video_source
    text_source_available = sources["comments"].get("status") in {"ready", "empty"}
    video_source_available = video_source.get("status") in {"ready", "empty"}
    if not text_source_available and not video_source_available:
        return unavailable_response(response, request, sources, now=now)

    complaints, wishlist = _weekly_rule_signals(docs)
    return available_response(
        response,
        request,
        docs=docs,
        sources=sources,
        docs_newest=docs_newest,
        excluded_count=excluded_count,
        source_truncated=source_truncated,
        video_mentions=video_mentions,
        video_rows=video_rows,
        video_newest=video_newest,
        complaints=complaints,
        wishlist=wishlist,
        text_source_available=text_source_available,
        video_source_available=video_source_available,
        positive_cue=_has_affirmed_positive_cue,
        now=now,
    )

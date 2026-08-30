"""Response projection helpers for the strict seven-day weekly voice query."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.domains.intelligent_query.common import (
    fact as _fact,
    freshness as _freshness,
    is_en as _is_en,
    latest_observed_at as _latest_observed_at,
    localized as _localized,
    missing as _missing,
)
from app.domains.intelligent_query.contracts import NormalizedRequest
from app.domains.intelligent_query.repository import int0, text


_COMPLAINT_LABEL_EN = {
    "对焦": "Autofocus",
    "画质": "Image quality",
    "做工/品控": "Build quality",
    "价格": "Price",
    "兼容性/固件": "Compatibility / firmware",
    "体积/重量": "Size / weight",
}


def annotate_source_scopes(
    request: NormalizedRequest,
    sources: dict[str, Any],
) -> None:
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


def unavailable_response(
    response: dict[str, Any],
    request: NormalizedRequest,
    sources: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
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


def _signal_summary(
    docs: list[dict[str, Any]],
    complaints: dict[str, Any],
    wishlist: dict[str, Any],
    positive_cue: Callable[[str], bool],
) -> tuple[list[dict[str, Any]], int, int, Counter[str]]:
    negative_count = int0(complaints.get("total_matched"))
    wishlist_count = int0(wishlist.get("total"))
    positive_docs = [doc for doc in docs if positive_cue(doc["lower"])]
    category_counts: Counter[str] = Counter()
    for category in complaints.get("categories") or []:
        count = int0(category.get("count"))
        if count:
            category_counts[text(category.get("label"), 60)] += count
    return positive_docs, negative_count, wishlist_count, category_counts


def _populate_facts(
    response: dict[str, Any],
    request: NormalizedRequest,
    *,
    docs: list[dict[str, Any]],
    positive_docs: list[dict[str, Any]],
    negative_count: int,
    wishlist_count: int,
    video_mentions: int,
    text_source_available: bool,
    video_source_available: bool,
) -> None:
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
                        "对周内内部评论执行中英文正面词匹配，并用局部否定窗口排除被否定词",
                        "bilingual positive cues with local negation windows over weekly internal comments",
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


def _positive_evidence(
    request: NormalizedRequest,
    positive_docs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    ordered = sorted(
        positive_docs,
        key=lambda item: (item.get("likes", 0), item.get("at", "")),
        reverse=True,
    )[:3]
    for index, doc in enumerate(ordered):
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
    return evidence


def _negative_evidence(
    request: NormalizedRequest,
    complaints: dict[str, Any],
) -> list[dict[str, Any]]:
    quotes: list[dict[str, Any]] = []
    for category in complaints.get("categories") or []:
        for quote in category.get("quotes") or []:
            label = text(category.get("label"), 80)
            quotes.append(
                {
                    "category": _COMPLAINT_LABEL_EN.get(label, label) if _is_en(request) else label,
                    **quote,
                }
            )
    return [
        {
            "id": f"voice-complaint-{index + 1}",
            "kind": "complaint_rule_signal",
            "source": text(quote.get("source"), 60),
            "title": text(quote.get("category"), 80),
            "snippet": text(quote.get("text"), 180),
            "observed_at": quote.get("at") or None,
            "confidence": "medium",
        }
        for index, quote in enumerate(quotes[:3])
    ]


def _video_evidence(video_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
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
        for item in video_rows
    ]


def _append_sample_evidence(
    request: NormalizedRequest,
    evidence: list[dict[str, Any]],
    docs: list[dict[str, Any]],
) -> None:
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
                "title": _localized(
                    request,
                    "Viltrox 相关声音（情绪未分类）",
                    "Viltrox-related voice (sentiment unclassified)",
                ),
                "snippet": snippet,
                "observed_at": doc.get("at") or None,
                "confidence": "high",
            }
        )
        represented_snippets.add(snippet)
        if len(evidence) >= 12:
            break


def _build_evidence(
    request: NormalizedRequest,
    docs: list[dict[str, Any]],
    positive_docs: list[dict[str, Any]],
    complaints: dict[str, Any],
    video_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence = _positive_evidence(request, positive_docs)
    evidence.extend(_negative_evidence(request, complaints))
    evidence.extend(_video_evidence(video_rows))
    _append_sample_evidence(request, evidence, docs)
    return evidence[:12]


def _set_answer(
    response: dict[str, Any],
    request: NormalizedRequest,
    *,
    docs: list[dict[str, Any]],
    positive_docs: list[dict[str, Any]],
    negative_count: int,
    wishlist_count: int,
    video_mentions: int,
    category_counts: Counter[str],
    text_source_available: bool,
    video_source_available: bool,
) -> None:
    top_complaint = category_counts.most_common(1)
    top_text_raw = top_complaint[0][0] if top_complaint else ""
    top_text = (
        _COMPLAINT_LABEL_EN.get(top_text_raw, top_text_raw)
        if _is_en(request)
        else top_text_raw
    )
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
        return

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


def _update_coverage(
    response: dict[str, Any],
    request: NormalizedRequest,
    *,
    docs: list[dict[str, Any]],
    video_mentions: int,
    excluded_count: int,
    source_truncated: bool,
    text_source_available: bool,
    video_source_available: bool,
) -> tuple[int, bool]:
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
    return sample_total, internal_source_gap


def _set_missing_fields(
    response: dict[str, Any],
    request: NormalizedRequest,
    *,
    source_truncated: bool,
    text_source_available: bool,
    video_source_available: bool,
) -> None:
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


def available_response(
    response: dict[str, Any],
    request: NormalizedRequest,
    *,
    docs: list[dict[str, Any]],
    sources: dict[str, Any],
    docs_newest: Any,
    excluded_count: int,
    source_truncated: bool,
    video_mentions: int,
    video_rows: list[dict[str, Any]],
    video_newest: Any,
    complaints: dict[str, Any],
    wishlist: dict[str, Any],
    text_source_available: bool,
    video_source_available: bool,
    positive_cue: Callable[[str], bool],
    now: datetime,
) -> dict[str, Any]:
    positive_docs, negative_count, wishlist_count, category_counts = _signal_summary(
        docs,
        complaints,
        wishlist,
        positive_cue,
    )
    _populate_facts(
        response,
        request,
        docs=docs,
        positive_docs=positive_docs,
        negative_count=negative_count,
        wishlist_count=wishlist_count,
        video_mentions=video_mentions,
        text_source_available=text_source_available,
        video_source_available=video_source_available,
    )
    response["evidence"] = _build_evidence(
        request,
        docs,
        positive_docs,
        complaints,
        video_rows,
    )
    _set_answer(
        response,
        request,
        docs=docs,
        positive_docs=positive_docs,
        negative_count=negative_count,
        wishlist_count=wishlist_count,
        video_mentions=video_mentions,
        category_counts=category_counts,
        text_source_available=text_source_available,
        video_source_available=video_source_available,
    )
    sample_total, internal_source_gap = _update_coverage(
        response,
        request,
        docs=docs,
        video_mentions=video_mentions,
        excluded_count=excluded_count,
        source_truncated=source_truncated,
        text_source_available=text_source_available,
        video_source_available=video_source_available,
    )
    _set_missing_fields(
        response,
        request,
        source_truncated=source_truncated,
        text_source_available=text_source_available,
        video_source_available=video_source_available,
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


__all__ = ["annotate_source_scopes", "available_response", "unavailable_response"]

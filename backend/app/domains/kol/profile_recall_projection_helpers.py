"""profile_recall_projection 的纯函数帮工(CC 战役 2026-08-30 提取,行为逐字节不变)。

只做投影/展示组装的碎步:分数取整、证据摘要碎件、_format_item 的字段组装、why-fit
匹配碎步、过滤器归一碎步、LLM 重排的 prompt/解析碎步。红线原样继承:纯展示,
绝不写/影响 viltrox_fit_score / recall 分 / rule_v0;本模块不 import
profile_recall_projection(防环)。
"""
from __future__ import annotations

import re
from typing import Any

from app.domains.kol.profile_recall_contract import _clean_text
from app.domains.kol.profile_recall_filter_modes import (
    TRI_STATE_FILTER_FIELDS,
    normalize_tri_state_filter,
)
from app.domains.kol.profile_recall_product_queries import PRODUCT_LINE_PERSONAS
from app.domains.kol.profile_recall_relevance import WHY_FIT_RULES


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def round_opt(value: Any, digits: int) -> float | None:
    return round(float(value), digits) if value is not None else None


def str_field(row: dict[str, Any], key: str) -> Any:
    return row.get(key) or ""


# ── 采纳画像 ────────────────────────────────────────────────────────────────


def adoption_counters(rows: list) -> tuple[int, dict[str, int], dict[str, int]]:
    platforms: dict[str, int] = {}
    topic_words: dict[str, int] = {}
    n = 0
    for r in rows:
        d = dict(r)
        n += 1
        p = str(d.get("platform") or "").lower()
        if p:
            platforms[p] = platforms.get(p, 0) + 1
        blob = f"{d.get('primary_topic') or ''} {d.get('bio') or ''}".lower()
        for w in re.findall(r"[a-z]{4,}", blob)[:20]:
            topic_words[w] = topic_words.get(w, 0) + 1
    return n, platforms, topic_words


# ── LLM 重排碎步 ─────────────────────────────────────────────────────────────


def rerank_candidates(buckets: dict[str, list]) -> list[dict[str, Any]]:
    cands: list[dict[str, Any]] = []
    for bucket in ("creator", "reviewer"):
        cands.extend((buckets.get(bucket) or [])[:12])
    return cands


def rerank_prompt(cands: list[dict[str, Any]], query_text: str, persona_text: str, product_label: str) -> str:
    lines = []
    for i, it in enumerate(cands):
        blurb = " ".join(
            str(x) for x in (it.get("why_fit") or "", it.get("recall_reason") or "", it.get("bio") or "")
        ).replace("\n", " ")[:200]
        lines.append(f"{i + 1}. handle={str(it.get('handle'))[:30]} :: {blurb}")
    return (
        "Task: rerank creator candidates for a marketing search.\nQuery: " + str(query_text)[:200]
        + ("\nTarget persona: " + str(persona_text)[:200] if persona_text else "")
        + ("\nProduct: " + str(product_label)[:80] if product_label else "")
        + "\nFor EACH numbered candidate output one object {\"i\": number, \"s\": relevance 0-100}."
        + " Output STRICTLY one JSON array, no prose, reply starts with [\n\n"
        + "\n".join(lines)
    )


def sanitized_rerank_args(safe_context: dict[str, Any]) -> tuple[list, str, str, str]:
    return (
        list(safe_context.get("candidates") or []),
        str(safe_context.get("query_text") or ""),
        str(safe_context.get("persona_text") or ""),
        str(safe_context.get("product_label") or ""),
    )


def rerank_response_unusable(resp: dict[str, Any]) -> bool:
    return str(resp.get("model") or "") == "rule_v0" or str(resp.get("status") or "") != "success"


def apply_rerank_scores(cands: list[dict[str, Any]], arr: Any) -> int:
    hits = 0
    for obj in arr if isinstance(arr, list) else []:
        try:
            idx = int(obj.get("i")) - 1
            score = max(0.0, min(100.0, float(obj.get("s"))))
        except (TypeError, ValueError, AttributeError):
            continue
        if 0 <= idx < len(cands):
            cands[idx]["display_rank_score"] = round(
                _float(cands[idx].get("display_rank_score")) + 0.15 * (score / 100.0), 6
            )
            cands[idx]["llm_rerank_score"] = score
            hits += 1
    return hits


# ── 证据摘要碎件 ─────────────────────────────────────────────────────────────


def summary_counters(items: list[dict[str, Any]]) -> tuple[int, int]:
    with_view_count = sum(1 for item in items if item.get("view_count") not in (None, ""))
    deep_analysis_count = sum(
        1
        for item in items
        if any(
            _clean_text(item.get(key), 20)
            for key in ("content_summary", "product_presence", "brand_exposure")
        )
    )
    return with_view_count, deep_analysis_count


def representative_and_targets(
    ranked: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    representative: list[dict[str, Any]] = []
    content_targets: list[dict[str, Any]] = []
    for item in ranked:
        title = _clean_text(item.get("title"), 220)
        url = _clean_text(item.get("content_url"), 500)
        if not title and not url:
            continue
        representative.append(
            {
                "title": title or url,
                "content_url": url,
                "thumbnail_url": _clean_text(item.get("thumbnail_url"), 500),
                "view_count": item.get("view_count"),
                "like_count": item.get("like_count"),
                "comment_count": item.get("comment_count"),
                "share_count": item.get("share_count"),
                "data_truth": item.get("data_truth"),
            }
        )
        content_targets.append(
            {
                "evidence_id": item.get("evidence_id"),
                "content_url": url,
            }
        )
        if len(representative) >= 3:
            break
    return representative, content_targets


def evidence_titles_of(ranked: list[dict[str, Any]]) -> list[str]:
    """垂类多路取证要读的标题窗口(展示用 representative 仍只留 3 条,互不干扰)。"""
    evidence_titles: list[str] = []
    for item in ranked[:20]:
        title = _clean_text(item.get("title"), 220)
        if title and title not in evidence_titles:
            evidence_titles.append(title)
    return evidence_titles


def summary_texts(ranked: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for item in ranked[:6]:
        texts.extend(
            [
                _clean_text(item.get("title"), 500),
                _clean_text(item.get("product_presence"), 500),
                _clean_text(item.get("brand_exposure"), 500),
            ]
        )
    return texts


# ── persona / why-fit 碎步 ──────────────────────────────────────────────────


def _persona_meta_parts(query_meta: dict[str, Any]) -> list[str]:
    profile_key = str((query_meta or {}).get("query_profile") or "")
    persona_meta = PRODUCT_LINE_PERSONAS.get(profile_key) or {}
    if not persona_meta:
        return []
    return [str(persona_meta.get("persona") or ""), str(persona_meta.get("label") or "")]


def _product_focus_parts(product_focus: Any) -> list[str]:
    if isinstance(product_focus, (list, tuple)):
        return [str(item) for item in product_focus if item]
    if product_focus:
        return [str(product_focus)]
    return []


def persona_parts(query_meta: dict[str, Any], product_focus: Any, target_persona: Any) -> list[str]:
    parts = _persona_meta_parts(query_meta) + _product_focus_parts(product_focus)
    if target_persona:
        parts.append(str(target_persona))
    if (query_meta or {}).get("query_text_provided"):
        parts.append(str((query_meta or {}).get("query_text") or ""))
    return parts


def why_fit_kol_blob(row: dict[str, Any], evidence: dict[str, Any]) -> str:
    """KOL 真实信号 blob:画像文本 / 类型理由 / 已用器材 / 垂类标签 / bio。"""
    return " ".join(
        _clean_text(value, 600).lower()
        for value in (
            row.get("profile_text"),
            row.get("type_reason"),
            row.get("bio"),
            " ".join(str(lens) for lens in (evidence.get("used_lenses") or [])),
            " ".join(str(label) for label in (evidence.get("reason_labels") or [])),
        )
        if value
    )


def why_fit_matches(persona_blob: str, kol_blob: str) -> list[str]:
    matched: list[str] = []
    seen: set[str] = set()
    for _persona_key, persona_words, kol_words, phrase in WHY_FIT_RULES:
        if phrase in seen:
            continue
        persona_hit = (not persona_blob) or any(word.lower() in persona_blob for word in persona_words)
        kol_hit = any(word.lower() in kol_blob for word in kol_words)
        if persona_hit and kol_hit:
            matched.append(phrase)
            seen.add(phrase)
        if len(matched) >= 2:
            break
    return matched


# ── 过滤器归一碎步 ───────────────────────────────────────────────────────────


def filter_values(value: Any) -> list[str]:
    raw = value if isinstance(value, (list, tuple, set)) else [value]
    values: list[str] = []
    for item in raw:
        text = " ".join(str(item or "").split()).strip()
        if text and text.lower() not in {"all", "*", "any"} and text not in values:
            values.append(text)
    return values


def tri_state_normalized(value: dict[str, Any], normalized: dict[str, Any], unsupported: list[str]) -> None:
    for key in ("platforms", "countries", "languages", "verticals"):
        source, mode, invalid_mode = normalize_tri_state_filter(value.get(key))
        values = filter_values(source)
        if values:
            normalized[key] = values
        if values and mode != "require" and key in TRI_STATE_FILTER_FIELDS:
            normalized[f"{key}_mode"] = mode
        elif invalid_mode or mode != "require":
            unsupported.append(f"{key}_mode")


def follower_bounds_normalized(
    value: dict[str, Any], normalized: dict[str, Any], unsupported: list[str]
) -> None:
    for canonical, aliases in (
        ("followers_min", ("followers_min", "follower_min")),
        ("followers_max", ("followers_max", "follower_max")),
    ):
        raw = next((value.get(alias) for alias in aliases if value.get(alias) not in (None, "")), None)
        if raw is None:
            continue
        try:
            parsed = max(0, int(float(raw)))
        except (TypeError, ValueError):
            unsupported.append(canonical)
            continue
        normalized[canonical] = parsed


def gear_content_normalized(value: dict[str, Any], normalized: dict[str, Any], unsupported: list[str]) -> None:
    gear_content = str(value.get("gear_content") or "any").strip().lower()
    if gear_content in {"yes", "true", "1"}:
        normalized["gear_content"] = "yes"
    elif gear_content in {"", "any", "all", "*"}:
        pass
    elif gear_content in {"no", "false", "0"}:
        # Absence of captured gear evidence is not proof that a creator has no
        # gear content.  Ignoring this negative filter is safer than returning
        # false negatives; the response says it was unsupported.
        unsupported.append("gear_content:no_negative_evidence")
    else:
        unsupported.append("gear_content")


# ── 硬筛碎步 ────────────────────────────────────────────────────────────────


def platform_filter_reason(
    row: dict[str, Any], filters: dict[str, Any], reasons: dict[str, str], unknown: list[str]
) -> None:
    platform = str(row.get("platform") or "").strip().lower()
    requested_platforms = {str(item).strip().lower() for item in filters.get("platforms") or []}
    if requested_platforms:
        if not platform:
            unknown.append("platform")
            reasons["platforms"] = "unknown"
        elif platform not in requested_platforms:
            reasons["platforms"] = "mismatch"


def followers_state(row: dict[str, Any]) -> tuple[int, bool]:
    followers_raw = row.get("followers")
    followers_known = followers_raw not in (None, "")
    followers = 0
    if followers_known:
        try:
            followers = max(0, int(float(followers_raw)))
        except (TypeError, ValueError):
            followers_known = False
    return followers, followers_known


def followers_filter_reasons(
    filters: dict[str, Any],
    followers: int,
    followers_known: bool,
    reasons: dict[str, str],
    unknown: list[str],
) -> None:
    if "followers_min" in filters:
        if not followers_known:
            unknown.append("followers")
            reasons["followers_min"] = "unknown"
        elif followers < int(filters["followers_min"]):
            reasons["followers_min"] = "mismatch"
    if "followers_max" in filters:
        if not followers_known:
            unknown.append("followers")
            reasons["followers_max"] = "unknown"
        elif followers > int(filters["followers_max"]):
            reasons["followers_max"] = "mismatch"


def missing_field_flags(
    *,
    country: str,
    language: str,
    followers_known: bool,
    vertical_unknown: bool,
    used_lenses: list,
    gear_signal: bool,
) -> list[str]:
    # These fields are useful UI honesty signals even when no filter targets
    # them.  Missing does not become a numeric zero or a negative claim.
    # 三态放行(include_unknown / exclude)也照旧走这里 —— 放行 != 假装知道。
    return [field for field, missing in (
        ("country", not country), ("language", not language), ("followers", not followers_known),
        ("verticals", vertical_unknown),
        ("gear_content", not used_lenses and not gear_signal),
    ) if missing]


def gear_filter_reason(
    filters: dict[str, Any],
    used_lenses: list,
    vertical_blob: str,
    gear_signal: bool,
    reasons: dict[str, str],
) -> None:
    if filters.get("gear_content") == "yes" and not gear_signal:
        reasons["gear_content"] = "unknown" if not vertical_blob and not used_lenses else "mismatch"


# ── _format_item 组装碎件 ───────────────────────────────────────────────────


def retrieval_score_of(hit: Any) -> float | None:
    return (
        hit.retrieval_score
        if hit.retrieval_score is not None
        else hit.lexical_score
        if hit.lexical_score is not None
        else hit.vector_score
    )


def display_rank_score(rank_score: float | None, relevance_adjust: float) -> float | None:
    return round(rank_score + relevance_adjust, 6) if rank_score is not None else None


def provisional_lane_fields(bucket: str, provisional_lane: str, provisional_lane_source: str) -> dict[str, Any]:
    return {
        "provisional_profile_lane": provisional_lane if bucket == "unknown" else bucket,
        "provisional_profile_lane_source": provisional_lane_source if bucket == "unknown" else "profile_index",
        "profile_type_confidence": "low" if bucket == "unknown" else "indexed",
    }


def evidence_quality_of(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "video_evidence_count": int(evidence.get("video_evidence_count") or 0),
        "with_view_count": int(evidence.get("with_view_count") or 0),
        "deep_analysis_count": int(evidence.get("deep_analysis_count") or 0),
        "view_count_coverage_ratio": evidence.get("view_count_coverage_ratio"),
        "claim_status": "coverage_only_not_accuracy",
        "note": evidence.get("coverage_note") or "证据覆盖计数，不代表分析准确率或合作结果。",
    }


def attach_optional_evidence(
    item: dict[str, Any], evidence: dict[str, Any], content_status: Any
) -> None:
    representative = list(evidence.get("representative_evidence") or [])
    if representative:
        item["representative_evidence"] = representative
    if content_status:
        item["content_evidence_status"] = content_status
    used_lenses = list(evidence.get("used_lenses") or [])
    if used_lenses:
        item["used_lenses"] = used_lenses
        item["used_lenses_note"] = "从作品标题和视频分析中提取的镜头提及,不是确证拥有"


# ── 车道/信号 blob 碎步 ─────────────────────────────────────────────────────


def lane_retrieval_meta(item: dict[str, Any]) -> dict[str, Any]:
    return (
        (item.get("source_fields") or {}).get("retrieval_meta") or {}
        if isinstance(item.get("source_fields"), dict)
        else {}
    )


def lane_blob(item: dict[str, Any]) -> str:
    representative = item.get("representative_evidence") or []
    return " ".join(
        str(value or "").lower()
        for value in (
            item.get("primary_topic"),
            item.get("bio"),
            " ".join(str(value) for value in item.get("used_lenses") or []),
            " ".join(
                str(row.get("title") or "") for row in representative if isinstance(row, dict)
            ),
        )
    )


def joined_titles(representative: list) -> str:
    return " ".join(str(item.get("title") or "") for item in representative if isinstance(item, dict))


def joined_strs(values: Any) -> str:
    return " ".join(str(value) for value in values)

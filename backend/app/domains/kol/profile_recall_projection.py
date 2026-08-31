"""Candidate evidence, filtering, bucketing, and presentation for KOL recall."""
from __future__ import annotations

import re
from typing import Any, Callable

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol.metric_truth import project_evidence_item_truth, project_pool_item_truth
from app.domains.kol.profile_recall_contract import (
    LENS_MENTION_RE,
    METHOD,
    PROFILE_REASON_KEYWORDS,
    RecallHit,
    SEARCH_STRATEGY_BUCKET_POLICIES,
    SUPPORTED_RECALL_FILTERS,
    _clean_text,
)
from app.domains.kol.profile_recall_filter_modes import (
    CandidateFilterVerdict, tri_state_outcome, unknown_field_candidates,
)
from app.domains.kol.profile_recall_country_gate import country_match_key
from app.domains.kol.profile_recall_content_projection import public_content_evidence_status
from app.domains.kol.profile_recall_language_gate import resolve_language_match_key
from app.domains.kol.profile_recall_precision import missingness_aware_weighted_score
from app.domains.kol.profile_recall_relevance import _relevance_signals
from app.domains.kol.profile_recall_projection_helpers import (
    _float,
    _optional_float,
)
from app.domains.kol import profile_recall_projection_helpers as fmt
from app.domains.kol.profile_vertical_signals import VerticalReading, vertical_filter_outcome


logger = get_logger(__name__)


def _adoption_profile(
    *,
    get_connection: Callable[[], Any] = get_conn,
) -> dict:
    """采纳回流(2026-07-02 用户令):收藏/入项目 KOL 的 platform 与主题词分布。
    行数极小(收藏12+分配5级别),每次召回一小查;采纳样本 <5 不学,防两三条记录带偏。"""
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT p.platform, p.primary_topic, p.bio
            FROM vkpi_kol_pool p
            WHERE p.id IN (SELECT kol_pool_id FROM vkpi_kol_pool_favorites)
               OR p.id IN (SELECT kol_pool_id FROM vkpi_project_kol_assignments)
            """
        ).fetchall()
    except Exception:
        logger.debug("偏好画像读取失败,返回空画像(best-effort)", exc_info=True)
        return {}
    n, platforms, topic_words = fmt.adoption_counters(rows)
    if n < 5:
        return {}
    top_words = {w for w, c in sorted(topic_words.items(), key=lambda kv: -kv[1])[:15] if c >= 2}
    return {"n": n, "platforms": platforms, "top_words": top_words}


def _adoption_boost_for(item: dict[str, Any], profile: dict) -> float:
    """展示层小幅上浮:平台份额 0.02 + 采纳主题词命中候选 bio/why_fit 0.03,封顶 0.05。"""
    n = int(profile.get("n") or 0)
    if not n:
        return 0.0
    boost = 0.0
    p = str(item.get("platform") or "").lower()
    if p and profile.get("platforms", {}).get(p):
        boost += 0.02 * (profile["platforms"][p] / n)
    blob = f"{item.get('bio') or ''} {item.get('why_fit') or ''}".lower()
    if any(w in blob for w in profile.get("top_words", set())):
        boost += 0.03
    return round(min(0.05, boost), 4)


def _llm_rerank_buckets(buckets: dict[str, list], query_text: str, persona_text: str, product_label: str) -> str:
    """头部候选 LLM 相关性重排(两桶各 top12 合一次 flash 调用,0.15 权重折进 display 分)。
    任何失败静默跳过(返回诊断短语);红线:绝不触 vector/recall/fit 分。"""
    try:
        from app.core.model_registry import current_task_model_binding, split_binding
        from app.platform.llm_production import generate_text
    except Exception:
        logger.debug("KOL recall production LLM boundary unavailable", exc_info=True)
        return "production_boundary_unavailable"
    cands = fmt.rerank_candidates(buckets)
    if len(cands) < 4:
        return "too_few_candidates"
    from app.domains.kol.contact_system import sanitize_contact_values_for_external_processing

    safe_context = sanitize_contact_values_for_external_processing(
        {
            "candidates": cands,
            "query_text": query_text,
            "persona_text": persona_text,
            "product_label": product_label,
        }
    )
    cands, query_text, persona_text, product_label = fmt.sanitized_rerank_args(safe_context)
    prompt = fmt.rerank_prompt(cands, query_text, persona_text, product_label)
    try:
        provider, model = split_binding(
            current_task_model_binding().get("kol_content_fit_analysis", "")
        )
        resp = generate_text(
            prompt,
            provider=provider,
            model=model,
            purpose="vkpi_recall_rerank_v1",
            max_output_tokens=3000,
            cost_tag="kol_recall",
            metadata={
                "task_binding": "kol_content_fit_analysis",
                "surface": "kol_pool_search",
                "phase": "candidate_rerank",
            },
        )
    except Exception:
        logger.debug("KOL recall production rerank unavailable", exc_info=True)
        return "llm_unavailable"
    if fmt.rerank_response_unusable(resp):
        return "llm_unusable"
    text = str(resp.get("text") or "")
    # 截断救援:thinking 模型思考 token 吃掉输出预算是常态,复用 audience_stats 的
    # 残缺数组抢救解析(抢救到多少算多少,绝不编造)。
    try:
        from app.domains.kol.audience_stats import _extract_json_array

        arr = _extract_json_array(text)
    except Exception:
        arr = []
    if not arr:
        return "parse_failed"
    return f"ok:{fmt.apply_rerank_scores(cands, arr)}"


_EXCLUDED_REGION_RE = re.compile(
    r"中国|中國|大陆|大陸|香港|台湾|台灣|hong\s*kong|taiwan|china", re.IGNORECASE
)
_EXCLUDED_REGION_CODES = {"CN", "HK", "TW", "CHINA"}


def _country_in_excluded_region(value: Any) -> bool:
    """地区排除判据(P0-6 取代旧大陆/汉字双判据):country 命中 {CN/HK/TW} 之一即排除,
    同时匹配中文地名(中国/中國/大陆/大陸/香港/台湾/台灣/Hong Kong/Taiwan/China)与 ISO 码
    (CN/HK/TW/CHINA)。country 为空 → 不命中 → 放行(海外中文博主一律放行)。"""
    text = str(value or "").strip()
    if not text:
        return False
    if text.upper() in _EXCLUDED_REGION_CODES:
        return True
    return bool(_EXCLUDED_REGION_RE.search(text))


# P0-6 移除:旧汉字判据 `_looks_chinese` 已从过滤链下线(误杀三地区外日韩/海外中文号),
# 排除一律走 `_country_in_excluded_region` 地区判据。函数整体删除,不再保留死代码。


def _normalise_lens_mention(value: str) -> str:
    text = _clean_text(value, 80)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\b[Ff]\s*/?\s*", "F", text)
    text = re.sub(r"\b(viltrox)\b", "Viltrox", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(af)\b", "AF", text, flags=re.IGNORECASE)
    for word in ("pro", "lab", "evo", "air", "dl", "fe", "stm", "vcm", "apo"):
        text = re.sub(rf"\b{word}\b", word.upper() if word in {"lab", "evo", "air", "dl", "fe", "stm", "vcm", "apo"} else "Pro", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d)\s*mm", r"\1mm", text, flags=re.IGNORECASE)
    if "viltrox" not in text.lower():
        text = f"Viltrox {text}"
    return text


def _extract_lenses(*texts: Any, limit: int = 3) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in texts:
        blob = _clean_text(raw, 1200)
        if not blob:
            continue
        for match in LENS_MENTION_RE.finditer(blob):
            start = max(0, match.start() - 50)
            end = min(len(blob), match.end() + 50)
            context = blob[start:end].lower()
            phrase = match.group(0)
            if "viltrox" not in context and not phrase.lower().strip().startswith("af"):
                continue
            normalised = _normalise_lens_mention(phrase)
            key = re.sub(r"[^a-z0-9]+", "", normalised.lower())
            if key in seen:
                continue
            seen.add(key)
            found.append(normalised)
            if len(found) >= limit:
                return found
    return found


def _reason_labels(*texts: Any, limit: int = 2) -> list[str]:
    blob = " ".join(_clean_text(text, 1200).lower() for text in texts if text)
    labels: list[str] = []
    for label, keywords in PROFILE_REASON_KEYWORDS:
        if any(keyword in blob for keyword in keywords):
            labels.append(label)
        if len(labels) >= limit:
            break
    return labels


def _evidence_score(row: dict[str, Any]) -> tuple[int, int, int, int]:
    title = str(row.get("title") or "")
    product_blob = " ".join(str(row.get(key) or "") for key in ("product_presence", "brand_exposure"))
    blob = f"{title} {product_blob}".lower()
    lens_bonus = 1 if LENS_MENTION_RE.search(blob) else 0
    viltrox_bonus = 1 if "viltrox" in blob else 0
    thumbnail_bonus = 1 if _clean_text(row.get("thumbnail_url"), 500) else 0
    try:
        views = int(row.get("view_count") or 0)
    except (TypeError, ValueError):
        views = 0
    return lens_bonus, viltrox_bonus, thumbnail_bonus, views


def _evidence_summaries(
    kol_pool_ids: list[int],
    *,
    get_connection: Callable[[], Any] = get_conn,
) -> dict[int, dict[str, Any]]:
    if not kol_pool_ids:
        return {}
    placeholders = ",".join(["?"] * len(kol_pool_ids))
    rows = get_connection().execute(
        f"""
        SELECT e.kol_pool_id,
               e.id AS evidence_id,
               COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), NULLIF(e.content_url, '')) AS title,
               e.content_url,
               e.thumbnail_url,
               e.view_count,
               e.like_count,
               e.comment_count,
               e.share_count,
               e.source,
               e.metrics_source,
               e.metrics_scraped_at,
               e.scrape_source,
               e.scrape_status,
               c.result #>> '{{layer1_visual_content,content_summary}}' AS content_summary,
               c.result #>> '{{layer1_visual_content,product_presence}}' AS product_presence,
               c.result #>> '{{layer1_visual_content,brand_exposure}}' AS brand_exposure
        FROM vkpi_kol_video_evidence e
        LEFT JOIN vkpi_analysis_cache c
          ON c.target_type = 'video'
         AND c.target_id = e.id::text
         AND c.derive_method = 'video_analysis_final_v1'
         AND c.status = 'ready'
        WHERE e.kol_pool_id IN ({placeholders})
          AND e.is_active IS NOT FALSE
        ORDER BY e.kol_pool_id, e.posted_at DESC NULLS LAST, e.id DESC
        """,
        tuple(kol_pool_ids),
    ).fetchall()
    by_id: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        item = project_evidence_item_truth(dict(row))
        by_id.setdefault(int(item["kol_pool_id"]), []).append(item)
    return {kol_id: _evidence_summary(items) for kol_id, items in by_id.items()}


def _evidence_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(items, key=_evidence_score, reverse=True)
    video_evidence_count = len(items)
    with_view_count, deep_analysis_count = fmt.summary_counters(items)
    representative, content_targets = fmt.representative_and_targets(ranked)
    texts = fmt.summary_texts(ranked)
    return {
        "representative_evidence": representative,
        # Private, request-local identity coordinates.  Smart-local may
        # use them to read the exact cached post/final-v1 row; they are
        # never copied into a returned candidate.
        "_targeted_content_targets": content_targets,
        "evidence_titles": fmt.evidence_titles_of(ranked)[:12],
        "used_lenses": _extract_lenses(*texts),
        "reason_labels": _reason_labels(
            *(texts + [_clean_text(item.get("content_summary"), 500) for item in ranked[:3]])
        ),
        "video_evidence_count": video_evidence_count,
        "with_view_count": with_view_count,
        "deep_analysis_count": deep_analysis_count,
        "view_count_coverage_ratio": round(with_view_count / video_evidence_count, 4)
        if video_evidence_count
        else None,
        "coverage_note": "证据覆盖计数，不代表分析准确率或合作结果。",
    }


def _persona_text_for_query(query_meta: dict[str, Any], product_focus: Any, target_persona: Any) -> str:
    """拼出"本次 query 面向的人群"文本(供 why-fit 人群侧关键词匹配)。
    优先级:planner 的 product_focus/target_persona + 产品线 persona/label + 原始 query_text。
    纯展示用文本,绝不参与评分。"""
    parts = fmt.persona_parts(query_meta, product_focus, target_persona)
    return _clean_text(" ".join(p for p in parts if p), 600).lower()


def _why_fit(
    row: dict[str, Any],
    evidence: dict[str, Any],
    persona_text: str,
    product_label: str,
) -> str:
    """一句话「为何这个人适合本次产品/人群」:把 ① 本次 query 人群 与 ② KOL 真实信号做规则匹配。
    命中即拼可读理由(无 LLM 也有理由);纯展示文本,绝不写/影响 viltrox_fit_score。"""
    matched = fmt.why_fit_matches(str(persona_text or ""), fmt.why_fit_kol_blob(row, evidence))
    target = str(product_label or "").strip()
    if matched:
        signal = " + ".join(matched)
        return f"{signal} → 适合{target}" if target else f"适合理由:{signal}"
    # 无规则命中时的可读兜底:用画像标签说人话,不假装精准匹配。
    fallback_labels = list(evidence.get("reason_labels") or []) or _reason_labels(
        row.get("profile_text"), row.get("type_reason")
    )
    label_text = "/".join(fallback_labels[:2]) if fallback_labels else "内容画像"
    return f"{label_text}画像与{target}人群相近" if target else f"{label_text}画像与产品人群相近"


def _recall_reason(row: dict[str, Any], evidence: dict[str, Any]) -> str:
    lenses = list(evidence.get("used_lenses") or [])
    reason_labels = list(evidence.get("reason_labels") or [])
    if not reason_labels:
        reason_labels = _reason_labels(row.get("profile_text"), row.get("type_reason"))
    profile_part = "/".join(reason_labels[:2]) if reason_labels else "索引画像"
    if lenses:
        return f"作品证据:{lenses[0]}相关内容 + {profile_part}画像匹配"
    representative = list(evidence.get("representative_evidence") or [])
    if representative:
        title = _clean_text(representative[0].get("title"), 42)
        return f"作品证据:{title} + {profile_part}画像匹配"
    return f"画像匹配:{profile_part}内容画像与产品 query 相近"


# 实现平移到 profile_recall_projection_helpers.filter_values(逐字节不变);老名字保留。
_filter_values = fmt.filter_values


def _country_match_key(value: Any) -> str:
    """闸的国家归一化口径。实现已搬进 ``profile_recall_country_gate``(逐字节不变),
    取数腿按同一把尺子反查出可匹配写法的闭包 —— 两侧共用一把尺子,不许再有第二把。"""
    return country_match_key(value)


_LANGUAGE_ALIASES = {
    "english": "en",
    "中文": "zh",
    "chinese": "zh",
    "mandarin": "zh",
    "japanese": "ja",
    "日语": "ja",
    "korean": "ko",
    "韩语": "ko",
    "german": "de",
    "德语": "de",
    "french": "fr",
    "法语": "fr",
    "spanish": "es",
    "西班牙语": "es",
    "italian": "it",
    "意大利语": "it",
    "portuguese": "pt",
    "葡萄牙语": "pt",
}


def _language_match_key(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip().lower().replace("_", "-")
    if not text:
        return ""
    return _LANGUAGE_ALIASES.get(text, text.split("-", 1)[0])


def _normalize_recall_filters(value: Any) -> tuple[dict[str, Any], list[str]]:
    if value in (None, ""):
        return {}, []
    if not isinstance(value, dict):
        raise ValueError("filters must be an object")
    unsupported = sorted(str(key) for key in value if str(key) not in SUPPORTED_RECALL_FILTERS)
    normalized: dict[str, Any] = {}
    fmt.tri_state_normalized(value, normalized, unsupported)
    fmt.follower_bounds_normalized(value, normalized, unsupported)
    fmt.gear_content_normalized(value, normalized, unsupported)
    return normalized, sorted(set(unsupported))


_GEAR_CONTENT_TERMS = (
    "lens", "lenses", "camera", "gear", "镜头", "相机", "器材",
    "fujifilm", "sony", "canon", "nikon", "lumix", "viltrox", "sigma", "tamron",
)

def _factual_candidate_signal_blob(row: dict[str, Any], evidence: dict[str, Any]) -> str:
    """Pool facts + persisted content evidence; never derived profile prose."""

    values = (
        row.get("bio"),
        row.get("primary_topic"),
        row.get("content_style"),
        row.get("secondary_topics_json"),
        fmt.joined_titles(evidence.get("representative_evidence") or []),
        fmt.joined_strs(evidence.get("used_lenses") or []),
        fmt.joined_strs(evidence.get("reason_labels") or []),
    )
    return " ".join(_clean_text(value, 800).lower() for value in values if value)


def _candidate_filter_verdict(
    row: dict[str, Any],
    evidence: dict[str, Any],
    filters: dict[str, Any],
    *,
    vertical_reading: VerticalReading | None = None,
) -> CandidateFilterVerdict:
    """Return (passes hard filters, rejected fields, unknown evidence fields).

    countries / languages 走三态(``countries_mode`` / ``languages_mode``,缺省 ``require`` 与
    历史行为逐字节一致);返回值仍可解包成三元组,只是旁挂了 ``rejected_known_mismatch`` /
    ``rejected_unknown`` / ``unknown_field_candidates``(见 profile_recall_filter_modes)。"""
    reasons: dict[str, str] = {}
    unknown: list[str] = []

    fmt.platform_filter_reason(row, filters, reasons, unknown)

    country = _country_match_key(row.get("country"))
    # 自报优先 -> 推断兜底 -> 都没有才是未知。推断值住在另一列(迁移 305),永不冒充自报值;
    # 这一行之前只读 p.language,于是推断车道的成果在**真正把人剔掉的这道闸**上完全不存在。
    language, _language_origin = resolve_language_match_key(row, match_key=_language_match_key)
    for filter_key, current, match_key in (
        ("countries", country, _country_match_key),
        ("languages", language, _language_match_key),
    ):
        requested = {match_key(item) for item in filters.get(filter_key) or []}
        requested.discard("")
        outcome = tri_state_outcome(current, requested, filters.get(f"{filter_key}_mode"))
        if outcome:
            reasons[filter_key] = outcome

    followers, followers_known = fmt.followers_state(row)
    fmt.followers_filter_reasons(filters, followers, followers_known, reasons, unknown)

    # gear_content 闸继续吃它原来的语料(逐字节不动);垂类改走多路取证,两者刻意分家——
    # 垂类语料变宽绝不允许顺带把器材证据要求放宽(用户红线)。
    vertical_blob = _factual_candidate_signal_blob(row, evidence)
    vertical_reading = _vertical_filter_reasons(row, evidence, filters, vertical_reading, reasons, unknown)

    used_lenses = list(evidence.get("used_lenses") or [])
    gear_signal = bool(used_lenses) or any(term in vertical_blob for term in _GEAR_CONTENT_TERMS)
    fmt.gear_filter_reason(filters, used_lenses, vertical_blob, gear_signal, reasons)

    unknown.extend(fmt.missing_field_flags(
        country=country, language=language, followers_known=followers_known,
        vertical_unknown=vertical_reading.is_unknown,
        used_lenses=used_lenses, gear_signal=gear_signal,
    ))
    return CandidateFilterVerdict(reasons, unknown, unknown_field_candidates(row, reasons))


def _vertical_filter_reasons(
    row: dict[str, Any],
    evidence: dict[str, Any],
    filters: dict[str, Any],
    vertical_reading: VerticalReading | None,
    reasons: dict[str, str],
    unknown: list[str],
) -> VerticalReading:
    vertical_outcome, vertical_reading, _vertical_hits = vertical_filter_outcome(
        row, evidence, filters.get("verticals") or [], reading=vertical_reading,
    )
    if vertical_outcome:
        reasons["verticals"] = vertical_outcome
        if vertical_outcome == "unknown":
            unknown.append("verticals")
    return vertical_reading


def _normalize_bucket_policy(
    value: Any,
    *,
    search_strategy: str,
    result_limit: int,
) -> tuple[dict[str, int], bool]:
    strategy = str(search_strategy or "balanced").strip().lower()
    if strategy not in SEARCH_STRATEGY_BUCKET_POLICIES:
        raise ValueError("search_strategy must be balanced, vertical, or expansion")
    source = value if isinstance(value, dict) else SEARCH_STRATEGY_BUCKET_POLICIES[strategy]
    raw = {
        key: max(0, min(result_limit, int(float(source.get(key) or 0))))
        for key in ("core_vertical", "expansion", "exploration")
    }
    original = dict(raw)
    remaining = result_limit
    normalized: dict[str, int] = {}
    for key in ("core_vertical", "expansion", "exploration"):
        normalized[key] = min(raw[key], remaining)
        remaining -= normalized[key]
    if remaining > 0:
        normalized["exploration"] += remaining
    return normalized, normalized != original or sum(original.values()) != result_limit


def _is_relevance_backfill(item: dict[str, Any]) -> bool:
    return str(item.get("match_tier") or "") == "backfill"


_CORE_VERTICAL_TERMS = (
    "lens", "camera", "gear", "review", "reviewer", "comparison", "tutorial",
    "photography", "photographer", "filmmaker", "filmmaking",
    "flash", "speedlight", "strobe", "monitor", "field monitor",
    "镜头", "相机", "器材", "评测", "测评", "对比", "教程", "摄影", "影视",
    "闪光灯", "监视器", "监看器",
)


def _natural_business_lane(item: dict[str, Any]) -> tuple[str, str]:
    if _is_relevance_backfill(item):
        return "exploration", "仅放宽查询相关性后的显式补位"
    factual_anchors = fmt.lane_retrieval_meta(item).get("factual_anchor_terms") or []
    if item.get("match_tier") == "strict" and factual_anchors:
        return "core_vertical", "事实字段或内容证据满足全部产品锚点"
    blob = fmt.lane_blob(item)
    if item.get("match_tier") == "strict" and any(term in blob for term in _CORE_VERTICAL_TERMS):
        return "core_vertical", "事实池字段或内容证据命中垂类"
    if item.get("match_tier") == "relaxed":
        return "expansion", "仅部分事实检索信号，需人工复核"
    return "expansion", "查询相关但缺少强垂直器材证据"


def _assign_business_buckets(
    items: list[dict[str, Any]],
    bucket_policy: dict[str, int],
) -> dict[str, list[dict[str, Any]]]:
    business_buckets: dict[str, list[dict[str, Any]]] = {
        "core_vertical": [],
        "expansion": [],
        "exploration": [],
    }
    for item in items:
        lane, reason = _natural_business_lane(item)
        item["candidate_bucket"] = lane
        item["candidate_bucket_reason"] = reason
        # Compatibility aliases during the UI migration; candidate_bucket is
        # the canonical contract.
        item["business_lane"] = lane
        item["candidate_lane"] = lane
        business_buckets[lane].append(item)
    for lane, lane_items in business_buckets.items():
        target = int(bucket_policy.get(lane) or 0)
        for item in lane_items:
            item["candidate_bucket_target"] = target
    return business_buckets


def _bucket_for(row: dict[str, Any], mixed_policy: str) -> str:
    profile_type = str(row.get("profile_type") or "").strip().lower()
    if profile_type == "creator":
        return "creator"
    if profile_type == "reviewer":
        return "reviewer"
    if profile_type == "mixed" and mixed_policy == "dominant":
        return "creator" if _float(row.get("creator_type_score")) >= _float(row.get("reviewer_type_score")) else "reviewer"
    return "unknown"


def _type_label(row: dict[str, Any]) -> str:
    profile_type = str(row.get("profile_type") or "").strip().lower()
    if profile_type == "mixed":
        return "双修"
    if profile_type == "creator":
        return "创作者"
    if profile_type == "reviewer":
        return "测评号"
    return "未分类"


def _type_score_for_bucket(row: dict[str, Any], bucket: str) -> float | None:
    if bucket == "creator":
        return _optional_float(row.get("creator_type_score"))
    if bucket == "reviewer":
        return _optional_float(row.get("reviewer_type_score"))
    return None


def _provisional_profile_lane(row: dict[str, Any]) -> tuple[str, str]:
    """Content-term hint only; it never relabels an unknown indexed type."""

    blob = " ".join(
        str(row.get(key) or "").lower()
        for key in ("bio", "primary_topic", "content_style", "secondary_topics_json")
    )
    reviewer_terms = ("review", "reviewer", "comparison", "unboxing", "评测", "测评", "对比", "器材")
    creator_terms = ("photographer", "filmmaker", "videographer", "creator", "摄影师", "视频", "人像")
    if any(term in blob for term in reviewer_terms):
        return "reviewer", "factual_pool_content_terms"
    if any(term in blob for term in creator_terms):
        return "creator", "factual_pool_content_terms"
    return "unknown", "insufficient_type_evidence"


def _recall_rank_score(
    *,
    vector_score: float | None,
    type_score: float | None,
    vector_weight: float,
    type_weight: float,
    type_boost_enabled: bool,
) -> float | None:
    if not type_boost_enabled:
        return float(vector_score) if vector_score is not None else None
    score, _missing, _coverage = missingness_aware_weighted_score(
        (
            ("retrieval", vector_score, float(vector_weight)),
            ("type", (float(type_score) / 100.0) if type_score is not None else None, float(type_weight)),
        )
    )
    return score


def _format_item(
    hit: RecallHit,
    row: dict[str, Any],
    bucket: str,
    *,
    vector_weight: float,
    type_weight: float,
    type_boost_enabled: bool,
    evidence: dict[str, Any],
    persona_text: str = "",
    product_label: str = "",
    video_leaning: bool = False,
) -> dict[str, Any]:
    row = project_pool_item_truth(row)
    # Ranking/relevance may consume only confirmed cooperation evidence.  Sourced
    # legacy claims remain visible in pool detail with a ``declared`` receipt,
    # but never increase search relevance as if they were observed facts.
    row["brand_collaborations_json"] = row.get("brand_collaborations_factual_json") or []
    type_rank_score = _type_score_for_bucket(row, bucket)
    retrieval_score = fmt.retrieval_score_of(hit)
    rank_score = _recall_rank_score(
        vector_score=retrieval_score,
        type_score=type_rank_score,
        vector_weight=vector_weight,
        type_weight=type_weight,
        type_boost_enabled=type_boost_enabled,
    )
    # 独立展示信号(绝不并入 viltrox_fit_score / recall_rank_score / vector_score / rule_v0)。
    relevance_adjust, relevance_flags, relevance_notes, tier_hint = _relevance_signals(
        row, evidence, video_leaning=video_leaning
    )
    why_fit_text = _why_fit(row, evidence, persona_text, product_label)
    if relevance_notes:
        why_fit_text = f"{why_fit_text}({';'.join(relevance_notes)})"
    provisional_lane, provisional_lane_source = _provisional_profile_lane(row)
    evidence_quality = fmt.evidence_quality_of(evidence)
    data_truth = row.get("data_truth") or {}
    item = {
        "kol_pool_id": int(row.get("kol_pool_id") or hit.kol_pool_id),
        **{key: fmt.str_field(row, key) for key in ("handle", "display_name", "platform", "profile_url", "avatar_url")},
        **{key: row.get(key) for key in (
            "followers", "avg_views", "avg_likes", "avg_comments", "engagement_rate",
            "real_er", "real_er_sample_n", "real_er_computed_at", "real_er_method", "data_truth",
        )},
        **{key: data_truth.get(key) for key in (
            "source_type", "source_ref", "metric_observed_at", "metric_recorded_at",
        )},
        "last_seen_at": row.get("last_seen_at"),
        "updated_at": row.get("updated_at"),
        **{key: fmt.str_field(row, key) for key in ("country", "language", "primary_topic", "bio")},
        "vector_score": fmt.round_opt(hit.vector_score, 6),
        "lexical_score": fmt.round_opt(hit.lexical_score, 6),
        "hybrid_rrf_score": hit.hybrid_rrf_score,
        "retrieval_score": fmt.round_opt(retrieval_score, 6),
        "retrieval_method": hit.retrieval_method,
        "type_rank_score": fmt.round_opt(type_rank_score, 1),
        "recall_rank_score": fmt.round_opt(rank_score, 6),
        "recall_rank_score_method": "missingness_aware_retrieval_type_v1",
        # 独立展示分:= recall_rank_score + 展示用 adjust;仅供前端展示排序/分档,
        # 绝不回写任何评分字段(recall_rank_score 原值不变,供审计)。
        "display_rank_score": fmt.display_rank_score(rank_score, relevance_adjust),
        "display_relevance_adjust": round(relevance_adjust, 6),
        "relevance_flags": relevance_flags,
        "relevance_tier_hint": tier_hint,
        "profile_type": fmt.str_field(row, "profile_type"),
        "bucket": bucket,
        **fmt.provisional_lane_fields(bucket, provisional_lane, provisional_lane_source),
        "type_label": _type_label(row),
        "creator_type_score": _optional_float(row.get("creator_type_score")),
        "reviewer_type_score": _optional_float(row.get("reviewer_type_score")),
        "type_reason": fmt.str_field(row, "type_reason"),
        "type_method": fmt.str_field(row, "type_method"),
        "recall_reason": _recall_reason(row, evidence),
        "why_fit": why_fit_text,
        "source_fields": {
            "vector_method": METHOD,
            "type_method": fmt.str_field(row, "type_method"),
            "qdrant_point_id": hit.qdrant_point_id,
            "retrieval_method": hit.retrieval_method,
            "retrieval_tier": hit.retrieval_tier,
            "retrieval_meta": hit.retrieval_meta,
            "sufficiency": fmt.str_field(row, "sufficiency"),
            "ranking_method": "missingness_aware_retrieval_type_v1",
            "evidence_coverage": evidence_quality,
        },
        "evidence_quality": evidence_quality,
    }
    fmt.attach_optional_evidence(
        item, evidence,
        public_content_evidence_status(evidence.get("targeted_content_evidence_status")),
    )
    return item

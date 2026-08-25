"""KOL 内容契合深析：只消费经证明的 final_v1 视频证据，绝不改 rule_v0 评分。"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.logging import get_logger
from app.core.model_registry import current_task_model_binding, split_binding
from app.db.connection import get_conn
from app.domains.analysis.cache_reuse import canonical_final_v1_cache_reuse
from app.platform import llm_production
from app.platform.llm_runtime_errors import normalise_job_error

logger = get_logger("viltrox.domains.kol.content_fit_analysis")
# 独立命名空间(红线:不与 viltrox_fit / outreach_draft / video 分析重叠)
TARGET_TYPE = "kol"
DERIVE_METHOD = "content_fit_v1"
VIDEO_DERIVE_METHOD = "video_analysis_final_v1"
POOL_EVIDENCE_POST_TABLE = "vkpi_kol_video_evidence"

# 只读这么多【已深析】视频进 fit prompt,控成本(final_v1 单条已很厚)。
# P9:8 太小→样本不足、Viltrox 识别易零命中;抬到 15(只读已有 final_v1,不新跑 Gemini;
# fit LLM 仍走 budget_guard),可经 env 覆盖。
MAX_VIDEOS = max(1, int(os.environ.get("VKPI_CONTENT_FIT_MAX_VIDEOS", "15")))
MAX_COMMENTS = 30
MAX_OUTPUT_TOKENS = 1400
LLM_PURPOSE = "vkpi_kol_content_fit"
LLM_COST_TAG = "vkpi_kol_content_fit"
MODEL_TASK = "kol_content_fit_analysis"
MAX_MODEL_ATTEMPTS = 3
CONTENT_FIT_JOB_TYPE = "kol_content_fit_analysis"
ACTIVE_JOB_STATES = frozenset({"queued", "running", "retrying", "processing"})
FAILED_JOB_STATES = frozenset({"failed", "triage", "cancelled", "canceled", "void", "timeout"})


class _VideoAnalyses(list[dict[str, Any]]):
    """List-compatible loader result carrying the read-only cache proof gate."""


def _video_analysis_cache_gate(videos: Any) -> dict[str, Any]:
    gate = getattr(videos, "cache_gate", None)
    return dict(gate) if isinstance(gate, dict) else {}


def legacy_video_cache_response(kol_pool_id: int, product_sku: str | None, gate: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "legacy_unverified", "state": "legacy_unverified", "terminal": True,
        "kol_pool_id": int(kol_pool_id), "product_sku": normalize_product_sku(product_sku) or None,
        "derive_method": content_fit_derive_method(product_sku),
        "reason": "legacy_video_analysis_cache_unverified",
        "revalidation_required": True, "claim_status": "descriptive_only",
        "cache_reuse_status": "legacy_unverified", "cache_gate": dict(gate),
        "provider_calls": False, "write_db": False, "cached": False,
    }

def normalize_product_sku(product_sku: str | None) -> str:
    """Canonical product identity used by cache and active-job idempotency."""
    return " ".join(str(product_sku or "").strip().upper().split())


def content_fit_derive_method(product_sku: str | None = None) -> str:
    """Return a stable cache namespace for the exact product scope."""
    normalized = normalize_product_sku(product_sku)
    if not normalized:
        return DERIVE_METHOD
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{DERIVE_METHOD}:sku:{digest}"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _loads(value: Any) -> Any:
    if value in (None, "", b""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return value


def _text(value: Any, limit: int = 600) -> str:
    return str(value or "").strip()[:limit]


def _model_binding() -> tuple[str, str]:
    """Return the reviewed exact binding; registration is not an availability claim."""
    return split_binding(current_task_model_binding().get(MODEL_TASK) or "")


_NON_RETRYABLE_ERROR_MARKERS = (
    "readiness",
    "production_ready",
    "budget",
    "not_configured",
    "model_binding",
    "invalid_api_key",
    "unauthorized",
    "forbidden",
    "permission",
    "credential",
    "ai_disabled",
)
_RETRYABLE_ERROR_MARKERS = (
    "timeout",
    "deadline",
    "rate_limit",
    "ratelimit",
    "rate limit",
    "too_many_requests",
    "too many requests",
    "429",
    "throttl",
    "provider_unavailable",
    "upstream",
    "connection",
    "network",
    "reset by peer",
    "temporarily unavailable",
)
_REPAIRABLE_RESPONSE_MARKERS = (
    "parse_failure",
    "validation_failure",
    "empty_response",
)
_GENERIC_REPAIRABLE_RESPONSE_MARKERS = ("schema_failure",)


def _error_candidates(value: Any, *, depth: int = 0) -> list[str]:
    """Extract bounded provider error codes/messages, including nested errors."""
    if depth > 8:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text[:240]] if text else []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    if isinstance(value, list):
        out: list[str] = []
        for item in reversed(value[-5:]):
            out.extend(_error_candidates(item, depth=depth + 1))
        return out
    if not isinstance(value, dict):
        return []
    out = []
    priority_keys = (
        "code",
        "error_code",
        "failure_code",
        "type",
        "status",
        "reason",
        "message",
        "error",
        "detail",
        "cause",
    )
    for key in priority_keys:
        if key in value:
            out.extend(_error_candidates(value.get(key), depth=depth + 1))
    for key, nested in value.items():
        if key not in priority_keys and isinstance(nested, (dict, list)):
            out.extend(_error_candidates(nested, depth=depth + 1))
    return out


def _classified_error(candidates: list[str], markers: tuple[str, ...]) -> str:
    for candidate in candidates:
        lowered = candidate.lower()
        matched = next((marker for marker in markers if marker in lowered), "")
        if not matched:
            continue
        compact = candidate[:120]
        if compact and all(char.isalnum() or char in "_.:-" for char in compact):
            return compact
        if matched in {
            "rate_limit",
            "ratelimit",
            "rate limit",
            "too_many_requests",
            "too many requests",
            "429",
            "throttl",
        }:
            return "rate_limit"
        if matched in {"timeout", "deadline"}:
            return "timeout"
        if matched in {"connection", "network", "reset by peer"}:
            return "connection_error"
        if matched in {"provider_unavailable", "upstream", "temporarily unavailable"}:
            return "provider_unavailable"
        if matched in {"invalid_api_key", "unauthorized", "forbidden", "permission", "credential"}:
            return "provider_auth_failed"
        if matched == "budget":
            return "budget_exceeded"
        if matched in {"readiness", "production_ready", "model_binding"}:
            return "readiness_not_production_ready"
        if matched == "not_configured":
            return "not_configured"
        if matched == "ai_disabled":
            return "ai_disabled"
        return "llm_unavailable"
    return ""


def _failure_code(value: Any) -> str:
    result = value if isinstance(value, dict) else {}
    failure = result.get("failure") if isinstance(result.get("failure"), dict) else {}
    top_level = [
        str(item).strip()
        for item in (
            failure.get("code"),
            result.get("failure_code"),
            result.get("reason"),
        )
        if str(item or "").strip()
    ]
    nested = _error_candidates(result.get("errors"))
    # Readiness/budget/auth failures are terminal even if a nested transport
    # message also exists. Otherwise surface nested transient causes so an
    # exact-model retry is not suppressed by a generic provider_failed shell.
    terminal = _classified_error(top_level + nested, _NON_RETRYABLE_ERROR_MARKERS)
    if terminal:
        return terminal
    transient = _classified_error(nested + top_level, _RETRYABLE_ERROR_MARKERS)
    if transient:
        return transient
    repairable = _classified_error(nested + top_level, _REPAIRABLE_RESPONSE_MARKERS)
    if repairable:
        return repairable
    generic_repairable = _classified_error(
        nested + top_level,
        _GENERIC_REPAIRABLE_RESPONSE_MARKERS,
    )
    if generic_repairable:
        return generic_repairable
    return str((top_level + nested + ["llm_unavailable"])[0])[:120]


def _retryable_failure(value: Any) -> bool:
    code = _failure_code(value).lower()
    if any(marker in code for marker in _NON_RETRYABLE_ERROR_MARKERS):
        return False
    return any(
        marker in code
        for marker in (
            *_RETRYABLE_ERROR_MARKERS,
            *_REPAIRABLE_RESPONSE_MARKERS,
            *_GENERIC_REPAIRABLE_RESPONSE_MARKERS,
        )
    )


def _valid_content_fit_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if str(value.get("fit_verdict") or "") not in {"fit", "partial_fit", "not_fit"}:
        return False
    for key in ("creator_type", "content_summary", "audience_signal"):
        if not isinstance(value.get(key), str) or not str(value.get(key) or "").strip():
            return False
    reasons = value.get("fit_reasons")
    if not isinstance(reasons, list) or not 1 <= len(reasons) <= 8:
        return False
    if not all(isinstance(item, str) and item.strip() for item in reasons):
        return False
    confidence = value.get("confidence")
    return (
        not isinstance(confidence, bool)
        and isinstance(confidence, (int, float))
        and 0.0 <= float(confidence) <= 1.0
    )


# ── 只读：KOL 基本信息 ──────────────────────────────────────────────


def _kol_row(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, handle, display_name, platform, followers, primary_topic,
               content_style, bio
        FROM vkpi_kol_pool WHERE id=?
        """,
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row) if row else None


# ── 只读：该 KOL 全部视频的 final_v1 Gemini 深析(主依据) ────────────


def _video_analyses(conn: Any, kol_pool_id: int, *, limit: int = MAX_VIDEOS) -> list[dict[str, Any]]:
    """只读并返回全体 canonical final_v1；任一 legacy ready 行即整体拒绝。"""
    rows = conn.execute(
        """
        SELECT
            e.id AS evidence_id,
            COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), e.content_url) AS title,
            e.content_url,
            e.platform,
            e.view_count,
            e.like_count,
            e.comment_count,
            ac.id, ac.target_type, ac.target_id, ac.derive_method,
            ac.model, ac.prompt_version, ac.status,
            ac.result AS result
        FROM vkpi_analysis_cache ac
        JOIN vkpi_kol_video_evidence e ON e.id = CAST(ac.target_id AS BIGINT)
        WHERE ac.target_type = 'video'
          AND ac.derive_method = ?
          AND ac.status = 'ready'
          AND e.kol_pool_id = ?
        ORDER BY COALESCE(e.view_count, 0) DESC, e.id DESC
        """,
        (VIDEO_DERIVE_METHOD, int(kol_pool_id)),
    ).fetchall()
    analyses = _VideoAnalyses()
    analyses.cache_gate = {
        "status": "canonical", "revalidation_required": False,
        "claim_status": "descriptive_only", "reasons": [],
    }
    legacy: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        try:
            decision = canonical_final_v1_cache_reuse(
                data,
                target_type="video",
                target_id=str(data.get("evidence_id") or data.get("target_id") or ""),
                derive_method=VIDEO_DERIVE_METHOD,
            )
        except Exception:
            logger.warning("vkpi.content_fit.video_cache_classifier_failed", exc_info=True)
            decision = {"reusable": False, "cache_id": data.get("id"),
                        "reasons": ["canonical_classifier_failed"]}
        if not decision.get("reusable"):
            legacy.append(dict(decision))
            continue
        if len(analyses) >= int(limit):
            continue
        result = _loads(data.get("result")) or {}
        if not isinstance(result, dict):
            continue
        layer1 = result.get("layer1_visual_content") or {}
        layer2 = result.get("layer2_viewer_emotion") or {}
        layer3 = result.get("layer3_three_values") or {}
        layer5 = result.get("layer5_recommendations") or {}
        layer6 = result.get("layer6_flags_and_scores") or {}
        raw = result.get("raw_gemini_video") or {}
        scene_timeline = layer1.get("scene_timeline") if isinstance(layer1, dict) else None
        scenes = []
        if isinstance(scene_timeline, list):
            for sc in scene_timeline[:6]:
                if isinstance(sc, dict):
                    scenes.append(
                        {
                            "timestamp": _text(sc.get("timestamp"), 16),
                            "what": _text(sc.get("what"), 200),
                            "why_it_matters": _text(sc.get("why_it_matters"), 200),
                        }
                    )
        scores = layer6.get("scores") if isinstance(layer6, dict) else None
        analyses.append(
            {
                "evidence_id": data.get("evidence_id"),
                "title": _text(data.get("title"), 200),
                "content_url": _text(data.get("content_url"), 300),
                "platform": _text(data.get("platform"), 40),
                "view_count": data.get("view_count"),
                "content_summary": _text((layer1 or {}).get("content_summary"), 600),
                "brand_exposure": _text((layer1 or {}).get("brand_exposure"), 300),
                "product_presence": _text((layer1 or {}).get("product_presence"), 300),
                "competitor_presence": _text((layer1 or {}).get("competitor_presence"), 300),
                "scene_timeline": scenes,
                "viewer_reaction": _text((layer2 or {}).get("one_sentence_viewer_reaction"), 300),
                "first_three_seconds": _text((layer2 or {}).get("first_three_seconds_feeling"), 300),
                "three_values": layer3 if isinstance(layer3, dict) else {},
                "scores": scores if isinstance(scores, dict) else {},
                "key_hook": _text((layer6 or {}).get("key_hook"), 200),
                "risk_flags": _text((layer6 or {}).get("risk_flags"), 200),
                "final_verdict": _text((layer6 or {}).get("final_verdict"), 400),
                "cooperation_recommendation": _text((layer5 or {}).get("cooperation_recommendation"), 400),
                "content_genre": _text((raw or {}).get("content_genre"), 120),
                "content_topic": _text((raw or {}).get("content_topic"), 200),
                "target_audience": _text((raw or {}).get("target_audience"), 300),
                "viltrox_detected": bool((raw or {}).get("viltrox_detected")),
                "viltrox_products_all": (raw or {}).get("viltrox_products_all") or [],
                "competitor_mentions": (raw or {}).get("competitor_mentions") or [],
            }
        )
    if legacy:
        analyses.clear()
        analyses.cache_gate = {
            "status": "legacy_unverified", "revalidation_required": True,
            "claim_status": "descriptive_only",
            "reasons": list(dict.fromkeys(
                reason for item in legacy for reason in (item.get("reasons") or [])
            )),
            "cache_ids": [item.get("cache_id") for item in legacy if item.get("cache_id") is not None],
        }
    return analyses


# ── 只读：粉丝评论情报(受众信号) ──────────────────────────────────


def _fan_comments(conn: Any, kol_pool_id: int, *, limit: int = MAX_COMMENTS) -> list[dict[str, Any]]:
    """只读该 KOL evidence 下的可用粉丝评论。"""
    rows = conn.execute(
        """
        SELECT v.comment_text, v.author_handle, v.likes_count, v.language_detected
        FROM vkpi_comments v
        JOIN vkpi_kol_video_evidence e ON e.id = v.post_id
        WHERE v.post_table = ?
          AND e.kol_pool_id = ?
          AND COALESCE(NULLIF(TRIM(v.comment_text), ''), '') != ''
        ORDER BY COALESCE(v.likes_count, 0) DESC, v.id ASC
        LIMIT ?
        """,
        (POOL_EVIDENCE_POST_TABLE, int(kol_pool_id), int(limit)),
    ).fetchall()
    return [
        {
            "text": _text(r["comment_text"], 240),
            "author": _text(r["author_handle"], 80),
            "likes": r["likes_count"],
            "lang": _text(r["language_detected"], 16),
        }
        for r in rows
    ]


# ── 只读：规则版 dimensions_11 ─────────────────────────────────────


def _dimensions_11(kol_pool_id: int) -> dict[str, Any]:
    """读规则版 11 维(rule_dimensions_11_v0);失败返回 {} 不阻断(评论/视频已是主依据)。"""
    try:
        from app.domains.kol import eleven_dimensions
        persisted = eleven_dimensions.load_persisted_dimensions_11(int(kol_pool_id))
        if isinstance(persisted, dict) and persisted:
            return persisted
        return eleven_dimensions.compose_dimensions_11(int(kol_pool_id))
    except Exception:
        logger.warning("vkpi.content_fit.dimensions11_failed", exc_info=True)
        return {}


# ── 产品画像(SKU 或自由文本 persona) ──────────────────────────────


def _resolve_product(product_sku: str | None, product_persona: str | None) -> dict[str, Any]:
    """SKU → 真目录深度;或直接用调用方给的 persona 文本。只读 vkpi_products 目录。"""
    sku = str(product_sku or "").strip()
    if sku:
        try:
            from app.domains.costs.product_catalog import list_product_catalog
            products = list_product_catalog(limit=300, query="").get("products") or []
            for prod in products:
                if str(prod.get("sku") or "").strip().lower() == sku.lower():
                    return {
                        "mode": "sku",
                        "sku": str(prod.get("sku") or ""),
                        "model_name": str(prod.get("model_name") or ""),
                        "marketing_name": str(prod.get("marketing_name") or ""),
                        "category_main": str(prod.get("category_main") or ""),
                        "category_detail": str(prod.get("category_detail") or ""),
                        "price_usd": float(prod["price_usd"]) if prod.get("price_usd") is not None else None,
                        "description": _text(prod.get("description"), 800),
                    }
        except Exception:
            logger.warning("vkpi.content_fit.product_lookup_failed", exc_info=True)
        # SKU 给了但没命中:仍把原始 SKU 当线索喂 LLM(诚实标 unresolved)
        return {"mode": "sku_unresolved", "sku": sku}
    persona = str(product_persona or "").strip()
    if persona:
        return {"mode": "persona", "persona": _text(persona, 1200)}
    return {"mode": "none"}


# ── prompt 构造 ────────────────────────────────────────────────────


def _build_prompt(
    kol: dict[str, Any],
    product: dict[str, Any],
    videos: list[dict[str, Any]],
    comments: list[dict[str, Any]],
    dimensions: dict[str, Any],
) -> str:
    kol_lines = [
        f"- 平台/Platform: {kol.get('platform') or '-'}",
        f"- Handle: {kol.get('handle') or '-'}",
        f"- 名称/Name: {kol.get('display_name') or '-'}",
        f"- 粉丝/Followers: {kol.get('followers') or '-'}（注意:粉丝数是表层指标,判断不得以它为主）",
        f"- 主题/Topic: {kol.get('primary_topic') or '-'}",
    ]
    if product.get("mode") == "sku":
        product_lines = [
            f"- 模式: 真实 SKU 深度",
            f"- SKU: {product.get('sku')}",
            f"- 名称: {product.get('marketing_name') or product.get('model_name')}",
            f"- 类别: {product.get('category_main')} / {product.get('category_detail')}",
            f"- 价格: {product.get('price_usd')} USD",
            f"- 描述: {product.get('description')}",
        ]
    elif product.get("mode") == "sku_unresolved":
        product_lines = [f"- 模式: SKU 未在目录命中(原始线索: {product.get('sku')})"]
    elif product.get("mode") == "persona":
        product_lines = [f"- 模式: 产品画像/persona", f"- 画像: {product.get('persona')}"]
    else:
        product_lines = ["- 模式: 未指定具体产品,做通用品牌契合判断(VILTROX 唯卓仕 相机镜头/影像配件)"]
    video_blocks: list[str] = []
    for idx, v in enumerate(videos, 1):
        scenes = "; ".join(
            f"[{s.get('timestamp')}] {s.get('what')}（{s.get('why_it_matters')}）"
            for s in (v.get("scene_timeline") or [])
        )
        video_blocks.append(
            "\n".join(
                [
                    f"视频{idx}（{v.get('platform')} · {v.get('view_count') or '?'} 播放）: {v.get('title')}",
                    f"  画面综述: {v.get('content_summary') or '-'}",
                    f"  分镜: {scenes or '-'}",
                    f"  品牌露出: {v.get('brand_exposure') or '-'}",
                    f"  产品在场: {v.get('product_presence') or '-'}",
                    f"  竞品在场: {v.get('competitor_presence') or '-'}",
                    f"  观众反应: {v.get('viewer_reaction') or '-'}",
                    f"  题材/受众: {v.get('content_genre') or '-'} / {v.get('target_audience') or '-'}",
                    f"  Viltrox 识别: {'是' if v.get('viltrox_detected') else '否'}; 竞品提及: {v.get('competitor_mentions')}",
                    f"  该视频判语: {v.get('final_verdict') or '-'}",
                ]
            )
        )
    comment_lines = [
        f"  - ({c.get('likes') or 0}赞) @{c.get('author')}: {c.get('text')}" for c in comments[:MAX_COMMENTS]
    ]
    dim_summary = "-"
    if isinstance(dimensions, dict) and dimensions:
        dim_summary = (
            f"overall_score={dimensions.get('overall_score')}, "
            f"method={dimensions.get('method')}, "
            f"data_completeness={(dimensions.get('confidence') or {}).get('data_completeness')}"
        )
    return (
        "你是 VILTROX(唯卓仕,相机镜头/影像配件品牌)的 KOL 内容契合深析专家。\n"
        "**判断主依据**:该 KOL 过往视频的画面/故事(Gemini 视频深析)+ 粉丝评论。"
        "这两者**胜过粉丝数等表层指标**。粉丝数只能作背景,不得作为适合/不适合的主理由。\n\n"
        "【KOL 资料】\n" + "\n".join(kol_lines) + "\n\n"
        "【目标产品】\n" + "\n".join(product_lines) + "\n\n"
        "【该 KOL 过往视频的画面与故事(主依据①)】\n"
        + ("\n\n".join(video_blocks) if video_blocks else "(无)") + "\n\n"
        "【粉丝评论(主依据②,反映真实受众)】\n"
        + ("\n".join(comment_lines) if comment_lines else "(无评论数据)") + "\n\n"
        "【规则版 11 维画像(辅助)】\n" + dim_summary + "\n\n"
        "请基于以上**真实视频内容与评论**(不要凭空发挥),输出 JSON:\n"
        "{\n"
        '  "creator_type": "他是什么类型的创作者(基于真实视频画面/题材,一句话)",\n'
        '  "content_summary": "过往视频画面/故事综述(2-4 句,引用具体分镜/场景)",\n'
        '  "audience_signal": "粉丝评论反映的受众画像与情绪(若无评论则说明数据缺失,不杜撰)",\n'
        '  "fit_verdict": "fit | partial_fit | not_fit 三选一(该 KOL 对该产品是否适合)",\n'
        '  "fit_reasons": ["逐条理由,每条必须基于上面的视频内容或评论证据,标明依据"],\n'
        '  "confidence": 0.0 到 1.0 的小数(视频/评论越充分越高)\n'
        "}\n"
        "只返回 JSON,不要额外文字。"
    )


# ── 缓存读写 ───────────────────────────────────────────────────────


def _read_cache(
    conn: Any,
    kol_pool_id: int,
    product_sku: str | None = None,
    *,
    include_stale: bool = False,
) -> dict[str, Any] | None:
    derive_method = content_fit_derive_method(product_sku)
    status_clause = "status IN ('ready', 'stale')" if include_stale else "status = 'ready'"
    row = conn.execute(
        f"""
        SELECT result, model, cost, status, updated_at
        FROM vkpi_analysis_cache
        WHERE target_type = ? AND target_id = ? AND derive_method = ? AND {status_clause}
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (TARGET_TYPE, str(int(kol_pool_id)), derive_method),
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    state = str(data.get("status") or "ready").strip().lower()
    if state not in {"ready", "stale"}:
        state = "stale"
    result = _loads(data.get("result")) or {}
    return {
        "status": state,
        "state": state,
        "kol_pool_id": int(kol_pool_id),
        "product_sku": normalize_product_sku(product_sku) or None,
        "derive_method": derive_method,
        "result": result,
        "model": data.get("model"),
        "cost": data.get("cost"),
        "updated_at": str(data.get("updated_at") or ""),
        "cached": True,
    }


def _content_fit_job_snapshot(
    conn: Any,
    kol_pool_id: int,
    product_sku: str | None = None,
    *,
    job_id: int | None = None,
) -> dict[str, Any] | None:
    requested_job_id = int(job_id or 0)
    derive_method = content_fit_derive_method(product_sku)
    job_clause = " AND id = ?" if requested_job_id > 0 else ""
    params: tuple[Any, ...] = (
        CONTENT_FIT_JOB_TYPE,
        TARGET_TYPE,
        str(int(kol_pool_id)),
        derive_method,
        *((requested_job_id,) if requested_job_id > 0 else ()),
    )
    row = conn.execute(
        f"""
        SELECT id, status, last_error, last_error_category, next_retry_at,
               created_at, started_at, updated_at
        FROM apify_jobs
        WHERE job_type = ?
          AND payload->>'target_type' = ?
          AND payload->>'target_id' = ?
          AND payload->>'derive_method' = ?
          {job_clause}
        ORDER BY
          (status IN ('queued', 'running', 'retrying', 'processing')) DESC,
          updated_at DESC,
          id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    status = str(data.get("status") or "").strip().lower()
    if status in ACTIVE_JOB_STATES:
        state = status
    elif status == "blocked":
        state = "blocked"
    elif status in FAILED_JOB_STATES or status == "done":
        state = "failed"
    else:
        state = "pending"
    raw_error = _loads(data.get("last_error"))
    if raw_error in (None, "", b""):
        normalized_error: dict[str, Any] = {}
        raw_stage = ""
    elif isinstance(raw_error, dict):
        normalized_error = normalise_job_error(
            raw_error.get("reason") or raw_error.get("error"),
            raw_error.get("reason_detail"),
        )
        raw_stage = str(raw_error.get("stage") or "").strip().lower().replace(" ", "_")
    else:
        normalized_error = normalise_job_error(raw_error)
        raw_stage = ""
    reason = _text(normalized_error.get("reason"), 120) or None
    reason_detail = _text(normalized_error.get("reason_detail"), 120) or None
    stage = raw_stage if re.fullmatch(r"[a-z][a-z0-9_.:-]{0,99}", raw_stage) else None
    raw_category = str(data.get("last_error_category") or "").strip().lower().replace(" ", "_")
    error_category = raw_category if re.fullmatch(r"[a-z][a-z0-9_.:-]{0,79}", raw_category) else None
    return {
        "id": int(data["id"]),
        "status": status or None,
        "state": state,
        "terminal": state in {"ready", "blocked", "failed"},
        "reason": reason,
        "reason_detail": reason_detail,
        "error_category": error_category,
        "stage": stage,
        "next_retry_at": data.get("next_retry_at") or None,
        "created_at": data.get("created_at") or None,
        "started_at": data.get("started_at") or None,
        "updated_at": data.get("updated_at") or None,
    }


def _as_utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _job_supersedes_cache(job: dict[str, Any] | None, cached: dict[str, Any] | None) -> bool:
    if not job:
        return False
    if not cached:
        return True
    created_at = _as_utc_datetime(job.get("created_at"))
    cache_updated_at = _as_utc_datetime(cached.get("updated_at"))
    return bool(created_at and cache_updated_at and created_at > cache_updated_at)


def _write_cache(
    conn: Any,
    kol_pool_id: int,
    result: dict[str, Any],
    *,
    model: str,
    cost_usd: float,
    triggered_by_user_id: int | None,
    product_sku: str | None = None,
) -> None:
    now = _utcnow()
    derive_method = content_fit_derive_method(product_sku)
    conn.execute(
        """
        INSERT INTO vkpi_analysis_cache (
            target_type, target_id, model, derive_method, result, cost, status,
            triggered_by_user_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?::jsonb, ?, 'ready', ?, ?, ?)
        ON CONFLICT (target_type, target_id, derive_method)
        DO UPDATE SET model=EXCLUDED.model, result=EXCLUDED.result, cost=EXCLUDED.cost,
            status='ready', triggered_by_user_id=EXCLUDED.triggered_by_user_id,
            updated_at=EXCLUDED.updated_at
        """,
        (
            TARGET_TYPE,
            str(int(kol_pool_id)),
            str(model or "llm_gateway"),
            derive_method,
            json.dumps(result, ensure_ascii=False, default=str),
            float(cost_usd or 0.0),
            int(triggered_by_user_id) if triggered_by_user_id else None,
            now,
            now,
        ),
    )
    conn.commit()


def _parse_llm_json(text: str) -> dict[str, Any] | None:
    cleaned = str(text or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned[4:] if cleaned.lower().startswith("json") else cleaned
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


# ── 公共入口 ───────────────────────────────────────────────────────
def get_content_fit(
    kol_pool_id: int,
    product_sku: str | None = None,
    *,
    job_id: int | None = None,
) -> dict[str, Any]:
    """纯只读:返回 ready cache 或最新有界任务态，绝不入队/不烧 LLM。"""
    conn = get_conn()
    cached = _read_cache(conn, int(kol_pool_id), product_sku, include_stale=True)
    normalized_sku = normalize_product_sku(product_sku)
    job = _content_fit_job_snapshot(
        conn,
        int(kol_pool_id),
        normalized_sku,
        job_id=job_id,
    )
    if _job_supersedes_cache(job, cached):
        return {
            "status": job.get("status"),
            "state": job.get("state") or "pending",
            "terminal": bool(job.get("terminal")),
            "kol_pool_id": int(kol_pool_id),
            "product_sku": normalized_sku or None,
            "derive_method": content_fit_derive_method(normalized_sku),
            "job_id": job.get("id"),
            "analysis_job": job,
            "previous_cache_updated_at": cached.get("updated_at") if cached else None,
        }
    if cached:
        return {**cached, "job_id": job.get("id") if job else None, "analysis_job": job}
    if job:
        return {
            "status": job.get("status"),
            "state": job.get("state") or "pending",
            "terminal": bool(job.get("terminal")),
            "kol_pool_id": int(kol_pool_id),
            "product_sku": normalized_sku or None,
            "derive_method": content_fit_derive_method(normalized_sku),
            "job_id": job.get("id"),
            "analysis_job": job,
        }
    return {
        "status": "not_requested",
        "state": "missing",
        "kol_pool_id": int(kol_pool_id),
        "product_sku": normalized_sku or None,
        "derive_method": content_fit_derive_method(normalized_sku),
    }


def analyze_content_fit(
    kol_pool_id: int,
    product_sku: str | None = None,
    *,
    product_persona: str | None = None,
    force: bool = False,
    staff: dict[str, Any] | None = None,
    authorization_checkpoint: Callable[[bool], None] | None = None,
) -> dict[str, Any]:
    """基于 canonical final_v1、评论和 dimensions_11 生成独立 fit 缓存。"""
    kid = int(kol_pool_id)
    conn = get_conn()
    kol = _kol_row(conn, kid)
    if not kol:
        raise LookupError(f"kol_pool_id not found: {kid}")
    videos = _video_analyses(conn, kid)
    cache_gate = _video_analysis_cache_gate(videos)
    if cache_gate.get("status") == "legacy_unverified":
        return legacy_video_cache_response(kid, product_sku, cache_gate)
    if not force:
        cached = _read_cache(conn, kid, product_sku)
        if cached:
            return cached
    if not videos:
        # 诚实:无已分析视频证据,不杜撰、不烧 LLM、不写 cache(status 列只允许 ready/stale)。
        return {
            "state": "insufficient_evidence",
            "status": "insufficient_evidence",
            "kol_pool_id": kid,
            "reason": "no_ready_video_analysis",
            "hint": "该 KOL 暂无 video_analysis_final_v1 ready 数据;视频分析是另一条重链,本模块不新跑。",
            "video_count": 0,
            "comment_count": 0,
            "cached": False,
        }
    comments = _fan_comments(conn, kid)
    dimensions = _dimensions_11(kid)
    product = _resolve_product(product_sku, product_persona)

    prompt = _build_prompt(kol, product, videos, comments, dimensions)
    triggered_by_user_id = (staff or {}).get("user_id")
    provider, model = _model_binding()
    resp: dict[str, Any] = {}
    parsed: dict[str, Any] | None = None
    for attempt_index in range(1, MAX_MODEL_ATTEMPTS + 1):
        if authorization_checkpoint:
            authorization_checkpoint(attempt_index > 1)
        try:
            resp = llm_production.generate_json(
                prompt,
                provider=provider,
                model=model,
                purpose=LLM_PURPOSE,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                cost_tag=LLM_COST_TAG,
                triggered_by=triggered_by_user_id or MODEL_TASK,
                staff=staff or {},
                required_keys=(
                    "creator_type",
                    "content_summary",
                    "audience_signal",
                    "fit_verdict",
                    "fit_reasons",
                    "confidence",
                ),
                validator=_valid_content_fit_payload,
                metadata={
                    "task_binding": MODEL_TASK,
                    "kol_pool_id": kid,
                    "video_count": len(videos),
                    "comment_count": len(comments),
                    "product_mode": product.get("mode"),
                    "product_sku": normalize_product_sku(product_sku) or None,
                    "phase": "kol_analysis",
                    "subphase": "content_fit",
                    "attempt_index": attempt_index,
                    "total": MAX_MODEL_ATTEMPTS,
                    "target_label": _text(kol.get("handle") or kol.get("display_name"), 120),
                },
            )
        except Exception as exc:  # fail closed; the queue worker may retry later
            logger.warning("vkpi.content_fit.strict_llm_failed", exc_info=True)
            resp = {"status": "unavailable", "failure": {"code": type(exc).__name__}}
        candidate = resp.get("json") if isinstance(resp, dict) else None
        exact_response = (
            str(resp.get("status") or "") == "success"
            and str(resp.get("provider") or "").strip().lower() == provider
            and str(resp.get("model") or "").strip().startswith(model)
        )
        if exact_response and _valid_content_fit_payload(candidate):
            parsed = candidate
            break
        if exact_response:
            # A structurally invalid response may be retried, but every attempt keeps
            # the same exact binding and gets its own atomic reservation/progress row.
            continue
        if not _retryable_failure(resp):
            break

    if parsed is None:
        # 未就绪/预算拦截/契约错误均诚实返回,不写 cache,不把 rule_v0 冒充深析。
        reason = _failure_code(resp)
        if str(resp.get("status") or "") == "success":
            reason = "exact_model_or_json_contract_mismatch"
        return {
            "state": "llm_failed",
            "status": "llm_failed",
            "kol_pool_id": kid,
            "reason": reason,
            "video_count": len(videos),
            "comment_count": len(comments),
            "cached": False,
        }

    result = {
        "schema_version": DERIVE_METHOD,
        "verdict_status": "ready",
        "creator_type": _text(parsed.get("creator_type"), 300),
        "content_summary": _text(parsed.get("content_summary"), 1200),
        "audience_signal": _text(parsed.get("audience_signal"), 1200),
        "fit_verdict": _text(parsed.get("fit_verdict"), 40),
        "fit_reasons": [_text(x, 400) for x in (parsed.get("fit_reasons") or []) if str(x).strip()][:8],
        "confidence": _coerce_confidence(parsed.get("confidence")),
        "evidence_basis": {
            "video_count": len(videos),
            "comment_count": len(comments),
            "has_dimensions_11": bool(dimensions),
            "video_evidence_ids": [v.get("evidence_id") for v in videos],
        },
        "product_context": product,
        "provenance": {
            "model": model,
            "provider": provider,
            "task_binding": MODEL_TASK,
            "fallback_used": False,
            "generated_at": _utcnow(),
        },
    }

    if authorization_checkpoint:
        authorization_checkpoint(True)
    _write_cache(
        conn,
        kid,
        result,
        model=model,
        cost_usd=(
            float(resp.get("cost_micro_usd")) / 1_000_000.0
            if resp.get("cost_micro_usd") is not None
            else float(resp.get("cost_cents") or 0) / 100.0
        ),
        triggered_by_user_id=int(triggered_by_user_id) if triggered_by_user_id else None,
        product_sku=product_sku,
    )

    return {
        "state": "ready",
        "kol_pool_id": kid,
        "product_sku": normalize_product_sku(product_sku) or None,
        "derive_method": content_fit_derive_method(product_sku),
        "result": result,
        "model": model,
        "cost": float(resp.get("cost_cents") or 0) / 100.0,
        "updated_at": result["provenance"]["generated_at"],
        "cached": False,
    }


def _coerce_confidence(value: Any) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(1.0, num)), 3)

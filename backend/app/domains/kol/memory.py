"""KOL long-term memory aggregator (v1: pure aggregate, no LLM, no v6_fit).

This module builds a read-only "memory" snapshot of a KOL from existing
signals (deep analysis results, video evidence, content posts, assignments,
failed jobs, lifecycle timeline). It is physically isolated from KOL scoring:

  * It never reads or writes ``vkpi_kol_pool.viltrox_fit_score`` into the
    snapshot, and it never touches ``rule_v0``.
  * ``llm_v6_fit`` (LLM-only deep-fit signal) is deliberately excluded from the
    snapshot — it is adjacent-to-scoring semantics.
  * ``rebuild_kol_memory_snapshot`` wraps its INSERT with a viltrox_fit_score
    snapshot guard (mirrors video_evidence.py) and rolls back + raises if any
    score changed, proving zero score touches.

No LLM provider calls. No write to scoring tables.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.db.connection import get_conn
from app.domains.kol import lifecycle as kol_lifecycle
from app.platform import llm_gateway

MEMORY_METHOD = "kol_memory_pure_aggregate_v1"
SCORE_FIELDS = ("viltrox_fit_score", "viltrox_fit_reason")

# ─── v2: LLM 长期记忆 summary(预算放开 = record-only,绝不硬拦)───
MEMORY_METHOD_V2 = "kol_memory_llm_summary_v2"
# llm_gateway purpose / cost_tag。无 caps 行 = record-only:gateway 预算闸仅 warn 不 raise。
V2_PURPOSE = "vkpi_kol_memory_summary"
# MEMORY 教训:token 太小 = 分镜截断;1200 给人话 summary 足够,可调到 2048。
V2_MAX_OUTPUT_TOKENS = 1200
# prompt 取材时 timeline 截断条数(避免超长 prompt)。
_V2_TIMELINE_MAX = 12

_SHIPPED_STAGES = ("content_posted", "reviewed")
_PUBLISHED_STAGES = ("published",)
_PUBLISHED_POST_STATUSES = ("matched", "retrospective_ready")


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
    except Exception:
        return default
    return parsed if parsed is not None else default


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dim(item: Any) -> dict[str, Any]:
    dims = _loads(item, {})
    return dims if isinstance(dims, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _dedup_strings(values: list[Any]) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in seen:
            seen.append(text)
    return seen


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── source readers (all SELECT, never read/write viltrox_fit_score) ───

def _load_kol(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, content_style, recommended_product_lines_json, created_at
        FROM vkpi_kol_pool
        WHERE id=?
        """,
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row) if row else None


def _load_deep_results(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, analysis_kind, source_evidence_id, llm_dimensions_11, created_at
        FROM vkpi_kol_llm_deep_analysis_results
        WHERE kol_pool_id=?
          AND status='ready'
        ORDER BY created_at DESC, id DESC
        """,
        (int(kol_pool_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_video_evidence(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, content_url, platform, video_title, posted_at,
               view_count, like_count, comment_count
        FROM vkpi_kol_video_evidence
        WHERE kol_pool_id=?
          AND is_active IS NOT FALSE
        ORDER BY posted_at DESC NULLS LAST, id DESC
        """,
        (int(kol_pool_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_content_posts(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, project_id, platform, content_url, published_at,
               view_count, like_count, comment_count, status
        FROM vkpi_project_content_posts
        WHERE kol_pool_id=?
        ORDER BY published_at DESC NULLS LAST, id DESC
        """,
        (int(kol_pool_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_assignments(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, project_id, stage, stage_status, tracking_number, created_at, updated_at
        FROM vkpi_project_kol_assignments
        WHERE kol_pool_id=?
        ORDER BY created_at DESC
        """,
        (int(kol_pool_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_failed_jobs(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, job_type, last_error, updated_at
        FROM apify_jobs
        WHERE status='failed'
          AND payload->>'kol_pool_id' = ?
        ORDER BY updated_at DESC, id DESC
        """,
        (str(int(kol_pool_id)),),
    ).fetchall()
    return [dict(row) for row in rows]


# ─── inference helpers (pure aggregate; never derive scores) ───

def _infer_content_style(deep_results: list[dict[str, Any]], kol: dict[str, Any]) -> str:
    summaries: list[str] = []
    observations: list[str] = []
    for item in deep_results:
        dims = _dim(item.get("llm_dimensions_11"))
        layer1 = dims.get("layer1_summary") if isinstance(dims.get("layer1_summary"), dict) else {}
        content_summary = _text(layer1.get("content_summary"))
        if content_summary:
            summaries.append(content_summary)
        prod_obs = _text(layer1.get("production_observations"))
        if prod_obs:
            observations.append(prod_obs)
    parts = _dedup_strings(summaries + observations)
    if parts:
        return " | ".join(parts[:3])
    # fallback: read-only kol_pool.content_style string (never written).
    return _text(kol.get("content_style"))


def _infer_recommended_product_lines(deep_results: list[dict[str, Any]], kol: dict[str, Any]) -> list[str]:
    collected: list[Any] = []
    for item in deep_results:
        dims = _dim(item.get("llm_dimensions_11"))
        layer1 = dims.get("layer1_summary") if isinstance(dims.get("layer1_summary"), dict) else {}
        recs = dims.get("recommendations") if isinstance(dims.get("recommendations"), dict) else {}
        collected.extend(_as_list(layer1.get("product_presence")))
        collected.extend(_as_list(layer1.get("brand_exposure")))
        collected.extend(_as_list(recs.get("cooperation_recommendation")))
    lines = _dedup_strings(collected)
    if lines:
        return lines
    # fallback: read-only kol_pool.recommended_product_lines_json (TEXT json).
    fallback = _loads(kol.get("recommended_product_lines_json"), [])
    return _dedup_strings(_as_list(fallback))


def _infer_risk(deep_results: list[dict[str, Any]]) -> dict[str, Any]:
    flags: list[Any] = []
    final_verdict: str | None = None
    for item in deep_results:
        dims = _dim(item.get("llm_dimensions_11"))
        risk = dims.get("risk") if isinstance(dims.get("risk"), dict) else {}
        flags.extend(_as_list(risk.get("risk_flags")))
        verdict = _text(risk.get("final_verdict"))
        if verdict and final_verdict is None:
            final_verdict = verdict
    return {"risk_flags": _dedup_strings(flags), "final_verdict": final_verdict}


def _compute_fulfillment(
    assignments: list[dict[str, Any]],
    content_posts: list[dict[str, Any]],
    failed_jobs: list[dict[str, Any]],
) -> dict[str, int]:
    assigned_count = len(assignments)
    shipped_count = 0
    published_from_stage = 0
    for item in assignments:
        stage = _text(item.get("stage"))
        tracking = _text(item.get("tracking_number"))
        if stage in _SHIPPED_STAGES or tracking:
            shipped_count += 1
        if stage in _PUBLISHED_STAGES:
            published_from_stage += 1
    published_from_posts = sum(
        1 for item in content_posts if _text(item.get("status")) in _PUBLISHED_POST_STATUSES
    )
    published_count = published_from_posts + published_from_stage
    return {
        "assigned_count": assigned_count,
        "shipped_count": shipped_count,
        "published_count": published_count,
        "failed_jobs_count": len(failed_jobs),
    }


# ─── public API ───

def build_kol_memory_snapshot(kol_pool_id: int) -> dict[str, Any]:
    """Build the pure-aggregate memory snapshot (no LLM, no v6_fit, no score)."""

    kol_pool_id = int(kol_pool_id)
    conn = get_conn()
    kol = _load_kol(conn, kol_pool_id)
    if not kol:
        return {
            "status": "missing",
            "kol_pool_id": kol_pool_id,
            "snapshot": {
                "content_style": "",
                "recommended_product_lines": [],
                "risk": {"risk_flags": [], "final_verdict": None},
                "fulfillment": {
                    "assigned_count": 0,
                    "shipped_count": 0,
                    "published_count": 0,
                    "failed_jobs_count": 0,
                },
                "timeline": [],
                "note": "pure_aggregate,no_llm,no_v6_fit",
            },
            "source_counts": {
                "deep_results": 0,
                "video_evidence": 0,
                "content_posts": 0,
                "assignments": 0,
                "failed_jobs": 0,
            },
            "computed_at": _utcnow(),
            "note": "pure_aggregate, no_llm, no_v6_fit",
        }

    deep_results = _load_deep_results(conn, kol_pool_id)
    video_evidence = _load_video_evidence(conn, kol_pool_id)
    content_posts = _load_content_posts(conn, kol_pool_id)
    assignments = _load_assignments(conn, kol_pool_id)
    failed_jobs = _load_failed_jobs(conn, kol_pool_id)
    timeline = kol_lifecycle.collect_lifecycle_events(kol_pool_id)

    snapshot = {
        "content_style": _infer_content_style(deep_results, kol),
        "recommended_product_lines": _infer_recommended_product_lines(deep_results, kol),
        "risk": _infer_risk(deep_results),
        "fulfillment": _compute_fulfillment(assignments, content_posts, failed_jobs),
        "timeline": timeline,
        "note": "pure_aggregate,no_llm,no_v6_fit",
    }
    source_counts = {
        "deep_results": len(deep_results),
        "video_evidence": len(video_evidence),
        "content_posts": len(content_posts),
        "assignments": len(assignments),
        "failed_jobs": len(failed_jobs),
    }
    return {
        "status": "ready",
        "kol_pool_id": kol_pool_id,
        "snapshot": _jsonable(snapshot),
        "source_counts": source_counts,
        "computed_at": _utcnow(),
        "note": "pure_aggregate, no_llm, no_v6_fit",
    }


def _score_snapshot(conn: Any, kol_pool_id: int) -> dict[int, dict[str, Any]]:
    row = conn.execute(
        "SELECT id, viltrox_fit_score, viltrox_fit_reason FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        return {}
    item = dict(row)
    return {
        int(item["id"]): {
            "viltrox_fit_score": item.get("viltrox_fit_score"),
            "viltrox_fit_reason": item.get("viltrox_fit_reason"),
        }
    }


def _changed_score_ids(before: dict[int, dict[str, Any]], after: dict[int, dict[str, Any]]) -> list[int]:
    changed: list[int] = []
    for kol_id, before_item in before.items():
        after_item = after.get(kol_id, {})
        if any(before_item.get(field) != after_item.get(field) for field in SCORE_FIELDS):
            changed.append(kol_id)
    return changed


def rebuild_kol_memory_snapshot(kol_pool_id: int) -> dict[str, Any]:
    """Rebuild and persist a memory snapshot (pure aggregate, no LLM).

    Wraps the INSERT in a viltrox_fit_score snapshot guard to prove the rebuild
    touches zero scoring fields; rolls back + raises if any score changed.
    """

    kol_pool_id = int(kol_pool_id)
    built = build_kol_memory_snapshot(kol_pool_id)
    if built.get("status") == "missing":
        return {
            "written": False,
            "snapshot_id": None,
            "kol_pool_id": kol_pool_id,
            "snapshot": built.get("snapshot"),
            "source_counts": built.get("source_counts"),
            "llm_calls": False,
            "viltrox_fit_score_changed_ids": [],
            "computed_at": built.get("computed_at"),
            "status": "missing",
        }

    conn = get_conn()
    before_scores = _score_snapshot(conn, kol_pool_id)
    try:
        row = conn.execute(
            """
            INSERT INTO vkpi_kol_memory_snapshots
                (kol_pool_id, snapshot_json, source_counts, computed_at)
            VALUES (?, ?::jsonb, ?::jsonb, NOW())
            RETURNING id
            """,
            (
                kol_pool_id,
                json.dumps(built.get("snapshot") or {}, ensure_ascii=False, default=str),
                json.dumps(built.get("source_counts") or {}, ensure_ascii=False, default=str),
            ),
        ).fetchone()
        after_scores = _score_snapshot(conn, kol_pool_id)
        changed_ids = _changed_score_ids(before_scores, after_scores)
        if changed_ids:
            conn.rollback()
            raise RuntimeError(f"viltrox_fit_score changed unexpectedly: {changed_ids}; rolled back")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise

    snapshot_id = int(dict(row)["id"]) if row else None
    return {
        "written": snapshot_id is not None,
        "snapshot_id": snapshot_id,
        "kol_pool_id": kol_pool_id,
        "snapshot": built.get("snapshot"),
        "source_counts": built.get("source_counts"),
        "llm_calls": False,
        "viltrox_fit_score_changed_ids": [],
        "computed_at": built.get("computed_at"),
        "status": "ready",
    }


def get_latest_kol_memory_snapshot(kol_pool_id: int) -> dict[str, Any] | None:
    """Read the most recent persisted snapshot for one KOL, or None."""

    kol_pool_id = int(kol_pool_id)
    row = get_conn().execute(
        """
        SELECT id, kol_pool_id, snapshot_json, source_counts, computed_at
        FROM vkpi_kol_memory_snapshots
        WHERE kol_pool_id=?
        ORDER BY computed_at DESC, id DESC
        LIMIT 1
        """,
        (kol_pool_id,),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    return {
        "status": "ready",
        "snapshot_id": item.get("id"),
        "kol_pool_id": int(item.get("kol_pool_id")) if item.get("kol_pool_id") is not None else kol_pool_id,
        "snapshot": _loads(item.get("snapshot_json"), {}),
        "source_counts": _loads(item.get("source_counts"), {}),
        "computed_at": _jsonable(item.get("computed_at")),
        "note": "pure_aggregate, no_llm, no_v6_fit",
    }


# ─── v2: LLM 长期记忆 summary(预算放开;绝不写 viltrox_fit_score / 独立于 V6 Fit)───

def _build_summary_v2_prompt(snapshot: dict[str, Any]) -> str:
    """拼装 v2 长期记忆 prompt(纯字符串,镜像 outreach_draft._build_prompt 风格)。

    取材完全来自 v1 纯聚合 snapshot;timeline 截断避免超长。严禁打分语义。
    """
    content_style = _text(snapshot.get("content_style")) or "-"

    product_lines = snapshot.get("recommended_product_lines")
    product_lines = product_lines if isinstance(product_lines, list) else []
    product_text = "、".join(_dedup_strings(product_lines)[:12]) or "-"

    risk = snapshot.get("risk") if isinstance(snapshot.get("risk"), dict) else {}
    risk_flags = risk.get("risk_flags") if isinstance(risk.get("risk_flags"), list) else []
    risk_text = "、".join(_dedup_strings(risk_flags)[:12]) or "-"
    final_verdict = _text(risk.get("final_verdict")) or "-"

    fulfillment = snapshot.get("fulfillment") if isinstance(snapshot.get("fulfillment"), dict) else {}

    timeline = snapshot.get("timeline") if isinstance(snapshot.get("timeline"), list) else []
    recent = timeline[:_V2_TIMELINE_MAX]
    timeline_lines: list[str] = []
    for event in recent:
        if not isinstance(event, dict):
            timeline_lines.append(f"- {_text(event)}"[:200])
            continue
        kind = _text(event.get("event") or event.get("kind") or event.get("type"))
        when = _text(event.get("at") or event.get("occurred_at") or event.get("created_at"))
        label = _text(event.get("label") or event.get("title") or event.get("detail") or event.get("summary"))
        parts = [p for p in (when, kind, label) if p]
        timeline_lines.append(("- " + " · ".join(parts))[:200] if parts else "- (事件)")
    timeline_text = "\n".join(timeline_lines) if timeline_lines else "- (无生命周期事件)"

    material_lines = [
        f"- 内容风格/Content style: {content_style}",
        f"- 推荐产品线/Recommended product lines: {product_text}",
        f"- 风险标记/Risk flags: {risk_text}",
        f"- 风险结论/Final verdict: {final_verdict}",
        f"- 合作履约/Fulfillment: 已分配 {int(fulfillment.get('assigned_count') or 0)} · "
        f"已寄送 {int(fulfillment.get('shipped_count') or 0)} · "
        f"已发布 {int(fulfillment.get('published_count') or 0)} · "
        f"失败任务 {int(fulfillment.get('failed_jobs_count') or 0)}",
    ]

    return (
        "你是 VILTROX(唯卓仕)KOL 长期记忆分析师。"
        "基于以下纯聚合材料,写一段自然语言长期记忆 summary。\n\n"
        "聚合材料:\n" + "\n".join(material_lines) + "\n\n"
        "最近生命周期事件(已截断):\n" + timeline_text + "\n\n"
        "要求:用人话总结以下四个维度,串成一段连贯的长期记忆:\n"
        "1. 内容风格——TA 的创作方向、调性、制作水准。\n"
        "2. 推荐产品线——基于材料适合主推哪些镜头/产品方向。\n"
        "3. 风险——合作中需要留意的点(若材料无风险则明说无明显风险)。\n"
        "4. 合作履约——历史寄送/发布/失败的履约表现与可靠度。\n\n"
        "硬约束(不可违反):严禁输出任何打分/契合度数值;summary 独立于 V6 Fit;"
        "不得编造材料里没有的事实,材料缺失就说明该维度暂无足够信息。\n"
        "只返回 JSON:{\"summary_v2\": \"<自然语言长期记忆,涵盖上述四维>\", \"highlights\": [\"要点1\", \"要点2\"]}"
    )


def _parse_summary_v2(text: str) -> dict[str, Any]:
    """解析 LLM 文本:strip ``` 围栏 + json.loads;坏则把 raw text 当 summary_v2 文本(容错优先)。"""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned[4:] if cleaned.lower().lstrip().startswith("json") else cleaned
        cleaned = cleaned.strip()
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return {"summary": _text(text), "highlights": []}
    if not isinstance(parsed, dict):
        return {"summary": _text(text), "highlights": []}
    summary = _text(parsed.get("summary_v2") or parsed.get("summary"))
    highlights_raw = parsed.get("highlights")
    highlights = _dedup_strings(highlights_raw) if isinstance(highlights_raw, list) else []
    if not summary:
        summary = _text(text)
    return {"summary": summary, "highlights": highlights}


def build_kol_memory_snapshot_v2(kol_pool_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """v2:复用 v1 纯聚合 + 喂 LLM 产一段长期记忆 summary。

    完全复用 v1 build_kol_memory_snapshot 的聚合;status=='missing' 原样返回不调 LLM。
    预算 record-only:直接 invoke(gateway 内部 budget 仅 warn 不 raise),不传 skip_budget_check。
    绝不写 viltrox_fit_score;summary_v2 段显式不含任何 score / v6_fit 键。
    """

    kol_pool_id = int(kol_pool_id)
    built = build_kol_memory_snapshot(kol_pool_id)
    if built.get("status") == "missing":
        return built

    snapshot = dict(built["snapshot"])  # 浅拷贝,不改 v1 段
    resp = llm_gateway.invoke(
        _build_summary_v2_prompt(snapshot),
        purpose=V2_PURPOSE,
        max_output_tokens=V2_MAX_OUTPUT_TOKENS,
        cost_tag=V2_PURPOSE,
        staff=staff or {},
        metadata={"kol_pool_id": int(kol_pool_id), "method": MEMORY_METHOD_V2},
    )

    text = str(resp.get("text") or "").strip()
    provider = resp.get("provider")
    model = resp.get("model")
    fallback_used = bool(resp.get("fallback_used"))

    if str(resp.get("status") or "") != "success" or not text:
        summary_v2 = {
            "status": "fallback",
            "summary": "",
            "highlights": [],
            "provider": provider,
            "model": model,
            "fallback_used": fallback_used,
            "note": "llm_unavailable_or_fallback_rule_v0",
            "computed_at": _utcnow(),
        }
    else:
        parsed = _parse_summary_v2(text)
        summary_v2 = {
            "status": "ready",
            "summary": parsed["summary"],
            "highlights": parsed["highlights"],
            "provider": provider,
            "model": model,
            "fallback_used": fallback_used,
            "computed_at": _utcnow(),
        }

    # summary_v2 显式独立于 V6 Fit,绝不含任何 score 键。
    snapshot["summary_v2"] = summary_v2
    snapshot["note"] = "llm_summary_v2,no_v6_fit,no_score"

    return {
        "status": "ready",
        "kol_pool_id": kol_pool_id,
        "snapshot": _jsonable(snapshot),
        "source_counts": built["source_counts"],
        "computed_at": _utcnow(),
        "llm_calls": True,
        "note": "llm_summary_v2,no_v6_fit,no_score",
    }


def rebuild_kol_memory_snapshot_v2(kol_pool_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """v2 重建并持久化(按需触发,跑一次 LLM summary)。

    照抄 v1 rebuild 的 INSERT + viltrox_fit_score 守卫骨架;仅把 built 换成 v2 构建。
    守卫:before/after 对比,changed 非空则 rollback + raise(零写 viltrox_fit_score 审计)。
    summary_v2 仅落在现有 snapshot_json JSONB 内(additive,无需新表/列)。
    """

    kol_pool_id = int(kol_pool_id)
    built = build_kol_memory_snapshot_v2(kol_pool_id, staff=staff)
    if built.get("status") == "missing":
        return {
            "written": False,
            "snapshot_id": None,
            "kol_pool_id": kol_pool_id,
            "snapshot": built.get("snapshot"),
            "source_counts": built.get("source_counts"),
            "llm_calls": True,
            "method": MEMORY_METHOD_V2,
            "viltrox_fit_score_changed_ids": [],
            "computed_at": built.get("computed_at"),
            "status": "missing",
        }

    conn = get_conn()
    before_scores = _score_snapshot(conn, kol_pool_id)
    try:
        row = conn.execute(
            """
            INSERT INTO vkpi_kol_memory_snapshots
                (kol_pool_id, snapshot_json, source_counts, computed_at)
            VALUES (?, ?::jsonb, ?::jsonb, NOW())
            RETURNING id
            """,
            (
                kol_pool_id,
                json.dumps(built.get("snapshot") or {}, ensure_ascii=False, default=str),
                json.dumps(built.get("source_counts") or {}, ensure_ascii=False, default=str),
            ),
        ).fetchone()
        after_scores = _score_snapshot(conn, kol_pool_id)
        changed_ids = _changed_score_ids(before_scores, after_scores)
        if changed_ids:
            conn.rollback()
            raise RuntimeError(f"viltrox_fit_score changed unexpectedly: {changed_ids}; rolled back")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise

    snapshot_id = int(dict(row)["id"]) if row else None
    return {
        "written": snapshot_id is not None,
        "snapshot_id": snapshot_id,
        "kol_pool_id": kol_pool_id,
        "snapshot": built.get("snapshot"),
        "source_counts": built.get("source_counts"),
        "llm_calls": True,
        "method": MEMORY_METHOD_V2,
        "viltrox_fit_score_changed_ids": [],
        "computed_at": built.get("computed_at"),
        "status": "ready",
    }

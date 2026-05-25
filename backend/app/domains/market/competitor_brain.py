"""P8 deterministic competitor brain preview."""
from __future__ import annotations

import json
import secrets
import hashlib
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.domains import audit


from app.domains.market.competitor_brain_helpers import (
    FORBIDDEN_WRITE_FLAGS,
    REVIEW_STATUS_ACTIONS,
    SCENARIO,
    _actor_label,
    _aggregate,
    _content_brain_signals,
    _json,
    _loads,
    _lower,
    _normalize_brand,
    _risk_watch_signals,
    _safe_limit,
    _signal_score,
    _table_exists,
    _text,
    _utcnow,
    _voc_signals,
)


def build_competitor_brain_preview(
    *,
    limit: int = 20,
    json_out: str = "",
    md_out: str = "",
) -> dict[str, Any]:
    safe_limit = _safe_limit(limit)
    content_signals = _content_brain_signals(1000)
    voc_signals = _voc_signals()
    risk_watch_signals = _risk_watch_signals()
    all_signals = content_signals + voc_signals + risk_watch_signals
    competitors = _aggregate(all_signals, safe_limit)
    payload = {
        "scenario": SCENARIO,
        "provider_calls": False,
        "write_db": False,
        "summary": {
            "competitor_brands": len(competitors),
            "signals": len(all_signals),
            "content_signals": len(content_signals),
            "voc_signals": len(voc_signals),
            "risk_watch_signals": len(risk_watch_signals),
        },
        "competitors": competitors,
    }
    markdown = format_preview_summary(payload)
    payload["markdown"] = markdown
    if json_out:
        Path(json_out).write_text(json.dumps({k: v for k, v in payload.items() if k != "markdown"}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if md_out:
        Path(md_out).write_text(markdown, encoding="utf-8")
    return payload


def commit_competitor_signals(
    *,
    limit: int = 200,
    committed_by: str = "cli",
) -> dict[str, Any]:
    """Persist deterministic P8 preview signals for review."""
    safe_limit = _safe_limit(limit, default=200, ceiling=1000)
    content_signals = _content_brain_signals(1000)
    voc_signals = _voc_signals()
    risk_watch_signals = _risk_watch_signals()
    all_signals = (content_signals + voc_signals + risk_watch_signals)[:safe_limit]
    run_uid = f"p8sig-{secrets.token_hex(8)}"
    now = _utcnow()
    conn = get_conn()
    source_summary = {
        "scenario": SCENARIO,
        "provider_calls": False,
        "write_db": True,
        "content_signals": len(content_signals),
        "voc_signals": len(voc_signals),
        "risk_watch_signals": len(risk_watch_signals),
        "committed_signals": len(all_signals),
    }
    conn.execute(
        """
        INSERT INTO vkpi_competitor_signal_runs (
          run_uid, status, source_summary_json, signal_count,
          committed_by, created_at, committed_at
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (run_uid, "committed", _json(source_summary), len(all_signals), committed_by, now, now),
    )
    run_row = conn.execute("SELECT id FROM vkpi_competitor_signal_runs WHERE run_uid=?", (run_uid,)).fetchone()
    run_id = int(run_row["id"])
    inserted = 0
    for signal in all_signals:
        basis = _json(
            {
                "run_uid": run_uid,
                "brand": signal.get("brand"),
                "signal_type": signal.get("signal_type"),
                "source_table": signal.get("source_table"),
                "source_id": signal.get("source_id"),
                "source_sheet": signal.get("source_sheet"),
                "source_row": signal.get("source_row"),
                "detail": signal.get("detail"),
            }
        )
        signal_uid = f"p8sig-{hashlib.sha256(basis.encode('utf-8')).hexdigest()[:16]}"
        conn.execute(
            """
            INSERT INTO vkpi_competitor_signals (
              signal_uid, run_id, brand, normalized_brand, signal_type, severity, score,
              product_hints_json, source_table, source_id, source_sheet, source_row,
              source_url, platform, detail, evidence_json, review_status, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                signal_uid,
                run_id,
                _text(signal.get("brand")),
                _normalize_brand(signal.get("brand")),
                _text(signal.get("signal_type")),
                _text(signal.get("severity")) or "medium",
                _signal_score(signal),
                _json(signal.get("product_hints") or []),
                _text(signal.get("source_table")),
                signal.get("source_id"),
                _text(signal.get("source_sheet")),
                signal.get("source_row"),
                _text(signal.get("source_url")),
                _text(signal.get("platform")),
                _text(signal.get("detail")),
                _json(signal),
                "pending_review",
                now,
                now,
            ),
        )
        inserted += 1
    conn.commit()
    return {
        "scenario": "p8_competitor_signal_commit",
        "run_uid": run_uid,
        "run_id": run_id,
        "inserted_signals": inserted,
        "source_summary": source_summary,
        "provider_calls": False,
        "write_db": True,
    }


def get_competitor_brain_status() -> dict[str, Any]:
    """Return read-only P8 competitor signal coverage."""
    if not _table_exists("vkpi_competitor_signals"):
        return {
            "schema_ready": False,
            "run_count": 0,
            "signal_count": 0,
            "review_status_counts": {},
            "brand_distribution": {},
            "signal_type_distribution": {},
        }
    conn = get_conn()
    run_count = int(conn.execute("SELECT COUNT(*) AS count FROM vkpi_competitor_signal_runs").fetchone()["count"] or 0)
    signal_count = int(conn.execute("SELECT COUNT(*) AS count FROM vkpi_competitor_signals").fetchone()["count"] or 0)
    status_rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(review_status, ''), 'pending_review') AS status,
               COUNT(*) AS count
        FROM vkpi_competitor_signals
        GROUP BY COALESCE(NULLIF(review_status, ''), 'pending_review')
        ORDER BY count DESC, status
        """
    ).fetchall()
    brand_rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(normalized_brand, ''), brand) AS brand,
               COUNT(*) AS count
        FROM vkpi_competitor_signals
        GROUP BY COALESCE(NULLIF(normalized_brand, ''), brand)
        ORDER BY count DESC, brand
        LIMIT 20
        """
    ).fetchall()
    type_rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(signal_type, ''), 'unknown') AS signal_type,
               COUNT(*) AS count
        FROM vkpi_competitor_signals
        GROUP BY COALESCE(NULLIF(signal_type, ''), 'unknown')
        ORDER BY count DESC, signal_type
        LIMIT 20
        """
    ).fetchall()
    latest_run = conn.execute(
        """
        SELECT *
        FROM vkpi_competitor_signal_runs
        ORDER BY committed_at DESC, created_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    return {
        "schema_ready": True,
        "run_count": run_count,
        "signal_count": signal_count,
        "review_status_counts": {str(row["status"]): int(row["count"] or 0) for row in status_rows},
        "brand_distribution": {str(row["brand"]): int(row["count"] or 0) for row in brand_rows},
        "signal_type_distribution": {str(row["signal_type"]): int(row["count"] or 0) for row in type_rows},
        "latest_run": dict(latest_run) if latest_run else None,
    }


def list_competitor_signals(
    *,
    review_status: str = "",
    brand: str = "",
    signal_type: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """Return committed competitor signals for read-only review."""
    if not _table_exists("vkpi_competitor_signals"):
        return {"schema_ready": False, "signals": [], "count": 0}
    safe_limit = _safe_limit(limit, default=100, ceiling=500)
    where: list[str] = []
    params: list[Any] = []
    if review_status:
        where.append("COALESCE(NULLIF(s.review_status, ''), 'pending_review')=?")
        params.append(review_status)
    if brand:
        where.append("(LOWER(s.normalized_brand)=LOWER(?) OR LOWER(s.brand)=LOWER(?))")
        params.extend([brand, brand])
    if signal_type:
        where.append("LOWER(s.signal_type)=LOWER(?)")
        params.append(signal_type)
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    rows = get_conn().execute(
        f"""
        SELECT s.*, r.run_uid, r.status AS run_status, r.committed_at
        FROM vkpi_competitor_signals s
        LEFT JOIN vkpi_competitor_signal_runs r ON r.id = s.run_id
        {where_sql}
        ORDER BY
          CASE s.review_status WHEN 'pending_review' THEN 0 ELSE 1 END,
          s.score DESC,
          s.created_at DESC,
          s.id DESC
        LIMIT ?
        """,
        (*params, safe_limit),
    ).fetchall()
    signals = []
    for row in rows:
        data = dict(row)
        data["product_hints"] = _loads(data.get("product_hints_json"), [])
        data["evidence"] = _loads(data.get("evidence_json"), {})
        signals.append(data)
    return {"schema_ready": True, "signals": signals, "count": len(signals)}


def _review_suggestion_for_signal(signal: dict[str, Any]) -> dict[str, Any]:
    brand = _text(signal.get("normalized_brand") or signal.get("brand"))
    signal_type = _text(signal.get("signal_type"))
    severity = _lower(signal.get("severity") or "medium")
    score = float(signal.get("score") or 0)
    product_hints = signal.get("product_hints")
    if not isinstance(product_hints, list):
        product_hints = _loads(signal.get("product_hints_json"), [])
    evidence = signal.get("evidence")
    if not isinstance(evidence, dict):
        evidence = _loads(signal.get("evidence_json"), {})
    source_table = _text(signal.get("source_table"))
    source_id = signal.get("source_id")
    detail = _text((evidence or {}).get("detail") or signal.get("detail"))
    reasons: list[str] = []

    if not brand or brand == "unknown":
        reasons.append("missing_brand")
        return {"suggested_action": "pending_review", "confidence": 0.35, "reasons": reasons}
    if not source_table and not source_id:
        reasons.append("missing_source")
        return {"suggested_action": "pending_review", "confidence": 0.4, "reasons": reasons}
    if not detail:
        reasons.append("missing_evidence_detail")
        return {"suggested_action": "pending_review", "confidence": 0.45, "reasons": reasons}

    if severity in {"critical", "danger", "high"}:
        reasons.append(f"severity:{severity}")
        return {"suggested_action": "ready", "confidence": 0.9, "reasons": reasons}
    if signal_type in {"competitor_focus", "pricing_sensitive", "product_comparison", "voc_issue", "risk_watch"}:
        reasons.append(f"signal_type:{signal_type}")
        if product_hints:
            reasons.append("has_product_hints")
        return {"suggested_action": "ready", "confidence": 0.82 if product_hints else 0.72, "reasons": reasons}
    if score >= 25:
        reasons.append(f"score:{score:g}")
        return {"suggested_action": "ready", "confidence": 0.75, "reasons": reasons}
    if signal_type == "competitor_mention" and not product_hints and score <= 20:
        reasons.append("generic_competitor_mention_without_product")
        return {"suggested_action": "ignored", "confidence": 0.68, "reasons": reasons}

    reasons.append("valid_but_low_context")
    return {"suggested_action": "pending_review", "confidence": 0.55, "reasons": reasons}


def build_competitor_signal_review_suggestions(
    *,
    review_status: str = "pending_review",
    limit: int = 100,
) -> dict[str, Any]:
    """Return deterministic review suggestions without mutating competitor signals."""
    if not _table_exists("vkpi_competitor_signals"):
        return {
            "scenario": "p8_competitor_signal_review_suggestions",
            "schema_ready": False,
            "provider_calls": False,
            "write_db": False,
            "suggestions": [],
            "count": 0,
            "suggested_actions": {},
        }
    result = list_competitor_signals(review_status=review_status, limit=limit)
    suggestions: list[dict[str, Any]] = []
    action_counts: Counter[str] = Counter()
    brand_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    for signal in result.get("signals") or []:
        suggestion = _review_suggestion_for_signal(signal)
        action = str(suggestion.get("suggested_action") or "pending_review")
        action_counts[action] += 1
        brand = _text(signal.get("normalized_brand") or signal.get("brand") or "unknown")
        signal_type = _text(signal.get("signal_type") or "unknown")
        brand_counts[brand] += 1
        type_counts[signal_type] += 1
        suggestions.append(
            {
                "signal_id": signal.get("id"),
                "signal_uid": signal.get("signal_uid"),
                "brand": brand,
                "signal_type": signal_type,
                "severity": signal.get("severity"),
                "score": signal.get("score"),
                "review_status": signal.get("review_status") or "pending_review",
                "suggested_action": action,
                "confidence": suggestion.get("confidence"),
                "reasons": suggestion.get("reasons") or [],
                "source_table": signal.get("source_table"),
                "source_id": signal.get("source_id"),
                "source_row": signal.get("source_row"),
                "detail": _text((signal.get("evidence") or {}).get("detail") or signal.get("detail"))[:240],
            }
        )
    suggestions.sort(
        key=lambda item: (
            {"ready": 0, "pending_review": 1, "ignored": 2, "rejected": 3}.get(str(item.get("suggested_action")), 9),
            -float(item.get("confidence") or 0),
            -float(item.get("score") or 0),
            str(item.get("brand") or ""),
        )
    )
    return {
        "scenario": "p8_competitor_signal_review_suggestions",
        "schema_ready": True,
        "provider_calls": False,
        "write_db": False,
        "filters": {"review_status": review_status, "limit": _safe_limit(limit, default=100, ceiling=500)},
        "count": len(suggestions),
        "suggested_actions": dict(action_counts),
        "brand_distribution": dict(brand_counts),
        "signal_type_distribution": dict(type_counts),
        "suggestions": suggestions,
    }


def format_review_suggestions(payload: dict[str, Any]) -> str:
    lines = [
        "# P8 Competitor Signal Review Suggestions",
        "",
        "```text",
        f"scenario={payload.get('scenario', '')}",
        f"provider_calls={str(bool(payload.get('provider_calls'))).lower()}",
        f"write_db={str(bool(payload.get('write_db'))).lower()}",
        f"count={int(payload.get('count') or 0)}",
    ]
    for action, count in sorted((payload.get("suggested_actions") or {}).items()):
        lines.append(f"suggested.{action}={int(count or 0)}")
    lines.extend(["```", "", "## Suggestions", ""])
    for item in payload.get("suggestions") or []:
        lines.append(
            f"- signal_id={item.get('signal_id')} action={item.get('suggested_action')} "
            f"confidence={float(item.get('confidence') or 0):.2f} "
            f"brand={item.get('brand')} type={item.get('signal_type')} "
            f"reasons={','.join(item.get('reasons') or [])}"
        )
    if not payload.get("suggestions"):
        lines.append("- none")
    return "\n".join(lines).rstrip() + "\n"


def apply_competitor_signal_review_suggestions(
    *,
    review_status: str = "pending_review",
    suggested_action: str = "ready",
    limit: int = 100,
    staff: dict[str, Any] | None = None,
    actor: str = "cli",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Apply or preview deterministic review suggestions."""
    suggestion_payload = build_competitor_signal_review_suggestions(review_status=review_status, limit=limit)
    clean_action = _lower(suggested_action or "ready")
    target_status = REVIEW_STATUS_ACTIONS.get(clean_action)
    if not target_status:
        raise ValueError("suggested_action must map to ready, rejected, ignored, or pending_review")
    candidates = [
        item
        for item in suggestion_payload.get("suggestions") or []
        if str(item.get("suggested_action") or "") == target_status
    ]
    applied: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for item in candidates:
        signal_id = int(item.get("signal_id") or 0)
        if not signal_id:
            continue
        try:
            result = review_competitor_signal(
                signal_id,
                action=target_status,
                note=f"bulk_suggestion:{','.join(item.get('reasons') or [])}",
                staff=staff,
                actor=actor,
                dry_run=dry_run,
            )
            applied.append(
                {
                    "signal_id": signal_id,
                    "brand": item.get("brand"),
                    "signal_type": item.get("signal_type"),
                    "suggested_action": target_status,
                    "confidence": item.get("confidence"),
                    "dry_run": result.get("dry_run"),
                    "write_db": result.get("write_db"),
                }
            )
        except Exception as exc:
            errors.append({"signal_id": signal_id, "error": str(exc)})
    return {
        "scenario": "p8_competitor_signal_apply_suggestions",
        "provider_calls": False,
        "write_db": not bool(dry_run),
        "dry_run": bool(dry_run),
        "filters": {
            "review_status": review_status,
            "suggested_action": target_status,
            "limit": _safe_limit(limit, default=100, ceiling=500),
        },
        "candidate_count": len(candidates),
        "applied_count": len(applied),
        "error_count": len(errors),
        "applied": applied,
        "errors": errors,
    }


def format_apply_suggestions(payload: dict[str, Any]) -> str:
    lines = [
        "# P8 Competitor Signal Apply Suggestions",
        "",
        "```text",
        f"scenario={payload.get('scenario', '')}",
        f"provider_calls={str(bool(payload.get('provider_calls'))).lower()}",
        f"write_db={str(bool(payload.get('write_db'))).lower()}",
        f"dry_run={str(bool(payload.get('dry_run'))).lower()}",
        f"candidate_count={int(payload.get('candidate_count') or 0)}",
        f"applied_count={int(payload.get('applied_count') or 0)}",
        f"error_count={int(payload.get('error_count') or 0)}",
        "```",
        "",
        "## Applied",
        "",
    ]
    for item in payload.get("applied") or []:
        lines.append(
            f"- signal_id={item.get('signal_id')} action={item.get('suggested_action')} "
            f"brand={item.get('brand')} type={item.get('signal_type')} "
            f"dry_run={str(bool(item.get('dry_run'))).lower()}"
        )
    if not payload.get("applied"):
        lines.append("- none")
    if payload.get("errors"):
        lines.extend(["", "## Errors", ""])
        for item in payload.get("errors") or []:
            lines.append(f"- signal_id={item.get('signal_id')} error={item.get('error')}")
    return "\n".join(lines).rstrip() + "\n"


def review_competitor_signal(
    signal_id: int,
    *,
    action: str,
    note: str = "",
    staff: dict[str, Any] | None = None,
    actor: str = "cli",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Mark a committed competitor signal as ready/rejected/ignored without changing source rows."""
    if not _table_exists("vkpi_competitor_signals"):
        raise LookupError("competitor signal table not found")
    clean_action = _lower(action)
    next_status = REVIEW_STATUS_ACTIONS.get(clean_action)
    if not next_status:
        raise ValueError("action must be one of ready, approve, reject, ignore, pending_review")
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_competitor_signals WHERE id=?", (int(signal_id),)).fetchone()
    if not row:
        raise LookupError("competitor signal not found")
    data = dict(row)
    previous_status = _text(data.get("review_status")) or "pending_review"
    now = _utcnow()
    reviewer = _actor_label(staff, fallback=actor or "cli")
    decision = {
        "status": next_status,
        "action": clean_action,
        "note": _text(note),
        "reviewed_at": now,
        "reviewed_by": reviewer,
        "previous_status": previous_status,
    }
    result = {
        "id": int(signal_id),
        "signal_uid": data.get("signal_uid"),
        "brand": data.get("normalized_brand") or data.get("brand"),
        "previous_status": previous_status,
        "review_status": next_status,
        "dry_run": bool(dry_run),
        "write_db": not bool(dry_run),
        "provider_calls": False,
        "decision": decision,
    }
    if dry_run:
        return result

    evidence = _loads(data.get("evidence_json"), {})
    if not isinstance(evidence, dict):
        evidence = {"original_evidence": evidence}
    history = evidence.get("review_history")
    if not isinstance(history, list):
        history = []
    history.append(decision)
    evidence["review_decision"] = decision
    evidence["review_history"] = history[-20:]
    conn.execute(
        """
        UPDATE vkpi_competitor_signals
        SET review_status=?, evidence_json=?, updated_at=?
        WHERE id=?
        """,
        (next_status, _json(evidence), now, int(signal_id)),
    )
    conn.commit()
    audit.log_business_event(
        staff_id=resolve_staff_id(staff) or 0,
        action_type="competitor_signal_review",
        target_type="competitor_signal",
        target_id=int(signal_id),
        detail=f"{previous_status} -> {next_status}",
        metadata=decision,
    )
    return result


def format_preview_summary(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# P8 Competitor Brain Preview",
        "",
        "```text",
        f"scenario={payload.get('scenario', '')}",
        f"provider_calls={str(bool(payload.get('provider_calls'))).lower()}",
        f"write_db={str(bool(payload.get('write_db'))).lower()}",
        f"competitor_brands={int(summary.get('competitor_brands') or 0)}",
        f"signals={int(summary.get('signals') or 0)}",
        f"content_signals={int(summary.get('content_signals') or 0)}",
        f"voc_signals={int(summary.get('voc_signals') or 0)}",
        f"risk_watch_signals={int(summary.get('risk_watch_signals') or 0)}",
        "```",
        "",
        "## Top Competitors",
        "",
    ]
    for idx, item in enumerate(payload.get("competitors") or [], start=1):
        lines.extend(
            [
                f"### {idx}. {item.get('brand', '')} score={item.get('score', 0)}",
                "",
                f"- signals={item.get('signal_count', 0)} risk_count={item.get('risk_count', 0)}",
                f"- product_hints={', '.join(item.get('product_hints') or []) or '-'}",
                f"- signal_types={json.dumps(item.get('top_signal_types') or {}, ensure_ascii=False)}",
                "- evidence:",
            ]
        )
        for evidence in (item.get("evidence") or [])[:5]:
            source = evidence.get("source_table") or evidence.get("source_sheet") or ""
            source_id = evidence.get("source_id") or evidence.get("source_row") or ""
            lines.append(
                f"  - {evidence.get('signal_type', '')} source={source}:{source_id} "
                f"detail={_text(evidence.get('detail'))[:160]}"
            )
        lines.append("")
    if not payload.get("competitors"):
        lines.append("No competitor signals found.")
    return "\n".join(lines).rstrip() + "\n"

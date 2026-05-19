"""P4 deterministic new-launch match dry-run.

P4-1 reads raw Memory rows directly. It does not call P3 candidate helpers,
does not call providers, and does not write recommendation tables. P4-2 may
attach budget-gated recommendation reasons after ranking without changing the
deterministic score.
"""
from __future__ import annotations

import json
import math
import re
import secrets
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi import memory
from app.services.vkpi import llm_gateway
from app.services.vkpi.budget_guard import check_budget, get_budget_status


BUDGET_SCOPE = "cron:p4_recommendations_daily"
REASON_BUDGET_SCOPE = "cron:p4_recommendation_reasons"
SCENARIO = "new_launch_match"
FORBIDDEN_WRITE_FLAGS = {"--commit", "--write-db", "--provider-call"}


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row.items()) if hasattr(row, "items") else dict(row)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _load_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
    except Exception:
        return default
    return parsed


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_date(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _within_days(value: Any, days: int, *, now: datetime) -> bool:
    parsed = _parse_date(value)
    if not parsed:
        return False
    return abs((now - parsed).days) <= int(days)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_limit(limit: int) -> int:
    return max(1, min(500, int(limit or 100)))


def _split_csv(value: str) -> set[str]:
    return {_country_key(part) for part in str(value or "").split(",") if _country_key(part)}


_COUNTRY_ALIASES = {
    "美国": "united states",
    "usa": "united states",
    "us": "united states",
    "u.s.": "united states",
    "英国": "united kingdom",
    "uk": "united kingdom",
    "u.k.": "united kingdom",
    "德国": "germany",
    "日本": "japan",
    "法国": "france",
    "加拿大": "canada",
    "澳大利亚": "australia",
    "韩国": "south korea",
    "意大利": "italy",
    "西班牙": "spain",
    "荷兰": "netherlands",
    "波兰": "poland",
    "印度": "india",
    "巴西": "brazil",
    "墨西哥": "mexico",
}


def _country_key(value: Any) -> str:
    key = re.sub(r"\s+", " ", _lower(value))
    return _COUNTRY_ALIASES.get(key, key)


def _family_tokens(value: str) -> dict[str, Any]:
    text = _lower(value).replace("/", " ")
    focal = ""
    aperture = ""
    series: set[str] = set()
    mount: set[str] = set()
    focal_match = re.search(r"(\d{2,3})\s*mm", text)
    if not focal_match:
        focal_match = re.search(r"\b(\d{2,3})\b", text)
    if focal_match:
        focal = f"{focal_match.group(1)}mm"
    aperture_match = re.search(r"f\s*(\d+(?:\.\d+)?)", text)
    if aperture_match:
        aperture = f"f{aperture_match.group(1)}"
    for token in ("lab", "air", "evo", "pro", "lite"):
        if token in text:
            series.add(token)
    for token in ("fe", "e", "z", "xf", "x", "m43", "pl", "ef", "rf"):
        if re.search(rf"\b{re.escape(token)}\b", text):
            mount.add(token)
    return {"focal": focal, "aperture": aperture, "series": series, "mount": mount}


def _clean_product_query(query: str) -> str:
    return re.sub(r"\s+", " ", _lower(query)).strip()


def _source_fields(row: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    default_table = "vkpi_memory_links" if row.get("link_type") or row.get("link_uid") else "computed:p4_new_launch_match"
    return {
        "source_table": row.get("source_table") or default_table,
        "source_id": str(row.get("source_id") or row.get("id") or payload.get("source_id") or ""),
        "source_ref": row.get("source_ref") or payload.get("source_ref") or "p4_new_launch_match:computed",
        "source_sheet": payload.get("source_sheet") or "",
        "source_row": payload.get("source_row") or "",
        "confidence_score": _safe_float(row.get("confidence_score"), 1.0),
    }


def _evidence(
    *,
    evidence_type: str,
    polarity: str,
    severity: str,
    detail: str,
    score_component: str,
    row: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = row or {}
    base = _source_fields(row, payload)
    return {
        "type": evidence_type,
        "polarity": polarity,
        "severity": severity,
        "detail": detail,
        "score_component": score_component,
        **base,
    }


def _fact_payload(row: dict[str, Any]) -> dict[str, Any]:
    fact = _load_json(row.get("fact_json") or "{}", {})
    return fact if isinstance(fact, dict) else {}


def _source_payload(row: dict[str, Any]) -> dict[str, Any]:
    source = _load_json(row.get("source_json") or "{}", {})
    return source if isinstance(source, dict) else {}


def _entity_payload(row: dict[str, Any], key: str) -> dict[str, Any]:
    payload = _load_json(row.get(key) or "{}", {})
    return payload if isinstance(payload, dict) else {}


def _select_target_family(product_query: str) -> dict[str, Any]:
    query = _clean_product_query(product_query)
    if not query:
        raise ValueError("product query is required")
    conn = get_conn()
    like = f"%{query}%"
    exact = conn.execute(
        """
        SELECT *
        FROM vkpi_memory_entities
        WHERE entity_type='product_family'
          AND (lower(display_name)=? OR lower(identity_key)=?)
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (query, query),
    ).fetchone()
    if exact:
        return _row_to_dict(exact)

    family = conn.execute(
        """
        SELECT f.*,
               COUNT(DISTINCT nl.source_entity_id) AS member_count,
               COUNT(w.id) AS cooperation_count
        FROM vkpi_memory_entities f
        LEFT JOIN vkpi_memory_links nl
          ON nl.target_entity_id=f.id
         AND nl.link_type='normalized_to_product_family'
        LEFT JOIN vkpi_memory_links w
          ON w.target_entity_id=nl.source_entity_id
         AND w.link_type='worked_on_product'
        WHERE f.entity_type='product_family'
          AND (lower(f.display_name) LIKE ? OR lower(f.identity_key) LIKE ?)
        GROUP BY f.id
        ORDER BY cooperation_count DESC, member_count DESC, f.display_name
        LIMIT 1
        """,
        (like, like),
    ).fetchone()
    if family:
        return _row_to_dict(family)

    product = conn.execute(
        """
        SELECT f.*
        FROM vkpi_memory_entities p
        JOIN vkpi_memory_links nl
          ON nl.source_entity_id=p.id
         AND nl.link_type='normalized_to_product_family'
        JOIN vkpi_memory_entities f ON f.id=nl.target_entity_id
        WHERE p.entity_type='product'
          AND (lower(p.display_name) LIKE ? OR lower(p.identity_key) LIKE ?)
        ORDER BY p.updated_at DESC, p.id DESC
        LIMIT 1
        """,
        (like, like),
    ).fetchone()
    if product:
        return _row_to_dict(product)
    raise ValueError(f"no product_family found for query: {product_query}")


def _product_family_maps() -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    conn = get_conn()
    product_to_family: dict[int, dict[str, Any]] = {}
    family_by_id: dict[int, dict[str, Any]] = {}
    rows = conn.execute(
        """
        SELECT p.id AS product_id,
               p.entity_uid AS product_uid,
               p.display_name AS product_name,
               p.identity_key AS product_key,
               p.metadata_json AS product_metadata_json,
               f.id AS family_id,
               f.entity_uid AS family_uid,
               f.display_name AS family_name,
               f.identity_key AS family_key,
               f.metadata_json AS family_metadata_json
        FROM vkpi_memory_links nl
        JOIN vkpi_memory_entities p ON p.id=nl.source_entity_id
        JOIN vkpi_memory_entities f ON f.id=nl.target_entity_id
        WHERE nl.link_type='normalized_to_product_family'
          AND p.entity_type='product'
          AND f.entity_type='product_family'
        """
    ).fetchall()
    for raw in rows:
        row = _row_to_dict(raw)
        product_to_family[int(row["product_id"])] = row
        family_by_id[int(row["family_id"])] = {
            "id": int(row["family_id"]),
            "entity_uid": row["family_uid"],
            "display_name": row["family_name"],
            "identity_key": row["family_key"],
            "metadata_json": row["family_metadata_json"],
        }
    return product_to_family, family_by_id


def _kol_entities() -> list[dict[str, Any]]:
    return [
        _row_to_dict(row)
        for row in get_conn().execute(
        """
        SELECT *
        FROM vkpi_memory_entities
        WHERE entity_type='kol'
          AND status IN ('active', 'imported', 'needs_human_review')
        ORDER BY id
        """
        ).fetchall()
    ]


def _pool_by_source_ref() -> dict[str, dict[str, Any]]:
    rows = get_conn().execute(
        """
        SELECT id, platform, handle, display_name, country, source_ref,
               sync_status, raw_platform_data
        FROM vkpi_kol_pool
        WHERE source_type='legacy_excel_p2d'
        """
    ).fetchall()
    return {_text(row["source_ref"]): _row_to_dict(row) for row in rows}


def _legacy_entities_by_uid() -> dict[str, dict[str, Any]]:
    rows = get_conn().execute(
        """
        SELECT id, entity_uid, weak_label, resolution_decision
        FROM vkpi_legacy_kol_entities
        """
    ).fetchall()
    return {_text(row["entity_uid"]): _row_to_dict(row) for row in rows}


def _kol_facts() -> dict[int, list[dict[str, Any]]]:
    rows = get_conn().execute(
        """
        SELECT *
        FROM vkpi_memory_facts
        WHERE fact_type IN (
          'contact_status',
          'risk_flag',
          'sync_status',
          'weak_label',
          'country',
          'review_state',
          'evidence_count'
        )
        ORDER BY observed_at DESC, id DESC
        """
    ).fetchall()
    facts: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        item = _row_to_dict(row)
        facts[int(item["entity_id"])].append(item)
    return facts


def _worked_links() -> dict[int, list[dict[str, Any]]]:
    rows = get_conn().execute(
        """
        SELECT l.*,
               p.entity_uid AS product_uid,
               p.display_name AS product_name,
               p.identity_key AS product_key
        FROM vkpi_memory_links l
        JOIN vkpi_memory_entities p ON p.id=l.target_entity_id
        WHERE l.link_type='worked_on_product'
        """
    ).fetchall()
    links: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        item = _row_to_dict(row)
        links[int(item["source_entity_id"])].append(item)
    return links


def _target_market_signals(target_family_id: int) -> list[dict[str, Any]]:
    rows = get_conn().execute(
        """
        SELECT *
        FROM vkpi_memory_facts
        WHERE entity_id=?
          AND fact_type IN ('market_signal', 'launch_plan')
        ORDER BY observed_at DESC, id DESC
        """,
        (int(target_family_id),),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _latest_fact_value(facts: list[dict[str, Any]], fact_type: str) -> str:
    for fact in facts:
        if _text(fact.get("fact_type")) == fact_type:
            return _text(fact.get("fact_value_text"))
    return ""


def _first_fact(facts: list[dict[str, Any]], fact_type: str) -> dict[str, Any]:
    for fact in facts:
        if _text(fact.get("fact_type")) == fact_type:
            return fact
    return {}


def _evidence_count(facts: list[dict[str, Any]]) -> int:
    total = 0
    for fact in facts:
        if _text(fact.get("fact_type")) != "evidence_count":
            continue
        payload = _fact_payload(fact)
        total += _safe_int(payload.get("count"), _safe_int(fact.get("fact_value_text")))
    return total


def _risk_count(facts: list[dict[str, Any]]) -> int:
    return sum(1 for fact in facts if _text(fact.get("fact_type")) == "risk_flag")


def _contact_score(contact_status: str, pool: dict[str, Any] | None) -> tuple[int, str]:
    raw = _load_json((pool or {}).get("raw_platform_data") or "{}", {})
    has_email = bool(raw.get("contact_has_email")) if isinstance(raw, dict) else False
    has_phone = bool(raw.get("contact_has_phone")) if isinstance(raw, dict) else False
    if has_email and has_phone:
        return 10, "email_and_phone_available_restricted"
    if has_email:
        return 7, "email_available_restricted"
    normalized = _lower(contact_status)
    if normalized in {"available_restricted", "dm_only"}:
        return 4, normalized
    if normalized == "missing":
        return 0, "missing"
    return 2, normalized or "unknown"


def _cooperation_score(count: int) -> int:
    if count >= 10:
        return 15
    if count >= 5:
        return 10
    if count >= 2:
        return 6
    if count == 1:
        return 3
    return 0


def _freshness_score(count: int) -> int:
    if count >= 10:
        return 5
    if count >= 5:
        return 3
    if count > 0:
        return 1
    return 0


def _region_score(country: str, primary: set[str], secondary: set[str]) -> tuple[int, str]:
    key = _country_key(country)
    if not key:
        return 5, "missing_country"
    if primary and key in primary:
        return 10, "primary_target_market"
    if secondary and key in secondary:
        return 6, "secondary_target_market"
    if not primary and not secondary:
        return 3, "known_country_no_target_market"
    return 3, "other_known_market"


def _product_match_score(
    *,
    target_family: dict[str, Any],
    links: list[dict[str, Any]],
    product_to_family: dict[int, dict[str, Any]],
) -> tuple[int, str, dict[str, Any] | None]:
    target_family_id = int(target_family["id"])
    target_tokens = _family_tokens(_text(target_family.get("display_name") or target_family.get("identity_key")))
    best: tuple[int, str, dict[str, Any] | None] = (0, "no_product_family_match", None)
    for link in links:
        family = product_to_family.get(int(link["target_entity_id"]))
        if not family:
            continue
        if int(family["family_id"]) == target_family_id:
            return 25, "direct_family_match", link
        tokens = _family_tokens(_text(family.get("family_name") or family.get("family_key")))
        same_focal = bool(target_tokens["focal"] and target_tokens["focal"] == tokens["focal"])
        shared_mount = bool(target_tokens["mount"] and tokens["mount"] and target_tokens["mount"] & tokens["mount"])
        if same_focal or shared_mount:
            if best[0] < 15:
                best = (15, "adjacent_family_match", link)
            continue
        if target_tokens["focal"] or tokens["focal"]:
            if best[0] < 10:
                best = (10, "same_product_type", link)
    return best


def _market_signal_score(signals: list[dict[str, Any]], *, now: datetime) -> tuple[int, list[dict[str, Any]]]:
    score = 0
    evidence_rows: list[dict[str, Any]] = []
    launch = None
    official = None
    for signal in signals:
        payload = _fact_payload(signal)
        signal_type = _text(payload.get("signal_type") or signal.get("fact_type"))
        date_value = payload.get("signal_date") or payload.get("launch_date") or payload.get("publish_date")
        if signal_type == "launch_plan" and launch is None and _within_days(date_value, 30, now=now):
            launch = signal
        if signal_type in {"official_content", "official_material"} and official is None and _within_days(date_value, 90, now=now):
            official = signal
        if launch and official:
            break
    if launch:
        score += 10
        evidence_rows.append(launch)
    if official:
        score += 5
        evidence_rows.append(official)
    return score, evidence_rows


def _percentile(index: int, total: int) -> float:
    if total <= 1:
        return 100.0
    return round(100 * (total - 1 - index) / (total - 1), 1)


def _median_score(items: list[dict[str, Any]]) -> float:
    if not items:
        return 0.0
    scores = sorted(float(item["score"]) for item in items)
    mid = len(scores) // 2
    if len(scores) % 2:
        return scores[mid]
    return round((scores[mid - 1] + scores[mid]) / 2, 1)


def _distribution(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "p90_plus": sum(1 for item in items if int(item["percentile_rank"]) >= 90),
        "p75_to_p90": sum(1 for item in items if 75 <= int(item["percentile_rank"]) < 90),
        "p50_to_p75": sum(1 for item in items if 50 <= int(item["percentile_rank"]) < 75),
        "below_p50": sum(1 for item in items if int(item["percentile_rank"]) < 50),
    }


def _open_link(entity_uid: str) -> str:
    return f"/kol/{entity_uid}"


def _json_write(path: str, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")


def _markdown_write(path: str, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(payload), encoding="utf-8")


def _market_detail(signal: dict[str, Any]) -> str:
    payload = _fact_payload(signal)
    signal_type = payload.get("signal_type") or signal.get("fact_type")
    date_value = payload.get("signal_date") or payload.get("launch_date") or payload.get("publish_date") or ""
    product = payload.get("product_name") or payload.get("product") or payload.get("launch_name") or ""
    return f"{signal_type} {product} {date_value}".strip()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _last_by_uid(table: str, column: str, uid: str) -> dict[str, Any]:
    row = get_conn().execute(f"SELECT * FROM {table} WHERE {column}=?", (uid,)).fetchone()
    return _row_to_dict(row) if row else {}


def _persist_preview_run(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") or {}
    items = payload.get("items") or []
    now = _utcnow()
    run_uid = f"p4nlm-{secrets.token_hex(8)}"
    conn = get_conn()
    filters = {
        "scenario": SCENARIO,
        "source_mode": "new_launch_match_preview",
        "dry_run": True,
        "product_query": payload.get("product_query"),
        "target_family_uid": payload.get("target_family_uid"),
        "target_family_name": payload.get("target_family_name"),
        "llm_reasons_requested": bool(summary.get("llm_reasons_requested")),
        "reason_count": int(summary.get("reasons_attached") or 0),
    }
    try:
        conn.execute(
            """
            INSERT INTO vkpi_kol_recommendation_runs
                (run_uid, launch_id, strategy_version, status, candidate_count, recommendation_count,
                 filters_json, created_by_staff_id, created_at, completed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_uid,
                None,
                "new_launch_match_v1",
                "previewed",
                int(summary.get("total_candidates_evaluated") or 0),
                len(items),
                _json(filters),
                None,
                now,
                now,
            ),
        )
        run = _last_by_uid("vkpi_kol_recommendation_runs", "run_uid", run_uid)
        run_id = int(run.get("id") or 0)
        recommendation_ids: list[int] = []
        for item in items:
            rec_uid = f"p4nlm-rec-{secrets.token_hex(8)}"
            reason = item.get("recommendation_reason") or {}
            feature_snapshot = {
                "scenario": SCENARIO,
                "product_query": payload.get("product_query"),
                "target_family_uid": payload.get("target_family_uid"),
                "target_family_name": payload.get("target_family_name"),
                "kol_entity_uid": item.get("kol_entity_uid"),
                "legacy_entity_uid": item.get("legacy_entity_uid"),
                "review_required": bool(item.get("review_required")),
                "links": item.get("links") or {},
            }
            explanation = {
                "evidence_pro": item.get("evidence_pro") or [],
                "evidence_con": item.get("evidence_con") or [],
                "recommendation_reason": reason,
                "source": "p4_new_launch_match",
            }
            conn.execute(
                """
                INSERT INTO vkpi_kol_recommendations
                    (recommendation_uid, run_id, launch_id, kol_pool_id, linked_main_kol_id, platform, handle,
                     display_name, score, rank, status, feature_snapshot_json, scoring_breakdown_json,
                     explanation_json, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec_uid,
                    run_id,
                    None,
                    item.get("kol_pool_id"),
                    None,
                    item.get("platform") or "",
                    item.get("handle") or "",
                    item.get("display_name") or item.get("handle") or "",
                    item.get("score"),
                    item.get("rank"),
                    "previewed",
                    _json(feature_snapshot),
                    _json(item.get("score_breakdown") or {}),
                    _json(explanation),
                    now,
                    now,
                ),
            )
            rec = _last_by_uid("vkpi_kol_recommendations", "recommendation_uid", rec_uid)
            rec_id = int(rec.get("id") or 0)
            recommendation_ids.append(rec_id)
            conn.execute(
                """
                INSERT INTO vkpi_recommendation_explanations
                    (recommendation_id, explanation_type, explanation_text, strengths_json, concerns_json,
                     model_version, created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    rec_id,
                    "p4_new_launch_match",
                    reason.get("short_reason") or "Explainable new-launch match preview.",
                    _json(item.get("evidence_pro") or []),
                    _json(item.get("evidence_con") or []),
                    reason.get("model") or "rule_v1",
                    now,
                ),
            )
        conn.commit()
        return {
            "enabled": True,
            "run_uid": run_uid,
            "run_id": run_id,
            "recommendation_count": len(recommendation_ids),
            "recommendation_ids": recommendation_ids,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def _deterministic_reason(item: dict[str, Any]) -> dict[str, str]:
    pro = item.get("evidence_pro") or []
    con = item.get("evidence_con") or []
    strongest = pro[0].get("detail") if pro else "Has usable Memory evidence for this launch"
    concern = con[0].get("detail") if con else "No major concern in current Memory"
    handle = item.get("handle") or item.get("display_name") or item.get("kol_entity_uid")
    return {
        "short_reason": f"{handle} is a candidate because {strongest}.",
        "pitch_angle": "Use the historical product-family evidence as the first outreach angle.",
        "caution_note": concern,
    }


def _reason_prompt(payload: dict[str, Any], item: dict[str, Any]) -> str:
    pro = [
        {"type": row.get("type"), "detail": row.get("detail"), "source_ref": row.get("source_ref")}
        for row in (item.get("evidence_pro") or [])[:5]
    ]
    con = [
        {"type": row.get("type"), "detail": row.get("detail"), "severity": row.get("severity")}
        for row in (item.get("evidence_con") or [])[:5]
    ]
    compact = {
        "scenario": SCENARIO,
        "product": payload.get("product_query"),
        "target_family": payload.get("target_family_name"),
        "kol": {
            "platform": item.get("platform"),
            "handle": item.get("handle"),
            "display_name": item.get("display_name"),
            "country": item.get("country"),
            "score": item.get("score"),
            "review_required": item.get("review_required"),
        },
        "score_breakdown": item.get("score_breakdown"),
        "evidence_pro": pro,
        "evidence_con": con,
    }
    return (
        "Write a concise V-KPI recommendation reason as strict JSON with keys "
        "short_reason, pitch_angle, caution_note. Do not invent facts. "
        "Use only the evidence below. No markdown.\n\n"
        + json.dumps(compact, ensure_ascii=False, default=_json_default)
    )


def _parse_reason_text(text: str) -> dict[str, str] | None:
    raw = _text(text)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return None
    if not isinstance(parsed, dict):
        return None
    return {
        "short_reason": _text(parsed.get("short_reason")),
        "pitch_angle": _text(parsed.get("pitch_angle")),
        "caution_note": _text(parsed.get("caution_note")),
    }


def _attach_reason(payload: dict[str, Any], item: dict[str, Any]) -> None:
    response = llm_gateway.invoke(
        _reason_prompt(payload, item),
        purpose="p4_recommendation_reasons",
        max_output_tokens=220,
        cost_tag=REASON_BUDGET_SCOPE,
        metadata={
            "scenario": SCENARIO,
            "kol_entity_uid": item.get("kol_entity_uid"),
            "rank": item.get("rank"),
            "product_query": payload.get("product_query"),
        },
    )
    parsed = _parse_reason_text(str(response.get("text") or "")) if response.get("status") == "success" else None
    if parsed and all(parsed.values()):
        reason = parsed
        mode = "llm"
    else:
        reason = _deterministic_reason(item)
        mode = "deterministic_fallback"
    item["recommendation_reason"] = {
        "mode": mode,
        "provider": response.get("provider") or "rule_v0",
        "model": response.get("model") or "rule_v0",
        "status": response.get("status") or "",
        "fallback_reason": response.get("reason") or "",
        **reason,
    }


def build_new_launch_match_preview(
    *,
    product_query: str,
    limit: int = 100,
    primary_markets: str = "",
    secondary_markets: str = "",
    json_out: str = "",
    md_out: str = "",
    with_llm_reasons: bool = False,
    reason_limit: int = 20,
    persist_run: bool = False,
) -> dict[str, Any]:
    """Build a deterministic P4-1 dry-run preview from raw Memory rows."""

    safe_limit = _safe_limit(limit)
    readiness = memory.readiness()
    if readiness.get("status") != "ready_for_p4_dry_run":
        raise RuntimeError(f"memory readiness blocked P4 dry-run: {readiness.get('status')}")
    if bool(readiness.get("provider_calls_allowed")):
        raise RuntimeError("P4-1 requires provider_calls_allowed=false")

    cost_ok = check_budget(BUDGET_SCOPE, 0.0)
    budget_status = get_budget_status(BUDGET_SCOPE, estimated_cost=0.0)
    if not cost_ok:
        raise RuntimeError("budget_guard_blocked")

    now = _utcnow()
    target_family = _select_target_family(product_query)
    product_to_family, _ = _product_family_maps()
    kol_rows = _kol_entities()
    pool_map = _pool_by_source_ref()
    legacy_map = _legacy_entities_by_uid()
    facts_by_entity = _kol_facts()
    links_by_entity = _worked_links()
    signals = _target_market_signals(int(target_family["id"]))
    market_score, market_evidence = _market_signal_score(signals, now=now)
    primary = _split_csv(primary_markets)
    secondary = _split_csv(secondary_markets)
    eligible: list[dict[str, Any]] = []
    hard_excluded = 0
    low_evidence = 0

    for kol in kol_rows:
        entity_id = int(kol["id"])
        identity = _entity_payload(kol, "identity_json")
        metadata = _entity_payload(kol, "metadata_json")
        source_ref = _text(identity.get("source_ref"))
        legacy_uid = _text(metadata.get("legacy_entity_uid"))
        pool = pool_map.get(source_ref)
        legacy = legacy_map.get(legacy_uid, {})
        facts = facts_by_entity.get(entity_id, [])
        links = links_by_entity.get(entity_id, [])
        weak_label = _text(legacy.get("weak_label") or metadata.get("weak_label") or _latest_fact_value(facts, "weak_label"))
        decision = _text(legacy.get("resolution_decision") or "")
        sync_status = _text((pool or {}).get("sync_status") or _latest_fact_value(facts, "sync_status") or kol.get("status"))
        review_state = _text(metadata.get("review_state") or _latest_fact_value(facts, "review_state"))
        if weak_label == "blocked_risk" or decision == "drop":
            hard_excluded += 1
            continue

        source_refs = {link.get("source_ref") for link in links if _text(link.get("source_ref"))}
        cooperation_count = len(source_refs)
        evidence_count = _evidence_count(facts)
        risk_count = _risk_count(facts)
        country_fact = _first_fact(facts, "country")
        contact_fact = _first_fact(facts, "contact_status")
        evidence_fact = _first_fact(facts, "evidence_count")
        contact_status = _latest_fact_value(facts, "contact_status")
        country = _text(identity.get("country") or (pool or {}).get("country") or _latest_fact_value(facts, "country"))

        product_score, product_match_type, product_match_link = _product_match_score(
            target_family=target_family,
            links=links,
            product_to_family=product_to_family,
        )
        cooperation_score = _cooperation_score(cooperation_count)
        region_score, region_reason = _region_score(country, primary, secondary)
        contact_score, contact_label = _contact_score(contact_status, pool)
        recency_score = 0
        freshness_score = _freshness_score(evidence_count)
        base_score = (
            product_score
            + cooperation_score
            + market_score
            + region_score
            + contact_score
            + recency_score
            + freshness_score
        )
        penalty_factors = {
            "needs_human_review": 0.85 if sync_status == "needs_human_review" or review_state == "needs_human_review" else 1.0,
            "risk_flag": 0.70 if risk_count > 0 else 1.0,
            "resolution_escalate": 0.90 if decision == "escalate" else 1.0,
        }
        penalty_factor = math.prod(penalty_factors.values())
        final_score = round(base_score * penalty_factor, 1)

        evidence_pro: list[dict[str, Any]] = []
        evidence_con: list[dict[str, Any]] = []
        if product_match_link:
            source_payload = _source_payload(product_match_link)
            product_name = _text(product_match_link.get("product_name"))
            evidence_pro.append(
                _evidence(
                    evidence_type=product_match_type,
                    polarity="pro",
                    severity="info",
                    detail=f"{product_match_type.replace('_', ' ')} via {product_name}",
                    score_component="product_match",
                    row=product_match_link,
                    payload=source_payload,
                )
            )
        if cooperation_count:
            row = links[0]
            source_payload = _source_payload(row)
            evidence_pro.append(
                _evidence(
                    evidence_type="cooperation_strength",
                    polarity="pro",
                    severity="info",
                    detail=f"{cooperation_count} unique historical cooperation records",
                    score_component="cooperation_strength",
                    row=row,
                    payload=source_payload,
                )
            )
        for signal in market_evidence[:2]:
            payload = _fact_payload(signal)
            evidence_pro.append(
                _evidence(
                    evidence_type=_text(payload.get("signal_type") or signal.get("fact_type")),
                    polarity="pro",
                    severity="info",
                    detail=_market_detail(signal),
                    score_component="market_signal",
                    row=signal,
                    payload=payload,
                )
            )
        if region_score >= 6:
            evidence_pro.append(
                _evidence(
                    evidence_type="region_match",
                    polarity="pro",
                    severity="info",
                    detail=f"{country or 'unknown'} matched as {region_reason}",
                    score_component="region_match",
                    row=country_fact,
                    payload=_fact_payload(country_fact) if country_fact else {},
                )
            )
        elif not country:
            evidence_con.append(
                _evidence(
                    evidence_type="missing_country",
                    polarity="con",
                    severity="low",
                    detail="KOL country is missing; neutral region score applied",
                    score_component="region_match",
                    row=country_fact,
                    payload=_fact_payload(country_fact) if country_fact else {},
                )
            )
        if contact_score > 0:
            evidence_pro.append(
                _evidence(
                    evidence_type="contact_available",
                    polarity="pro",
                    severity="info",
                    detail=f"Contact availability: {contact_label}",
                    score_component="contact_availability",
                    row=contact_fact,
                    payload=_fact_payload(contact_fact) if contact_fact else {},
                )
            )
        else:
            evidence_con.append(
                _evidence(
                    evidence_type="contact_missing",
                    polarity="con",
                    severity="medium",
                    detail="No usable contact status in Memory",
                    score_component="contact_availability",
                    row=contact_fact,
                    payload=_fact_payload(contact_fact) if contact_fact else {},
                )
            )
        if evidence_count < 5:
            evidence_con.append(
                _evidence(
                    evidence_type="low_evidence_count",
                    polarity="con",
                    severity="low",
                    detail=f"Only {evidence_count} legacy evidence rows",
                    score_component="data_freshness",
                    row=evidence_fact,
                    payload=_fact_payload(evidence_fact) if evidence_fact else {},
                )
            )
        else:
            evidence_pro.append(
                _evidence(
                    evidence_type="data_freshness",
                    polarity="pro",
                    severity="info",
                    detail=f"{evidence_count} legacy evidence rows",
                    score_component="data_freshness",
                    row=evidence_fact,
                    payload=_fact_payload(evidence_fact) if evidence_fact else {},
                )
            )
        evidence_con.append(
            _evidence(
                evidence_type="no_recent_activity_signal",
                polarity="con",
                severity="medium",
                detail="No KOL-linked activity fact found within 90 days",
                score_component="recency_boost",
            )
        )
        if sync_status == "needs_human_review" or review_state == "needs_human_review":
            evidence_con.append(
                _evidence(
                    evidence_type="sync_needs_review",
                    polarity="con",
                    severity="medium",
                    detail=f"sync_status={sync_status}, review_state={review_state}",
                    score_component="penalty",
                )
            )
        if decision == "escalate":
            evidence_con.append(
                _evidence(
                    evidence_type="resolution_escalate",
                    polarity="con",
                    severity="low",
                    detail="P2C resolution decision is escalate",
                    score_component="penalty",
                )
            )
        if risk_count:
            risk_fact = next((fact for fact in facts if _text(fact.get("fact_type")) == "risk_flag"), {})
            evidence_con.append(
                _evidence(
                    evidence_type="risk_flag",
                    polarity="con",
                    severity="high",
                    detail=f"{risk_count} risk flag(s) attached",
                    score_component="penalty",
                    row=risk_fact,
                    payload=_fact_payload(risk_fact) if risk_fact else {},
                )
            )

        if len(evidence_pro) + len(evidence_con) < 3:
            low_evidence += 1
            continue

        platform = _text((pool or {}).get("platform") or identity.get("platform"))
        handle = _text((pool or {}).get("handle") or identity.get("handle"))
        display_name = _text((pool or {}).get("display_name") or kol.get("display_name") or handle)
        item = {
            "rank": 0,
            "percentile_rank": 0,
            "kol_entity_uid": kol["entity_uid"],
            "legacy_entity_uid": legacy_uid,
            "kol_pool_id": _safe_int((pool or {}).get("id")),
            "platform": platform,
            "handle": handle,
            "display_name": display_name,
            "country": country,
            "score": final_score,
            "review_required": bool(sync_status == "needs_human_review" or decision == "escalate"),
            "hard_excluded": False,
            "score_breakdown": {
                "product_match": product_score,
                "cooperation_strength": cooperation_score,
                "market_signal": market_score,
                "region_match": region_score,
                "contact_availability": contact_score,
                "recency_boost": recency_score,
                "data_freshness": freshness_score,
                "base": base_score,
                "penalty_factors": penalty_factors,
                "penalty_factor": round(penalty_factor, 4),
                "final": final_score,
            },
            "evidence_pro": evidence_pro,
            "evidence_con": evidence_con,
            "links": {"open_in_vkpi": _open_link(kol["entity_uid"])},
        }
        eligible.append(item)

    eligible.sort(
        key=lambda item: (
            float(item["score"]),
            int(item["score_breakdown"]["product_match"]),
            int(item["score_breakdown"]["cooperation_strength"]),
            int(item["score_breakdown"]["data_freshness"]),
        ),
        reverse=True,
    )
    for idx, item in enumerate(eligible):
        item["rank"] = idx + 1
        item["percentile_rank"] = _percentile(idx, len(eligible))

    returned = eligible[:safe_limit]
    median = _median_score(returned)
    markdown_display = [item for item in returned if float(item["score"]) >= median]
    reasons_attached = 0
    summary = {
        "total_candidates_evaluated": len(kol_rows),
        "eligible_after_hard_filters": len(eligible),
        "excluded_blocked_or_dropped": hard_excluded,
        "excluded_low_evidence": low_evidence,
        "returned": len(returned),
        "markdown_display_count": len(markdown_display),
        "top_score": returned[0]["score"] if returned else 0,
        "median_score": median,
        "llm_reasons_requested": bool(with_llm_reasons),
        "reasons_attached": 0,
    }
    payload = {
        "scenario": SCENARIO,
        "mode": "dry_run",
        "generated_at": _iso(now),
        "product_query": product_query,
        "target_family_uid": target_family["entity_uid"],
        "target_family_name": target_family.get("display_name") or "",
        "provider_calls_allowed": False,
        "budget_guard": {
            "scope": BUDGET_SCOPE,
            "estimated_cost_usd": 0.0,
            "allowed": bool(cost_ok),
            "recorded_cost": False,
            "configured": bool(budget_status.get("configured")),
        },
        "summary": summary,
        "score_distribution": _distribution(returned),
        "items": returned,
        "markdown_items": markdown_display,
    }
    if with_llm_reasons:
        for item in returned[: max(0, min(int(reason_limit or 0), len(returned)))]:
            _attach_reason(payload, item)
            reasons_attached += 1
        summary["reasons_attached"] = reasons_attached
    if persist_run:
        payload["persistence"] = _persist_preview_run(payload)
    else:
        payload["persistence"] = {"enabled": False}
    _json_write(json_out, {key: value for key, value in payload.items() if key != "markdown_items"})
    _markdown_write(md_out, payload)
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    distribution = payload.get("score_distribution") or {}
    lines = [
        "# P4-1 New Launch Match Dry-Run",
        "",
        f"**Product:** {payload.get('product_query', '')}",
        f"**Target family:** {payload.get('target_family_name', '')}",
        f"**Generated at:** {payload.get('generated_at', '')}",
        "**Mode:** dry_run",
        f"**Provider calls allowed:** {str(bool(payload.get('provider_calls_allowed'))).lower()}",
        f"**Budget scope:** {(payload.get('budget_guard') or {}).get('scope', '')}",
        f"**Estimated provider cost:** {(payload.get('budget_guard') or {}).get('estimated_cost_usd', 0.0)}",
        "",
        "## Summary",
        "",
        f"- Total candidates evaluated: {summary.get('total_candidates_evaluated', 0)}",
        f"- Returned in JSON: {summary.get('returned', 0)}",
        f"- Displayed in Markdown: {summary.get('markdown_display_count', 0)}",
        f"- Excluded blocked/dropped: {summary.get('excluded_blocked_or_dropped', 0)}",
        f"- Excluded low evidence: {summary.get('excluded_low_evidence', 0)}",
        f"- Top score: {summary.get('top_score', 0)}",
        f"- Median score: {summary.get('median_score', 0)}",
        f"- Recommendation reasons attached: {summary.get('reasons_attached', 0)}",
        "",
        "## Score Distribution",
        "",
        f"- P90+: {distribution.get('p90_plus', 0)} candidates",
        f"- P75-P90: {distribution.get('p75_to_p90', 0)} candidates",
        f"- P50-P75: {distribution.get('p50_to_p75', 0)} candidates",
        f"- Below P50: {distribution.get('below_p50', 0)} candidates",
        "",
        "## Top Recommendations",
        "",
    ]
    for idx, item in enumerate(payload.get("markdown_items") or [], start=1):
        handle = item.get("handle") or item.get("display_name") or item.get("kol_entity_uid")
        breakdown = item.get("score_breakdown") or {}
        lines.extend(
            [
                f"### {idx}. @{handle} (score={item.get('score')}, percentile={item.get('percentile_rank')})",
                "",
                f"**Platform:** {item.get('platform', '')}",
                f"**Country:** {item.get('country', '')}",
                f"**Review required:** {str(bool(item.get('review_required'))).lower()}",
                "",
                "**Supporting evidence:**",
            ]
        )
        for evidence in item.get("evidence_pro") or []:
            source = f"{evidence.get('source_table', '')}:{evidence.get('source_id', '')}".strip(":")
            lines.append(f"- {evidence.get('type', '')}: {evidence.get('detail', '')} [{source}]")
        lines.extend(["", "**Concerns:**"])
        concerns = item.get("evidence_con") or []
        if concerns:
            for evidence in concerns:
                lines.append(
                    f"- {evidence.get('type', '')}: {evidence.get('detail', '')} "
                    f"(severity={evidence.get('severity', '')})"
                )
        else:
            lines.append("- None")
        reason = item.get("recommendation_reason") or {}
        if reason:
            lines.extend(
                [
                    "",
                    "**Recommendation reason:**",
                    f"- Short reason: {reason.get('short_reason', '')}",
                    f"- Pitch angle: {reason.get('pitch_angle', '')}",
                    f"- Caution note: {reason.get('caution_note', '')}",
                    f"- Reason mode: {reason.get('mode', '')} ({reason.get('provider', '')}/{reason.get('status', '')})",
                ]
            )
        lines.extend(
            [
                "",
                "**Score breakdown:** "
                f"product={breakdown.get('product_match', 0)} "
                f"cooperation={breakdown.get('cooperation_strength', 0)} "
                f"market={breakdown.get('market_signal', 0)} "
                f"region={breakdown.get('region_match', 0)} "
                f"contact={breakdown.get('contact_availability', 0)} "
                f"recency={breakdown.get('recency_boost', 0)} "
                f"freshness={breakdown.get('data_freshness', 0)} "
                f"-> base={breakdown.get('base', 0)} "
                f"x penalty={breakdown.get('penalty_factor', 1)} "
                f"= {breakdown.get('final', item.get('score'))}",
                "",
                f"[Open in V-KPI: {((item.get('links') or {}).get('open_in_vkpi') or '')}]",
                "",
                "---",
                "",
            ]
        )
    return "\n".join(lines)


def format_preview_summary(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        f"scenario={payload.get('scenario', '')}",
        f"mode={payload.get('mode', '')}",
        f"product_query={payload.get('product_query', '')}",
        f"target_family={payload.get('target_family_name', '')}",
        f"provider_calls_allowed={str(bool(payload.get('provider_calls_allowed'))).lower()}",
        f"budget_scope={(payload.get('budget_guard') or {}).get('scope', '')}",
        f"budget_allowed={str(bool((payload.get('budget_guard') or {}).get('allowed'))).lower()}",
        f"budget_recorded_cost={str(bool((payload.get('budget_guard') or {}).get('recorded_cost'))).lower()}",
        f"persistence_enabled={str(bool((payload.get('persistence') or {}).get('enabled'))).lower()}",
        f"persisted_run_uid={(payload.get('persistence') or {}).get('run_uid', '')}",
        f"persisted_recommendations={(payload.get('persistence') or {}).get('recommendation_count', 0)}",
        f"total_candidates_evaluated={summary.get('total_candidates_evaluated', 0)}",
        f"eligible_after_hard_filters={summary.get('eligible_after_hard_filters', 0)}",
        f"returned={summary.get('returned', 0)}",
        f"markdown_display_count={summary.get('markdown_display_count', 0)}",
        f"excluded_blocked_or_dropped={summary.get('excluded_blocked_or_dropped', 0)}",
        f"excluded_low_evidence={summary.get('excluded_low_evidence', 0)}",
        f"top_score={summary.get('top_score', 0)}",
        f"median_score={summary.get('median_score', 0)}",
        f"llm_reasons_requested={str(bool(summary.get('llm_reasons_requested'))).lower()}",
        f"reasons_attached={summary.get('reasons_attached', 0)}",
    ]
    for idx, item in enumerate((payload.get("items") or [])[:5], start=1):
        lines.append(
            f"sample.{idx}=rank:{item.get('rank')} score:{item.get('score')} "
            f"percentile:{item.get('percentile_rank')} "
            f"{item.get('platform')}:{item.get('handle')} "
            f"pro:{len(item.get('evidence_pro') or [])} con:{len(item.get('evidence_con') or [])}"
        )
    return "\n".join(lines)

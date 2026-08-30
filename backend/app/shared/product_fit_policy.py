"""Pure deterministic policy shared by product-fit recommendation flows.

Only stdlib and ``app.shared`` imports are permitted here.  Database reads,
provider calls, persistence, logging, and runtime model lookup belong behind
the repository and reason ports declared in :mod:`product_fit_contracts`.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Mapping

from app.shared.vkpi_utils import json_loads, row_to_dict, to_float, to_int, to_text


RECOMMENDATION_BUDGET_SCOPE = "cron:p4_recommendations_daily"
PRODUCT_FIT_REASON_BUDGET_SCOPE = "cron:p4_recommendation_reasons"
PRODUCT_FIT_SCENARIO = "kol_product_fit"


def text(value: Any) -> str:
    return to_text(value)


def lower(value: Any) -> str:
    return text(value).lower()


def load_json(value: Any, default: Any) -> Any:
    return json_loads(value, default)


def json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def as_row_dict(row: Any) -> dict[str, Any]:
    return row_to_dict(row)


def safe_int(value: Any, default: int = 0) -> int:
    return to_int(value, default)


def safe_float(value: Any, default: float = 0.0) -> float:
    return to_float(value, default)


def safe_limit(limit: int) -> int:
    return max(1, min(500, int(limit or 100)))


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


def country_key(value: Any) -> str:
    key = re.sub(r"\s+", " ", lower(value))
    return _COUNTRY_ALIASES.get(key, key)


def split_csv(value: str) -> set[str]:
    return {
        country_key(part)
        for part in str(value or "").split(",")
        if country_key(part)
    }


def family_tokens(value: str) -> dict[str, Any]:
    normalized = lower(value).replace("/", " ")
    focal = ""
    aperture = ""
    series: set[str] = set()
    mount: set[str] = set()
    focal_match = re.search(r"(\d{2,3})\s*mm", normalized)
    if not focal_match:
        focal_match = re.search(r"\b(\d{2,3})\b", normalized)
    if focal_match:
        focal = f"{focal_match.group(1)}mm"
    aperture_match = re.search(r"f\s*(\d+(?:\.\d+)?)", normalized)
    if aperture_match:
        aperture = f"f{aperture_match.group(1)}"
    for token in ("lab", "air", "evo", "pro", "lite"):
        if token in normalized:
            series.add(token)
    for token in ("fe", "e", "z", "xf", "x", "m43", "pl", "ef", "rf"):
        if re.search(rf"\b{re.escape(token)}\b", normalized):
            mount.add(token)
    return {"focal": focal, "aperture": aperture, "series": series, "mount": mount}


def parse_date(value: Any) -> datetime | None:
    raw = text(value)
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed = datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def within_days(value: Any, days: int, *, now: datetime) -> bool:
    parsed = parse_date(value)
    if not parsed:
        return False
    return abs((now - parsed).days) <= int(days)


def source_fields(
    row: Mapping[str, Any],
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_payload = payload or {}
    default_table = (
        "vkpi_memory_links"
        if row.get("link_type") or row.get("link_uid")
        else "computed:p4_new_launch_match"
    )
    return {
        "source_table": row.get("source_table") or default_table,
        "source_id": str(
            row.get("source_id")
            or row.get("id")
            or source_payload.get("source_id")
            or ""
        ),
        "source_ref": (
            row.get("source_ref")
            or source_payload.get("source_ref")
            or "p4_new_launch_match:computed"
        ),
        "source_sheet": source_payload.get("source_sheet") or "",
        "source_row": source_payload.get("source_row") or "",
        "confidence_score": safe_float(row.get("confidence_score"), 1.0),
    }


def evidence(
    *,
    evidence_type: str,
    polarity: str,
    severity: str,
    detail: str,
    score_component: str,
    row: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_row = row or {}
    return {
        "type": evidence_type,
        "polarity": polarity,
        "severity": severity,
        "detail": detail,
        "score_component": score_component,
        **source_fields(source_row, payload),
    }


def fact_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    value = load_json(row.get("fact_json") or "{}", {})
    return value if isinstance(value, dict) else {}


def source_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    value = load_json(row.get("source_json") or "{}", {})
    return value if isinstance(value, dict) else {}


def entity_payload(row: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = load_json(row.get(key) or "{}", {})
    return value if isinstance(value, dict) else {}


def latest_fact_value(facts: list[dict[str, Any]], fact_type: str) -> str:
    for fact in facts:
        if text(fact.get("fact_type")) == fact_type:
            return text(fact.get("fact_value_text"))
    return ""


def first_fact(facts: list[dict[str, Any]], fact_type: str) -> dict[str, Any]:
    for fact in facts:
        if text(fact.get("fact_type")) == fact_type:
            return fact
    return {}


def evidence_count(facts: list[dict[str, Any]]) -> int:
    total = 0
    for fact in facts:
        if text(fact.get("fact_type")) != "evidence_count":
            continue
        payload = fact_payload(fact)
        total += safe_int(payload.get("count"), safe_int(fact.get("fact_value_text")))
    return total


def risk_count(facts: list[dict[str, Any]]) -> int:
    return sum(1 for fact in facts if text(fact.get("fact_type")) == "risk_flag")


def contact_score(
    contact_status: str,
    pool: Mapping[str, Any] | None,
) -> tuple[int, str]:
    source = pool or {}
    raw = load_json(source.get("raw_platform_data") or "{}", {})
    has_email = (
        bool(source.get("contact_has_email"))
        if "contact_has_email" in source
        else bool(raw.get("contact_has_email"))
    )
    has_phone = (
        bool(source.get("contact_has_phone"))
        if "contact_has_phone" in source
        else bool(raw.get("contact_has_phone"))
    )
    if has_email and has_phone:
        return 10, "email_and_phone_available_restricted"
    if has_email:
        return 7, "email_available_restricted"
    normalized = lower(contact_status)
    if normalized in {"available_restricted", "dm_only"}:
        return 4, normalized
    if normalized == "missing":
        return 0, "missing"
    return 2, normalized or "unknown"


def freshness_score(count: int) -> int:
    if count >= 10:
        return 5
    if count >= 5:
        return 3
    if count > 0:
        return 1
    return 0


def market_signal_score(
    signals: list[dict[str, Any]],
    *,
    now: datetime,
) -> tuple[int, list[dict[str, Any]]]:
    score = 0
    evidence_rows: list[dict[str, Any]] = []
    launch = None
    official = None
    for signal in signals:
        payload = fact_payload(signal)
        signal_type = text(payload.get("signal_type") or signal.get("fact_type"))
        date_value = (
            payload.get("signal_date")
            or payload.get("launch_date")
            or payload.get("publish_date")
        )
        if (
            signal_type == "launch_plan"
            and launch is None
            and within_days(date_value, 30, now=now)
        ):
            launch = signal
        if (
            signal_type in {"official_content", "official_material"}
            and official is None
            and within_days(date_value, 90, now=now)
        ):
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


def market_detail(signal: Mapping[str, Any]) -> str:
    payload = fact_payload(signal)
    signal_type = payload.get("signal_type") or signal.get("fact_type")
    date_value = (
        payload.get("signal_date")
        or payload.get("launch_date")
        or payload.get("publish_date")
        or ""
    )
    product = (
        payload.get("product_name")
        or payload.get("product")
        or payload.get("launch_name")
        or ""
    )
    return f"{signal_type} {product} {date_value}".strip()


def percentile(index: int, total: int) -> float:
    if total <= 1:
        return 100.0
    return round(100 * (total - 1 - index) / (total - 1), 1)


def median_score(items: list[dict[str, Any]]) -> float:
    if not items:
        return 0.0
    scores = sorted(float(item["score"]) for item in items)
    mid = len(scores) // 2
    if len(scores) % 2:
        return scores[mid]
    return round((scores[mid - 1] + scores[mid]) / 2, 1)


def distribution(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "p90_plus": sum(
            1 for item in items if int(item["percentile_rank"]) >= 90
        ),
        "p75_to_p90": sum(
            1
            for item in items
            if 75 <= int(item["percentile_rank"]) < 90
        ),
        "p50_to_p75": sum(
            1
            for item in items
            if 50 <= int(item["percentile_rank"]) < 75
        ),
        "below_p50": sum(
            1 for item in items if int(item["percentile_rank"]) < 50
        ),
    }


def member_counts(product_to_family: Mapping[int, Mapping[str, Any]]) -> dict[int, int]:
    counts: dict[int, int] = defaultdict(int)
    for row in product_to_family.values():
        counts[int(row["family_id"])] += 1
    return counts


def family_product_ids(
    product_to_family: Mapping[int, Mapping[str, Any]],
) -> dict[int, set[int]]:
    result: dict[int, set[int]] = defaultdict(set)
    for product_id, row in product_to_family.items():
        result[int(row["family_id"])].add(int(product_id))
    return result


def proved_family_ids(
    links: list[dict[str, Any]],
    product_to_family: Mapping[int, Mapping[str, Any]],
) -> set[int]:
    family_ids: set[int] = set()
    for link in links:
        family = product_to_family.get(int(link.get("target_entity_id") or 0))
        if family:
            family_ids.add(int(family["family_id"]))
    return family_ids


def historical_fit(
    family: Mapping[str, Any],
    links: list[dict[str, Any]],
    product_to_family: Mapping[int, Mapping[str, Any]],
) -> tuple[int, str, dict[str, Any] | None]:
    family_id = int(family["id"])
    target_tokens = family_tokens(
        text(family.get("display_name") or family.get("identity_key"))
    )
    best: tuple[int, str, dict[str, Any] | None] = (0, "no_historical_fit", None)
    for link in links:
        link_family = product_to_family.get(int(link.get("target_entity_id") or 0))
        if not link_family:
            continue
        if int(link_family["family_id"]) == family_id:
            return 25, "direct_family_history", link
        tokens = family_tokens(
            text(link_family.get("family_name") or link_family.get("family_key"))
        )
        same_focal = bool(
            target_tokens["focal"] and target_tokens["focal"] == tokens["focal"]
        )
        if same_focal and best[0] < 10:
            best = (10, "same_focal_history", link)
    return best


def adjacent_fit(
    family: Mapping[str, Any],
    proved_families: list[dict[str, Any]],
) -> tuple[int, str, dict[str, Any] | None]:
    target_tokens = family_tokens(
        text(family.get("display_name") or family.get("identity_key"))
    )
    best: tuple[int, str, dict[str, Any] | None] = (0, "no_adjacent_fit", None)
    for proved in proved_families:
        if int(proved["id"]) == int(family["id"]):
            continue
        tokens = family_tokens(
            text(proved.get("display_name") or proved.get("identity_key"))
        )
        same_focal = bool(
            target_tokens["focal"] and target_tokens["focal"] == tokens["focal"]
        )
        shared_mount = bool(
            target_tokens["mount"]
            and tokens["mount"]
            and target_tokens["mount"] & tokens["mount"]
        )
        target_has_product_type = bool(
            target_tokens["focal"] or target_tokens["series"]
        )
        proved_has_product_type = bool(tokens["focal"] or tokens["series"])
        if same_focal:
            return 15, "same_focal_expansion", proved
        if target_has_product_type and proved_has_product_type and best[0] < 10:
            best = (10, "same_product_category_expansion", proved)
        if shared_mount and best[0] < 6:
            best = (6, "same_mount_expansion", proved)
    return best


def region_relevance(
    country: str,
    primary: set[str],
    secondary: set[str],
) -> tuple[int, str]:
    key = country_key(country)
    if not key:
        return 2, "missing_country"
    if primary and key in primary:
        return 5, "primary_target_market"
    if secondary and key in secondary:
        return 3, "secondary_target_market"
    return 2, "known_other_market"


def cooperation_depth(count: int) -> int:
    if count >= 10:
        return 15
    if count >= 5:
        return 10
    if count >= 2:
        return 6
    if count == 1:
        return 3
    return 0


def render_family_detail(family: Mapping[str, Any]) -> str:
    return text(
        family.get("display_name")
        or family.get("identity_key")
        or family.get("entity_uid")
    )


def normalize_product_fit_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", lower(value))


def dimensions11_fit_for_family(
    family: Mapping[str, Any],
    dimensions_fit: Mapping[str, Mapping[str, Any]],
) -> tuple[float, dict[str, Any] | None]:
    if not dimensions_fit:
        return 0.0, None
    family_name = render_family_detail(family)
    family_key = text(family.get("identity_key"))
    family_blob = normalize_product_fit_key(f"{family_name} {family_key}")
    if not family_blob:
        return 0.0, None
    best: dict[str, Any] | None = None
    best_component = 0.0
    for item in dimensions_fit.values():
        sku_norm = text(item.get("normalized"))
        if not sku_norm:
            continue
        if sku_norm not in family_blob and family_blob not in sku_norm:
            continue
        score = max(0.0, min(100.0, safe_float(item.get("score"), 0.0)))
        confidence = max(
            0.0,
            min(1.0, safe_float(item.get("confidence"), 0.0)),
        )
        component = round((score / 100.0) * confidence * 20.0, 1)
        if component > best_component:
            best_component = component
            best = {
                **item,
                "match_type": "sku_family_exact",
                "family_name": family_name,
            }
    return best_component, best


def append_history_fit_evidence(
    family: Mapping[str, Any],
    scores: Mapping[str, Any],
    evidence_pro: list[dict[str, Any]],
    evidence_con: list[dict[str, Any]],
) -> None:
    historical_link = scores["historical_link"]
    if historical_link:
        historical_type = scores["historical_type"]
        evidence_pro.append(
            evidence(
                evidence_type=historical_type,
                polarity="pro",
                severity="info",
                detail=(
                    f"{historical_type.replace('_', ' ')} via "
                    f"{text(historical_link.get('product_name'))}"
                ),
                score_component="historical_fit",
                row=historical_link,
                payload=source_payload(historical_link),
            )
        )
    else:
        evidence_con.append(
            evidence(
                evidence_type="no_direct_history",
                polarity="con",
                severity="low",
                detail=(
                    "No direct KOL cooperation found for "
                    f"{render_family_detail(family)}"
                ),
                score_component="historical_fit",
            )
        )
    adjacent_family = scores["adjacent_family"]
    if adjacent_family:
        adjacent_type = scores["adjacent_type"]
        evidence_pro.append(
            evidence(
                evidence_type=adjacent_type,
                polarity="pro",
                severity="info",
                detail=(
                    f"{adjacent_type.replace('_', ' ')} from "
                    f"{render_family_detail(adjacent_family)}"
                ),
                score_component="adjacent_product_fit",
                row=adjacent_family,
            )
        )


def append_penalty_fit_evidence(
    context: Mapping[str, Any],
    evidence_con: list[dict[str, Any]],
) -> None:
    if (
        context["sync_status"] == "needs_human_review"
        or context["review_state"] == "needs_human_review"
    ):
        evidence_con.append(
            evidence(
                evidence_type="sync_needs_review",
                polarity="con",
                severity="medium",
                detail=(
                    f"sync_status={context['sync_status']}, "
                    f"review_state={context['review_state']}"
                ),
                score_component="penalty",
            )
        )
    if context["decision"] == "escalate":
        evidence_con.append(
            evidence(
                evidence_type="resolution_escalate",
                polarity="con",
                severity="low",
                detail="P2C resolution decision is escalate",
                score_component="penalty",
            )
        )
    if context["risk_count"]:
        risk_fact = next(
            (
                fact
                for fact in context["facts"]
                if text(fact.get("fact_type")) == "risk_flag"
            ),
            {},
        )
        evidence_con.append(
            evidence(
                evidence_type="risk_flag",
                polarity="con",
                severity="high",
                detail=f"{context['risk_count']} risk flag(s) attached",
                score_component="penalty",
                row=risk_fact,
                payload=fact_payload(risk_fact) if risk_fact else {},
            )
        )


def rank_product_fit_candidates(
    eligible: list[dict[str, Any]],
    *,
    safe_limit_value: int,
) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]]]:
    eligible.sort(
        key=lambda item: (
            float(item["score"]),
            int(item["score_breakdown"]["historical_fit"]),
            int(item["score_breakdown"]["adjacent_product_fit"]),
            int(item["score_breakdown"]["market_activity"]),
        ),
        reverse=True,
    )
    for index, item in enumerate(eligible):
        item["rank"] = index + 1
        item["percentile_rank"] = percentile(index, len(eligible))
    returned = eligible[:safe_limit_value]
    median = median_score(returned)
    return (
        returned,
        median,
        [item for item in returned if float(item["score"]) >= median],
    )


def deterministic_reason(
    payload: Mapping[str, Any],
    item: Mapping[str, Any],
) -> dict[str, str]:
    pro = item.get("evidence_pro") or []
    con = item.get("evidence_con") or []
    strongest = (
        pro[0].get("detail")
        if pro
        else "Has usable Memory evidence for this product family"
    )
    concern = con[0].get("detail") if con else "No major concern in current Memory"
    kol = payload.get("kol") or {}
    handle = kol.get("handle") or kol.get("display_name") or kol.get("kol_entity_uid")
    family = item.get("product_family_name") or item.get("product_family_uid")
    return {
        "short_reason": f"{family} fits {handle} because {strongest}.",
        "pitch_angle": (
            "Frame outreach around the closest historical product-family evidence."
        ),
        "caution_note": concern,
    }


def reason_prompt(payload: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    compact = {
        "scenario": PRODUCT_FIT_SCENARIO,
        "kol": payload.get("kol"),
        "product_family": {
            "uid": item.get("product_family_uid"),
            "name": item.get("product_family_name"),
            "score": item.get("score"),
            "product_member_count": item.get("product_member_count"),
        },
        "score_breakdown": item.get("score_breakdown"),
        "evidence_pro": [
            {
                "type": row.get("type"),
                "detail": row.get("detail"),
                "source_ref": row.get("source_ref"),
            }
            for row in (item.get("evidence_pro") or [])[:5]
        ],
        "evidence_con": [
            {
                "type": row.get("type"),
                "detail": row.get("detail"),
                "severity": row.get("severity"),
            }
            for row in (item.get("evidence_con") or [])[:5]
        ],
    }
    return (
        "Write a concise V-KPI KOL-to-product recommendation reason as strict JSON with keys "
        "short_reason, pitch_angle, caution_note. Do not invent facts. "
        "Use only the evidence below. No markdown.\n\n"
        + json.dumps(compact, ensure_ascii=False, default=json_default)
    )


def valid_reason_payload(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(key), str) and bool(str(value.get(key) or "").strip())
        for key in ("short_reason", "pitch_angle", "caution_note")
    )


def reason_failure_code(value: Any) -> str:
    result = value if isinstance(value, dict) else {}
    failure = result.get("failure") if isinstance(result.get("failure"), dict) else {}
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    latest = errors[-1] if errors and isinstance(errors[-1], dict) else {}
    return str(
        failure.get("code")
        or result.get("failure_code")
        or result.get("reason")
        or latest.get("status")
        or "llm_unavailable"
    )[:120]


__all__ = [name for name in globals() if not name.startswith("_")]

"""Bounded Dealer candidate preview/import workflow used by ``dealer_scrape``.

All mutable seams are supplied by the facade.  That keeps record-only previews
database-free and preserves the long-standing monkeypatch contract.
"""
from __future__ import annotations

from typing import Any, Callable


def record_scrape_audit_impl(
    *,
    source: str,
    requested: int,
    inserted: int,
    skipped: int,
    geocoded: int,
    pending_geocode: int,
    record_only: bool,
    errors: list[Any],
    audit_table: str,
    table_exists: Callable[[str], bool],
    get_conn: Callable[[], Any],
    logger: Any,
) -> None:
    if not table_exists(audit_table):
        return
    try:
        conn = get_conn()
        conn.execute(
            f"""
            INSERT INTO {audit_table}
              (source, requested, inserted, skipped, geocoded,
               pending_geocode, record_only, error_count, created_at)
            VALUES (?,?,?,?,?,?,?,?, NOW())
            """,
            (
                str(source or ""),
                int(requested or 0),
                int(inserted or 0),
                int(skipped or 0),
                int(geocoded or 0),
                int(pending_geocode or 0),
                bool(record_only),
                int(len(errors or [])),
            ),
        )
        conn.commit()
    except Exception:
        logger.warning(
            "dealer_scrape.audit_record_failed",
            extra={"source": source, "record_only": record_only},
            exc_info=True,
        )


def scrape_dealers_enqueue_impl(
    limit: int,
    record_only: bool,
    source: str | None,
    *,
    str_or_none: Callable[[Any], str | None],
    clamp_limit: Callable[[Any], int],
    fetch_candidates: Callable[[str, int], list[dict[str, Any]]],
    geocode: Callable[[dict[str, Any]], tuple[float | None, float | None]],
    reviewed_persistence_contract: Callable[[], dict[str, Any]],
    reviewed_persistence_status: Callable[[], dict[str, Any]],
    upsert_dealer: Callable[..., dict[str, Any]],
    sleep: Callable[[float], None],
    sleep_between: float,
    record_scrape_audit: Callable[..., None],
    logger: Any,
) -> dict[str, Any]:
    src = str_or_none(source) or "reviewed_public_retailers_20260713"
    requested = clamp_limit(limit)
    errors: list[dict[str, Any]] = []
    candidates = fetch_candidates(src, requested)
    requested = len(candidates)

    from app.domains.events import radar_quality

    quality_contract = radar_quality.audit_dealer_candidates(candidates)
    if record_only is not False:
        plan: list[dict[str, Any]] = []
        geocoded = 0
        for candidate in candidates:
            lat, lng = geocode(candidate)
            will_geocode = lat is not None and lng is not None
            if will_geocode:
                geocoded += 1
            source_url = str_or_none(candidate.get("location_source_url"))
            observed_at = str_or_none(candidate.get("source_checked_at"))
            review_status = str_or_none(candidate.get("source_status")) or "unverified"
            plan.append(
                {
                    "name": str_or_none(candidate.get("name")),
                    "address": str_or_none(candidate.get("address")),
                    "city": str_or_none(candidate.get("city")),
                    "state": str_or_none(candidate.get("state")),
                    "brand_listing_url": str_or_none(candidate.get("brand_listing_url")),
                    "location_source_url": source_url,
                    "source_url": source_url,
                    "source_checked_at": observed_at,
                    "observed_at": observed_at,
                    "source_status": review_status,
                    "review_status": review_status,
                    "source_id": str_or_none(candidate.get("source_id")),
                    "stable_org_key": str_or_none(candidate.get("stable_org_key")),
                    "stable_location_key": str_or_none(
                        candidate.get("stable_location_key")
                    ),
                    "reviewer_id": str_or_none(candidate.get("reviewer_id")),
                    "evidence_scope": str_or_none(candidate.get("evidence_scope")),
                    "value_status": str_or_none(candidate.get("value_status")),
                    "authorization_status": (
                        str_or_none(candidate.get("authorization_status"))
                        or "needs_viltrox_confirmation"
                    ),
                    "postal_code": str_or_none(candidate.get("postal_code")),
                    "phone": str_or_none(candidate.get("phone")),
                    "contact_email": str_or_none(candidate.get("contact_email")),
                    "store_hours": str_or_none(candidate.get("store_hours")),
                    "public_services": str_or_none(candidate.get("public_services")),
                    "will_geocode": will_geocode,
                }
            )
        pending = len(plan) - geocoded
        persistence_contract = reviewed_persistence_contract()
        quality_allowed = bool(quality_contract["import_gate"]["allowed"])
        persistence_supported = bool(persistence_contract.get("supported"))
        import_allowed = quality_allowed and persistence_supported
        import_block_reason = None
        if not quality_allowed:
            import_block_reason = "quality_contract_blocked"
        elif not persistence_supported:
            import_block_reason = str(
                persistence_contract.get("reason")
                or "reviewed_persistence_contract_unavailable"
            )
        return {
            "ok": True,
            "source": src,
            "requested": requested,
            "inserted": 0,
            "skipped": len(plan),
            "geocoded": geocoded,
            "pending_geocode": pending,
            "record_only": True,
            "import_allowed": import_allowed,
            "import_block_reason": import_block_reason,
            "quality_status": quality_contract["quality_status"],
            "claim_status": quality_contract["claim_status"],
            "plan": plan,
            "quality_contract": quality_contract,
            "persistence_contract": persistence_contract,
            "errors": errors,
        }

    if not quality_contract["import_gate"]["allowed"]:
        blocking_codes = sorted(
            {
                str(item.get("code") or "dealer.quality_contract_failed")
                for item in quality_contract.get("issues", [])
                if item.get("severity") == "error"
            }
        )
        raise ValueError(
            "dealer import blocked by quality contract: "
            + ", ".join(blocking_codes[:8])
        )
    persistence_contract = reviewed_persistence_status()
    if not persistence_contract["supported"]:
        raise ValueError(
            "dealer import blocked by persistence contract: "
            + str(persistence_contract["reason"])
        )

    inserted = 0
    updated = 0
    geocoded = 0
    for candidate in candidates:
        payload = dict(candidate)
        payload.setdefault("source", src)
        payload.setdefault("source_status", "public_listing_verified")
        payload.setdefault("authorization_status", "needs_viltrox_confirmation")
        payload.setdefault(
            "verification_note",
            "公开在售页与门店地址页已核验；未从 Viltrox 官方名录确认授权关系。",
        )
        try:
            result = upsert_dealer(payload, ingest_class="reviewed_public_listing")
            if not isinstance(result, dict) or result.get("ok") is not True:
                errors.append(
                    {
                        "name": str_or_none(candidate.get("name")),
                        "error": "dealer upsert did not confirm success",
                    }
                )
                sleep(sleep_between)
                continue
            if result.get("inserted"):
                inserted += 1
            else:
                updated += 1
            if result.get("geocoded"):
                geocoded += 1
        except ValueError:
            logger.warning("dealer_scrape.upsert_validation_failed", exc_info=True)
            errors.append(
                {
                    "name": str_or_none(candidate.get("name")),
                    "error": "dealer validation failed",
                }
            )
        except Exception:
            logger.warning("dealer_scrape.upsert_failed", exc_info=True)
            errors.append(
                {
                    "name": str_or_none(candidate.get("name")),
                    "error": "dealer upsert failed",
                }
            )
        sleep(sleep_between)

    pending = inserted + updated - geocoded
    succeeded = inserted + updated
    failed = len(errors)
    import_completed = succeeded == requested and failed == 0
    result = {
        "ok": import_completed,
        "source": src,
        "requested": requested,
        "inserted": inserted,
        "updated": updated,
        "skipped": len(errors),
        "succeeded": succeeded,
        "failed": failed,
        "geocoded": geocoded,
        "pending_geocode": pending,
        "record_only": False,
        "import_allowed": True,
        "import_completed": import_completed,
        "write_status": (
            "complete" if import_completed else "failed" if succeeded == 0 else "partial"
        ),
        "quality_status": quality_contract["quality_status"],
        "claim_status": quality_contract["claim_status"],
        "quality_contract": quality_contract,
        "persistence_contract": persistence_contract,
        "errors": errors,
    }
    record_scrape_audit(
        source=src,
        requested=requested,
        inserted=inserted,
        skipped=len(errors),
        geocoded=geocoded,
        pending_geocode=pending,
        record_only=False,
        errors=errors,
    )
    return result

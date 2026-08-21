"""MY KOL video tracking write boundary.

This first slice intentionally accepts only video evidence that already exists
for the selected KOL.  Resolving an unseen URL must prove the remote author is
the selected KOL, which the generic resolver does not currently guarantee.
"""
from __future__ import annotations

from typing import Any

from app.domains.kol.pool_common import _table_columns
from app.domains.kol.url_deep_crawl import classify_url
from app.domains.kol.video_metric_refresh import enqueue_video_metric_refresh
from app.domains.kol.video_url_identity import (
    SUPPORTED_VIDEO_HOSTS,
    VideoUrlIdentityError,
    parse_supported_video_url,
)


MAX_PRODUCT_SKUS = 5
TRACKING_SOURCE = "my_kol_video_tracking"


class VideoTrackingError(RuntimeError):
    """Stable public error code with an HTTP-safe status."""

    def __init__(self, code: str, status_code: int = 422):
        super().__init__(code)
        self.code = code
        self.status_code = int(status_code)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def validate_supported_video_url(value: Any) -> tuple[str, str]:
    """Return ``(normalized_url, platform)`` for an explicit public video URL."""

    try:
        identity = parse_supported_video_url(value)
    except VideoUrlIdentityError as exc:
        raise VideoTrackingError(exc.code) from exc
    return identity.normalized_url, identity.platform


def normalize_product_skus(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise VideoTrackingError("product_skus_must_be_list")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise VideoTrackingError("product_sku_invalid")
        sku = value.strip()
        if not sku or len(sku) > 200:
            raise VideoTrackingError("product_sku_invalid")
        if sku not in seen:
            seen.add(sku)
            result.append(sku)
    if len(result) > MAX_PRODUCT_SKUS:
        raise VideoTrackingError("too_many_product_skus")
    return result


def _assert_target_writable(
    conn: Any,
    *,
    kol_pool_id: int,
    staff: dict[str, Any] | None,
) -> int:
    from app.domains.kol.my_kol_paid_action_access import (
        MyKolPaidActionError,
        assert_target_writable,
    )

    try:
        return assert_target_writable(
            conn,
            kol_pool_id=int(kol_pool_id),
            staff=staff,
        )
    except MyKolPaidActionError as exc:
        # Preserve the existing public video-tracking code while sharing one
        # row-level policy with deep analysis/comments/audience actions.
        code = (
            "my_kol_video_write_forbidden"
            if exc.code == "my_kol_paid_action_write_forbidden"
            else exc.code
        )
        raise VideoTrackingError(code, exc.status_code) from exc


def _validate_skus_exist(conn: Any, product_skus: list[str]) -> None:
    if not product_skus:
        return
    placeholders = ", ".join("?" for _ in product_skus)
    rows = conn.execute(
        f"SELECT sku FROM vkpi_products WHERE sku IN ({placeholders})",
        tuple(product_skus),
    ).fetchall()
    found = {_text(dict(row).get("sku")) for row in rows}
    if found != set(product_skus):
        raise VideoTrackingError("unknown_product_sku")


def _validate_evidence(
    evidence: dict[str, Any] | None,
    *,
    kol_pool_id: int,
    expected_platform: str | None = None,
) -> dict[str, Any]:
    if not evidence:
        raise VideoTrackingError("new_video_target_resolution_required")
    if _int(evidence.get("kol_pool_id")) != int(kol_pool_id):
        raise VideoTrackingError("video_evidence_owned_by_other_kol", 409)
    if evidence.get("is_active") in (False, 0):
        raise VideoTrackingError("video_evidence_inactive", 409)
    if _text(evidence.get("evidence_type") or "video").lower() != "video":
        raise VideoTrackingError("video_evidence_not_video")
    platform = _text(evidence.get("platform")).lower()
    if expected_platform and platform and platform != expected_platform:
        raise VideoTrackingError("video_evidence_platform_mismatch", 409)
    if platform not in SUPPORTED_VIDEO_HOSTS:
        raise VideoTrackingError("video_metric_platform_unsupported")
    try:
        identity = parse_supported_video_url(evidence.get("content_url"))
    except VideoUrlIdentityError as exc:
        raise VideoTrackingError("video_evidence_identity_invalid", 409) from exc
    if identity.platform != platform:
        raise VideoTrackingError("video_evidence_platform_mismatch", 409)
    return evidence


def _load_evidence_by_id(conn: Any, evidence_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM vkpi_kol_video_evidence WHERE id=? LIMIT 1",
        (int(evidence_id),),
    ).fetchone()
    return dict(row) if row else None


def _find_existing_evidence(
    conn: Any,
    *,
    normalized_url: str,
    platform: str,
    video_id: str,
    kol_pool_id: int,
) -> dict[str, Any] | None:
    exact = conn.execute(
        "SELECT * FROM vkpi_kol_video_evidence WHERE content_url=? LIMIT 1",
        (normalized_url,),
    ).fetchone()
    if exact:
        return dict(exact)
    rows = conn.execute(
        """
        SELECT *
        FROM vkpi_kol_video_evidence
        WHERE LOWER(COALESCE(platform, ''))=?
        ORDER BY id DESC
        LIMIT 1000
        """,
        (platform,),
    ).fetchall()
    matches: list[dict[str, Any]] = []
    for row in rows:
        candidate = dict(row)
        try:
            identity = parse_supported_video_url(candidate.get("content_url"))
        except VideoUrlIdentityError:
            continue
        if identity.platform == platform and identity.video_id == video_id:
            matches.append(candidate)
    for candidate in matches:
        if _int(candidate.get("kol_pool_id")) == int(kol_pool_id):
            return candidate
    return matches[0] if matches else None


def _link_products(
    conn: Any,
    *,
    evidence_id: int,
    product_skus: list[str],
    actor_id: int,
) -> tuple[int, list[str]]:
    inserted = 0
    for sku in product_skus:
        row = conn.execute(
            """
            INSERT INTO vkpi_kol_video_product_links (
                evidence_id, product_sku, relation_type, source,
                confidence, created_by_staff_id, created_at, updated_at
            ) VALUES (?, ?, 'manual', 'my_kol_video_tracking', 1.000, ?, NOW(), NOW())
            ON CONFLICT (evidence_id, product_sku, relation_type) DO NOTHING
            RETURNING product_sku
            """,
            (int(evidence_id), sku, int(actor_id)),
        ).fetchone()
        inserted += 1 if row else 0
    rows = conn.execute(
        """
        SELECT product_sku
        FROM vkpi_kol_video_product_links
        WHERE evidence_id=? AND relation_type='manual'
        ORDER BY product_sku
        """,
        (int(evidence_id),),
    ).fetchall()
    return inserted, [_text(dict(row).get("product_sku")) for row in rows]


def _activate_metric_tracking(
    conn: Any,
    *,
    evidence_id: int,
    actor_id: int,
    source: str = TRACKING_SOURCE,
) -> bool:
    """Create or reactivate the explicit durable auto-refresh subscription."""

    row = conn.execute(
        """
        INSERT INTO vkpi_kol_video_metric_tracking (
            evidence_id, tracked_by_staff_id, status, source,
            pause_reason, created_at, updated_at
        ) VALUES (?, ?, 'active', ?, '', NOW(), NOW())
        ON CONFLICT (evidence_id) DO UPDATE SET
            tracked_by_staff_id=excluded.tracked_by_staff_id,
            status='active',
            source=excluded.source,
            pause_reason='',
            updated_at=NOW()
        RETURNING evidence_id
        """,
        (int(evidence_id), int(actor_id), _text(source)[:80] or TRACKING_SOURCE),
    ).fetchone()
    return bool(row)


def _mark_tracking_enqueued(
    conn: Any,
    *,
    evidence_id: int,
    job_id: int,
    enqueue_status: str,
) -> None:
    conn.execute(
        """
        UPDATE vkpi_kol_video_metric_tracking
        SET last_enqueued_at=NOW(), last_job_id=?, last_enqueue_status=?,
            updated_at=NOW()
        WHERE evidence_id=? AND status='active'
        """,
        (
            int(job_id),
            _text(enqueue_status)[:32],
            int(evidence_id),
        ),
    )


def product_links_for_evidence(
    conn: Any,
    evidence_ids: list[int] | tuple[int, ...] | set[int],
) -> dict[int, list[dict[str, Any]]]:
    """Return all product links in one query, with migration-safe empty lists."""

    ids = sorted({_int(value) for value in evidence_ids if _int(value) > 0})
    result = {evidence_id: [] for evidence_id in ids}
    if not ids or not _table_columns(conn, "vkpi_kol_video_product_links"):
        return result
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT l.evidence_id, l.product_sku, l.relation_type, l.source,
               l.confidence, p.model_name, p.marketing_name
        FROM vkpi_kol_video_product_links l
        LEFT JOIN vkpi_products p ON p.sku=l.product_sku
        WHERE l.evidence_id IN ({placeholders})
        ORDER BY l.evidence_id, l.relation_type, l.product_sku
        """,
        tuple(ids),
    ).fetchall()
    for row in rows:
        item = dict(row)
        evidence_id = _int(item.pop("evidence_id", 0))
        if evidence_id in result:
            result[evidence_id].append(item)
    return result


def _queue_for_evidence(
    conn: Any,
    *,
    kol_pool_id: int,
    evidence: dict[str, Any],
    product_skus: list[str],
    actor_id: int,
    staff: dict[str, Any] | None,
    register_tracking: bool = True,
    refresh_source: str = TRACKING_SOURCE,
    queue_lane: str = "interactive",
) -> dict[str, Any]:
    _validate_skus_exist(conn, product_skus)
    links_created, all_product_skus = _link_products(
        conn,
        evidence_id=_int(evidence.get("id")),
        product_skus=product_skus,
        actor_id=actor_id,
    )
    tracking_active = False
    if register_tracking:
        tracking_active = _activate_metric_tracking(
            conn,
            evidence_id=_int(evidence.get("id")),
            actor_id=actor_id,
            source=refresh_source,
        )
    queued = enqueue_video_metric_refresh(
        conn,
        evidence=evidence,
        kol_pool_id=int(kol_pool_id),
        staff=staff,
        source=refresh_source,
        queue_lane=queue_lane,
    )
    if register_tracking:
        _mark_tracking_enqueued(
            conn,
            evidence_id=_int(evidence.get("id")),
            job_id=_int(queued.get("job_id")),
            enqueue_status=_text(queued.get("status")),
        )
    return {
        **queued,
        "kol_pool_id": int(kol_pool_id),
        "product_skus": all_product_skus,
        "product_links_created": links_created,
        "existing_evidence": True,
        "new_url_resolution_supported": False,
        "metric_tracking_status": "active" if tracking_active else "not_registered",
    }


def queue_tracked_video(
    conn: Any,
    *,
    kol_pool_id: int,
    content_url: Any,
    product_skus: Any,
    staff: dict[str, Any] | None,
) -> dict[str, Any]:
    """Link products and queue metrics for an already-owned video URL."""

    if int(kol_pool_id) <= 0:
        raise VideoTrackingError("kol_pool_id_invalid")
    actor_id = _assert_target_writable(
        conn,
        kol_pool_id=int(kol_pool_id),
        staff=staff,
    )
    normalized_url, platform = validate_supported_video_url(content_url)
    classified = classify_url(normalized_url)
    skus = normalize_product_skus(product_skus)
    evidence = _find_existing_evidence(
        conn,
        normalized_url=normalized_url,
        platform=platform,
        video_id=classified.video_id,
        kol_pool_id=int(kol_pool_id),
    )
    evidence = _validate_evidence(
        evidence,
        kol_pool_id=int(kol_pool_id),
        expected_platform=platform,
    )
    return _queue_for_evidence(
        conn,
        kol_pool_id=int(kol_pool_id),
        evidence=evidence,
        product_skus=skus,
        actor_id=actor_id,
        staff=staff,
    )


def queue_evidence_refresh(
    conn: Any,
    *,
    kol_pool_id: int,
    evidence_id: int,
    staff: dict[str, Any] | None,
    register_tracking: bool = True,
    refresh_source: str = TRACKING_SOURCE,
    queue_lane: str = "interactive",
) -> dict[str, Any]:
    """Queue a refresh for one existing evidence row with the same access gate."""

    if int(kol_pool_id) <= 0 or int(evidence_id) <= 0:
        raise VideoTrackingError("video_evidence_id_invalid")
    actor_id = _assert_target_writable(
        conn,
        kol_pool_id=int(kol_pool_id),
        staff=staff,
    )
    loaded = _load_evidence_by_id(conn, int(evidence_id))
    if not loaded:
        raise VideoTrackingError("video_evidence_not_found", 404)
    evidence = _validate_evidence(loaded, kol_pool_id=int(kol_pool_id))
    validate_supported_video_url(evidence.get("content_url"))
    return _queue_for_evidence(
        conn,
        kol_pool_id=int(kol_pool_id),
        evidence=evidence,
        product_skus=[],
        actor_id=actor_id,
        staff=staff,
        register_tracking=bool(register_tracking),
        refresh_source=refresh_source,
        queue_lane=queue_lane,
    )

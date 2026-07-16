"""Shopify organization-app Client Credentials grant and token lifecycle.

The module owns the short-lived Admin API token exchange and candidate connect
orchestration. Client secrets and access tokens are encrypted before storage
and are never returned. Refresh uses a committed fleet lease, so provider HTTP
never holds a PostgreSQL transaction open.
"""
from __future__ import annotations

import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains.commerce import shopify_connect


_AUTH_MODE = "client_credentials"
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_REFRESH_SKEW = timedelta(minutes=5)
_REFRESH_LOCK_KEY = "vkpi_shopify_client_credentials_refresh"
_REFRESH_LEASE_SECONDS = 60
_REFRESH_BACKOFF_BASE_SECONDS = 15
_REFRESH_BACKOFF_MAX_SECONDS = 300
_LOCAL_REFRESH_LOCK = threading.Lock()
logger = get_logger(__name__)


@dataclass(frozen=True)
class _TokenGrant:
    access_token: str
    granted_scopes: tuple[str, ...]
    expires_at: datetime
    refreshed_at: datetime


@dataclass(frozen=True)
class _TokenGrantResult:
    grant: _TokenGrant | None
    reason: str
    provider_rejected: bool = False


@dataclass(frozen=True)
class _ClientCredentialsCandidate:
    shop_domain: str
    client_id: str
    client_secret: str
    api_version: str


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_timestamp(value: Any) -> str | None:
    parsed = _as_utc(value)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z") if parsed else None


def token_is_fresh(creds: dict[str, Any], *, now: datetime | None = None) -> bool:
    token = str(creds.get("access_token") or "").strip()
    if not token:
        return False
    if str(creds.get("auth_mode") or "legacy_access_token") != _AUTH_MODE:
        return True
    expires_at = _as_utc(creds.get("access_token_expires_at"))
    return bool(expires_at and expires_at > (now or _now_utc()) + _REFRESH_SKEW)


def _validate_client_id(value: Any) -> str:
    client_id = str(value or "").strip()
    if not _CLIENT_ID_RE.fullmatch(client_id):
        raise ValueError("client_id must be 8-128 URL-safe characters")
    return client_id


def _validate_client_secret(value: Any) -> str:
    client_secret = str(value or "").strip()
    if len(client_secret) < 16 or len(client_secret) > 512:
        raise ValueError("client_secret must be 16-512 characters")
    return client_secret


def _validated_candidate(body: dict[str, Any] | None) -> _ClientCredentialsCandidate:
    payload = body or {}
    return _ClientCredentialsCandidate(
        shop_domain=shopify_connect._require_shop_domain(
            payload.get("shop_domain") or payload.get("shopDomain") or payload.get("shop")
        ),
        client_id=_validate_client_id(payload.get("client_id") or payload.get("clientId")),
        client_secret=_validate_client_secret(
            payload.get("client_secret") or payload.get("clientSecret")
        ),
        api_version=shopify_connect._require_api_version(
            payload.get("api_version") or payload.get("apiVersion")
        ),
    )


def _candidate_credentials(candidate: _ClientCredentialsCandidate) -> dict[str, Any]:
    return {
        "shop_domain": candidate.shop_domain,
        "auth_mode": _AUTH_MODE,
        "client_id": candidate.client_id,
        "client_secret": candidate.client_secret,
        "api_version": candidate.api_version,
    }


def _require_single_row_write(cursor: Any) -> None:
    """Fail closed when a runtime DML statement did not affect one row."""

    rowcount = getattr(cursor, "rowcount", None)
    if rowcount is not None and int(rowcount) != 1:
        raise RuntimeError("shopify credential write did not affect exactly one row")


def save_client_credentials(
    body: dict[str, Any],
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replace the singleton with organization-app credentials.

    Client Secret is encrypted into both the grant field and the existing
    webhook HMAC field so native webhook verification keeps one source of
    plaintext truth without ever returning it.
    """

    candidate = _validated_candidate(body)
    secret_encrypted = shopify_connect._encrypt(candidate.client_secret)
    now = shopify_connect._utcnow()
    actor = shopify_connect._actor(staff)

    conn = get_conn()
    cursor = conn.execute(
        """
        INSERT INTO vkpi_shopify_credentials
            (id, shop_domain, access_token_encrypted, webhook_secret_encrypted,
             api_version, status, connected_at, updated_at, updated_by_staff_id,
             auth_mode, client_id, client_secret_encrypted, access_token_expires_at,
             granted_scopes, last_refresh_at, revoked_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            shop_domain=excluded.shop_domain,
            access_token_encrypted=excluded.access_token_encrypted,
            webhook_secret_encrypted=excluded.webhook_secret_encrypted,
            api_version=excluded.api_version,
            status=excluded.status,
            connected_at=excluded.connected_at,
            updated_at=excluded.updated_at,
            updated_by_staff_id=excluded.updated_by_staff_id,
            auth_mode=excluded.auth_mode,
            client_id=excluded.client_id,
            client_secret_encrypted=excluded.client_secret_encrypted,
            access_token_expires_at=excluded.access_token_expires_at,
            granted_scopes=excluded.granted_scopes,
            last_refresh_at=excluded.last_refresh_at,
            revoked_at=excluded.revoked_at,
            refresh_lease_owner=NULL, refresh_lease_expires_at=NULL,
            refresh_retry_after=NULL, refresh_failure_count=0
        """,
        (
            shopify_connect._CREDS_SINGLETON_ID,
            candidate.shop_domain,
            "",
            secret_encrypted,
            candidate.api_version,
            "pending",
            None,
            now,
            actor,
            _AUTH_MODE,
            candidate.client_id,
            secret_encrypted,
            None,
            "",
            None,
            None,
        ),
    )
    _require_single_row_write(cursor)
    conn.commit()
    return {
        "ok": True,
        "shop_domain": candidate.shop_domain,
        "auth_mode": _AUTH_MODE,
        "client_id_configured": True,
        "client_secret_configured": True,
        "webhook_secret_configured": True,
        "token_configured": False,
        "status": "pending",
        "source": "db",
    }


def _token_endpoint(shop_domain: Any) -> str:
    domain = shopify_connect._require_shop_domain(shop_domain)
    return f"https://{domain}/admin/oauth/access_token"


def _request_token(creds: dict[str, Any], *, now: datetime) -> _TokenGrantResult:
    client_id = str(creds.get("client_id") or "").strip()
    client_secret = str(creds.get("client_secret") or "").strip()
    try:
        endpoint = _token_endpoint(creds.get("shop_domain"))
    except ValueError:
        return _TokenGrantResult(None, "invalid_shop_domain")
    if not client_id or not client_secret:
        return _TokenGrantResult(None, "client_credentials_missing")

    try:
        with httpx.Client(timeout=20) as client:
            response = client.post(
                endpoint,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            if response.status_code < 200 or response.status_code >= 300:
                rejected = response.status_code in {400, 401, 403}
                return _TokenGrantResult(
                    None,
                    "provider_rejected_credentials" if rejected else "provider_unreachable",
                    provider_rejected=rejected,
                )
            payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return _TokenGrantResult(None, "provider_unreachable")

    if not isinstance(payload, dict):
        return _TokenGrantResult(None, "provider_token_payload_invalid")
    token = str(payload.get("access_token") or "").strip()
    try:
        expires_in = int(payload.get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0
    if not token or expires_in <= 0 or expires_in > 172800:
        return _TokenGrantResult(None, "provider_token_payload_invalid")
    scopes = tuple(shopify_connect._scope_list(payload.get("scope")))
    return _TokenGrantResult(
        _TokenGrant(
            access_token=token,
            granted_scopes=scopes,
            expires_at=now + timedelta(seconds=expires_in),
            refreshed_at=now,
        ),
        "",
    )


def _acquire_postgres_singleflight(conn: Any) -> bool:
    """Serialize the final singleton replacement transaction only.

    External Shopify HTTP must happen before this transaction-level lock.
    Token refresh uses a committed row lease instead.
    """
    if not is_postgres_runtime():
        return False
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext(?))",
        (_REFRESH_LOCK_KEY,),
    )
    return True


def _retry_after_active(creds: dict[str, Any], *, now: datetime) -> bool:
    retry_after = _as_utc(creds.get("refresh_retry_after"))
    return bool(retry_after and retry_after > now)


def _claim_refresh_lease(
    conn: Any,
    *,
    owner: str,
    now: datetime,
) -> tuple[bool, dict[str, Any]]:
    """Claim a short fleet lease and commit before any provider HTTP."""

    expires_at = now + timedelta(seconds=_REFRESH_LEASE_SECONDS)
    cursor = conn.execute(
        """
        UPDATE vkpi_shopify_credentials
        SET refresh_lease_owner=?, refresh_lease_expires_at=?
        WHERE id=? AND auth_mode='client_credentials'
          AND (refresh_lease_owner IS NULL OR refresh_lease_owner='' OR refresh_lease_expires_at<=?)
          AND (refresh_retry_after IS NULL OR refresh_retry_after<=?)
        """,
        (
            owner,
            iso_timestamp(expires_at),
            shopify_connect._CREDS_SINGLETON_ID,
            iso_timestamp(now),
            iso_timestamp(now),
        ),
    )
    rowcount = getattr(cursor, "rowcount", None)
    acquired = rowcount is None or int(rowcount) == 1
    conn.commit()
    row = shopify_connect._load_row(conn)
    return acquired, shopify_connect._credentials_from_row(row) if row else {}


def _persist_grant(conn: Any, grant: _TokenGrant, *, lease_owner: str = "") -> None:
    encrypted_token = shopify_connect._encrypt(grant.access_token)
    refreshed_at = iso_timestamp(grant.refreshed_at)
    cursor = conn.execute(
        """
        UPDATE vkpi_shopify_credentials
        SET access_token_encrypted=?, access_token_expires_at=?, granted_scopes=?,
            last_refresh_at=?, revoked_at=NULL,
            status=CASE WHEN status IN ('error','revoked') THEN 'pending' ELSE status END,
            refresh_lease_owner=NULL, refresh_lease_expires_at=NULL,
            refresh_retry_after=NULL, refresh_failure_count=0, updated_at=?
        WHERE id=? AND auth_mode='client_credentials'
          AND (?='' OR refresh_lease_owner=?)
        """,
        (
            encrypted_token,
            iso_timestamp(grant.expires_at),
            ",".join(grant.granted_scopes),
            refreshed_at,
            refreshed_at,
            shopify_connect._CREDS_SINGLETON_ID,
            lease_owner,
            lease_owner,
        ),
    )
    _require_single_row_write(cursor)
    conn.commit()


def _persist_refresh_failure(
    conn: Any,
    *,
    reason: str,
    provider_rejected: bool,
    now: datetime,
    lease_owner: str = "",
    failure_count: int = 0,
) -> tuple[str, str | None]:
    status = "revoked" if provider_rejected else "error"
    timestamp = iso_timestamp(now)
    retry_at = None
    if not provider_rejected:
        backoff_seconds = min(
            _REFRESH_BACKOFF_MAX_SECONDS,
            _REFRESH_BACKOFF_BASE_SECONDS * (2 ** min(max(0, failure_count), 5)),
        )
        retry_at = iso_timestamp(now + timedelta(seconds=backoff_seconds))
    cursor = conn.execute(
        """
        UPDATE vkpi_shopify_credentials
        SET status=?, connected_at=NULL, revoked_at=?,
            access_token_encrypted=CASE WHEN ?='revoked' THEN '' ELSE access_token_encrypted END,
            access_token_expires_at=CASE WHEN ?='revoked' THEN NULL ELSE access_token_expires_at END,
            refresh_lease_owner=NULL, refresh_lease_expires_at=NULL,
            refresh_retry_after=?, refresh_failure_count=refresh_failure_count+1,
            updated_at=?
        WHERE id=? AND auth_mode='client_credentials'
          AND (?='' OR refresh_lease_owner=?)
        """,
        (
            status,
            timestamp if provider_rejected else None,
            status,
            status,
            retry_at,
            timestamp,
            shopify_connect._CREDS_SINGLETON_ID,
            lease_owner,
            lease_owner,
        ),
    )
    _require_single_row_write(cursor)
    conn.commit()
    return reason, retry_at


def _safe_refresh_result(
    *,
    ok: bool,
    status: str,
    reason: str = "",
    creds: dict[str, Any] | None = None,
    grant: _TokenGrant | None = None,
    reused: bool = False,
    retry_at: str | None = None,
) -> dict[str, Any]:
    current = creds or {}
    return {
        "ok": ok,
        "status": status,
        "reason": reason or None,
        "auth_mode": _AUTH_MODE,
        "shop_domain": str(current.get("shop_domain") or ""),
        "token_configured": bool(grant or current.get("access_token")),
        "access_token_expires_at": iso_timestamp(
            grant.expires_at if grant else current.get("access_token_expires_at")
        ),
        "granted_scopes": list(
            grant.granted_scopes if grant else current.get("granted_scopes") or []
        ),
        "last_refresh_at": iso_timestamp(
            grant.refreshed_at if grant else current.get("last_refresh_at")
        ),
        "reused": reused,
        "retry_at": retry_at or iso_timestamp(current.get("refresh_retry_after")),
    }


def refresh_access_token(
    *,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh once across the process and PostgreSQL fleet.

    ``force`` bypasses only the caller's initial freshness check. Provider
    backoff and another owner's active lease are never bypassed. The fleet
    lease is committed before HTTP, so no database transaction stays open
    during the 20-second provider timeout.
    """

    current_time = (now or _now_utc()).astimezone(timezone.utc)
    initial = shopify_connect.get_credentials()
    if str(initial.get("auth_mode") or "") != _AUTH_MODE:
        return _safe_refresh_result(
            ok=False,
            status="not_configured",
            reason="client_credentials_not_configured",
            creds=initial,
        )
    if str(initial.get("status") or "") == "revoked":
        return _safe_refresh_result(
            ok=False,
            status="revoked",
            reason="provider_rejected_credentials",
            creds=initial,
        )
    if _retry_after_active(initial, now=current_time):
        return _safe_refresh_result(
            ok=False,
            status="error",
            reason="provider_backoff_active",
            creds=initial,
        )
    observed_token = str(initial.get("access_token") or "")
    observed_refresh = iso_timestamp(initial.get("last_refresh_at"))
    if not force and token_is_fresh(initial, now=current_time):
        return _safe_refresh_result(ok=True, status="configured", creds=initial, reused=True)

    with _LOCAL_REFRESH_LOCK:
        conn = get_conn()
        lease_owner = secrets.token_urlsafe(24)
        try:
            row = shopify_connect._load_row(conn)
            locked_creds = shopify_connect._credentials_from_row(row) if row else {}
            if str(locked_creds.get("auth_mode") or "") != _AUTH_MODE:
                return _safe_refresh_result(
                    ok=False,
                    status="not_configured",
                    reason="client_credentials_not_configured",
                    creds=locked_creds,
                )
            if str(locked_creds.get("status") or "") == "revoked":
                return _safe_refresh_result(
                    ok=False,
                    status="revoked",
                    reason="provider_rejected_credentials",
                    creds=locked_creds,
                )
            if _retry_after_active(locked_creds, now=current_time):
                return _safe_refresh_result(
                    ok=False,
                    status="error",
                    reason="provider_backoff_active",
                    creds=locked_creds,
                )
            refreshed_by_waiter = (
                str(locked_creds.get("access_token") or "") != observed_token
                or iso_timestamp(locked_creds.get("last_refresh_at")) != observed_refresh
            )
            if token_is_fresh(locked_creds, now=current_time) and (not force or refreshed_by_waiter):
                return _safe_refresh_result(
                    ok=True,
                    status="configured",
                    creds=locked_creds,
                    reused=True,
                )

            acquired, claimed_creds = _claim_refresh_lease(
                conn,
                owner=lease_owner,
                now=current_time,
            )
            if not acquired:
                if _retry_after_active(claimed_creds, now=current_time):
                    return _safe_refresh_result(
                        ok=False,
                        status="error",
                        reason="provider_backoff_active",
                        creds=claimed_creds,
                    )
                return _safe_refresh_result(
                    ok=False,
                    status="pending",
                    reason="refresh_in_progress",
                    creds=claimed_creds,
                    retry_at=iso_timestamp(
                        claimed_creds.get("refresh_lease_expires_at")
                    ),
                )

            provider_creds = claimed_creds or locked_creds
            grant_result = _request_token(provider_creds, now=current_time)
            if grant_result.grant is None:
                _, retry_at = _persist_refresh_failure(
                    conn,
                    reason=grant_result.reason,
                    provider_rejected=grant_result.provider_rejected,
                    now=current_time,
                    lease_owner=lease_owner,
                    failure_count=int(provider_creds.get("refresh_failure_count") or 0),
                )
                return _safe_refresh_result(
                    ok=False,
                    status="revoked" if grant_result.provider_rejected else "error",
                    reason=grant_result.reason,
                    creds=provider_creds,
                    retry_at=retry_at,
                )

            _persist_grant(conn, grant_result.grant, lease_owner=lease_owner)
            return _safe_refresh_result(
                ok=True,
                status="configured",
                creds=provider_creds,
                grant=grant_result.grant,
            )
        except Exception:
            try:
                conn.rollback()
            except Exception:
                logger.debug("shopify.client_credentials.refresh_rollback_failed", exc_info=True)
            return _safe_refresh_result(
                ok=False,
                status="error",
                reason="token_refresh_internal_error",
                creds=initial,
            )


def credentials_with_fresh_token() -> dict[str, Any]:
    creds = shopify_connect.get_credentials()
    if str(creds.get("auth_mode") or "legacy_access_token") != _AUTH_MODE:
        return creds
    if token_is_fresh(creds):
        return creds
    result = refresh_access_token()
    refreshed = shopify_connect.get_credentials()
    if not result.get("ok"):
        refreshed["token_refresh_reason"] = str(result.get("reason") or "token_refresh_failed")
        refreshed["access_token"] = ""
    return refreshed


def _persist_candidate_and_grant(
    conn: Any,
    candidate: _ClientCredentialsCandidate,
    grant: _TokenGrant,
    staff: dict[str, Any] | None,
    *,
    verified_at: str,
) -> None:
    """Atomically replace credentials only after all external stages passed."""

    secret_encrypted = shopify_connect._encrypt(candidate.client_secret)
    token_encrypted = shopify_connect._encrypt(grant.access_token)
    refreshed_at = iso_timestamp(grant.refreshed_at)
    cursor = conn.execute(
        """
        INSERT INTO vkpi_shopify_credentials
            (id, shop_domain, access_token_encrypted, webhook_secret_encrypted,
             api_version, status, connected_at, updated_at, updated_by_staff_id,
             auth_mode, client_id, client_secret_encrypted, access_token_expires_at,
             granted_scopes, last_refresh_at, revoked_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            shop_domain=excluded.shop_domain,
            access_token_encrypted=excluded.access_token_encrypted,
            webhook_secret_encrypted=excluded.webhook_secret_encrypted,
            api_version=excluded.api_version,
            status=excluded.status,
            connected_at=excluded.connected_at,
            updated_at=excluded.updated_at,
            updated_by_staff_id=excluded.updated_by_staff_id,
            auth_mode=excluded.auth_mode,
            client_id=excluded.client_id,
            client_secret_encrypted=excluded.client_secret_encrypted,
            access_token_expires_at=excluded.access_token_expires_at,
            granted_scopes=excluded.granted_scopes,
            last_refresh_at=excluded.last_refresh_at,
            revoked_at=excluded.revoked_at,
            refresh_lease_owner=NULL, refresh_lease_expires_at=NULL,
            refresh_retry_after=NULL, refresh_failure_count=0
        """,
        (
            shopify_connect._CREDS_SINGLETON_ID,
            candidate.shop_domain,
            token_encrypted,
            secret_encrypted,
            candidate.api_version,
            "connected",
            verified_at,
            refreshed_at,
            shopify_connect._actor(staff),
            _AUTH_MODE,
            candidate.client_id,
            secret_encrypted,
            iso_timestamp(grant.expires_at),
            ",".join(grant.granted_scopes),
            refreshed_at,
            None,
        ),
    )
    _require_single_row_write(cursor)


def connect_client_credentials(
    body: dict[str, Any],
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run token, identity and webhook stages before one atomic replacement."""

    candidate = _validated_candidate(body)
    # Check before the token/probe/webhook stages. A missing production key
    # must not create provider-side subscriptions that cannot be committed.
    shopify_connect.require_credentials_encryption_ready()
    candidate_creds = _candidate_credentials(candidate)
    now = _now_utc()
    phases: dict[str, dict[str, Any]] = {
        "authorization": {"status": "pending"},
        "probe": {"status": "pending"},
        "webhooks": {"status": "pending"},
        "commit": {"status": "pending"},
    }

    with _LOCAL_REFRESH_LOCK:
        grant_result = _request_token(candidate_creds, now=now)
        if grant_result.grant is None:
            phases["authorization"] = {
                "status": "error",
                "reason": grant_result.reason,
            }
            return {
                **_safe_refresh_result(
                    ok=False,
                    status="error",
                    reason=grant_result.reason,
                    creds=candidate_creds,
                ),
                "client_id_configured": False,
                "client_secret_configured": False,
                "webhook_secret_configured": False,
                "preserved_existing": True,
                "phases": phases,
            }

        phases["authorization"] = {
            "status": "success",
            "expires_at": iso_timestamp(grant_result.grant.expires_at),
            "scope_count": len(grant_result.grant.granted_scopes),
        }
        live_candidate = {
            **candidate_creds,
            "access_token": grant_result.grant.access_token,
            "webhook_secret": candidate.client_secret,
        }

        probe = shopify_connect._probe_credentials(live_candidate)
        if not probe.get("ok"):
            phases["probe"] = {
                "status": "error",
                "reason": str(probe.get("reason") or "provider_probe_failed"),
            }
            return {
                **_safe_refresh_result(
                    ok=False,
                    status="error",
                    reason=str(probe.get("reason") or "provider_probe_failed"),
                    creds=candidate_creds,
                ),
                "client_id_configured": False,
                "client_secret_configured": False,
                "webhook_secret_configured": False,
                "preserved_existing": True,
                "phases": phases,
            }
        verified_at = str(probe.get("verified_at") or iso_timestamp(now) or "")
        phases["probe"] = {
            "status": "success",
            "verified_at": verified_at,
            "shop_identity_verified": True,
        }

        webhook_result = shopify_connect._register_webhooks_with_credentials(live_candidate)
        if not webhook_result.get("ok"):
            reason = str(webhook_result.get("reason") or "provider_webhook_error")
            phases["webhooks"] = {
                "status": "error",
                "reason": reason,
                "registered_count": int(webhook_result.get("registered_count") or 0),
                "required_count": int(webhook_result.get("required_count") or 0),
                "cleanup": dict(webhook_result.get("cleanup") or {}),
            }
            return {
                **_safe_refresh_result(
                    ok=False,
                    status="error",
                    reason=reason,
                    creds=candidate_creds,
                ),
                "client_id_configured": False,
                "client_secret_configured": False,
                "webhook_secret_configured": False,
                "preserved_existing": True,
                "phases": phases,
            }
        phases["webhooks"] = {
            "status": "success",
            "registered_count": int(webhook_result.get("registered_count") or 0),
            "required_count": int(webhook_result.get("required_count") or 0),
        }

        conn = get_conn()
        try:
            _acquire_postgres_singleflight(conn)
            _persist_candidate_and_grant(
                conn,
                candidate,
                grant_result.grant,
                staff,
                verified_at=verified_at,
            )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                logger.debug("shopify.client_credentials.connect_rollback_failed", exc_info=True)
            cleanup = shopify_connect._cleanup_webhooks(
                live_candidate,
                list(webhook_result.get("registered") or []),
            )
            phases["commit"] = {
                "status": "error",
                "reason": "credential_persist_failed",
                "cleanup": cleanup,
            }
            return {
                **_safe_refresh_result(
                    ok=False,
                    status="error",
                    reason="credential_persist_failed",
                    creds=candidate_creds,
                ),
                "client_id_configured": False,
                "client_secret_configured": False,
                "webhook_secret_configured": False,
                "preserved_existing": True,
                "phases": phases,
            }

    phases["commit"] = {"status": "success", "activated_at": verified_at}

    return {
        **_safe_refresh_result(
            ok=True,
            status="connected",
            creds=candidate_creds,
            grant=grant_result.grant,
        ),
        "client_id_configured": True,
        "client_secret_configured": True,
        "webhook_secret_configured": True,
        "preserved_existing": False,
        "verified_at": verified_at,
        "registered_count": int(webhook_result.get("registered_count") or 0),
        "phases": phases,
    }


__all__ = [
    "connect_client_credentials",
    "credentials_with_fresh_token",
    "iso_timestamp",
    "refresh_access_token",
    "save_client_credentials",
    "token_is_fresh",
]

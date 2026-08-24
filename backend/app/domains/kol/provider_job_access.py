"""Durable actor/session/target fences for KOL provider jobs.

The HTTP request authorization that creates a queue row is not durable: an
operator can be suspended, lose ``vkpi:write``, transfer a search session, or
the queued query/URL can be edited before a worker claims the row.  This module
seals the immutable execution contract at enqueue time and revalidates the
live actor and session immediately before provider-capable code runs.

The server-owned branch is deliberately not a JSON flag.  Internal callers
must hold a :class:`ServerOwnedProviderCapability` minted in-process; the
persisted claim is signed so a copied/edited queue payload fails closed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any

from app.core.config import IS_PRODUCTION, JWT_SECRET
from app.core.permissions import check_tab_permission
from app.core.security import user_status_allows_auth
from app.db.connection import db_connection_sync_scope, get_conn
from app.domains.kol.provider_job_payloads import provider_job_payload_delta


FENCE_KEY = "kol_provider_job_fence"
FENCE_VERSION = 1
FENCE_RESULT_KEY = "kol_provider_job_fence_result"

SESSION_ADVANCE = "session_advance"
SMART_SEARCH_PROFILE_ADVANCE = "smart_search_profile_advance"
VIDEO_URL_RESOLVE = "video_url_resolve"
VIDEO_ANALYSIS = "video_analysis"
CONTENT_FIT_ANALYSIS = "content_fit_analysis"
SUPPORTED_ACTIONS = frozenset(
    {
        CONTENT_FIT_ANALYSIS,
        SESSION_ADVANCE,
        SMART_SEARCH_PROFILE_ADVANCE,
        VIDEO_ANALYSIS,
        VIDEO_URL_RESOLVE,
    }
)
# Search-session linkage/progress is appended after enqueue and result fields
# are appended while a job is running.  They do not authorize provider scope.
# Every other queued field is sealed, including all query/filter/model knobs.
_MUTABLE_RUNTIME_KEYS = frozenset(
    {
        FENCE_KEY,
        FENCE_RESULT_KEY,
        "job_id",
        "_llm_execution",
        "search_session_lineage",
        "search_session_item_id",
        "search_session_item_status",
        "search_session_stage",
        "video_url_resolution",
        "video_url_resolve_result",
        "session_advance_result",
        "smart_search_profile_advance_result", "search_session_last_job_status",
        "search_session_last_error", "diagnostics", "search_session_item_statuses",
        "search_session_cache_id", "search_session_analysis_status", "my_kol_paid_action_fence",
    }
)


class ProviderJobAccessError(RuntimeError):
    """Stable terminal error for a durable provider authorization failure."""

    def __init__(self, code: str, status_code: int = 403):
        super().__init__(code)
        self.code = str(code)
        self.status_code = int(status_code)


@dataclass(frozen=True)
class ServerOwnedProviderCapability:
    """Opaque process-issued capability; HTTP dictionaries are never accepted."""

    action: str
    target_id: str
    search_session_id: int | None
    signature: str


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _canonical(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _signing_secret() -> bytes:
    explicit = _text(os.environ.get("VKPI_PROVIDER_JOB_FENCE_SECRET"))
    if explicit:
        return explicit.encode("utf-8")
    if IS_PRODUCTION:
        # Production already requires JWT_SECRET at config import time.  A
        # dedicated secret may be rotated independently via the env above.
        return _text(JWT_SECRET).encode("utf-8")
    # Local web/worker processes must share a restart-stable development key;
    # config's randomly generated local JWT secret is intentionally per-process.
    return b"vkpi-local-provider-job-fence-v1-development-only"


def _signature(value: Any) -> str:
    return hmac.new(
        _signing_secret(),
        _canonical(value).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _signed_claim(claim: dict[str, Any]) -> dict[str, Any]:
    unsigned = {key: value for key, value in claim.items() if key != "signature"}
    return {**unsigned, "signature": _signature(unsigned)}


def _verify_signed_claim(claim: dict[str, Any]) -> bool:
    supplied = _text(claim.get("signature"))
    unsigned = {key: value for key, value in claim.items() if key != "signature"}
    return bool(supplied) and hmac.compare_digest(supplied, _signature(unsigned))


def _actor_ids(staff: dict[str, Any] | None) -> tuple[int, int]:
    context = staff or {}
    staff_id = _int(context.get("staff_id") or context.get("id"))
    # Real request contexts always carry user_id.  The id fallback preserves
    # legacy internal callers while the worker still resolves staff->user.
    user_id = _int(context.get("user_id") or context.get("id"))
    return staff_id, user_id


def _session_binding(
    session: dict[str, Any],
    *,
    fallback_owner_user_id: int,
    bind_query: bool,
    fallback_query_text: str = "",
    fallback_query_type: str = "unknown",
    fallback_input_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session_id = _int(session.get("id"))
    owner_user_id = _int(session.get("created_by")) or int(fallback_owner_user_id)
    binding: dict[str, Any] = {
        "search_session_id": session_id,
        "owner_user_id": owner_user_id,
        "bind_query": bool(bind_query),
    }
    if bind_query:
        input_payload = session.get("input_payload")
        if not isinstance(input_payload, dict):
            input_payload = dict(fallback_input_payload or {})
        binding.update(
            {
                "query_text": _text(session.get("query_text") or fallback_query_text),
                "query_type": _text(
                    session.get("query_type") or fallback_query_type or "unknown"
                ).lower(),
                "input_payload_fingerprint": _digest(input_payload),
            }
        )
    return binding


def _strict_video_identity(payload: dict[str, Any]) -> dict[str, str]:
    # Lazy import avoids the url_deep_crawl -> video_url_resolver re-export
    # cycle during application startup.
    from app.domains.kol.url_deep_crawl import (
        CN_VIDEO_ANALYSIS_PLATFORMS,
        SUPPORTED_PLATFORMS,
        classify_url,
    )

    url = _text(payload.get("url"))
    source_url = _text(payload.get("source_url"))
    if not url or not source_url:
        raise ProviderJobAccessError("video_url_identity_drifted", 409)
    try:
        first = classify_url(url)
        second = classify_url(source_url)
    except Exception as exc:
        raise ProviderJobAccessError("video_url_identity_drifted", 409) from exc
    supported = SUPPORTED_PLATFORMS | CN_VIDEO_ANALYSIS_PLATFORMS
    if (
        first.url_type != "video"
        or second.url_type != "video"
        or first.platform not in supported
        or first.platform != second.platform
        or first.video_id != second.video_id
        or first.normalized_url != second.normalized_url
    ):
        raise ProviderJobAccessError("video_url_identity_drifted", 409)
    target_id = f"{first.platform}:{first.video_id}"
    if (
        _text(payload.get("platform")).lower() != first.platform
        or _text(payload.get("video_id")) != first.video_id
        or _text(payload.get("target_type")).lower() != "video_url"
        or _text(payload.get("target_id")) != target_id
    ):
        raise ProviderJobAccessError("video_url_identity_drifted", 409)
    return {
        "normalized_url": first.normalized_url,
        "platform": first.platform,
        "video_id": first.video_id,
        "target_id": target_id,
    }


def _video_evidence_binding(evidence: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical durable identity for one pool-owned video row."""

    from app.domains.kol.video_url_identity import (
        VideoUrlIdentityError,
        parse_supported_video_url,
    )

    evidence_id = _int(evidence.get("evidence_id") or evidence.get("id"))
    kol_pool_id = _int(evidence.get("kol_pool_id"))
    if evidence_id <= 0 or kol_pool_id <= 0:
        raise ProviderJobAccessError("video_analysis_target_invalid", 409)
    try:
        identity = parse_supported_video_url(_text(evidence.get("content_url")))
    except VideoUrlIdentityError as exc:
        raise ProviderJobAccessError("video_analysis_evidence_identity_invalid", 409) from exc
    return {
        "evidence_id": evidence_id,
        "kol_pool_id": kol_pool_id,
        "platform": identity.platform,
        "video_id": identity.video_id,
        "normalized_url": identity.normalized_url,
    }


def _execution_contract(payload: dict[str, Any], *, action: str) -> dict[str, Any]:
    if action not in SUPPORTED_ACTIONS:
        raise ProviderJobAccessError("provider_job_action_unsupported", 403)
    immutable = {
        key: value
        for key, value in payload.items()
        if key not in _MUTABLE_RUNTIME_KEYS
    }
    contract: dict[str, Any] = {
        "action": action,
        "payload": immutable,
    }
    if action == VIDEO_URL_RESOLVE:
        contract["video_identity"] = _strict_video_identity(payload)
    return contract


def issue_server_owned_provider_capability(
    *,
    action: str,
    target_id: str,
    search_session_id: int | None = None,
) -> ServerOwnedProviderCapability:
    """Mint an explicit internal-only capability for a server-owned job."""

    action_text = _text(action).lower()
    if action_text not in SUPPORTED_ACTIONS:
        raise ProviderJobAccessError("provider_job_action_unsupported", 403)
    claims = {
        "version": FENCE_VERSION,
        "mode": "server_owned",
        "action": action_text,
        "target_id": _text(target_id),
        "search_session_id": _int(search_session_id) or None,
    }
    return ServerOwnedProviderCapability(
        action=action_text,
        target_id=_text(target_id),
        search_session_id=_int(search_session_id) or None,
        signature=_signature(claims),
    )


def _valid_server_capability(
    capability: ServerOwnedProviderCapability | None,
    *,
    action: str,
    target_id: str,
    search_session_id: int | None,
) -> bool:
    if not isinstance(capability, ServerOwnedProviderCapability):
        return False
    claims = {
        "version": FENCE_VERSION,
        "mode": "server_owned",
        "action": _text(capability.action).lower(),
        "target_id": _text(capability.target_id),
        "search_session_id": _int(capability.search_session_id) or None,
    }
    return bool(
        claims["action"] == _text(action).lower()
        and claims["target_id"] == _text(target_id)
        and claims["search_session_id"] == (_int(search_session_id) or None)
        and hmac.compare_digest(_text(capability.signature), _signature(claims))
    )


def build_search_session_provider_fence(
    *,
    action: str,
    session: dict[str, Any],
    payload: dict[str, Any],
    staff: dict[str, Any] | None,
    fallback_query_text: str = "",
    fallback_query_type: str = "unknown",
    fallback_input_payload: dict[str, Any] | None = None,
    server_owned_capability: ServerOwnedProviderCapability | None = None,
) -> dict[str, Any]:
    """Bind one search-session job to actor, owner and query/filter state."""

    action_text = _text(action).lower()
    if action_text not in {SESSION_ADVANCE, SMART_SEARCH_PROFILE_ADVANCE}:
        raise ProviderJobAccessError("provider_job_action_unsupported", 403)
    session_id = _int(session.get("id") or payload.get("search_session_id"))
    target_id = _text(payload.get("target_id") or session_id)
    staff_id, user_id = _actor_ids(staff)
    server_owned = _valid_server_capability(
        server_owned_capability,
        action=action_text,
        target_id=target_id,
        search_session_id=session_id,
    )
    if not server_owned and (staff_id <= 0 or user_id <= 0):
        raise ProviderJobAccessError("provider_job_actor_required", 403)
    if not server_owned and any(
        key in (staff or {}) for key in ("role", "permissions", "permissions_json")
    ) and not check_tab_permission(staff or {}, "vkpi", "write"):
        raise ProviderJobAccessError("vkpi_write_permission_required", 403)
    binding = _session_binding(
        session,
        fallback_owner_user_id=0 if server_owned else user_id,
        bind_query=True,
        fallback_query_text=fallback_query_text,
        fallback_query_type=fallback_query_type,
        fallback_input_payload=fallback_input_payload,
    )
    if session_id <= 0 or binding["search_session_id"] != session_id:
        raise ProviderJobAccessError("search_session_target_invalid", 409)
    if server_owned:
        if _int(binding.get("owner_user_id")) > 0:
            raise ProviderJobAccessError("server_owned_session_has_user_owner", 403)
    elif _int(binding.get("owner_user_id")) != user_id:
        raise ProviderJobAccessError("search_session_owner_mismatch", 403)
    claim = {
        "version": FENCE_VERSION,
        "mode": "server_owned" if server_owned else "user",
        "action": action_text,
        "target_id": target_id,
        "actor": {
            "staff_id": None if server_owned else staff_id,
            "user_id": None if server_owned else user_id,
        },
        "session": binding,
        "execution_fingerprint": _digest(
            _execution_contract(payload, action=action_text)
        ),
    }
    return _signed_claim(claim)


def build_video_url_provider_fence(
    *,
    payload: dict[str, Any],
    staff: dict[str, Any] | None,
    server_owned_capability: ServerOwnedProviderCapability | None = None,
) -> dict[str, Any]:
    """Bind a resolver job to actor, optional session owner and native URL id."""

    identity = _strict_video_identity(payload)
    session_id = _int(payload.get("search_session_id")) or None
    staff_id, user_id = _actor_ids(staff)
    server_owned = _valid_server_capability(
        server_owned_capability,
        action=VIDEO_URL_RESOLVE,
        target_id=identity["target_id"],
        search_session_id=session_id,
    )
    if not server_owned and (staff_id <= 0 or user_id <= 0):
        raise ProviderJobAccessError("provider_job_actor_required", 403)
    if not server_owned and any(
        key in (staff or {}) for key in ("role", "permissions", "permissions_json")
    ) and not check_tab_permission(staff or {}, "vkpi", "write"):
        raise ProviderJobAccessError("vkpi_write_permission_required", 403)
    session_binding = {
        "search_session_id": session_id,
        "owner_user_id": None if server_owned else user_id,
        # URL jobs bind the native video identity in the execution contract.
        # Session query text may preserve the user's pre-normalized URL.
        "bind_query": False,
    }
    claim = {
        "version": FENCE_VERSION,
        "mode": "server_owned" if server_owned else "user",
        "action": VIDEO_URL_RESOLVE,
        "target_id": identity["target_id"],
        "actor": {
            "staff_id": None if server_owned else staff_id,
            "user_id": None if server_owned else user_id,
        },
        "session": session_binding,
        "execution_fingerprint": _digest(
            _execution_contract(payload, action=VIDEO_URL_RESOLVE)
        ),
    }
    return _signed_claim(claim)


def build_content_fit_provider_fence(
    *,
    payload: dict[str, Any],
    session: dict[str, Any] | None,
    staff: dict[str, Any] | None = None,
    server_owned_capability: ServerOwnedProviderCapability | None = None,
) -> dict[str, Any]:
    """Seal one internally delegated content-fit job.

    A user-owned search inherits the freshly revalidated live actor.  A true
    system session instead presents an opaque in-process capability; a JSON
    ``server_owned`` flag is never sufficient.
    """

    target_id = _text(payload.get("target_id") or payload.get("kol_pool_id"))
    session_id = _int(payload.get("search_session_id")) or None
    if not target_id:
        raise ProviderJobAccessError("content_fit_target_invalid", 409)
    server_owned = _valid_server_capability(
        server_owned_capability,
        action=CONTENT_FIT_ANALYSIS,
        target_id=target_id,
        search_session_id=session_id,
    )
    if server_owned and session_id:
        raise ProviderJobAccessError("server_owned_session_must_be_root", 403)
    staff_id, user_id = _actor_ids(staff)
    if not server_owned and (staff_id <= 0 or user_id <= 0):
        raise ProviderJobAccessError("provider_job_actor_required", 403)
    if not server_owned and not check_tab_permission(staff or {}, "vkpi", "write"):
        raise ProviderJobAccessError("vkpi_write_permission_required", 403)
    source_session = dict(session or {})
    if session_id and _int(source_session.get("id")) != session_id:
        raise ProviderJobAccessError("search_session_target_invalid", 409)
    current_owner = _int(source_session.get("created_by"))
    if session_id:
        if server_owned and (
            "created_by" not in source_session
            or source_session.get("created_by") is not None
        ):
            raise ProviderJobAccessError("server_owned_session_has_user_owner", 403)
        if not server_owned and current_owner != user_id:
            raise ProviderJobAccessError("search_session_owner_mismatch", 403)
    session_binding = _session_binding(
        source_session,
        fallback_owner_user_id=0 if server_owned else user_id,
        bind_query=True,
    ) if session_id else {
        "search_session_id": 0,
        "owner_user_id": 0,
        "bind_query": False,
    }
    claim = {
        "version": FENCE_VERSION,
        "mode": "server_owned" if server_owned else "user",
        "action": CONTENT_FIT_ANALYSIS,
        "target_id": target_id,
        "actor": {
            "staff_id": None if server_owned else staff_id,
            "user_id": None if server_owned else user_id,
        },
        "session": session_binding,
        "execution_fingerprint": _digest(
            _execution_contract(payload, action=CONTENT_FIT_ANALYSIS)
        ),
    }
    return _signed_claim(claim)


def build_video_analysis_provider_fence(
    *,
    payload: dict[str, Any],
    evidence: dict[str, Any],
    session: dict[str, Any] | None,
    staff: dict[str, Any] | None = None,
    server_owned_capability: ServerOwnedProviderCapability | None = None,
) -> dict[str, Any]:
    """Seal a final-v1 child to its live actor, session, KOL and evidence."""

    binding = _video_evidence_binding(evidence)
    target_id = _text(payload.get("target_id"))
    session_id = _int(payload.get("search_session_id")) or None
    if (
        _text(payload.get("target_type")).lower() != "video"
        or target_id != str(binding["evidence_id"])
        or _int(payload.get("kol_pool_id")) != binding["kol_pool_id"]
    ):
        raise ProviderJobAccessError("video_analysis_target_invalid", 409)
    try:
        payload_identity = _video_evidence_binding(
            {
                "evidence_id": binding["evidence_id"],
                "kol_pool_id": binding["kol_pool_id"],
                "content_url": payload.get("source_url"),
            }
        )
    except ProviderJobAccessError as exc:
        raise ProviderJobAccessError("video_analysis_evidence_identity_drifted", 409) from exc
    if payload_identity != binding:
        raise ProviderJobAccessError("video_analysis_evidence_identity_drifted", 409)

    server_owned = _valid_server_capability(
        server_owned_capability,
        action=VIDEO_ANALYSIS,
        target_id=target_id,
        search_session_id=session_id,
    )
    if server_owned and session_id:
        raise ProviderJobAccessError("server_owned_session_must_be_root", 403)
    staff_id, user_id = _actor_ids(staff)
    if not server_owned and (staff_id <= 0 or user_id <= 0):
        raise ProviderJobAccessError("provider_job_actor_required", 403)
    if not server_owned and not check_tab_permission(staff or {}, "vkpi", "write"):
        raise ProviderJobAccessError("vkpi_write_permission_required", 403)

    source_session = dict(session or {})
    if session_id and _int(source_session.get("id")) != session_id:
        raise ProviderJobAccessError("search_session_target_invalid", 409)
    if session_id and _int(source_session.get("created_by")) != user_id:
        raise ProviderJobAccessError("search_session_owner_mismatch", 403)
    session_binding = (
        _session_binding(
            source_session,
            fallback_owner_user_id=user_id,
            bind_query=True,
        )
        if session_id
        else {
            "search_session_id": 0,
            "owner_user_id": 0 if server_owned else user_id,
            "bind_query": False,
        }
    )
    claim = {
        "version": FENCE_VERSION,
        "mode": "server_owned" if server_owned else "user",
        "action": VIDEO_ANALYSIS,
        "target_id": target_id,
        "actor": {
            "staff_id": None if server_owned else staff_id,
            "user_id": None if server_owned else user_id,
        },
        "session": session_binding,
        "target": {
            **binding,
            "search_session_item_id": _int(payload.get("search_session_item_id")),
        },
        "execution_fingerprint": _digest(
            _execution_contract(payload, action=VIDEO_ANALYSIS)
        ),
    }
    return _signed_claim(claim)


def _load_session(conn: Any, session_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, query_text, query_type, source, status, created_by,
               input_payload_json, result_summary_json, archived_at,
               archived_by, archive_reason, created_at, updated_at
        FROM vkpi_kol_search_sessions
        WHERE id=?
        LIMIT 1
        """,
        (int(session_id),),
    ).fetchone()
    if not row:
        raise ProviderJobAccessError("search_session_not_found", 404)
    from app.domains.kol.search_sessions_serde import _row_to_session

    return _row_to_session(row)


def _assert_content_fit_session_target(
    conn: Any,
    payload: dict[str, Any],
    *,
    session_id: int,
) -> None:
    """Prove the child KOL is a pool-backed item of the fenced session."""

    kol_pool_id = _int(payload.get("kol_pool_id") or payload.get("target_id"))
    item_id = _int(payload.get("search_session_item_id"))
    if kol_pool_id <= 0:
        raise ProviderJobAccessError("content_fit_target_invalid", 409)
    params: list[int] = [int(session_id), int(kol_pool_id)]
    item_clause = ""
    if item_id > 0:
        item_clause = " AND id=?"
        params.append(item_id)
    row = conn.execute(
        f"""
        SELECT id
        FROM vkpi_kol_search_session_items
        WHERE session_id=? AND kol_pool_id=?{item_clause}
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    if not row:
        raise ProviderJobAccessError("content_fit_session_target_mismatch", 409)


def _assert_video_analysis_target(
    conn: Any,
    payload: dict[str, Any],
    *,
    session_id: int,
    target_claim: dict[str, Any],
) -> None:
    evidence_id = _int(payload.get("target_id"))
    kol_pool_id = _int(payload.get("kol_pool_id"))
    row = conn.execute(
        """
        SELECT id AS evidence_id, kol_pool_id, content_url, platform, is_active
        FROM vkpi_kol_video_evidence
        WHERE id=?
        LIMIT 1
        """,
        (evidence_id,),
    ).fetchone()
    if not row or dict(row).get("is_active") in (False, 0, "0"):
        raise ProviderJobAccessError("video_analysis_evidence_unavailable", 409)
    current = _video_evidence_binding(dict(row))
    expected = {
        key: target_claim.get(key)
        for key in ("evidence_id", "kol_pool_id", "platform", "video_id", "normalized_url")
    }
    if current != expected or current["kol_pool_id"] != kol_pool_id:
        raise ProviderJobAccessError("video_analysis_evidence_drifted", 409)

    item_id = _int(payload.get("search_session_item_id"))
    claimed_item_id = _int(target_claim.get("search_session_item_id"))
    if claimed_item_id > 0 and item_id != claimed_item_id:
        raise ProviderJobAccessError("video_analysis_session_item_drifted", 409)
    if session_id <= 0:
        if item_id > 0:
            raise ProviderJobAccessError("video_analysis_session_item_drifted", 409)
        return
    if item_id <= 0:
        raise ProviderJobAccessError("video_analysis_session_item_required", 409)
    item = conn.execute(
        """
        SELECT id, session_id, kol_pool_id, evidence_id, source_url
        FROM vkpi_kol_search_session_items
        WHERE id=? AND session_id=?
        LIMIT 1
        """,
        (item_id, int(session_id)),
    ).fetchone()
    if not item:
        raise ProviderJobAccessError("video_analysis_session_item_drifted", 409)
    item_data = dict(item)
    item_kol = _int(item_data.get("kol_pool_id"))
    item_evidence = _int(item_data.get("evidence_id"))
    if (item_kol and item_kol != kol_pool_id) or (
        item_evidence and item_evidence != evidence_id
    ):
        raise ProviderJobAccessError("video_analysis_session_item_drifted", 409)
    source_url = _text(item_data.get("source_url"))
    if not item_kol and not item_evidence:
        try:
            item_binding = _video_evidence_binding(
                {
                    "evidence_id": evidence_id,
                    "kol_pool_id": kol_pool_id,
                    "content_url": source_url,
                }
            )
        except ProviderJobAccessError as exc:
            raise ProviderJobAccessError("video_analysis_session_item_drifted", 409) from exc
        if item_binding != current:
            raise ProviderJobAccessError("video_analysis_session_item_drifted", 409)


def _active_actor(conn: Any, *, staff_id: int, user_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT s.*, u.status AS user_status, u.email AS user_email
        FROM staff s
        JOIN users u ON u.id=s.user_id
        WHERE s.id=?
        LIMIT 1
        """,
        (int(staff_id),),
    ).fetchone()
    if not row:
        raise ProviderJobAccessError("provider_job_actor_inactive", 403)
    actor = dict(row)
    if _int(actor.get("user_id")) != int(user_id):
        raise ProviderJobAccessError("provider_job_actor_changed", 403)
    if actor.get("active") not in (True, 1, "1") or _text(actor.get("suspended_at")):
        raise ProviderJobAccessError("provider_job_actor_inactive", 403)
    if not user_status_allows_auth(actor.get("user_status"), production=True):
        raise ProviderJobAccessError("provider_job_actor_inactive", 403)
    if not check_tab_permission(actor, "vkpi", "write"):
        raise ProviderJobAccessError("provider_job_permission_revoked", 403)
    return actor


def revalidate_provider_job_fence(
    conn: Any,
    payload: dict[str, Any],
    *,
    expected_action: str,
) -> dict[str, Any]:
    """Revalidate immediately before planner/LLM/discovery/provider execution."""

    action = _text(expected_action).lower()
    fence = payload.get(FENCE_KEY)
    if not isinstance(fence, dict):
        raise ProviderJobAccessError("provider_job_fence_missing", 403)
    if _int(fence.get("version")) != FENCE_VERSION:
        raise ProviderJobAccessError("provider_job_fence_invalid", 403)
    if _text(fence.get("action")).lower() != action:
        raise ProviderJobAccessError("provider_job_action_drifted", 409)
    if not _verify_signed_claim(fence):
        raise ProviderJobAccessError("provider_job_fence_signature_invalid", 403)

    current_fingerprint = _digest(_execution_contract(payload, action=action))
    if not hmac.compare_digest(
        _text(fence.get("execution_fingerprint")), current_fingerprint
    ):
        raise ProviderJobAccessError("provider_job_payload_drifted", 409)
    payload_target_id = _text(
        payload.get("target_id")
        or (payload.get("kol_pool_id") if action == CONTENT_FIT_ANALYSIS else "")
    )
    if _text(fence.get("target_id")) != payload_target_id:
        raise ProviderJobAccessError("provider_job_target_drifted", 409)

    mode = _text(fence.get("mode")).lower()
    if mode not in {"user", "server_owned"}:
        raise ProviderJobAccessError("provider_job_fence_invalid", 403)
    session_claim = fence.get("session")
    if not isinstance(session_claim, dict):
        raise ProviderJobAccessError("provider_job_fence_invalid", 403)
    session_id = _int(session_claim.get("search_session_id"))
    payload_session_id = _int(payload.get("search_session_id"))
    if session_id != payload_session_id:
        raise ProviderJobAccessError("search_session_target_drifted", 409)

    session: dict[str, Any] | None = None
    if session_id > 0:
        session = _load_session(conn, session_id)
        if session.get("archived_at"):
            raise ProviderJobAccessError("search_session_archived", 409)
        if _text(session.get("status")).lower() == "cancelled":
            raise ProviderJobAccessError("search_session_cancelled", 409)
        current_owner = _int(session.get("created_by"))
        claimed_owner = _int(session_claim.get("owner_user_id"))
        if current_owner != claimed_owner:
            raise ProviderJobAccessError("search_session_owner_drifted", 403)
        if session_claim.get("bind_query") is True:
            current_binding = _session_binding(
                session,
                fallback_owner_user_id=current_owner,
                bind_query=True,
            )
            for key in (
                "query_text",
                "query_type",
                "input_payload_fingerprint",
            ):
                if current_binding.get(key) != session_claim.get(key):
                    raise ProviderJobAccessError("search_session_query_drifted", 409)
        if action == CONTENT_FIT_ANALYSIS:
            _assert_content_fit_session_target(
                conn,
                payload,
                session_id=session_id,
            )
        elif action == VIDEO_ANALYSIS:
            target_claim = fence.get("target")
            if not isinstance(target_claim, dict):
                raise ProviderJobAccessError("provider_job_fence_invalid", 403)
            _assert_video_analysis_target(
                conn,
                payload,
                session_id=session_id,
                target_claim=target_claim,
            )
    elif action == VIDEO_ANALYSIS:
        target_claim = fence.get("target")
        if not isinstance(target_claim, dict):
            raise ProviderJobAccessError("provider_job_fence_invalid", 403)
        _assert_video_analysis_target(
            conn,
            payload,
            session_id=0,
            target_claim=target_claim,
        )

    if mode == "server_owned":
        if action in {CONTENT_FIT_ANALYSIS, VIDEO_ANALYSIS} and session_id > 0:
            raise ProviderJobAccessError("server_owned_session_must_be_root", 403)
        if (
            action == CONTENT_FIT_ANALYSIS
            and session is not None
            and session.get("created_by") is not None
        ):
            raise ProviderJobAccessError("server_owned_session_has_user_owner", 403)
        if session is not None and _int(session.get("created_by")) > 0:
            raise ProviderJobAccessError("server_owned_session_has_user_owner", 403)
        return {"server_owned": True, "staff_id": None, "user_id": None}

    actor_claim = fence.get("actor")
    if not isinstance(actor_claim, dict):
        raise ProviderJobAccessError("provider_job_fence_invalid", 403)
    staff_id = _int(actor_claim.get("staff_id"))
    user_id = _int(actor_claim.get("user_id"))
    if staff_id <= 0 or user_id <= 0:
        raise ProviderJobAccessError("provider_job_actor_required", 403)
    if _int(payload.get("staff_id")) not in {0, staff_id}:
        raise ProviderJobAccessError("provider_job_actor_drifted", 403)
    if _int(payload.get("triggered_by_user_id")) not in {0, user_id}:
        raise ProviderJobAccessError("provider_job_actor_drifted", 403)
    return _active_actor(conn, staff_id=staff_id, user_id=user_id)


def terminal_block_provider_job(
    conn: Any,
    *,
    job_id: int,
    payload: dict[str, Any],
    error: ProviderJobAccessError,
    provider_calls_performed: bool | None = False,
) -> None:
    """Persist a non-retryable blocked terminal with truthful provider state."""

    result = {
        "status": "blocked",
        "reason": error.code,
        "provider_calls_performed": provider_calls_performed,
        "retry_allowed": False,
    }
    payload[FENCE_RESULT_KEY] = result
    last_error = _canonical(result)[:2000]
    serialized = _canonical(provider_job_payload_delta(payload))
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='blocked',
                    last_error=%s,
                    last_error_category='blocked',
                    next_retry_at=NULL,
                    payload=COALESCE(apify_jobs.payload, '{}'::jsonb) || %s::jsonb,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (last_error, serialized, int(job_id)),
            )


def authorize_provider_job_before_execution(
    worker_conn: Any,
    job: dict[str, Any],
    payload: dict[str, Any],
    *,
    expected_action: str,
) -> dict[str, Any] | None:
    """Return the live actor/capability or terminally block before provider I/O."""

    try:
        with db_connection_sync_scope():
            return revalidate_provider_job_fence(
                get_conn(),
                payload,
                expected_action=expected_action,
            )
    except ProviderJobAccessError as exc:
        terminal_block_provider_job(
            worker_conn,
            job_id=int(job["id"]),
            payload=payload,
            error=exc,
            provider_calls_performed=False,
        )
        return None


def guard_provider_job_before_execution(
    worker_conn: Any,
    job: dict[str, Any],
    payload: dict[str, Any],
    *,
    expected_action: str,
) -> bool:
    """Compatibility boolean wrapper around the actor-returning guard."""

    return authorize_provider_job_before_execution(
        worker_conn,
        job,
        payload,
        expected_action=expected_action,
    ) is not None


def revalidate_provider_job_checkpoint(
    payload: dict[str, Any],
    *,
    expected_action: str,
) -> dict[str, Any]:
    """Second checkpoint for work that separates provider reads from writes."""

    with db_connection_sync_scope():
        return revalidate_provider_job_fence(
            get_conn(),
            payload,
            expected_action=expected_action,
        )


__all__ = [
    "CONTENT_FIT_ANALYSIS",
    "FENCE_KEY",
    "FENCE_RESULT_KEY",
    "ProviderJobAccessError",
    "SESSION_ADVANCE",
    "SMART_SEARCH_PROFILE_ADVANCE",
    "ServerOwnedProviderCapability",
    "VIDEO_URL_RESOLVE",
    "VIDEO_ANALYSIS",
    "build_content_fit_provider_fence",
    "build_video_analysis_provider_fence",
    "build_search_session_provider_fence",
    "build_video_url_provider_fence",
    "authorize_provider_job_before_execution",
    "guard_provider_job_before_execution",
    "issue_server_owned_provider_capability",
    "revalidate_provider_job_checkpoint",
    "revalidate_provider_job_fence",
    "terminal_block_provider_job",
]

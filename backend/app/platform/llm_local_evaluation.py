"""Narrow, job-scoped authorization for local LLM evaluation.

This is deliberately not a model-readiness bypass.  Production jobs continue
to use the signed readiness gate.  A local evaluation must be requested at
enqueue time and carries a short-lived HMAC capability bound to one video,
one derive method, and one exact model.  Workers verify the persisted
capability for every job; process-wide worker flags cannot reinterpret old
queue rows as evaluation work.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from app.core.config import IS_PRODUCTION


PRODUCTION_EXECUTION_CLASS = "production"
LOCAL_EVALUATION_EXECUTION_CLASS = "local_evaluation"
LOCAL_EVALUATION_CAPABILITY_FIELD = "_local_evaluation_capability"
LOCAL_EVALUATION_REQUEST_FIELD = "local_evaluation"
LOCAL_EVALUATION_DERIVE_METHOD = "video_analysis_final_v1"
LOCAL_EVALUATION_CACHE_DERIVE_METHOD = "video_analysis_final_v1__local_eval"
LOCAL_EVALUATION_PROVIDER = "google"
LOCAL_EVALUATION_MODEL = "gemini-2.5-flash"
LOCAL_EVALUATION_BINDING = f"{LOCAL_EVALUATION_PROVIDER}/{LOCAL_EVALUATION_MODEL}"
LOCAL_EVALUATION_CLAIM_STATUS = "descriptive_only"
LOCAL_EVALUATION_MODEL_READINESS_STATUS = "evaluation_only_not_production_ready"

_CAPABILITY_VERSION = "vkpi-local-llm-eval-capability-v1"
_SIGNING_DOMAIN = b"vkpi:local-llm-evaluation:v1\n"
_ENABLED_ENV = "VKPI_LLM_LOCAL_EVALUATION_ENABLED"
_SIGNING_SECRET_ENV = "VKPI_LLM_LOCAL_EVALUATION_SIGNING_SECRET"
_DEFAULT_TTL_SECONDS = 900
_MAX_TTL_SECONDS = 1800


class LocalEvaluationCapabilityError(ValueError):
    """Raised when an explicit local-evaluation request cannot be authorized."""


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def local_evaluation_operator_enabled() -> bool:
    return not IS_PRODUCTION and _truthy(os.environ.get(_ENABLED_ENV))


def _signing_secret() -> str:
    # An explicit shared secret is preferred.  JWT_SECRET is an existing
    # server/worker shared secret and is only a fallback; never return or log it.
    return str(
        os.environ.get(_SIGNING_SECRET_ENV)
        or os.environ.get("JWT_SECRET")
        or ""
    ).strip()


def _canonical_claims(claims: dict[str, Any]) -> bytes:
    return json.dumps(
        claims,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _signature(claims: dict[str, Any], secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        _SIGNING_DOMAIN + _canonical_claims(claims),
        hashlib.sha256,
    ).hexdigest()


def issue_local_evaluation_capability(
    *,
    job_id: int,
    target_type: str,
    target_id: str,
    derive_method: str,
    model_binding: str = LOCAL_EVALUATION_BINDING,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    now: int | None = None,
) -> dict[str, Any]:
    """Issue one short-lived local-only capability or fail closed."""

    if IS_PRODUCTION:
        raise LocalEvaluationCapabilityError("local_evaluation_forbidden_in_production")
    if not _truthy(os.environ.get(_ENABLED_ENV)):
        raise LocalEvaluationCapabilityError("local_evaluation_disabled")
    secret = _signing_secret()
    if not secret:
        raise LocalEvaluationCapabilityError("local_evaluation_signing_secret_missing")

    normalized_job_id = int(job_id or 0)
    normalized_target_type = str(target_type or "").strip().lower()
    normalized_target_id = str(target_id or "").strip()
    normalized_derive = str(derive_method or "").strip().lower()
    normalized_binding = str(model_binding or "").strip()
    if normalized_job_id <= 0:
        raise LocalEvaluationCapabilityError("local_evaluation_job_id_invalid")
    if normalized_target_type != "video" or not normalized_target_id:
        raise LocalEvaluationCapabilityError("local_evaluation_target_not_allowed")
    if normalized_derive != LOCAL_EVALUATION_DERIVE_METHOD:
        raise LocalEvaluationCapabilityError("local_evaluation_derive_not_allowed")
    if normalized_binding != LOCAL_EVALUATION_BINDING:
        raise LocalEvaluationCapabilityError("local_evaluation_model_not_allowed")

    issued_at = int(now if now is not None else time.time())
    ttl = max(60, min(int(ttl_seconds or _DEFAULT_TTL_SECONDS), _MAX_TTL_SECONDS))
    claims = {
        "version": _CAPABILITY_VERSION,
        "execution_class": LOCAL_EVALUATION_EXECUTION_CLASS,
        "job_id": normalized_job_id,
        "target_type": normalized_target_type,
        "target_id": normalized_target_id,
        "derive_method": normalized_derive,
        "cache_derive_method": LOCAL_EVALUATION_CACHE_DERIVE_METHOD,
        "model_binding": normalized_binding,
        "issued_at": issued_at,
        "expires_at": issued_at + ttl,
        "nonce": secrets.token_urlsafe(24),
    }
    return {"claims": claims, "signature": _signature(claims, secret)}


def local_evaluation_requested(payload: dict[str, Any]) -> bool:
    return bool(
        payload.get(LOCAL_EVALUATION_REQUEST_FIELD) is True
        or str(payload.get("execution_class") or "").strip().lower()
        == LOCAL_EVALUATION_EXECUTION_CLASS
        or payload.get(LOCAL_EVALUATION_CAPABILITY_FIELD)
    )


def _invalid(reason: str) -> dict[str, Any]:
    return {
        "requested": True,
        "valid": False,
        "reason": reason,
        "execution_class": LOCAL_EVALUATION_EXECUTION_CLASS,
        "evaluation_only": True,
        "production_authorized": False,
        "claim_status": LOCAL_EVALUATION_CLAIM_STATUS,
        "model_readiness_status": LOCAL_EVALUATION_MODEL_READINESS_STATUS,
    }


def verify_job_local_evaluation_capability(
    payload: dict[str, Any],
    *,
    job_id: int,
    now: int | None = None,
) -> dict[str, Any]:
    """Resolve one job to production or a verified local-evaluation scope.

    Missing evaluation fields mean an ordinary production job.  If any
    evaluation marker is present, verification is mandatory and every mismatch
    fails closed instead of silently falling back to production.
    """

    if not local_evaluation_requested(payload):
        return {
            "requested": False,
            "valid": True,
            "reason": "production_job",
            "execution_class": PRODUCTION_EXECUTION_CLASS,
            "evaluation_only": False,
            "production_authorized": False,
            "claim_status": LOCAL_EVALUATION_CLAIM_STATUS,
            "model_readiness_status": "production_readiness_required",
        }
    if IS_PRODUCTION:
        return _invalid("local_evaluation_forbidden_in_production")
    if not _truthy(os.environ.get(_ENABLED_ENV)):
        return _invalid("local_evaluation_disabled")
    secret = _signing_secret()
    if not secret:
        return _invalid("local_evaluation_signing_secret_missing")

    capability = payload.get(LOCAL_EVALUATION_CAPABILITY_FIELD)
    if not isinstance(capability, dict):
        return _invalid("local_evaluation_capability_missing")
    claims = capability.get("claims")
    supplied_signature = str(capability.get("signature") or "")
    if not isinstance(claims, dict) or not supplied_signature:
        return _invalid("local_evaluation_capability_malformed")
    expected_signature = _signature(claims, secret)
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return _invalid("local_evaluation_capability_signature_invalid")

    current_time = int(now if now is not None else time.time())
    try:
        issued_at = int(claims.get("issued_at"))
        expires_at = int(claims.get("expires_at"))
    except (TypeError, ValueError):
        return _invalid("local_evaluation_capability_time_invalid")
    if issued_at > current_time + 30:
        return _invalid("local_evaluation_capability_not_yet_valid")
    if expires_at <= current_time:
        return _invalid("local_evaluation_capability_expired")
    if expires_at - issued_at > _MAX_TTL_SECONDS:
        return _invalid("local_evaluation_capability_ttl_invalid")
    if not str(claims.get("nonce") or "").strip():
        return _invalid("local_evaluation_capability_nonce_missing")

    expected = {
        "version": _CAPABILITY_VERSION,
        "execution_class": LOCAL_EVALUATION_EXECUTION_CLASS,
        "job_id": int(job_id or 0),
        "target_type": str(payload.get("target_type") or "").strip().lower(),
        "target_id": str(payload.get("target_id") or "").strip(),
        "derive_method": str(payload.get("derive_method") or "").strip().lower(),
        "cache_derive_method": LOCAL_EVALUATION_CACHE_DERIVE_METHOD,
        "model_binding": str(payload.get("model_binding") or "").strip(),
    }
    for key, expected_value in expected.items():
        if claims.get(key) != expected_value:
            return _invalid(f"local_evaluation_capability_{key}_mismatch")
    if expected["target_type"] != "video" or not expected["target_id"]:
        return _invalid("local_evaluation_target_not_allowed")
    if expected["derive_method"] != LOCAL_EVALUATION_DERIVE_METHOD:
        return _invalid("local_evaluation_derive_not_allowed")
    if expected["model_binding"] != LOCAL_EVALUATION_BINDING:
        return _invalid("local_evaluation_model_not_allowed")

    nonce_sha256 = hashlib.sha256(str(claims["nonce"]).encode("utf-8")).hexdigest()
    return {
        "requested": True,
        "valid": True,
        "reason": "local_evaluation_capability_valid",
        "execution_class": LOCAL_EVALUATION_EXECUTION_CLASS,
        "authorization_scope": "evaluation_only",
        "evaluation_only": True,
        "production_authorized": False,
        "claim_status": LOCAL_EVALUATION_CLAIM_STATUS,
        "model_readiness_status": LOCAL_EVALUATION_MODEL_READINESS_STATUS,
        "binding": LOCAL_EVALUATION_BINDING,
        "model": LOCAL_EVALUATION_MODEL,
        "base_derive_method": LOCAL_EVALUATION_DERIVE_METHOD,
        "cache_derive_method": LOCAL_EVALUATION_CACHE_DERIVE_METHOD,
        "capability_expires_at": expires_at,
        "capability_nonce_sha256": nonce_sha256,
    }


def redact_local_evaluation_capability(value: Any) -> Any:
    """Remove the signed capability from API response payloads."""

    if isinstance(value, dict):
        return {
            key: redact_local_evaluation_capability(item)
            for key, item in value.items()
            if key != LOCAL_EVALUATION_CAPABILITY_FIELD
        }
    if isinstance(value, list):
        return [redact_local_evaluation_capability(item) for item in value]
    return value

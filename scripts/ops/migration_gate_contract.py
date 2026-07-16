"""Pure contracts and cryptographic checks for migration 243 -> 244 evidence."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # pragma: no cover - verification fails closed
    InvalidSignature = Exception  # type: ignore[assignment,misc]
    Ed25519PublicKey = None  # type: ignore[assignment,misc]


EXPECTED_PRE_MIGRATION = "243_vkpi_event_radar.sql"
EXPECTED_POST_MIGRATION = "244_vkpi_event_radar_truth_scope.sql"
PRE_MIGRATION = "migrations/243_vkpi_event_radar.sql"
UP_MIGRATION = "migrations/244_vkpi_event_radar_truth_scope.sql"
DOWN_MIGRATION = "migrations/244_vkpi_event_radar_truth_scope_down.sql"

ATTESTATION_TYPE = "vkpi_migration_release_producer_attestation"
RUNNER_ATTESTATION_TYPE = "vkpi_pg_restore_runner_attestation"
SOURCE_MANIFEST_TYPE = "vkpi_migration_244_approved_source_manifest"
RECEIPT_TYPE = "vkpi_migration_release_receipt"

# No release producer or runner key has completed a controlled key ceremony.
# These immutable empty allowlists intentionally make production verification
# fail closed until a separately approved external authorization controller is
# implemented.  Test keys are accepted only by the non-authorizing replay API.
TRUSTED_PRODUCER_PUBLIC_KEYS: Mapping[str, str] = MappingProxyType({})
TRUSTED_RUNNER_PUBLIC_KEYS: Mapping[str, str] = MappingProxyType({})

PG_BIGINT_MAX = (1 << 63) - 1
FUTURE_TOLERANCE = timedelta(minutes=5)
FINALIZATION_TOLERANCE = timedelta(minutes=15)
MAX_AUTHORIZING_AGE = timedelta(hours=24)
MAX_METADATA_BYTES = 64 * 1024
MAX_EVIDENCE_BYTES = 512 * 1024
MAX_SOURCE_MANIFEST_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_DUMP_BYTES = 256 * 1024 * 1024 * 1024
MAX_MIGRATION_BYTES = 16 * 1024 * 1024
MAX_TOC_ENTRIES = 100_000

STAMP_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BUNDLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{7,127}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MIGRATION_KEY_RE = re.compile(r"^[0-9]{3}_[a-z0-9_]+\.sql$")
BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")

REQUIRED_ROW_ANCHORS = (
    "schema_migrations",
    "vkpi_events",
    "vkpi_dealers",
    "vkpi_projects",
    "vkpi_kol_pool",
    "vkpi_event_watch_targets",
    "vkpi_event_source_runs",
    "vkpi_event_opportunities",
    "vkpi_event_source_observations",
    "vkpi_event_opportunity_changes",
    "vkpi_event_opportunity_dealers",
    "vkpi_event_opportunity_promotions",
)
REQUIRED_BACKUP_RECEIPTS = ("pg_restore_list",)
REQUIRED_RESTORE_RECEIPTS = ("pg_restore_execute", "row_anchor_readback")
REQUIRED_REHEARSAL_RECEIPTS = (
    "migration_244_up",
    "migration_244_post_apply",
    "migration_244_down",
    "migration_244_post_rollback",
)
REQUIRED_POST_APPLY_CHECKS = (
    "organization_columns_present",
    "organization_not_null_and_positive",
    "workspace_unique_constraints_present",
    "workspace_foreign_keys_present",
    "dealer_identity_aliases_present",
    "cross_workspace_violation_rejected",
)
REQUIRED_POST_ROLLBACK_CHECKS = (
    "organization_columns_absent",
    "legacy_constraints_restored",
    "dealer_identity_aliases_absent",
    "migration_244_marker_absent",
    "row_anchors_restored",
)
REQUIRED_TOC_OBJECTS = (
    "TABLE public schema_migrations",
    "TABLE DATA public schema_migrations",
    "TABLE public vkpi_events",
    "TABLE DATA public vkpi_events",
)

SECRET_KEY_RE = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|"
    r"database[_-]?url|dsn|authorization|cookie)",
    re.IGNORECASE,
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"postgres(?:ql)?://", re.IGNORECASE),
    re.compile(
        r"(?:password|passwd|token|secret|api[_-]?key)[ \t]*[:=]",
        re.IGNORECASE,
    ),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bBearer[ \t]+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


class DuplicateJsonKey(ValueError):
    pass


class Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, check_id: str, passed: bool, detail: str) -> bool:
        self.items.append(
            {
                "id": check_id,
                "status": "passed" if passed else "failed",
                "detail": detail,
            }
        )
        return passed

    @property
    def failed(self) -> int:
        return sum(item["status"] == "failed" for item in self.items)

    @property
    def passed(self) -> int:
        return sum(item["status"] == "passed" for item in self.items)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not STAMP_RE.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def is_exact_int(
    value: object, *, minimum: int = 0, maximum: int = PG_BIGINT_MAX
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def json_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKey("duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError("non-finite JSON number")


def loads_strict(data: bytes) -> object:
    return json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_pairs,
        parse_constant=_reject_nonfinite,
    )


def describe(value: object) -> str:
    if value is None or isinstance(value, (bool, int, float)):
        return repr(value)
    if isinstance(value, str):
        digest = sha256_bytes(value.encode("utf-8"))[:12]
        return f"str(len={len(value)},sha256={digest})"
    if isinstance(value, list):
        return f"list(len={len(value)})"
    if isinstance(value, dict):
        return f"object(fields={len(value)})"
    return type(value).__name__


def strict_object(
    value: object,
    *,
    required: Iterable[str],
    prefix: str,
    checks: Checks,
    optional: Iterable[str] = (),
) -> dict[str, Any] | None:
    if not checks.add(prefix + ".object", isinstance(value, dict), "object required"):
        return None
    assert isinstance(value, dict)
    required_set, keys = set(required), set(value)
    allowed = required_set | set(optional)
    checks.add(
        prefix + ".required_fields",
        required_set <= keys,
        f"required={len(required_set)} present={len(required_set & keys)}",
    )
    checks.add(
        prefix + ".unknown_fields",
        keys <= allowed,
        f"allowed={len(allowed)} actual={len(keys)}",
    )
    return value


def schema_version_exact(
    payload: Mapping[str, Any], *, prefix: str, checks: Checks
) -> bool:
    return checks.add(
        prefix + ".schema_version",
        is_exact_int(payload.get("schema_version"), minimum=1, maximum=1),
        "exact integer schema_version=1 required",
    )


def sensitive_paths(value: object, *, prefix: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}"
            if SECRET_KEY_RE.search(str(key)):
                hits.append(child)
            hits.extend(sensitive_paths(item, prefix=child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(sensitive_paths(item, prefix=f"{prefix}[{index}]"))
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS):
            hits.append(prefix)
    return sorted(set(hits))


def check_no_secrets(value: object, *, prefix: str, checks: Checks) -> bool:
    hits = sensitive_paths(value)
    return checks.add(
        prefix + ".secret_scan",
        not hits,
        f"sensitive_field_count={len(hits)}",
    )


def attestation_message(attestation: Mapping[str, Any]) -> bytes:
    return canonical_json(
        {
            "algorithm": attestation.get("algorithm"),
            "attestation_type": attestation.get("attestation_type"),
            "key_id": attestation.get("key_id"),
            "payload_sha256": attestation.get("payload_sha256"),
            "schema_version": attestation.get("schema_version"),
            "signed_at": attestation.get("signed_at"),
        }
    )


def _verify_ed25519(
    *, key_text: str | None, signature_text: object, message: bytes
) -> tuple[bool, bool]:
    try:
        public_raw = base64.b64decode(key_text or "", validate=True)
        signature_raw = base64.b64decode(
            signature_text if isinstance(signature_text, str) else "", validate=True
        )
        encoded = (
            len(public_raw) == 32
            and len(signature_raw) == 64
            and isinstance(signature_text, str)
            and BASE64_RE.fullmatch(signature_text) is not None
        )
    except (ValueError, binascii.Error):
        return False, False
    if Ed25519PublicKey is None or not encoded:
        return encoded, False
    try:
        Ed25519PublicKey.from_public_bytes(public_raw).verify(signature_raw, message)
    except (InvalidSignature, ValueError):
        return encoded, False
    return encoded, True


def verify_producer_attestation(
    payload: Mapping[str, Any],
    *,
    prefix: str,
    now: datetime,
    not_before: datetime | None,
    finalized_at: datetime | None,
    max_age: timedelta,
    public_keys: Mapping[str, str],
    checks: Checks,
) -> bool:
    raw = strict_object(
        payload.get("attestation"),
        required=(
            "schema_version",
            "attestation_type",
            "algorithm",
            "key_id",
            "signed_at",
            "payload_sha256",
            "signature",
        ),
        prefix=prefix + ".attestation",
        checks=checks,
    )
    if raw is None:
        return False
    version_ok = schema_version_exact(raw, prefix=prefix + ".attestation", checks=checks)
    type_ok = checks.add(
        prefix + ".attestation.type",
        raw.get("attestation_type") == ATTESTATION_TYPE,
        "producer attestation type required",
    )
    algorithm_ok = checks.add(
        prefix + ".attestation.algorithm",
        raw.get("algorithm") == "Ed25519",
        "Ed25519 required",
    )
    unsigned = dict(payload)
    unsigned.pop("attestation", None)
    try:
        expected_sha = json_sha256(unsigned)
    except (TypeError, ValueError):
        expected_sha = ""
    binding_ok = checks.add(
        prefix + ".attestation.payload_binding",
        is_sha256(raw.get("payload_sha256"))
        and raw.get("payload_sha256") == expected_sha,
        f"canonical_payload_sha256={expected_sha}",
    )
    key_id = raw.get("key_id")
    key_text = public_keys.get(key_id) if isinstance(key_id, str) else None
    key_ok = checks.add(
        prefix + ".attestation.key_allowlisted",
        isinstance(key_text, str),
        "key id must exist in the immutable approved public-key allowlist",
    )
    signed_at = parse_time(raw.get("signed_at"))
    time_ok = signed_at is not None
    if signed_at is not None:
        time_ok = (
            signed_at <= now + FUTURE_TOLERANCE
            and now - signed_at <= max_age
            and (not_before is None or signed_at >= not_before)
            and (finalized_at is None or signed_at <= finalized_at + FUTURE_TOLERANCE)
        )
    checks.add(
        prefix + ".attestation.time",
        time_ok,
        "fresh signature after action and before finalization required",
    )
    encoded, signature_ok = _verify_ed25519(
        key_text=key_text,
        signature_text=raw.get("signature"),
        message=attestation_message(raw),
    )
    checks.add(prefix + ".attestation.encoding", encoded, "raw Ed25519 encoding")
    checks.add(prefix + ".attestation.signature", signature_ok, "signature result")
    return all((version_ok, type_ok, algorithm_ok, binding_ok, key_ok, time_ok, signature_ok))


def validate_migration_state(
    value: object, *, prefix: str, checks: Checks
) -> dict[str, Any] | None:
    mapping = strict_object(
        value,
        required=("version_keys", "version_keys_sha256", "content_sha256"),
        prefix=prefix,
        checks=checks,
    )
    if mapping is None:
        return None
    keys = mapping.get("version_keys")
    keys_ok = (
        isinstance(keys, list)
        and 0 < len(keys) <= 10_000
        and all(isinstance(key, str) and MIGRATION_KEY_RE.fullmatch(key) for key in keys)
        and keys == sorted(set(keys))
    )
    checks.add(prefix + ".version_keys", keys_ok, "sorted unique migration keys required")
    expected = json_sha256(keys) if keys_ok else None
    checks.add(
        prefix + ".key_set_digest",
        expected is not None and mapping.get("version_keys_sha256") == expected,
        f"expected={expected}",
    )
    checks.add(
        prefix + ".content_digest",
        is_sha256(mapping.get("content_sha256")),
        "database-computed schema_migrations content digest required",
    )
    return mapping if keys_ok and expected == mapping.get("version_keys_sha256") else None

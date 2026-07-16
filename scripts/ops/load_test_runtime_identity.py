from __future__ import annotations

from scripts.ops.load_test_telemetry import *

def _strict_identity_json_loads(encoded: bytes) -> Mapping[str, Any]:
    def object_without_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate identity JSON key: {key}")
            result[key] = value
        return result

    def reject_nonfinite(constant: str) -> None:
        raise ValueError(f"non-finite identity JSON constant: {constant}")

    payload = json.loads(
        encoded.decode("utf-8"),
        object_pairs_hook=object_without_duplicates,
        parse_constant=reject_nonfinite,
    )
    if not isinstance(payload, Mapping):
        raise ValueError("identity response root must be an object")
    return payload


async def _read_bounded_json_response(response: Any, max_response_bytes: int) -> Mapping[str, Any]:
    size = 0
    chunks: list[bytes] = []
    while True:
        chunk = await response.content.read(min(65536, max_response_bytes + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > max_response_bytes:
            raise ValueError("identity response exceeded safety cap")
    return _strict_identity_json_loads(b"".join(chunks))

def _runtime_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def validate_target_runtime_identity_payload(
    payload: Any,
    contract: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate release, migration, and worker truth from one bounded /health body."""

    failures: list[str] = []
    observed: dict[str, Any] = {}
    if not target_runtime_identity_contract_valid(contract):
        return {
            "pass": False,
            "plan_binding_valid": False,
            "failure_reasons": ["target_runtime_identity_plan_contract_invalid"],
            "observed": observed,
            "raw_health_payload_persisted": False,
        }
    if not isinstance(payload, Mapping):
        return {
            "pass": False,
            "plan_binding_valid": True,
            "failure_reasons": ["runtime_health_root_not_object"],
            "observed": observed,
            "raw_health_payload_persisted": False,
        }

    build = payload.get("build") if isinstance(payload.get("build"), Mapping) else {}
    trust = payload.get("trust") if isinstance(payload.get("trust"), Mapping) else {}
    release_contract = contract["release"]
    migration_contract = contract["migration"]
    worker_contract = contract["worker"]
    expected_server = str(release_contract["expected_server_git_sha"])
    expected_client = str(release_contract["expected_client_git_sha"])
    expected_migration = str(migration_contract["expected_applied_version"])
    expected_worker = str(worker_contract["expected_release_sha"])
    expected_boot = str(worker_contract["expected_boot_nonce_sha256"])

    server_sha = str(build.get("git_sha") or "").strip().lower()
    trusted_server_sha = str(trust.get("server_git_sha") or "").strip().lower()
    client_sha = str(trust.get("client_git_sha") or "").strip().lower()
    migration = str(trust.get("db_migration_max") or "").strip()
    migration_source = str(trust.get("db_migration_source") or "").strip()
    worker_sha = str(trust.get("worker_sha") or "").strip().lower()
    worker_sha_source = str(trust.get("worker_sha_source") or "").strip()
    heartbeat_source = str(trust.get("worker_heartbeat_source") or "").strip()
    worker_boot = str(trust.get("worker_boot_nonce_sha256") or "").strip().lower()
    worker_name = str(trust.get("worker_name") or "").strip()

    if payload.get("status") != "ok":
        failures.append("runtime_health_status_not_ok")
    if server_sha != expected_server or trusted_server_sha != expected_server:
        failures.append("runtime_server_release_sha_mismatch")
    if client_sha != expected_client or build.get("client_matches_server") is not True:
        failures.append("runtime_client_release_sha_mismatch")
    if trust.get("sha_aligned") is not True:
        failures.append("runtime_release_alignment_not_proven")
    if migration != expected_migration:
        failures.append("runtime_migration_version_mismatch")
    if migration_source != migration_contract["required_source"]:
        failures.append("runtime_migration_source_untrusted")
    db_startup = trust.get("db_startup")
    if not isinstance(db_startup, Mapping):
        failures.append("runtime_database_startup_unavailable")
    else:
        if db_startup.get("backend") != "postgres":
            failures.append("runtime_database_backend_not_postgres")
        if db_startup.get("state") != "completed":
            failures.append("runtime_database_startup_incomplete")
        if db_startup.get("schema_migrations") != "completed":
            failures.append("runtime_database_migration_startup_incomplete")

    if worker_sha != expected_worker:
        failures.append("runtime_worker_release_sha_mismatch")
    if worker_sha_source != worker_contract["required_sha_source"]:
        failures.append("runtime_worker_release_source_untrusted")
    if heartbeat_source != worker_contract["required_heartbeat_source"]:
        failures.append("runtime_worker_heartbeat_source_untrusted")
    if trust.get("worker_online") is not True:
        failures.append("runtime_worker_not_online")
    if not worker_name:
        failures.append("runtime_worker_name_missing")
    worker_pid = trust.get("worker_pid")
    if isinstance(worker_pid, bool) or not isinstance(worker_pid, int) or worker_pid <= 0:
        failures.append("runtime_worker_pid_invalid")
    if worker_boot != expected_boot:
        failures.append("runtime_worker_boot_nonce_mismatch")

    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    worker_started_at = _runtime_utc(trust.get("worker_started_at"))
    heartbeat = _runtime_utc(trust.get("worker_heartbeat"))
    approved_not_before = _runtime_utc(worker_contract.get("not_before"))
    heartbeat_age: float | None = None
    if worker_started_at is None:
        failures.append("runtime_worker_started_at_invalid")
    elif approved_not_before is None or worker_started_at < approved_not_before:
        failures.append("runtime_worker_started_before_approved_restart")
    if heartbeat is None:
        failures.append("runtime_worker_heartbeat_invalid")
    else:
        heartbeat_age = (reference - heartbeat).total_seconds()
        if not math.isfinite(heartbeat_age) or heartbeat_age < -30:
            failures.append("runtime_worker_heartbeat_in_future")
        elif heartbeat_age > int(worker_contract["maximum_heartbeat_age_seconds"]):
            failures.append("runtime_worker_heartbeat_stale")
        if worker_started_at is not None and heartbeat < worker_started_at:
            failures.append("runtime_worker_heartbeat_predates_start")

    observed.update(
        {
            "server_release_sha": server_sha[:12] or None,
            "client_release_sha": client_sha[:12] or None,
            "migration_version": migration or None,
            "migration_source": migration_source or None,
            "worker_release_sha": worker_sha[:12] or None,
            "worker_release_source": worker_sha_source or None,
            "worker_heartbeat_source": heartbeat_source or None,
            "worker_name": worker_name or None,
            "worker_pid": worker_pid,
            "worker_boot_nonce_sha256": worker_boot[:12] or None,
            "worker_started_at": (
                worker_started_at.isoformat(timespec="seconds").replace("+00:00", "Z")
                if worker_started_at is not None
                else None
            ),
            "worker_heartbeat_age_seconds": (
                round(heartbeat_age, 3)
                if heartbeat_age is not None and math.isfinite(heartbeat_age)
                else None
            ),
        }
    )
    return {
        "pass": not failures,
        "plan_binding_valid": True,
        "failure_reasons": sorted(set(failures)),
        "observed": observed,
        "raw_health_payload_persisted": False,
    }


async def probe_target_runtime_health(
    context: RequestContext,
    *,
    backend_base: str,
    max_response_bytes: int,
) -> dict[str, Any]:
    """Read only the fixed loopback /health identity surface once."""

    headers = {
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "User-Agent": "vkpi-readonly-load-test/runtime-identity-v1",
    }
    request_count = 0
    try:
        # /health is the public runtime-attestation surface.  Never disclose a
        # staff bearer token to this probe, even though the target is loopback.
        request_count = 1
        async with context.session.get(
            f"{backend_base}{TARGET_RUNTIME_HEALTH_PATH}",
            headers=headers,
            allow_redirects=False,
        ) as response:
            if int(response.status) != 200:
                raise ValueError("runtime health endpoint returned a non-200 status")
            payload = await _read_bounded_json_response(
                response,
                min(int(max_response_bytes), 1024 * 1024),
            )
        return {"ok": True, "payload": payload, "request_count": request_count}
    except Exception as exc:  # noqa: BLE001 - fail closed without persisting response details
        return {
            "ok": False,
            "reason": type(exc).__name__,
            "request_count": request_count,
        }


async def verify_target_runtime_identity(
    context: RequestContext,
    *,
    backend_base: str,
    max_response_bytes: int,
    execution_plan: ImmutableCapacityExecutionPlan,
    probe_fn: Callable[..., Awaitable[Mapping[str, Any]]] = probe_target_runtime_health,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not _is_immutable_capacity_execution_plan(execution_plan):
        return {
            "pass": False,
            "plan_binding_valid": False,
            "request_count": 0,
            "failure_reasons": ["target_runtime_identity_plan_not_canonical"],
            "observed": {},
            "raw_health_payload_persisted": False,
            "token_persisted": False,
        }
    contract = execution_plan["target_runtime_identity"]
    if not target_runtime_identity_contract_valid(contract):
        return {
            "pass": False,
            "plan_binding_valid": False,
            "plan_sha256": execution_plan.plan_sha256,
            "request_count": 0,
            "failure_reasons": ["target_runtime_identity_plan_contract_invalid"],
            "observed": {},
            "raw_health_payload_persisted": False,
            "token_persisted": False,
        }
    try:
        probe = await probe_fn(
            context,
            backend_base=backend_base,
            max_response_bytes=max_response_bytes,
        )
    except Exception as exc:  # noqa: BLE001 - injected/network failure is a blocked preflight
        probe = {"ok": False, "reason": type(exc).__name__, "request_count": 0}
    if not isinstance(probe, Mapping):
        probe = {"ok": False, "reason": "invalid_probe_result", "request_count": 0}
    raw_request_count = probe.get("request_count")
    try:
        request_count = int(raw_request_count or 0)
    except (TypeError, ValueError, OverflowError):
        request_count = 0
    if probe.get("ok") is not True:
        return {
            "pass": False,
            "plan_binding_valid": True,
            "plan_sha256": execution_plan.plan_sha256,
            "identity_source": "loopback_health_runtime_trust",
            "request_count": request_count,
            "failure_reasons": [
                f"runtime_health_probe_failed:{str(probe.get('reason') or 'unknown')[:80]}"
            ],
            "observed": {},
            "raw_health_payload_persisted": False,
            "token_persisted": False,
        }
    if (
        isinstance(raw_request_count, bool)
        or request_count != RUNTIME_IDENTITY_PREFLIGHT_REQUESTS
    ):
        return {
            "pass": False,
            "plan_binding_valid": True,
            "plan_sha256": execution_plan.plan_sha256,
            "identity_source": "loopback_health_runtime_trust",
            "request_count": max(request_count, 0),
            "failure_reasons": ["runtime_health_probe_request_count_invalid"],
            "observed": {},
            "raw_health_payload_persisted": False,
            "token_persisted": False,
        }
    result = validate_target_runtime_identity_payload(probe.get("payload"), contract, now=now)
    result.update(
        {
            "plan_sha256": execution_plan.plan_sha256,
            "identity_source": "loopback_health_runtime_trust",
            "request_count": request_count,
            "token_persisted": False,
        }
    )
    return result


def blocked_target_runtime_identity_report(
    args: Any,
    *,
    plan: ImmutableCapacityExecutionPlan,
    approval: Mapping[str, Any],
    auth_meta: Mapping[str, Any],
    preflight: Mapping[str, Any],
) -> dict[str, Any]:
    now = utc_now()
    request_count = int(preflight.get("request_count") or 0)
    return {
        "schema_version": 4,
        "evidence_type": (
            "live_local_readonly_preflight_only" if request_count else "blocked_local_capacity_attempt"
        ),
        "requested_live": True,
        "network_observed": request_count > 0,
        "network_requests_issued": request_count,
        "pressure_observed": False,
        "pressure_completed": False,
        "live_run": False,
        "synthetic_fixture": False,
        "started_at": now,
        "completed_at": now,
        "report_sha256": "computed_after_redaction",
        "capacity_execution_plan": plan.public_dict(),
        "operator_preflight": public_capacity_execution_approval(approval),
        "runtime_identity_preflight": dict(preflight),
        "runtime_identity_preflight_request_count": request_count,
        "auth": dict(auth_meta),
        "safety": {
            "loopback_only": True,
            "method_allowlist": ["GET"],
            "business_mutations": False,
            "provider_calls": False,
            "browser_calls": False,
            "http_session_created": True,
            "pressure_started": False,
            "automatic_stop": True,
        },
        "profiles": [
            {
                "profile": "target_runtime_identity",
                "status": "blocked",
                "blocked_reason": (
                    "target runtime release, migration, or worker identity preflight did not "
                    "match the operator-approved plan"
                ),
                "preflight": [],
                "stages": [],
            }
        ],
        "executed_stage_count": 0,
        "preflight_request_count": request_count,
        "identity_preflight_request_count": 0,
        "overall_capacity": None,
        "limitations": [
            "no pressure stage was started because target runtime identity failed closed",
            "port/listener health alone is never accepted as release identity evidence",
        ],
    }


__all__ = [name for name in globals() if not name.startswith("__")]

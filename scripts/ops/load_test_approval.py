from __future__ import annotations

import sys
from datetime import timedelta
from scripts.ops.load_test_contracts import *
from scripts.ops.load_test_execution_policy import *

CAPACITY_EXECUTION_PLAN_SCHEMA = "vkpi-capacity-execution-plan/v2"
CAPACITY_EXECUTION_APPROVAL_SCHEMA = "vkpi-capacity-execution-approval/v1"
CAPACITY_EXECUTION_APPROVAL_SCOPE = "local_loopback_readonly_capacity"
CAPACITY_EXECUTION_NONCE_CONSUMPTION_SCHEMA = "vkpi-capacity-approval-nonce-consumption/v1"
MAX_CAPACITY_EXECUTION_APPROVAL_BYTES = 64 * 1024
MAX_CAPACITY_EXECUTION_APPROVAL_SECONDS = 4 * 60 * 60
MAX_CAPACITY_EXECUTION_APPROVAL_FUTURE_SKEW_SECONDS = 120.0
CAPACITY_RUNNER_SOURCE_FILES = (
    "scripts/load_test_vkpi_readonly.py",
    "scripts/ops/load_test_approval.py",
    "scripts/ops/load_test_calibration.py",
    "scripts/ops/load_test_cli.py",
    "scripts/ops/load_test_contracts.py",
    "scripts/ops/load_test_execution_policy.py",
    "scripts/ops/load_test_legacy.py",
    "scripts/ops/load_test_runner.py",
    "scripts/ops/load_test_runtime_identity.py",
    "scripts/ops/load_test_telemetry.py",
    "scripts/ops/load_test_verdict.py",
    "scripts/ops/load_test_workload.py",
    "scripts/ops/load_test_cli_contract.py",
)
# Execution approval is a separate operator role.  It is deliberately empty
# until a real operator public key is approved in code review.  CLI flags and
# environment variables can supply an approval artifact, never a trust root.
TRUSTED_CAPACITY_OPERATOR_ED25519_PUBLIC_KEYS: Mapping[str, str] = MappingProxyType({})
_EXECUTION_PLAN_CAPABILITY = object()
_EXECUTION_APPROVAL_CAPABILITY = object()
_EXECUTION_APPROVAL_CONSUMPTION_CAPABILITY = object()


def _offline_telemetry() -> dict[str, Any]:
    return {
        "sample_interval_seconds": None,
        "samples": [],
        "summary": {
            "process_metrics_available": False,
            "peak_combined_process_cpu_percent": None,
            "peak_combined_process_rss_kib": None,
            "event_loop_lag_ms": {"available": False, "p50": None, "p95": None, "max": None},
            "optional_adapters": {
                "db_pool": {"available": False, "last_value": None, "unavailable_reasons": ["offline_fixture"]},
                "redis": {"available": False, "last_value": None, "unavailable_reasons": ["offline_fixture"]},
            },
        },
    }


async def _run_with_configured_telemetry(
    operation: Awaitable[dict[str, Any]],
    *,
    live: bool,
    ports: Sequence[int],
    args: argparse.Namespace,
    adapter_readers: Mapping[str, Any] | None = None,
    required_listeners: Mapping[str, int] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not live:
        return await operation, _offline_telemetry()
    # Local import avoids widening the module import cycle while preserving a
    # single telemetry implementation for approved live execution.
    from scripts.ops.load_test_telemetry import run_with_resource_telemetry

    return await run_with_resource_telemetry(
        operation,
        ports=ports,
        sample_interval_seconds=args.resource_sample_seconds,
        adapter_readers=adapter_readers,
        required_listeners=required_listeners,
    )


def _canonical_execution_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class ImmutableCapacityExecutionPlan(Mapping[str, Any]):
    """Canonical immutable plan used as the approval verification input.

    Nested values are decoded from the sealed canonical bytes on access, so a
    caller can mutate only a copy.  The live gate accepts only objects created
    by ``build_immutable_capacity_execution_plan`` in this process.
    """

    __slots__ = ("_canonical", "_keys", "_plan_sha256", "_capability")

    def __setattr__(self, name: str, value: Any) -> None:
        if hasattr(self, name):
            raise AttributeError("capacity execution plan fields are immutable")
        object.__setattr__(self, name, value)

    def __init__(self, payload: Mapping[str, Any], *, capability: object):
        if capability is not _EXECUTION_PLAN_CAPABILITY:
            raise TypeError("capacity execution plans must be built by the canonical builder")
        canonical = _canonical_execution_json_bytes(payload)
        decoded = json.loads(canonical.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise TypeError("capacity execution plan must be an object")
        self._canonical = canonical
        self._keys = tuple(decoded)
        self._plan_sha256 = hashlib.sha256(canonical).hexdigest()
        self._capability = capability

    def __getitem__(self, key: str) -> Any:
        decoded = json.loads(self._canonical.decode("utf-8"))
        return decoded[key]

    def __iter__(self):
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def plan_sha256(self) -> str:
        return self._plan_sha256

    def public_dict(self) -> dict[str, Any]:
        payload = json.loads(self._canonical.decode("utf-8"))
        payload["plan_sha256"] = self._plan_sha256
        payload["immutable_canonical_plan"] = True
        return payload


class _VerifiedCapacityExecutionApproval(dict[str, Any]):
    """In-process authority; serialization intentionally discards authority."""


class _ConsumedCapacityExecutionApproval(_VerifiedCapacityExecutionApproval):
    """Single-use in-process authority created only after atomic consumption."""


def build_immutable_capacity_execution_plan(
    payload: Mapping[str, Any],
) -> ImmutableCapacityExecutionPlan:
    if not isinstance(payload, Mapping):
        raise TypeError("capacity execution plan payload must be an object")
    if payload.get("schema_version") != CAPACITY_EXECUTION_PLAN_SCHEMA:
        raise ValueError("capacity execution plan schema is unsupported")
    plan = ImmutableCapacityExecutionPlan(
        payload,
        capability=_EXECUTION_PLAN_CAPABILITY,
    )
    if plan._capability is not _EXECUTION_PLAN_CAPABILITY:
        raise RuntimeError("capacity execution plan could not be sealed")
    return plan


def _is_immutable_capacity_execution_plan(value: Any) -> bool:
    if not isinstance(value, ImmutableCapacityExecutionPlan):
        return False
    if value._capability is not _EXECUTION_PLAN_CAPABILITY:
        return False
    try:
        return secrets.compare_digest(
            hashlib.sha256(value._canonical).hexdigest(),
            value.plan_sha256,
        )
    except (AttributeError, TypeError, ValueError):
        return False


def validate_execution_run_nonce(value: str | None) -> str | None:
    if value is None:
        return None
    nonce = str(value).strip()
    if not (16 <= len(nonce) <= 128):
        raise ValueError("execution run nonce must contain 16-128 characters")
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", nonce):
        raise ValueError("execution run nonce contains unsupported characters")
    return nonce


def current_capacity_code_head() -> str:
    """Read only the repository HEAD; never consult a network or token store."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except Exception as exc:
        raise ValueError("capacity execution plan requires a readable git HEAD") from exc
    head = result.stdout.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", head):
        raise ValueError("capacity execution plan git HEAD is malformed")
    return head


def current_capacity_worktree_state() -> dict[str, Any]:
    """Return only clean/dirty truth and a digest of porcelain status bytes.

    The status payload can contain local paths, so it is never returned or
    serialized.  A digest still binds an approved plan to the exact clean
    state observed when the plan was built.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ],
            cwd=ROOT,
            capture_output=True,
            text=False,
            timeout=10,
            check=True,
        )
    except Exception as exc:
        raise ValueError("capacity execution plan requires readable worktree status") from exc
    status_bytes = bytes(result.stdout)
    return {
        "worktree_clean": not status_bytes,
        "worktree_status_sha256": hashlib.sha256(status_bytes).hexdigest(),
    }


def current_capacity_runner_source_bundle_sha256() -> str:
    """Hash the exact local runtime harness, including untracked source files."""
    digest = hashlib.sha256()
    for relative in CAPACITY_RUNNER_SOURCE_FILES:
        encoded = _secure_read_regular_file(
            ROOT / relative,
            max_bytes=2 * 1024 * 1024,
            label=f"capacity runner source {relative}",
            require_owner=False,
            require_private=False,
        )
        digest.update(relative.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(hashlib.sha256(encoded).digest())
        digest.update(b"\x00")
    return digest.hexdigest()


def _capacity_execution_runtime_binding_status(
    plan: ImmutableCapacityExecutionPlan,
) -> tuple[dict[str, bool], list[str]]:
    """Re-check the content-addressed clean worktree before granting authority."""
    checks = {
        "worktree_clean": False,
        "worktree_status_binding_valid": False,
        "git_head_binding_valid": False,
        "runtime_source_bundle_binding_valid": False,
        "target_runtime_identity_plan_binding_valid": False,
        "nonce_ledger_plan_binding_present": False,
    }
    failures: list[str] = []
    try:
        code = plan["code"]
        consumption = plan["approval_consumption"]
        target_runtime_identity = plan["target_runtime_identity"]
        current_state = current_capacity_worktree_state()
        current_head = current_capacity_code_head()
        current_bundle = current_capacity_runner_source_bundle_sha256()
    except (KeyError, OSError, TypeError, ValueError):
        return checks, ["capacity_execution_runtime_binding_unavailable"]

    planned_clean = code.get("worktree_clean") is True
    current_clean = current_state.get("worktree_clean") is True
    checks["worktree_clean"] = bool(planned_clean and current_clean)
    if not checks["worktree_clean"]:
        failures.append("capacity_execution_worktree_not_clean")

    planned_status_sha256 = code.get("worktree_status_sha256")
    current_status_sha256 = current_state.get("worktree_status_sha256")
    checks["worktree_status_binding_valid"] = bool(
        isinstance(planned_status_sha256, str)
        and isinstance(current_status_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", planned_status_sha256)
        and secrets.compare_digest(planned_status_sha256, current_status_sha256)
    )
    if not checks["worktree_status_binding_valid"]:
        failures.append("capacity_execution_worktree_status_binding")

    planned_head = code.get("git_head")
    checks["git_head_binding_valid"] = bool(
        isinstance(planned_head, str)
        and secrets.compare_digest(planned_head, current_head)
    )
    if not checks["git_head_binding_valid"]:
        failures.append("capacity_execution_git_head_binding")

    planned_bundle = code.get("runtime_source_bundle_sha256")
    checks["runtime_source_bundle_binding_valid"] = bool(
        isinstance(planned_bundle, str)
        and re.fullmatch(r"[0-9a-f]{64}", planned_bundle)
        and secrets.compare_digest(planned_bundle, current_bundle)
    )
    if not checks["runtime_source_bundle_binding_valid"]:
        failures.append("capacity_execution_runtime_source_bundle_binding")

    checks["target_runtime_identity_plan_binding_valid"] = (
        target_runtime_identity_contract_valid(target_runtime_identity)
    )
    if not checks["target_runtime_identity_plan_binding_valid"]:
        failures.append("capacity_execution_target_runtime_identity_plan_binding")

    ledger_binding = consumption.get("ledger_dir_path_sha256")
    checks["nonce_ledger_plan_binding_present"] = bool(
        consumption.get("single_use_required") is True
        and consumption.get("atomic_create_required") is True
        and isinstance(ledger_binding, str)
        and re.fullmatch(r"[0-9a-f]{64}", ledger_binding)
    )
    if not checks["nonce_ledger_plan_binding_present"]:
        failures.append("capacity_execution_nonce_ledger_binding")
    return checks, failures


def _strict_execution_approval_json_loads(encoded: bytes) -> Mapping[str, Any]:
    def object_without_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_nonfinite(constant: str) -> None:
        raise ValueError(f"non-finite JSON constant: {constant}")

    payload = json.loads(
        encoded.decode("utf-8"),
        object_pairs_hook=object_without_duplicates,
        parse_constant=reject_nonfinite,
    )
    if not isinstance(payload, Mapping):
        raise ValueError("capacity execution approval root must be an object")
    return payload


def _runtime_trusted_capacity_operator_public_keys() -> Mapping[str, str]:
    compatibility_module = sys.modules.get("scripts.load_test_vkpi_readonly")
    candidate = getattr(
        compatibility_module,
        "TRUSTED_CAPACITY_OPERATOR_ED25519_PUBLIC_KEYS",
        TRUSTED_CAPACITY_OPERATOR_ED25519_PUBLIC_KEYS,
    )
    return candidate if isinstance(candidate, Mapping) else MappingProxyType({})


def _runtime_non_operator_trust_roots() -> tuple[Mapping[str, str], Mapping[str, str]]:
    compatibility_module = sys.modules.get("scripts.load_test_vkpi_readonly")
    calibration = getattr(
        compatibility_module,
        "TRUSTED_CALIBRATION_ED25519_PUBLIC_KEYS",
        TRUSTED_CALIBRATION_ED25519_PUBLIC_KEYS,
    )
    telemetry = getattr(
        compatibility_module,
        "TRUSTED_TELEMETRY_ED25519_PUBLIC_KEYS",
        TRUSTED_TELEMETRY_ED25519_PUBLIC_KEYS,
    )
    return (
        calibration if isinstance(calibration, Mapping) else MappingProxyType({}),
        telemetry if isinstance(telemetry, Mapping) else MappingProxyType({}),
    )


def _execution_approval_failure(*reasons: str) -> dict[str, Any]:
    return {
        "status": "untrusted_or_unapproved",
        "trusted": False,
        "key_id": None,
        "plan_sha256": None,
        "run_nonce_sha256": None,
        "approval_file_sha256": None,
        "issued_at": None,
        "expires_at": None,
        "evaluated_at": None,
        "signer_allowlisted": False,
        "signature_valid": False,
        "plan_binding_valid": False,
        "run_binding_valid": False,
        "time_binding_valid": False,
        "operator_role_separated": False,
        "worktree_clean": False,
        "worktree_status_binding_valid": False,
        "git_head_binding_valid": False,
        "runtime_source_bundle_binding_valid": False,
        "target_runtime_identity_plan_binding_valid": False,
        "nonce_ledger_plan_binding_present": False,
        "nonce_consumed": False,
        "consumption_status": "not_attempted",
        "consumption_record_sha256": None,
        "consumption_ledger_path_sha256": None,
        "verifier_key_material": "public_only",
        "signature_persisted": False,
        "raw_nonce_persisted": False,
        "token_or_private_key_read": False,
        "failure_reasons": sorted(set(reasons or ("execution_approval_not_configured",))),
    }


def verify_capacity_execution_approval(
    approval_path: Path | None,
    *,
    plan: ImmutableCapacityExecutionPlan,
    run_nonce: str | None,
    evaluated_at: datetime | None = None,
) -> Mapping[str, Any]:
    """Verify a run-bound operator approval using public keys only.

    Missing, malformed, expired, cross-role, dirty-tree, or plan-mismatched
    input is returned as an untrusted result.  Replay protection is completed
    by ``consume_capacity_execution_approval`` before any execution resource is
    opened.  This function never serializes the signature bytes.
    """
    if not _is_immutable_capacity_execution_plan(plan):
        return _execution_approval_failure("execution_plan_not_immutable_or_canonical")
    try:
        nonce = validate_execution_run_nonce(run_nonce)
    except ValueError:
        nonce = None
    if not nonce:
        result = _execution_approval_failure("execution_run_nonce_not_configured")
        result["plan_sha256"] = plan.plan_sha256
        return result
    nonce_sha256 = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    if approval_path is None:
        result = _execution_approval_failure("execution_approval_not_configured")
        result["plan_sha256"] = plan.plan_sha256
        result["run_nonce_sha256"] = nonce_sha256
        return result

    evaluated = (evaluated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result = _execution_approval_failure("execution_approval_unverified")
    result["plan_sha256"] = plan.plan_sha256
    result["run_nonce_sha256"] = nonce_sha256
    result["evaluated_at"] = _iso_utc(evaluated)
    try:
        encoded = _secure_read_regular_file(
            Path(approval_path).expanduser(),
            max_bytes=MAX_CAPACITY_EXECUTION_APPROVAL_BYTES,
            label="capacity execution approval",
            require_owner=True,
            require_private=True,
        )
        result["approval_file_sha256"] = hashlib.sha256(encoded).hexdigest()
        payload = _strict_execution_approval_json_loads(encoded)
    except (OSError, TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        result["failure_reasons"] = ["execution_approval_unreadable_or_malformed"]
        return result

    signed_fields = (
        "schema_version",
        "algorithm",
        "key_id",
        "approval_scope",
        "plan_sha256",
        "run_nonce_sha256",
        "issued_at",
        "expires_at",
    )
    allowed_fields = set(signed_fields) | {"signature_base64"}
    unknown_fields = sorted(set(str(key) for key in payload) - allowed_fields)
    missing_fields = sorted(allowed_fields - set(str(key) for key in payload))
    schema_valid = payload.get("schema_version") == CAPACITY_EXECUTION_APPROVAL_SCHEMA
    algorithm_valid = payload.get("algorithm") == "Ed25519"
    scope_valid = payload.get("approval_scope") == CAPACITY_EXECUTION_APPROVAL_SCOPE
    key_id = payload.get("key_id")
    key_id_valid = isinstance(key_id, str) and bool(
        re.fullmatch(r"[A-Za-z0-9._-]{3,64}", key_id)
    )
    result["key_id"] = key_id if key_id_valid else None
    plan_binding_valid = bool(
        isinstance(payload.get("plan_sha256"), str)
        and secrets.compare_digest(payload["plan_sha256"], plan.plan_sha256)
    )
    run_binding_valid = bool(
        isinstance(payload.get("run_nonce_sha256"), str)
        and secrets.compare_digest(payload["run_nonce_sha256"], nonce_sha256)
    )
    result["plan_binding_valid"] = plan_binding_valid
    result["run_binding_valid"] = run_binding_valid

    try:
        issued_at = _parse_utc_datetime(
            payload.get("issued_at"), field_name="execution approval issued_at"
        )
        expires_at = _parse_utc_datetime(
            payload.get("expires_at"), field_name="execution approval expires_at"
        )
    except ValueError:
        issued_at = None
        expires_at = None
    if issued_at is not None:
        result["issued_at"] = _iso_utc(issued_at)
    if expires_at is not None:
        result["expires_at"] = _iso_utc(expires_at)
    validity_seconds = (
        (expires_at - issued_at).total_seconds()
        if issued_at is not None and expires_at is not None
        else -1.0
    )
    time_binding_valid = bool(
        issued_at is not None
        and expires_at is not None
        and 0.0 < validity_seconds <= MAX_CAPACITY_EXECUTION_APPROVAL_SECONDS
        and issued_at
        <= evaluated + timedelta(seconds=MAX_CAPACITY_EXECUTION_APPROVAL_FUTURE_SKEW_SECONDS)
        and evaluated <= expires_at
    )
    result["time_binding_valid"] = time_binding_valid

    operator_keys = _runtime_trusted_capacity_operator_public_keys()
    registered_public_key = operator_keys.get(key_id) if key_id_valid else None
    signer_allowlisted = isinstance(registered_public_key, str)
    result["signer_allowlisted"] = signer_allowlisted
    calibration_keys, telemetry_keys = _runtime_non_operator_trust_roots()
    other_ids = set(calibration_keys) | set(telemetry_keys)
    other_material = {
        str(value)
        for value in (*calibration_keys.values(), *telemetry_keys.values())
        if isinstance(value, str)
    }
    operator_role_separated = bool(
        signer_allowlisted
        and key_id not in other_ids
        and registered_public_key not in other_material
    )
    result["operator_role_separated"] = operator_role_separated

    signature_text = payload.get("signature_base64")
    signature_valid = False
    key_material_valid = False
    if (
        Ed25519PublicKey is not None
        and signer_allowlisted
        and isinstance(signature_text, str)
        and schema_valid
        and algorithm_valid
        and scope_valid
        and not unknown_fields
        and not missing_fields
    ):
        try:
            public_bytes = base64.b64decode(
                registered_public_key.encode("ascii"), validate=True
            )
            signature = base64.b64decode(signature_text.encode("ascii"), validate=True)
            key_material_valid = len(public_bytes) == 32 and len(signature) == 64
            if key_material_valid:
                message = _canonical_execution_json_bytes(
                    {field: payload.get(field) for field in signed_fields}
                )
                Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, message)
                signature_valid = True
        except (InvalidSignature, ValueError, TypeError, UnicodeError, binascii.Error):
            signature_valid = False
    result["signature_valid"] = signature_valid

    runtime_checks, runtime_failures = _capacity_execution_runtime_binding_status(plan)
    result.update(runtime_checks)

    failures: list[str] = []
    if unknown_fields or missing_fields:
        failures.append("execution_approval_schema_fields")
    if not schema_valid:
        failures.append("execution_approval_schema")
    if not algorithm_valid:
        failures.append("execution_approval_algorithm")
    if not scope_valid:
        failures.append("execution_approval_scope")
    if not key_id_valid:
        failures.append("execution_approval_key_id")
    if not signer_allowlisted:
        failures.append("execution_approval_signer_not_allowlisted")
    if signer_allowlisted and not key_material_valid:
        failures.append("execution_approval_public_key_or_signature_encoding")
    if not operator_role_separated:
        failures.append("execution_approval_operator_role_not_separated")
    if not plan_binding_valid:
        failures.append("execution_approval_plan_binding")
    if not run_binding_valid:
        failures.append("execution_approval_run_binding")
    if not time_binding_valid:
        failures.append("execution_approval_time_binding")
    if not signature_valid:
        failures.append("execution_approval_signature")
    failures.extend(runtime_failures)

    if failures:
        result["failure_reasons"] = sorted(set(failures))
        return result
    verified = _VerifiedCapacityExecutionApproval(result)
    verified.update(
        {
            "status": "trusted_operator_execution_approval",
            "trusted": True,
            "failure_reasons": [],
        }
    )
    verified._verification_capability = _EXECUTION_APPROVAL_CAPABILITY
    verified._verified_content_sha256 = _canonical_json_sha256(dict(verified))
    return verified


def is_verified_capacity_execution_approval(value: Any) -> bool:
    if not isinstance(value, _VerifiedCapacityExecutionApproval):
        return False
    if getattr(value, "_verification_capability", None) is not _EXECUTION_APPROVAL_CAPABILITY:
        return False
    try:
        observed = _canonical_json_sha256(dict(value))
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False
    return secrets.compare_digest(
        observed,
        str(getattr(value, "_verified_content_sha256", "")),
    )


def _open_private_capacity_nonce_ledger_dir(ledger_dir: Path) -> tuple[Path, int]:
    """Create/open an owner-only ledger directory without following its leaf."""
    expanded = Path(ledger_dir).expanduser()
    try:
        try:
            leaf_metadata = os.lstat(expanded)
        except FileNotFoundError:
            leaf_metadata = None
        if leaf_metadata is not None and stat.S_ISLNK(leaf_metadata.st_mode):
            raise OSError("nonce ledger directory must not be a symlink")
        normalized = expanded.resolve(strict=False)
        normalized.parent.mkdir(parents=True, exist_ok=True)
        try:
            normalized.mkdir(mode=0o700)
        except FileExistsError:
            pass
        metadata = os.lstat(normalized)
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise OSError("nonce ledger path is not a real directory")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise OSError("nonce ledger directory is not owned by the current user")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise OSError("nonce ledger directory permissions are not owner-only")
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        directory_fd = os.open(normalized, flags)
        opened = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (hasattr(os, "getuid") and opened.st_uid != os.getuid())
            or stat.S_IMODE(opened.st_mode) & 0o077
        ):
            os.close(directory_fd)
            raise OSError("nonce ledger directory changed during secure open")
        return normalized, directory_fd
    except (OSError, TypeError, ValueError):
        raise


def _execution_approval_consumption_failure(
    approval: Mapping[str, Any],
    *reasons: str,
) -> dict[str, Any]:
    result = public_capacity_execution_approval(approval)
    result.update(
        {
            "status": "approval_nonce_consumption_failed",
            "trusted": False,
            "nonce_consumed": False,
            "consumption_status": "failed_closed",
            "consumption_record_sha256": None,
            "failure_reasons": sorted(
                set(reasons or ("execution_approval_nonce_consumption_failed",))
            ),
        }
    )
    return result


def consume_capacity_execution_approval(
    approval: Mapping[str, Any],
    *,
    plan: ImmutableCapacityExecutionPlan,
    ledger_dir: Path,
    consumed_at: datetime | None = None,
) -> Mapping[str, Any]:
    """Atomically consume a verified run nonce exactly once.

    The O_EXCL record key is derived from the already-verified nonce hash.  No
    raw nonce, signature, token, path, or private key is written to the ledger.
    Runtime code/worktree bindings are re-checked immediately before the claim
    to close the verification-to-execution gap.
    """
    if not is_verified_capacity_execution_approval(approval):
        return _execution_approval_consumption_failure(
            approval,
            "execution_approval_not_verified_for_consumption",
        )
    if not _is_immutable_capacity_execution_plan(plan):
        return _execution_approval_consumption_failure(
            approval,
            "execution_plan_not_immutable_or_canonical",
        )

    try:
        plan_nonce_sha256 = plan["run_binding"]["execution_run_nonce_sha256"]
        consumption_contract = plan["approval_consumption"]
        planned_ledger_sha256 = consumption_contract["ledger_dir_path_sha256"]
        actual_ledger_sha256 = capacity_path_binding_sha256(ledger_dir)
    except (KeyError, TypeError, ValueError):
        return _execution_approval_consumption_failure(
            approval,
            "capacity_execution_nonce_ledger_binding",
        )
    approval_nonce_sha256 = approval.get("run_nonce_sha256")
    plan_binding_valid = bool(
        isinstance(approval.get("plan_sha256"), str)
        and secrets.compare_digest(str(approval["plan_sha256"]), plan.plan_sha256)
    )
    nonce_binding_valid = bool(
        isinstance(approval_nonce_sha256, str)
        and isinstance(plan_nonce_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", approval_nonce_sha256)
        and secrets.compare_digest(approval_nonce_sha256, plan_nonce_sha256)
    )
    ledger_binding_valid = bool(
        consumption_contract.get("single_use_required") is True
        and consumption_contract.get("atomic_create_required") is True
        and isinstance(planned_ledger_sha256, str)
        and isinstance(actual_ledger_sha256, str)
        and secrets.compare_digest(planned_ledger_sha256, actual_ledger_sha256)
    )
    if not plan_binding_valid:
        return _execution_approval_consumption_failure(
            approval,
            "execution_approval_plan_binding",
        )
    if not nonce_binding_valid:
        return _execution_approval_consumption_failure(
            approval,
            "execution_approval_run_binding",
        )
    if not ledger_binding_valid:
        return _execution_approval_consumption_failure(
            approval,
            "capacity_execution_nonce_ledger_binding",
        )

    runtime_checks, runtime_failures = _capacity_execution_runtime_binding_status(plan)
    if runtime_failures:
        failed = _execution_approval_consumption_failure(approval, *runtime_failures)
        failed.update(runtime_checks)
        return failed

    consumed = (consumed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        expires_at = _parse_utc_datetime(
            approval.get("expires_at"),
            field_name="execution approval expires_at",
        )
    except ValueError:
        expires_at = None
    if expires_at is None or consumed > expires_at:
        return _execution_approval_consumption_failure(
            approval,
            "execution_approval_expired_before_consumption",
        )
    record = {
        "schema_version": CAPACITY_EXECUTION_NONCE_CONSUMPTION_SCHEMA,
        "approval_scope": CAPACITY_EXECUTION_APPROVAL_SCOPE,
        "plan_sha256": plan.plan_sha256,
        "run_nonce_sha256": approval_nonce_sha256,
        "approval_file_sha256": approval.get("approval_file_sha256"),
        "key_id": approval.get("key_id"),
        "consumed_at": _iso_utc(consumed),
        "raw_nonce_persisted": False,
        "signature_persisted": False,
        "token_or_private_key_read": False,
    }
    record_bytes = _canonical_execution_json_bytes(record) + b"\n"
    record_sha256 = hashlib.sha256(record_bytes).hexdigest()
    directory_fd = -1
    record_fd = -1
    try:
        _normalized_ledger, directory_fd = _open_private_capacity_nonce_ledger_dir(
            Path(ledger_dir)
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        record_fd = os.open(
            f"nonce-{approval_nonce_sha256}.json",
            flags,
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(record_fd, "wb") as stream:
            record_fd = -1
            stream.write(record_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(directory_fd)
    except FileExistsError:
        return _execution_approval_consumption_failure(
            approval,
            "execution_approval_nonce_already_consumed",
        )
    except (OSError, TypeError, ValueError):
        return _execution_approval_consumption_failure(
            approval,
            "execution_approval_nonce_ledger_unavailable",
        )
    finally:
        if record_fd >= 0:
            os.close(record_fd)
        if directory_fd >= 0:
            os.close(directory_fd)

    result = _ConsumedCapacityExecutionApproval(dict(approval))
    result.update(runtime_checks)
    result.update(
        {
            "status": "trusted_operator_execution_approval_consumed",
            "trusted": True,
            "nonce_consumed": True,
            "consumption_status": "consumed_once",
            "consumption_record_sha256": record_sha256,
            "consumption_ledger_path_sha256": actual_ledger_sha256,
            "raw_nonce_persisted": False,
            "signature_persisted": False,
            "token_or_private_key_read": False,
            "failure_reasons": [],
        }
    )
    result._verification_capability = _EXECUTION_APPROVAL_CAPABILITY
    result._verified_content_sha256 = _canonical_json_sha256(dict(result))
    result._consumption_capability = _EXECUTION_APPROVAL_CONSUMPTION_CAPABILITY
    result._consumed_content_sha256 = _canonical_json_sha256(dict(result))
    result._execution_redeemed = False
    return result


def is_consumed_capacity_execution_approval(value: Any) -> bool:
    if not isinstance(value, _ConsumedCapacityExecutionApproval):
        return False
    if not is_verified_capacity_execution_approval(value):
        return False
    if (
        getattr(value, "_consumption_capability", None)
        is not _EXECUTION_APPROVAL_CONSUMPTION_CAPABILITY
    ):
        return False
    try:
        observed = _canonical_json_sha256(dict(value))
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False
    return bool(
        value.get("nonce_consumed") is True
        and value.get("consumption_status") == "consumed_once"
        and secrets.compare_digest(
            observed,
            str(getattr(value, "_consumed_content_sha256", "")),
        )
    )


def redeem_consumed_capacity_execution_approval(value: Any) -> bool:
    """Redeem a consumed in-process capability once before token/session access."""
    if not is_consumed_capacity_execution_approval(value):
        return False
    if getattr(value, "_execution_redeemed", True) is not False:
        return False
    value._execution_redeemed = True
    return True


def public_capacity_execution_approval(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the only approval fields allowed in reports/stdout."""
    allowed = (
        "status",
        "trusted",
        "key_id",
        "plan_sha256",
        "run_nonce_sha256",
        "approval_file_sha256",
        "issued_at",
        "expires_at",
        "evaluated_at",
        "signer_allowlisted",
        "signature_valid",
        "plan_binding_valid",
        "run_binding_valid",
        "time_binding_valid",
        "operator_role_separated",
        "worktree_clean",
        "worktree_status_binding_valid",
        "git_head_binding_valid",
        "runtime_source_bundle_binding_valid",
        "target_runtime_identity_plan_binding_valid",
        "nonce_ledger_plan_binding_present",
        "nonce_consumed",
        "consumption_status",
        "consumption_record_sha256",
        "consumption_ledger_path_sha256",
        "verifier_key_material",
        "signature_persisted",
        "raw_nonce_persisted",
        "token_or_private_key_read",
        "failure_reasons",
    )
    return {field: value.get(field) for field in allowed}


def blocked_operator_approval_report(
    args: argparse.Namespace,
    *,
    plan: ImmutableCapacityExecutionPlan,
    approval: Mapping[str, Any],
    reason: str = "operator execution approval did not pass",
) -> dict[str, Any]:
    """Build a report without consulting token, session, HTTP, DB, or browser state."""
    public_approval = public_capacity_execution_approval(approval)
    now = _iso_utc(datetime.now(timezone.utc))
    return {
        "schema_version": 4,
        "evidence_type": "blocked_local_capacity_attempt",
        "requested_live": True,
        "network_observed": False,
        "network_requests_issued": 0,
        "pressure_completed": False,
        "live_run": False,
        "synthetic_fixture": False,
        "started_at": now,
        "completed_at": now,
        "report_sha256": "computed_after_redaction",
        "capacity_execution_plan": plan.public_dict(),
        "operator_preflight": public_approval,
        "auth": {
            "sources": [],
            "token_count": None,
            "independent_session_count": int(args.session_count),
            "token_file_read": False,
            "token_emitted": False,
            "token_persisted": False,
        },
        "safety": {
            "loopback_only": True,
            "method_allowlist": ["GET"],
            "business_mutations": False,
            "provider_calls": False,
            "browser_calls": False,
            "token_file_read": False,
            "http_session_created": False,
            "automatic_stop": True,
        },
        "profiles": [
            {
                "profile": "operator_execution_approval",
                "status": "blocked",
                "blocked_reason": reason,
                "preflight": [],
                "stages": [],
            }
        ],
        "executed_stage_count": 0,
        "preflight_request_count": 0,
        "identity_preflight_request_count": 0,
        "overall_capacity": None,
        "limitations": [
            "no token file, HTTP session, network request, database, provider, or browser was accessed",
            "an operator approval is authority for this exact bounded plan only; it is not capacity evidence",
        ],
    }


__all__ = [name for name in globals() if not name.startswith("__")]

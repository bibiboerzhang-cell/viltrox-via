from __future__ import annotations

import sys

from scripts.ops.load_test_verdict import *

def redact_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "***"
                if str(key).lower() in {"token", "authorization", "password", "secret"}
                else redact_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        clean = value
        for pattern in _SECRET_PATTERNS:
            clean = pattern.sub("***", clean)
        return clean
    return value


def report_contains_secret(report: Mapping[str, Any], token: str | None) -> bool:
    encoded = json.dumps(report, ensure_ascii=False)
    if token and token in encoded:
        return True
    return any(pattern.search(encoded) for pattern in _SECRET_PATTERNS)


def _git(command: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *command], cwd=ROOT, capture_output=True, text=True, timeout=5, check=True
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _listener_snapshot(port: int) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-Fpc"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return []
    listeners: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            if current:
                listeners.append(current)
            current = {"pid": int(line[1:]) if line[1:].isdigit() else line[1:]}
        elif line.startswith("c"):
            current["command"] = line[1:]
    if current:
        listeners.append(current)
    return listeners


def resource_snapshot(ports: Sequence[int]) -> dict[str, Any]:
    listeners = {str(port): _listener_snapshot(port) for port in ports}
    pids = sorted(
        {
            int(item["pid"])
            for entries in listeners.values()
            for item in entries
            if isinstance(item.get("pid"), int)
        }
    )
    processes: list[dict[str, Any]] = []
    ps_error = ""
    if pids:
        try:
            result = subprocess.run(
                ["ps", "-o", "pid=,%cpu=,%mem=,rss=", "-p", ",".join(str(pid) for pid in pids)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            ps_error = result.stderr.strip()[:200]
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 4 and parts[0].isdigit():
                    processes.append(
                        {
                            "pid": int(parts[0]),
                            "cpu_percent": float(parts[1]),
                            "memory_percent": float(parts[2]),
                            "rss_kib": int(parts[3]),
                        }
                    )
        except Exception as exc:
            ps_error = f"{type(exc).__name__}: {str(exc)[:160]}"
    try:
        load_average = [round(float(value), 3) for value in os.getloadavg()]
    except (AttributeError, OSError):
        load_average = []
    if not pids and not ps_error:
        ps_error = "no loopback listener PIDs discovered"
    elif pids and not processes and not ps_error:
        ps_error = "ps returned no process metrics for discovered listener PIDs"
    return {
        "load_average_1m_5m_15m": load_average,
        "listeners": listeners,
        "processes": processes,
        "process_metrics_unavailable_reason": ps_error or None,
    }


_TELEMETRY_FIELDS: dict[str, tuple[str, ...]] = {
    "db_pool": (
        "active",
        "idle",
        "checked_out",
        "max_size",
        "waiting",
        "overflow",
        "checkout_wait_ms",
    ),
    "redis": (
        "connected_clients",
        "blocked_clients",
        "used_memory_bytes",
        "ops_per_sec",
        "keyspace_hits",
        "keyspace_misses",
        "evicted_keys",
    ),
}
_TELEMETRY_REASONABLE_MAX: Mapping[str, float] = MappingProxyType(
    {
        "active": 1_000_000,
        "idle": 1_000_000,
        "checked_out": 1_000_000,
        "max_size": 1_000_000,
        "waiting": 1_000_000,
        "overflow": 1_000_000,
        "checkout_wait_ms": 3_600_000,
        "connected_clients": 10_000_000,
        "blocked_clients": 10_000_000,
        "used_memory_bytes": 10**16,
        "ops_per_sec": 10**12,
        "keyspace_hits": 10**18,
        "keyspace_misses": 10**18,
        "evicted_keys": 10**18,
    }
)


def _telemetry_failure(reason: str) -> dict[str, Any]:
    return {"available": False, "value": None, "reason": reason[:200]}


def _strict_telemetry_json_loads(encoded: bytes) -> Mapping[str, Any]:
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
        raise ValueError("snapshot root must be an object")
    return payload


def _telemetry_attestation_failure(*reasons: str, format_valid: bool = False) -> dict[str, Any]:
    return {
        "status": "untrusted_external_input",
        "trusted": False,
        "format_valid": format_valid,
        "signer_allowlisted": False,
        "signature_valid": False,
        "snapshot_binding_valid": False,
        "key_id": None,
        "algorithm": "Ed25519",
        "failure_reasons": sorted(set(reasons or ("telemetry_attestation_missing",))),
    }


def _runtime_trusted_telemetry_public_keys() -> Mapping[str, str]:
    compatibility_module = sys.modules.get("scripts.load_test_vkpi_readonly")
    candidate = getattr(
        compatibility_module,
        "TRUSTED_TELEMETRY_ED25519_PUBLIC_KEYS",
        TRUSTED_TELEMETRY_ED25519_PUBLIC_KEYS,
    )
    return candidate if isinstance(candidate, Mapping) else MappingProxyType({})


def _verify_telemetry_producer_attestation(payload: Mapping[str, Any]) -> dict[str, Any]:
    attestation = payload.get("producer_attestation")
    if not isinstance(attestation, Mapping):
        return _telemetry_attestation_failure("telemetry_attestation_not_object")
    expected_fields = {"schema_version", "algorithm", "key_id", "signature_base64"}
    if set(attestation) != expected_fields:
        return _telemetry_attestation_failure("telemetry_attestation_schema_mismatch")
    schema_valid = attestation.get("schema_version") == TELEMETRY_ATTESTATION_SCHEMA
    algorithm_valid = attestation.get("algorithm") == "Ed25519"
    key_id = attestation.get("key_id")
    key_id_valid = isinstance(key_id, str) and bool(
        re.fullmatch(r"[A-Za-z0-9._-]{3,64}", key_id)
    )
    signature_text = attestation.get("signature_base64")
    signature_shape_valid = isinstance(signature_text, str) and bool(signature_text)
    format_valid = schema_valid and algorithm_valid and key_id_valid and signature_shape_valid
    if not format_valid:
        return _telemetry_attestation_failure(
            "telemetry_attestation_fields_invalid",
            format_valid=False,
        )
    registered_key = _runtime_trusted_telemetry_public_keys().get(key_id)
    signer_allowlisted = isinstance(registered_key, str)
    result = _telemetry_attestation_failure(
        "telemetry_producer_not_allowlisted" if not signer_allowlisted else "telemetry_signature_invalid",
        format_valid=True,
    )
    result["key_id"] = key_id
    result["signer_allowlisted"] = signer_allowlisted
    signature_valid = False
    if signer_allowlisted and Ed25519PublicKey is not None:
        try:
            public_bytes = base64.b64decode(registered_key.encode("ascii"), validate=True)
            signature = base64.b64decode(signature_text.encode("ascii"), validate=True)
            if len(public_bytes) != 32 or len(signature) != 64:
                raise ValueError("invalid Ed25519 material length")
            signed_snapshot = {
                field: payload.get(field)
                for field in (
                    "schema_version",
                    "service",
                    "host",
                    "port",
                    "run_nonce",
                    "observed_at",
                    "sequence",
                    "metrics",
                )
            }
            message = json.dumps(
                signed_snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, message)
            signature_valid = True
        except (InvalidSignature, ValueError, TypeError, UnicodeError, binascii.Error):
            signature_valid = False
    result["signature_valid"] = signature_valid
    result["snapshot_binding_valid"] = signature_valid
    result["trusted"] = signer_allowlisted and signature_valid
    result["status"] = (
        "trusted_independent_telemetry_producer"
        if result["trusted"]
        else "untrusted_external_input"
    )
    result["failure_reasons"] = (
        []
        if result["trusted"]
        else [
            "telemetry_signature_invalid"
            if signer_allowlisted
            else "telemetry_producer_not_allowlisted"
        ]
    )
    return result


@dataclass
class TelemetrySidecarReader:
    name: str
    path: Path | None
    expected_host: str
    expected_port: int
    run_nonce: str | None
    max_age_seconds: float = MAX_TELEMETRY_AGE_SECONDS
    _last_observed_at: datetime | None = None
    _last_sequence: int | None = None

    def read(self) -> dict[str, Any]:
        result = optional_json_telemetry_adapter(
            self.name,
            self.path,
            expected_host=self.expected_host,
            expected_port=self.expected_port,
            run_nonce=self.run_nonce,
            previous_observed_at=self._last_observed_at,
            previous_sequence=self._last_sequence,
            max_age_seconds=self.max_age_seconds,
        )
        if result.get("available"):
            self._last_observed_at = _parse_utc_datetime(
                result.get("observed_at"), field_name="telemetry observed_at"
            )
            self._last_sequence = int(result.get("sequence"))
        return result


def optional_json_telemetry_adapter(
    name: str,
    path: Path | None,
    *,
    expected_host: str = "127.0.0.1",
    expected_port: int | None = None,
    run_nonce: str | None = None,
    previous_observed_at: datetime | None = None,
    previous_sequence: int | None = None,
    max_age_seconds: float = MAX_TELEMETRY_AGE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate one fresh, run-bound, service-bound local telemetry snapshot."""
    if name not in _TELEMETRY_FIELDS:
        raise ValueError(f"unknown telemetry adapter: {name}")
    if path is None:
        return {"available": False, "value": None, "reason": "not_configured"}
    if expected_port is None or not run_nonce:
        return _telemetry_failure("strict_contract_not_configured")
    candidate = Path(path).expanduser()
    try:
        encoded = _secure_read_regular_file(
            candidate,
            max_bytes=MAX_TELEMETRY_FILE_BYTES,
            label="telemetry snapshot",
            require_owner=True,
            require_private=True,
        )
        payload = _strict_telemetry_json_loads(encoded)
        expected_keys = {
            "schema_version",
            "service",
            "host",
            "port",
            "run_nonce",
            "observed_at",
            "sequence",
            "metrics",
            "producer_attestation",
        }
        if set(payload) != expected_keys:
            raise ValueError("snapshot fields must exactly match the strict v1 schema")
        if payload.get("schema_version") != TELEMETRY_SIDECAR_SCHEMA:
            raise ValueError("snapshot schema_version is unsupported")
        if payload.get("service") != name:
            raise ValueError("snapshot service binding mismatch")
        if str(payload.get("host") or "") != str(expected_host):
            raise ValueError("snapshot host binding mismatch")
        if not isinstance(payload.get("port"), int) or isinstance(payload.get("port"), bool):
            raise ValueError("snapshot port must be an integer")
        if int(payload["port"]) != int(expected_port):
            raise ValueError("snapshot port binding mismatch")
        if not isinstance(payload.get("run_nonce"), str) or not secrets.compare_digest(
            payload["run_nonce"], run_nonce
        ):
            raise ValueError("snapshot run nonce binding mismatch")
        observed_at = _parse_utc_datetime(payload.get("observed_at"), field_name="telemetry observed_at")
        evaluated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        age_seconds = (evaluated_at - observed_at).total_seconds()
        if age_seconds > float(max_age_seconds):
            raise ValueError("snapshot is stale")
        if age_seconds < -MAX_TELEMETRY_FUTURE_SKEW_SECONDS:
            raise ValueError("snapshot is from the future")
        sequence = payload.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise ValueError("snapshot sequence must be a non-negative integer")
        if previous_observed_at is not None and observed_at <= previous_observed_at:
            raise ValueError("snapshot observed_at did not advance")
        if previous_sequence is not None and sequence <= previous_sequence:
            raise ValueError("snapshot sequence did not advance")
        raw_metrics = payload.get("metrics")
        if not isinstance(raw_metrics, Mapping):
            raise ValueError("snapshot metrics must be an object")
        required_fields = set(_TELEMETRY_FIELDS[name])
        if set(raw_metrics) != required_fields:
            raise ValueError("snapshot metrics must contain the complete service metric set")
        metrics: dict[str, int | float] = {}
        for field_name in _TELEMETRY_FIELDS[name]:
            value = raw_metrics.get(field_name)
            if not _is_number(value):
                raise ValueError(f"snapshot metric {field_name} must be finite numeric")
            numeric = float(value)
            if numeric < 0.0 or numeric > _TELEMETRY_REASONABLE_MAX[field_name]:
                raise ValueError(f"snapshot metric {field_name} is outside its reasonable range")
            metrics[field_name] = value
        producer_attestation = _verify_telemetry_producer_attestation(payload)
        if producer_attestation.get("format_valid") is not True:
            raise ValueError("snapshot producer attestation does not match the strict schema")
        return {
            "available": True,
            "value": metrics,
            "reason": None,
            "source": "strict_run_bound_local_json_sidecar",
            "schema_version": TELEMETRY_SIDECAR_SCHEMA,
            "service": name,
            "host": expected_host,
            "port": int(expected_port),
            "observed_at": _iso_utc(observed_at),
            "sequence": sequence,
            "run_nonce_sha256": hashlib.sha256(run_nonce.encode("utf-8")).hexdigest(),
            "external_input_trust": producer_attestation.get("status"),
            "producer_attestation": producer_attestation,
        }
    except Exception as exc:  # noqa: BLE001 - telemetry absence must not fail a trial
        return _telemetry_failure(f"{type(exc).__name__}: {str(exc)[:160]}")


def summarize_resource_telemetry(
    samples: Sequence[Mapping[str, Any]],
    *,
    required_listeners: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    process_samples: list[Sequence[Mapping[str, Any]]] = []
    load_samples: list[Sequence[float]] = []
    observed_pids: set[int] = set()
    unavailable_reasons: set[str] = set()
    event_loop_lag_ms: list[float] = []
    optional_available_count = {"db_pool": 0, "redis": 0}
    optional_trusted_count = {"db_pool": 0, "redis": 0}
    optional_last: dict[str, Any] = {"db_pool": None, "redis": None}
    optional_key_ids: dict[str, set[str]] = {"db_pool": set(), "redis": set()}
    optional_reasons: dict[str, set[str]] = {"db_pool": set(), "redis": set()}
    listener_checks: dict[str, dict[str, Any]] = {
        service: {
            "port": int(port),
            "samples_with_listener": 0,
            "samples_with_process_metrics": 0,
        }
        for service, port in sorted((required_listeners or {}).items())
    }
    for sample in samples:
        snapshot = sample.get("snapshot") if isinstance(sample.get("snapshot"), Mapping) else {}
        processes = snapshot.get("processes") if isinstance(snapshot.get("processes"), Sequence) else []
        valid_processes = [item for item in processes if isinstance(item, Mapping)]
        process_pids = {
            int(item["pid"])
            for item in valid_processes
            if isinstance(item.get("pid"), int) and not isinstance(item.get("pid"), bool)
        }
        process_samples.append(valid_processes)
        for process in valid_processes:
            if isinstance(process.get("pid"), int):
                observed_pids.add(int(process["pid"]))
        loads = snapshot.get("load_average_1m_5m_15m")
        if isinstance(loads, Sequence) and not isinstance(loads, (str, bytes)) and loads:
            load_samples.append([float(value) for value in loads])
        reason = snapshot.get("process_metrics_unavailable_reason")
        if reason:
            unavailable_reasons.add(str(reason)[:200])
        lag = sample.get("event_loop_lag_ms")
        if isinstance(lag, (int, float)):
            event_loop_lag_ms.append(max(0.0, float(lag)))
        adapters = snapshot.get("optional_adapters") if isinstance(snapshot.get("optional_adapters"), Mapping) else {}
        for name in ("db_pool", "redis"):
            adapter = adapters.get(name) if isinstance(adapters.get(name), Mapping) else {}
            if adapter.get("available"):
                optional_available_count[name] += 1
                optional_last[name] = adapter.get("value")
                attestation = adapter.get("producer_attestation")
                if isinstance(attestation, Mapping) and attestation.get("trusted") is True:
                    optional_trusted_count[name] += 1
                    if isinstance(attestation.get("key_id"), str):
                        optional_key_ids[name].add(str(attestation["key_id"]))
            elif adapter.get("reason"):
                optional_reasons[name].add(str(adapter["reason"])[:200])
        listeners = snapshot.get("listeners") if isinstance(snapshot.get("listeners"), Mapping) else {}
        for service, check in listener_checks.items():
            entries = listeners.get(str(check["port"]))
            entries = entries if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes)) else []
            listener_pids = {
                int(item["pid"])
                for item in entries
                if isinstance(item, Mapping)
                and isinstance(item.get("pid"), int)
                and not isinstance(item.get("pid"), bool)
            }
            if listener_pids:
                check["samples_with_listener"] += 1
            if listener_pids & process_pids:
                check["samples_with_process_metrics"] += 1

    combined_cpu = [sum(float(item.get("cpu_percent") or 0.0) for item in group) for group in process_samples]
    combined_rss = [sum(int(item.get("rss_kib") or 0) for item in group) for group in process_samples]
    process_metrics_available = any(bool(group) for group in process_samples)
    return {
        "sample_count": len(samples),
        "process_metrics_available": process_metrics_available,
        "observed_listener_process_pids": sorted(observed_pids),
        "peak_combined_process_cpu_percent": round(max(combined_cpu), 2)
        if process_metrics_available
        else None,
        "peak_combined_process_rss_kib": max(combined_rss) if process_metrics_available else None,
        "peak_load_average_1m": round(max((loads[0] for loads in load_samples), default=0.0), 3)
        if load_samples
        else None,
        "unavailable_reasons": sorted(unavailable_reasons),
        "event_loop_lag_ms": {
            "available": bool(event_loop_lag_ms),
            "p50": round(percentile(event_loop_lag_ms, 50), 3) if event_loop_lag_ms else None,
            "p95": round(percentile(event_loop_lag_ms, 95), 3) if event_loop_lag_ms else None,
            "max": round(max(event_loop_lag_ms), 3) if event_loop_lag_ms else None,
        },
        "optional_adapters": {
            name: {
                "available": bool(samples)
                and optional_available_count[name] == len(samples),
                "available_sample_count": optional_available_count[name],
                "required_sample_count": len(samples),
                "all_samples_fresh_bound_and_advancing": bool(samples)
                and optional_available_count[name] == len(samples),
                "all_samples_trusted_independent_producer": bool(samples)
                and optional_trusted_count[name] == len(samples),
                "trusted_sample_count": optional_trusted_count[name],
                "trusted_producer_key_ids": sorted(optional_key_ids[name]),
                "last_value": optional_last[name]
                if optional_available_count[name] == len(samples) and samples
                else None,
                "unavailable_reasons": sorted(optional_reasons[name]),
            }
            for name in ("db_pool", "redis")
        },
        "listener_process_coverage": {
            "pass": bool(listener_checks)
            and bool(samples)
            and all(
                check["samples_with_listener"] == len(samples)
                and check["samples_with_process_metrics"] == len(samples)
                for check in listener_checks.values()
            ),
            "required_services": listener_checks,
            "required_sample_count": len(samples),
        },
        "metric_semantics": (
            "listener PID CPU is ps lifetime-average %CPU; RSS is point-in-time KiB; "
            "generator and unrelated host load are not isolated"
        ),
    }


async def _safe_resource_sample(
    ports: Sequence[int],
    *,
    started: float,
    snapshotter: Callable[[Sequence[int]], Mapping[str, Any]],
    adapter_readers: Mapping[str, TelemetrySidecarReader] | None = None,
    event_loop_lag_ms: float | None = None,
) -> dict[str, Any]:
    try:
        snapshot = await asyncio.to_thread(snapshotter, ports)
    except Exception as exc:  # pragma: no cover - defensive host-tool isolation
        snapshot = {
            "load_average_1m_5m_15m": [],
            "listeners": {},
            "processes": [],
            "process_metrics_unavailable_reason": f"{type(exc).__name__}: {str(exc)[:160]}",
        }
    snapshot = dict(snapshot)
    snapshot["optional_adapters"] = {
        name: (
            adapter_readers[name].read()
            if adapter_readers and name in adapter_readers
            else {"available": False, "value": None, "reason": "not_configured"}
        )
        for name in ("db_pool", "redis")
    }
    return {
        "captured_at": utc_now(),
        "offset_seconds": round(time.perf_counter() - started, 4),
        "event_loop_lag_ms": round(float(event_loop_lag_ms), 4) if event_loop_lag_ms is not None else None,
        "snapshot": snapshot,
    }


async def run_with_resource_telemetry(
    operation: Awaitable[_T],
    *,
    ports: Sequence[int],
    sample_interval_seconds: float,
    snapshotter: Callable[[Sequence[int]], Mapping[str, Any]] = resource_snapshot,
    adapter_readers: Mapping[str, TelemetrySidecarReader] | None = None,
    required_listeners: Mapping[str, int] | None = None,
    adapter_paths: Mapping[str, Path | None] | None = None,
) -> tuple[_T, dict[str, Any]]:
    """Observe a stage without failing it when host process tools are unavailable."""
    if adapter_readers is None and adapter_paths:
        adapter_readers = {
            name: TelemetrySidecarReader(name, adapter_paths.get(name), "127.0.0.1", 0, None)
            for name in ("db_pool", "redis")
        }
    started = time.perf_counter()
    samples = [
        await _safe_resource_sample(
            ports,
            started=started,
            snapshotter=snapshotter,
            adapter_readers=adapter_readers,
            event_loop_lag_ms=0.0,
        )
    ]
    task = asyncio.ensure_future(operation)
    interval = max(_RESOURCE_SAMPLE_MIN_SECONDS, sample_interval_seconds)
    try:
        while True:
            scheduled = time.perf_counter() + interval
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(task), timeout=interval
                )
                break
            except asyncio.TimeoutError:
                lag_ms = max(0.0, (time.perf_counter() - scheduled) * 1000.0)
                samples.append(
                    await _safe_resource_sample(
                        ports,
                        started=started,
                        snapshotter=snapshotter,
                        adapter_readers=adapter_readers,
                        event_loop_lag_ms=lag_ms,
                    )
                )
    finally:
        samples.append(
            await _safe_resource_sample(
                ports,
                started=started,
                snapshotter=snapshotter,
                adapter_readers=adapter_readers,
                event_loop_lag_ms=0.0,
            )
        )
    return result, {
        "sample_interval_seconds": interval,
        "samples": samples,
        "summary": summarize_resource_telemetry(
            samples,
            required_listeners=required_listeners,
        ),
    }


def environment_snapshot(frontend_base: str, backend_base: str, postgres_port: int, redis_port: int) -> dict[str, Any]:
    frontend_port = urlparse(frontend_base).port or (443 if frontend_base.startswith("https") else 80)
    backend_port = urlparse(backend_base).port or (443 if backend_base.startswith("https") else 80)
    dirty_text = _git(["status", "--short"])
    return {
        "evidence_scope": "single local workstation; loopback; no WAN, TLS proxy, CDN, or cloud autoscaling",
        "repo_root": str(ROOT),
        "git_branch": _git(["branch", "--show-current"]),
        "git_head": _git(["rev-parse", "HEAD"]),
        "dirty_entry_count": 0 if not dirty_text else len(dirty_text.splitlines()),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "logical_cpu_count": os.cpu_count(),
        "frontend_base": frontend_base,
        "backend_base": backend_base,
        "listeners": resource_snapshot((frontend_port, backend_port, postgres_port, redis_port))["listeners"],
    }

__all__ = [name for name in globals() if not name.startswith("__")]

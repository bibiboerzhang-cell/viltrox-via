from __future__ import annotations

import json
from pathlib import Path

from scripts.ops.audit_runtime_media_log_leaks import audit_files, load_baseline_offsets, main


def _category(report: dict, segment: str, name: str) -> dict:
    return report["files"][0][segment]["categories"][name]


def test_streaming_audit_separates_historical_and_growth_without_echoing_secrets(tmp_path: Path) -> None:
    log_path = tmp_path / "worker.log"
    historical_secret = "HISTORICAL_ACCESS_KEY_VALUE"
    historical = (
        b"safe line\n"
        + f"Authorization: AWS4-HMAC-SHA256 Credential={historical_secret}/date/auto/s3/aws4_request\n".encode()
        + b"GET /api/admin/vkpi/media/image-proxy?url=https%3A%2F%2Fcdn.invalid%2Fa%3Fx-signature%3DOLD_SIGNED_VALUE\n"
    )
    log_path.write_bytes(historical)
    baseline_offset = len(historical)
    growth_secret = "NEW_SIGNATURE_VALUE"
    token_secret = "NEW_TOKEN_VALUE"
    with log_path.open("ab") as handle:
        handle.write(
            f"GET /object?X-Amz-Credential=NEW_ACCESS_VALUE&X-Amz-Signature={growth_secret}\n".encode()
        )
        handle.write(f"GET /asset?access_token={token_secret}&client_secret=NEW_CLIENT_SECRET\n".encode())

    before = log_path.read_bytes()
    report = audit_files(
        [log_path],
        root=tmp_path,
        baseline_offsets={"worker.log": baseline_offset},
        max_ranges=10,
    )
    after = log_path.read_bytes()
    serialized = json.dumps(report, sort_keys=True)

    assert before == after
    assert report["status"] == "new_findings"
    assert report["files"][0]["baseline_offset"] == baseline_offset
    assert report["files"][0]["growth_byte_range"][0] == baseline_offset
    assert _category(report, "historical", "aws_authorization_credential")["occurrences"] == 1
    assert _category(report, "historical", "media_proxy_embedded_url")["first_line"] == 3
    assert _category(report, "growth", "aws_query_credential")["first_line"] == 4
    assert _category(report, "growth", "aws_query_signature")["occurrences"] == 1
    assert _category(report, "growth", "query_token")["occurrences"] == 1
    assert _category(report, "growth", "query_secret")["occurrences"] == 1
    for secret in (
        historical_secret,
        "OLD_SIGNED_VALUE",
        "NEW_ACCESS_VALUE",
        growth_secret,
        token_secret,
        "NEW_CLIENT_SECRET",
    ):
        assert secret not in serialized


def test_previous_scan_state_becomes_next_read_only_baseline(tmp_path: Path) -> None:
    log_path = tmp_path / "admin.log"
    log_path.write_text("GET /api/admin/vkpi/media/video-proxy?url=https%3A%2F%2Fcdn.invalid%2Fold\n")
    first = audit_files([log_path], root=tmp_path)
    assert first["status"] == "historical_findings_only"
    assert first["summary"]["growth_occurrences"] == 0

    state_path = tmp_path / "baseline.json"
    state_path.write_text(json.dumps(first))
    offsets = load_baseline_offsets(state_path)
    with log_path.open("a") as handle:
        handle.write("GET /next?token=NEW_VALUE\n")

    second = audit_files([log_path], root=tmp_path, baseline_offsets=offsets)
    assert second["status"] == "new_findings"
    assert second["summary"]["historical_occurrences"] == 1
    assert second["summary"]["growth_occurrences"] == 1
    assert _category(second, "growth", "query_token")["first_line"] == 2
    assert "NEW_VALUE" not in json.dumps(second)


def test_percent_encoded_and_double_encoded_sensitive_queries_are_detected(tmp_path: Path) -> None:
    log_path = tmp_path / "encoded.log"
    log_path.write_bytes(
        b"GET /proxy?url=https%253A%252F%252Fhost%252Fpath%253FX-Amz-Credential%253DVALUE%2526X-Amz-Signature%253DVALUE2\n"
        b"GET /proxy?url=https%3A%2F%2Fhost%2Fpath%3Fapi_key%3DVALUE3%26secret%3DVALUE4\n"
    )

    report = audit_files([log_path], root=tmp_path)
    historical = report["files"][0]["historical"]["categories"]
    assert historical["aws_query_credential"]["occurrences"] == 1
    assert historical["aws_query_signature"]["occurrences"] == 1
    assert historical["query_api_or_access_key"]["occurrences"] == 1
    assert historical["query_secret"]["occurrences"] == 1
    assert "VALUE" not in json.dumps(report)


def test_cli_is_redacted_by_default_and_fail_on_new_is_opt_in(tmp_path: Path, capsys) -> None:
    log_path = tmp_path / "runtime.log"
    log_path.write_text("GET /asset?token=CLI_SECRET\n")

    default_exit = main(["--root", str(tmp_path), str(log_path), "--compact"])
    default_output = capsys.readouterr().out
    assert default_exit == 0
    assert "CLI_SECRET" not in default_output
    assert json.loads(default_output)["status"] == "historical_findings_only"

    fail_exit = main(
        [
            "--root",
            str(tmp_path),
            str(log_path),
            "--baseline-offset",
            f"{log_path}=0",
            "--fail-on-new",
            "--compact",
        ]
    )
    fail_output = capsys.readouterr().out
    assert fail_exit == 3
    assert "CLI_SECRET" not in fail_output
    assert json.loads(fail_output)["status"] == "new_findings"


def test_truncated_file_is_incomplete_instead_of_claiming_clean(tmp_path: Path) -> None:
    log_path = tmp_path / "truncated.log"
    log_path.write_text("safe\n")
    report = audit_files([log_path], root=tmp_path, baseline_offsets={"truncated.log": 999})

    assert report["status"] == "incomplete"
    assert report["summary"]["truncated_files"] == 1
    assert report["files"][0]["truncated_since_baseline"] is True
    assert report["files"][0]["raw_content_included"] is False


def test_bound_complete_baseline_cli_contract_is_enforced(tmp_path: Path, capsys) -> None:
    logs = tmp_path / "runtime" / "logs"
    logs.mkdir(parents=True)
    worker = logs / "worker.log"
    admin = logs / "admin-8102-access.log"
    worker.write_text("worker ready\n")
    admin.write_text("GET /health 200\n")
    boot_sha = "d" * 64
    not_before = "2026-07-14T05:00:00Z"

    baseline_exit = main(
        [
            "--root",
            str(tmp_path),
            "--worker-boot-nonce-sha256",
            boot_sha,
            "--worker-not-before",
            not_before,
            "--compact",
        ]
    )
    baseline = json.loads(capsys.readouterr().out)
    assert baseline_exit == 0
    assert baseline["schema_version"] == 2
    assert baseline["runtime_binding"]["worker_boot_nonce_sha256"] == boot_sha
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline))

    admin.write_text(admin.read_text() + "GET /api/auth/me 200\n")
    canary_exit = main(
        [
            "--root",
            str(tmp_path),
            "--baseline-state",
            str(baseline_path),
            "--worker-boot-nonce-sha256",
            boot_sha,
            "--worker-not-before",
            not_before,
            "--require-complete-baseline",
            "--fail-on-new",
            "--compact",
        ]
    )
    canary = json.loads(capsys.readouterr().out)
    assert canary_exit == 0
    assert canary["summary"]["growth_occurrences"] == 0
    assert sum(row["growth_bytes_scanned"] for row in canary["files"]) > 0
    assert {row["baseline_source"] for row in canary["files"]} == {"provided"}


def test_complete_baseline_rejects_wrong_worker_binding(tmp_path: Path, capsys) -> None:
    logs = tmp_path / "runtime" / "logs"
    logs.mkdir(parents=True)
    for name in ("worker.log", "admin-8102-access.log"):
        (logs / name).write_text("safe\n")
    baseline_exit = main(
        [
            "--root",
            str(tmp_path),
            "--worker-boot-nonce-sha256",
            "d" * 64,
            "--worker-not-before",
            "2026-07-14T05:00:00Z",
            "--compact",
        ]
    )
    baseline = capsys.readouterr().out
    assert baseline_exit == 0
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(baseline)

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "--baseline-state",
            str(baseline_path),
            "--worker-boot-nonce-sha256",
            "e" * 64,
            "--worker-not-before",
            "2026-07-14T05:00:00Z",
            "--require-complete-baseline",
            "--compact",
        ]
    )
    error = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert error["error_category"] == "baseline_runtime_binding_mismatch"

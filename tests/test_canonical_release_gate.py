from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_python_compile  # noqa: E402
import check_repo_hardening  # noqa: E402


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_every_release_entrypoint_delegates_to_the_canonical_gate() -> None:
    wrapper = _read("scripts/verify_repo.sh")
    workflow = _read(".github/workflows/verify.yml")
    makefile = _read("Makefile")
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    runbook = _read("docs/OPERATIONS_RUNBOOK.md")

    assert 'exec bash "$ROOT/scripts/verify.sh" "$@"' in wrapper
    active_wrapper_lines = [
        line.strip()
        for line in wrapper.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert active_wrapper_lines == [
        "set -euo pipefail",
        'ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"',
        'exec bash "$ROOT/scripts/verify.sh" "$@"',
    ]
    assert "bash ./scripts/verify_repo.sh" in workflow
    assert makefile.split("verify:\n", 1)[1].strip() == "@bash scripts/verify.sh"
    assert "VKPI_VERIFY_REQUIRE_RUNTIME=1 VKPI_VERIFY_REQUIRE_CLEAN_WORKTREE=1" in deploy
    assert 'bash "${PROJECT_ROOT}/scripts/verify.sh"' in deploy
    assert "bash ./scripts/verify.sh" in runbook
    assert "only canonical repository/release gate" in runbook


def test_canonical_gate_contains_the_union_of_all_reviewed_checks() -> None:
    gate = _read("scripts/verify.sh")
    required_in_order = (
        "release_candidate_worktree",
        "generate_frontend_contracts.py\" --check",
        "check_silent_exception_baseline.py",
        "check_repo_hardening.py",
        "--warning-baseline",
        "-m alembic",
        "check_python_compile.py",
        "-m pytest",
        "npm test",
        "tsc --noEmit",
        "npm run build -- --outDir",
        "check_chunk_graph.py",
        "redline_fit_score",
        "check_line_guard.py",
        "verify_runtime_health.py",
        "local_release_acceptance.py",
        "capture_browser_console_cdp.mjs",
        "verify_browser_console_capture.py",
        "audit_runtime_media_log_leaks.py",
    )
    cursor = 0
    for token in required_in_order:
        cursor = gate.index(token, cursor) + len(token)
    assert '"$ROOT/frontend/dist/assets"' not in gate
    assert "mktemp -d" in gate
    assert "VKPI_VERIFY_REQUIRE_RUNTIME" in gate
    assert "--json-out" in gate
    assert 'value="$(tr -d \'[:space:]\' < "$ROOT/BUILD_GIT_SHA")"' in gate
    assert '--expected-head "$(release_head)"' in gate


def test_browser_console_gate_is_explicit_strict_and_fail_closed() -> None:
    gate = _read("scripts/verify.sh")
    capture_at = gate.index('"$ROOT/scripts/capture_browser_console_cdp.mjs"')
    verifier_at = gate.index('"$ROOT/scripts/verify_browser_console_capture.py"')
    live_assertion_at = gate.index('claims.get("live_extension_free_run_completed")')
    assert capture_at < verifier_at < live_assertion_at

    for required in (
        "VKPI_VERIFY_REQUIRE_BROWSER_CONSOLE",
        "VKPI_VERIFY_REQUIRE_RUNTIME",
        'RUNTIME_VERIFICATION_STATE" != "verified',
        'ACCEPTANCE_VERIFICATION_STATE" != "verified',
        "VKPI_BROWSER_GATE_URL",
        "VKPI_BROWSER_GATE_TOKEN",
        "VKPI_CHROME_PATH",
        "VKPI_BROWSER_CONSOLE_EVIDENCE_DIR",
        "overall.get(\"release_eligible\") is not True",
        'capture.get("run_kind") != "live"',
        'BROWSER_CONSOLE_VERIFICATION_STATE" != "verified',
        "未启动 Chrome",
        "不是完整浏览器发布验收",
    ):
        assert required in gate

    # The default path exits the gate function before the only Node capture
    # invocation, and the verifier has no fixture escape hatch in verify.sh.
    opt_in_guard_at = gate.index(
        'if ! truthy_env "${VKPI_VERIFY_REQUIRE_BROWSER_CONSOLE:-0}"; then'
    )
    not_requested_return_at = gate.index("return 0", opt_in_guard_at)
    assert opt_in_guard_at < not_requested_return_at < capture_at
    assert "--allow-fixture" not in gate
    assert "chmod 700 --" not in gate
    assert "chmod 600 --" not in gate


def test_post_restart_runtime_log_canary_is_ordered_and_fail_closed() -> None:
    gate = _read("scripts/verify.sh")
    verifier = _read("scripts/verify_runtime_log_canary.py")
    browser_verify_at = gate.index('claims.get("live_extension_free_run_completed")')
    scanner_at = gate.index('"$ROOT/scripts/ops/audit_runtime_media_log_leaks.py"')
    verdict_at = gate.index('"$ROOT/scripts/verify_runtime_log_canary.py"')
    assert browser_verify_at < scanner_at < verdict_at
    for required in (
        "VKPI_VERIFY_STRICT_POST_RESTART",
        "VKPI_EXPECTED_WORKER_BOOT_NONCE_SHA256",
        "VKPI_WORKER_NOT_BEFORE",
        "VKPI_VERIFY_REQUIRE_RUNTIME_LOG_CANARY",
        "VKPI_RUNTIME_LOG_BASELINE_STATE",
        "VKPI_RUNTIME_LOG_CANARY_JSON_OUT",
        "--baseline-state",
        "--worker-boot-nonce-sha256",
        "--require-complete-baseline",
        "--fail-on-new",
        "runtime/logs/admin-8102-error.log",
        "runtime/logs/worker-interactive.log",
        'expected_logs+=("runtime/logs/worker-bulk${index}.log")',
        'expected_logs+=("runtime/logs/worker-${index}.log")',
        '"${expected_logs[@]}"',
        "--expected-log",
        "--expected-worker-count",
        "--expected-redis-worker-count",
        "expected runtime log is missing, unreadable, or a symlink",
        "expected runtime log changed during canary",
        "legacy worker log presence changed during canary",
        'RUNTIME_LOG_CANARY_STATE" != "verified',
        'BROWSER_CONSOLE_VERIFICATION_STATE" != "verified',
        "strict post-restart worker identity binding",
        "append_failed_step_once \"$RUNTIME_LOG_CANARY_STEP_NAME\"",
    ):
        assert required in gate
    for required in (
        '"unscanned_tail_files"',
        'row.get("baseline_source") != "provided"',
        "baseline predates the reviewed worker restart",
        "worker boot binding does not match",
        "canary observed no post-baseline log bytes",
        'safety.get("raw_content_included") is not False',
        "expected runtime log manifest does not match the reviewed fleet shape",
    ):
        assert required in verifier


def test_strict_runtime_gate_supports_a_rotating_multi_worker_fleet() -> None:
    gate = _read("scripts/verify.sh")
    strict_at = gate.index('if truthy_env "${VKPI_VERIFY_STRICT_POST_RESTART:-0}"; then')
    redis_at = gate.index("local require_redis_worker=", strict_at)
    strict_block = gate[strict_at:redis_at]

    assert "VKPI_EXPECTED_WORKER_COUNT" in strict_block
    assert '--expected-worker-count "$VKPI_EXPECTED_WORKER_COUNT"' in strict_block
    assert "worker count or boot nonce" in strict_block
    assert '--worker-not-before "$VKPI_WORKER_NOT_BEFORE"' in strict_block


def test_reviewed_warning_ratchet_is_fail_closed_and_not_raised_to_current_debt(
    tmp_path: Path,
) -> None:
    payload = json.loads(_read("scripts/hardening_warning_baseline.json"))
    assert payload == {
        "schema_version": 1,
        "policy": "ratchet_only",
        "warning_kind": "print_call",
        "max_warnings": 0,
        "review_required_for_increase": True,
        "review_note": (
            "Repository-approved legacy ceiling. Decrease as print debt is removed; "
            "do not increase to make a release green."
        ),
    }

    baseline = check_repo_hardening._load_warning_baseline(
        ROOT / "scripts" / "hardening_warning_baseline.json"
    )
    assert check_repo_hardening._warning_baseline_error(0, baseline) is None
    assert "current=1 baseline=0" in str(
        check_repo_hardening._warning_baseline_error(1, baseline)
    )

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"max_warnings": 999999}', encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        check_repo_hardening._load_warning_baseline(invalid)
    with pytest.raises(ValueError, match="does not exist"):
        check_repo_hardening._load_warning_baseline(tmp_path / "missing.json")


def test_deploy_runs_canonical_gate_before_any_build_backup_or_remote_command() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    gate_at = deploy.index(
        "VKPI_VERIFY_REQUIRE_RUNTIME=1 VKPI_VERIFY_REQUIRE_CLEAN_WORKTREE=1"
    )
    mutations = (
        'npm --prefix frontend run build',
        '"${SCRIPT_DIR}/backup_prod_vkpi.sh"',
        '\nssh "${SSH_TARGET}"',
        "\nrsync -az",
    )
    for marker in mutations:
        assert gate_at < deploy.index(marker)


def test_deploy_cannot_succeed_without_bound_post_restart_acceptance() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    restart_at = deploy.index("WORKER_RESTART_NOT_BEFORE=")
    baseline_at = deploy.index("REMOTE_LOG_BASELINE=", restart_at)
    acceptance_at = deploy.index("scripts/local_release_acceptance.py", baseline_at)
    browser_at = deploy.index("scripts/capture_browser_console_cdp.mjs", acceptance_at)
    canary_at = deploy.index("--require-complete-baseline", browser_at)
    validator_at = deploy.index("scripts/verify_runtime_journal_canary.py", canary_at)
    accepted_at = deploy.index("DEPLOY_ACCEPTED=1", validator_at)
    assert restart_at < baseline_at < acceptance_at < browser_at < canary_at < validator_at < accepted_at

    for required in (
        "VKPI_BROWSER_GATE_URL",
        "VKPI_BROWSER_GATE_TOKEN",
        "WORKER_BOOT_NONCE_SHA256",
        "WORKER_RESTART_NOT_BEFORE",
        "required_total < 41",
        "missing_board_families",
        "verify_browser_console_capture.py",
        "audit_systemd_journal_media_log_leaks.py",
        "verify_runtime_journal_canary.py",
        "--fail-on-new",
        "--expected-worker-boot-nonce-sha256",
    ):
        assert required in deploy


def test_deploy_validates_aligned_predeploy_anchor_before_remote_mutation() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    health_fetch_at = deploy.index("REMOTE_PREDEPLOY_HEALTH_JSON=")
    strict_anchor_at = deploy.index(
        '"${PROJECT_ROOT}/scripts/verify_runtime_health.py"',
        health_fetch_at,
    )
    backup_at = deploy.index('"${SCRIPT_DIR}/backup_prod_vkpi.sh"', strict_anchor_at)
    prepare_at = deploy.index("atomic_release_layout.py' prepare", strict_anchor_at)
    assert health_fetch_at < strict_anchor_at < backup_at < prepare_at
    strict_block = deploy[strict_anchor_at:backup_at]
    for required in (
        "--strict-deploy",
        '--expected-head "${PREDEPLOY_APP_SHA}"',
        '--expected-migration "${PREDEPLOY_MIGRATION}"',
        '--expected-worker-count "${EXPECTED_WORKER_COUNT}"',
        "pre-deploy rollback anchor is not a strict aligned",
    ):
        assert required in strict_block
    assert "no legacy mismatch bypass" in deploy[health_fetch_at:strict_anchor_at]


def test_deploy_quiesces_all_release_consumers_before_switch_and_restore() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    forward_quiesce_at = deploy.index("quiesce_remote_release_consumers\n")
    env_switch_at = deploy.index("staging_db_clone.py' switch-env", forward_quiesce_at)
    pointer_switch_at = deploy.index("atomic_release_layout.py' activate", env_switch_at)
    assert forward_quiesce_at < env_switch_at < pointer_switch_at

    rollback_at = deploy.index("attempt_automatic_rollback()")
    rollback_stop_at = deploy.index("complete web/worker fleet", rollback_at)
    rollback_restore_at = deploy.index("atomic_release_layout.py' restore", rollback_stop_at)
    assert rollback_at < rollback_stop_at < rollback_restore_at
    assert "sudo systemctl stop '${SERVICE_NAME}' ${WORKER_SYSTEMD_UNIT_ARGS}" in deploy


def test_cloud_log_canary_uses_journald_not_stale_runtime_files() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    baseline_at = deploy.index("REMOTE_LOG_BASELINE=", deploy.index("WORKER_RESTART_NOT_BEFORE="))
    canary_block = deploy[baseline_at:deploy.index("LOCAL_ASSET=", baseline_at)]
    assert "audit_systemd_journal_media_log_leaks.py" in canary_block
    assert "verify_runtime_journal_canary.py" in canary_block
    assert "JOURNAL_SYSTEMD_UNIT_FLAGS" in canary_block
    assert "audit_runtime_media_log_leaks.py" not in canary_block
    assert "verify_runtime_log_canary.py" not in canary_block


def test_contract_check_mode_is_non_mutating() -> None:
    target = ROOT / "frontend" / "src" / "lib" / "contracts.generated.ts"
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "generate_frontend_contracts.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    after = hashlib.sha256(target.read_bytes()).hexdigest()
    assert result.returncode == 0, result.stderr
    assert after == before


def test_python_compile_check_is_in_memory_and_reports_syntax_errors(tmp_path: Path) -> None:
    good = tmp_path / "good.py"
    bad = tmp_path / "bad.py"
    good.write_text("answer = 42\n", encoding="utf-8")
    bad.write_text("def broken(:\n", encoding="utf-8")

    assert check_python_compile.compile_paths([good]) == []
    errors = check_python_compile.compile_paths([bad])
    assert len(errors) == 1
    assert "invalid syntax" in errors[0]
    assert not (tmp_path / "__pycache__").exists()

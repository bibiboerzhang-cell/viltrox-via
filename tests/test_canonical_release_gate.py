from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_python_compile  # noqa: E402
import check_repo_hardening  # noqa: E402
from ops import safe_python_router  # noqa: E402


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_github_ci_uses_full_history_secret_scan_and_ci_only_dependencies() -> None:
    workflow = _read(".github/workflows/verify.yml")
    verify_job = workflow.split("  verify:\n", 1)[1].split(
        "\n  postgres-integration:\n", 1
    )[0]
    postgres_job = workflow.split("\n  postgres-integration:\n", 1)[1]
    loadtest = _read(".github/workflows/loadtest-smoke.yml")
    ci_requirements = _read("requirements-ci.txt")
    gitleaks = _read(".gitleaks.toml")

    assert "actions/checkout@v6" in workflow
    assert "fetch-depth: 0" in workflow
    assert "gitleaks/gitleaks-action@v3" in workflow
    assert 'GITLEAKS_VERSION: "8.30.1"' in workflow
    assert workflow.count("pip install -r requirements-ci.txt") == 2
    assert "actions/setup-python@v6" in workflow
    assert "actions/setup-node@v5" in workflow
    assert "VKPI_SAFE_PYTHON_PROFILE: github-actions-static-v1" in workflow
    assert 'python -m pip install -r requirements-ci.txt' in workflow
    assert 'chmod go-w "$ci_python_real"' in workflow
    assert 'chmod -R go-w "$ci_purelib"' in workflow
    assert 'printf \'PYTHON_BIN=%s\\n\' "$ci_python" >> "$GITHUB_ENV"' in workflow
    assert '"$PYTHON_BIN" - <<\'PY\'' in workflow
    assert "services:" not in verify_job
    assert "Initialize database" not in verify_job
    assert '      DATABASE_URL:' not in verify_job
    assert '      LOCAL_DATABASE_URL:' not in verify_job
    assert '      DB_RUNTIME_BACKEND:' not in verify_job
    assert '      VKPI_PYTEST_ALLOW_LIVE_SERVICES:' not in verify_job
    assert "python -m venv --system-site-packages .venv" in verify_job
    assert "Assert hermetic database boundary" in verify_job
    assert 'printf \'PYTHON_BIN=%s\\n\' "$ci_python"' in verify_job
    assert "services:" in postgres_job
    assert 'VKPI_PYTEST_ALLOW_LIVE_SERVICES: "1"' in postgres_job
    assert "DATABASE_URL: postgresql://postgres@localhost:5432/viltrox_pg_integration" in postgres_job

    for pin in (
        "pandas==2.3.3",
        "python-calamine==0.6.2",
        "RapidFuzz==3.14.5",
        "psycopg2-binary==2.9.12",
    ):
        assert pin in ci_requirements
    assert "-r requirements.txt" in ci_requirements

    assert gitleaks.count('condition = "AND"') == 3
    assert "dimensions11_fit_for_family" in gitleaks
    assert "test_gemini_video_youtube_characterization" in gitleaks
    assert "test_stateless_alert_cold_import" in gitleaks

    assert "actions/checkout@v6" in loadtest
    assert "actions/setup-python@v6" in loadtest
    assert "actions/upload-artifact@v7" in loadtest


def test_github_static_python_profile_is_narrow_and_deploy_forbidden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    profile_names = (
        safe_python_router.CI_PROFILE_ENV,
        "GITHUB_ACTIONS",
        "CI",
        "RUNNER_OS",
        "RUNNER_ENVIRONMENT",
        "GITHUB_WORKSPACE",
        "GITHUB_EVENT_NAME",
        # 全部 5 个 strict 名都要清:freeze 里的 verify 是 strict 模式,只清一个会把
        # 外层环境泄进测试 → _github_static_profile_enabled 正确地拒绝 → 测试误红
        # (2026-09-02 真仓库 freeze 实测)。
        "VKPI_VERIFY_REQUIRE_CLEAN_WORKTREE",
        "VKPI_VERIFY_REQUIRE_RUNTIME",
        "VKPI_VERIFY_STRICT_POST_RESTART",
        "VKPI_VERIFY_REQUIRE_BROWSER_CONSOLE",
        "VKPI_VERIFY_REQUIRE_RUNTIME_LOG_CANARY",
    )
    for name in profile_names:
        monkeypatch.delenv(name, raising=False)

    assert safe_python_router._github_static_profile_enabled(root) is False

    github_env = {
        safe_python_router.CI_PROFILE_ENV: safe_python_router.GITHUB_STATIC_PROFILE,
        "GITHUB_ACTIONS": "true",
        "CI": "true",
        "RUNNER_OS": "Linux",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "GITHUB_WORKSPACE": str(root),
        "GITHUB_EVENT_NAME": "push",
    }
    for name, value in github_env.items():
        monkeypatch.setenv(name, value)
    assert safe_python_router._github_static_profile_enabled(root) is True
    # Validation must not consume the profile: pytest and other reviewed
    # commands invoke the safe wrapper again in child processes.
    assert os.environ[safe_python_router.CI_PROFILE_ENV] == (
        safe_python_router.GITHUB_STATIC_PROFILE
    )
    assert safe_python_router._github_static_profile_enabled(root) is True

    for name, value in github_env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("VKPI_VERIFY_REQUIRE_RUNTIME", "1")
    with pytest.raises(SystemExit, match="profile is not trusted"):
        safe_python_router._github_static_profile_enabled(root)

    train = _read("scripts/ops/train.sh")
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    assert "GitHub static Python profile is forbidden for release trains" in train
    assert "GitHub static Python profile is forbidden for deployment" in deploy


def test_github_static_profile_survives_a_nested_process_and_revalidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This profile-only test is platform independent and does not weaken or
    # replace the separate wrapper/dependency-mirror containment tests.
    for name in (
        "VKPI_VERIFY_REQUIRE_CLEAN_WORKTREE", "VKPI_VERIFY_REQUIRE_RUNTIME",
        "VKPI_VERIFY_STRICT_POST_RESTART", "VKPI_VERIFY_REQUIRE_BROWSER_CONSOLE",
        "VKPI_VERIFY_REQUIRE_RUNTIME_LOG_CANARY",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        safe_python_router.CI_PROFILE_ENV: safe_python_router.GITHUB_STATIC_PROFILE,
        "GITHUB_ACTIONS": "true", "CI": "true", "RUNNER_OS": "Linux",
        "RUNNER_ENVIRONMENT": "github-hosted", "GITHUB_WORKSPACE": str(ROOT),
        "GITHUB_EVENT_NAME": "push",
    }.items():
        monkeypatch.setenv(name, value)
    source = (
        "import os, runpy, sys\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        "router = runpy.run_path(str(root / 'scripts/ops/safe_python_router.py'))\n"
        "assert router['_github_static_profile_enabled'](root)\n"
        "print(os.environ.get('VKPI_SAFE_PYTHON_PROFILE', 'MISSING'))\n"
    )
    assert safe_python_router._github_static_profile_enabled(ROOT) is True
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", source, str(ROOT)],
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == safe_python_router.GITHUB_STATIC_PROFILE

    # The inherited value is never a cached authorization: a stricter child
    # environment must be independently rejected.
    monkeypatch.setenv("VKPI_VERIFY_REQUIRE_RUNTIME", "1")
    rejected = subprocess.run(
        [sys.executable, "-I", "-S", "-c", source, str(ROOT)],
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert rejected.returncode != 0
    assert "profile is not trusted" in rejected.stderr


def test_safe_python_has_strict_native_temp_anchors_for_macos_and_linux() -> None:
    anchors = safe_python_router._TRUSTED_STICKY_TEMP_PARENTS
    assert Path("/private/tmp") in anchors
    assert Path("/tmp") in anchors
    selected = safe_python_router._default_trusted_temp_parent()
    info = selected.lstat()
    assert selected == selected.resolve(strict=True)
    assert info.st_uid == 0
    assert info.st_mode & 0o7777 == 0o1777


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
    assert '"${TRUSTED_CANDIDATE_VERIFIER}" run-deploy-gate' in deploy
    assert '--snapshot "${DEPLOY_CANDIDATE_DIR}"' in deploy
    for binding in (
        '--runtime-root "${runtime_root}"',
        '--health-env-file "${LOCAL_HEALTH_ENV_FILE}"',
        '--health-url "${health_url}"',
        '--base-url "${base_url}"',
        '--verify-json-out "${verify_receipt}"',
        '--acceptance-json-out "${acceptance_receipt}"',
    ):
        assert binding in deploy
    # BSD chmod (the local deployment controller runs on macOS) does not
    # accept GNU-style ``--`` after the mode and would abort before upload.
    assert "chmod 700 --" not in deploy
    assert "chmod 600 --" not in deploy
    assert "bash ./scripts/verify.sh" in runbook
    assert "only canonical repository/release gate" in runbook


def test_canonical_gate_contains_the_union_of_all_reviewed_checks() -> None:
    gate = _read("scripts/verify.sh")
    required_in_order = (
        "release_candidate_worktree",
        "generate_frontend_contracts.py\" --check",
        "npm audit --omit=dev --audit-level=moderate",
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


def test_strict_runtime_health_uses_private_probe_and_static_mode_never_fetches() -> None:
    gate = _read("scripts/verify.sh")
    runtime_at = gate.index("runtime_sha_aligned()")
    run_at = gate.index('run_step "$RUNTIME_STEP_NAME" runtime_sha_aligned', runtime_at)
    runtime_block = gate[runtime_at:run_at]
    not_requested_at = runtime_block.index(
        'RUNTIME_VERIFICATION_STATE="not_requested"'
    )
    early_return_at = runtime_block.index("return 0", not_requested_at)
    fetch_at = runtime_block.index("scripts/ops/fetch_runtime_health.py")

    assert not_requested_at < early_return_at < fetch_at
    assert "scripts/ops/fetch_runtime_health.py" in runtime_block
    assert "VKPI_HEALTH_ENV_FILE" in runtime_block
    assert '--env-file "$VKPI_HEALTH_ENV_FILE"' in runtime_block
    assert "curl " not in runtime_block
    assert '--expected-migration "$latest_migration"' in runtime_block
    assert "--require-worker" in runtime_block


def test_static_gate_runtime_step_is_deterministic_by_default() -> None:
    gate = _read("scripts/verify.sh")
    runtime_at = gate.index("runtime_sha_aligned()")
    run_at = gate.index('run_step "$RUNTIME_STEP_NAME" runtime_sha_aligned', runtime_at)
    block = gate[runtime_at:run_at]

    assert 'RUNTIME_STEP_NAME="runtime trust (not requested static-gate mode)"' in block
    assert 'RUNTIME_VERIFICATION_STATE="not_requested"' in block
    assert "静态门禁未请求运行态探测" in block


def test_cloud_deploy_requires_explicit_local_health_secret_source() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    source_guard_at = deploy.index('LOCAL_HEALTH_ENV_FILE="${VKPI_HEALTH_ENV_FILE:-}"')
    gate_at = deploy.index("\nrun_predeploy_embedded_browser_gate\n")
    assert source_guard_at < gate_at
    source_guard = deploy[source_guard_at:gate_at]
    assert 'if [ -z "${LOCAL_HEALTH_ENV_FILE}" ]; then' in source_guard
    assert "must explicitly name the protected local health-token dotenv" in source_guard

    gate_block = deploy.split("run_predeploy_canonical_gate() {", 1)[1].split(
        "\n}\n\nrun_predeploy_final_runtime_gate() {", 1
    )[0]
    assert '--health-env-file "${LOCAL_HEALTH_ENV_FILE}"' in gate_block
    assert "OPS_HEALTH_TOKEN" not in gate_block
    assert "x-ops-token" not in gate_block


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


def test_deploy_runs_canonical_gate_before_remote_transport_or_mutation() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    browser_gate = deploy.split("run_predeploy_embedded_browser_gate() {", 1)[1].split(
        "\n}\n\ncapture_remote_sync_unit_state() {", 1
    )[0]
    assert browser_gate.index("start_local_candidate_browser_runtime") < browser_gate.index(
        "run_predeploy_canonical_gate"
    ) < browser_gate.index("cleanup_local_candidate_browser_runtime")
    assert "run_predeploy_embedded_browser_gate\nsetup_deploy_ssh_transport" in deploy
    top_level = deploy.split("\nrun_predeploy_embedded_browser_gate\n", 1)[1]
    assert top_level.index("setup_deploy_ssh_transport") < top_level.index(
        "acquire_remote_deploy_lock"
    )


def test_deploy_requires_embedded_production_browser_gate_before_remote_state() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    helper = _read("scripts/ops/run_isolated_candidate_web.sh")
    canonical_at = deploy.index("run_predeploy_canonical_gate()")
    function_at = deploy.index("run_predeploy_embedded_browser_gate()")
    call_at = deploy.index("\nrun_predeploy_embedded_browser_gate\n", function_at)
    remote_state_at = deploy.index("\ncapture_remote_sync_unit_state\n", call_at)
    assert canonical_at < function_at < call_at < remote_state_at

    block = deploy[function_at:remote_state_at]
    for required in (
        'PREDEPLOY_BROWSER_URL=""',
        "start_local_candidate_browser_runtime",
        'controller_tmp_root="$(cd /tmp && pwd -P)"',
        'mktemp -d "${controller_tmp_root%/}/vkpi-candidate-browser-runtime.XXXXXX"',
        '"${controller_tmp_root%/}"/vkpi-candidate-browser-runtime.*',
        'CANDIDATE_ROOT="${DEPLOY_CANDIDATE_DIR}"',
        'CANDIDATE_LOCAL_ENV_FILE="${LOCAL_CANDIDATE_RUNTIME_ENV}"',
        '/usr/bin/sandbox-exec -f "${LOCAL_CANDIDATE_WEB_PROFILE}"',
        'CANDIDATE_LAUNCHER="${DEPLOY_CANDIDATE_DIR}/scripts/ops/run_isolated_candidate_web.sh"',
        '"${DEPLOY_PHYSICAL_PYTHON}" -I -S -B -',
        "os.setsid()",
        "LOCAL_CANDIDATE_WEB_PGID",
        'kill -TERM -- "-${pgid}"',
        'kill -KILL -- "-${pgid}"',
        "connect_ex",
        "errno.ECONNREFUSED",
        "cleanup_local_candidate_browser_runtime",
        "env -i",
        'ENVIRONMENT=local',
        'LOCAL_ENV_FILE="${LOCAL_CANDIDATE_RUNTIME_ENV}"',
        '/usr/bin/sandbox-exec -f "${LOCAL_CANDIDATE_VERIFY_PROFILE}"',
        'RUNTIME_ENV_KEEP_DB_URL=1',
        'VKPI_SAFE_PYTHON_REAL="${DEPLOY_PHYSICAL_PYTHON}"',
        '"${DEPLOY_CANDIDATE_DIR}/scripts/ops/safe_python.sh" -I -B -',
        '"${DEPLOY_CANDIDATE_DIR}/scripts"',
        '"${DEPLOY_CANDIDATE_DIR}/backend" <<\'PY\'',
        "create_local_auth_context(int(sys.argv[1]))",
        '"${BROWSER_GATE_TOKEN_TTL_SECONDS}"',
        "scripts/capture_browser_console_cdp.mjs",
        '--overall-timeout-ms "${BROWSER_GATE_OVERALL_TIMEOUT_MS}"',
        "scripts/verify_browser_console_capture.py",
        'pages.get("required") != 21',
        'pages.get("captured") != 21',
        'pages.get("passed") != 21',
        "assert_deploy_source_unchanged",
        "verify_deploy_candidate",
    ):
        assert required in deploy
    token_mint_at = block.index(
        '"${DEPLOY_CANDIDATE_DIR}/scripts/ops/safe_python.sh" -I -B -'
    )
    token_mint_block = block[token_mint_at:block.index("failure_log=", token_mint_at)]
    assert '"${LOCAL_SAFE_PYTHON}"' not in token_mint_block
    assert '"${PROJECT_ROOT}/scripts"' not in token_mint_block
    assert '"${PROJECT_ROOT}/backend"' not in token_mint_block
    for required in (
        'PYTHONPATH="${CANDIDATE_ROOT}/backend"',
        'BIND="127.0.0.1:${CANDIDATE_PORT}"',
        "-m gunicorn app.main:app",
        "ENABLE_LOCAL_ORCHESTRATOR=0",
        "ENABLE_SCHEDULER=0",
        "VKPI_ADVISOR_EXTERNAL_AI_ENABLED=0",
        "VKPI_RELEASE_VALIDATION_FENCE_PATH",
        "vkpi-release-validation/v1",
        "VKPI_SKIP_DOTENV=1",
        "VKPI_ASYNC_ENABLED=0",
        "VKPI_MEDIA_CACHE_STORAGE=local",
        "CANDIDATE_LOCAL_ENV_FILE",
        'controller_tmp_root="$(cd /tmp && pwd -P)"',
        'candidate_runtime_parent="$(cd "${candidate_runtime_parent}" && pwd -P)"',
        '"${controller_tmp_root%/}"/vkpi-candidate-browser-runtime.*',
        "PRIVATE_LOCAL_ENV_FILE",
        "cleanup_private_local_env",
        "before.st_uid != os.geteuid()",
        "stat.S_IMODE(before.st_mode) & 0o077",
        "SENTRY_DSN",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "APIFY_TOKEN",
        "YTDLP_PROXY",
        'cd "${CANDIDATE_RUNTIME}"',
    ):
        assert required in helper
    assert 'cd "${PROJECT_ROOT}"' not in helper
    start_at = deploy.index("start_local_candidate_browser_runtime", function_at)
    canonical_call_at = deploy.index("run_predeploy_canonical_gate", start_at)
    capture_at = deploy.index("scripts/capture_browser_console_cdp.mjs", start_at)
    cleanup_at = deploy.index("cleanup_local_candidate_browser_runtime", capture_at)
    ssh_setup_at = deploy.index("\nsetup_deploy_ssh_transport\n", cleanup_at)
    assert function_at < start_at < canonical_call_at < capture_at < cleanup_at < ssh_setup_at
    assert 'npm --prefix frontend run build' not in deploy
    mint = block.split('if ! token="$(' , 1)[1].split(')"; then', 1)[0]
    assert "env -i" in mint
    assert "PYTHONPATH=" not in mint
    assert 'PATH="${BROWSER_GATE_CONTROLLER_PATH}"' in mint
    assert 'HOME="${LOCAL_CANDIDATE_WEB_RUNTIME}/home"' in mint
    assert 'XDG_CACHE_HOME="${LOCAL_CANDIDATE_WEB_RUNTIME}/cache"' in mint
    assert 'TMPDIR="${LOCAL_CANDIDATE_WEB_RUNTIME}/tmp"' in mint
    assert (
        'VKPI_SAFE_PYTHON_CONTROLLER_RUNTIME_ROOT="${LOCAL_CANDIDATE_WEB_RUNTIME}"'
        in mint
    )
    assert 'LOCAL_ENV_FILE="${PROJECT_ROOT}/.env"' not in mint
    assert 'PATH="${PATH}"' not in mint
    assert 'HOME="${HOME:-/tmp}"' not in mint


def test_candidate_browser_cleanup_is_fail_closed_and_promotes_exit_failure() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    cleanup = deploy.split("cleanup_local_candidate_browser_runtime()", 1)[1].split(
        "cleanup_initial_deploy_resources()", 1
    )[0]
    initial_trap = deploy.split("cleanup_initial_deploy_resources()", 1)[1].split(
        "bind_rescue_rollback_candidate()", 1
    )[0]
    final_trap = deploy.split("cleanup_post_deploy_evidence()", 1)[1].split(
        "trap cleanup_post_deploy_evidence EXIT", 1
    )[0]

    assert "candidate_process_group_live" in cleanup
    assert 'kill -TERM -- "-${pgid}"' in cleanup
    assert 'kill -KILL -- "-${pgid}"' in cleanup
    assert "connect_ex" in cleanup
    assert "errno.ECONNREFUSED" in cleanup
    assert "LOCAL_CANDIDATE_WEB_RUNTIME=\"\"" in cleanup
    assert "trap - EXIT" in initial_trap
    assert 'exit "${original_rc}"' in initial_trap
    assert "trap - EXIT" in final_trap
    assert 'exit "${original_rc}"' in final_trap


def test_deploy_disables_inherited_xtrace_before_any_credential_or_child() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    xtrace = deploy.index("*x*) set +x")
    caller_token_guard = deploy.index("VKPI_BROWSER_GATE_TOKEN")
    first_child = deploy.index('SCRIPT_DIR="$(cd')
    assert xtrace < caller_token_guard < first_child
    assert "monotonic for the rest of the deploy" in deploy[:caller_token_guard]


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
    assert "--token-ttl 1200 --overall-timeout 1170" in deploy[acceptance_at:browser_at]

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
        '"${DEPLOY_CANDIDATE_DIR}/scripts/verify_runtime_health.py"',
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

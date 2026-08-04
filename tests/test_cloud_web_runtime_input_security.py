from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_deploy_rejects_exported_browser_tokens_before_any_child_process() -> None:
    deploy_path = ROOT / "scripts" / "ops" / "deploy_local_to_cloud.sh"
    sentinel = "header.payload.exported-secret"

    for variable in ("VKPI_BROWSER_GATE_TOKEN", "POST_DEPLOY_BROWSER_TOKEN"):
        environment = {**os.environ, variable: sentinel}
        result = subprocess.run(
            ["/bin/bash", str(deploy_path)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert "Caller-supplied browser gate tokens are forbidden" in result.stderr
        assert sentinel not in result.stdout
        assert sentinel not in result.stderr
        assert "dirty worktree" not in result.stderr


def test_production_loopback_endpoints_reject_every_override_before_children() -> None:
    deploy_path = ROOT / "scripts" / "ops" / "deploy_local_to_cloud.sh"
    hostile_values = (
        {"HEALTH_URL": "http://127.0.0.1:8002/health"},
        {"HEALTH_URL": "http://localhost:8001/health"},
        {"HEALTH_URL": "http://127.0.0.1:8001/health' ; ssh attacker"},
        {"VKPI_REMOTE_ACCEPTANCE_BASE_URL": "http://127.0.0.1:8002"},
        {"VKPI_REMOTE_ACCEPTANCE_BASE_URL": "http://127.0.0.1:8001/health"},
        {"VKPI_REMOTE_ACCEPTANCE_BASE_URL": "http://127.0.0.1:8001' ; ssh attacker"},
        {"SSH_TARGET": "-oProxyCommand=attacker"},
        {"SSH_TARGET": "other-production"},
        {"SYNC_SERVICE": "other-sync.service"},
        {"SYNC_TIMER": "other-sync.timer"},
        {"REMOTE_SYNC_SERVICE_UNIT_RELATIVE": "scripts/ops/systemd/other-sync.service"},
        {"VKPI_CHROME_PATH": "/tmp/fake-chrome"},
    )

    for override in hostile_values:
        result = subprocess.run(
            ["/bin/bash", str(deploy_path)],
            cwd=ROOT,
            env={**os.environ, **override},
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert (
            "Production host, health, sync, and browser identities must remain "
            "exact reviewed values."
        ) in result.stderr
        assert "dirty worktree" not in result.stderr
        assert "VKPI_DEPLOY_CANDIDATE_DIR" not in result.stderr
        assert next(iter(override.values())) not in result.stderr

    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    guard_at = deploy.index(
        "Production host, health, sync, and browser identities must remain exact"
    )
    first_child_at = deploy.index('SCRIPT_DIR="$(cd')
    assert guard_at < first_child_at
    assert 'HEALTH_URL="${PRODUCTION_HEALTH_URL}"' in deploy
    assert (
        'REMOTE_ACCEPTANCE_BASE_URL="${PRODUCTION_REMOTE_ACCEPTANCE_BASE_URL}"'
        in deploy
    )


def test_both_browser_controllers_start_with_an_empty_minimal_environment() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    controller = re.compile(
        r'env -i \\\n'
        r'\s+PATH="\$\{BROWSER_GATE_CONTROLLER_PATH\}" \\\n'
        r'\s+HOME=/tmp \\\n'
        r'\s+XDG_CACHE_HOME=/tmp \\\n'
        r'\s+TMPDIR=/tmp \\\n'
        r'\s+LANG=C\.UTF-8 \\\n'
        r'\s+VKPI_BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS=.* \\\n'
        r'\s+VKPI_BROWSER_GATE_TOKEN=.* \\\n'
        r'\s+node \\\n'
        r'\s+"\$\{DEPLOY_CANDIDATE_DIR\}/scripts/capture_browser_console_cdp\.mjs"'
    )
    invocations = controller.findall(deploy)
    assert len(invocations) == 2
    for invocation in invocations:
        for forbidden in (
            "DATABASE_URL=",
            "REDIS_URL=",
            "APIFY_TOKEN=",
            "SSH_AUTH_SOCK=",
            "VKPI_DEPLOY_",
        ):
            assert forbidden not in invocation


def test_default_browser_token_ttl_is_derived_from_the_enforced_overall_deadline() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    overall_timeout_ms = 600_000
    budget_seconds = (overall_timeout_ms + 999) // 1000
    token_ttl_seconds = budget_seconds + 120

    assert budget_seconds == 600
    assert token_ttl_seconds == 720
    assert "BROWSER_GATE_CAPTURE_BUDGET_SECONDS=$(((BROWSER_GATE_OVERALL_TIMEOUT_MS + 999) / 1000))" in deploy
    assert "BROWSER_GATE_CAPTURE_BUDGET_SECONDS + BROWSER_GATE_TOKEN_SAFETY_MARGIN_SECONDS" in deploy
    assert "BROWSER_GATE_PAGE_COUNT" not in deploy
    assert "mutually" in deploy and "exclusive per-step maxima" in deploy


def test_reviewed_viltroxtest_scope_always_binds_exact_public_browser_url() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    exact_function_at = deploy.index("viltroxtest_browser_gate_is_exact()")
    exact_value_at = deploy.index(
        'valid = sys.argv[1] == "https://www.viltroxtest.com/"',
        exact_function_at,
    )
    unconditional_guard = (
        'if [ "${VILTROXTEST_RELEASE_SCOPE}" = "1" ] '
        '&& ! viltroxtest_browser_gate_is_exact; then'
    )
    unconditional_at = deploy.index(unconditional_guard, exact_value_at)
    strategy_specific_at = deploy.index(
        'if [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then', unconditional_at
    )

    assert exact_function_at < exact_value_at < unconditional_at < strategy_specific_at
    assert (
        "The reviewed viltroxtest release scope requires "
        "VKPI_BROWSER_GATE_URL=https://www.viltroxtest.com/."
    ) in deploy[unconditional_at:strategy_specific_at]


def test_deploy_database_identity_parsers_reject_conninfo_query_overrides() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    assert deploy.count("safe_query_parameters = {") >= 2
    assert deploy.count(
        "any(key.lower() not in safe_query_parameters for key, _value in query)"
    ) >= 2
    endpoint_start = deploy.index("def endpoint(name: str, expected_port: int)")
    assert '"dbname"' not in deploy[
        endpoint_start : deploy.index("direct = endpoint", endpoint_start)
    ]
    parser_start = deploy.index("def database_name_from_url(value, label)")
    predeploy_parser = deploy[
        parser_start : deploy.index(
            'database_name = database_name_from_url(matches[0]', parser_start
        )
    ]
    assert "parse_qsl(" in predeploy_parser
    assert "parsed.fragment" in predeploy_parser
    assert "key.lower() not in safe_query_parameters" in predeploy_parser

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_PATH = ROOT / "scripts" / "ops" / "deploy_local_to_cloud.sh"
LANE_TEMPLATE_PATH = ROOT / "scripts" / "ops" / "systemd" / "vkpi-lane-overrides.env"


def _deploy() -> str:
    return DEPLOY_PATH.read_text(encoding="utf-8")


def test_sync_timer_and_service_are_captured_quiesced_and_restored() -> None:
    deploy = _deploy()

    capture_call = deploy.index(
        "capture_remote_sync_unit_state\n",
        deploy.index("MIGRATION_MANIFEST_CSV="),
    )
    early_sync_quiesce = deploy.index("\nquiesce_remote_sync_units\n", capture_call)
    build = deploy.index('if [ "${SKIP_BUILD:-0}" != "1" ]', early_sync_quiesce)
    first_release_mutation = deploy.index(
        'ssh "${SSH_TARGET}" "sudo install -d',
        build,
    )
    quiesce_call = deploy.index("\nquiesce_remote_release_consumers\n", first_release_mutation)
    env_switch = deploy.index("staging_db_clone.py' switch-env", quiesce_call)
    pointer_switch = deploy.index("atomic_release_layout.py' activate", env_switch)
    assert (
        capture_call
        < early_sync_quiesce
        < build
        < first_release_mutation
        < quiesce_call
        < env_switch
        < pointer_switch
    )

    early_quiesce = deploy.split("quiesce_remote_sync_units()", 1)[1].split(
        "quiesce_remote_release_consumers()", 1
    )[0]
    timer_stop = early_quiesce.index("sudo systemctl stop '${SYNC_TIMER}'")
    timer_mask = early_quiesce.index("sudo systemctl mask --runtime '${SYNC_TIMER}'")
    service_stop = early_quiesce.index("sudo systemctl stop '${SYNC_SERVICE}'")
    service_mask = early_quiesce.index("sudo systemctl mask --runtime '${SYNC_SERVICE}'")
    assert timer_stop < timer_mask < service_stop < service_mask
    assert r'sync_mask_path=\"/run/systemd/system/\${sync_unit}\"' in early_quiesce
    assert r'readlink -- \"\${sync_mask_path}\"' in early_quiesce
    assert "UnitFileState" not in early_quiesce
    assert "mask --runtime --now '${SYNC_TIMER}' '${SYNC_SERVICE}'" not in early_quiesce
    assert "SYNC_UNITS_MAY_HAVE_BEEN_MUTATED=1" in early_quiesce

    quiesce = deploy.split("quiesce_remote_release_consumers()", 1)[1].split(
        "fetch_predeploy_runtime_health()", 1
    )[0]
    timer_stop = quiesce.index("sudo systemctl stop '${SYNC_TIMER}'")
    timer_mask = quiesce.index("sudo systemctl mask --runtime '${SYNC_TIMER}'")
    service_stop = quiesce.index("sudo systemctl stop '${SYNC_SERVICE}'")
    service_mask = quiesce.index("sudo systemctl mask --runtime '${SYNC_SERVICE}'")
    assert timer_stop < timer_mask < service_stop < service_mask
    assert r'sync_mask_path=\"/run/systemd/system/\${sync_unit}\"' in quiesce
    assert r'readlink -- \"\${sync_mask_path}\"' in quiesce
    assert "UnitFileState" not in quiesce
    assert "mask --runtime --now '${SYNC_TIMER}' '${SYNC_SERVICE}'" not in quiesce
    assert "sync unit failed to stop" in quiesce
    assert "sync unit failed to mask" in quiesce
    assert "SYNC_UNITS_MAY_HAVE_BEEN_MUTATED=1" in quiesce

    cleanup = deploy.split("cleanup_post_deploy_evidence()", 1)[1].split(
        "trap cleanup_post_deploy_evidence EXIT", 1
    )[0]
    assert "ROLLBACK_ARMED" in cleanup
    assert "restore_remote_sync_unit_state" in cleanup

    rollback = deploy.split("attempt_automatic_rollback()", 1)[1].split(
        "cleanup_post_deploy_evidence()", 1
    )[0]
    assert "restore_remote_sync_unit_state" in rollback
    assert rollback.index("restore_remote_sync_unit_state") < rollback.index(
        "ROLLBACK_COMPLETED=1"
    )
    success_restore = deploy.rindex("restore_remote_sync_unit_state\n")
    accepted = deploy.rindex("DEPLOY_ACCEPTED=1")
    assert success_restore < accepted


def test_default_postdeploy_evidence_is_durable_and_never_deleted_on_success() -> None:
    deploy = _deploy()

    assert (
        'POST_DEPLOY_EVIDENCE_DIR="${PROJECT_ROOT}/runtime/ops/post-deploy/${RELEASE_ID}"'
        in deploy
    )
    assert 'mkdir -m 0700 -- "${POST_DEPLOY_EVIDENCE_DIR}"' in deploy
    assert 'rm -rf -- "${POST_DEPLOY_EVIDENCE_DIR}"' not in deploy
    assert "post-restart evidence retained: ${POST_DEPLOY_EVIDENCE_DIR}" in deploy
    assert "retained post-restart evidence: ${POST_DEPLOY_EVIDENCE_DIR}" in deploy


def test_lane_override_is_nonsecret_allowlisted_and_atomically_installed() -> None:
    deploy = _deploy()
    template = LANE_TEMPLATE_PATH.read_text(encoding="utf-8")
    allowed = {
        "APIFY_WORKER_GEMINI_QPS",
        "APIFY_WORKER_LLM_CONCURRENCY",
        "APIFY_WORKER_PROFILE_MEDIA_CONCURRENCY",
        "APIFY_WORKER_COMMENTS_CONCURRENCY",
        "APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY",
        "LLM_MONTHLY_BUDGET_USD",
        "POSTGRES_POOL_MIN_SIZE",
        "POSTGRES_POOL_MAX_SIZE",
    }
    entries: dict[str, str] = {}
    for raw in template.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        assert key not in entries
        assert re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value)
        entries[key] = value
    assert set(entries) == allowed
    assert not any(
        marker in key for key in entries for marker in ("SECRET", "TOKEN", "PASSWORD", "API_KEY")
    )

    validate_at = deploy.index("if ! validate_lane_override_template; then")
    first_release_mutation = deploy.index('ssh "${SSH_TARGET}" "sudo install -d', validate_at)
    install_block_at = deploy.index(
        "# Install only the already-verified unit payload", first_release_mutation
    )
    install_at = deploy.index("lane_tmp=\\$(sudo mktemp", install_block_at)
    worker_restart = deploy.index("sudo systemctl restart ${WORKER_SYSTEMD_UNIT_ARGS}", install_at)
    assert validate_at < first_release_mutation < install_at < worker_restart
    install = deploy[install_block_at:worker_restart]
    for required in (
        "sudo install -d -o root -g root -m 0755 '${REMOTE_LANE_OVERRIDE_DIR}'",
        "sudo install -o root -g root -m 0644",
        "sudo mv -f --",
        "0:0:755",
        "0:0:644",
        "sudo cmp -s",
        "trap cleanup_lane_tmp EXIT",
    ):
        assert required in install

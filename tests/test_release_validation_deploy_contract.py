from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts/ops/deploy_local_to_cloud.sh"


def _deploy() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def _shell_function(source: str, name: str, next_name: str) -> str:
    body = source.split(f"{name}()", 1)[1].split(f"{next_name}()", 1)[0]
    return f"{name}(){body}"


def test_root_owned_fence_has_exact_install_verify_remove_contract() -> None:
    deploy = _deploy()
    assert 'RELEASE_VALIDATION_FENCE="/run/vkpi-release-validation.fence"' in deploy
    verify = deploy.split("verify_remote_release_validation_fence()", 1)[1].split(
        "install_remote_release_validation_fence()", 1
    )[0]
    install = deploy.split("install_remote_release_validation_fence()", 1)[1].split(
        "remove_remote_release_validation_fence()", 1
    )[0]
    remove = deploy.split("remove_remote_release_validation_fence()", 1)[1].split(
        "quiesce_remote_pgbouncer_for_clone()", 1
    )[0]
    for required in (
        '"${expected}" != active ] && [ "${expected}" != absent',
        "O_NOFOLLOW",
        "observed.st_uid == 0",
        "observed.st_gid == 0",
        "stat.S_IMODE(observed.st_mode) == 0o444",
        "observed.st_nlink == 1",
        'payload = b"vkpi-release-validation/v1\\n"',
    ):
        assert required in verify
    assert 'RELEASE_CONSUMERS_QUIESCED}" != "1"' in install
    assert 'RELEASE_DRAIN_VERIFIED}" != "1"' in install
    assert "tempfile.mkstemp" in install
    assert "os.link(temporary, path, follow_symlinks=False)" in install
    assert "RELEASE_VALIDATION_FENCE_INSTALL_MAY_HAVE_COMMITTED=1" in install
    assert "reconcile_remote_release_validation_fence_install" in install
    assert "RELEASE_VALIDATION_FENCE_INSTALLED=1" in install
    assert "verify_remote_release_validation_fence active" in install
    assert "verify_remote_release_validation_fence active" in remove
    assert "path.unlink()" in remove
    assert "verify_remote_release_validation_fence absent" in remove
    assert "RELEASE_VALIDATION_FENCE_INSTALLED=0" in remove


def test_fence_is_installed_before_candidate_activation_and_held_through_gates() -> None:
    deploy = _deploy()
    final_drain = deploy.index("verify_remote_release_drain quiesced")
    install = deploy.index("\ninstall_remote_release_validation_fence\n", final_drain)
    pointer = deploy.index("atomic_release_layout.py' activate", install)
    redis_start = deploy.index(
        "sudo systemctl enable --now '${STAGING_REDIS_WORKER_SERVICE}'", pointer
    )
    web_start = deploy.index("sudo systemctl restart '${SERVICE_NAME}'", redis_start)
    apify_start = deploy.index("sudo systemctl restart ${WORKER_SYSTEMD_UNIT_ARGS}", web_start)
    remote_acceptance = deploy.index("scripts/local_release_acceptance.py", apify_start)
    browser = deploy.index("scripts/capture_browser_console_cdp.mjs", remote_acceptance)
    canary = deploy.index("verify_runtime_journal_canary.py", browser)
    fenced_drain = deploy.index("verify_remote_release_drain fenced", canary)
    remove = deploy.index("\nremove_remote_release_validation_fence\n", fenced_drain)
    assert (
        final_drain
        < install
        < pointer
        < redis_start
        < web_start
        < apify_start
        < remote_acceptance
        < browser
        < canary
        < fenced_drain
        < remove
    )


def test_runtime_must_report_fence_and_empty_provider_boundary_before_commit() -> None:
    deploy = _deploy()
    pointer = deploy.rindex(
        "post-deploy current pointer does not name the accepted release"
    )
    tail = deploy[pointer:]
    for required in (
        "verify_remote_release_validation_fence active",
        'f.get("active") is True',
        'f.get("valid") is True',
        'f.get("source")=="verified_marker"',
        "verify_remote_release_drain fenced",
        'FENCED_RELEASE_DRAIN_VERIFIED}" != "1"',
    ):
        assert required in tail
    commit = tail.index("RELEASE_VALIDATION_COMMIT_STARTED=1")
    remove = tail.index("remove_remote_release_validation_fence", commit)
    sync_restore = tail.index("restore_remote_sync_unit_state", remove)
    final_verify = tail.index("verify_deploy_candidate", sync_restore)
    final_source = tail.index("assert_deploy_source_unchanged", final_verify)
    accepted = tail.index("DEPLOY_ACCEPTED=1", final_source)
    assert commit < remove < sync_restore < final_verify < final_source < accepted


def test_rollback_opens_old_release_only_after_state_restore() -> None:
    deploy = _deploy()
    rollback = deploy.split("attempt_automatic_rollback()", 1)[1].split(
        "cleanup_post_deploy_evidence()", 1
    )[0]
    layout_restore = rollback.index("atomic_release_layout.py' restore")
    pool_restore = rollback.index("restore_remote_pgbouncer_state", layout_restore)
    fence_remove = rollback.index("remove_remote_release_validation_fence", pool_restore)
    redis_start = rollback.index(
        "sudo systemctl start '${STAGING_REDIS_WORKER_SERVICE}'", fence_remove
    )
    web_start = rollback.index("sudo systemctl restart '${SERVICE_NAME}'", redis_start)
    assert layout_restore < pool_restore < fence_remove < redis_start < web_start


def test_irreversible_activation_never_auto_rolls_back_provider_side_effects() -> None:
    deploy = _deploy()
    cleanup = deploy.split("cleanup_post_deploy_evidence()", 1)[1].split(
        "trap cleanup_post_deploy_evidence EXIT", 1
    )[0]
    assert 'RELEASE_VALIDATION_COMMIT_STARTED}" != "1"' in cleanup
    assert 'RELEASE_VALIDATION_COMMIT_STARTED}" = "1"' in cleanup
    assert "automatic rollback is forbidden because provider side effects may now exist" in cleanup


def test_prepare_and_fence_install_reconcile_lost_ssh_acknowledgements() -> None:
    deploy = _deploy()
    prepare_call = deploy.rindex("ROLLBACK_PREPARE_MAY_HAVE_COMMITTED=1")
    prepare_end = deploy.index("STAGING_REDIS_WORKER_UNIT_STATE=", prepare_call)
    prepare = deploy[prepare_call:prepare_end]
    assert prepare.index("ROLLBACK_PREPARE_MAY_HAVE_COMMITTED=1") < prepare.index(
        "atomic_release_layout.py' prepare"
    )
    assert "if ! ssh" in prepare
    assert "reconcile_remote_prepare_commit_state" in prepare
    assert 'ROLLBACK_ARMED}" != "1"' in prepare
    assert "ROLLBACK_PREPARE_MAY_HAVE_COMMITTED=0" in prepare

    prepare_probe = deploy.split("reconcile_remote_prepare_commit_state()", 1)[1].split(
        "attempt_automatic_rollback()", 1
    )[0]
    for required in (
        "rollback-unit-state",
        "metadata.sha256",
        "prepare metadata digest mismatch",
        'root / "previous"',
        "prepare previous pointer was not committed",
        "ROLLBACK_ARMED=1",
    ):
        assert required in prepare_probe

    fence_probe = deploy.split(
        "reconcile_remote_release_validation_fence_install()", 1
    )[1].split("install_remote_release_validation_fence()", 1)[0]
    assert "verify_remote_release_validation_fence active" in fence_probe
    assert "verify_remote_release_validation_fence absent" in fence_probe
    assert "RELEASE_VALIDATION_FENCE_INSTALLED=1" in fence_probe

    cleanup = deploy.split("cleanup_post_deploy_evidence()", 1)[1].split(
        "trap cleanup_post_deploy_evidence EXIT", 1
    )[0]
    assert "reconcile_remote_prepare_commit_state" in cleanup
    assert "reconcile_remote_release_validation_fence_install" in cleanup


def test_final_activation_mutations_are_reconciled_before_acceptance_or_cleanup() -> None:
    deploy = _deploy()
    remove = deploy.split("remove_remote_release_validation_fence()", 1)[1].split(
        "quiesce_remote_pgbouncer_for_clone()", 1
    )[0]
    remove_intent = remove.index("RELEASE_VALIDATION_FENCE_REMOVE_MAY_HAVE_COMMITTED=1")
    remove_mutation = remove.index('if ! ssh "${SSH_TARGET}"', remove_intent)
    remove_reconcile = remove.index(
        "reconcile_remote_release_validation_fence_remove", remove_mutation
    )
    assert remove_intent < remove_mutation < remove_reconcile
    assert "verify_remote_release_validation_fence absent" in remove

    restore = deploy.split("restore_remote_sync_unit_state()", 1)[1].split(
        "reconcile_remote_prepare_commit_state()", 1
    )[0]
    restore_intent = restore.index("SYNC_UNITS_RESTORE_MAY_HAVE_COMMITTED=1")
    restore_mutation = restore.index('if ! ssh "${SSH_TARGET}"', restore_intent)
    restore_reconcile = restore.index("reconcile_remote_sync_unit_restore", restore_mutation)
    assert restore_intent < restore_mutation < restore_reconcile
    assert "SYNC_UNITS_RESTORED=1" not in restore

    receipt = deploy.split("inspect_remote_sync_unit_restore_receipt()", 1)[1].split(
        "reconcile_remote_sync_unit_restore()", 1
    )[0]
    assert "systemctl show --property LoadState" in receipt
    assert "systemctl show --property UnitFileState" in receipt
    assert "systemctl show --property ActiveState" in receipt
    assert "sudo systemctl" not in receipt
    assert "vkpi-sync-unit-restore/v1:restored" not in receipt
    assert "printf 'vkpi-sync-unit-restore/v1:%s" in receipt

    cleanup = deploy.split("cleanup_post_deploy_evidence()", 1)[1].split(
        "trap cleanup_post_deploy_evidence EXIT", 1
    )[0]
    assert "reconcile_remote_release_validation_fence_remove" in cleanup
    assert "restore_remote_sync_unit_state || true" in cleanup
    assert "report_final_activation_recovery_path" in cleanup
    assert "Never activate the previous release after this boundary" in deploy


@pytest.mark.parametrize(
    ("fault_mode", "expected"),
    [
        ("committed_ack_lost", "0|0|0"),
        ("not_committed", "0|1|0"),
        ("readback_unavailable", "1|1|1"),
    ],
)
def test_fence_remove_lost_ack_fault_injection(
    fault_mode: str,
    expected: str,
) -> None:
    function = _shell_function(
        _deploy(),
        "reconcile_remote_release_validation_fence_remove",
        "remove_remote_release_validation_fence",
    )
    harness = f"""
set -u
RELEASE_VALIDATION_FENCE_REMOVE_MAY_HAVE_COMMITTED=1
RELEASE_VALIDATION_FENCE_INSTALLED=1
verify_remote_release_validation_fence() {{
  case "${{FAULT_MODE}}:$1" in
    committed_ack_lost:absent|not_committed:active) return 0 ;;
    *) return 1 ;;
  esac
}}
{function}
reconcile_rc=0
reconcile_remote_release_validation_fence_remove >/dev/null 2>&1 || reconcile_rc=$?
printf '%s|%s|%s' "${{reconcile_rc}}" "${{RELEASE_VALIDATION_FENCE_INSTALLED}}" "${{RELEASE_VALIDATION_FENCE_REMOVE_MAY_HAVE_COMMITTED}}"
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        cwd=ROOT,
        env={**os.environ, "FAULT_MODE": fault_mode},
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == expected
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("fault_mode", "expected"),
    [
        ("committed_ack_lost", "0|1|0"),
        ("partial_commit", "1|0|1"),
        ("readback_unavailable", "1|0|1"),
        ("invalid_receipt", "1|0|1"),
    ],
)
def test_sync_restore_lost_ack_fault_injection(
    fault_mode: str,
    expected: str,
) -> None:
    function = _shell_function(
        _deploy(),
        "reconcile_remote_sync_unit_restore",
        "restore_remote_sync_unit_state",
    )
    harness = f"""
set -u
SYNC_UNITS_RESTORE_MAY_HAVE_COMMITTED=1
SYNC_UNITS_RESTORED=0
inspect_remote_sync_unit_restore_receipt() {{
  case "${{FAULT_MODE}}" in
    committed_ack_lost) printf '%s\\n' 'vkpi-sync-unit-restore/v1:restored' ;;
    partial_commit) printf '%s\\n' 'vkpi-sync-unit-restore/v1:not-restored' ;;
    invalid_receipt) printf '%s\\n' 'unexpected-receipt' ;;
    *) return 1 ;;
  esac
}}
{function}
reconcile_rc=0
reconcile_remote_sync_unit_restore >/dev/null 2>&1 || reconcile_rc=$?
printf '%s|%s|%s' "${{reconcile_rc}}" "${{SYNC_UNITS_RESTORED}}" "${{SYNC_UNITS_RESTORE_MAY_HAVE_COMMITTED}}"
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        cwd=ROOT,
        env={**os.environ, "FAULT_MODE": fault_mode},
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == expected
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("fault_mode", "initial_may", "initial_state", "expected"),
    [
        ("retry_success", "0", "active", "0|2|0|0"),
        ("already_absent", "1", "absent", "0|0|0|0"),
        ("unknown", "1", "unknown", "1|0|1|1"),
    ],
)
def test_fence_remove_full_mutation_is_bounded_and_lost_ack_safe(
    fault_mode: str,
    initial_may: str,
    initial_state: str,
    expected: str,
) -> None:
    deploy = _deploy()
    reconcile = _shell_function(
        deploy,
        "reconcile_remote_release_validation_fence_remove",
        "remove_remote_release_validation_fence",
    )
    remove = _shell_function(
        deploy,
        "remove_remote_release_validation_fence",
        "quiesce_remote_pgbouncer_for_clone",
    )
    harness = f"""
set -u
RELEASE_VALIDATION_FENCE_REMOVE_MAY_HAVE_COMMITTED={initial_may}
RELEASE_VALIDATION_FENCE_INSTALLED=1
RELEASE_VALIDATION_FENCE=/run/vkpi-release-validation.fence
RELEASE_ID=test-release
SSH_TARGET=test-host
REMOTE_STATE={initial_state}
SSH_CALLS=0
verify_remote_release_validation_fence() {{
  [ "${{REMOTE_STATE}}" = "$1" ]
}}
ssh() {{
  SSH_CALLS=$((SSH_CALLS + 1))
  case "${{FAULT_MODE}}:${{SSH_CALLS}}" in
    retry_success:1)
      REMOTE_STATE=active
      return 1
      ;;
    retry_success:2)
      REMOTE_STATE=absent
      return 0
      ;;
    *) return 99 ;;
  esac
}}
{reconcile}
{remove}
remove_rc=0
remove_remote_release_validation_fence >/dev/null 2>&1 || remove_rc=$?
printf '%s|%s|%s|%s' "${{remove_rc}}" "${{SSH_CALLS}}" "${{RELEASE_VALIDATION_FENCE_INSTALLED}}" "${{RELEASE_VALIDATION_FENCE_REMOVE_MAY_HAVE_COMMITTED}}"
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        cwd=ROOT,
        env={**os.environ, "FAULT_MODE": fault_mode},
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == expected
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("fault_mode", "initial_may", "initial_state", "expected"),
    [
        ("retry_success", "0", "not-restored", "0|2|1|0"),
        ("already_restored", "1", "restored", "0|0|1|0"),
        ("unknown", "1", "unknown", "1|0|0|1"),
    ],
)
def test_sync_restore_full_mutation_is_bounded_and_lost_ack_safe(
    fault_mode: str,
    initial_may: str,
    initial_state: str,
    expected: str,
) -> None:
    deploy = _deploy()
    reconcile = _shell_function(
        deploy,
        "reconcile_remote_sync_unit_restore",
        "restore_remote_sync_unit_state",
    )
    restore = _shell_function(
        deploy,
        "restore_remote_sync_unit_state",
        "reconcile_remote_prepare_commit_state",
    )
    harness = f"""
set -u
SYNC_UNITS_RESTORE_MAY_HAVE_COMMITTED={initial_may}
SYNC_UNITS_RESTORED=0
SYNC_UNITS_MAY_HAVE_BEEN_MUTATED=1
SYNC_UNITS_CAPTURED=1
SYNC_UNITS_RESTORE_RECONCILE_STATE=
SYNC_SERVICE=vkpi-daily-sync.service
SYNC_SERVICE_ACTIVE_STATE=inactive
SYNC_SERVICE_UNIT_FILE_STATE=disabled
SYNC_TIMER=vkpi-daily-sync.timer
SYNC_TIMER_ACTIVE_STATE=active
SYNC_TIMER_UNIT_FILE_STATE=enabled
HEALTH_SENTINEL_SERVICE=vkpi-health-sentinel.service
HEALTH_SENTINEL_SERVICE_ACTIVE_STATE=inactive
HEALTH_SENTINEL_SERVICE_UNIT_FILE_STATE=static
HEALTH_SENTINEL_TIMER=vkpi-health-sentinel.timer
HEALTH_SENTINEL_TIMER_ACTIVE_STATE=active
HEALTH_SENTINEL_TIMER_UNIT_FILE_STATE=enabled
RELEASE_ID=test-release
SSH_TARGET=test-host
REMOTE_SYNC_STATE={initial_state}
SSH_CALLS=0
inspect_remote_sync_unit_restore_receipt() {{
  case "${{REMOTE_SYNC_STATE}}" in
    restored) printf '%s\\n' 'vkpi-sync-unit-restore/v1:restored' ;;
    not-restored) printf '%s\\n' 'vkpi-sync-unit-restore/v1:not-restored' ;;
    *) return 1 ;;
  esac
}}
ssh() {{
  SSH_CALLS=$((SSH_CALLS + 1))
  case "${{FAULT_MODE}}:${{SSH_CALLS}}" in
    retry_success:1)
      REMOTE_SYNC_STATE=not-restored
      return 1
      ;;
    retry_success:2)
      REMOTE_SYNC_STATE=restored
      return 0
      ;;
    *) return 99 ;;
  esac
}}
{reconcile}
{restore}
restore_rc=0
restore_remote_sync_unit_state >/dev/null 2>&1 || restore_rc=$?
printf '%s|%s|%s|%s' "${{restore_rc}}" "${{SSH_CALLS}}" "${{SYNC_UNITS_RESTORED}}" "${{SYNC_UNITS_RESTORE_MAY_HAVE_COMMITTED}}"
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        cwd=ROOT,
        env={**os.environ, "FAULT_MODE": fault_mode},
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == expected
    assert result.stderr == ""


def test_production_chrome_binary_is_exact_and_signature_bound() -> None:
    deploy = _deploy()
    exact_guard = deploy.index('VKPI_CHROME_PATH+x}')
    first_child = deploy.index('SCRIPT_DIR="$(cd')
    signature = deploy.index('/usr/bin/codesign --verify --deep "${PRODUCTION_CHROME_APP}"')
    notarization = deploy.index(
        '/usr/sbin/spctl --assess --type execute --verbose=4 "${PRODUCTION_CHROME_APP}"'
    )
    notarized_source = deploy.index('source=Notarized Developer ID', notarization)
    identity = deploy.index('/usr/bin/codesign -d --verbose=4', notarized_source)
    predeploy_browser = deploy.index("run_predeploy_embedded_browser_gate\n")
    assert exact_guard < first_child < signature < notarization < notarized_source < identity < predeploy_browser
    assert "codesign --verify --deep --strict" not in deploy
    assert 'Identifier=com.google.Chrome' in deploy
    assert 'TeamIdentifier=EQHXZ8M8AV' in deploy

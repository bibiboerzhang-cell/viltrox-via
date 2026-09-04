"""Release guards for the production-disabled Anthropic Batch transport."""

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "ops" / "deploy_local_to_cloud.sh"
TRAIN = ROOT / "scripts" / "ops" / "train.sh"


def test_deploy_rechecks_batches_after_release_consumers_are_quiesced() -> None:
    source = DEPLOY.read_text(encoding="utf-8")
    quiesce = source.index("quiesce_remote_release_consumers() {")
    proof_call = source.index("verify_remote_anthropic_batch_shutdown", quiesce)
    quiesced_flag = source.index("RELEASE_CONSUMERS_QUIESCED=1", quiesce)
    proof = source.index("verify_remote_anthropic_batch_shutdown() {")
    activate = source.index("atomic_release_layout.py' activate")

    assert quiesced_flag < proof_call < proof < activate
    proof_source = source[proof:source.index("\nverify_remote_release_drain()", proof)]
    assert "SEPARATE_SCHEDULER_SERVICE" in proof_source
    assert "sudo -n -u postgres psql" in proof_source
    assert "submitting','provider_unknown','in_progress','expired" in proof_source
    assert "verify_anthropic_batch_shutdown.py" in proof_source
    assert 'p.get("reconcile_complete") is True' in proof_source
    assert 'p.get("active_count")==0' in proof_source
    assert "read_remote_anthropic_batch_shutdown_count" in proof_source
    assert "ANTHROPIC_BATCH_SHUTDOWN_VERIFIED=1" in proof_source
    assert source.index('if [ "${ANTHROPIC_BATCH_SHUTDOWN_VERIFIED}" != "1" ]') < activate


def test_early_deploy_and_train_checks_fail_closed_on_unknown_or_active_batches() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8")
    train = TRAIN.read_text(encoding="utf-8")

    assert "PREDEPLOY_ACTIVE_LLM_BATCHES" in deploy
    assert "active Anthropic batches could not be read" in deploy
    assert '"${PREDEPLOY_ACTIVE_LLM_BATCHES}" != "0"' in deploy
    assert "submitting','provider_unknown','in_progress','expired" in deploy
    assert "llm_batch_shutdown_preflight" in train
    assert "provider_unknown'" in train and "expired'" in train
    assert "无法读取线上活动批次" in train
    assert "必须先人工回收" in train


def test_dedicated_scheduler_is_captured_masked_and_restored_exactly() -> None:
    source = DEPLOY.read_text(encoding="utf-8")
    capture = source.split("capture_remote_sync_unit_state()", 1)[1].split(
        "capture_remote_pgbouncer_unit_state()", 1
    )[0]
    quiesce = source.split("quiesce_remote_scheduler_unit()", 1)[1].split(
        "quiesce_remote_sync_units()", 1
    )[0]
    restore = source.split("restore_remote_sync_unit_state()", 1)[1].split(
        "reconcile_remote_prepare_commit_state()", 1
    )[0]

    for token in (
        "SCHEDULER_LOAD_STATE",
        "SCHEDULER_ACTIVE_STATE",
        "SCHEDULER_UNIT_FILE_STATE",
        "SCHEDULER_ATOMIC_CAPTURED_STATE",
    ):
        assert token in capture
    stop = quiesce.index('sudo systemctl stop "${unit}"')
    runtime_mask = quiesce.index('sudo systemctl mask --runtime "${unit}"')
    mask_receipt = quiesce.index('readlink -- "${runtime_mask_path}"', runtime_mask)
    assert stop < runtime_mask < mask_receipt

    prepare = next(
        line for line in source.splitlines() if "atomic_release_layout.py' prepare" in line
    )
    assert "--optional-unit-name '${SEPARATE_SCHEDULER_SERVICE}'" in prepare
    assert (
        "--optional-unit-state "
        "'${SEPARATE_SCHEDULER_SERVICE}=${SCHEDULER_ATOMIC_CAPTURED_STATE}'"
    ) in prepare
    assert "SCHEDULER_ATOMIC_ROLLBACK_STATE" in source

    for token in (
        "SCHEDULER_ACCEPTED_STATE_COMMITTED",
        "scheduler_load=loaded",
        "scheduler_active=active",
        "scheduler_file=enabled",
        "not-found:inactive:not-found",
        'sudo systemctl enable --runtime "${unit}"',
        'observed_file="$(systemctl show --property UnitFileState',
    ):
        assert token in restore

    rollback = source.split("attempt_automatic_rollback()", 1)[1].split(
        "cleanup_post_deploy_evidence()", 1
    )[0]
    early_fail_close = rollback.index("fail_close_remote_scheduler_unit")
    prepare_reconcile = rollback.index("reconcile_remote_prepare_commit_state")
    stop_scheduler = rollback.index(
        "sudo systemctl stop '${SEPARATE_SCHEDULER_SERVICE}'"
    )
    mask_scheduler = rollback.index(
        "sudo systemctl mask --runtime '${SEPARATE_SCHEDULER_SERVICE}'"
    )
    atomic_restore = rollback.index("atomic_release_layout.py' restore")
    reset_target = rollback.index("SCHEDULER_ACCEPTED_STATE_COMMITTED=0")
    exact_restore = rollback.index("restore_remote_sync_unit_state")
    assert early_fail_close < prepare_reconcile
    assert stop_scheduler < mask_scheduler < reset_target < atomic_restore < exact_restore


def test_scheduler_quiesce_accepts_its_exact_runtime_mask_on_second_call(tmp_path: Path) -> None:
    """Execute both release checkpoints, including the already-quiesced path."""

    source = DEPLOY.read_text(encoding="utf-8")
    function_source = "quiesce_remote_scheduler_unit() {" + source.split(
        "quiesce_remote_scheduler_unit() {", 1
    )[1].split("\nquiesce_remote_sync_units()", 1)[0]

    state_dir = tmp_path / "state"
    fake_bin = tmp_path / "bin"
    mask_root = tmp_path / "run" / "systemd" / "system"
    state_dir.mkdir()
    fake_bin.mkdir()
    mask_root.mkdir(parents=True)
    (state_dir / "LoadState").write_text("loaded\n", encoding="utf-8")
    (state_dir / "ActiveState").write_text("active\n", encoding="utf-8")
    (state_dir / "UnitFileState").write_text("enabled\n", encoding="utf-8")

    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        """#!/bin/bash
set -euo pipefail
command_name="$1"
shift
case "${command_name}" in
  show)
    property=""
    while [ "$#" -gt 0 ]; do
      if [ "$1" = --property ]; then property="$2"; shift 2; else shift; fi
    done
    case "${property}" in
      LoadState|ActiveState|UnitFileState) cat "${VKPI_TEST_STATE}/${property}" ;;
      *) exit 2 ;;
    esac
    ;;
  stop)
    printf 'inactive\n' >"${VKPI_TEST_STATE}/ActiveState"
    ;;
  mask)
    [ "$1" = --runtime ]
    unit="$2"
    printf 'masked\n' >"${VKPI_TEST_STATE}/LoadState"
    printf 'masked-runtime\n' >"${VKPI_TEST_STATE}/UnitFileState"
    ln -sfn /dev/null "${VKPI_TEST_MASK_ROOT}/${unit}"
    ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    sudo = fake_bin / "sudo"
    sudo.write_text("#!/bin/bash\nset -euo pipefail\nexec \"$@\"\n", encoding="utf-8")
    sudo.chmod(0o755)

    # Preserve the production logic verbatim while redirecting only the fixed
    # systemd runtime directory into pytest's private filesystem.
    function_source = function_source.replace("/run/systemd/system", str(mask_root))
    harness = f"""
set -euo pipefail
SYNC_UNITS_CAPTURED=1
SYNC_UNITS_MAY_HAVE_BEEN_MUTATED=0
SCHEDULER_QUIESCED=0
SCHEDULER_LOAD_STATE=loaded
SCHEDULER_ACTIVE_STATE=active
SCHEDULER_UNIT_FILE_STATE=enabled
SEPARATE_SCHEDULER_SERVICE=vkpi-scheduler.service
SSH_TARGET=fake-host
ssh() {{
  shift
  eval "$1"
}}
{function_source}
quiesce_remote_scheduler_unit
[ "${{SCHEDULER_QUIESCED}}" = 1 ]
quiesce_remote_scheduler_unit
[ "${{SCHEDULER_QUIESCED}}" = 1 ]
[ "$(cat "${{VKPI_TEST_STATE}}/LoadState")" = masked ]
[ "$(cat "${{VKPI_TEST_STATE}}/ActiveState")" = inactive ]
[ "$(cat "${{VKPI_TEST_STATE}}/UnitFileState")" = masked-runtime ]
[ "$(readlink -- "${{VKPI_TEST_MASK_ROOT}}/vkpi-scheduler.service")" = /dev/null ]
"""
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "VKPI_TEST_STATE": str(state_dir),
        "VKPI_TEST_MASK_ROOT": str(mask_root),
    }
    completed = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_provider_shutdown_receipt_is_retained_as_release_evidence() -> None:
    source = DEPLOY.read_text(encoding="utf-8")
    proof = source.index("verify_remote_anthropic_batch_shutdown() {")
    evidence = source.index(
        'LOCAL_ANTHROPIC_BATCH_SHUTDOWN="${POST_DEPLOY_EVIDENCE_DIR}/'
        'anthropic-batch-shutdown.json"'
    )
    accepted = source.rindex("DEPLOY_ACCEPTED=1")
    assert proof < evidence < accepted
    assert 'printf \'%s\\n\' "${ANTHROPIC_BATCH_PROVIDER_RECEIPT}"' in source
    assert 'chmod 600 "${LOCAL_ANTHROPIC_BATCH_SHUTDOWN}"' in source

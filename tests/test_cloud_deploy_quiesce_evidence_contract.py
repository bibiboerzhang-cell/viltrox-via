from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_PATH = ROOT / "scripts" / "ops" / "deploy_local_to_cloud.sh"
LANE_TEMPLATE_PATH = ROOT / "scripts" / "ops" / "systemd" / "vkpi-lane-overrides.env"
DRAIN_HELPER_PATH = ROOT / "scripts" / "ops" / "verify_release_drain.py"


def _deploy() -> str:
    return DEPLOY_PATH.read_text(encoding="utf-8")


def test_sync_timer_and_service_are_captured_quiesced_and_restored() -> None:
    deploy = _deploy()

    capture_call = deploy.index(
        "capture_remote_sync_unit_state\n",
        deploy.index("MIGRATION_MANIFEST_CSV="),
    )
    early_sync_quiesce = deploy.index("\nquiesce_remote_sync_units\n", capture_call)
    first_release_mutation = deploy.index(
        'ssh "${SSH_TARGET}" "sudo install -d',
        early_sync_quiesce,
    )
    quiesce_call = deploy.index("\nquiesce_remote_release_consumers\n", first_release_mutation)
    env_switch = deploy.index("staging_db_clone.py' switch-env", quiesce_call)
    pointer_switch = deploy.index("atomic_release_layout.py' activate", env_switch)
    assert (
        capture_call
        < early_sync_quiesce
        < first_release_mutation
        < quiesce_call
        < env_switch
        < pointer_switch
    )
    assert 'npm --prefix frontend run build' not in deploy

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
    assert "reviewed timer/service failed to stop" in quiesce
    assert "reviewed timer/service failed to mask" in quiesce
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


def test_release_drain_runs_live_before_any_quiesce_and_again_after_quiesce() -> None:
    deploy = _deploy()
    helper = DRAIN_HELPER_PATH.read_text(encoding="utf-8")

    live_drain = deploy.index("\nverify_remote_release_drain live\n")
    early_quiesce = deploy.index("\nquiesce_remote_sync_units\n", live_drain)
    quiesce = deploy.index(
        "\nquiesce_remote_release_consumers\n",
        deploy.index("# From this point until the new release"),
    )
    drain_gate = deploy.index("\nverify_remote_release_drain quiesced\n", quiesce)
    clone_create = deploy.index("STAGING_CLONE_CREATE_JSON=", drain_gate)
    env_switch = deploy.index("staging_db_clone.py' switch-env", clone_create)
    pointer_switch = deploy.index("atomic_release_layout.py' activate", env_switch)
    assert (
        live_drain
        < early_quiesce
        < quiesce
        < drain_gate
        < clone_create
        < env_switch
        < pointer_switch
    )

    function = deploy.split("verify_remote_release_drain()", 1)[1].split(
        "fetch_predeploy_runtime_health()", 1
    )[0]
    for required in (
        'RELEASE_CONSUMERS_QUIESCED}" != "1"',
        'SYNC_UNITS_QUIESCED}" != "1"',
        'SYNC_UNITS_MAY_HAVE_BEEN_MUTATED}" != "0"',
        '<"${DEPLOY_CANDIDATE_DIR}/scripts/ops/verify_release_drain.py"',
        "--env-file '${REMOTE_ROOT}/.env'",
        'local expected_database="${PREDEPLOY_DATABASE_NAME}"',
        'local expected_migration="${PREDEPLOY_MIGRATION}"',
        'expected_database="${STAGING_CLONE_DATABASE:-${PREDEPLOY_DATABASE_NAME}}"',
        'expected_migration="${LATEST_MIGRATION}"',
        "--expected-database '${expected_database}'",
        "--current-migration '${expected_migration}'",
        'assert d.get("database_identity_verified") is True',
        'assert d.get("search_path_verified") is True',
        "LIVE_RELEASE_DRAIN_VERIFIED=1",
        "FENCED_RELEASE_DRAIN_VERIFIED=1",
        "RELEASE_DRAIN_VERIFIED=1",
    ):
        assert required in function
    fenced = function.split("fenced)", 1)[1].split(";;", 1)[0]
    assert "PREDEPLOY_MIGRATION" not in fenced
    assert "LATEST_MIGRATION" in fenced
    assert "'${REMOTE_RELEASE_DIR}/scripts/ops/verify_release_drain.py'" not in function
    assert "SET TRANSACTION READ ONLY" not in deploy
    assert "default_transaction_read_only=on" in helper
    assert "client.xrange(" in helper
    assert 'min=f"({last_delivered_id}"' in helper
    assert "raw_xinfo_lag" in helper
    assert "lag_or_consumer_count_blocks_release" in helper


def test_health_sentinel_is_captured_quiesced_and_restored_on_every_exit() -> None:
    deploy = _deploy()
    capture = deploy.split("capture_remote_sync_unit_state()", 1)[1].split(
        "capture_remote_pgbouncer_unit_state()", 1
    )[0]
    for required in (
        'HEALTH_SENTINEL_SERVICE="vkpi-health-sentinel.service"',
        'HEALTH_SENTINEL_TIMER="vkpi-health-sentinel.timer"',
        "'${HEALTH_SENTINEL_SERVICE}'",
        "'${HEALTH_SENTINEL_TIMER}'",
        "HEALTH_SENTINEL_SERVICE_ACTIVE_STATE",
        "HEALTH_SENTINEL_SERVICE_UNIT_FILE_STATE",
        "HEALTH_SENTINEL_TIMER_ACTIVE_STATE",
        "HEALTH_SENTINEL_TIMER_UNIT_FILE_STATE",
    ):
        assert required in deploy if required.startswith("HEALTH_SENTINEL_") else required in capture

    for function_name, terminator in (
        ("quiesce_remote_sync_units()", "quiesce_remote_release_consumers()"),
        ("quiesce_remote_release_consumers()", "verify_remote_release_drain()"),
    ):
        block = deploy.split(function_name, 1)[1].split(terminator, 1)[0]
        sentinel_timer_stop = block.index(
            "sudo systemctl stop '${HEALTH_SENTINEL_TIMER}'"
        )
        sentinel_timer_mask = block.index(
            "sudo systemctl mask --runtime '${HEALTH_SENTINEL_TIMER}'"
        )
        sentinel_service_stop = block.index(
            "sudo systemctl stop '${HEALTH_SENTINEL_SERVICE}'"
        )
        sentinel_service_mask = block.index(
            "sudo systemctl mask --runtime '${HEALTH_SENTINEL_SERVICE}'"
        )
        assert (
            sentinel_timer_stop
            < sentinel_timer_mask
            < sentinel_service_stop
            < sentinel_service_mask
        )
        assert "SYNC_UNITS_MAY_HAVE_BEEN_MUTATED=1" in block

    restore = deploy.split("restore_remote_sync_unit_state()", 1)[1].split(
        "attempt_automatic_rollback()", 1
    )[0]
    for required in (
        "'${HEALTH_SENTINEL_SERVICE}'",
        "'${HEALTH_SENTINEL_SERVICE_ACTIVE_STATE}'",
        "'${HEALTH_SENTINEL_SERVICE_UNIT_FILE_STATE}'",
        "'${HEALTH_SENTINEL_TIMER}'",
        "'${HEALTH_SENTINEL_TIMER_ACTIVE_STATE}'",
        "'${HEALTH_SENTINEL_TIMER_UNIT_FILE_STATE}'",
        "REMOTE_RESTORE_REVIEWED_TIMERS",
        'observed_file="$(systemctl show --property UnitFileState',
        'observed_active="$(systemctl show --property ActiveState',
    ):
        assert required in restore

    cleanup = deploy.split("cleanup_post_deploy_evidence()", 1)[1].split(
        "trap cleanup_post_deploy_evidence EXIT", 1
    )[0]
    assert "restore_remote_sync_unit_state || true" in cleanup
    assert deploy.rindex("restore_remote_sync_unit_state\n") < deploy.rindex(
        "DEPLOY_ACCEPTED=1"
    )


def test_health_sentinel_unit_is_verified_captured_and_installed() -> None:
    deploy = _deploy()

    assert (
        'HEALTH_SENTINEL_SERVICE_UNIT_RELATIVE="scripts/ops/systemd/'
        'vkpi-health-sentinel.service"'
    ) in deploy
    assert "Reviewed health sentinel service unit must be an existing" in deploy

    verify_lines = [
        line for line in deploy.splitlines() if "sudo systemd-analyze verify" in line
    ]
    assert len(verify_lines) == 2
    for line in verify_lines:
        assert "'${REMOTE_RELEASE_DIR}/${HEALTH_SENTINEL_SERVICE_UNIT_RELATIVE}'" in line

    prepare = next(
        line
        for line in deploy.splitlines()
        if "atomic_release_layout.py' prepare" in line
    )
    assert "--unit-name '${HEALTH_SENTINEL_SERVICE}'" in prepare

    install = next(
        line
        for line in deploy.splitlines()
        if "sentinel_unit_target='/etc/systemd/system/${HEALTH_SENTINEL_SERVICE}'" in line
    )
    for required in (
        "sentinel_unit_source='${REMOTE_CURRENT_DIR}/"
        "${HEALTH_SENTINEL_SERVICE_UNIT_RELATIVE}'",
        r"sentinel_unit_tmp=\$(sudo mktemp '/etc/systemd/system/."
        "${HEALTH_SENTINEL_SERVICE}.XXXXXX')",
        r"sudo install -o root -g root -m 0644 "
        r'\"\${sentinel_unit_source}\" \"\${sentinel_unit_tmp}\"',
        r'sudo mv -f -- \"\${sentinel_unit_tmp}\" \"\${sentinel_unit_target}\"',
        "staged health sentinel owner, mode, or content verification failed",
        "installed health sentinel owner, mode, or content verification failed",
        r'sudo cmp -s \"\${sentinel_unit_source}\" \"\${sentinel_unit_target}\"',
    ):
        assert required in install


def test_legacy_writer_and_live_pid_guard_is_dynamic_and_rechecked(
    tmp_path: Path,
) -> None:
    deploy = _deploy()
    expected_units = (
        "viltrox-2.0-scheduler.service",
        "viltrox-2.0-worker.service",
        "viltrox-2.0-admin.service",
        "viltrox-2.0-public.service",
    )
    array = deploy.split("LEGACY_WRITER_UNITS=(", 1)[1].split(")", 1)[0]
    assert tuple(re.findall(r"^\s+([^\s]+\.service)$", array, re.MULTILINE)) == expected_units

    helper = deploy.split("verify_remote_legacy_writers_absent()", 1)[1].split(
        "quiesce_remote_pgbouncer_for_clone()", 1
    )[0]
    for required in (
        "systemctl show --property ActiveState",
        "inactive|failed",
        'if [ -L "${pidfile}" ]',
        '[ -f "${pidfile}" ]',
        '[ -d "/proc/${pid}" ]',
        "legacy runtime/worker.pid still represents a live process",
    ):
        assert required in helper

    calls = [
        match.start()
        for match in re.finditer(
            r"^verify_remote_legacy_writers_absent$", deploy, re.MULTILINE
        )
    ]
    assert len(calls) == 4
    first_quiesce = deploy.index("\nquiesce_remote_sync_units\n")
    worker_ready = deploy.index("exact 16-service worker fleet failed readiness")
    commit = deploy.index("RELEASE_VALIDATION_COMMIT_STARTED=1")
    fence_remove = deploy.index("\nremove_remote_release_validation_fence\n", commit)
    accepted = deploy.rindex("DEPLOY_ACCEPTED=1")
    assert (
        calls[0]
        < first_quiesce
        < worker_ready
        < calls[1]
        < calls[2]
        < commit
        < fence_remove
        < calls[3]
        < accepted
    )

    remote_guard = deploy.split(
        "<<'REMOTE_VERIFY_LEGACY_WRITERS'\n", 1
    )[1].split("\nREMOTE_VERIFY_LEGACY_WRITERS", 1)[0]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_systemctl = fake_bin / "systemctl"
    fake_systemctl.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"${FAKE_SYSTEMD_ACTIVE_STATE:-inactive}\"\n",
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)
    remote_root = tmp_path / "remote"
    (remote_root / "runtime").mkdir(parents=True)
    command = ["/bin/bash", "-s", "--", str(remote_root), *expected_units]
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "FAKE_SYSTEMD_ACTIVE_STATE": "inactive",
    }

    idle = subprocess.run(
        command,
        input=remote_guard,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert idle.returncode == 0, idle.stderr

    active = subprocess.run(
        command,
        input=remote_guard,
        env={**environment, "FAKE_SYSTEMD_ACTIVE_STATE": "active"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert active.returncode != 0
    assert "legacy writer is not inactive" in active.stderr

    (remote_root / "runtime" / "worker.pid").write_text(
        f"{os.getpid()}\n", encoding="utf-8"
    )
    live_pid = subprocess.run(
        command,
        input=remote_guard,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert live_pid.returncode != 0
    assert "runtime/worker.pid still represents a live process" in live_pid.stderr


def test_staging_clone_captures_and_quiesces_pgbouncer_service_and_socket() -> None:
    deploy = _deploy()

    assert 'PGBOUNCER_SERVICE="pgbouncer.service"' in deploy
    assert 'PGBOUNCER_SOCKET="pgbouncer.socket"' in deploy
    assert 'PGBOUNCER_PORT="6432"' in deploy

    capture = deploy.split("capture_remote_pgbouncer_unit_state()", 1)[1].split(
        "restore_remote_pgbouncer_state()", 1
    )[0]
    for required in (
        'if [ "${STAGING_DB_CLONE_MODE}" != "1" ]',
        "--property LoadState",
        "--property ActiveState",
        "--property UnitFileState",
        "'${PGBOUNCER_SERVICE}'",
        "'${PGBOUNCER_SOCKET}'",
        "PGBOUNCER_STATE_CAPTURED=1",
    ):
        assert required in capture

    top_level = deploy[
        deploy.index("setup_deploy_ssh_transport\n") :
        deploy.index("sync_state=", deploy.index("setup_deploy_ssh_transport\n"))
    ]
    assert (
        'if [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then\n'
        "  capture_remote_pgbouncer_unit_state\n"
        "fi"
    ) in top_level

    quiesce = deploy.split("quiesce_remote_pgbouncer_for_clone()", 1)[1].split(
        "quiesce_remote_release_consumers()", 1
    )[0]
    socket_stop = quiesce.index('sudo systemctl stop "${socket}"')
    socket_mask = quiesce.index('sudo systemctl mask --runtime "${socket}"')
    service_stop = quiesce.index('sudo systemctl stop "${service}"')
    service_mask = quiesce.index('sudo systemctl mask --runtime "${service}"')
    assert socket_stop < socket_mask < service_stop < service_mask
    assert 'mask_path="/run/systemd/system/${unit}"' in quiesce
    assert 'readlink -- "${mask_path}"' in quiesce
    assert 'ss -H -ltn "sport = :${port}"' in quiesce
    assert "PgBouncer listener remains on 6432 after quiesce" in quiesce
    assert "PGBOUNCER_MAY_HAVE_BEEN_MUTATED=1" in quiesce
    assert "PGBOUNCER_QUIESCED=1" in quiesce
    assert "systemctl enable" not in quiesce
    assert "systemctl disable" not in quiesce

    release_quiesce = deploy.split("quiesce_remote_release_consumers()", 1)[1].split(
        "fetch_predeploy_runtime_health()", 1
    )[0]
    assert "quiesce_remote_pgbouncer_for_clone" in release_quiesce
    clone_gate = deploy[
        deploy.index('if [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then', 2000) :
        deploy.index("STAGING_CLONE_CREATE_JSON=", 2000)
    ]
    assert 'PGBOUNCER_QUIESCED}" != "1"' in clone_gate


def test_pgbouncer_restore_is_exact_idempotent_and_precedes_all_consumers() -> None:
    deploy = _deploy()
    restore = deploy.split("restore_remote_pgbouncer_state()", 1)[1].split(
        "restore_remote_sync_unit_state()", 1
    )[0]

    for required in (
        'if [ "${STAGING_DB_CLONE_MODE}" != "1" ]',
        'if [ "${PGBOUNCER_MAY_HAVE_BEEN_MUTATED}" != "1" ]',
        'sudo systemctl unmask --runtime "${socket}" "${service}"',
        "sudo systemctl daemon-reload",
        "'${PGBOUNCER_SERVICE_ACTIVE_STATE}'",
        "'${PGBOUNCER_SOCKET_ACTIVE_STATE}'",
        "'${PGBOUNCER_SERVICE_UNIT_FILE_STATE}'",
        "'${PGBOUNCER_SOCKET_UNIT_FILE_STATE}'",
        'sudo systemctl stop "${socket}" "${service}"',
        "--property MainPID",
        "PgBouncer service MainPID is invalid after restore",
        "fail_closed_pgbouncer",
        "PGBOUNCER_RESTORED=1",
    ):
        assert required in restore
    assert "systemctl enable" not in restore
    assert "systemctl disable" not in restore

    rollback = deploy.split("attempt_automatic_rollback()", 1)[1].split(
        "cleanup_post_deploy_evidence()", 1
    )[0]
    rollback_restore = rollback.index("restore_remote_pgbouncer_state")
    rollback_redis_start = rollback.index(
        "sudo systemctl start '${STAGING_REDIS_WORKER_SERVICE}'"
    )
    rollback_web_start = rollback.index(
        "sudo systemctl restart '${SERVICE_NAME}' ${WORKER_SYSTEMD_UNIT_ARGS}"
    )
    assert rollback_restore < rollback_redis_start < rollback_web_start

    success = deploy.split(
        "# The static dual map must still match the prepared hash before PgBouncer",
        1,
    )[1]
    success_restore = success.index("restore_remote_pgbouncer_state")
    redis_start = success.index("sudo systemctl enable --now '${STAGING_REDIS_WORKER_SERVICE}'")
    web_start = success.index("sudo systemctl restart '${SERVICE_NAME}'")
    worker_start = success.index("sudo systemctl restart ${WORKER_SYSTEMD_UNIT_ARGS}")
    assert success_restore < redis_start < web_start < worker_start

    cleanup = deploy.split("cleanup_post_deploy_evidence()", 1)[1].split(
        "trap cleanup_post_deploy_evidence EXIT", 1
    )[0]
    assert "PGBOUNCER_MAY_HAVE_BEEN_MUTATED" in cleanup
    assert "restore_remote_pgbouncer_state || true" in cleanup


def test_non_clone_deploy_never_touches_pgbouncer_units() -> None:
    deploy = _deploy()
    # There are no hidden call sites: one conditional capture, one release
    # quiesce, one rollback re-quiesce, and three idempotent restore paths.
    assert deploy.count("capture_remote_pgbouncer_unit_state") == 2
    assert deploy.count("quiesce_remote_pgbouncer_for_clone") == 3
    assert deploy.count("restore_remote_pgbouncer_state") == 4
    for function_name, next_function in (
        ("capture_remote_pgbouncer_unit_state()", "restore_remote_pgbouncer_state()"),
        ("restore_remote_pgbouncer_state()", "restore_remote_sync_unit_state()"),
        ("quiesce_remote_pgbouncer_for_clone()", "quiesce_remote_release_consumers()"),
    ):
        body = deploy.split(function_name, 1)[1].split(next_function, 1)[0]
        guard = body.index('if [ "${STAGING_DB_CLONE_MODE}" != "1" ]')
        first_remote_touch = min(
            position
            for marker in ("ssh ", "systemctl ")
            if (position := body.find(marker)) >= 0
        )
        assert guard < body.index("return 0", guard) < first_remote_touch


def test_staging_clone_binds_effective_web_runtime_and_dual_map_transaction() -> None:
    deploy = _deploy()

    capture_runtime = deploy.index(
        'capture_remote_web_database_runtime "${STAGING_SOURCE_DATABASE}"'
    )
    capture_map = deploy.index("capture_remote_pgbouncer_database_map", capture_runtime)
    first_mutation = deploy.index("\nquiesce_remote_sync_units\n", capture_map)
    clone_create = deploy.index("STAGING_CLONE_CREATE_JSON=", first_mutation)
    prepare_map = deploy.index("prepare_remote_pgbouncer_database_map", clone_create)
    env_switch = deploy.index("staging_db_clone.py' switch-env", prepare_map)
    static_verify = deploy.index(
        "\nverify_remote_pgbouncer_database_map\n", env_switch
    )
    service_restore = deploy.index(
        "\nrestore_remote_pgbouncer_state\n", static_verify
    )
    source_probe = deploy.index(
        'probe_remote_pgbouncer_database "${STAGING_SOURCE_DATABASE}"',
        service_restore,
    )
    target_probe = deploy.index(
        'probe_remote_pgbouncer_database "${STAGING_CLONE_DATABASE}"',
        source_probe,
    )
    redis_start = deploy.index(
        "sudo systemctl enable --now '${STAGING_REDIS_WORKER_SERVICE}'",
        target_probe,
    )
    assert (
        capture_runtime
        < capture_map
        < first_mutation
        < clone_create
        < prepare_map
        < env_switch
        < static_verify
        < service_restore
        < source_probe
        < target_probe
        < redis_start
    )
    assert (
        'PGBOUNCER_WEB_POOL_EFFECTIVE_BEFORE="${PGBOUNCER_WEB_POOL_EFFECTIVE}"'
        in deploy
    )
    assert deploy.count(
        '"${PGBOUNCER_WEB_POOL_EFFECTIVE_BEFORE}"'
    ) >= 4

    rollback = deploy.split("attempt_automatic_rollback()", 1)[1].split(
        "cleanup_post_deploy_evidence()", 1
    )[0]
    stop_consumers = rollback.index("complete web/worker fleet")
    requiesce = rollback.index("quiesce_remote_pgbouncer_for_clone", stop_consumers)
    restore_layout = rollback.index("atomic_release_layout.py' restore", requiesce)
    restore_map = rollback.index("restore_remote_pgbouncer_database_map", restore_layout)
    restore_service = rollback.index("restore_remote_pgbouncer_state", restore_map)
    rollback_probe = rollback.index(
        'probe_remote_pgbouncer_database "${PREDEPLOY_DATABASE_NAME}"',
        restore_service,
    )
    restart_web = rollback.index(
        "sudo systemctl restart '${SERVICE_NAME}' ${WORKER_SYSTEMD_UNIT_ARGS}",
        rollback_probe,
    )
    assert (
        stop_consumers
        < requiesce
        < restore_layout
        < restore_map
        < restore_service
        < rollback_probe
        < restart_web
    )

    final_verify = deploy.rindex("verify_remote_pgbouncer_database_map")
    final_target_probe = deploy.rindex(
        'probe_remote_pgbouncer_database "${STAGING_CLONE_DATABASE}"'
    )
    accepted = deploy.rindex("DEPLOY_ACCEPTED=1")
    assert final_verify < final_target_probe < accepted
    assert '"DB_USE_PGBOUNCER": "0"' in (
        ROOT / "scripts" / "ops" / "staging_db_clone.py"
    ).read_text(encoding="utf-8")


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
        # 2026-07-22 多并发地基:车道永远直连 5432(session advisory lock 与
        # PgBouncer transaction pooling 不相容),值被 deploy 验证器钉死为 0。
        "DB_USE_PGBOUNCER",
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
    assert entries["DB_USE_PGBOUNCER"] == "0"
    assert entries["POSTGRES_POOL_MAX_SIZE"] == "6"
    assert 'integer("DB_USE_PGBOUNCER", 0, 0)' in deploy
    assert not any(
        marker in key for key in entries for marker in ("SECRET", "TOKEN", "PASSWORD", "API_KEY")
    )

    validate_at = deploy.index("if ! validate_lane_override_template; then")
    prepare_line = next(
        line for line in deploy.splitlines() if "atomic_release_layout.py' prepare" in line
    )
    assert "--rollback-file '${REMOTE_LANE_OVERRIDE_FILE}'" in prepare_line
    prepare_at = deploy.index("atomic_release_layout.py' prepare", validate_at)
    first_release_mutation = deploy.index('ssh "${SSH_TARGET}" "sudo install -d', validate_at)
    install_block_at = deploy.index(
        "# Install only the already-verified unit payload", first_release_mutation
    )
    install_at = deploy.index("lane_tmp=\\$(sudo mktemp", install_block_at)
    worker_restart = deploy.index("sudo systemctl restart ${WORKER_SYSTEMD_UNIT_ARGS}", install_at)
    assert validate_at < first_release_mutation < prepare_at < install_at < worker_restart
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

    rollback = deploy.split("attempt_automatic_rollback()", 1)[1].split(
        "cleanup_post_deploy_evidence()", 1
    )[0]
    assert rollback.index("atomic_release_layout.py' restore") < rollback.index(
        "sudo systemctl restart '${SERVICE_NAME}'"
    )


def _ancestor_guard_function(deploy: str) -> str:
    start = deploy.index("\nassert_remote_release_is_ancestor() {\n") + 1
    end = deploy.index("\n}\n", start) + 3
    return deploy[start:end]


def test_deploy_refuses_candidates_that_do_not_descend_from_the_served_release() -> None:
    deploy = _deploy()

    main = deploy[deploy.index("\nrun_predeploy_embedded_browser_gate\n") :]
    transport_at = main.index("\nsetup_deploy_ssh_transport\n")
    prelock_guard_at = main.index("\nassert_remote_release_is_ancestor prelock\n", transport_at)
    mutex_at = main.index("\nacquire_remote_deploy_lock\n", prelock_guard_at)
    locked_guard_at = main.index("\nassert_remote_release_is_ancestor locked\n", mutex_at)
    first_state_read_at = main.index("\ncapture_remote_sync_unit_state\n", locked_guard_at)
    release_upload_at = main.index(
        "sudo install -d -o root -g root -m 0755 '${REMOTE_RELEASES_DIR}'", locked_guard_at
    )
    assert (
        transport_at
        < prelock_guard_at
        < mutex_at
        < locked_guard_at
        < first_state_read_at
        < release_upload_at
    )

    guard = _ancestor_guard_function(deploy)
    assert "readlink -- '${REMOTE_CURRENT_DIR}'" in guard
    assert "'${REMOTE_CURRENT_DIR}/BUILD_GIT_SHA'" in guard
    assert 'git merge-base --is-ancestor "${remote_sha}" "${candidate_sha}"' in guard
    assert 'git log --oneline "${candidate_sha}..${remote_sha}"' in guard
    assert 'DEPLOY_ALLOW_NON_ANCESTOR="${VKPI_DEPLOY_ALLOW_NON_ANCESTOR:-0}"' in deploy
    assert 'if [ "${DEPLOY_ALLOW_NON_ANCESTOR}" = "1" ]; then' in guard
    assert "category=candidate_not_descendant" in guard
    assert "category=current_pointer_missing" in guard
    assert "category=transport_failed" in guard
    assert "category=remote_commit_unknown_locally" in guard
    # The override path must stay loud: it is an operator decision, not a default.
    assert "!!! VKPI_DEPLOY_ALLOW_NON_ANCESTOR=1" in guard


def test_ancestor_guard_behaviour_against_a_stubbed_remote(tmp_path: Path) -> None:
    deploy = _deploy()
    guard = _ancestor_guard_function(deploy)
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "HOME": str(tmp_path),
    }

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(name: str) -> str:
        (repo / name).write_text(name, encoding="utf-8")
        git("add", name)
        git("commit", "-q", "-m", name)
        return git("rev-parse", "HEAD")

    git("init", "-q", "-b", "main")
    base = commit("base")
    served = commit("hotfix")
    git("checkout", "-q", "-b", "diverged", base)
    diverged = commit("local-only")
    git("checkout", "-q", "-b", "descendant", served)
    descendant = commit("on-top-of-hotfix")

    (tmp_path / "guard.sh").write_text(guard, encoding="utf-8")
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -u\n"
        "SSH_TARGET=viltrox\n"
        "REMOTE_CURRENT_DIR=/opt/viltrox-2.0/current\n"
        'FIRST_ATOMIC_BOOTSTRAP_MODE="${FIRST_ATOMIC_BOOTSTRAP_MODE:-0}"\n'
        'DEPLOY_ALLOW_NON_ANCESTOR="${VKPI_DEPLOY_ALLOW_NON_ANCESTOR:-0}"\n'
        'ssh() { printf "%s\\n" "${FAKE_LINK}"; printf "%s\\n" "${FAKE_BUILD}"; '
        'return "${FAKE_RC:-0}"; }\n'
        f'source "{tmp_path / "guard.sh"}"\n'
        "assert_remote_release_is_ancestor prelock\n",
        encoding="utf-8",
    )

    def run(candidate: str, link: str, build: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(harness)],
            cwd=repo,
            env={**env, "LOCAL_GIT_SHA": candidate, "FAKE_LINK": link, "FAKE_BUILD": build, **extra},
            capture_output=True,
            text=True,
            check=False,
        )

    served_link = f"/opt/viltrox-2.0/releases/20260821T000000Z-{served[:12]}"

    accepted = run(descendant, served_link, served)
    assert accepted.returncode == 0, accepted.stderr
    assert "is an ancestor of candidate" in accepted.stdout

    # Pointer suffix alone (legacy release without BUILD_GIT_SHA) still resolves.
    assert run(descendant, served_link, "").returncode == 0

    refused = run(diverged, served_link, served)
    assert refused.returncode == 1
    assert "category=candidate_not_descendant" in refused.stderr
    assert served in refused.stderr and diverged in refused.stderr
    assert "hotfix" in refused.stderr
    assert "VKPI_DEPLOY_ALLOW_NON_ANCESTOR=1" in refused.stderr

    overridden = run(diverged, served_link, served, VKPI_DEPLOY_ALLOW_NON_ANCESTOR="1")
    assert overridden.returncode == 0
    assert "!!! VKPI_DEPLOY_ALLOW_NON_ANCESTOR=1" in overridden.stderr

    unknown = run(descendant, "/opt/viltrox-2.0/releases/x-abcdefabcdef", "")
    assert unknown.returncode == 1
    assert "category=remote_commit_unknown_locally" in unknown.stderr

    missing = run(descendant, "", "")
    assert missing.returncode == 1
    assert "category=current_pointer_missing" in missing.stderr

    bootstrap = run(descendant, "", "", FIRST_ATOMIC_BOOTSTRAP_MODE="1")
    assert bootstrap.returncode == 0

    transport = run(descendant, "", "", FAKE_RC="255")
    assert transport.returncode == 1
    assert "category=transport_failed" in transport.stderr

    conflict = run(descendant, f"/opt/viltrox-2.0/releases/x-{diverged[:12]}", served)
    assert conflict.returncode == 1
    assert "category=release_identity_conflict" in conflict.stderr


def test_train_script_orders_the_shuttle_steps_and_passes_bash_n() -> None:
    train_path = ROOT / "scripts" / "ops" / "train.sh"
    train = train_path.read_text(encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(train_path)], check=False).returncode == 0
    assert os.access(train_path, os.X_OK)

    first_clean = train.index('assert_clean_tree "出发前"')
    freeze_at = train.index("freeze --repo")
    second_clean = train.index('assert_clean_tree "freeze 后"')
    dist_copy = train.index('rsync -a --delete "${CANDIDATE_DIR}/frontend/dist/" "${ROOT}/frontend/dist/"')
    restart_at = train.index("\nwait_for_alignment\n")
    deploy_at = train.index('bash "${ROOT}/scripts/ops/deploy_local_to_cloud.sh"')
    assert first_clean < freeze_at < second_clean < dist_copy < restart_at < deploy_at

    # kill 本地栈:纯数字 pid 逐个点名(带噪声令牌整条失效),supervisor 不在清单里。
    assert "grep -E '^[0-9]+$'" in train
    assert "xargs -n1 kill -TERM" in train and "xargs -n1 kill -KILL" in train
    assert "local_stack_supervisor" not in train.split("LOCAL_STACK_PATTERNS=(", 1)[1].split(")", 1)[0]
    # 对齐四件:server sha / client sha / apify workers≥N / redis worker 单代。
    alignment = train.split("health_alignment() {", 1)[1].split("wait_for_alignment() {", 1)[0]
    for required in ('"server": server == expected', '"client": client == expected', "workers >= min_workers", "redis_count == 1"):
        assert required in alignment
    # 令牌只走 --env-file;部署段日志落 runtime/ops/deploy-<sha>.log;SHA 缺省 HEAD 且显式值必须等于 HEAD。
    assert '--env-file "${HEALTH_ENV_FILE}"' in train
    assert 'DEPLOY_LOG="${OPS_DIR}/deploy-${SHORT9}.log"' in train
    assert 'HEAD_SHA="$(git rev-parse --verify HEAD)"' in train
    assert 'VKPI_STAGING_DB_CLONE=0' in train

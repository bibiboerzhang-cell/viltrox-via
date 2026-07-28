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

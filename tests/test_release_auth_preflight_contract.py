from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_deploy_preflights_candidate_auth_before_every_remote_mutation() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    runtime_env = _read("scripts/runtime_env.py")
    preflight = deploy.split(
        "verify_remote_candidate_production_auth_contract()", 1
    )[1].split("harden_first_atomic_root()", 1)[0]

    assert '2>/dev/null < "${DEPLOY_CANDIDATE_DIR}/scripts/runtime_env.py"' in preflight
    assert "sudo -n -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env -i" in preflight
    assert "ENVIRONMENT=production" in preflight
    assert "'${REMOTE_ROOT}/.venv/bin/python' -B -I -" in preflight
    assert "--production-auth-preflight '${REMOTE_ROOT}/.env' '${REMOTE_APP_GROUP}'" in preflight
    assert 'category="candidate_or_transport_invalid"' in preflight
    assert "credential values, lengths, and" in preflight
    assert "fingerprints are neither computed nor emitted" in preflight
    assert "JWT_SECRET}" not in preflight
    assert "ADMIN_PASSWORD}" not in preflight
    for required in (
        "initial = env_path.lstat()",
        "not stat.S_ISREG(initial.st_mode)",
        "initial.st_nlink != 1",
        "initial.st_uid != expected_owner_uid",
        "initial.st_gid != expected_group_gid",
        "stat.S_IMODE(initial.st_mode) != 0o640",
        'getattr(os, "O_NOFOLLOW", 0)',
        "_load_env_file(descriptor_path)",
        "_apply_auth_contract()",
        "expected_owner_uid=0",
        "grp.getgrnam(arguments[2]).gr_gid",
        'sys.stdout.write(category + "\\n")',
    ):
        assert required in runtime_env

    main = deploy[deploy.index("\nrun_predeploy_embedded_browser_gate\n") :]
    transport_at = main.index("\nsetup_deploy_ssh_transport\n")
    prelock_at = main.index(
        "\nverify_remote_candidate_production_auth_contract prelock\n",
        transport_at,
    )
    mutex_at = main.index("\nacquire_remote_deploy_lock\n", prelock_at)
    locked_at = main.index(
        "\nverify_remote_candidate_production_auth_contract locked\n",
        mutex_at,
    )
    first_state_read_at = main.index("\ncapture_remote_sync_unit_state\n", locked_at)
    bootstrap_mutation_at = main.index("\n  harden_first_atomic_root\n", locked_at)
    timer_quiesce_at = main.index("\nquiesce_remote_sync_units\n", locked_at)
    release_upload_at = main.index(
        "sudo install -d -o root -g root -m 0755 '${REMOTE_RELEASES_DIR}'",
        locked_at,
    )
    consumer_quiesce_at = main.index("\nquiesce_remote_release_consumers\n", locked_at)
    activate_at = main.index("atomic_release_layout.py' activate", locked_at)

    assert transport_at < prelock_at < mutex_at < locked_at < first_state_read_at
    assert locked_at < min(
        bootstrap_mutation_at,
        timer_quiesce_at,
        release_upload_at,
        consumer_quiesce_at,
        activate_at,
    )

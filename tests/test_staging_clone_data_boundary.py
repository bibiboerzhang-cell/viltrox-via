from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "ops" / "deploy_local_to_cloud.sh"


def test_new_staging_clone_is_hard_blocked_before_any_remote_release_window() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8")

    mode_validation = deploy.index(
        'if [ "${STAGING_DB_CLONE_MODE}" != "0" ] && [ "${STAGING_DB_CLONE_MODE}" != "1" ]'
    )
    guard_at = deploy.index(
        'if [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then\n'
        '  echo "Refusing VKPI_STAGING_DB_CLONE=1 before remote mutation:',
        mode_validation,
    )
    guard_end = deploy.index("\nfi", guard_at) + len("\nfi")
    guard = deploy[guard_at:guard_end]
    assert "writable staging-clone validation can lose accepted writes" in guard
    assert "proven read-only validation and irreversible commit protocol" in guard
    assert "exit 1" in guard
    assert "VKPI_" not in guard.split("VKPI_STAGING_DB_CLONE", 1)[1]

    first_remote_release_step = deploy.index("\nrun_predeploy_embedded_browser_gate\n")
    prepare_may_have_committed = deploy.index(
        "\nROLLBACK_PREPARE_MAY_HAVE_COMMITTED=1\n", first_remote_release_step
    )
    rollback_arm = deploy.index(
        "\n  ROLLBACK_ARMED=1\n", prepare_may_have_committed
    )
    clone_create = deploy.index("staging_db_clone.py' create")
    clone_web_start = deploy.index("sudo systemctl restart '${SERVICE_NAME}'", clone_create)
    assert (
        guard_end
        < first_remote_release_step
        < prepare_may_have_committed
        < rollback_arm
        < clone_create
        < clone_web_start
    )

    # Existing database-safe paths remain available; only creation/activation
    # of a new writable clone is disabled by this P0 guard.
    assert 'DATABASE_RELEASE_STRATEGY="in-place"' in deploy
    assert 'DATABASE_RELEASE_STRATEGY="reuse-active-clone"' in deploy

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_worker_start_resolves_and_validates_exact_source_identity() -> None:
    start = _read("scripts/start_worker.sh")

    build_file_at = start.index('"$ROOT/BUILD_GIT_SHA"')
    git_head_at = start.index('git -C "$ROOT" rev-parse HEAD')
    validation_at = start.index('^[0-9a-f]{40}$')
    export_at = start.index('export APP_GIT_SHA="$WORKER_SOURCE_SHA"')
    launch_at = start.index('nohup "$PYTHON_BIN"')

    assert build_file_at < validation_at < export_at < launch_at
    assert git_head_at < validation_at
    assert "refusing to start worker without a valid 40-hex source identity" in start


def test_worker_identity_schema_runtime_write_and_health_read_are_aligned() -> None:
    migration = _read("migrations/246_vkpi_worker_runtime_identity.sql")
    worker = _read("backend/app/workers/apify_jobs_worker.py")
    health = _read("backend/app/main.py")

    fields = ("worker_git_sha", "boot_nonce_sha256", "started_at")
    for field in fields:
        assert re.search(rf"ADD COLUMN IF NOT EXISTS {field}\b", migration)
        assert field in worker
        assert field in health
    assert "ON CONFLICT (worker_name) DO UPDATE" in worker


def test_membership_backfill_is_non_destructive_and_ambiguous_safe() -> None:
    forward = _read("migrations/245_vkpi_staff_organization_membership_backfill.sql")
    rollback = _read("migrations/245_vkpi_staff_organization_membership_backfill_down.sql")

    assert "INSERT INTO organization_members" in forward
    assert "WHERE NOT EXISTS" in forward
    assert "existing.staff_id = s.id" in forward
    assert "ON CONFLICT (organization_id, staff_id) DO NOTHING" in forward
    assert not re.search(r"\b(DELETE|UPDATE|DROP|TRUNCATE)\b", forward, re.IGNORECASE)
    assert "Intentional no-op" in rollback

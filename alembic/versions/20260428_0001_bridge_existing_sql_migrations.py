"""bridge existing sql migrations into Alembic

Revision ID: 20260428_0001
Revises:
Create Date: 2026-04-28
"""
from __future__ import annotations

from pathlib import Path

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260428_0001"
down_revision = None
branch_labels = None
depends_on = None

POSTGRES_MIGRATION_SEQUENCE = (
    "003_postgres_baseline.sql",
    "005_via_control_stack.sql",
    "006_policy_governance_student_identity.sql",
    "007_via_reward_trace.sql",
    "008_p1_observability.sql",
    "009_job_runtime_stack.sql",
    "010_party_layer.sql",
    "011_security_lockout.sql",
    "012_admin_system_ops.sql",
    "013_creator_public.sql",
    "014_pending_asset_cleanup.sql",
    "015_v5_commerce_admin_schema.sql",
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _migrations_dir() -> Path:
    return _project_root() / "migrations"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name not in {"postgresql", "postgres"}:
        raise RuntimeError("Alembic migration bridge only supports Postgres runtimes.")

    bind.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version_key TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    existing = {
        row[0]
        for row in bind.execute(sa.text("SELECT version_key FROM schema_migrations")).fetchall()
    }

    for migration_name in POSTGRES_MIGRATION_SEQUENCE:
        migration_file = _migrations_dir() / migration_name
        if not migration_file.exists():
            raise RuntimeError(f"Required migration file missing: {migration_name}")
        if migration_name in existing:
            continue
        bind.exec_driver_sql(migration_file.read_text(encoding="utf-8"))
        bind.execute(
            sa.text(
                """
                INSERT INTO schema_migrations(version_key)
                VALUES (:version_key)
                ON CONFLICT (version_key) DO NOTHING
                """
            ),
            {"version_key": migration_name},
        )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is not supported for the bridge revision; "
        "use database snapshot restore for rollback."
    )

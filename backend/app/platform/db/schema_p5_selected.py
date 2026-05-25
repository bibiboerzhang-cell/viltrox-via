"""SQLite guard for selected V-KPI P5 tables."""
from __future__ import annotations

from app.db.connection import get_conn, is_postgres_runtime

_SCHEMA_READY = False


def ensure_vkpi_p5_selected_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_uid TEXT NOT NULL UNIQUE,
            campaign_name TEXT NOT NULL,
            owner_staff_id INTEGER,
            platform TEXT DEFAULT '',
            product_sku TEXT DEFAULT '',
            period_start TEXT,
            period_end TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            goal_json TEXT NOT NULL DEFAULT '{}',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_campaigns_status ON vkpi_campaigns(status, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_campaigns_owner ON vkpi_campaigns(owner_staff_id, status);

        CREATE TABLE IF NOT EXISTS vkpi_campaign_projects (
            campaign_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            added_by_staff_id INTEGER,
            added_at TEXT NOT NULL,
            PRIMARY KEY (campaign_id, project_id)
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_campaign_projects_project ON vkpi_campaign_projects(project_id);

        CREATE TABLE IF NOT EXISTS vkpi_budget_pools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pool_uid TEXT NOT NULL UNIQUE,
            pool_name TEXT NOT NULL,
            period_start TEXT,
            period_end TEXT,
            total_budget_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD',
            owner_staff_id INTEGER,
            status TEXT NOT NULL DEFAULT 'active',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_budget_pools_status ON vkpi_budget_pools(status, period_start, period_end);

        CREATE TABLE IF NOT EXISTS vkpi_budget_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            budget_pool_id INTEGER NOT NULL,
            campaign_id INTEGER,
            project_id INTEGER,
            staff_id INTEGER,
            allocated_cents INTEGER NOT NULL DEFAULT 0,
            note TEXT DEFAULT '',
            created_by_staff_id INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_budget_alloc_pool ON vkpi_budget_allocations(budget_pool_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_offboarding_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_uid TEXT NOT NULL UNIQUE,
            staff_id INTEGER NOT NULL,
            initiated_by_staff_id INTEGER,
            new_owner_staff_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            active_claims_count INTEGER NOT NULL DEFAULT 0,
            active_projects_count INTEGER NOT NULL DEFAULT 0,
            channels_count INTEGER NOT NULL DEFAULT 0,
            actual_costs_count INTEGER NOT NULL DEFAULT 0,
            actions_json TEXT NOT NULL DEFAULT '[]',
            result_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            executed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_offboarding_staff ON vkpi_offboarding_runs(staff_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_offboarding_status ON vkpi_offboarding_runs(status, created_at DESC);
        """
    )
    conn.commit()
    _SCHEMA_READY = True

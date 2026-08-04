from __future__ import annotations

import re
import uuid
from typing import Any

import pytest


pytestmark = pytest.mark.pg


_FLOW_SCHEMA = """
CREATE TABLE users (
    id BIGINT PRIMARY KEY,
    email TEXT NOT NULL,
    name TEXT NOT NULL,
    avatar_url TEXT NOT NULL DEFAULT ''
);
CREATE TABLE staff (
    id BIGINT PRIMARY KEY,
    user_id BIGINT REFERENCES users(id),
    role TEXT NOT NULL DEFAULT 'staff',
    active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE vkpi_project_members (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL,
    staff_id BIGINT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer'
);
CREATE TABLE vkpi_projects (
    id BIGSERIAL PRIMARY KEY,
    project_name TEXT NOT NULL,
    product_sku TEXT NOT NULL DEFAULT '',
    product_name TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT 'discovery',
    stage_status TEXT NOT NULL DEFAULT 'active',
    priority TEXT NOT NULL DEFAULT 'normal',
    assigned_staff_id BIGINT,
    created_by_staff_id BIGINT,
    target_post_date TIMESTAMPTZ,
    due_at TIMESTAMPTZ,
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    restricted BOOLEAN NOT NULL DEFAULT FALSE,
    is_public BOOLEAN NOT NULL DEFAULT FALSE,
    source_type TEXT NOT NULL DEFAULT 'manual'
);
CREATE TABLE kols (
    id BIGINT PRIMARY KEY,
    channel_name TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT ''
);
CREATE TABLE vkpi_kol_pool (
    id BIGSERIAL PRIMARY KEY,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    followers BIGINT,
    viltrox_fit_score DOUBLE PRECISION,
    profile_url TEXT NOT NULL DEFAULT '',
    avatar_url TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    duplicate_of_id BIGINT,
    linked_main_kol_id BIGINT,
    dashboard_account_type TEXT NOT NULL DEFAULT '',
    dashboard_tier TEXT NOT NULL DEFAULT '',
    has_video_evidence BOOLEAN NOT NULL DEFAULT FALSE,
    video_evidence_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE vkpi_kol_pool_favorites (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id),
    staff_id BIGINT NOT NULL,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (kol_pool_id, staff_id)
);
CREATE TABLE vkpi_kol_pool_members (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL,
    staff_id BIGINT NOT NULL,
    shared_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE vkpi_kol_pool_contacts (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL,
    contact_type TEXT NOT NULL,
    contact_value TEXT NOT NULL,
    contact_source TEXT NOT NULL DEFAULT '',
    consent_basis TEXT NOT NULL DEFAULT ''
);
CREATE TABLE vkpi_kol_claims (
    id BIGSERIAL PRIMARY KEY,
    kol_id BIGINT NOT NULL,
    staff_id BIGINT NOT NULL,
    project_id BIGINT,
    status TEXT NOT NULL DEFAULT 'active',
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    last_effective_touch_at TIMESTAMPTZ
);
CREATE TABLE vkpi_project_kol_assignments (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES vkpi_projects(id),
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id),
    stage TEXT NOT NULL,
    stage_status TEXT NOT NULL,
    assigned_staff_id BIGINT,
    source TEXT NOT NULL DEFAULT '',
    source_ref TEXT NOT NULL DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, kol_pool_id)
);
CREATE TABLE vkpi_kol_pool_touches (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL,
    staff_id BIGINT,
    channel TEXT NOT NULL,
    project_id BIGINT,
    note TEXT NOT NULL DEFAULT '',
    touched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (kol_pool_id, channel, project_id)
);
CREATE TABLE vkpi_employee_channels (
    id BIGSERIAL PRIMARY KEY,
    staff_id BIGINT NOT NULL,
    platform TEXT NOT NULL,
    account_handle TEXT NOT NULL,
    account_display_name TEXT NOT NULL DEFAULT '',
    account_url TEXT NOT NULL DEFAULT '',
    avatar_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    last_sync_at TIMESTAMPTZ,
    last_sync_status TEXT NOT NULL DEFAULT '',
    last_sync_error TEXT NOT NULL DEFAULT '',
    deleted_at TIMESTAMPTZ
);
CREATE TABLE vkpi_channel_metrics (
    id BIGSERIAL PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    snapshot_date DATE NOT NULL,
    followers BIGINT,
    posts_count BIGINT,
    total_views BIGINT,
    total_likes BIGINT,
    total_comments BIGINT,
    engagement_rate DOUBLE PRECISION,
    followers_delta BIGINT,
    posts_delta BIGINT,
    views_delta_24h BIGINT,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _dict_row(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def test_real_postgres_favorite_to_my_kol_to_project_readback(
    pg_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the real persistence chain in a random disposable PG schema."""
    import psycopg
    from psycopg import sql

    from app.db.connection import PostgresCompatConnection
    from app.domains.access import scope
    from app.domains.kol import my_kol_aggregate, pool_favorites
    from app.domains.memory import agent_memory_writer
    from app.domains.projects import workflow_projects_kols

    schema = f"vkpi_kol_flow_{uuid.uuid4().hex}"
    admin = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5)
    raw = None
    compat = None
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT current_database()")
            database_name = str((cur.fetchone() or [""])[0] or "")
            assert re.search(
                r"(?:^|[_-])(test|tests|ci|integration|disposable|scratch)(?:[_-]|$)",
                database_name,
                re.I,
            ), f"refusing non-disposable database {database_name!r}"
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

        raw = psycopg.connect(pg_dsn, connect_timeout=5)
        raw.autocommit = False
        with raw.cursor() as cur:
            cur.execute(
                sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(schema))
            )
        compat = PostgresCompatConnection(raw, pool=None)
        for statement in [part.strip() for part in _FLOW_SCHEMA.split(";") if part.strip()]:
            compat.execute(statement)
        compat.commit()

        staff_id = 701
        user_id = 1701
        compat.execute(
            "INSERT INTO users (id, email, name) VALUES (?, ?, ?)",
            (user_id, "isolated-flow@example.invalid", "Isolated Flow"),
        )
        compat.execute(
            "INSERT INTO staff (id, user_id, role, active) VALUES (?, ?, 'staff', TRUE)",
            (staff_id, user_id),
        )
        pool_row = compat.execute(
            """
            INSERT INTO vkpi_kol_pool (
                platform, handle, display_name, followers, profile_url, country
            ) VALUES ('youtube', '@isolated_creator', 'Isolated Creator', 42000,
                      'https://example.invalid/isolated_creator', 'US')
            RETURNING id
            """
        ).fetchone()
        project_row = compat.execute(
            """
            INSERT INTO vkpi_projects (
                project_name, product_sku, product_name, platform, stage, stage_status,
                assigned_staff_id, created_by_staff_id, source_type
            ) VALUES ('Isolated Integration Project', 'TEST-26', 'Test Lens', 'youtube',
                      'discovery', 'active', ?, ?, 'smart_search')
            RETURNING id
            """,
            (staff_id, staff_id),
        ).fetchone()
        compat.commit()
        kol_pool_id = int(_dict_row(pool_row)["id"])
        project_id = int(_dict_row(project_row)["id"])
        actor = {"id": staff_id, "user_id": user_id, "role": "staff", "is_owner": 0}

        monkeypatch.setattr(pool_favorites, "get_conn", lambda: compat)
        monkeypatch.setattr(workflow_projects_kols, "get_conn", lambda: compat)
        monkeypatch.setattr(workflow_projects_kols, "ensure_vkpi_schema", lambda: None)
        monkeypatch.setattr(workflow_projects_kols, "_log_project_audit", lambda **_kwargs: None)
        monkeypatch.setattr(scope, "get_conn", lambda: compat)
        monkeypatch.setattr(agent_memory_writer, "record_kol_signal", lambda *_args, **_kwargs: {})

        favorite = pool_favorites.add_favorite(
            kol_pool_id,
            staff=actor,
            note="isolated integration favorite",
        )
        assert favorite["status"] == "favorited"

        before = my_kol_aggregate.build_my_kol_aggregate(
            compat,
            staff_id,
            actor=actor,
        )
        assert before["kpi_summary"]["favorites_count"] == 1
        assert before["kpi_summary"]["in_project_count"] == 0
        assert before["pool_favorites"][0]["kol_pool_id"] == kol_pool_id
        assert before["pool_favorites"][0]["projects"] == []

        attached = workflow_projects_kols.add_project_kols(
            project_id,
            {"kol_pool_ids": [kol_pool_id]},
            staff=actor,
        )
        assert attached["inserted"] == 1
        assert attached["missing_kol_pool_ids"] == []

        after = my_kol_aggregate.build_my_kol_aggregate(
            compat,
            staff_id,
            actor=actor,
        )
        assert after["kpi_summary"]["favorites_count"] == 1
        assert after["kpi_summary"]["in_project_count"] == 1
        favorite_projects = after["pool_favorites"][0]["projects"]
        assert favorite_projects == [
            {
                "project_id": project_id,
                "project_name": "Isolated Integration Project",
                "stage": "discovered",
                "stage_status": "active",
            }
        ]

        assignment = compat.execute(
            """
            SELECT project_id, kol_pool_id, stage, stage_status, assigned_staff_id,
                   source, source_ref, metadata_json
            FROM vkpi_project_kol_assignments
            WHERE project_id=? AND kol_pool_id=?
            """,
            (project_id, kol_pool_id),
        ).fetchone()
        assignment_data = _dict_row(assignment)
        assert assignment_data["project_id"] == project_id
        assert assignment_data["kol_pool_id"] == kol_pool_id
        assert assignment_data["stage"] == "discovered"
        assert assignment_data["stage_status"] == "active"
        assert assignment_data["assigned_staff_id"] == staff_id

        favorite_count = compat.execute(
            "SELECT COUNT(*) AS n FROM vkpi_kol_pool_favorites WHERE staff_id=? AND kol_pool_id=?",
            (staff_id, kol_pool_id),
        ).fetchone()
        assert int(_dict_row(favorite_count)["n"]) == 1
    finally:
        if compat is not None:
            compat.close()
        elif raw is not None:
            raw.close()
        try:
            with admin.cursor() as cur:
                cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        finally:
            admin.close()

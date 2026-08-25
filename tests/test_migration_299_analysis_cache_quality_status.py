from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from app.db import connection


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations" / "299_vkpi_analysis_cache_quality_incomplete.sql"
DOWN = ROOT / "migrations" / "299_vkpi_analysis_cache_quality_incomplete_down.sql"


def test_migration_299_expands_only_the_cache_status_contract() -> None:
    up = UP.read_text(encoding="utf-8")
    down = DOWN.read_text(encoding="utf-8")
    compact_up = " ".join(up.split())

    assert "quality_incomplete" in up
    assert "CHECK (status IN ('ready', 'stale', 'quality_incomplete'))" in up
    assert "chk_vkpi_analysis_cache_quality_namespace" in up
    assert (
        "CHECK ( status <> 'quality_incomplete' "
        "OR target_type = 'video_quality_triage' ) NOT VALID"
    ) in compact_up
    assert "UPDATE vkpi_analysis_cache AS source" in up
    assert "__quality_migrated_" in up
    assert "cannot preserve a conflicting paid quality record safely" in up
    assert "DELETE FROM vkpi_analysis_cache" not in up
    assert "BEGIN;" not in up.upper() and "COMMIT;" not in up.upper()

    assert "SET status='stale'" in down
    assert "WHERE status='quality_incomplete'" in down
    assert down.index("SET status='stale'") < down.index(
        "DROP CONSTRAINT IF EXISTS chk_vkpi_analysis_cache_quality_namespace"
    )
    assert "DROP CONSTRAINT IF EXISTS chk_vkpi_analysis_cache_quality_namespace" in down
    assert "CHECK (status IN ('ready', 'stale'))" in down
    assert "DELETE FROM vkpi_analysis_cache" not in down
    assert "299_vkpi_analysis_cache_quality_incomplete.sql" in down
    assert "video_quality_triage" in up
    for text in (up, down):
        assert "?" not in text
        assert "%" not in text


def test_migration_299_is_discovered_forward_only() -> None:
    assert UP.name in connection._POSTGRES_MIGRATION_SEQUENCE
    assert DOWN.name not in connection._POSTGRES_MIGRATION_SEQUENCE


@pytest.mark.pg
def test_migration_299_up_down_up_on_real_postgres(pg_dsn: str) -> None:
    import psycopg
    from psycopg import sql

    schema = f"vkpi_analysis_quality_{uuid.uuid4().hex}"
    up = UP.read_text(encoding="utf-8")
    down = DOWN.read_text(encoding="utf-8")
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(
                sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
            )
            conn.execute(
                """
                CREATE TABLE schema_migrations (version_key TEXT PRIMARY KEY);
                INSERT INTO schema_migrations
                VALUES ('299_vkpi_analysis_cache_quality_incomplete.sql');
                CREATE TABLE vkpi_analysis_cache (
                  id BIGSERIAL PRIMARY KEY,
                  target_type TEXT NOT NULL,
                  target_id TEXT NOT NULL,
                  derive_method TEXT NOT NULL,
                  model TEXT,
                  result JSONB,
                  cost NUMERIC,
                  status TEXT NOT NULL DEFAULT 'ready',
                  triggered_by_user_id BIGINT,
                  CONSTRAINT chk_vkpi_analysis_cache_status
                    CHECK (status IN ('ready', 'stale', 'quality_incomplete')),
                  CONSTRAINT uq_vkpi_analysis_cache_target_method
                    UNIQUE (target_type, target_id, derive_method)
                );
                INSERT INTO vkpi_analysis_cache
                  (target_type,target_id,derive_method,model,result,cost,status)
                VALUES
                  ('video','ready-video','video_analysis_final_v1','legacy',
                   '{"paid":"ready"}',0.10,'ready'),
                  ('video','legacy-alone','video_analysis_final_v1','gemini',
                   '{"paid":"legacy-alone"}',1.25,'quality_incomplete'),
                  ('video_quality_triage','collision','video_analysis_final_v1','gemini',
                   '{"paid":"isolated-existing"}',2.50,'quality_incomplete'),
                  ('video','collision','video_analysis_final_v1','gemini',
                   '{"paid":"legacy-conflict"}',3.75,'quality_incomplete');
                """
            )
            conn.execute(up)

            constraint_defs = dict(
                conn.execute(
                    "SELECT conname,pg_get_constraintdef(oid) "
                    "FROM pg_constraint "
                    "WHERE conrelid='vkpi_analysis_cache'::regclass"
                ).fetchall()
            )
            assert "chk_vkpi_analysis_cache_quality_namespace" in constraint_defs
            assert "status <> 'quality_incomplete'::text" in constraint_defs[
                "chk_vkpi_analysis_cache_quality_namespace"
            ]
            assert "target_type = 'video_quality_triage'::text" in constraint_defs[
                "chk_vkpi_analysis_cache_quality_namespace"
            ]

            legacy_alone = conn.execute(
                "SELECT target_type,derive_method,cost,result->>'paid',status "
                "FROM vkpi_analysis_cache WHERE target_id='legacy-alone'"
            ).fetchone()
            assert legacy_alone == (
                "video_quality_triage",
                "video_analysis_final_v1",
                pytest.approx(1.25),
                "legacy-alone",
                "quality_incomplete",
            )
            collision_rows = conn.execute(
                "SELECT id,target_type,derive_method,cost,result->>'paid',status "
                "FROM vkpi_analysis_cache WHERE target_id='collision' ORDER BY id"
            ).fetchall()
            assert len(collision_rows) == 2
            assert {row[1] for row in collision_rows} == {"video_quality_triage"}
            assert {float(row[3]) for row in collision_rows} == {2.50, 3.75}
            assert {row[4] for row in collision_rows} == {
                "isolated-existing",
                "legacy-conflict",
            }
            assert {row[5] for row in collision_rows} == {"quality_incomplete"}
            migrated_conflict = next(row for row in collision_rows if row[4] == "legacy-conflict")
            assert migrated_conflict[2] == (
                "video_analysis_final_v1__quality_migrated_" + str(migrated_conflict[0])
            )
            assert conn.execute(
                "SELECT COUNT(*) FROM vkpi_analysis_cache "
                "WHERE target_type='video' AND status='quality_incomplete'"
            ).fetchone()[0] == 0
            assert conn.execute(
                "SELECT result->>'paid' FROM vkpi_analysis_cache "
                "WHERE target_type='video' AND status='ready'"
            ).fetchall() == [("ready",)]

            conn.execute(
                "INSERT INTO vkpi_analysis_cache(target_type,target_id,derive_method,status) "
                "VALUES ('video_quality_triage','new-quality','video_analysis_final_v1','quality_incomplete')"
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO vkpi_analysis_cache(target_type,target_id,derive_method,status) "
                    "VALUES ('video','invalid-status','video_analysis_final_v1','invalid')"
                )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO vkpi_analysis_cache(target_type,target_id,derive_method,status) "
                    "VALUES ('video','legacy-quality','video_analysis_final_v1','quality_incomplete')"
                )

            paid_before_down = conn.execute(
                "SELECT id,cost,result FROM vkpi_analysis_cache "
                "WHERE result ? 'paid' ORDER BY id"
            ).fetchall()
            conn.execute(down)
            assert conn.execute(
                "SELECT COUNT(*) FROM vkpi_analysis_cache WHERE status='quality_incomplete'"
            ).fetchone()[0] == 0
            assert conn.execute(
                "SELECT id,cost,result FROM vkpi_analysis_cache "
                "WHERE result ? 'paid' ORDER BY id"
            ).fetchall() == paid_before_down
            assert conn.execute(
                "SELECT COUNT(*) FROM pg_constraint "
                "WHERE conrelid='vkpi_analysis_cache'::regclass "
                "AND conname='chk_vkpi_analysis_cache_quality_namespace'"
            ).fetchone()[0] == 0
            assert conn.execute(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE version_key='299_vkpi_analysis_cache_quality_incomplete.sql'"
            ).fetchone()[0] == 0
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO vkpi_analysis_cache(target_type,target_id,derive_method,status) "
                    "VALUES ('video_quality_triage','down-rejects','video_analysis_final_v1','quality_incomplete')"
                )

            conn.execute(up)
            conn.execute(
                "INSERT INTO vkpi_analysis_cache(target_type,target_id,derive_method,status) "
                "VALUES ('video_quality_triage','up-again','video_analysis_final_v1','quality_incomplete')"
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO vkpi_analysis_cache(target_type,target_id,derive_method,status) "
                    "VALUES ('video','up-again-legacy','video_analysis_final_v1','quality_incomplete')"
                )
            assert conn.execute(
                "SELECT COUNT(*) FROM vkpi_analysis_cache "
                "WHERE target_type='video' AND status='quality_incomplete'"
            ).fetchone()[0] == 0
        finally:
            conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )

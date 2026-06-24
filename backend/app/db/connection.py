"""
db/connection.py — pooled compat DB runtime for Viltrox 2.0

2.0 keeps the old sqlite-style repository SQL surface (`?` placeholders,
sqlite-like row access, `lastrowid`, `PRAGMA table_info`) but runs production
traffic through a pooled Postgres runtime.
"""
from __future__ import annotations

import asyncio
import atexit
import json
import os
import re
import secrets as secrets_mod
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from app.core.logging import get_logger
from app.core.config import (
    DB_RUNTIME_URL,
    DB_BUSY_TIMEOUT_MS,
    DB_CACHE_SIZE_KB,
    DB_MMAP_SIZE_MB,
    DB_PATH,
    DB_RUNTIME_BACKEND,
    DB_USE_PGBOUNCER,
    DB_WAL_AUTOCHECKPOINT,
    IS_PRODUCTION,
    POSTGRES_POOL_MAX_SIZE,
    POSTGRES_POOL_MIN_SIZE,
    POSTGRES_POOL_TIMEOUT_SEC,
)
from app.db.connection_sql_translation import (
    translate_special_sql as _translate_special_sql,
    translate_sql_dialect as _translate_sql_dialect,
)


logger = get_logger(__name__)

_db_local = threading.local()
_scoped_conn: ContextVar[Any | None] = ContextVar("viltrox_db_scoped_conn", default=None)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_MIGRATIONS_DIR = _PROJECT_ROOT / "migrations"
_ENV_PATH = _PROJECT_ROOT / ".env"
_POSTGRES_MIGRATION_SEQUENCE = (
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
    "016_ai_usage_log.sql",
    "018_staff_permissions.sql",
    "019_kol_operations.sql",
    "020_kol_candidates.sql",
    "021_activities.sql",
    "022_kol_account_dossier.sql",
    "023_vkpi_core.sql",
    "024_vkpi_metric_lineage.sql",
    "025_vkpi_product_cost_catalog.sql",
    "026_vkpi_reports.sql",
    "027_vkpi_reconciliation.sql",
    "028_vkpi_audit_logs.sql",
    "029_vkpi_analytics.sql",
    "030_vkpi_employee_channels.sql",
    "031_vkpi_p5_selected.sql",
    "032_kol_contact_profile.sql",
    "033_vkpi_daily_outreach_digest.sql",
    "034_vkpi_shopify_order_snapshots.sql",
    "035_vkpi_project_evidence_assets.sql",
    "036_vkpi_cost_lifecycle_audit.sql",
    "037_vkpi_product_launches.sql",
    "038_vkpi_market_scan.sql",
    "039_vkpi_kol_pool.sql",
    "040_vkpi_kol_recommendations.sql",
    "041_vkpi_industry_projects.sql",
    "042_vkpi_industry_accounts.sql",
    "043_vkpi_industry_snapshots.sql",
    "044_vkpi_industry_posts.sql",
    "045_vkpi_automation_outcomes.sql",
    "046_vkpi_settings.sql",
    "047_vkpi_user_preferences.sql",
    "048_vkpi_notification_settings.sql",
    "055_vkpi_industry_post_media.sql",
    "056_vkpi_job_runtime_extensions.sql",
    "057_vkpi_ai_cost_budget.sql",
    "058_vkpi_legacy_import.sql",
    "058a_vkpi_legacy_import_launch_plan.sql",
    "058b_vkpi_legacy_import_dedupe.sql",
    "058c_vkpi_legacy_import_batch_column_compat.sql",
    "058d_vkpi_legacy_official_materials.sql",
    "058e_vkpi_legacy_entity_resolution.sql",
    "058f_vkpi_legacy_kol_entities_decisions.sql",
    "058g_vkpi_legacy_commit_attempts.sql",
    "059_vkpi_memory_tables.sql",
    "060_vkpi_budget_caps_defaults.sql",
    "061_vkpi_llm_budget_scopes.sql",
    "062_vkpi_content_brain_fields.sql",
    "063_vkpi_content_brain_budget_scope.sql",
    "064_vkpi_competitor_signals.sql",
    "065_vkpi_perf_indexes.sql",
    "066_vkpi_media_cache_assets.sql",
    "067_vkpi_brand_signal.sql",
    "068_vkpi_profile_deep_dimensions11.sql",
    "069_vkpi_competitor_relation.sql",
    "070_vkpi_kol_profile_deep_base.sql",
    "071_vkpi_product_catalog_official_specs.sql",
    "072_vkpi_product_catalog_widen_legacy_columns.sql",
    "073_vkpi_channel_post_metrics.sql",
    "074_vkpi_sync_runs.sql",
    "075_vkpi_sync_acknowledgements.sql",
    "076_vkpi_kol_refresh_tier.sql",
    "077_vkpi_llm_gateway_hard_caps.sql",
    "078_vkpi_gemini_single_kol_budget.sql",
    "079_vkpi_product_aliases.sql",
    "080_vkpi_product_spec_facts.sql",
    "082_vkpi_market_provider_smoke_budget.sql",
    "083_dashboard_kol_account_picker.sql",
    "084_vkpi_project_kol_assignments.sql",
    "085_vkpi_kol_video_evidence.sql",
    "086_vkpi_kol_pool_video_summary.sql",
    "087_evidence_type.sql",
    "088_fix_needs_scrape_hygiene.sql",
    "089_evidence_metadata_fields.sql",
    "090_dashboard_evidence_active.sql",
    "091_evidence_metrics_refresh_fields.sql",
    "092_evidence_publish_derive_method.sql",
    "093_project_follow_status.sql",
    "094_vkpi_project_stars.sql",
    "095_apify_jobs.sql",
    "096_vkpi_analysis_cache.sql",
    "097_apify_jobs_llm_guardrails.sql",
    "098_vkpi_project_contracts.sql",
    "099_vkpi_kol_profile_index.sql",
    "100_vkpi_kol_profile_type.sql",
    "101_vkpi_kol_profile_recall_status.sql",
    "102_vkpi_kol_url_deep_crawl.sql",
    "103_vkpi_kol_search_sessions.sql",
    "104_vkpi_evidence_source_width.sql",
    "105_apify_jobs_provider_retry.sql",
    "106_vkpi_project_retrospective_budget.sql",
    "107_vkpi_kol_pool_favorites.sql",
    "108_vkpi_kol_pool_video_cursor.sql",
    "109_vkpi_kol_pool_duplicate_of.sql",
    "110_vkpi_projects_restricted.sql",
    "111_vkpi_kol_pool_real_er_shadow.sql",
    "112_apify_jobs_started_at.sql",
    "113_vkpi_kol_pool_suspect_inflation.sql",
    "114_vkpi_kol_pool_touches.sql",
    "115_vkpi_kol_pool_contact_provenance.sql",
    "116_vkpi_pii_export_ledger.sql",
    "117_vkpi_dsar_requests.sql",
    "118_vkpi_kol_pool_contact_audit.sql",
    "119_vkpi_product_persona.sql",
    "120_vkpi_kol_content_fit_cache_index.sql",
    "121_apify_jobs_kol_content_fit_index.sql",
    "122_vkpi_events.sql",
    "123_vkpi_staff_groups.sql",
    "124_perf_indexes.sql",
    "125_vkpi_channel_metrics_filled.sql",
    "126_vkpi_fulfillment_observation_tasks.sql",
    "127_vkpi_data_freshness.sql",
    "128_vkpi_project_content_observation_windows.sql",
    "129_vkpi_project_content_posts.sql",
    "130_vkpi_scheduler_tasks.sql",
    "131_vkpi_project_members.sql",
    "132_vkpi_event_members.sql",
    "133_vkpi_is_public.sql",
    "134_vkpi_single_call_budget_reseed.sql",
    "135_vkpi_inventory.sql",
    "136_vkpi_inventory_movements.sql",
    "137_vkpi_share_audit.sql",
    "138_vkpi_shopify_orders.sql",
    "139_vkpi_isolation_perf_indexes.sql",
    "140_vkpi_worker_heartbeat.sql",
    "141_vkpi_action_inbox.sql",
    "142_vkpi_project_automation_audit.sql",
    "143_vkpi_kol_memory.sql",
    "144_vkpi_dealers.sql",
    "145_vkpi_shopify_credentials.sql",
    "146_vkpi_api_key_pool.sql",
    "147_vkpi_kol_portal_tokens.sql",
    "148_vkpi_kol_fit_snapshot.sql",
    "149_vkpi_brief_agent_schedule.sql",
    "150_vkpi_ai_today_hot.sql",
    "151_vkpi_market_signal_refresh.sql",
    "152_vkpi_competitor_radar.sql",
    "153_vkpi_report_analysis.sql",
    "154_vkpi_inventory_groups.sql",
    "155_users_last_seen.sql",
    "156_apify_jobs_claim_priority_index.sql",
    "157_vkpi_official_account_daily_report.sql",
    "158_vkpi_official_post_visual.sql",
    "159_vkpi_kol_pool_members.sql",
    "160_vkpi_project_members_group_origin.sql",
    "161_vkpi_kol_pool_members_group_origin.sql",
    "162_vkpi_goaffpro_credentials.sql",
    "163_vkpi_goaffpro_kol_links.sql",
    "164_vkpi_goaffpro_kol_metrics.sql",
    "165_vkpi_events_product.sql",
    "166_vkpi_llm_batches.sql",
    "167_scheduler_task_voc.sql",
    "168_scheduler_task_market_intelligence.sql",
    "169_vkpi_event_materials_products.sql",
    "170_apify_jobs_lease_hardening.sql",
    "171_vkpi_kol_cooperation.sql",
    "172_scheduler_task_content_fit_batch.sql",
    "173_vkpi_publish_approvals.sql",
    "174_vkpi_collab_settings.sql",
    "175_enable_daily_action_inbox_generate.sql",
    "176_vkpi_search_session_approved.sql",
    "177_scheduler_task_fulfillment_due_scan.sql",
    "178_job_execution_ledger_audit_trail.sql",
    "179_vkpi_action_inbox_decision_fields.sql",
    "180_vkpi_agent_orchestration.sql",
    "181_vkpi_action_inbox_verification.sql",
)

try:
    import psycopg
except Exception:
    psycopg = None

try:
    from psycopg_pool import ConnectionPool
except Exception:
    ConnectionPool = None

_PG_POOL: Any | None = None
_POOL_LOCK = threading.Lock()


class _ScopedConnectionHandle:
    """Mutable request-scope holder that survives sync endpoint thread hops."""

    __slots__ = ("conn",)

    def __init__(self) -> None:
        self.conn: Any | None = None

    def get(self) -> Any:
        if self.conn is None:
            self.conn = _build_postgres_conn() if is_postgres_runtime() else _build_sqlite_conn()
        return self.conn

    def close(self) -> None:
        if self.conn is None:
            return
        try:
            self.conn.close()
        finally:
            self.conn = None


class CompatRow:
    """A sqlite3.Row-like object that works for psycopg result rows."""

    __slots__ = ("_columns", "_values", "_mapping")

    def __init__(self, columns: Sequence[str], values: Sequence[Any]) -> None:
        self._columns = tuple(str(col) for col in columns)
        self._values = tuple(values)
        self._mapping = {self._columns[idx]: self._values[idx] for idx in range(len(self._columns))}

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> list[str]:
        return list(self._columns)

    def items(self) -> list[tuple[str, Any]]:
        return list(self._mapping.items())

    def values(self) -> list[Any]:
        return list(self._values)

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping.get(key, default)

    def __repr__(self) -> str:
        return f"CompatRow({self._mapping!r})"


def _column_name(col: Any) -> str:
    return str(getattr(col, "name", col[0] if isinstance(col, (tuple, list)) and col else col))


def _normalize_pg_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value



def is_postgres_runtime() -> bool:
    return DB_RUNTIME_BACKEND == "postgres" and bool(DB_RUNTIME_URL) and psycopg is not None and ConnectionPool is not None


def _get_pg_pool():
    global _PG_POOL
    if not is_postgres_runtime():
        return None
    if _PG_POOL is None:
        with _POOL_LOCK:
            if _PG_POOL is None:
                _PG_POOL = ConnectionPool(
                    conninfo=DB_RUNTIME_URL,
                    min_size=POSTGRES_POOL_MIN_SIZE,
                    max_size=POSTGRES_POOL_MAX_SIZE,
                    timeout=float(POSTGRES_POOL_TIMEOUT_SEC),
                    open=False,
                    kwargs={
                        "connect_timeout": 5,
                        # PgBouncer transaction pooling is incompatible with server-side prepared statements.
                        "prepare_threshold": None if DB_USE_PGBOUNCER else 5,
                    },
                    name="viltrox-2.0",
                )
                _PG_POOL.open(wait=True, timeout=float(POSTGRES_POOL_TIMEOUT_SEC))
    return _PG_POOL


class PostgresCompatCursor:
    def __init__(self, raw_cursor: Any) -> None:
        self._cursor = raw_cursor
        self._prefetched_rows: list[Sequence[Any]] = []
        self.lastrowid: int | None = None

    def execute(self, sql: str, params: Sequence[Any] | None = None):
        translated_sql, translated_params = _translate_special_sql(sql, params or ())
        try:
            self._cursor.execute(_translate_sql_dialect(translated_sql), list(translated_params or ()))
            if self._cursor.description and re.search(r"\bRETURNING\b", translated_sql, flags=re.IGNORECASE):
                row = self._cursor.fetchone()
                if row is not None:
                    self._prefetched_rows.append(row)
                    try:
                        self.lastrowid = int(row[0])
                    except (TypeError, ValueError) as exc:
                        logger.debug("postgres compat lastrowid unavailable: %s", exc)
                        self.lastrowid = None
        except Exception:
            try:
                self._cursor.connection.rollback()
            except Exception as rollback_exc:
                logger.debug("postgres compat rollback after execute failure skipped: %s", rollback_exc)
            raise
        return self

    @property
    def description(self):
        desc = self._cursor.description or []
        return [(_column_name(col), None, None, None, None, None, None) for col in desc]

    @property
    def rowcount(self) -> int:
        return int(getattr(self._cursor, "rowcount", 0) or 0)

    def _columns(self) -> list[str]:
        return [_column_name(col) for col in (self._cursor.description or [])]

    def fetchone(self):
        if self._prefetched_rows:
            row = self._prefetched_rows.pop(0)
        else:
            row = self._cursor.fetchone()
        if row is None:
            return None
        return CompatRow(self._columns(), [_normalize_pg_value(value) for value in row])

    def fetchall(self):
        rows = list(self._prefetched_rows)
        self._prefetched_rows.clear()
        fetched = self._cursor.fetchall()
        if fetched:
            rows.extend(fetched)
        if not rows:
            return []
        return [CompatRow(self._columns(), [_normalize_pg_value(value) for value in row]) for row in rows]

    def close(self) -> None:
        self._cursor.close()



class PostgresCompatConnection:
    """A minimal sqlite-compatible wrapper over psycopg connections."""

    def __init__(self, raw_conn: Any, pool: Any | None = None) -> None:
        self._raw = raw_conn
        self._pool = pool
        self._closed = False
        self.row_factory = sqlite3.Row

    def cursor(self) -> PostgresCompatCursor:
        return PostgresCompatCursor(self._raw.cursor())

    def execute(self, sql: str, params: Sequence[Any] | None = None):
        cur = self.cursor()
        return cur.execute(sql, params)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._raw.rollback()
        except Exception as exc:
            logger.debug("postgres compat close rollback skipped: %s", exc)
        if self._pool is not None:
            self._pool.putconn(self._raw)
        else:
            self._raw.close()


def _build_sqlite_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(DB_PATH),
        check_same_thread=False,
        timeout=max(5, DB_BUSY_TIMEOUT_MS / 1000),
        cached_statements=512,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute(f"PRAGMA cache_size={-abs(DB_CACHE_SIZE_KB)}")
    conn.execute(f"PRAGMA mmap_size={max(0, DB_MMAP_SIZE_MB) * 1024 * 1024}")
    conn.execute(f"PRAGMA wal_autocheckpoint={max(100, DB_WAL_AUTOCHECKPOINT)}")
    return conn


def _build_postgres_conn() -> PostgresCompatConnection:
    pool = _get_pg_pool()
    if pool is None:
        raise RuntimeError("Postgres runtime requested but psycopg/psycopg_pool is unavailable")
    return PostgresCompatConnection(pool.getconn(), pool=pool)


def open_standalone_conn() -> Any:
    """Return a dedicated connection for background work that must manage its own lifecycle."""
    return _build_postgres_conn() if is_postgres_runtime() else _build_sqlite_conn()


def close_standalone_conn(conn: Any) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception as exc:
        logger.debug("standalone connection close skipped: %s", exc)
    scoped = _scoped_conn.get()
    if isinstance(scoped, _ScopedConnectionHandle) and scoped.conn is conn:
        scoped.conn = None
    elif scoped is conn:
        _scoped_conn.set(None)
    if getattr(_db_local, "conn", None) is conn:
        _db_local.conn = None


def _bootstrap_sqlite_runtime() -> None:
    from app.db import migrations as sqlite_migrations

    sqlite_migrations.init_db()


def _run_runtime_seeders() -> None:
    from app.db.repositories.users import backfill_user_social_verified_flags
    from app.services.runtime_seed import ensure_runtime_seed_data

    try:
        ensure_runtime_seed_data()
        backfill_user_social_verified_flags()
    finally:
        local_conn = getattr(_db_local, "conn", None)
        if local_conn is not None:
            try:
                local_conn.close()
            except Exception as exc:
                logger.debug("runtime seeder local connection close skipped: %s", exc)
            _db_local.conn = None


async def init_db_runtime() -> None:
    if not is_postgres_runtime():
        await asyncio.to_thread(_bootstrap_sqlite_runtime)
        await asyncio.to_thread(_run_runtime_seeders)
        return
    _get_pg_pool()
    await asyncio.to_thread(_run_postgres_migrations)
    await asyncio.to_thread(_bootstrap_default_admin)
    await asyncio.to_thread(_run_runtime_seeders)


def close_db_runtime_sync() -> None:
    global _PG_POOL
    scoped = _scoped_conn.get()
    if isinstance(scoped, _ScopedConnectionHandle):
        try:
            scoped.close()
        except Exception as exc:
            logger.debug("scoped db connection close skipped: %s", exc)
        _scoped_conn.set(None)
    elif scoped is not None:
        try:
            scoped.close()
        except Exception as exc:
            logger.debug("scoped raw db connection close skipped: %s", exc)
        _scoped_conn.set(None)
    local_conn = getattr(_db_local, "conn", None)
    if local_conn is not None:
        try:
            local_conn.close()
        except Exception as exc:
            logger.debug("thread-local db connection close skipped: %s", exc)
        _db_local.conn = None
    if _PG_POOL is not None:
        _PG_POOL.close()
        _PG_POOL = None


async def close_db_runtime() -> None:
    close_db_runtime_sync()


atexit.register(close_db_runtime_sync)


def _run_postgres_migrations() -> None:
    pool = _get_pg_pool()
    if pool is None:
        return
    with pool.connection(timeout=float(POSTGRES_POOL_TIMEOUT_SEC)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version_key TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('viltrox_schema_migrations'))")
            cur.execute("SELECT version_key FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}
            for migration_name in _POSTGRES_MIGRATION_SEQUENCE:
                migration = _MIGRATIONS_DIR / migration_name
                if not migration.exists():
                    raise RuntimeError(f"Required Postgres migration is missing: {migration_name}")
                if migration.name in applied:
                    continue
                cur.execute(migration.read_text())
                cur.execute(
                    "INSERT INTO schema_migrations(version_key) VALUES (%s) ON CONFLICT DO NOTHING",
                    (migration.name,),
                )
        conn.commit()


def _read_env_override(key: str) -> str:
    if not _ENV_PATH.exists():
        return ""
    try:
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    except Exception:
        return ""
    return ""


def _bootstrap_default_admin() -> None:
    from app.core.security import hash_password

    pool = _get_pg_pool()
    if pool is None:
        return
    with pool.connection(timeout=float(POSTGRES_POOL_TIMEOUT_SEC)) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE email = %s LIMIT 1", ("admin@viltrox.com",))
            admin_exists = cur.fetchone() is not None
            admin_pw_plain = os.environ.get("ADMIN_PASSWORD", "").strip() or _read_env_override("ADMIN_PASSWORD")
            if not admin_pw_plain and not admin_exists:
                if IS_PRODUCTION:
                    raise RuntimeError("2.0 production bootstrap requires ADMIN_PASSWORD when admin@viltrox.com does not exist")
                admin_pw_plain = secrets_mod.token_urlsafe(16)
                logger.warning(
                    "Generated ephemeral ADMIN_PASSWORD for local Postgres bootstrap only; persist manually if needed: %s",
                    admin_pw_plain,
                )
            if not admin_pw_plain:
                cur.execute("SELECT id FROM users WHERE email = %s LIMIT 1", ("admin@viltrox.com",))
            else:
                admin_pw = hash_password(admin_pw_plain)
                cur.execute(
                    """
                    INSERT INTO users (created_at, email, password_hash, name, status, role)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (email) DO UPDATE SET
                        role = EXCLUDED.role,
                        status = EXCLUDED.status
                    RETURNING id
                    """,
                    (
                        datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "admin@viltrox.com",
                        admin_pw,
                        "Admin",
                        "approved",
                        "admin",
                    ),
                )
            admin_row = cur.fetchone()
            if admin_row:
                admin_user_id = int(admin_row[0])
                cur.execute("SELECT id FROM staff WHERE user_id = %s LIMIT 1", (admin_user_id,))
                if cur.fetchone() is None:
                    cur.execute(
                        """
                        INSERT INTO staff (
                            user_id, role, permissions_json, mfa_enabled, active,
                            invited_by, invited_at, accepted_at, is_owner, email_domain_verified
                        ) VALUES (%s, 'admin', %s, 0, 1, %s, now(), now(), 1, 1)
                        """,
                        (admin_user_id, json.dumps({"vkpi": "write"}), admin_user_id),
                    )
        conn.commit()


@asynccontextmanager
async def db_connection_scope() -> Iterator[Any]:
    if is_postgres_runtime():
        handle = _ScopedConnectionHandle()
        token = _scoped_conn.set(handle)
        try:
            yield None
        finally:
            try:
                handle.close()
            finally:
                _scoped_conn.reset(token)
        return
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = _build_sqlite_conn()
        _db_local.conn = conn
    yield conn


@contextmanager
def db_connection_sync_scope() -> Iterator[Any]:
    if is_postgres_runtime():
        handle = _ScopedConnectionHandle()
        token = _scoped_conn.set(handle)
        try:
            yield None
        finally:
            try:
                handle.close()
            finally:
                _scoped_conn.reset(token)
        return
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = _build_sqlite_conn()
        _db_local.conn = conn
    yield conn


def get_conn() -> Any:
    """Return a scoped DB connection, preferring a request/task-scoped Postgres handle."""
    scoped = _scoped_conn.get()
    if isinstance(scoped, _ScopedConnectionHandle):
        return scoped.get()
    if scoped is not None:
        return scoped
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = _build_postgres_conn() if is_postgres_runtime() else _build_sqlite_conn()
        _db_local.conn = conn
    return conn


def table_exists(table_name: str) -> bool:
    conn = get_conn()
    if is_postgres_runtime():
        row = conn.execute(
            "SELECT to_regclass(current_schema() || '.' || ?) AS regclass",
            (table_name,),
        ).fetchone()
        return bool(row and row["regclass"])
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


async def start_db_actor() -> None:
    if is_postgres_runtime():
        await init_db_runtime()
        logger.info("Postgres pooled runtime initialized")
        return
    backend = "sqlite"
    logger.info("Single-writer DB actor started (%s)", backend)


async def stop_db_actor() -> None:
    if is_postgres_runtime():
        await close_db_runtime()
        logger.info("Postgres pooled runtime closed")
        return
    conn = getattr(_db_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception as exc:
            logger.debug("db actor connection close skipped: %s", exc)
        _db_local.conn = None
    logger.info("DB actor stopped")


async def db_read(fn: Callable[[], Any]) -> Any:
    async with db_connection_scope():
        return await asyncio.to_thread(fn)


async def db_write(fn: Callable[[], Any]) -> Any:
    async with db_connection_scope():
        return await asyncio.to_thread(fn)


def get_db_actor_stats() -> dict[str, Any]:
    if is_postgres_runtime():
        pool = _PG_POOL
        stats = pool.get_stats() if pool is not None else {}
        return {
            "mode": "postgres_pool",
            "running": pool is not None,
            "runtime_backend": "postgres",
            "pool": stats,
        }
    return {
        "mode": "sqlite_local",
        "running": True,
        "runtime_backend": "sqlite",
    }


def probe_postgres_connectivity() -> dict[str, Any]:
    result = {
        "configured": bool(DB_RUNTIME_URL),
        "driver_available": psycopg is not None and ConnectionPool is not None,
        "reachable": False,
        "runtime_selected": DB_RUNTIME_BACKEND == "postgres",
        "pool_open": _PG_POOL is not None,
        "pooler_enabled": bool(DB_USE_PGBOUNCER),
        "using_pool_url": bool(DB_USE_PGBOUNCER and DB_RUNTIME_URL),
    }
    if not DB_RUNTIME_URL or psycopg is None:
        return result
    try:
        if _PG_POOL is not None:
            with _PG_POOL.connection(timeout=float(POSTGRES_POOL_TIMEOUT_SEC)) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            result["reachable"] = True
            result["ok"] = True
            result["pool_stats"] = _PG_POOL.get_stats()
            return result
        with psycopg.connect(DB_RUNTIME_URL, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        result["reachable"] = True
        result["ok"] = True
        return result
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)[:240]
        return result


__all__ = [
    "CompatRow",
    "db_connection_scope",
    "db_connection_sync_scope",
    "db_read",
    "db_write",
    "get_conn",
    "get_db_actor_stats",
    "init_db_runtime",
    "close_db_runtime",
    "close_db_runtime_sync",
    "is_postgres_runtime",
    "probe_postgres_connectivity",
    "start_db_actor",
    "stop_db_actor",
    "table_exists",
]

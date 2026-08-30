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
    APP_ROLE,
    DB_RUNTIME_URL,
    DB_BUSY_TIMEOUT_MS,
    DB_CACHE_SIZE_KB,
    DB_MMAP_SIZE_MB,
    DB_PATH,
    DB_RUNTIME_BACKEND,
    DB_USE_PGBOUNCER,
    DB_WAL_AUTOCHECKPOINT,
    IS_PRODUCTION,
    MIGRATION_RUNNER_APP_ROLE,
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
_PRODUCTION_SQLITE_PATH = (_PROJECT_ROOT / "submissions.db").resolve()
# 迁移序列从 migrations/ 目录按文件名排序推导（不再手写 tuple），这样新增的
# NNN_*.sql 会自动登记，根治「手动 apply 了文件却忘记加进 manifest → 全新库缺表」
# 的反复漂移（例如 199_vkpi_skill_runs 就踩过这坑）。排除两类：
#   *_down.sql            —— 回滚伴随脚本，从不属于正向迁移路径。
#   _MIGRATION_EXCLUDE    —— 被 003_postgres_baseline 取代的前基线脚本(001/002/004)
#                            与 010 的回滚脚本；显式排除以保持与历史序列完全一致。
# 文件名字典序 == 3 位数字前缀的数值序（058a/b/... 亦紧跟 058），故 sorted()
# 顺序与原手写 tuple 逐项一致（已进程内比对，差集为空）。
_MIGRATION_EXCLUDE = frozenset(
    {
        "001_verification.sql",
        "002_intelligence.sql",
        "004_viltrox_matrix.sql",
        "010_party_layer_rollback.sql",
    }
)

# Migration 234 is the first release that is expected to run under the
# transaction and fleet-wide advisory lock owned by ``_run_postgres_migrations``.
# A forward file that commits independently would release that lock while a
# second Gunicorn process is waiting, exposing partially recorded schema state.
_RUNNER_OWNED_TRANSACTION_MIN_VERSION = 234
_FORWARD_TRANSACTION_CONTROL_RE = re.compile(
    r"(?mi)^\s*(?:BEGIN(?:\s+TRANSACTION)?|COMMIT(?:\s+TRANSACTION)?)\s*;"
)


def _validate_runner_owned_transactions(names: Sequence[str]) -> None:
    for name in names:
        match = re.match(r"^(\d{3})", name)
        if match is None or int(match.group(1)) < _RUNNER_OWNED_TRANSACTION_MIN_VERSION:
            continue
        path = _MIGRATIONS_DIR / name
        if _FORWARD_TRANSACTION_CONTROL_RE.search(path.read_text(encoding="utf-8")):
            raise RuntimeError(
                "Forward migration contains transaction control owned by the "
                f"Postgres migration runner: {name}"
            )


def _discover_postgres_migrations() -> tuple[str, ...]:
    names = sorted(
        path.name
        for path in _MIGRATIONS_DIR.glob("*.sql")
        if not path.name.endswith("_down.sql") and path.name not in _MIGRATION_EXCLUDE
    )
    _validate_runner_owned_transactions(names)
    return tuple(names)


_POSTGRES_MIGRATION_SEQUENCE = _discover_postgres_migrations()

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

_DB_STARTUP_MODE_ENV = "VKPI_DB_STARTUP_MODE"
_DB_STARTUP_MODE_FULL = "full"
_DB_STARTUP_MODE_MIGRATIONS_ONLY = "migrations-only"
_VALID_DB_STARTUP_MODES = frozenset(
    {
        _DB_STARTUP_MODE_FULL,
        _DB_STARTUP_MODE_MIGRATIONS_ONLY,
    }
)
_DB_STARTUP_STATUS_LOCK = threading.Lock()
_DB_STARTUP_STATUS: dict[str, Any] = {
    "mode": "uninitialized",
    "backend": DB_RUNTIME_BACKEND,
    "state": "not_started",
    "schema_migrations": "not_started",
    "default_admin_bootstrap": "not_started",
    "runtime_seeders": "not_started",
    "non_migration_startup_writes": "not_started",
    "started_at": None,
    "completed_at": None,
    "failed_stage": None,
    "error_type": None,
}


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _resolve_db_startup_mode() -> str:
    """Return the exact startup mode, rejecting typos instead of falling back to writes."""

    mode = os.environ.get(_DB_STARTUP_MODE_ENV, _DB_STARTUP_MODE_FULL).strip().lower()
    if mode not in _VALID_DB_STARTUP_MODES:
        raise RuntimeError(
            f"Unsupported {_DB_STARTUP_MODE_ENV}={mode!r}; "
            f"expected one of {sorted(_VALID_DB_STARTUP_MODES)}"
        )
    if mode == _DB_STARTUP_MODE_MIGRATIONS_ONLY and APP_ROLE != MIGRATION_RUNNER_APP_ROLE:
        raise RuntimeError(
            f"{_DB_STARTUP_MODE_ENV}={mode!r} requires "
            f"APP_ROLE={MIGRATION_RUNNER_APP_ROLE!r}"
        )
    if APP_ROLE == MIGRATION_RUNNER_APP_ROLE and mode != _DB_STARTUP_MODE_MIGRATIONS_ONLY:
        raise RuntimeError(
            f"APP_ROLE={MIGRATION_RUNNER_APP_ROLE!r} requires "
            f"{_DB_STARTUP_MODE_ENV}={_DB_STARTUP_MODE_MIGRATIONS_ONLY!r}"
        )
    return mode


def _reset_db_startup_status(*, mode: str, backend: str) -> None:
    with _DB_STARTUP_STATUS_LOCK:
        _DB_STARTUP_STATUS.update(
            {
                "mode": mode,
                "backend": backend,
                "state": "running",
                "schema_migrations": "not_started",
                "default_admin_bootstrap": "not_started",
                "runtime_seeders": "not_started",
                "non_migration_startup_writes": "not_started",
                "started_at": _utc_timestamp(),
                "completed_at": None,
                "failed_stage": None,
                "error_type": None,
            }
        )


def _update_db_startup_status(**changes: Any) -> None:
    with _DB_STARTUP_STATUS_LOCK:
        _DB_STARTUP_STATUS.update(changes)


def get_db_startup_status() -> dict[str, Any]:
    """Return a health-safe snapshot of this process's database startup stages."""

    with _DB_STARTUP_STATUS_LOCK:
        return dict(_DB_STARTUP_STATUS)


async def _run_db_startup_stage(stage: str, fn: Callable[[], None]) -> None:
    _update_db_startup_status(**{stage: "running"})
    try:
        await asyncio.to_thread(fn)
    except Exception as exc:
        _update_db_startup_status(
            state="failed",
            failed_stage=stage,
            error_type=type(exc).__name__,
            **{stage: "failed"},
        )
        logger.exception("db.startup.stage_failed | stage=%s", stage)
        raise
    _update_db_startup_status(**{stage: "completed"})


class _ScopedConnectionHandle:
    """Mutable request-scope holder that survives sync endpoint thread hops."""

    __slots__ = ("conn", "release_validation_guard")

    def __init__(self, *, release_validation_guard: bool = False) -> None:
        self.conn: Any | None = None
        self.release_validation_guard = bool(release_validation_guard)

    def get(self) -> Any:
        if self.conn is None:
            self.conn = (
                _build_postgres_conn(
                    release_validation_guard=self.release_validation_guard,
                )
                if is_postgres_runtime()
                else _build_sqlite_conn()
            )
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
    if os.environ.get("VKPI_PYTEST_HERMETIC", "").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("Hermetic pytest runtime forbids PostgreSQL connections")
    if not is_postgres_runtime():
        return None
    # pool_selfheal(2026-07-02):deep_crawl 内核收尾会 close_db_runtime 关掉全局池,
    # 同一 worker 进程的后续 job 全灭(PoolClosed/PoolTimeout)。已关就丢弃重建,自愈。
    if _PG_POOL is not None and bool(getattr(_PG_POOL, "closed", False)):
        with _POOL_LOCK:
            if _PG_POOL is not None and bool(getattr(_PG_POOL, "closed", False)):
                _PG_POOL = None
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
        # ``cursor.description`` is invariant for a result set, but psycopg
        # materializes Column wrappers every time it is accessed.  Large GTM
        # reads used to rebuild the same projection once per row (10k+ times),
        # spending more CPU on metadata than on row conversion.  Resolve the
        # projection once without changing CompatRow values or ordering.
        columns = self._columns()
        return [CompatRow(columns, [_normalize_pg_value(value) for value in row]) for row in rows]

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
    if (
        os.environ.get("VKPI_PYTEST_HERMETIC", "").strip().lower() in {"1", "true", "yes", "on"}
        and Path(DB_PATH).resolve() == _PRODUCTION_SQLITE_PATH
    ):
        raise RuntimeError("Hermetic pytest runtime refuses repository submissions.db")
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


def _build_postgres_conn(
    *,
    release_validation_guard: bool = False,
) -> PostgresCompatConnection:
    pool = _get_pg_pool()
    if pool is None:
        raise RuntimeError("Postgres runtime requested but psycopg/psycopg_pool is unavailable")
    raw_conn = pool.getconn()
    try:
        read_only = False
        if release_validation_guard:
            from app.core.release_validation import release_validation_active

            read_only = release_validation_active()
        # Reset both states because a pooled connection can cross from fenced
        # web traffic to an activated request or a non-web runtime.
        raw_conn.read_only = bool(read_only)
    except Exception:
        pool.putconn(raw_conn)
        raise
    return PostgresCompatConnection(raw_conn, pool=pool)


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
    from app.core.passwords import hash_password

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
                cur.execute("SELECT id FROM staff WHERE user_id = %s ORDER BY id DESC LIMIT 1", (admin_user_id,))
                staff_row = cur.fetchone()
                if staff_row is None:
                    cur.execute(
                        """
                        INSERT INTO staff (
                            user_id, role, permissions_json, mfa_enabled, active,
                            invited_by, invited_at, accepted_at, is_owner, email_domain_verified
                        ) VALUES (%s, 'admin', %s, 0, 1, %s, now(), now(), 1, 1)
                        RETURNING id
                        """,
                        (admin_user_id, json.dumps({"vkpi": "write"}), admin_user_id),
                    )
                    staff_row = cur.fetchone()
                if staff_row is None:
                    raise RuntimeError("default Postgres admin staff bootstrap failed")
                admin_staff_id = int(staff_row[0])
                cur.execute(
                    """
                    UPDATE staff
                    SET role = 'admin', active = 1, is_owner = 1,
                        email_domain_verified = 1
                    WHERE id = %s
                    """,
                    (admin_staff_id,),
                )
                cur.execute(
                    """
                    INSERT INTO organization_members (organization_id, staff_id, role)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (organization_id, staff_id)
                    DO UPDATE SET role = EXCLUDED.role
                    """,
                    (1, admin_staff_id, "owner"),
                )
        conn.commit()


@asynccontextmanager
async def db_connection_scope(
    *,
    release_validation_guard: bool = False,
) -> Iterator[Any]:
    if is_postgres_runtime():
        handle = _ScopedConnectionHandle(
            release_validation_guard=release_validation_guard,
        )
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
def db_connection_sync_scope(
    *,
    release_validation_guard: bool = False,
) -> Iterator[Any]:
    if is_postgres_runtime():
        handle = _ScopedConnectionHandle(
            release_validation_guard=release_validation_guard,
        )
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
def db_connection_sync_reusing_scope() -> Iterator[Any]:
    """Reuse an active request scope, otherwise create a bounded sync scope.

    Authentication runs both inside the request-wide DB middleware and from
    standalone helpers.  Always opening a nested pool lease inside a request
    can starve a small pool during a cold-token burst: every request queues for
    its second lease while an outer lease remains checked out.  This helper
    keeps standalone callers bounded without acquiring a second connection
    when the current context already owns a request-scoped handle.
    """
    if is_postgres_runtime() and isinstance(_scoped_conn.get(), _ScopedConnectionHandle):
        yield None
        return
    with db_connection_sync_scope() as conn:
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
            "startup": get_db_startup_status(),
        }
    return {
        "mode": "sqlite_local",
        "running": True,
        "runtime_backend": "sqlite",
        "startup": get_db_startup_status(),
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
    "db_connection_sync_reusing_scope",
    "db_connection_sync_scope",
    "db_read",
    "db_write",
    "get_conn",
    "get_db_actor_stats",
    "get_db_startup_status",
    "close_db_runtime",
    "close_db_runtime_sync",
    "is_postgres_runtime",
    "probe_postgres_connectivity",
    "stop_db_actor",
    "table_exists",
]

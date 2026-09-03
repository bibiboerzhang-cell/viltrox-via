"""Migration 307: users.token_version(登录令牌服务端吊销版本号,S-02)的存储契约。

钉四条线:

* **纯结构、单列、可空无默认**。307 只给 users 加一列 INTEGER NULL,零 DML;
  NULL 等价 0(从未吊销),读端 COALESCE。保持 additive-nullable-defaultless 形态,
  发布侧 forward-compat 结构策略(atomic_release_shared._ADD_COLUMN_RE)能直接认。
* **不碰任何别的列**。可执行语句里只许出现 token_version;password_hash / status /
  role 这些认证真相列一个都不许被指名。
* **compat 占位符陷阱**。迁移全文(注释在内)禁 ASCII 问号与百分号,禁自带事务。
* **down 只注销自己**。DROP 自己的列 + 只删自己的 schema_migrations 行。

真库路(``@pytest.mark.pg``)up → up → down → up 跑一圈,证明既有 users 行逐字未动、
新列可空无默认、down 后消失。
"""
from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import pytest

from app.db import connection

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "migrations"
UP_PATH = MIGRATIONS_DIR / "307_users_token_version.sql"
DOWN_PATH = MIGRATIONS_DIR / "307_users_token_version_down.sql"
UP = UP_PATH.read_text(encoding="utf-8")
DOWN = DOWN_PATH.read_text(encoding="utf-8")

NEW_COLUMN = "token_version"
#: 认证真相列:307 的可执行语句里一个都不许出现。
PROTECTED_COLUMNS = ("password_hash", "status", "role", "email", "email_verified")

_COMMENT_RE = re.compile(r"--[^\n]*")
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
#: 与 scripts/ops/atomic_release_shared._ADD_COLUMN_RE 同形:additive-nullable-defaultless 策略的识别口径。
_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+users\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+token_version\s+INTEGER\s+NULL\s*;",
    re.IGNORECASE,
)


def _executable_sql(source: str) -> str:
    return _STRING_LITERAL_RE.sub("''", _COMMENT_RE.sub("", source))


def test_up_adds_exactly_one_nullable_defaultless_integer_column() -> None:
    assert _ADD_COLUMN_RE.search(UP), "missing the single ADD COLUMN IF NOT EXISTS token_version INTEGER NULL"
    executable = _executable_sql(UP)
    assert executable.count("ADD COLUMN") == 1, "307 must add exactly one column"
    assert "NOT NULL" not in executable, "NULL is how 'never revoked' is spelled; readers COALESCE to 0"
    assert "DEFAULT" not in executable.upper(), "a default would break the additive-nullable-defaultless policy"
    assert "CREATE INDEX" not in executable.upper(), "token_version is read by primary key only; no index"


def test_up_documents_semantics_and_single_write_point() -> None:
    assert f"COMMENT ON COLUMN users.{NEW_COLUMN} IS" in UP
    assert "token_revocation.py" in UP, "comment must name the only write point"
    assert "NULL means never revoked" in UP
    for trigger in ("改密", "登出", "踢人"):
        assert trigger in UP, f"comment must list the bump trigger: {trigger}"


def test_executable_sql_never_names_an_auth_truth_column() -> None:
    for path, source in ((UP_PATH, UP), (DOWN_PATH, DOWN)):
        executable = _executable_sql(source)
        stray = [column for column in PROTECTED_COLUMNS if re.search(rf"\b{column}\b", executable)]
        assert not stray, f"{path.name} names auth truth columns: {stray!r}"


def test_migration_writes_no_user_rows_at_all() -> None:
    for path, source in ((UP_PATH, UP), (DOWN_PATH, DOWN)):
        executable = _executable_sql(source).upper()
        assert "UPDATE " not in executable, f"{path.name} must not update rows"
        assert "INSERT INTO" not in executable, f"{path.name} must not insert rows"
        assert "DELETE FROM USERS" not in executable, f"{path.name} must not delete user rows"


def test_up_and_down_are_idempotent_by_construction() -> None:
    up_exec = _executable_sql(UP)
    assert up_exec.count("ADD COLUMN IF NOT EXISTS") == 1
    assert re.search(r"ADD COLUMN(?! IF NOT EXISTS)", up_exec) is None
    down_exec = _executable_sql(DOWN)
    assert down_exec.count("DROP COLUMN IF EXISTS") == 1
    assert re.search(r"DROP COLUMN(?! IF EXISTS)", down_exec) is None


def test_down_drops_only_its_own_column_and_deregisters_itself() -> None:
    executable = _executable_sql(DOWN)
    assert f"ALTER TABLE users DROP COLUMN IF EXISTS {NEW_COLUMN};" in DOWN
    assert executable.count("DROP COLUMN") == 1
    assert executable.count("DELETE FROM schema_migrations") == 1
    assert "307_users_token_version.sql" in DOWN
    assert "306_" not in DOWN and "305_" not in DOWN  # 不顺手注销别的迁移


def test_migration_sql_avoids_the_compat_placeholder_traps() -> None:
    for path, source in ((UP_PATH, UP), (DOWN_PATH, DOWN)):
        assert "?" not in source, f"{path.name} contains an ASCII question mark"
        assert "%" not in source, f"{path.name} contains a percent literal"
        assert "BEGIN;" not in source.upper() and "COMMIT;" not in source.upper(), (
            f"{path.name} must not own its transaction"
        )


def test_migration_307_is_discovered_forward_only() -> None:
    assert UP_PATH.name in connection._POSTGRES_MIGRATION_SEQUENCE
    assert DOWN_PATH.name not in connection._POSTGRES_MIGRATION_SEQUENCE
    assert connection._POSTGRES_MIGRATION_SEQUENCE.index(UP_PATH.name) > connection._POSTGRES_MIGRATION_SEQUENCE.index(
        "306_vkpi_product_persona_term_performance.sql"
    )


@pytest.mark.pg
def test_migration_307_up_up_down_up_on_real_postgres(pg_dsn: str) -> None:
    import psycopg
    from psycopg import sql

    schema = f"vkpi_users_307_{uuid4().hex}"
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            conn.execute(
                """
                CREATE TABLE schema_migrations (version_key TEXT PRIMARY KEY);
                INSERT INTO schema_migrations(version_key) VALUES ('307_users_token_version.sql');
                CREATE TABLE users (
                  id BIGSERIAL PRIMARY KEY,
                  email TEXT UNIQUE,
                  password_hash TEXT,
                  status TEXT DEFAULT 'pending',
                  role TEXT DEFAULT 'creator'
                );
                INSERT INTO users(email, password_hash, status, role) VALUES
                  ('a@example.test', 'hash-a', 'active', 'admin'),
                  ('b@example.test', 'hash-b', 'active', 'creator');
                """
            )

            def truth_rows() -> list[tuple]:
                return conn.execute(
                    "SELECT id, email, password_hash, status, role FROM users ORDER BY id"
                ).fetchall()

            def column_shape() -> tuple | None:
                return conn.execute(
                    "SELECT data_type, column_default, is_nullable FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name='users' AND column_name='token_version'",
                    (schema,),
                ).fetchone()

            baseline = truth_rows()
            conn.execute(UP)
            shape = column_shape()
            assert shape is not None
            data_type, column_default, is_nullable = shape
            assert data_type == "integer"
            assert column_default is None
            assert is_nullable == "YES"
            assert truth_rows() == baseline
            assert conn.execute("SELECT COUNT(*) FROM users WHERE token_version IS NOT NULL").fetchone()[0] == 0
            # 读端口径:NULL 等价 0;写端口径:COALESCE + 1。
            conn.execute("UPDATE users SET token_version = COALESCE(token_version, 0) + 1 WHERE email='a@example.test'")
            assert conn.execute(
                "SELECT COALESCE(token_version, 0) FROM users ORDER BY id"
            ).fetchall() == [(1,), (0,)]

            conn.execute(UP)  # 幂等
            assert column_shape() is not None

            conn.execute(DOWN)
            assert column_shape() is None
            assert truth_rows() == baseline
            assert conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version_key='307_users_token_version.sql'"
            ).fetchone()[0] == 0
            conn.execute(DOWN)  # 幂等
            conn.execute(UP)
            assert column_shape() is not None
            assert truth_rows() == baseline
        finally:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))

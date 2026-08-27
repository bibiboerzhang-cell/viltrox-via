"""Migration 305: 内容推断语言的**分列存储**契约。

为什么这个文件存在
------------------
本功能唯一的立身之本是一条红线:**推断值绝不许冒充自报值**。
``vkpi_kol_pool.language`` 是平台/创作者自己声明的语言;迁移 305 新加的六列存的是
我们从创作者自己写的文本(bio + 视频标题)里估出来的值 —— 可重建、可信度更低、
运维必须一眼分得清。这两者一旦在存储层混在一起,门面上再怎么标注都是补丁。

所以下面的断言分两路把「分列」钉死:

* **静态路**(无需数据库):把 SQL 里的注释和字符串字面量剥掉之后,可执行语句里
  出现的每一个 ``language*`` 标识符都必须以 ``language_inferred`` 开头。迁移里
  不许有任何一条 ``UPDATE`` / ``INSERT`` / ``DELETE`` 打到 ``vkpi_kol_pool`` 上 ——
  305 是纯结构迁移,碰零行数据,自然也就碰不到自报列。
* **真库路**(``@pytest.mark.pg``):在一次性 schema 里造一张带 ``language`` 的表,
  灌进三种自报状态(有值 / 空串 / NULL),up → up → down → up 跑一圈,每一步都回读
  自报列的类型、默认值和三行取值,证明它逐字未动;推断列则在 down 之后干净消失、
  重新 up 之后回来是 NULL(可重建,不该被回滚保留)。

另外两条口径也在这里钉住:

* 六列**一律可空、一律无默认值**。自报列是 ``DEFAULT ''``(见迁移 039),推断列
  故意不给默认 —— 判不出就是 NULL,也就是「未知」,不是空串、更不是任何具体值。
  这正是红线 4「判不出进未知档,不是不合格」在存储层的对应物。
* 迁移只前向注册:``305_..._down.sql`` 绝不许进 ``_POSTGRES_MIGRATION_SEQUENCE``。
"""
from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import pytest

from app.db import connection


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "migrations"
UP_PATH = MIGRATIONS_DIR / "305_vkpi_kol_pool_language_inferred.sql"
DOWN_PATH = MIGRATIONS_DIR / "305_vkpi_kol_pool_language_inferred_down.sql"
UP = UP_PATH.read_text(encoding="utf-8")
DOWN = DOWN_PATH.read_text(encoding="utf-8")

#: 自报列。迁移 305 的可执行语句里一次都不许出现它。
SELF_REPORTED_COLUMN = "language"

#: 新列 -> (DDL 里的类型字面量, information_schema.data_type)。
NEW_COLUMNS = {
    "language_inferred": ("TEXT", "text"),
    "language_inferred_confidence": ("TEXT", "text"),
    "language_inferred_source": ("TEXT", "text"),
    "language_inferred_sample_n": ("INTEGER", "integer"),
    "language_inferred_at": ("TIMESTAMPTZ", "timestamp with time zone"),
    "language_inferred_method": ("TEXT", "text"),
}

INDEX_NAME = "idx_vkpi_kol_pool_language_inferred"

_COMMENT_RE = re.compile(r"--[^\n]*")
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_LANGUAGE_TOKEN_RE = re.compile(r"\blanguage\w*", re.IGNORECASE)


def _executable_sql(source: str) -> str:
    """剥掉 ``--`` 注释和单引号字面量,只留下真正会被数据库执行的标识符文本。

    必须剥:迁移的注释和 ``COMMENT ON COLUMN`` 的正文里本来就会自然地谈到
    「language」这个词(那是**说明**,不是**操作**)。不剥就分不清「提到自报列」
    和「改动自报列」,红线断言会退化成一条永远为假的噪音。
    """

    return _STRING_LITERAL_RE.sub("''", _COMMENT_RE.sub("", source))


def test_up_adds_exactly_the_six_inferred_columns_with_the_right_types() -> None:
    for column, (ddl_type, _pg_type) in NEW_COLUMNS.items():
        pattern = (
            rf"ALTER TABLE vkpi_kol_pool\s+ADD COLUMN IF NOT EXISTS "
            rf"{re.escape(column)} {re.escape(ddl_type)};"
        )
        assert re.search(pattern, UP), f"missing ADD COLUMN for {column} {ddl_type}"

    executable = _executable_sql(UP)
    assert executable.count("ADD COLUMN") == len(NEW_COLUMNS), (
        "305 must add exactly six columns; an extra ADD COLUMN slipped in"
    )


def test_up_leaves_every_new_column_nullable_and_defaultless() -> None:
    """判不出就是 NULL。给默认值 = 替创作者凭空做了一个声明。"""

    executable = _executable_sql(UP)
    assert "NOT NULL" not in executable, (
        "an inferred column must stay nullable; NULL is how 'unknown' is spelled"
    )
    assert "DEFAULT" not in executable.upper(), (
        "an inferred column must have no default; a default would fabricate a value "
        "for creators the engine could not judge"
    )


def test_up_creates_the_lookup_index() -> None:
    pattern = (
        rf"CREATE INDEX IF NOT EXISTS {re.escape(INDEX_NAME)}\s+"
        rf"ON vkpi_kol_pool \(language_inferred\);"
    )
    assert re.search(pattern, UP), "missing the language_inferred lookup index"


def test_up_documents_that_null_means_unknown_not_self_reported() -> None:
    """列注释是运维在 psql 里唯一能读到的口径,红线 1 必须写在那里。"""

    for column in NEW_COLUMNS:
        assert f"COMMENT ON COLUMN vkpi_kol_pool.{column} IS" in UP, (
            f"{column} ships without a column comment"
        )
    assert "NULL means unknown, never a self-reported value" in UP


def test_migration_never_touches_the_self_reported_language_column() -> None:
    """红线 1 的结构性保证:可执行语句里没有一个裸 ``language`` 标识符。

    只要 SQL 从来没有指名过自报列,推断值在存储层就不可能污染它 —— 这比事后比对
    ``overlap_must_be_zero`` 更强,因为它排除的是**能力**而不只是**当次结果**。
    """

    for path, source in ((UP_PATH, UP), (DOWN_PATH, DOWN)):
        executable = _executable_sql(source)
        stray = [
            token
            for token in _LANGUAGE_TOKEN_RE.findall(executable)
            if not token.lower().startswith("language_inferred")
        ]
        assert not stray, (
            f"{path.name} names the self-reported column: {stray!r}; "
            "migration 305 must only ever touch language_inferred*"
        )
        assert f"DROP COLUMN IF EXISTS {SELF_REPORTED_COLUMN};" not in executable


def test_migration_writes_no_rows_at_all() -> None:
    """305 是纯结构迁移。没有 DML,就没有任何一条路径能把推断值写进自报列。"""

    for path, source in ((UP_PATH, UP), (DOWN_PATH, DOWN)):
        executable = _executable_sql(source).upper()
        assert "UPDATE " not in executable, f"{path.name} must not update rows"
        assert "INSERT INTO" not in executable, f"{path.name} must not insert rows"
        assert "DELETE FROM VKPI_KOL_POOL" not in executable, (
            f"{path.name} must not delete creator rows"
        )


def test_up_is_idempotent_by_construction() -> None:
    """每一条 DDL 都带 IF NOT EXISTS,重跑不炸;``COMMENT ON`` 天然幂等。"""

    executable = _executable_sql(UP)
    assert executable.count("ADD COLUMN IF NOT EXISTS") == len(NEW_COLUMNS)
    assert "CREATE INDEX IF NOT EXISTS" in executable
    assert re.search(r"ADD COLUMN(?! IF NOT EXISTS)", executable) is None
    assert re.search(r"CREATE INDEX(?! IF NOT EXISTS)", executable) is None


def test_down_drops_only_its_own_evidence_and_deregisters_itself() -> None:
    executable = _executable_sql(DOWN)
    assert f"DROP INDEX IF EXISTS {INDEX_NAME};" in executable
    for column in NEW_COLUMNS:
        assert f"ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS {column};" in executable, (
            f"down leaves {column} behind"
        )
    assert executable.count("DROP COLUMN") == len(NEW_COLUMNS)

    # 索引先于列被丢弃:显式顺序,不靠 Postgres 的级联行为兜底。
    assert executable.index("DROP INDEX") < executable.index("DROP COLUMN")

    assert "DELETE FROM schema_migrations" in executable
    assert "305_vkpi_kol_pool_language_inferred.sql" in DOWN
    # 不许顺手把别的迁移一起注销。
    assert _executable_sql(DOWN).count("DELETE FROM schema_migrations") == 1


def test_down_is_idempotent_by_construction() -> None:
    executable = _executable_sql(DOWN)
    assert executable.count("DROP COLUMN IF EXISTS") == len(NEW_COLUMNS)
    assert re.search(r"DROP COLUMN(?! IF EXISTS)", executable) is None
    assert re.search(r"DROP INDEX(?! IF EXISTS)", executable) is None


def test_migration_sql_avoids_the_compat_placeholder_traps() -> None:
    """ASCII 问号会被 compat 适配器当占位符,百分号会撞上 LIKE 转义。两者一个都不许有。"""

    for path, source in ((UP_PATH, UP), (DOWN_PATH, DOWN)):
        assert "?" not in source, f"{path.name} contains an ASCII question mark"
        assert "%" not in source, f"{path.name} contains a percent literal"
        assert "BEGIN;" not in source.upper() and "COMMIT;" not in source.upper(), (
            f"{path.name} must not own its transaction"
        )


def test_migration_305_is_discovered_forward_only() -> None:
    assert UP_PATH.name in connection._POSTGRES_MIGRATION_SEQUENCE
    assert DOWN_PATH.name not in connection._POSTGRES_MIGRATION_SEQUENCE


@pytest.mark.pg
def test_migration_305_up_up_down_up_on_real_postgres(pg_dsn: str) -> None:
    """真库跑一圈,并在每一步回读自报列 —— 它必须逐字未动。"""

    import psycopg
    from psycopg import sql

    schema = f"vkpi_kol_lang_305_{uuid4().hex}"
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(
                sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
            )
            conn.execute(
                """
                CREATE TABLE schema_migrations (version_key TEXT PRIMARY KEY);
                INSERT INTO schema_migrations(version_key)
                VALUES ('305_vkpi_kol_pool_language_inferred.sql');
                CREATE TABLE vkpi_kol_pool (
                  id BIGSERIAL PRIMARY KEY,
                  handle TEXT NOT NULL,
                  language TEXT DEFAULT ''
                );
                INSERT INTO vkpi_kol_pool(handle, language) VALUES
                  ('declared-en', 'en'),
                  ('declared-blank', ''),
                  ('declared-null', NULL);
                """
            )

            def self_reported_rows() -> list[tuple[str, str | None]]:
                return conn.execute(
                    "SELECT handle, language FROM vkpi_kol_pool ORDER BY handle"
                ).fetchall()

            def self_reported_shape() -> tuple[str, str | None, str]:
                return conn.execute(
                    "SELECT data_type, column_default, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name='vkpi_kol_pool' "
                    "AND column_name='language'",
                    (schema,),
                ).fetchone()

            def inferred_shape() -> dict[str, tuple[str, str | None, str]]:
                rows = conn.execute(
                    "SELECT column_name, data_type, column_default, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name='vkpi_kol_pool' "
                    "AND strpos(column_name, 'language_inferred') = 1",
                    (schema,),
                ).fetchall()
                return {row[0]: (row[1], row[2], row[3]) for row in rows}

            def index_names() -> set[str]:
                return {
                    row[0]
                    for row in conn.execute(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname=%s AND tablename='vkpi_kol_pool'",
                        (schema,),
                    ).fetchall()
                }

            baseline_rows = self_reported_rows()
            baseline_shape = self_reported_shape()
            assert baseline_rows == [
                ("declared-blank", ""),
                ("declared-en", "en"),
                ("declared-null", None),
            ]

            # --- up ------------------------------------------------------
            conn.execute(UP)

            shape = inferred_shape()
            assert set(shape) == set(NEW_COLUMNS), (
                f"unexpected inferred column set: {sorted(shape)}"
            )
            for column, (_ddl_type, pg_type) in NEW_COLUMNS.items():
                data_type, column_default, is_nullable = shape[column]
                assert data_type == pg_type, f"{column} is {data_type}, expected {pg_type}"
                assert column_default is None, (
                    f"{column} carries default {column_default!r}; unknown must stay NULL"
                )
                assert is_nullable == "YES", f"{column} is NOT NULL"

            assert INDEX_NAME in index_names()
            comment = conn.execute(
                "SELECT col_description('vkpi_kol_pool'::regclass, attnum) "
                "FROM pg_attribute "
                "WHERE attrelid='vkpi_kol_pool'::regclass AND attname='language_inferred'"
            ).fetchone()[0]
            assert "never a self-reported value" in comment

            # 自报列在结构和数据两面都未动。
            assert self_reported_shape() == baseline_shape
            assert self_reported_rows() == baseline_rows

            # 新列一上来全是 NULL —— 迁移没有替任何人填过值。
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE language_inferred IS NOT NULL"
            ).fetchone()[0] == 0

            # 写入推断值之后,自报列依旧一动不动:这就是「分列」的全部意义。
            conn.execute(
                "UPDATE vkpi_kol_pool "
                "SET language_inferred='ko', language_inferred_confidence='high', "
                "    language_inferred_source='bio+video_titles', "
                "    language_inferred_sample_n=12, language_inferred_at=NOW(), "
                "    language_inferred_method='kol_content_langdetect_vote_v1' "
                "WHERE handle='declared-null'"
            )
            assert self_reported_rows() == baseline_rows
            assert conn.execute(
                "SELECT language, language_inferred FROM vkpi_kol_pool "
                "WHERE handle='declared-null'"
            ).fetchone() == (None, "ko")

            # --- up 重跑(幂等) -----------------------------------------
            conn.execute(UP)
            assert set(inferred_shape()) == set(NEW_COLUMNS)
            assert INDEX_NAME in index_names()
            assert self_reported_rows() == baseline_rows
            assert conn.execute(
                "SELECT language_inferred FROM vkpi_kol_pool WHERE handle='declared-null'"
            ).fetchone()[0] == "ko", "re-running up must not wipe inferred evidence"

            # --- down ----------------------------------------------------
            conn.execute(DOWN)
            assert inferred_shape() == {}
            assert INDEX_NAME not in index_names()
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM schema_migrations "
                "WHERE version_key='305_vkpi_kol_pool_language_inferred.sql'"
            ).fetchone()[0] == 0
            # 回滚只丢自己的证据:自报列和创作者行一条不少。
            assert self_reported_shape() == baseline_shape
            assert self_reported_rows() == baseline_rows

            # --- down 重跑(幂等) ---------------------------------------
            conn.execute(DOWN)
            assert inferred_shape() == {}
            assert self_reported_rows() == baseline_rows

            # --- up 再来一次 ---------------------------------------------
            conn.execute(UP)
            assert set(inferred_shape()) == set(NEW_COLUMNS)
            assert INDEX_NAME in index_names()
            assert self_reported_rows() == baseline_rows
            # 推断值是可重建的证据,不该被回滚偷偷保留下来。
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE language_inferred IS NOT NULL"
            ).fetchone()[0] == 0
        finally:
            conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )

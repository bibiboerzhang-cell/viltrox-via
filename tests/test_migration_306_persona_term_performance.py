"""Migration 306: persona 词效回填列(term_performance_json)的存储契约。

钉三条线:

* **纯结构、单列**。306 只给 vkpi_product_persona 加一列 JSONB 可空无默认,
  零 DML——persona 正文列(what_is / ideal_persona / 各 *_json)在可执行语句里
  一个都不许被指名,结构上就碰不到 LLM 生成的正文。
* **NULL 语义**。NULL = 该 SKU 尚未经历带词效证据的重放,不是零产出;列注释
  必须把来源(discovery_term_evidence 经 per_sku_term_performance 聚合)、唯一
  写点(build_product_personas.py 重放路径)和 low_sample 口径写给 psql 里的运维。
* **compat 占位符陷阱**。迁移全文(注释在内)禁 ASCII 问号与百分号——compat
  适配器把问号当占位符,历史上炸过 apply(vkpi-migration-question-mark-trap)。

真库路(``@pytest.mark.pg``)up → up → down → up 跑一圈,每步回读 persona 正文
行,证明它逐字未动;回填值在 down 之后消失、重新 up 回来是 NULL(可重建证据,
不该被回滚偷偷保留)。
"""
from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import pytest

from app.db import connection

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "migrations"
UP_PATH = MIGRATIONS_DIR / "306_vkpi_product_persona_term_performance.sql"
DOWN_PATH = MIGRATIONS_DIR / "306_vkpi_product_persona_term_performance_down.sql"
UP = UP_PATH.read_text(encoding="utf-8")
DOWN = DOWN_PATH.read_text(encoding="utf-8")

NEW_COLUMN = "term_performance_json"

#: persona 正文列:306 的可执行语句里一个都不许出现。
BODY_COLUMNS = (
    "what_is",
    "key_specs_json",
    "ideal_persona",
    "ideal_creator_types_json",
    "verticals_json",
    "promotion_angles_json",
    "avoid_types_json",
)

_COMMENT_RE = re.compile(r"--[^\n]*")
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")


def _executable_sql(source: str) -> str:
    """剥掉注释与字符串字面量:注释和 COMMENT ON 正文里自然会谈到正文列,
    那是说明不是操作;不剥就分不清「提到」和「改动」。"""

    return _STRING_LITERAL_RE.sub("''", _COMMENT_RE.sub("", source))


def test_up_adds_exactly_one_nullable_defaultless_jsonb_column() -> None:
    assert re.search(
        r"ALTER TABLE vkpi_product_persona\s+"
        r"ADD COLUMN IF NOT EXISTS term_performance_json JSONB NULL;",
        UP,
    ), "missing the single ADD COLUMN for term_performance_json JSONB NULL"
    executable = _executable_sql(UP)
    assert executable.count("ADD COLUMN") == 1, "306 must add exactly one column"
    assert "NOT NULL" not in executable, "NULL is how 'not yet backfilled' is spelled"
    assert "DEFAULT" not in executable.upper(), (
        "a default would fabricate a ledger for SKUs never replayed"
    )


def test_up_documents_source_write_point_and_null_semantics() -> None:
    assert f"COMMENT ON COLUMN vkpi_product_persona.{NEW_COLUMN} IS" in UP
    assert "per_sku_term_performance" in UP, "comment must name the aggregation source"
    assert "build_product_personas.py" in UP, "comment must name the only write point"
    assert "low_sample" in UP, "comment must carry the starved-sample caveat"
    assert "NULL means not yet backfilled, never zero yield" in UP


def test_executable_sql_never_names_a_persona_body_column() -> None:
    """结构性保证:可执行语句指不到正文列,就绝不可能改到 LLM 生成的正文。"""

    for path, source in ((UP_PATH, UP), (DOWN_PATH, DOWN)):
        executable = _executable_sql(source)
        stray = [column for column in BODY_COLUMNS if column in executable]
        assert not stray, f"{path.name} names persona body columns: {stray!r}"


def test_migration_writes_no_persona_rows_at_all() -> None:
    for path, source in ((UP_PATH, UP), (DOWN_PATH, DOWN)):
        executable = _executable_sql(source).upper()
        assert "UPDATE " not in executable, f"{path.name} must not update rows"
        assert "INSERT INTO" not in executable, f"{path.name} must not insert rows"
        assert "DELETE FROM VKPI_PRODUCT_PERSONA" not in executable, (
            f"{path.name} must not delete persona rows"
        )


def test_up_and_down_are_idempotent_by_construction() -> None:
    up_exec = _executable_sql(UP)
    assert up_exec.count("ADD COLUMN IF NOT EXISTS") == 1
    assert re.search(r"ADD COLUMN(?! IF NOT EXISTS)", up_exec) is None
    down_exec = _executable_sql(DOWN)
    assert down_exec.count("DROP COLUMN IF EXISTS") == 1
    assert re.search(r"DROP COLUMN(?! IF EXISTS)", down_exec) is None


def test_down_drops_only_its_own_evidence_and_deregisters_itself() -> None:
    executable = _executable_sql(DOWN)
    assert (
        f"ALTER TABLE vkpi_product_persona\n  DROP COLUMN IF EXISTS {NEW_COLUMN};"
        in DOWN
    )
    assert executable.count("DROP COLUMN") == 1
    assert "DELETE FROM schema_migrations" in executable
    assert "306_vkpi_product_persona_term_performance.sql" in DOWN
    assert executable.count("DELETE FROM schema_migrations") == 1  # 不顺手注销别的迁移


def test_migration_sql_avoids_the_compat_placeholder_traps() -> None:
    """ASCII 问号会被 compat 适配器当占位符炸 apply,百分号撞 LIKE 转义。"""

    for path, source in ((UP_PATH, UP), (DOWN_PATH, DOWN)):
        assert "?" not in source, f"{path.name} contains an ASCII question mark"
        assert "%" not in source, f"{path.name} contains a percent literal"
        assert "BEGIN;" not in source.upper() and "COMMIT;" not in source.upper(), (
            f"{path.name} must not own its transaction"
        )


def test_migration_306_is_discovered_forward_only() -> None:
    assert UP_PATH.name in connection._POSTGRES_MIGRATION_SEQUENCE
    assert DOWN_PATH.name not in connection._POSTGRES_MIGRATION_SEQUENCE


@pytest.mark.pg
def test_migration_306_up_up_down_up_on_real_postgres(pg_dsn: str) -> None:
    """真库跑一圈,每步回读 persona 正文——它必须逐字未动。"""

    import psycopg
    from psycopg import sql

    schema = f"vkpi_persona_306_{uuid4().hex}"
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
                VALUES ('306_vkpi_product_persona_term_performance.sql');
                CREATE TABLE vkpi_product_persona (
                  product_sku TEXT PRIMARY KEY,
                  what_is TEXT,
                  ideal_persona TEXT,
                  generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                INSERT INTO vkpi_product_persona(product_sku, what_is, ideal_persona) VALUES
                  ('EPIC-65-MACRO', '65mm macro lens', 'macro creators'),
                  ('AF-85-F18', '85mm portrait lens', 'portrait creators');
                """
            )

            def body_rows() -> list[tuple]:
                return conn.execute(
                    "SELECT product_sku, what_is, ideal_persona "
                    "FROM vkpi_product_persona ORDER BY product_sku"
                ).fetchall()

            def column_shape() -> tuple | None:
                return conn.execute(
                    "SELECT data_type, column_default, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name='vkpi_product_persona' "
                    "AND column_name='term_performance_json'",
                    (schema,),
                ).fetchone()

            baseline = body_rows()
            assert len(baseline) == 2

            # --- up ------------------------------------------------------
            conn.execute(UP)
            shape = column_shape()
            assert shape is not None, "term_performance_json was not created"
            data_type, column_default, is_nullable = shape
            assert data_type == "jsonb"
            assert column_default is None, "not-yet-backfilled must stay NULL, no default"
            assert is_nullable == "YES"
            assert body_rows() == baseline  # 正文逐字未动
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM vkpi_product_persona "
                "WHERE term_performance_json IS NOT NULL"
            ).fetchone()[0] == 0  # 迁移没替任何 SKU 填过账

            comment = conn.execute(
                "SELECT col_description('vkpi_product_persona'::regclass, attnum) "
                "FROM pg_attribute WHERE attrelid='vkpi_product_persona'::regclass "
                "AND attname='term_performance_json'"
            ).fetchone()[0]
            assert "NULL means not yet backfilled" in comment

            # 回填一条真载荷,正文依旧不动:这就是「分列」的全部意义。
            conn.execute(
                "UPDATE vkpi_product_persona SET term_performance_json = "
                "'{\"schema\": \"persona_term_performance_v1\", \"low_sample\": true}'::jsonb "
                "WHERE product_sku = 'EPIC-65-MACRO'"
            )
            assert body_rows() == baseline

            # --- up 重跑(幂等) -----------------------------------------
            conn.execute(UP)
            assert column_shape() is not None
            assert conn.execute(
                "SELECT term_performance_json ->> 'schema' AS s "
                "FROM vkpi_product_persona WHERE product_sku = 'EPIC-65-MACRO'"
            ).fetchone()[0] == "persona_term_performance_v1", (
                "re-running up must not wipe backfilled evidence"
            )

            # --- down ----------------------------------------------------
            conn.execute(DOWN)
            assert column_shape() is None
            assert body_rows() == baseline
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM schema_migrations "
                "WHERE version_key='306_vkpi_product_persona_term_performance.sql'"
            ).fetchone()[0] == 0

            # --- down 重跑(幂等)+ up 再来一次 ---------------------------
            conn.execute(DOWN)
            assert column_shape() is None
            conn.execute(UP)
            assert column_shape() is not None
            assert body_rows() == baseline
            # 回填值是可重建证据,不该被回滚偷偷保留。
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM vkpi_product_persona "
                "WHERE term_performance_json IS NOT NULL"
            ).fetchone()[0] == 0
        finally:
            conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )

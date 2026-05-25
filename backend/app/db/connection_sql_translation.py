"""SQLite-to-Postgres SQL translation helpers for the compat connection."""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any


def translate_sql_placeholders(sql: str) -> str:
    out: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_double:
            if in_single and i + 1 < len(sql) and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_single = not in_single
            out.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
            i += 1
            continue
        if ch == "?" and not in_single and not in_double:
            out.append("%s")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def translate_insert_or_ignore(sql: str) -> str:
    if not re.match(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+", sql, flags=re.IGNORECASE):
        return sql
    translated = re.sub(
        r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+",
        "INSERT INTO ",
        sql,
        count=1,
        flags=re.IGNORECASE,
    )
    if re.search(r"\bON\s+CONFLICT\b", translated, flags=re.IGNORECASE):
        return translated
    if re.search(r"\bRETURNING\b", translated, flags=re.IGNORECASE):
        parts = re.split(r"\bRETURNING\b", translated, maxsplit=1, flags=re.IGNORECASE)
        return f"{parts[0].rstrip()} ON CONFLICT DO NOTHING RETURNING {parts[1].lstrip()}"
    return translated.rstrip() + " ON CONFLICT DO NOTHING"


def translate_pragma_table_info(sql: str) -> tuple[str, bool]:
    match = re.match(r"^\s*PRAGMA\s+table_info\(([^)]+)\)\s*$", sql, flags=re.IGNORECASE)
    if not match:
        return sql, False
    translated = """
        SELECT
            (cols.ordinal_position - 1) AS cid,
            cols.column_name AS name,
            COALESCE(cols.data_type, '') AS type,
            CASE WHEN cols.is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,
            cols.column_default AS dflt_value,
            CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                   WHERE tc.constraint_type = 'PRIMARY KEY'
                     AND tc.table_schema = current_schema()
                     AND tc.table_name = %s
                     AND kcu.column_name = cols.column_name
                ) THEN 1 ELSE 0
            END AS pk
        FROM information_schema.columns cols
        WHERE cols.table_schema = current_schema()
          AND cols.table_name = %s
        ORDER BY cols.ordinal_position
    """
    return translated, True


def translate_sqlite_master(sql: str) -> tuple[str, bool]:
    normalized = " ".join(sql.strip().split())
    if "FROM sqlite_master" not in normalized:
        return sql, False
    translated = """
        SELECT table_name AS name
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_type = 'BASE TABLE'
          AND table_name = %s
    """
    return translated, True


def translate_sql_dialect(sql: str) -> str:
    translated = translate_sql_placeholders(translate_insert_or_ignore(sql))
    replacements = [
        (r"\s+COLLATE\s+NOCASE\b", ""),
        (r"strftime\('%Y-W%W',\s*([^)]+?)\s*\)", r"""TO_CHAR(\1, 'IYYY-"W"IW')"""),
        (r"strftime\('%H',\s*([^)]+?)\s*\)", r"TO_CHAR(CAST(\1 AS TIMESTAMP), 'HH24')"),
        (r"substr\(([^,]+),\s*(\d+),\s*(\d+)\)", r"SUBSTRING(CAST(\1 AS TEXT) FROM \2 FOR \3)"),
        (r"date\('now',\s*'start of month'\)", r"(DATE_TRUNC('month', NOW()))::date"),
        (r"date\('now',\s*'start of year'\)", r"(DATE_TRUNC('year', NOW()))::date"),
        (r"datetime\('now',\s*'start of month'\)", r"DATE_TRUNC('month', NOW())"),
        (r"datetime\('now',\s*'start of year'\)", r"DATE_TRUNC('year', NOW())"),
        (r"datetime\('now',\s*%s\s*\|\|\s*' days'\)", r"(NOW() + ((%s || ' days'))::interval)"),
        (r"date\('now',\s*%s\s*\|\|\s*' days'\)", r"DATE(NOW() + ((%s || ' days'))::interval)"),
        (r"datetime\('now',\s*%s\)", r"(NOW() + (%s)::interval)"),
        (r"datetime\('now'\)", "NOW()"),
        (r"date\('now'\)", "DATE(NOW())"),
        (r"GROUP_CONCAT\s*\(\s*DISTINCT\s+([^)]+?)\s*\)", r"STRING_AGG(DISTINCT (\1)::text, ',' ORDER BY (\1)::text)"),
        (r"GROUP_CONCAT\s*\(\s*([^)]+?)\s*\)", r"STRING_AGG((\1)::text, ',')"),
    ]
    for pattern, replacement in replacements:
        translated = re.sub(pattern, replacement, translated, flags=re.IGNORECASE)
    translated = re.sub(
        r"datetime\('now',\s*'([^']+)'\)",
        lambda match: f"(NOW() + INTERVAL '{match.group(1)}')",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"date\('now',\s*'([^']+)'\)",
        lambda match: f"DATE(NOW() + INTERVAL '{match.group(1)}')",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"\b((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*_json)\s+(NOT\s+)?LIKE\b",
        lambda match: f"CAST({match.group(1)} AS TEXT) {match.group(2) or ''}LIKE",
        translated,
        flags=re.IGNORECASE,
    )
    return translated


def translate_special_sql(sql: str, params: Sequence[Any]) -> tuple[str, Sequence[Any]]:
    translated, matched = translate_pragma_table_info(sql)
    if matched:
        table_name = re.match(r"^\s*PRAGMA\s+table_info\(([^)]+)\)\s*$", sql, flags=re.IGNORECASE).group(1).strip().strip("'").strip('"')
        return translated, (table_name, table_name)
    translated, matched = translate_sqlite_master(sql)
    if matched:
        name = params[0] if params else ""
        return translated, (name,)
    return sql, params

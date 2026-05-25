"""V-KPI product catalog schema and read helpers."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn, is_postgres_runtime


def ensure_product_catalog_schema() -> None:
    conn = get_conn()
    if is_postgres_runtime():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_products (
                sku TEXT PRIMARY KEY,
                category_main TEXT NOT NULL,
                category_detail TEXT,
                model_name TEXT NOT NULL,
                marketing_name TEXT,
                price_usd NUMERIC(10,2),
                status TEXT NOT NULL DEFAULT 'priced',
                description TEXT,
                source_file TEXT,
                series TEXT DEFAULT '',
                mount TEXT DEFAULT '',
                product_url TEXT DEFAULT '',
                specs_json TEXT NOT NULL DEFAULT '{}',
                fit_tags_json TEXT NOT NULL DEFAULT '[]',
                source_url TEXT DEFAULT '',
                source_checked_at TIMESTAMPTZ,
                source_confidence NUMERIC(4,2) NOT NULL DEFAULT 0,
                imported_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        for column, ddl in {
            "series": "TEXT DEFAULT ''",
            "mount": "TEXT DEFAULT ''",
            "product_url": "TEXT DEFAULT ''",
            "specs_json": "TEXT NOT NULL DEFAULT '{}'",
            "fit_tags_json": "TEXT NOT NULL DEFAULT '[]'",
            "source_url": "TEXT DEFAULT ''",
            "source_checked_at": "TIMESTAMPTZ",
            "source_confidence": "NUMERIC(4,2) NOT NULL DEFAULT 0",
        }.items():
            conn.execute(f"ALTER TABLE vkpi_products ADD COLUMN IF NOT EXISTS {column} {ddl}")
        for column in (
            "sku",
            "category_main",
            "category_detail",
            "model_name",
            "marketing_name",
            "status",
            "source_file",
        ):
            conn.execute(f"ALTER TABLE vkpi_products ALTER COLUMN {column} TYPE TEXT")
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_products (
                sku TEXT PRIMARY KEY,
                category_main TEXT NOT NULL,
                category_detail TEXT,
                model_name TEXT NOT NULL,
                marketing_name TEXT,
                price_usd REAL,
                status TEXT NOT NULL DEFAULT 'priced',
                description TEXT,
                source_file TEXT,
                series TEXT DEFAULT '',
                mount TEXT DEFAULT '',
                product_url TEXT DEFAULT '',
                specs_json TEXT NOT NULL DEFAULT '{}',
                fit_tags_json TEXT NOT NULL DEFAULT '[]',
                source_url TEXT DEFAULT '',
                source_checked_at TEXT,
                source_confidence REAL NOT NULL DEFAULT 0,
                imported_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        existing = {str(row["name"]) for row in conn.execute("PRAGMA table_info(vkpi_products)").fetchall()}
        for column, ddl in {
            "series": "TEXT DEFAULT ''",
            "mount": "TEXT DEFAULT ''",
            "product_url": "TEXT DEFAULT ''",
            "specs_json": "TEXT NOT NULL DEFAULT '{}'",
            "fit_tags_json": "TEXT NOT NULL DEFAULT '[]'",
            "source_url": "TEXT DEFAULT ''",
            "source_checked_at": "TEXT",
            "source_confidence": "REAL NOT NULL DEFAULT 0",
        }.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE vkpi_products ADD COLUMN {column} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON vkpi_products(category_main)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_status ON vkpi_products(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_price ON vkpi_products(price_usd) WHERE price_usd IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_series_mount ON vkpi_products(series, mount)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_source_confidence ON vkpi_products(source_confidence DESC, updated_at DESC)")
    conn.commit()


def list_product_catalog(
    limit: int = 300,
    *,
    categories: list[str] | None = None,
    query: str = "",
    status: str = "",
) -> dict[str, Any]:
    ensure_product_catalog_schema()
    where: list[str] = []
    params: list[Any] = []
    normalized_categories = [str(item).strip() for item in (categories or []) if str(item).strip()]
    if normalized_categories:
        placeholders = ",".join("?" for _ in normalized_categories)
        where.append(f"category_main IN ({placeholders})")
        params.extend(normalized_categories)
    if status:
        where.append("status=?")
        params.append(str(status).strip())
    if query:
        like = f"%{str(query).strip().lower()}%"
        where.append("(lower(sku) LIKE ? OR lower(model_name) LIKE ? OR lower(COALESCE(marketing_name, '')) LIKE ? OR lower(COALESCE(description, '')) LIKE ?)")
        params.extend([like, like, like, like])
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = get_conn().execute(
        f"""
        SELECT sku, category_main, category_detail, model_name, marketing_name,
               price_usd, status, description, source_file, series, mount,
               product_url, specs_json, fit_tags_json, source_url, source_checked_at,
               source_confidence, imported_at, updated_at
        FROM vkpi_products
        {clause}
        ORDER BY category_main ASC,
                 CASE WHEN price_usd IS NULL THEN 1 ELSE 0 END ASC,
                 price_usd DESC,
                 sku ASC
        LIMIT ?
        """,
        (*params, max(1, min(500, int(limit or 300)))),
    ).fetchall()
    summary = get_conn().execute(
        """
        SELECT category_main, status, COUNT(*) AS count
        FROM vkpi_products
        GROUP BY category_main, status
        ORDER BY category_main ASC, status ASC
        """
    ).fetchall()
    return {
        "products": [dict(row) for row in rows],
        "summary": [dict(row) for row in summary],
        "count": len(rows),
    }

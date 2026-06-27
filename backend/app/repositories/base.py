"""Repository 基类 —— 统一 conn/取数/写数,薄封装 PostgresCompatConnection。

占位符用 ?(compat 适配器);所有方法 best-effort 由调用方决定异常处理粒度。
"""
from __future__ import annotations

from typing import Any, Sequence

from app.db.connection import get_conn, table_exists


class BaseRepository:
    """薄基类:统一连接 + 行字典化 + 表存在守卫。子类只写 SQL 与领域方法。"""

    table: str = ""

    def _conn(self) -> Any:
        return get_conn()

    def exists(self) -> bool:
        return bool(self.table) and table_exists(self.table)

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        row = self._conn().execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn().execute(sql, tuple(params)).fetchall()]

    def execute(self, sql: str, params: Sequence[Any] = (), *, commit: bool = True) -> Any:
        conn = self._conn()
        cur = conn.execute(sql, tuple(params))
        if commit:
            conn.commit()
        return cur

    def scalar(self, sql: str, params: Sequence[Any] = (), *, key: str = "v") -> Any:
        row = self.fetch_one(sql, params)
        return row.get(key) if row else None

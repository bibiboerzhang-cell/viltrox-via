"""顶栏 Ask「$SKU / 镜头」直达候选 —— 纯读、零 LLM、零敏感列。

回答一个问题:用户在 ⌘K 里敲 ``$75`` / ``$85mm pro`` 时,给 ≤20 条产品候选。

数据源(全只读):
  - vkpi_products(官方目录):sku / model_name / marketing_name
  - vkpi_kol_lens_evidence(迁移 287 派生表):lens_key / display_name / product_sku
    —— 这是 KOL 真正「说出口」的镜头家族名,按 display_name 去重,提及多的排前。

输出只有三列 {sku, display_name, lens_key}:价格 / 成本 / 评分 / 规格一概不带。
compat 注意:SQL 全 ? 占位;子串命中走 STRPOS(PG)/ INSTR(SQLite)绑定参数,
SQL 字面量里绝不出现百分号;SQL 内不写注释(ASCII 问号陷阱)。
红线:零触 viltrox_fit_score / rule_v0;表缺 / 查询失败按来源诚实标 absent / error,
不把故障装成零结果。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger("viltrox.domains.products.catalog_suggest")

MAX_QUERY_LENGTH = 80
LIMIT_DEFAULT = 20
LIMIT_MAX = 20
# 两个来源各多取一点再合并去重,避免目录里一个型号 N 个卡口把家族名挤掉。
_SOURCE_FETCH_MULTIPLIER = 3


def _text(value: Any, limit: int = 200) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:limit]


def _norm_key(value: str) -> str:
    return " ".join(value.lower().split())


def _contains(column_expr: str, *, postgres: bool) -> str:
    """子串命中谓词;参数绑定小写关键词,SQL 里无百分号。"""
    fn = "STRPOS" if postgres else "INSTR"
    return f"{fn}(LOWER(COALESCE({column_expr}, '')), ?) > 0"


def _rank(label: str, keyword: str) -> int:
    """0=完全相等 1=前缀 2=子串;用于合并后排序,越小越前。"""
    lowered = label.lower()
    if lowered == keyword:
        return 0
    if lowered.startswith(keyword):
        return 1
    return 2


def _lens_rows(conn: Any, keyword: str, fetch: int, *, postgres: bool) -> list[dict[str, Any]]:
    sql = f"""
        SELECT lens_key, display_name, product_sku, SUM(mention_count) AS mentions
        FROM vkpi_kol_lens_evidence
        WHERE resolution <> 'unresolved'
          AND COALESCE(display_name, '') <> ''
          AND ({_contains('display_name', postgres=postgres)}
               OR {_contains('lens_key', postgres=postgres)}
               OR {_contains('product_sku', postgres=postgres)})
        GROUP BY lens_key, display_name, product_sku
        ORDER BY mentions DESC, display_name ASC
        LIMIT ?
    """
    rows = conn.execute(sql, (keyword, keyword, keyword, fetch)).fetchall()
    return [dict(row) for row in rows]


def _product_rows(conn: Any, keyword: str, fetch: int, *, postgres: bool) -> list[dict[str, Any]]:
    sql = f"""
        SELECT sku, model_name, marketing_name
        FROM vkpi_products
        WHERE ({_contains('sku', postgres=postgres)}
               OR {_contains('model_name', postgres=postgres)}
               OR {_contains('marketing_name', postgres=postgres)})
        ORDER BY LENGTH(COALESCE(marketing_name, model_name, '')) ASC, sku ASC
        LIMIT ?
    """
    rows = conn.execute(sql, (keyword, keyword, keyword, fetch)).fetchall()
    return [dict(row) for row in rows]


def suggest_catalog(
    conn: Any,
    q: str,
    *,
    limit: int = LIMIT_DEFAULT,
    postgres: bool,
    lens_table_exists: bool = True,
) -> dict[str, Any]:
    keyword = _norm_key(_text(q, MAX_QUERY_LENGTH))
    try:
        result_limit = max(1, min(int(limit), LIMIT_MAX))
    except (TypeError, ValueError):
        result_limit = LIMIT_DEFAULT
    fetch = result_limit * _SOURCE_FETCH_MULTIPLIER
    source_status: dict[str, dict[str, Any]] = {
        "lens_evidence": {"status": "ready", "result_count": 0},
        "products": {"status": "ready", "result_count": 0},
    }
    if not keyword:
        return {"status": "empty", "q": "", "items": [], "source_status": source_status}

    merged: list[tuple[int, dict[str, Any]]] = []
    by_name: dict[str, dict[str, Any]] = {}
    seen_skus: set[str] = set()

    def _push(order: int, sku: str, display_name: str, lens_key: str) -> None:
        name_key = _norm_key(display_name)
        if not name_key:
            return
        existing = by_name.get(name_key)
        if existing is not None:
            # 同名家族行(无 SKU)先到、带 SKU 的行后到:把 SKU 补上,让候选能直达 SKU 360°。
            if sku and not existing["sku"] and sku not in seen_skus:
                existing["sku"] = sku
                seen_skus.add(sku)
            if lens_key and not existing["lens_key"]:
                existing["lens_key"] = lens_key
            return
        if sku and sku in seen_skus:
            return
        if sku:
            seen_skus.add(sku)
        item = {"sku": sku, "display_name": display_name, "lens_key": lens_key}
        by_name[name_key] = item
        merged.append((order, item))

    if lens_table_exists:
        try:
            lens_rows = _lens_rows(conn, keyword, fetch, postgres=postgres)
            source_status["lens_evidence"]["result_count"] = len(lens_rows)
            for row in lens_rows:
                _push(
                    0,
                    _text(row.get("product_sku"), 120),
                    _text(row.get("display_name"), 160),
                    _text(row.get("lens_key"), 120),
                )
        except Exception:
            logger.warning("catalog_suggest lens_evidence 子查询失败", exc_info=True)
            source_status["lens_evidence"] = {"status": "error", "result_count": 0, "reason": "query_failed"}
    else:
        source_status["lens_evidence"] = {"status": "absent", "result_count": 0, "reason": "table_missing"}

    try:
        product_rows = _product_rows(conn, keyword, fetch, postgres=postgres)
        source_status["products"]["result_count"] = len(product_rows)
        for row in product_rows:
            sku = _text(row.get("sku"), 120)
            display = _text(row.get("marketing_name"), 160) or _text(row.get("model_name"), 160) or sku
            _push(1, sku, display, "")
    except Exception:
        logger.warning("catalog_suggest products 子查询失败", exc_info=True)
        source_status["products"] = {"status": "error", "result_count": 0, "reason": "query_failed"}

    # 同一命中档位内:KOL 真说出口的镜头家族名(lens_evidence)先于目录长名,再短者优先。
    merged.sort(key=lambda pair: (_rank(pair[1]["display_name"], keyword), pair[0], len(pair[1]["display_name"])))
    items = [item for _order, item in merged[:result_limit]]
    states = {entry["status"] for entry in source_status.values()}
    if states == {"error"} or states == {"error", "absent"}:
        status = "error"
    elif items:
        status = "ready" if states == {"ready"} else "partial"
    else:
        status = "empty" if states == {"ready"} else "partial"
    return {"status": status, "q": keyword, "items": items, "source_status": source_status}

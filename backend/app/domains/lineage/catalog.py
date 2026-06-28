"""Data Catalog(数据目录)—— 让每个指标自报来源/真假/新鲜度(统一可追溯面)。

把指标注册表(definitions.METRICS)× 最新指标值(vkpi_metric_values 含 data_status/confidence/is_partial)
合成一行一指标的目录:{metric_key, label, source, source_count, data_status, is_partial, value, last_computed_at}。
解决"哪些数字是真的 / 待接入 / 部分"——dashboard 任意数字可机读自报真假。
红线:纯只读合成,零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn, table_exists
from app.domains.lineage.definitions import METRICS


def build_data_catalog() -> dict[str, Any]:
    """指标注册表 × 最新指标值 → 数据目录(每指标自报 data_status/新鲜度)。"""
    latest: dict[str, dict[str, Any]] = {}
    if table_exists("vkpi_metric_values"):
        try:
            for r in get_conn().execute(
                "SELECT DISTINCT ON (metric_key) metric_key, value_numeric, unit, currency, "
                "source_count, data_status, is_partial, confidence, created_at "
                "FROM vkpi_metric_values ORDER BY metric_key, created_at DESC"
            ).fetchall():
                d = dict(r)
                latest[d["metric_key"]] = d
        except Exception:
            latest = {}

    catalog = []
    for key, meta in METRICS.items():
        v = latest.get(key, {})
        sc = int(v.get("source_count") or 0)
        status = v.get("data_status") or ("real" if sc > 0 else "awaiting_source")
        catalog.append({
            "metric_key": key,
            "label": meta.get("label"),
            "label_zh": meta.get("label_zh"),
            "drilldown_source": meta.get("drilldown_source"),
            "unit": meta.get("unit"),
            "source_count": sc,
            "data_status": status,
            "is_partial": bool(v.get("is_partial")) if v.get("is_partial") is not None else (sc == 0),
            "confidence": v.get("confidence"),
            "value_numeric": v.get("value_numeric"),
            "last_computed_at": str(v.get("created_at") or "")[:19] or None,
        })
    real = sum(1 for c in catalog if c["data_status"] == "real")
    return {
        "status": "ok",
        "total_metrics": len(catalog),
        "real": real,
        "awaiting_source": len(catalog) - real,
        "catalog": catalog,
        "note": "数据目录:每指标自报 source/data_status/新鲜度;source_count=0 即 awaiting_source(诚实,非伪绿)。零触 viltrox_fit_score。",
    }

"""B4 · 时间索引 —— 窗口内"谁在变好/变差"(compute-on-read,只读)。

基于 vkpi_channel_metrics 日快照(followers/views/engagement),算窗口内每个频道
首末快照的变化量与变化率,排出 risers / fallers。回答"过去 N 天哪些账号在变好"。
窗口相对数据最新快照(非墙钟),避免历史数据落空。红线:全程只读;零触 viltrox_fit_score。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

_TABLE = "vkpi_channel_metrics"
# 指标白名单(同时防列名注入)。
_METRICS = {"followers", "total_views", "engagement_rate", "total_likes", "posts_count"}


def _channel_names() -> dict[int, str]:
    """best-effort:channel_id → 名称(若有 vkpi_channels 表)。"""
    for tbl, idc, namec in (("vkpi_channels", "id", "name"), ("vkpi_channels", "id", "handle")):
        if not table_exists(tbl):
            continue
        try:
            rows = get_conn().execute(f"SELECT {idc} AS cid, {namec} AS nm FROM {tbl}").fetchall()
            out = {int(dict(r)["cid"]): str(dict(r).get("nm") or "") for r in rows if dict(r).get("cid") is not None}
            if out:
                return out
        except Exception:
            continue
    return {}


def compute_movers(*, window_days: int = 30, metric: str = "followers", limit: int = 10) -> dict[str, Any]:
    """窗口内 movers:每个频道首末快照变化量/率 → risers + fallers。"""
    m = str(metric or "followers").strip()
    if m not in _METRICS:
        m = "followers"
    if not table_exists(_TABLE):
        return {"status": "unavailable", "reason": "channel_metrics_absent"}
    conn = get_conn()
    maxrow = conn.execute(f"SELECT MAX(snapshot_date) AS hi FROM {_TABLE}").fetchone()
    hi = dict(maxrow).get("hi") if maxrow else None
    if not hi:
        return {"status": "no_data", "metric": m}
    try:
        # snapshot_date 是 date;阈值 = 最新快照 - window_days。
        hi_d = hi if hasattr(hi, "year") else __import__("datetime").date.fromisoformat(str(hi)[:10])
        lo_d = hi_d - timedelta(days=max(1, min(int(window_days or 30), 365)))
    except Exception:
        return {"status": "no_data", "metric": m}
    rows = conn.execute(
        f"""
        WITH win AS (
            SELECT channel_id, snapshot_date, {m} AS mval
            FROM {_TABLE}
            WHERE snapshot_date >= ? AND {m} IS NOT NULL
        ),
        ranked AS (
            SELECT channel_id, mval, snapshot_date,
                ROW_NUMBER() OVER (PARTITION BY channel_id ORDER BY snapshot_date ASC)  AS rn_first,
                ROW_NUMBER() OVER (PARTITION BY channel_id ORDER BY snapshot_date DESC) AS rn_last
            FROM win
        )
        SELECT f.channel_id AS cid, f.mval AS first_v, l.mval AS last_v
        FROM ranked f JOIN ranked l ON f.channel_id = l.channel_id
        WHERE f.rn_first = 1 AND l.rn_last = 1
        """,
        (str(lo_d),),
    ).fetchall()
    names = _channel_names()
    items = []
    for r in rows:
        d = dict(r)
        cid = int(d.get("cid") or 0)
        first_v, last_v = float(d.get("first_v") or 0), float(d.get("last_v") or 0)
        if first_v == last_v:
            continue
        delta = last_v - first_v
        pct = round(delta / first_v, 4) if first_v else None
        items.append({
            "channel_id": cid,
            "name": names.get(cid, f"channel #{cid}"),
            "first": first_v, "last": last_v,
            "delta": round(delta, 2), "pct_change": pct,
        })
    risers = sorted([x for x in items if x["delta"] > 0], key=lambda x: x["delta"], reverse=True)[: int(limit)]
    fallers = sorted([x for x in items if x["delta"] < 0], key=lambda x: x["delta"])[: int(limit)]
    return {
        "status": "ok",
        "metric": m,
        "window_days": int(window_days),
        "window": {"from": str(lo_d), "to": str(hi_d)},
        "channels_tracked": len(items),
        "risers": risers,
        "fallers": fallers,
        "note": "时间索引:窗口内首末快照变化(相对数据最新快照计窗);只读,零触 viltrox_fit_score。",
    }

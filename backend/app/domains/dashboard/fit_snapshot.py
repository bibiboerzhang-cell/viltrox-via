"""V6 Fit Top —— 每日 fit 历史快照 + Top Movers 计算。

诚实 by design + 红线安全:
- capture_daily_snapshot 只【读】 vkpi_kol_pool.viltrox_fit_score / followers,另存一份历史到
  vkpi_kol_fit_snapshot。**绝不写回 vkpi_kol_pool**,指纹 SUM(viltrox_fit_score) 不变。
- compute_top_movers 纯 SELECT:对最近两份快照 diff,按 |Δfit| 排序取前 N。只有 ≥2 份快照(即至少
  跑过两天)才有 movers;只有一份时诚实返回空 + reason,绝不编造。
- 零 LLM、零 provider 调用。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)


def capture_daily_snapshot() -> dict[str, Any]:
    """把当前 KOL Pool 的 fit_score/followers 存一份当日快照(幂等:同日重跑不重复)。"""
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_kol_fit_snapshot
            (snapshot_date, kol_pool_id, pool_uid, platform, handle, display_name, fit_score, followers)
        SELECT CURRENT_DATE, id, pool_uid, platform, handle, display_name, viltrox_fit_score, followers
        FROM vkpi_kol_pool
        WHERE viltrox_fit_score IS NOT NULL
        ON CONFLICT (snapshot_date, kol_pool_id) DO NOTHING
        """
    )
    conn.commit()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_kol_fit_snapshot WHERE snapshot_date = CURRENT_DATE"
    ).fetchone()
    rows = int(dict(row)["n"]) if row else 0
    dates = conn.execute(
        "SELECT COUNT(DISTINCT snapshot_date) AS d FROM vkpi_kol_fit_snapshot"
    ).fetchone()
    distinct_days = int(dict(dates)["d"]) if dates else 0
    logger.info("vkpi_fit_snapshot.captured", extra={"rows": rows, "distinct_days": distinct_days})
    return {"status": "ok", "rows": rows, "distinct_days": distinct_days}


def _top_by_fit(conn: Any, lim: int) -> list[dict[str, Any]]:
    """回落视图:当前 fit 最高的 N 个 KOL(真数据,无需历史)。delta=0,前端按 mode=top_fit 显示「Fit X」。"""
    rows = conn.execute(
        """
        SELECT kol_pool_id, handle, display_name, platform, fit_score AS fit_now, followers
        FROM vkpi_kol_fit_snapshot
        WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM vkpi_kol_fit_snapshot)
          AND fit_score IS NOT NULL
        ORDER BY fit_score DESC
        LIMIT ?
        """,
        (lim,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        out.append(
            {
                "kol_pool_id": int(d.get("kol_pool_id") or 0),
                "handle": str(d.get("handle") or d.get("display_name") or ""),
                "name": str(d.get("display_name") or d.get("handle") or ""),
                "platform": str(d.get("platform") or ""),
                "fit_now": round(float(d.get("fit_now") or 0), 2),
                "fit_prev": round(float(d.get("fit_now") or 0), 2),
                "delta": 0.0,
                "followers": int(d.get("followers") or 0),
            }
        )
    return out


def compute_top_movers(limit: int = 8) -> dict[str, Any]:
    """有 ≥2 天历史且 fit 有变化 → 真 movers(mode=movers);否则回落到当前 fit 最高(mode=top_fit)。
    两种都是真数据,绝不编造。fit_score 来自 rule_v0 多为静态,故 movers 罕见,top_fit 是常态展示。"""
    conn = get_conn()
    days = conn.execute(
        "SELECT COUNT(DISTINCT snapshot_date) AS d FROM vkpi_kol_fit_snapshot"
    ).fetchone()
    distinct_days = int(dict(days)["d"]) if days else 0
    lim = max(1, min(int(limit or 8), 50))
    if distinct_days < 2:
        return {
            "available": True,
            "mode": "top_fit",
            "distinct_days": distinct_days,
            "movers": _top_by_fit(conn, lim),
        }
    rows = conn.execute(
        """
        WITH d AS (
            SELECT DISTINCT snapshot_date FROM vkpi_kol_fit_snapshot
            ORDER BY snapshot_date DESC LIMIT 2
        ),
        latest AS (SELECT MAX(snapshot_date) AS dt FROM d),
        prev   AS (SELECT MIN(snapshot_date) AS dt FROM d)
        SELECT cur.kol_pool_id, cur.handle, cur.display_name, cur.platform,
               cur.fit_score AS fit_now, p.fit_score AS fit_prev,
               (cur.fit_score - p.fit_score) AS delta, cur.followers
        FROM vkpi_kol_fit_snapshot cur
        JOIN vkpi_kol_fit_snapshot p
          ON p.kol_pool_id = cur.kol_pool_id
         AND p.snapshot_date = (SELECT dt FROM prev)
        WHERE cur.snapshot_date = (SELECT dt FROM latest)
          AND cur.fit_score IS NOT NULL AND p.fit_score IS NOT NULL
          AND cur.fit_score <> p.fit_score
        ORDER BY ABS(cur.fit_score - p.fit_score) DESC
        LIMIT ?
        """,
        (lim,),
    ).fetchall()
    movers = []
    for r in rows:
        d = dict(r)
        delta = float(d.get("delta") or 0)
        movers.append(
            {
                "kol_pool_id": int(d.get("kol_pool_id") or 0),
                "handle": str(d.get("handle") or d.get("display_name") or ""),
                "name": str(d.get("display_name") or d.get("handle") or ""),
                "platform": str(d.get("platform") or ""),
                "fit_now": round(float(d.get("fit_now") or 0), 2),
                "fit_prev": round(float(d.get("fit_prev") or 0), 2),
                "delta": round(delta, 2),
                "followers": int(d.get("followers") or 0),
            }
        )
    if movers:
        return {"available": True, "mode": "movers", "distinct_days": distinct_days, "movers": movers}
    # 有 ≥2 天历史但 fit 无变化(rule_v0 静态)→ 回落到当前 fit 最高,避免空卡。
    return {"available": True, "mode": "top_fit", "distinct_days": distinct_days, "movers": _top_by_fit(conn, lim)}

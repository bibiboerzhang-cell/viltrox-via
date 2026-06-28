"""P0-3 假粉/异常号规则离群检测(从 pool.py 行为不变搬出)。

红线:只写 suspect_inflation* 独立列;绝不触 viltrox_fit_score 写点、绝不调 rule_v0。
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.kol.pool_common import (
    _clear_kol_pool_read_cache,
    _float_or_none,
    _int_or_none,
    _json,
    _utcnow,
)
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema


# ─── P0-3 假粉/异常号规则离群(独立列;绝不触 viltrox_fit_score 写点/rule_v0)─────
# 纯现有数据三规则,任一命中即置 suspect_inflation=TRUE + 文案 reason:
#   R1 高粉低播:followers>=阈值 且 avg_views < followers*ratio(典型买粉号画像)。
#   R2 ER 对同量级 peer z-score 离群:|z| 远离 → 互动率异常偏低(刷量)/偏高(刷互动)。
#   R3 real_er(播放分母实算)与生产 ER(粉丝分母)巨大背离:abs(real_er - er) 超阈。
# 写点仅 suspect_inflation/inflation_reason/inflation_signals_json/inflation_checked_at/
# inflation_method 五根独立列;UPDATE 语句不含 viltrox_fit_score,rule_v0 零调用。
_INFLATION_METHOD = "rule_outlier_v1"
_INFL_HIGH_FOLLOWERS = 100000          # R1 起算量级
_INFL_VIEW_RATIO = 0.01                # R1:avg_views < followers*1% 视为低播
_INFL_ER_Z_THRESHOLD = 2.5             # R2:|z|>=2.5 视为 ER 离群
_INFL_REALER_DIVERGENCE = 3.0          # R3:|real_er - production_er| >= 3(百分点)
_INFL_PEER_MIN_N = 8                   # R2:同量级 peer 至少 N 个才算 z-score


def _follower_bucket(followers: int) -> str:
    if followers >= 1000000:
        return "1m+"
    if followers >= 100000:
        return "100k_1m"
    if followers >= 10000:
        return "10k_100k"
    return "lt_10k"


def detect_inflation(*, execute: bool = False, only_id: int | None = None) -> dict[str, Any]:
    """离线可批跑的假粉/异常号规则离群检测。

    红线:只写 suspect_inflation* 独立列;绝不触 viltrox_fit_score 写点、绝不调 rule_v0。
    默认 dry-run(execute=False),--execute/execute=True 才落库。only_id 供「发现落库时
    顺带打」单行调用(import/enrich 后传 kol_pool_id)。
    """
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    # peer ER 统计按 follower_bucket 分桶(SQL 端聚合,避免全表入内存)
    peer_rows = conn.execute(
        """
        SELECT
            CASE
                WHEN followers >= 1000000 THEN '1m+'
                WHEN followers >= 100000 THEN '100k_1m'
                WHEN followers >= 10000 THEN '10k_100k'
                ELSE 'lt_10k'
            END AS bucket,
            AVG(engagement_rate) AS mean_er,
            COUNT(engagement_rate) AS n,
            AVG(engagement_rate * engagement_rate) AS mean_sq
        FROM vkpi_kol_pool
        WHERE followers IS NOT NULL AND engagement_rate IS NOT NULL
        GROUP BY bucket
        """
    ).fetchall()
    peer_stats: dict[str, dict[str, float]] = {}
    for prow in peer_rows:
        p = dict(prow)
        mean = float(p.get("mean_er") or 0.0)
        mean_sq = float(p.get("mean_sq") or 0.0)
        n = int(p.get("n") or 0)
        var = max(0.0, mean_sq - mean * mean)
        peer_stats[str(p.get("bucket"))] = {"mean": mean, "std": var ** 0.5, "n": float(n)}

    where = "WHERE id=?" if only_id else ""
    args = (int(only_id),) if only_id else ()
    rows = conn.execute(
        f"SELECT id, followers, avg_views, engagement_rate, real_er FROM vkpi_kol_pool {where}",
        args,
    ).fetchall()

    now = _utcnow()
    flagged = 0
    scanned = 0
    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        scanned += 1
        followers = _int_or_none(item.get("followers")) or 0
        avg_views = _int_or_none(item.get("avg_views"))
        er = _float_or_none(item.get("engagement_rate"))
        real_er = _float_or_none(item.get("real_er"))
        reasons: list[str] = []
        signals: dict[str, Any] = {}

        # R1 高粉低播
        if followers >= _INFL_HIGH_FOLLOWERS and avg_views is not None and avg_views < followers * _INFL_VIEW_RATIO:
            pct = (avg_views / followers * 100.0) if followers else 0.0
            reasons.append(f"高粉低播:{followers:,} 粉但均播仅 {avg_views:,}(占粉 {pct:.2f}%<1%)")
            signals["high_followers_low_views"] = {"followers": followers, "avg_views": avg_views, "view_pct": round(pct, 4)}

        # R2 ER 对同量级 peer z-score 离群
        if er is not None:
            bucket = _follower_bucket(followers)
            stat = peer_stats.get(bucket)
            if stat and stat["n"] >= _INFL_PEER_MIN_N and stat["std"] > 0:
                z = (er - stat["mean"]) / stat["std"]
                if abs(z) >= _INFL_ER_Z_THRESHOLD:
                    direction = "异常偏低(疑买粉)" if z < 0 else "异常偏高(疑刷互动)"
                    reasons.append(f"ER 对同量级 peer 离群 z={z:.1f} {direction}")
                    signals["er_zscore"] = {"bucket": bucket, "er": round(er, 4), "peer_mean": round(stat["mean"], 4), "z": round(z, 3)}

        # R3 real_er(播放分母)与生产 ER(粉丝分母)巨大背离
        if real_er is not None and er is not None and abs(real_er - er) >= _INFL_REALER_DIVERGENCE:
            reasons.append(f"real_er({real_er:.2f}%)与生产 ER({er:.2f}%)背离 {abs(real_er - er):.2f}pp")
            signals["realer_divergence"] = {"real_er": round(real_er, 4), "production_er": round(er, 4), "gap": round(abs(real_er - er), 4)}

        suspect = bool(reasons)
        reason_text = "; ".join(reasons)[:1000]
        results.append({"id": int(item["id"]), "suspect": suspect, "reason": reason_text})
        if suspect:
            flagged += 1
        if execute:
            conn.execute(
                """
                UPDATE vkpi_kol_pool
                SET suspect_inflation=?,
                    inflation_reason=?,
                    inflation_signals_json=?,
                    inflation_checked_at=?,
                    inflation_method=?
                WHERE id=?
                """,
                (bool(suspect), reason_text, _json(signals), now, _INFLATION_METHOD, int(item["id"])),
            )
    if execute:
        conn.commit()
        _clear_kol_pool_read_cache()
    return {
        "scanned": scanned,
        "flagged": flagged,
        "executed": bool(execute),
        "method": _INFLATION_METHOD,
        "viltrox_fit_score_write": False,
        "items": results if only_id else results[:50],
    }


def suspect_inflation_review_list(*, limit: int = 200, offset: int = 0) -> dict[str, Any]:
    """疑似刷量复核清单:纯只读 SELECT 已置 flag 的行,独立角标列;不触 fit_score。"""
    ensure_vkpi_product_industry_schema()
    safe_limit = max(1, min(500, int(limit or 200)))
    safe_offset = max(0, int(offset or 0))
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, pool_uid, platform, handle, display_name, followers, avg_views,
               engagement_rate, real_er, viltrox_fit_score,
               suspect_inflation, inflation_reason, inflation_signals_json,
               inflation_checked_at, inflation_method
        FROM vkpi_kol_pool
        WHERE suspect_inflation = TRUE
        ORDER BY COALESCE(followers, 0) DESC, inflation_checked_at DESC
        LIMIT ? OFFSET ?
        """,
        (safe_limit, safe_offset),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE suspect_inflation = TRUE").fetchone()
    return {
        "total": int(total["n"] if total else 0),
        "items": [dict(r) for r in rows],
        "note": "独立角标,绝不参与 viltrox_fit_score;复核人工裁定后可清 flag。",
        "viltrox_fit_score_write": False,
    }

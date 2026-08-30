"""Legacy weekly-pulse entrypoint retained only as a fail-closed cron guard.

The former synchronous refresh now returns ``durable_queue_required`` and no
longer proves provider completion.  Until a parent batch receipt aggregates
worker terminal states, this script exits before writing a success pulse.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from app.db.connection import db_connection_sync_scope, get_conn
from app.domains.sync.daily_sync import run_kol_pool_light_refresh

REPO = Path(__file__).resolve().parents[2]
LOG = REPO / "docs" / "KOL-Pool-pulse-log.md"


def fingerprint(conn) -> str:
    row = conn.execute(
        "SELECT COUNT(*) AS n, ROUND(SUM(viltrox_fit_score)::numeric,4) AS s, COUNT(viltrox_fit_reason) AS r FROM vkpi_kol_pool",
    ).fetchone()
    d = dict(row)
    return f"{d['n']}/{d['s']}/{d['r']}"


def rows_snapshot(conn) -> dict[int, str]:
    rows = conn.execute(
        """
        SELECT kp.id, COALESCE(kp.viltrox_fit_score::text,'NULL') AS score
        FROM vkpi_kol_refresh_tier rt JOIN vkpi_kol_pool kp ON kp.id=rt.kol_pool_id
        """,
    ).fetchall()
    return {int(r["id"]): str(r["score"]) for r in rows}


def apify_usage_recent(window_sec: float) -> float:
    token = (os.environ.get("APIFY_TOKEN") or os.environ.get("APIFY_API_TOKEN") or "").strip()
    if not token:
        return -1.0
    try:
        query = urllib.parse.urlencode({"limit": 40, "desc": "true"})
        req = urllib.request.Request(
            f"https://api.apify.com/v2/actor-runs?{query}",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        runs = json.loads(urllib.request.urlopen(req, timeout=30).read()).get("data", {}).get("items", [])
        cutoff = time.time() - window_sec - 120
        total = 0.0
        for r in runs:
            try:
                started = time.mktime(time.strptime(str(r.get("startedAt", ""))[:19], "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                continue
            if started >= cutoff:
                total += float(r.get("usageTotalUsd") or 0)
        return round(total, 4)
    except Exception:
        return -1.0


def require_completed_refresh(result: dict) -> None:
    """Refuse to publish a pulse receipt for an enqueue-only/deprecated caller."""
    status = str(result.get("status") or "").strip().lower()
    if status not in {"ok", "completed", "succeeded", "success"}:
        raise RuntimeError(
            f"weekly pulse refresh is not complete (status={status or 'missing'}); "
            "durable worker completion evidence is required"
        )


def main() -> None:
    stale_before = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db_connection_sync_scope():
        conn = get_conn()
        fp_before = fingerprint(conn)
        before = rows_snapshot(conn)
    t0 = time.time()
    with db_connection_sync_scope():
        result = run_kol_pool_light_refresh({
            "kol_refresh_selector": "qualified",
            "allow_qualified_kol_refresh": True,
            "kol_limit": 25,
            "kol_max_posts": 1,
            "kol_tiers": ["hot", "warm"],
            "kol_stale_before": stale_before,
        })
    require_completed_refresh(result)
    elapsed = round(time.time() - t0, 1)
    usd = apify_usage_recent(elapsed)
    with db_connection_sync_scope():
        conn = get_conn()
        fp_after = fingerprint(conn)
        after = rows_snapshot(conn)
    changed = [
        f"{rid}:{before.get(rid)}→{after.get(rid)}"
        for rid in sorted(set(before) | set(after))
        if before.get(rid) != after.get(rid)
    ]
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    line = (
        f"\n## {stamp} 周脉冲(合法漂移)\n"
        f"- requested={result.get('requested')} refreshed={result.get('refreshed')} "
        f"partial={result.get('partial')} errors={result.get('errors')} elapsed={elapsed}s\n"
        f"- 指纹: {fp_before} → {fp_after}\n"
        f"- Apify 实测: {'$' + str(usd) if usd >= 0 else '查询失败(手工核)'}\n"
        f"- 逐行归因({len(changed)} 行变更): {'; '.join(changed) if changed else '无'}\n"
    )
    LOG.parent.mkdir(exist_ok=True)
    if not LOG.exists():
        LOG.write_text("# KOL Pool 周脉冲日志(每次运行=一起合法漂移;E6 落地即停)\n")
    with LOG.open("a") as fh:
        fh.write(line)
    print(line)


if __name__ == "__main__":
    main()

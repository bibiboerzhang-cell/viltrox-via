"""方案A:V6 Fit 零成本补分(rule_v0,智能层战役第 0 号;2026-06-12 裁决①放行)。

红线第二次修订依据(裁决原文):"fit_score 写入仅限 rule_v0 评分语义——在线写点
pool.py:838 enrich_item + 经批准并留痕的离线 backfill(同函数)"。
本脚本调用与 enrich_item 完全相同的 ScoringRegistry rule_v0 评分函数,零 provider 零 LLM。

执行五条件落点:
  a. dry-run 分布已呈批(957 可补/155 门槛拦下/min15 max95 avg41.1,与既有 11 行同量纲)
  b. 门槛:platform 非空 且 (followers>0 或 posts_count>0);不达标行保持 NULL(诚实空值)
  c. 写前快照 viltrox2-pre-fitbackfill-20260611T191937.dump;写后新指纹基线落 handover
  d. provenance:viltrox_fit_reason 写入 rule_v0 输出并带 'rule_v0_backfill_20260612' 标记
     (表无 score_source 列,reason 标记 + 本脚本/commit 双留痕);幂等=仅 score IS NULL 行
  e. 脉冲/E6 刷新后 enrich_item 重算覆盖 = 预期行为

用法:python scripts/backfill_fit_scores.py --apply(无 --apply 为 dry-run 摘要)
"""
from __future__ import annotations

import sys

from app.db.connection import db_connection_sync_scope, get_conn
from app.domains.scoring import ScoringRegistry

MARKER = "rule_v0_backfill_20260612"


def eligible(row: dict) -> bool:
    return bool(str(row.get("platform") or "").strip()) and (
        (row.get("followers") or 0) > 0 or (row.get("posts_count") or 0) > 0
    )


def main(apply: bool) -> None:
    conn = get_conn()
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, platform, followers, posts_count, avg_views, engagement_rate,
                   primary_topic, bio, sync_status
            FROM vkpi_kol_pool
            WHERE viltrox_fit_score IS NULL
            ORDER BY id
            """,
        ).fetchall()
    ]
    scorer = ScoringRegistry.get("rule_v0")
    written = skipped = errors = 0
    for r in rows:
        if not eligible(r):
            skipped += 1
            continue
        er = r.get("engagement_rate")
        er = float(er) if er is not None else None
        er = (er / 100.0) if er is not None and er > 1 else er
        platform = str(r.get("platform") or "").lower()
        try:
            s = scorer.score(
                {
                    "platform": platform,
                    "followers": r.get("followers"),
                    "posts_count": r.get("posts_count"),
                    "avg_views": r.get("avg_views"),
                    "engagement_rate": er,
                    "primary_topic": r.get("primary_topic") or r.get("bio") or "",
                    "sync_status": r.get("sync_status") or "",
                },
                {"product_name": "Viltrox lens", "category": "camera lens", "target_platforms": [platform]},
            )
        except Exception:
            errors += 1
            continue
        reason = f"[{MARKER}] " + "; ".join(list(s.strengths or [])[:3] + list(s.concerns or [])[:2])[:480]
        if apply:
            conn.execute(
                "UPDATE vkpi_kol_pool SET viltrox_fit_score=?, viltrox_fit_reason=?, updated_at=NOW() "
                "WHERE id=? AND viltrox_fit_score IS NULL",
                (round(float(s.score), 2), reason, int(r["id"])),
            )
        written += 1
    if apply:
        conn.commit()
    mode = "APPLY" if apply else "DRY"
    print(f"[{mode}] null_rows={len(rows)} written={written} threshold_skipped={skipped} errors={errors}")


if __name__ == "__main__":
    with db_connection_sync_scope():
        main("--apply" in sys.argv)

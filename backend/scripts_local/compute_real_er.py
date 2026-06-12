"""0号 RealER 影子列计算(2026-06-12 裁定选项B)。

口径:近10条 active video evidence 的 pooled ER = Σ(likes+comments)/Σviews(views>0 行)。
只写 real_er* 影子列;rule_v0/viltrox_fit_score 零触。默认 dry-run,--execute 落库。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.connection import db_connection_sync_scope, get_conn  # noqa: E402

EXECUTE = "--execute" in sys.argv
MIN_SAMPLE = 3


def main() -> None:
    with db_connection_sync_scope():
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT e.kol_pool_id,
                   COUNT(*) AS n,
                   SUM(COALESCE(e.like_count,0) + COALESCE(e.comment_count,0)) AS engagement,
                   SUM(e.view_count) AS views
            FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY kol_pool_id
                    ORDER BY COALESCE(publish_date, posted_at::timestamptz, created_at) DESC
                ) AS rn
                FROM vkpi_kol_video_evidence
                WHERE is_active IS NOT FALSE
                  AND COALESCE(evidence_type,'video')='video'
                  AND COALESCE(view_count,0) > 0
            ) e
            WHERE e.rn <= 10
            GROUP BY e.kol_pool_id
            HAVING COUNT(*) >= ? AND SUM(e.view_count) > 0
            """,
            (MIN_SAMPLE,),
        ).fetchall()
        written = 0
        for row in rows:
            data = dict(row)
            er = round(100.0 * float(data["engagement"]) / float(data["views"]), 4)
            print(f"kol={data['kol_pool_id']} n={data['n']} real_er={er}%")
            if EXECUTE:
                conn.execute(
                    """
                    UPDATE vkpi_kol_pool
                    SET real_er=?, real_er_sample_n=?, real_er_computed_at=NOW(),
                        real_er_method='evidence10_pooled_v1'
                    WHERE id=?
                    """,
                    (er, int(data["n"]), int(data["kol_pool_id"])),
                )
                written += 1
        if EXECUTE:
            conn.commit()
            print(f"[EXECUTE] written={written}")
        else:
            print(f"[DRY-RUN] candidates={len(rows)};--execute 落库")


if __name__ == "__main__":
    main()

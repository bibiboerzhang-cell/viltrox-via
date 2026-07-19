"""primary_topic 回填(规则分类,零 LLM;2026-07-19 挂账清偿刀②)。

背景:vkpi_kol_pool.primary_topic 建列以来零写入器(1805 行全空),而 rule_v0
product 维度、外联文案、focal 矩阵等十余处读端都在吃这列。分类逻辑复用
eleven_dimensions.derive_primary_topic(industry_clusters 关键词计数,确定性幂等)。

判据:top1 cluster 命中 >=1 才写;命中 0 保持空(诚实空值,不猜)。
幂等:UPDATE 带 primary_topic 空守卫,已填行(含未来人工填写)绝不覆盖。
不触 viltrox_fit_score / rule_v0 评分公式——只补数据列。

用法:python backend/scripts/backfill_primary_topic.py [--apply](缺省 dry-run 摘要)
"""
from __future__ import annotations

import json
import sys
from collections import Counter

from app.db.connection import get_conn
from app.domains.kol.eleven_dimensions import derive_primary_topic


def main(apply: bool) -> None:
    conn = get_conn()
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, platform, handle, display_name, bio, primary_topic,
                   secondary_topics_json, content_style, recommended_product_lines_json,
                   raw_platform_data
            FROM vkpi_kol_pool
            WHERE primary_topic IS NULL OR TRIM(primary_topic) = ''
            ORDER BY id
            """,
        ).fetchall()
    ]
    written = empty = errors = 0
    dist: Counter[str] = Counter()
    for r in rows:
        try:
            topic, secondary = derive_primary_topic(r)
        except Exception as exc:  # noqa: BLE001 - 单行失败不拖垮批次
            errors += 1
            print(f"kol={r['id']} ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        if not topic:
            empty += 1
            continue
        dist[topic] += 1
        written += 1
        if apply:
            conn.execute(
                """
                UPDATE vkpi_kol_pool
                SET primary_topic = ?, secondary_topics_json = ?
                WHERE id = ? AND (primary_topic IS NULL OR TRIM(primary_topic) = '')
                """,
                (topic, json.dumps(secondary, ensure_ascii=False), int(r["id"])),
            )
    if apply:
        conn.commit()
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] candidates={len(rows)} written={written} kept_empty={empty} errors={errors}")
    for name, n in dist.most_common(20):
        print(f"  {name}: {n}")


if __name__ == "__main__":
    main(apply="--apply" in sys.argv[1:])

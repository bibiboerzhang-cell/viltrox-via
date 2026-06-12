"""C5:721 个在役 KOL 批量入收藏(战役第一段;C6 选择器切 My KOL 的铁前置)。

归属规则(dry-run 清单人审后执行):
  assignment.assigned_staff_id → 否则 project.assigned_staff_id → 否则 project.created_by_staff_id
  → 否则 DEFAULT_STAFF_ID(84,Jianbo)。同一 (kol, staff) 幂等跳过。
范围:vkpi_project_kol_assignments 中 stage NOT IN ('churned','cancelled','lost') 的全部 kol_pool_id。
红线:仅写 vkpi_kol_pool_favorites(107 表);kol_pool/projects 零写入;fit_score 零接触。

用法:
  python scripts/backfill_pool_favorites.py            # dry-run:输出人审清单 markdown 到 stdout
  python scripts/backfill_pool_favorites.py --apply    # 执行(需 107 已 apply + 清单过目令)
"""
from __future__ import annotations

import sys
from collections import defaultdict

from app.db.connection import db_connection_sync_scope, get_conn
from app.domains.kol.pool_common import _looks_like_garbage_handle

DEFAULT_STAFF_ID = 84
EXCLUDED_STAGES = ("churned", "cancelled", "lost")
# 裁决剔除(2026-06-12 清单过目):staff 40 名下 4 条为 smoke/CRUD 测试项目
# (4041 CODEX-VIDEO-VERIFY / 4042 CODEX-TIKTOK-VERIFY / 4027+4026 UI Verification
#  时间戳系列 / 3620 测试项目"21饿31"),非真实在役,不入收藏。
# 注:4026 为复核时发现的同系列漏网(pool#3778 经它仍挂 staff 40);剔后执行口径
# 781→777 对,与裁决数字吻合。涉及 KOL(1526/1579/3778)凡经真实项目在役的照常保留。
EXCLUDED_PROJECT_IDS = (4041, 4042, 4027, 4026, 3620)


def resolve_rows(conn) -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT DISTINCT ON (a.kol_pool_id, owner.staff_id)
               a.kol_pool_id,
               owner.staff_id,
               p.id AS project_id,
               p.project_name,
               kp.handle, kp.display_name, kp.platform
        FROM vkpi_project_kol_assignments a
        JOIN vkpi_projects p ON p.id = a.project_id
        JOIN vkpi_kol_pool kp ON kp.id = a.kol_pool_id
        CROSS JOIN LATERAL (
            SELECT COALESCE(a.assigned_staff_id, p.assigned_staff_id, p.created_by_staff_id, {DEFAULT_STAFF_ID}) AS staff_id
        ) owner
        WHERE COALESCE(a.stage,'') NOT IN {EXCLUDED_STAGES!r}
          AND a.project_id NOT IN {EXCLUDED_PROJECT_IDS!r}
        ORDER BY a.kol_pool_id, owner.staff_id, a.updated_at DESC
        """,
    ).fetchall()
    return [dict(r) for r in rows]


def main(apply: bool) -> None:
    conn = get_conn()
    # 逐步算术(裁决 2026-06-12,可逐步复算):
    #   781(全量) → −3(测试项目 4041/4042/4027/3620) → 778 → −1(同系列漏网 4026) → 777
    #   → −17(污染行,排除集=_looks_like_garbage_handle 同一函数,入库与 backfill 共用一个卫生标准)
    #   → 769(执行时口径终版;770/760 均作废)
    # 排除≠放生(裁决②):17 对 assignment 是真实在役关系,污染的是 KOL 行非关系;
    # P6 污染专项含"重建"条款——垃圾行净化/重建档后此 17 对补收藏。
    raw_pairs = resolve_rows(conn)
    pairs = [p for p in raw_pairs if not _looks_like_garbage_handle(str(p.get("handle") or "").strip().lower())]
    excluded_dirty = [p for p in raw_pairs if p not in pairs]
    existing = {
        (int(r["kol_pool_id"]), int(r["staff_id"]))
        for r in conn.execute("SELECT kol_pool_id, staff_id FROM vkpi_kol_pool_favorites").fetchall()
    } if _table_exists(conn) else set()

    todo = [p for p in pairs if (int(p["kol_pool_id"]), int(p["staff_id"])) not in existing]
    by_staff: dict[int, list[dict]] = defaultdict(list)
    for p in todo:
        by_staff[int(p["staff_id"])].append(p)

    if not apply:
        print(f"# C5 backfill dry-run 人审清单({len(todo)} 对 kol×staff,distinct KOL {len({p['kol_pool_id'] for p in todo})})\n")
        print("## 逐步算术(裁决 2026-06-12)")
        print(f"781(全量)→ −3(测试项目 4041/4042/4027/3620)→ 778 → −1(漏网 4026)→ {len(raw_pairs)}")
        print(f"→ −{len(excluded_dirty)}(污染行,排除集=_looks_like_garbage_handle 同一函数)→ **{len(pairs)}**\n")
        print("## 污染排除名单(排除≠放生:P6 重建后补收藏)")
        for p in excluded_dirty:
            print(f"- pool#{p['kol_pool_id']} {p['platform']} {str(p['handle'])[:36]!r} ← 项目 {p['project_id']}")
        print()
        print("归属规则:assignment.assigned_staff_id → project.assigned_staff_id → project.created_by → 84\n")
        for sid in sorted(by_staff):
            group = by_staff[sid]
            print(f"## staff {sid}({len(group)} 个 KOL)")
            for p in group[:400]:
                print(f"- pool#{p['kol_pool_id']} {p['platform']} {p['handle'] or p['display_name']} ← 项目 {p['project_id']} {str(p['project_name'] or '')[:40]}")
            if len(group) > 400:
                print(f"  …(其余 {len(group)-400} 条略)")
            print()
        return

    inserted = 0
    for p in todo:
        conn.execute(
            "INSERT INTO vkpi_kol_pool_favorites (kol_pool_id, staff_id, note) VALUES (?, ?, ?) ON CONFLICT (kol_pool_id, staff_id) DO NOTHING",
            (int(p["kol_pool_id"]), int(p["staff_id"]), f"C5 backfill ← project:{p['project_id']}"),
        )
        inserted += 1
    conn.commit()
    print(f"[APPLY] inserted={inserted} skipped_existing={len(pairs)-len(todo)}")


def _table_exists(conn) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name='vkpi_kol_pool_favorites' LIMIT 1",
    ).fetchone()
    return bool(row)


if __name__ == "__main__":
    with db_connection_sync_scope():
        main("--apply" in sys.argv)

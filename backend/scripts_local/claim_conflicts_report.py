"""乙案③:存量归属冲突清单(只读,2026-06-12)。

769+ 收藏(vkpi_kol_pool_favorites)与认领数据交叉,列出「同一 KOL 被多人收藏
且有认领冲突」的清单,写入 docs/乙案-存量归属冲突清单-20260612.md(过目件)。

口径(与 workflow_projects._pool_claim_occupancy 同源,乙案=项目维独占):
  - 认领事实独占源 = active claim(vkpi_kol_claims,经 vkpi_kol_pool.linked_main_kol_id
    join 池;预研实测命中≈0)∪ 在役 assignment 负责人;
  - 在役 assignment = stage ∉ {churned,cancelled,lost,closed} 且 stage_status ∉
    {lost,cancelled,released,deleted} 且 项目非 cancelled/deleted/restricted;
  - 冲突 = 该 KOL 被 ≥2 人收藏,且存在占用人,且(占用人 ≥2 个 或 有收藏人 ∉ 占用人集合)。

数据库零写入;唯一落盘物是报告 markdown。

用法(仓库根执行,必须 .venv 解释器):
  .venv/bin/python backend/scripts_local/claim_conflicts_report.py
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
# app.core.config 在 import 时按 cwd 读 .env;DATABASE_URL 在仓库根 .env
os.chdir(_REPO_ROOT)

from app.db.connection import get_conn  # noqa: E402

REPORT_PATH = _REPO_ROOT / "docs" / "乙案-存量归属冲突清单-20260612.md"

IN_SERVICE_SQL = """
SELECT a.kol_pool_id,
       a.assigned_staff_id,
       a.project_id,
       COALESCE(a.stage, '') AS stage,
       p.project_name
FROM vkpi_project_kol_assignments a
JOIN vkpi_projects p ON p.id = a.project_id
WHERE a.assigned_staff_id IS NOT NULL
  AND COALESCE(a.stage, '') NOT IN ('churned', 'cancelled', 'lost', 'closed')
  AND COALESCE(a.stage_status, '') NOT IN ('lost', 'cancelled', 'released', 'deleted')
  AND p.stage <> 'cancelled'
  AND COALESCE(p.stage_status, '') <> 'deleted'
  AND COALESCE(p.restricted, FALSE) = FALSE
"""

ACTIVE_CLAIMS_SQL = """
SELECT kp.id AS kol_pool_id, c.id AS claim_id, c.staff_id, c.project_id, c.claimed_at
FROM vkpi_kol_claims c
JOIN vkpi_kol_pool kp ON kp.linked_main_kol_id = c.kol_id
WHERE c.status = 'active'
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def staff_names(conn) -> dict[int, str]:
    rows = conn.execute(
        "SELECT st.id AS staff_id, u.name AS user_name FROM staff st LEFT JOIN users u ON u.id = st.user_id"
    ).fetchall()
    names: dict[int, str] = {}
    for row in rows:
        data = dict(row)
        sid = int(data.get("staff_id") or 0)
        if sid:
            names[sid] = str(data.get("user_name") or "").strip() or f"员工#{sid}"
    return names


def kol_labels(conn, pool_ids: list[int]) -> dict[int, str]:
    if not pool_ids:
        return {}
    labels: dict[int, str] = {}
    chunk = 500
    for start in range(0, len(pool_ids), chunk):
        batch = pool_ids[start:start + chunk]
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"SELECT id, COALESCE(platform, '') AS platform, COALESCE(handle, '') AS handle, "
            f"COALESCE(display_name, '') AS display_name FROM vkpi_kol_pool WHERE id IN ({placeholders})",
            batch,
        ).fetchall()
        for row in rows:
            data = dict(row)
            pid = int(data["id"])
            name = str(data.get("display_name") or "").strip() or str(data.get("handle") or "").strip() or f"#{pid}"
            platform = str(data.get("platform") or "").strip()
            handle = str(data.get("handle") or "").strip()
            labels[pid] = f"{name}({platform}/{handle})" if (platform or handle) else name
    return labels


def main() -> int:
    conn = get_conn()
    names = staff_names(conn)

    def name_of(staff_id: int) -> str:
        return names.get(int(staff_id or 0), f"员工#{staff_id}")

    fav_stats = dict(conn.execute(
        "SELECT COUNT(*) AS pairs, COUNT(DISTINCT kol_pool_id) AS kols, COUNT(DISTINCT staff_id) AS staff "
        "FROM vkpi_kol_pool_favorites"
    ).fetchone())
    claim_stats = [dict(row) for row in conn.execute(
        "SELECT COALESCE(status, '') AS status, COUNT(*) AS n FROM vkpi_kol_claims GROUP BY COALESCE(status, '') ORDER BY n DESC"
    ).fetchall()]

    favoriters: dict[int, set[int]] = defaultdict(set)
    for row in conn.execute("SELECT kol_pool_id, staff_id FROM vkpi_kol_pool_favorites").fetchall():
        data = dict(row)
        if int(data.get("staff_id") or 0):
            favoriters[int(data["kol_pool_id"])].add(int(data["staff_id"]))

    in_service: dict[int, list[dict]] = defaultdict(list)
    for row in conn.execute(IN_SERVICE_SQL).fetchall():
        data = dict(row)
        in_service[int(data["kol_pool_id"])].append({
            "staff_id": int(data["assigned_staff_id"]),
            "project_id": int(data["project_id"]),
            "project_name": str(data.get("project_name") or ""),
            "stage": str(data.get("stage") or ""),
        })

    active_claims: dict[int, list[dict]] = defaultdict(list)
    for row in conn.execute(ACTIVE_CLAIMS_SQL).fetchall():
        data = dict(row)
        active_claims[int(data["kol_pool_id"])].append({
            "claim_id": int(data["claim_id"]),
            "staff_id": int(data.get("staff_id") or 0),
            "project_id": data.get("project_id"),
            "claimed_at": str(data.get("claimed_at") or ""),
        })

    multi_fav_ids = sorted(pid for pid, sids in favoriters.items() if len(sids) >= 2)
    multi_owner_ids = sorted(
        pid for pid, rows in in_service.items() if len({item["staff_id"] for item in rows}) >= 2
    )

    conflicts: list[dict] = []
    for pid in multi_fav_ids:
        owner_rows = in_service.get(pid, [])
        claim_rows = active_claims.get(pid, [])
        owner_ids = {item["staff_id"] for item in owner_rows} | {item["staff_id"] for item in claim_rows if item["staff_id"]}
        if not owner_ids:
            continue
        fav_ids = favoriters[pid]
        outside_favs = fav_ids - owner_ids
        if len(owner_ids) < 2 and not outside_favs:
            continue
        kinds = []
        if len(owner_ids) >= 2:
            kinds.append("多 owner 在役")
        if outside_favs:
            kinds.append("非占用人收藏")
        if claim_rows:
            kinds.append("active claim 交叉")
        conflicts.append({
            "kol_pool_id": pid,
            "favoriters": sorted(fav_ids),
            "owners": owner_rows,
            "claims": claim_rows,
            "owner_ids": sorted(owner_ids),
            "kinds": kinds,
        })

    involved_ids = sorted({c["kol_pool_id"] for c in conflicts} | set(multi_owner_ids) | set(active_claims))
    labels = kol_labels(conn, involved_ids)

    lines: list[str] = []
    lines.append("# 乙案 · 存量归属冲突清单(过目件)")
    lines.append("")
    lines.append(f"- 生成:{utcnow()} · `backend/scripts_local/claim_conflicts_report.py`(只读,数据库零写入)")
    lines.append("- 口径:独占体制乙案=项目维;事实独占源 = active claim(vkpi_kol_claims 经 linked_main_kol_id)∪ 在役 assignment 负责人(stage ∉ churned/cancelled/lost/closed,stage_status ∉ lost/cancelled/released/deleted,项目非 cancelled/deleted/restricted)。")
    lines.append("- 冲突定义:同一 KOL 被 ≥2 人收藏,且存在占用人,且(占用人 ≥2 或 有收藏人不在占用人集合)。")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append(f"- 收藏对数 / 去重 KOL / 收藏人数:**{fav_stats.get('pairs')} / {fav_stats.get('kols')} / {fav_stats.get('staff')}**")
    claim_summary = "、".join(f"{row['status'] or '(空)'}={row['n']}" for row in claim_stats) or "0 行"
    lines.append(f"- vkpi_kol_claims 状态分布:{claim_summary}(经 linked_main_kol_id 命中池行的 active claim:{sum(len(v) for v in active_claims.values())} 条)")
    lines.append(f"- 被 ≥2 人收藏的 KOL:**{len(multi_fav_ids)}** 个;多 owner 在役 KOL:**{len(multi_owner_ids)}** 个")
    lines.append(f"- **主清单(多人收藏 × 认领/在役占用冲突):{len(conflicts)} 个 KOL**")
    lines.append("")
    lines.append("## 主清单:同一 KOL 被多人收藏且有认领冲突")
    lines.append("")
    if conflicts:
        lines.append("| kol_pool_id | KOL | 收藏人 | 在役占用(负责人 → 项目/阶段) | active claim | 冲突类型 |")
        lines.append("|---|---|---|---|---|---|")
        for item in conflicts:
            pid = item["kol_pool_id"]
            fav_text = "、".join(name_of(sid) for sid in item["favoriters"])
            owner_text = "<br>".join(
                f"{name_of(o['staff_id'])} → #{o['project_id']} {o['project_name']}({o['stage']})"
                for o in item["owners"]
            ) or "-"
            claim_text = "<br>".join(
                f"claim#{c['claim_id']} {name_of(c['staff_id'])}" for c in item["claims"]
            ) or "-"
            lines.append(
                f"| {pid} | {labels.get(pid, f'#{pid}')} | {fav_text} | {owner_text} | {claim_text} | {'、'.join(item['kinds'])} |"
            )
    else:
        lines.append("(无命中——当前没有「被多人收藏且占用冲突」的 KOL。)")
    lines.append("")
    lines.append("## 附录 A:多 owner 在役 KOL 全清单(预研在册 55 个口径,无论是否被收藏)")
    lines.append("")
    if multi_owner_ids:
        lines.append("| kol_pool_id | KOL | 在役负责人 → 项目/阶段 | 收藏人 |")
        lines.append("|---|---|---|---|")
        for pid in multi_owner_ids:
            owner_text = "<br>".join(
                f"{name_of(o['staff_id'])} → #{o['project_id']} {o['project_name']}({o['stage']})"
                for o in in_service.get(pid, [])
            )
            fav_text = "、".join(name_of(sid) for sid in sorted(favoriters.get(pid, set()))) or "-"
            lines.append(f"| {pid} | {labels.get(pid, f'#{pid}')} | {owner_text} | {fav_text} |")
    else:
        lines.append("(无多 owner 在役 KOL。)")
    lines.append("")
    lines.append("## 附录 B:active claim × 池行交叉明细")
    lines.append("")
    if active_claims:
        lines.append("| kol_pool_id | KOL | claim_id | 认领人 | claimed_at |")
        lines.append("|---|---|---|---|---|")
        for pid in sorted(active_claims):
            for c in active_claims[pid]:
                lines.append(
                    f"| {pid} | {labels.get(pid, f'#{pid}')} | {c['claim_id']} | {name_of(c['staff_id'])} | {c['claimed_at']} |"
                )
    else:
        lines.append("(active claim 经 linked_main_kol_id 命中池行:0 条——与预研「claims 表不可用作独占数据源」一致。)")
    lines.append("")
    lines.append("> 处置建议(乙案 B 口径):主清单为「撞车外联」风险点——选择器置灰 + 写入侧防绕过已上线,存量不做归属手术;多 owner 在役即跨项目共跟进的业务现实,合法保留,仅标示。")
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"report written: {REPORT_PATH}")
    print(f"favorites pairs={fav_stats.get('pairs')} kols={fav_stats.get('kols')} staff={fav_stats.get('staff')}")
    print(f"multi-favorited KOLs={len(multi_fav_ids)} multi-owner in-service KOLs={len(multi_owner_ids)}")
    print(f"conflict KOLs={len(conflicts)} active-claim pool hits={sum(len(v) for v in active_claims.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

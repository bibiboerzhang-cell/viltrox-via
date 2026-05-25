"""scripts/smoke_vkpi_kol_pool_import.py

R59 smoke: 验证 kol_pool.import_items 真实工作.

测试场景:
  1. 单条 Apify 风格数据导入 → vkpi_kol_pool 多 1 行
  2. 多条数据批量导入 → 多 N 行
  3. 重复 platform/handle → ON CONFLICT 触发 UPDATE,不增行
  4. 缺 handle 的数据 → skipped 计数
  5. 字段映射: avg_views / followers / engagement_rate 都进表
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from _smoke_seed import seed_admin, cleanup_admin
except ImportError:
    seed_admin = None
    cleanup_admin = None

from app.db.connection import get_conn
from app.domains.kol import pool as kol_pool


PREFIX = "vkpi-pool-imp-"
MARKER = f"{PREFIX}{int(time.time())}"


def main() -> None:
    conn = get_conn()
    user_id = 0
    staff_id = 0
    
    if seed_admin:
        user_id, staff_id = seed_admin(conn, marker=MARKER)
        print(f"[seed] user_id={user_id} staff_id={staff_id}")
    else:
        print("[seed] _smoke_seed.py missing")
        sys.exit(1)
    
    failures: list[str] = []
    
    # 测试用 handle (带 marker 便于 cleanup)
    h1 = f"{MARKER}-handle-1"
    h2 = f"{MARKER}-handle-2"
    h3 = f"{MARKER}-handle-3"
    
    baseline_count = _count_pool()
    print(f"[baseline] kol_pool count = {baseline_count}")
    
    # ── 场景 1: 单条 Apify 风格导入 ──
    print("[1/5] 单条 Apify 风格数据 (Instagram)")
    apify_item = {
        "platform": "instagram",
        "username": h1,
        "fullName": "Test User 1",
        "biography": "Test bio",
        "followersCount": 12345,
        "averageViews": 5000,
        "averageLikes": 200,
        "engagementRate": 1.62,
        "publicEmail": f"{h1}@example.com",
    }
    result = kol_pool.import_items(
        [apify_item],
        source_type="apify",
        source_ref=MARKER,
        platform="instagram",
        staff={"id": staff_id, "is_owner": True},
    )
    if result["imported"] != 1:
        failures.append(f"场景 1: 期望 imported=1,实际 {result}")
    else:
        # 验证字段映射
        row = _get_pool_row("instagram", h1)
        if not row:
            failures.append("场景 1: 导入后查不到")
        elif row.get("followers") != 12345:
            failures.append(f"场景 1: followers 字段映射错 {row.get('followers')}")
        elif row.get("avg_views") != 5000:
            failures.append(f"场景 1: avg_views 字段映射错 {row.get('avg_views')}")
        elif row.get("email") != f"{h1}@example.com":
            failures.append(f"场景 1: email 字段映射错 {row.get('email')}")
        else:
            print(f"   PASS: id={row['id']} followers={row['followers']} avg_views={row['avg_views']}")
    
    # ── 场景 2: 多条批量导入 ──
    print("[2/5] 批量 2 条 (TikTok + YouTube)")
    items = [
        {
            "platform": "tiktok",
            "username": h2,
            "name": "TT User",
            "followersCount": 5000,
        },
        {
            "platform": "youtube",
            "channelName": h3,
            "subscriberCount": 100000,
            "videoCount": 250,
            "channelUrl": f"https://youtube.com/@{h3}",
        },
    ]
    result = kol_pool.import_items(
        items,
        source_type="apify",
        source_ref=MARKER,
        staff={"id": staff_id, "is_owner": True},
    )
    if result["imported"] != 2:
        failures.append(f"场景 2: 期望 imported=2,实际 {result}")
    else:
        print(f"   PASS: imported=2")
    
    # ── 场景 3: 重复 platform+handle → ON CONFLICT UPDATE,不增行 ──
    print("[3/5] 重复导入,期望 ON CONFLICT UPDATE")
    pre_count = _count_pool()
    
    # 重导 h1,但改 followers
    apify_item_v2 = dict(apify_item)
    apify_item_v2["followersCount"] = 99999  # 期望被 UPDATE 覆盖
    
    result = kol_pool.import_items(
        [apify_item_v2],
        source_type="apify",
        source_ref=MARKER,
        platform="instagram",
        staff={"id": staff_id, "is_owner": True},
    )
    
    post_count = _count_pool()
    if post_count != pre_count:
        failures.append(f"场景 3: 期望不增行,实际 {pre_count}→{post_count}")
    else:
        # 验证字段被 UPDATE
        row = _get_pool_row("instagram", h1)
        if row.get("followers") != 99999:
            failures.append(f"场景 3: UPDATE 没生效 followers={row.get('followers')}")
        else:
            print(f"   PASS: 不增行,followers 从 12345 → 99999")
    
    # ── 场景 4: 缺 handle 跳过 ──
    print("[4/5] 缺 handle 的数据应该 skipped")
    bad_items = [
        {"platform": "instagram", "fullName": "no handle"},  # 缺 username
        {"platform": "tiktok", "username": ""},  # 空 handle
    ]
    result = kol_pool.import_items(
        bad_items,
        source_type="apify",
        source_ref=MARKER,
        staff={"id": staff_id, "is_owner": True},
    )
    if result["imported"] != 0:
        failures.append(f"场景 4: 期望 imported=0,实际 {result}")
    elif result["skipped"] != 2:
        failures.append(f"场景 4: 期望 skipped=2,实际 {result}")
    else:
        print(f"   PASS: imported=0 skipped=2")
    
    # ── 场景 5: list_pool 能查到 ──
    print("[5/5] list_pool 能列出导入的 KOL")
    listed = kol_pool.list_pool(limit=100, query=MARKER)
    items_count = len(listed.get("items", []))
    if items_count < 3:
        failures.append(f"场景 5: 期望 >=3 行 marker 数据,实际 {items_count}")
    else:
        print(f"   PASS: 列出 {items_count} 行 marker 数据")
    
    # ── cleanup ──
    print("\n[cleanup]")
    deleted = _cleanup_pool(MARKER)
    print(f"  deleted {deleted} pool rows")
    if cleanup_admin and staff_id:
        cleanup_admin(conn, user_id=user_id, staff_id=staff_id)
    
    # ── 总结 ──
    if failures:
        print("\n=== FAIL ===")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("\nVKPI_KOL_POOL_IMPORT_SMOKE_OK")
        sys.exit(0)


# ─── 辅助 ─────────────────────────────────────────


def _count_pool() -> int:
    row = get_conn().execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone()
    return int(row["n"]) if row else 0


def _get_pool_row(platform: str, handle: str) -> dict:
    row = get_conn().execute(
        "SELECT * FROM vkpi_kol_pool WHERE platform=? AND handle=?",
        (platform, handle.lower()),
    ).fetchone()
    return dict(row) if row else {}


def _cleanup_pool(marker: str) -> int:
    """删 source_ref=marker 的行"""
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM vkpi_kol_pool WHERE source_ref=?",
        (marker,),
    )
    conn.commit()
    return cur.rowcount if hasattr(cur, "rowcount") else 0


if __name__ == "__main__":
    main()

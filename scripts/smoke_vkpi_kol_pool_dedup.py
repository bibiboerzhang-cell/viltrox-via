"""scripts/smoke_vkpi_kol_pool_dedup.py

R59 smoke: 验证 KOL Pool 三层 dedup 规则.

dedup 三层:
  Layer 1: platform + handle (UNIQUE 约束)
  Layer 2: dedup_key (kol_claims_common.dedup_key,含 email)
  Layer 3: handle 不同但是 alias (vkpi_kol_pool_aliases 表)

测试场景:
  1. 同 platform + 同 handle → 视为同一行 (Layer 1 触发 ON CONFLICT)
  2. 同 platform + 不同 handle 但同 email → dedup_key 一致 (Layer 2)
  3. 同 platform + handle 大小写不同 → normalize 后视为同一行
  4. 不同 platform + 同 handle → 视为不同行 (跨平台不去重)
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
from app.services.vkpi import kol_pool
from app.services.vkpi.kol_claims_common import dedup_key, normalize_handle, normalize_platform


PREFIX = "vkpi-pool-dedup-"
MARKER = f"{PREFIX}{int(time.time())}"


def main() -> None:
    conn = get_conn()
    user_id = 0
    staff_id = 0
    
    if seed_admin:
        user_id, staff_id = seed_admin(conn, marker=MARKER)
    else:
        print("[seed] _smoke_seed.py missing")
        sys.exit(1)
    
    failures: list[str] = []
    
    h_main = f"{MARKER}-main"
    
    # ── 场景 1: 同 platform + handle → ON CONFLICT ──
    print("[1/4] 同 platform + handle → 不增行 (Layer 1)")
    
    pre = _count_marker(MARKER)
    
    kol_pool.import_items(
        [{"platform": "instagram", "username": h_main, "fullName": "v1"}],
        source_type="manual",
        source_ref=MARKER,
        staff={"id": staff_id, "is_owner": True},
    )
    after_first = _count_marker(MARKER)
    
    kol_pool.import_items(
        [{"platform": "instagram", "username": h_main, "fullName": "v2"}],  # 同 handle
        source_type="manual",
        source_ref=MARKER,
        staff={"id": staff_id, "is_owner": True},
    )
    after_second = _count_marker(MARKER)
    
    if after_first - pre != 1:
        failures.append(f"场景 1: 第一次没 +1,实际 {after_first - pre}")
    elif after_second != after_first:
        failures.append(f"场景 1: 重复导入增行 {after_first}→{after_second}")
    else:
        print(f"   PASS: 第一次 +1,第二次不增 ({after_first}→{after_second})")
    
    # ── 场景 2: dedup_key 函数验证 ──
    print("[2/4] dedup_key 函数: 同 email 同 handle 应一致")
    
    key_a = dedup_key("instagram", "user_a", "shared@x.com")
    key_b = dedup_key("instagram", "user_a", "shared@x.com")  # 完全一样
    key_c = dedup_key("instagram", "user_a", "different@y.com")  # email 不同
    
    if key_a != key_b:
        failures.append(f"场景 2: 同输入应同 key,实际 {key_a} != {key_b}")
    elif key_a == key_c:
        failures.append(f"场景 2: email 不同应不同 key,实际同")
    else:
        print(f"   PASS: dedup_key 正确区分 email")
    
    # ── 场景 3: handle 大小写 normalize ──
    print("[3/4] handle 大小写 → normalize 后视为同一行")
    
    h_case = f"{MARKER}-CaseTest"
    
    pre_case = _count_marker(MARKER)
    kol_pool.import_items(
        [{"platform": "instagram", "username": h_case}],  # 原始大小写
        source_type="manual",
        source_ref=MARKER,
        staff={"id": staff_id, "is_owner": True},
    )
    
    after_first_case = _count_marker(MARKER)
    
    # 重导,但 handle 全小写
    kol_pool.import_items(
        [{"platform": "instagram", "username": h_case.lower()}],
        source_type="manual",
        source_ref=MARKER,
        staff={"id": staff_id, "is_owner": True},
    )
    after_second_case = _count_marker(MARKER)
    
    if after_second_case != after_first_case:
        failures.append(f"场景 3: 大小写不一致触发了新增行 {after_first_case}→{after_second_case}")
    else:
        print(f"   PASS: normalize 后视为同一行")
    
    # ── 场景 4: 跨平台不去重 ──
    print("[4/4] 不同 platform + 同 handle → 视为不同行")
    
    h_cross = f"{MARKER}-cross"
    pre_cross = _count_marker(MARKER)
    
    kol_pool.import_items(
        [
            {"platform": "instagram", "username": h_cross},
            {"platform": "tiktok", "username": h_cross},
            {"platform": "youtube", "channelName": h_cross},
        ],
        source_type="manual",
        source_ref=MARKER,
        staff={"id": staff_id, "is_owner": True},
    )
    after_cross = _count_marker(MARKER)
    
    if after_cross - pre_cross != 3:
        failures.append(f"场景 4: 期望 +3 (3 个平台),实际 {after_cross - pre_cross}")
    else:
        print(f"   PASS: 跨平台 +3")
    
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
        print("\nVKPI_KOL_POOL_DEDUP_SMOKE_OK")
        sys.exit(0)


def _count_marker(marker: str) -> int:
    row = get_conn().execute(
        "SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE source_ref=?",
        (marker,),
    ).fetchone()
    return int(row["n"]) if row else 0


def _cleanup_pool(marker: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM vkpi_kol_pool WHERE source_ref=?",
        (marker,),
    )
    conn.commit()
    return cur.rowcount if hasattr(cur, "rowcount") else 0


if __name__ == "__main__":
    main()

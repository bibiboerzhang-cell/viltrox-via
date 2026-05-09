"""scripts/migrate_admin_permissions.py

R59-FW-PERM: 一次性数据迁移 - 把现有 admin role staff 的 permissions_json
里 "write" 级别的 tab 升级到 "admin" 级别.

为什么需要:
  R59-FW-PERM 修了 _level_allows 让 admin level 真生效.
  但现有 admin role staff 的 permissions_json 里很多 tab 是 "write".
  如果不迁移,他们会突然失去 require_tab(*, "admin") 的接口访问能力.
  
  例如:
    现有 staff role=admin, permissions={"vkpi": "write"}
    修复后 require_tab("vkpi", "admin") 会拒绝他 (因为他是 write 不是 admin)
    → 25 个 admin 接口对他失效

迁移规则:
  1. 找所有 role="admin" 的 staff
  2. 把 permissions_json 里的 TAB key (vkpi/operations/etc) 从 "write" 升级到 "admin"
  3. 不动 OWNER_ONLY_SYSTEM_KEYS (api_keys/models/restart/members) 字段
  4. 不动 system.usage 字段
  5. 不动 manager/employee/viewer role 的 staff

模式:
  --dry-run: 只打印影响,不写库 (推荐先跑)
  --apply:   真实写入

使用:
  PYTHONPATH=backend .venv/bin/python scripts/migrate_admin_permissions.py --dry-run
  PYTHONPATH=backend .venv/bin/python scripts/migrate_admin_permissions.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.connection import get_conn
from app.core.permissions import (
    TAB_PERMISSION_KEYS,
    OWNER_ONLY_SYSTEM_KEYS,
)


def _parse_perms(raw: Any) -> dict[str, str]:
    """安全 parse permissions_json"""
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except json.JSONDecodeError:
            pass
    return {}


def _upgrade_perms(current: dict[str, str]) -> dict[str, str]:
    """
    升级规则:
      - TAB key 中 value="write" 的 → "admin"
      - 其他保持不变
      - OWNER_ONLY_SYSTEM_KEYS / system.usage 不动
      - 其他 system.* 也不动
    """
    upgraded = dict(current)
    for tab in TAB_PERMISSION_KEYS:
        if upgraded.get(tab) == "write":
            upgraded[tab] = "admin"
    return upgraded


def _diff_changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """生成 before/after diff 描述"""
    changes = []
    for key in sorted(set(before) | set(after)):
        b = before.get(key, "—")
        a = after.get(key, "—")
        if b != a:
            changes.append(f"{key}: {b} → {a}")
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description="R59-FW-PERM admin permissions migration")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="预览影响,不写库")
    mode.add_argument("--apply", action="store_true", help="实际执行迁移")
    args = parser.parse_args()
    
    conn = get_conn()
    
    # 1. 找所有 role="admin" 的 staff (active 且非 owner)
    print("═══════════════════════════════════════")
    print("  R59-FW-PERM: admin permissions migration")
    print("═══════════════════════════════════════")
    print()
    print(f"模式: {'DRY-RUN (不写库)' if args.dry_run else 'APPLY (实际执行)'}")
    print()
    
    rows = conn.execute(
        """
        SELECT s.id, s.user_id, s.role, s.permissions_json,
               COALESCE(s.is_owner, 0) AS is_owner,
               COALESCE(s.active, 0) AS active,
               u.email AS email
        FROM staff s
        LEFT JOIN users u ON u.id = s.user_id
        WHERE LOWER(COALESCE(s.role, '')) = 'admin'
          AND COALESCE(s.active, 0) = 1
        ORDER BY s.id
        """
    ).fetchall()
    
    if not rows:
        print("[info] 没有 active admin role staff,无需迁移")
        print()
        print("═══════════════════════════════════════")
        print("  完成: 影响 0 个 staff")
        print("═══════════════════════════════════════")
        return
    
    print(f"[info] 找到 {len(rows)} 个 active admin role staff")
    print()
    
    # 2. 分析每个 staff 的迁移影响
    affected = []
    skipped_owner = []
    skipped_no_change = []
    
    for row in rows:
        row_dict = dict(row)
        staff_id = int(row_dict["id"])
        email = row_dict.get("email") or "(no email)"
        is_owner = bool(int(row_dict.get("is_owner") or 0))
        
        if is_owner:
            skipped_owner.append((staff_id, email))
            continue
        
        current = _parse_perms(row_dict.get("permissions_json"))
        upgraded = _upgrade_perms(current)
        changes = _diff_changes(current, upgraded)
        
        if not changes:
            skipped_no_change.append((staff_id, email))
            continue
        
        affected.append((staff_id, email, current, upgraded, changes))
    
    # 3. 打印影响报告
    print("─── 跳过 (owner) ───")
    for sid, email in skipped_owner:
        print(f"  staff_id={sid} email={email} (owner bypass)")
    print(f"  小计: {len(skipped_owner)} 个")
    print()
    
    print("─── 跳过 (无变化) ───")
    for sid, email in skipped_no_change:
        print(f"  staff_id={sid} email={email}")
    print(f"  小计: {len(skipped_no_change)} 个")
    print()
    
    print("─── 待迁移 ───")
    for sid, email, before, after, changes in affected:
        print(f"  staff_id={sid} email={email}")
        for change in changes:
            print(f"    {change}")
    print(f"  小计: {len(affected)} 个")
    print()
    
    if not affected:
        print("═══════════════════════════════════════")
        print("  完成: 没有 staff 需要迁移")
        print("═══════════════════════════════════════")
        return
    
    # 4. dry-run 不写库,直接退出
    if args.dry_run:
        print("═══════════════════════════════════════")
        print(f"  DRY-RUN 完成: {len(affected)} 个 staff 待迁移")
        print(f"  实际执行请加 --apply")
        print("═══════════════════════════════════════")
        return
    
    # 5. apply 模式 - 真实写入
    print("─── APPLY 实际执行 ───")
    success = 0
    failed = 0
    
    for sid, email, before, after, _changes in affected:
        try:
            conn.execute(
                "UPDATE staff SET permissions_json=? WHERE id=?",
                (json.dumps(after, ensure_ascii=False), sid),
            )
            success += 1
            print(f"  ✓ staff_id={sid} email={email} 已升级")
        except Exception as exc:
            failed += 1
            print(f"  ✗ staff_id={sid} email={email} 失败: {exc}")
    
    conn.commit()
    
    print()
    print("═══════════════════════════════════════")
    print(f"  迁移完成: 成功 {success} / 失败 {failed}")
    print("═══════════════════════════════════════")
    
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

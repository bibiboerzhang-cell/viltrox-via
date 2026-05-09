"""scripts/smoke_vkpi_admin_permission.py

R59-FW-PERM smoke: 验证 admin level 真生效.

测试场景:
  1. is_owner=True 通过 require_tab(*, "admin") (短路)
  2. role="admin" + permissions[vkpi]="admin" 通过
  3. role="admin" + permissions[vkpi]="write" 拒绝 (修复前能过,修复后不能)
  4. role="employee" + permissions[vkpi]="write" 拒绝 (admin level 拒绝)
  5. role="employee" + permissions[vkpi]="write" 通过 require_tab(*, "write")
  6. _level_allows admin/write/read 三层关系正确

不依赖真实 HTTP server,直接调 check_tab_permission.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.permissions import (
    _level_allows,
    check_tab_permission,
    normalize_permissions,
    default_permissions_for_role,
)


PREFIX = "vkpi-admin-perm-"
MARKER = f"{PREFIX}{int(time.time())}"


def main() -> None:
    failures: list[str] = []
    
    # ── 场景 0: _level_allows 三层关系 ──
    print("[0/6] _level_allows 三层关系")
    
    # read 接受所有 (read/write/admin)
    for value in ["read", "write", "admin"]:
        if not _level_allows(value, "read"):
            failures.append(f"_level_allows({value!r}, 'read') 应返回 True")
    if _level_allows("none", "read"):
        failures.append("_level_allows('none', 'read') 应返回 False")
    
    # write 接受 (write/admin),拒绝 (read/none)
    if not _level_allows("write", "write"):
        failures.append("_level_allows('write', 'write') 应返回 True")
    if not _level_allows("admin", "write"):
        failures.append("_level_allows('admin', 'write') 应返回 True")
    if _level_allows("read", "write"):
        failures.append("_level_allows('read', 'write') 应返回 False (R59-FW-PERM 修正)")
    
    # admin 只接受 admin
    if not _level_allows("admin", "admin"):
        failures.append("_level_allows('admin', 'admin') 应返回 True")
    if _level_allows("write", "admin"):
        failures.append("_level_allows('write', 'admin') 应返回 False (R59-FW-PERM 修正,历史 bug)")
    if _level_allows("read", "admin"):
        failures.append("_level_allows('read', 'admin') 应返回 False")
    if _level_allows("none", "admin"):
        failures.append("_level_allows('none', 'admin') 应返回 False")
    
    if not failures:
        print("   PASS: read < write < admin 层级正确")
    
    # ── 场景 1: is_owner=True 短路 admin level ──
    print("[1/6] is_owner=True 通过 require_tab(*, 'admin')")
    owner_staff = {"is_owner": 1, "role": "admin", "permissions_json": "{}"}
    if not check_tab_permission(owner_staff, "vkpi", "admin"):
        failures.append("场景 1: owner 应通过 admin level")
    else:
        print("   PASS: owner 短路 admin level")
    
    # ── 场景 2: role=admin + permissions[vkpi]=admin 通过 ──
    print("[2/6] role=admin + permissions[vkpi]=admin 通过")
    admin_staff = {
        "is_owner": 0,
        "role": "admin",
        "permissions_json": json.dumps({"vkpi": "admin"}),
    }
    if not check_tab_permission(admin_staff, "vkpi", "admin"):
        failures.append(f"场景 2: admin role + permission=admin 应通过 admin level")
    else:
        print("   PASS: admin role + admin permission 通过")
    
    # ── 场景 3: role=admin + permissions[vkpi]=write 拒绝 admin level ──
    print("[3/6] role=admin + permissions[vkpi]=write 拒绝 admin level")
    
    # 注意: default_permissions_for_role 给 admin role 默认 admin
    # 但 permissions_json 显式覆盖为 write,所以最终 permission["vkpi"] = "write"
    admin_write_staff = {
        "is_owner": 0,
        "role": "admin",
        "permissions_json": json.dumps({"vkpi": "write"}),  # 显式覆盖到 write
    }
    if check_tab_permission(admin_write_staff, "vkpi", "admin"):
        failures.append(
            f"场景 3: admin role 但 vkpi=write,不应通过 admin level "
            f"(R59-FW-PERM 修复前会通过 = 历史 bug)"
        )
    else:
        # 但应该通过 write level
        if not check_tab_permission(admin_write_staff, "vkpi", "write"):
            failures.append("场景 3: admin role + write permission 应通过 write level")
        else:
            print("   PASS: 显式 write 不通过 admin,但通过 write")
    
    # ── 场景 4: role=employee + permissions[vkpi]=write 拒绝 admin level ──
    print("[4/6] role=employee + permissions[vkpi]=write 拒绝 admin level")
    employee_staff = {
        "is_owner": 0,
        "role": "employee",
        "permissions_json": json.dumps({"vkpi": "write"}),
    }
    if check_tab_permission(employee_staff, "vkpi", "admin"):
        failures.append(
            "场景 4: employee role + vkpi=write,不应通过 admin level "
            "(R59-FW-PERM 修复前会通过 = 历史 bug,普通员工能改预算)"
        )
    else:
        print("   PASS: employee + write 拒绝 admin level")
    
    # ── 场景 5: 普通员工 write level 仍通过 ──
    print("[5/6] role=employee + write 通过 write level")
    if not check_tab_permission(employee_staff, "vkpi", "write"):
        failures.append("场景 5: employee + write permission 应通过 write level")
    else:
        print("   PASS: employee write 通过")
    
    # ── 场景 6: default_permissions_for_role admin 默认值 ──
    print("[6/6] admin role 默认 permissions 是 admin level")
    defaults = default_permissions_for_role("admin")
    if defaults.get("vkpi") != "admin":
        failures.append(
            f"场景 6: admin role 默认 vkpi 应为 'admin',实际 {defaults.get('vkpi')!r}"
        )
    elif defaults.get("operations") != "admin":
        failures.append(
            f"场景 6: admin role 默认 operations 应为 'admin',实际 {defaults.get('operations')!r}"
        )
    elif defaults.get("system.api_keys") not in ("read", "none"):
        failures.append(
            f"场景 6: admin role api_keys 应为 'read' (OWNER_ONLY 降级),实际 {defaults.get('system.api_keys')!r}"
        )
    else:
        print(f"   PASS: admin role 默认 vkpi=admin, api_keys={defaults['system.api_keys']}")
    
    # ── 总结 ──
    if failures:
        print("\n=== FAIL ===")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("\nVKPI_ADMIN_PERMISSION_SMOKE_OK")
        sys.exit(0)


if __name__ == "__main__":
    main()

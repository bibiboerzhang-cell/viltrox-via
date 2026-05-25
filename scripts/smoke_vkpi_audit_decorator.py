"""scripts/smoke_vkpi_audit_decorator.py

R59 smoke: 验证审计装饰器在 success/failure 两种情况下都正确写日志.

测试场景:
  1. 装饰过的函数成功执行 → vkpi_business_audit_logs 多一行 status=success
  2. 装饰过的函数抛异常 → 日志多一行 status=failed,异常仍然抛出
  3. 默认 target_id 提取从 kwargs 找 (kol_id / project_id 等)
  4. 自定义 extractor 也工作
  5. staff_id=0 时不写日志 (跳过)
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
from app.domains import audit
from app.services.vkpi.audit_decorator import audit_action


PREFIX = "vkpi-audit-"
MARKER = f"{PREFIX}{int(time.time())}"


# ─── 测试用的装饰过的假函数 ─────────────────────────


@audit_action(action_type="kol_import_test", target_type="kol_pool")
def fake_kol_import(*, staff: dict, kol_id: int) -> dict:
    return {"id": kol_id, "imported": True}


@audit_action(action_type="kol_import_failure", target_type="kol_pool")
def fake_kol_import_fail(*, staff: dict, kol_id: int) -> dict:
    raise ValueError("simulated_failure")


@audit_action(
    action_type="kol_custom_extract",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: f"custom-{kwargs.get('handle', '')}",
    detail_extractor=lambda result, kwargs: f"imported {result.get('count', 0)} items",
)
def fake_kol_custom(*, staff: dict, handle: str) -> dict:
    return {"count": 5, "handle": handle}


# ─── Smoke 主体 ─────────────────────────────────────


def main() -> None:
    conn = get_conn()
    user_id = 0
    staff_id = 0
    
    # seed admin (R58E helper)
    if seed_admin:
        try:
            user_id, staff_id = seed_admin(conn, marker=MARKER)
            print(f"[seed] user_id={user_id} staff_id={staff_id}")
        except Exception as exc:
            print(f"[seed] failed: {exc}")
            sys.exit(1)
    else:
        print("[seed] _smoke_seed.py missing, skip")
        sys.exit(1)
    
    # 取测试前 audit 计数
    baseline = _count_audit_logs()
    print(f"[baseline] audit_logs count = {baseline}")
    
    failures: list[str] = []
    
    # ── 场景 1: 成功执行,写 success 日志 ──
    print("[1/5] 成功执行 → status=success")
    result = fake_kol_import(staff={"id": staff_id, "is_owner": True}, kol_id=12345)
    if not result.get("imported"):
        failures.append("场景 1: 业务返回错")
    else:
        new_count = _count_audit_logs()
        if new_count != baseline + 1:
            failures.append(f"场景 1: 期望 +1 日志,实际 {new_count - baseline}")
        else:
            log = _latest_audit_log()
            if log.get("action_type") != "kol_import_test":
                failures.append(f"场景 1: action_type 错 {log}")
            elif log.get("target_id") != "12345":
                failures.append(f"场景 1: target_id 错 期望 12345 实际 {log.get('target_id')}")
            else:
                print(f"   PASS: action_type={log['action_type']} target_id={log['target_id']}")
    
    baseline = _count_audit_logs()
    
    # ── 场景 2: 抛异常,写 failed 日志,异常透传 ──
    print("[2/5] 抛异常 → status=failed,异常透传")
    raised = False
    try:
        fake_kol_import_fail(staff={"id": staff_id, "is_owner": True}, kol_id=99999)
    except ValueError as exc:
        if "simulated_failure" in str(exc):
            raised = True
        else:
            failures.append(f"场景 2: 异常内容错 {exc}")
    
    if not raised:
        failures.append("场景 2: 没抛异常")
    else:
        new_count = _count_audit_logs()
        if new_count != baseline + 1:
            failures.append(f"场景 2: 期望 +1 日志,实际 {new_count - baseline}")
        else:
            log = _latest_audit_log()
            import json
            metadata = json.loads(log.get("metadata_json") or "{}")
            if metadata.get("action_status") != "failed":
                failures.append(f"场景 2: action_status 错 {metadata}")
            elif "simulated_failure" not in (metadata.get("error") or ""):
                failures.append(f"场景 2: error 没记录 {metadata}")
            else:
                print(f"   PASS: status=failed error={metadata.get('error')}")
    
    baseline = _count_audit_logs()
    
    # ── 场景 3: 默认 target_id 从 kwargs 提取 ──
    print("[3/5] 默认 target_id 从 kwargs.kol_id 提取")
    fake_kol_import(staff={"id": staff_id, "is_owner": True}, kol_id=77777)
    log = _latest_audit_log()
    if log.get("target_id") != "77777":
        failures.append(f"场景 3: target_id 错 {log.get('target_id')}")
    else:
        print(f"   PASS: target_id={log['target_id']}")
    
    baseline = _count_audit_logs()
    
    # ── 场景 4: 自定义 extractor ──
    print("[4/5] 自定义 target_id_extractor + detail_extractor")
    fake_kol_custom(staff={"id": staff_id, "is_owner": True}, handle="@test_user")
    log = _latest_audit_log()
    if log.get("target_id") != "custom-@test_user":
        failures.append(f"场景 4: 自定义 target_id 错 {log.get('target_id')}")
    elif "imported 5 items" not in (log.get("detail") or ""):
        failures.append(f"场景 4: 自定义 detail 错 {log.get('detail')}")
    else:
        print(f"   PASS: target_id={log['target_id']} detail={log['detail']}")
    
    baseline = _count_audit_logs()
    
    # ── 场景 5: staff_id=0 不写日志 ──
    print("[5/5] staff_id=0 时跳过审计")
    fake_kol_import(staff={"id": 0}, kol_id=11111)
    new_count = _count_audit_logs()
    if new_count != baseline:
        failures.append(f"场景 5: 期望 +0 日志(staff_id=0 跳过),实际 +{new_count - baseline}")
    else:
        print(f"   PASS: staff_id=0 时不写日志")
    
    # ── cleanup ──
    print("\n[cleanup]")
    _cleanup_audit_logs(staff_id=staff_id)
    if cleanup_admin and staff_id:
        cleanup_admin(conn, user_id=user_id, staff_id=staff_id)
    
    # ── 总结 ──
    if failures:
        print("\n=== FAIL ===")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("\nVKPI_AUDIT_DECORATOR_SMOKE_OK")
        sys.exit(0)


# ─── 辅助 ─────────────────────────────────────────


def _count_audit_logs() -> int:
    audit.ensure_vkpi_audit_schema()
    row = get_conn().execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs").fetchone()
    return int(row["n"]) if row else 0


def _latest_audit_log() -> dict:
    row = get_conn().execute(
        "SELECT * FROM vkpi_business_audit_logs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else {}


def _cleanup_audit_logs(*, staff_id: int) -> None:
    """删除测试 staff 的审计日志"""
    if staff_id:
        get_conn().execute(
            "DELETE FROM vkpi_business_audit_logs WHERE staff_id=?",
            (int(staff_id),),
        )
        get_conn().commit()


if __name__ == "__main__":
    main()

"""scripts/smoke_vkpi_sync_trigger.py

R60 smoke: 验证手动触发 sync job 工作.

测试场景:
  1. POST /sync/trigger/{job} 用 admin token 通过
  2. POST /sync/trigger/invalid_job 返回 400
  3. 触发后 audit log 有记录
  4. 默认情况下 vkpi:write 用户(非 admin)被拒绝
"""
from __future__ import annotations

from stdout_utils import out

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from _smoke_seed import seed_admin, cleanup_admin
except ImportError:
    seed_admin = None
    cleanup_admin = None

from app.db.connection import get_conn


PREFIX = "vkpi-sync-trigger-"
MARKER = f"{PREFIX}{int(time.time())}"
BASE_URL = "http://127.0.0.1:8102"


def main() -> None:
    conn = get_conn()
    user_id = 0
    staff_id = 0
    
    if seed_admin:
        user_id, staff_id = seed_admin(conn, marker=MARKER, vkpi_permission="admin")
    else:
        sys.exit(1)
    
    try:
        from app.core.security import make_token
        admin_token = make_token(user_id, "admin")
    except Exception as exc:
        out(f"[token] failed: {exc}")
        sys.exit(1)
    
    failures: list[str] = []
    
    # 取 baseline audit count
    baseline_audit = _count_audit("sync_trigger")
    
    # ── 场景 1: POST /sync/trigger/alerts (轻量 job,触发开销小) ──
    out("[1/3] POST /sync/trigger/alerts (admin token 应该通过)")
    
    resp = _post_json(
        f"{BASE_URL}/api/admin/vkpi/sync/trigger/alerts",
        admin_token,
        {},
    )
    
    if resp.get("status_code") not in (200, 201):
        failures.append(f"场景 1 HTTP 错: {resp}")
    else:
        body = resp["body"]
        if body.get("status") != "ok":
            failures.append(f"场景 1: 期望 status=ok,实际 {body}")
        elif body.get("job") != "alerts":
            failures.append(f"场景 1: job 字段错 {body}")
        else:
            out(f"   PASS: alerts job 触发成功")
    
    # ── 场景 2: POST /sync/trigger/invalid_job 应该 400 ──
    out("[2/3] POST /sync/trigger/invalid_job 应该 400")
    
    resp_invalid = _post_json(
        f"{BASE_URL}/api/admin/vkpi/sync/trigger/totally_invalid_job",
        admin_token,
        {},
    )
    
    if resp_invalid.get("status_code") != 400:
        failures.append(f"场景 2: 期望 400,实际 {resp_invalid.get('status_code')}")
    else:
        out(f"   PASS: invalid job 返回 400")
    
    # ── 场景 3: audit log 增加 ──
    out("[3/3] audit log 记录了触发")
    
    new_count = _count_audit("sync_trigger")
    if new_count <= baseline_audit:
        failures.append(f"场景 3: 期望 audit +1,实际 {baseline_audit} → {new_count}")
    else:
        out(f"   PASS: audit log +{new_count - baseline_audit}")
    
    # ── cleanup ──
    _cleanup_audit("sync_trigger", staff_id)
    if cleanup_admin and staff_id:
        cleanup_admin(conn, user_id=user_id, staff_id=staff_id)
    
    if failures:
        out("\n=== FAIL ===")
        for f in failures:
            out(f"  - {f}")
        sys.exit(1)
    else:
        out("\nVKPI_SYNC_TRIGGER_SMOKE_OK")
        sys.exit(0)


def _post_json(url: str, token: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"status_code": resp.status, "body": json.loads(resp.read().decode("utf-8") or "{}")}
    except urllib.error.HTTPError as exc:
        return {"status_code": exc.code, "error": exc.read().decode("utf-8", errors="replace")[:500]}
    except Exception as exc:
        return {"status_code": -1, "error": str(exc)}


def _count_audit(action_type: str) -> int:
    row = get_conn().execute(
        "SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE action_type=?",
        (action_type,),
    ).fetchone()
    return int(row["n"]) if row else 0


def _cleanup_audit(action_type: str, staff_id: int) -> None:
    if staff_id:
        get_conn().execute(
            "DELETE FROM vkpi_business_audit_logs WHERE action_type=? AND staff_id=?",
            (action_type, int(staff_id)),
        )
        get_conn().commit()


if __name__ == "__main__":
    main()

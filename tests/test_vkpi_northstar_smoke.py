"""U3 · GTM 北极星端点 TestClient 真库冒烟(配方同 test_w5_route_smoke)。

观测目标(只读,不改 app 源文件):以「已认证 admin」身份打
GET /api/admin/vkpi/gtm/northstar,断言:
  - 不 500,status=ok;
  - 三指标齐(launch_briefs / dealers / verdict_rate),value/target 数值形;
  - 诚实态:表缺 → value=0 + status=table_missing(绝不编数)。
不硬断言具体行数(本地真库会随施工涨),只断言口径与形状 —— 具体数值
(brief=1 / dealer=0 / 裁决 0%)在冒烟运行输出里人工核对。

hermetic:不打真后端、不连真登录;只读真 Postgres(本机 54329/viltrox2)。
绝不写 viltrox_fit_score、不改 rule_v0。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


_ADMIN_USER = {"id": 1, "email": "admin@u3.test", "role": "admin"}
_ADMIN_STAFF = {
    "id": 1,
    "staff_id": 1,
    "user_id": 1,
    "role": "admin",
    "is_owner": 1,
    "permissions": {"vkpi": "admin"},
    "email": "admin@u3.test",
    "organization_id": 1,
    "organization_scope_status": "resolved",
}


@pytest.fixture(scope="module")
def admin_client():
    """已认证 admin 的 TestClient(中间件 + 依赖两道 seam 都伪造掉,配方同 W5)。"""
    import app.main as main_mod
    from app.main import app
    import app.api.dependencies.perms as perms_mod
    from app.api.dependencies.auth import get_user_required
    from fastapi.testclient import TestClient

    saved = {
        "main_gcu": main_mod.get_current_user,
        "main_scfu": main_mod.staff_context_for_user,
        "perms_scfu": perms_mod.staff_context_for_user,
        "overrides": dict(app.dependency_overrides),
    }
    main_mod.get_current_user = lambda request: _ADMIN_USER
    main_mod.staff_context_for_user = lambda user: _ADMIN_STAFF
    perms_mod.staff_context_for_user = lambda user: _ADMIN_STAFF
    app.dependency_overrides[get_user_required] = lambda: _ADMIN_USER

    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        main_mod.get_current_user = saved["main_gcu"]
        main_mod.staff_context_for_user = saved["main_scfu"]
        perms_mod.staff_context_for_user = saved["perms_scfu"]
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved["overrides"])


def test_northstar_shape_and_honesty(admin_client):
    resp = admin_client.get("/api/admin/vkpi/gtm/northstar")
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body.get("status") == "ok", body
    assert body.get("window_days") == 90
    metrics = body.get("metrics") or {}
    for key, target in (("launch_briefs", 30), ("dealers", 300), ("verdict_rate", 30.0)):
        m = metrics.get(key)
        assert isinstance(m, dict), f"missing metric {key}: {body}"
        assert isinstance(m.get("value"), (int, float)) and m["value"] >= 0, m
        assert m.get("target") == target, m
        assert m.get("status") in {"ok", "table_missing", "error"}, m
        # 诚实态:表缺必须 value=0(绝不编数)。
        if m["status"] == "table_missing":
            assert m["value"] == 0, m
    # 裁决率带 decided/total 明细(表缺时 0/0)。
    vr = metrics["verdict_rate"]
    assert isinstance(vr.get("decided"), int) and isinstance(vr.get("total"), int)
    # 冒烟证据:打印真库现值,人工核对(预期 brief=1 / dealer=0 / 裁决≈0)。
    print(
        "NORTHSTAR live:",
        {k: {"value": v.get("value"), "status": v.get("status")} for k, v in metrics.items()},
    )


def test_northstar_requires_auth_wiring(admin_client):
    # 证明路由真挂上了(注册表 anchors 生效),而不是 404。
    resp = admin_client.get("/api/admin/vkpi/gtm/northstar")
    assert resp.status_code != 404

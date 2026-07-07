"""U1 · 顶栏全局任务进度中心端点冒烟(admin 身份,真库只读)。

配方同 test_w5_route_smoke.py:middleware + require_tab 两道 seam 伪造 admin,
TestClient 打真 Postgres(纯读路径)。断言:
  - GET /api/admin/vkpi/progress/center 200;
  - 契约字段齐(counts/running/queued/recent_done/stage_flow);
  - 纯读诊断位(write_db/llm_calls/worker_touched 全 False);
  - recent_done ≤ 5;
红线:零写库,不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


_ADMIN_USER = {"id": 1, "email": "admin@u1.test", "role": "admin"}
_ADMIN_STAFF = {
    "id": 1,
    "staff_id": 1,
    "user_id": 1,
    "role": "admin",
    "is_owner": 1,
    "permissions": {"vkpi": "admin"},
    "email": "admin@u1.test",
}


@pytest.fixture(scope="module")
def admin_client():
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


def test_progress_center_route_mounted():
    """注册表制挂载生效:路由存在于 app.routes(ADMIN_ROUTER_MODULES append)。"""
    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/admin/vkpi/progress/center" in paths


def test_progress_center_contract(admin_client):
    resp = admin_client.get("/api/admin/vkpi/progress/center")
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()

    assert body.get("status") == "ready"
    counts = body.get("counts") or {}
    for key in ("running", "queued", "active_total", "recent_total"):
        assert isinstance(counts.get(key), int), f"counts.{key} 应为 int: {counts}"

    for key in ("running", "queued", "recent_done", "stage_flow"):
        assert isinstance(body.get(key), list), f"{key} 应为 list"
    assert len(body["recent_done"]) <= 5

    # 阶段流契约(前端 4 步文案同源):队列中→抓取→分析→落库
    stages = [s.get("stage") for s in body["stage_flow"]]
    assert stages == ["queued", "search", "thinking", "summarizing"]

    diagnostics = body.get("diagnostics") or {}
    assert diagnostics.get("write_db") is False
    assert diagnostics.get("llm_calls") is False
    assert diagnostics.get("worker_touched") is False


def test_progress_center_task_shapes(admin_client):
    """真库出真任务数:跑中/排队行字段形状(有数据才逐行断言,空库不误伤)。"""
    resp = admin_client.get("/api/admin/vkpi/progress/center")
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()

    for task in body.get("running") or []:
        assert task.get("status") in ("running", "processing")
        pct = task.get("progress_pct")
        assert pct is None or (isinstance(pct, int) and 0 <= pct <= 100)
        assert task.get("stage") in ("queued", "search", "thinking", "summarizing")
    for task in body.get("queued") or []:
        assert task.get("status") in ("queued", "retrying")
        assert task.get("progress_pct") == 0
        eta = task.get("eta_seconds")
        assert eta is None or isinstance(eta, (int, float))
    for item in body.get("recent_done") or []:
        assert isinstance(item.get("id"), str)
        assert isinstance(item.get("has_error"), bool)


def test_progress_center_respects_limit(admin_client):
    resp = admin_client.get("/api/admin/vkpi/progress/center?limit=1&recent_minutes=120")
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert len(body.get("running") or []) <= 1
    assert len(body.get("queued") or []) <= 1
    # limit 越界被 FastAPI 校验拦下(ge=1/le=50)
    resp_bad = admin_client.get("/api/admin/vkpi/progress/center?limit=0")
    assert resp_bad.status_code == 422

"""W5 · 后端路由 smoke(admin 身份,断言关键 GET 路由不 500)。

观测目标(只读,绝不改 app 源文件):
  以「已认证 admin」身份遍历一批 **无路径参数** 的关键 GET 路由,断言
  ``status_code != 500``。404/403/422 等都可接受(数据缺失/权限边界是业务语义),
  唯独 500(未捕获异常 / 序列化崩 / SQL 崩)不可接受 —— 那是真 bug。

伪造已认证身份的 seam(见文件顶部 _login_as_admin):
  1. middleware ``app.main.admin_rbac_middleware`` 在依赖之前跑,直接调
     ``app.main.get_current_user`` + ``app.main.staff_context_for_user``(按值导入
     的模块级符号)→ 必须在 **app.main 命名空间** monkeypatch 这两个。
  2. 路由依赖 ``require_tab(...)`` → ``get_user_required`` → ``staff_context_for_user``
     (从 app.core.permissions 导入进 perms.py)→ 用 dependency_overrides 覆盖
     ``get_user_required``,并 patch ``perms.staff_context_for_user`` 返回 admin staff。

hermetic:不打真后端、不连真登录(JWT/DB 用户查全绕过);只读真 Postgres(本机已起,
路由本就是读路径)。绝不写 viltrox_fit_score、不改 rule_v0。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# ── admin 身份(can_view_all=True:is_owner=1 + vkpi:admin) ────────────────────
_ADMIN_USER = {"id": 1, "email": "admin@w5.test", "role": "admin"}
_ADMIN_STAFF = {
    "id": 1,
    "staff_id": 1,
    "user_id": 1,
    "role": "admin",
    "is_owner": 1,
    "permissions": {"vkpi": "admin"},
    "email": "admin@w5.test",
}


# 一批「无路径参数」的关键 GET 路由(读路径)。媒体代理/外网类路由刻意排除
# (避免网络 I/O 让 smoke 不 hermetic)。
_SMOKE_PATHS = [
    "/health",
    "/api/admin/vkpi/actions/inbox",
    "/api/admin/dashboard/kols",
    "/api/admin/dashboard/map",
    "/api/admin/vkpi/dashboard",
    "/api/admin/vkpi/dashboard/agents/inbox",
    "/api/admin/vkpi/dashboard/kol-distribution",
    "/api/admin/vkpi/dashboard/recent-content",
    "/api/admin/vkpi/dashboard/system-health",
    "/api/admin/vkpi/dashboard/data-freshness",
    "/api/admin/vkpi/memory/summary",
    "/api/admin/vkpi/memory/entities",
    "/api/admin/vkpi/memory/readiness",
    "/api/admin/vkpi/kol-pool/summary",
    "/api/admin/vkpi/kol-pool/favorites",
    "/api/admin/vkpi/access/rbac-status",
    "/api/admin/vkpi/alerts",
    "/api/admin/vkpi/audit/summary",
    "/api/admin/vkpi/budgets",
    "/api/admin/vkpi/campaigns",
    "/api/admin/vkpi/channels",
    "/api/admin/vkpi/claims",
    "/api/admin/vkpi/costs",
    "/api/admin/vkpi/data-quality",
    "/api/admin/vkpi/events",
    "/api/admin/vkpi/inventory",
    "/api/admin/vkpi/kol-decisions",
    "/api/admin/vkpi/kols",
    "/api/admin/vkpi/links",
    "/api/admin/vkpi/projects",
    "/api/admin/vkpi/reports",
    "/api/admin/vkpi/settings/control-status",
    "/api/admin/vkpi/settings/providers",
    "/api/admin/vkpi/staff-directory",
    "/api/admin/vkpi/staff-kpi",
    "/api/admin/vkpi/sync/overview",
    "/api/admin/vkpi/task-queue",
    "/api/admin/vkpi/tasks",
]


@pytest.fixture(scope="module")
def admin_client():
    """已认证 admin 的 TestClient(中间件 + 依赖两道 seam 都伪造掉)。

    raise_server_exceptions=False:让未捕获异常落成 500 响应(可断言),
    而不是把异常抛进测试栈 —— smoke 的全部价值就在于「捕获 500」。
    """
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

    # seam 1:中间件(app.main 命名空间)
    main_mod.get_current_user = lambda request: _ADMIN_USER
    main_mod.staff_context_for_user = lambda user: _ADMIN_STAFF
    # seam 2:依赖(get_user_required 覆盖 + perms 的 staff_context)
    main_mod_perms_scfu = lambda user: _ADMIN_STAFF
    perms_mod.staff_context_for_user = main_mod_perms_scfu
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


@pytest.mark.parametrize("path", _SMOKE_PATHS)
def test_admin_get_route_does_not_500(admin_client, path):
    resp = admin_client.get(path)
    assert resp.status_code != 500, (
        f"{path} returned 500 (server-side crash):\n{resp.text[:500]}"
    )


def test_smoke_covers_health_as_baseline(admin_client):
    # /health 是公共路由,必须 200 —— 锚定整个 client/app 装配没坏。
    resp = admin_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert str(body.get("status") or "").lower() in {"ok", "healthy", "degraded"}


def test_admin_inbox_reachable_under_fake_auth(admin_client):
    # 证明「伪造 admin」确实穿透了 admin_rbac 中间件 + require_tab 双闸:
    # 这条 admin-gated 路由在真实未登录下会 403,这里必须拿到非 401/403。
    resp = admin_client.get("/api/admin/vkpi/actions/inbox")
    assert resp.status_code not in (401, 403), resp.text[:300]
    assert resp.status_code == 200, resp.text[:300]

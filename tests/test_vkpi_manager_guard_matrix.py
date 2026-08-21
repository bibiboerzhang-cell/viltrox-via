"""管理层闸角色矩阵(车道1 · manager_guard,2026-07-07)。

背景:permissions.py 给 manager+employee 默认 vkpi=write,故 require_tab("vkpi","write")
对全员恒真 —— 4 个敏感写口在其上加共享管理层闸(owner+manager,
app/api/dependencies/manager_guard.py,判定走 staff_domain.is_manager_staff)。

矩阵(roles × endpoints)
------------------------
roles     : owner(is_owner=1)│ manager(role=manager)│ employee(role=employee, is_owner=0)
写口(4): POST gtm/verdicts/{id}/decide(全端点闸)
           POST market-brain/gtm-plan/materialize dry_run=false(条件闸)
           POST goaffpro/kol/{id}/commission(全端点闸)
           POST goaffpro/kol/{id}/coupon(全端点闸)

断言
----
1. owner / manager:4 写口全部非 403(域层被 stub,返回 200)。
2. employee:4 写口全部 403 "management permission required",且域层写函数
   一次都没被调到(闸在写路径之前生效)。
3. employee 对 materialize dry_run=true 非 403(员工可模拟,零落库语义由域层保证,
   此处 stub 只证闸不拦)。
4. GET gtm/verdicts/pending 三角色均非 403(读端点不收紧)。

伪造身份的双 seam 照抄 test_w5_role_matrix(中间件 app.main + 依赖 perms);
hermetic:所有域层写函数被 patch 成纯记录器,不打真 DB / 真 GOAFFPRO。
绝不写 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# ── 三个身份(高位 id 不与真实 staff 撞车)────────────────────────────────────
_OWNER_USER = {"id": 990601, "email": "owner@mg.test", "role": "employee"}
_OWNER_STAFF = {
    "id": 990601, "staff_id": 990601, "user_id": 990601, "role": "employee",
    "is_owner": 1, "permissions": {"vkpi": "write"}, "email": "owner@mg.test",
    "organization_id": 1, "organization_scope_status": "resolved",
}

_MANAGER_USER = {"id": 990602, "email": "manager@mg.test", "role": "manager"}
_MANAGER_STAFF = {
    "id": 990602, "staff_id": 990602, "user_id": 990602, "role": "manager",
    "is_owner": 0, "permissions": {"vkpi": "write"}, "email": "manager@mg.test",
    "organization_id": 1, "organization_scope_status": "resolved",
}

_EMP_USER = {"id": 990603, "email": "emp@mg.test", "role": "employee"}
_EMP_STAFF = {
    "id": 990603, "staff_id": 990603, "user_id": 990603, "role": "employee",
    "is_owner": 0, "permissions": {"vkpi": "write"}, "email": "emp@mg.test",
    "organization_id": 1, "organization_scope_status": "resolved",
}

_IDENTITIES = {
    "owner": (_OWNER_USER, _OWNER_STAFF),
    "manager": (_MANAGER_USER, _MANAGER_STAFF),
    "employee": (_EMP_USER, _EMP_STAFF),
}

# POST 走 csrf_origin_middleware:无 auth cookie + Authorization: Bearer ... 放行
# (同 test_w5_role_matrix;token 值无关紧要,真实认证被 patch 伪造)。
_BEARER = {"Authorization": "Bearer manager-guard-test-token"}


def _make_client(user, staff):
    """构造一个伪造为 (user, staff) 身份的 TestClient + teardown 闭包(照抄 W5)。"""
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
    main_mod.get_current_user = lambda request: user
    main_mod.staff_context_for_user = lambda u: staff
    perms_mod.staff_context_for_user = lambda u: staff
    app.dependency_overrides[get_user_required] = lambda: user

    client = TestClient(app, raise_server_exceptions=False)

    def teardown():
        main_mod.get_current_user = saved["main_gcu"]
        main_mod.staff_context_for_user = saved["main_scfu"]
        perms_mod.staff_context_for_user = saved["perms_scfu"]
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved["overrides"])

    return client, teardown


# ── 域层 stub:纯记录器,写路径零真副作用 ────────────────────────────────────
class _NoopConn:
    def execute(self, sql, params=()):
        return None

    def commit(self):
        return None


@pytest.fixture()
def domain_calls(monkeypatch) -> list:
    """patch 4 写口背后的域层函数为记录器;返回被调函数名列表。"""
    import app.api.routers.vkpi_goaffpro as gp_router
    from app.domains.integrations import goaffpro_connect
    from app.domains.market_brain import materialize, verdict_flow

    calls: list = []

    def _record_verdict(**kwargs):
        calls.append("record_verdict")
        return {"ok": True, "probe": "verdict"}

    def _materialize_plan(**kwargs):
        calls.append("materialize_plan")
        return {"status": "ok", "probe": "materialize", "dry_run": kwargs.get("dry_run")}

    def _update_commission(affiliate_id, rate, **kwargs):
        calls.append("update_affiliate_commission")
        return {"ok": True, "commission_rate": rate}

    def _update_coupon(affiliate_id, code, **kwargs):
        calls.append("update_affiliate_coupon")
        return {"ok": True, "coupon": code}

    monkeypatch.setattr(verdict_flow, "record_verdict", _record_verdict)
    monkeypatch.setattr(
        verdict_flow, "list_pending_verdicts", lambda limit=50: {"status": "ok", "items": [], "count": 0}
    )
    monkeypatch.setattr(materialize, "materialize_plan", _materialize_plan)
    monkeypatch.setattr(goaffpro_connect, "ensure_goaffpro_links_schema", lambda: None)
    monkeypatch.setattr(goaffpro_connect, "update_affiliate_commission", _update_commission)
    monkeypatch.setattr(goaffpro_connect, "update_affiliate_coupon", _update_coupon)
    monkeypatch.setattr(gp_router, "get_conn", lambda: _NoopConn())
    monkeypatch.setattr(gp_router, "_assert_goaffpro_target_writable", lambda *_args: 990601)
    monkeypatch.setattr(gp_router, "_assert_goaffpro_provider_write_allowed", lambda: None)
    monkeypatch.setattr(gp_router, "_load_link", lambda conn, kol_pool_id: {"affiliate_id": "aff-test"})
    return calls


# ── 4 个敏感写口(name → 发请求闭包)─────────────────────────────────────────
def _post_decide(client):
    return client.post(
        "/api/admin/vkpi/gtm/verdicts/1/decide",
        json={"decision": "validated", "lesson": "matrix probe"},
        headers=_BEARER,
    )


def _post_materialize_real(client):
    return client.post(
        "/api/admin/vkpi/market-brain/gtm-plan/materialize",
        json={"sku": "MG-TEST-SKU", "dry_run": False},
        headers=_BEARER,
    )


# 高档(>15% 或固定金额):敏感操作,员工被拦、管理层放行。
def _post_commission(client):
    return client.post(
        "/api/admin/vkpi/goaffpro/kol/1/commission", json={"rate": 30}, headers=_BEARER
    )


def _post_coupon(client):
    return client.post(
        "/api/admin/vkpi/goaffpro/kol/1/coupon",
        json={"code": "MGTEST30", "discount_value": 30}, headers=_BEARER
    )


# 低档(percentage 且 0-15%):员工基础权限可自改(读裁决 2026-07-07 分档闸)。
def _post_commission_low(client):
    return client.post(
        "/api/admin/vkpi/goaffpro/kol/1/commission", json={"rate": 15}, headers=_BEARER
    )


def _post_coupon_low(client):
    return client.post(
        "/api/admin/vkpi/goaffpro/kol/1/coupon",
        json={"code": "MGTEST10", "discount_value": 10}, headers=_BEARER
    )


_WRITE_ENDPOINTS = {
    "verdict_decide": _post_decide,
    "materialize_write": _post_materialize_real,
    "goaffpro_commission": _post_commission,
    "goaffpro_coupon": _post_coupon,
}

# 员工基础可改的低档(≤15%)口子:不受管理层闸拦。
_EMPLOYEE_LOW_TIER_ENDPOINTS = {
    "goaffpro_commission_low": _post_commission_low,
    "goaffpro_coupon_low": _post_coupon_low,
}


def _request_as(role: str, do_request):
    user, staff = _IDENTITIES[role]
    client, teardown = _make_client(user, staff)
    try:
        return do_request(client)
    finally:
        teardown()


# ── (1) owner / manager:4 写口全放行(非 403)────────────────────────────────
@pytest.mark.parametrize("role", ["owner", "manager"])
@pytest.mark.parametrize("endpoint", list(_WRITE_ENDPOINTS), ids=list(_WRITE_ENDPOINTS))
def test_management_roles_pass_sensitive_writes(domain_calls, role, endpoint):
    resp = _request_as(role, _WRITE_ENDPOINTS[endpoint])
    assert resp.status_code != 403, f"{role} 不应被管理层闸拦:{resp.text[:300]}"
    assert resp.status_code == 200, resp.text[:300]
    # 域层写函数确实被调到(闸放行后走到写路径)。
    assert len(domain_calls) == 1


# ── (2) employee:4 写口全 403,且域层写函数零调用 ────────────────────────────
@pytest.mark.parametrize("endpoint", list(_WRITE_ENDPOINTS), ids=list(_WRITE_ENDPOINTS))
def test_employee_blocked_on_sensitive_writes(domain_calls, endpoint):
    resp = _request_as("employee", _WRITE_ENDPOINTS[endpoint])
    assert resp.status_code == 403, f"employee 应被拦:{resp.status_code} {resp.text[:300]}"
    assert resp.json()["detail"] == "management permission required"
    # 闸在写路径之前生效:域层一次都没被调到。
    assert domain_calls == []


# ── (2b) employee 对佣金/优惠码低档(≤15%)可自改:非 403,域层被调到 ──────────
@pytest.mark.parametrize(
    "endpoint", list(_EMPLOYEE_LOW_TIER_ENDPOINTS), ids=list(_EMPLOYEE_LOW_TIER_ENDPOINTS)
)
def test_employee_can_low_tier_commission_coupon(domain_calls, endpoint):
    resp = _request_as("employee", _EMPLOYEE_LOW_TIER_ENDPOINTS[endpoint])
    assert resp.status_code != 403, f"employee 低档不应被拦:{resp.status_code} {resp.text[:300]}"
    assert resp.status_code == 200, resp.text[:300]
    # 分档闸放行后走到域层写函数(证明 ≤15% 员工真能改)。
    assert len(domain_calls) == 1


# ── (3) employee 对 materialize dry_run=true 非 403(员工可模拟)──────────────
def test_employee_can_dry_run_materialize(domain_calls):
    def _post_dry(client):
        return client.post(
            "/api/admin/vkpi/market-brain/gtm-plan/materialize",
            json={"sku": "MG-TEST-SKU", "dry_run": True},
            headers=_BEARER,
        )

    resp = _request_as("employee", _post_dry)
    assert resp.status_code != 403, f"dry_run=True 员工可模拟:{resp.text[:300]}"
    assert resp.status_code == 200, resp.text[:300]
    assert resp.json().get("dry_run") is True
    assert domain_calls == ["materialize_plan"]


# ── (4) GET verdicts/pending 三角色均非 403(读端点不收紧)────────────────────
@pytest.mark.parametrize("role", list(_IDENTITIES), ids=list(_IDENTITIES))
def test_all_roles_can_read_pending_verdicts(domain_calls, role):
    resp = _request_as(role, lambda c: c.get("/api/admin/vkpi/gtm/verdicts/pending"))
    assert resp.status_code != 403, f"{role} 读端点不应被拦:{resp.text[:300]}"
    assert resp.status_code == 200, resp.text[:300]

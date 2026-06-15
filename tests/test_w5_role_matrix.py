"""W5 · 角色矩阵(admin 全局 vs 普通成员 own-only,与 W0 隔离同口径)。

证明两条「身份感知」端点对 admin 与普通成员给出不同 scope:

  (A) Action inbox(GET /api/admin/vkpi/actions/inbox · inbox.list_inbox)
      - admin(can_view_all=True)→ 看全局,响应 ``scope=='all'``;
      - 普通成员(can_view_all=False)→ 只看 ``owner_staff_id==自己`` 的,
        响应 ``scope=='own'``,且看不到公司级(owner=NULL)/他人 owner 的动作。
      测试自播一行「成员自己 owner」的 inbox 动作 → 成员只见这一行,
      admin 见到这一行 **加上** 其余全局行(成员可见集 ⊊ admin 可见集)。

  (B) Dashboard KPI 选择器(POST /api/admin/dashboard/kpi · scope.effective_staff_id)
      - admin → effective_staff_id 收口为 None(全局聚合,无 own-only 收窄);
      - 成员 → effective_staff_id 强制收口为自己 id(前端请求任何人都被降级回 self)。
      端点把 staff_scope_id 透传给 build_dashboard_kpi;这里 patch 掉聚合函数,
      只记录它实际拿到的 staff_scope_id —— 断言 admin=None、成员=自己 id,
      即「成员拿不到全局」与 W0 隔离一致。

伪造身份的双 seam 同 test_w5_route_smoke(中间件 app.main + 依赖 perms)。
hermetic:用真 Postgres 读 inbox(本就读路径),自播的成员行 try/finally 清理;
KPI 聚合被 patch 成纯记录器(不打真聚合、不依赖种子数)。绝不写 viltrox_fit_score。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# ── 两个身份 ──────────────────────────────────────────────────────────────────
_ADMIN_USER = {"id": 1, "email": "admin@w5.test", "role": "admin"}
_ADMIN_STAFF = {
    "id": 1, "staff_id": 1, "user_id": 1, "role": "admin",
    "is_owner": 1, "permissions": {"vkpi": "admin"}, "email": "admin@w5.test",
}

# 普通成员:vkpi:write(过得了 require_tab read/write),但 is_owner=0 且非 admin level
# → can_view_all=False → own-only。固定一个不与真实 staff 撞车的高位 id。
_MEMBER_STAFF_ID = 990501
_MEMBER_USER = {"id": _MEMBER_STAFF_ID, "email": "member@w5.test", "role": "employee"}
_MEMBER_STAFF = {
    "id": _MEMBER_STAFF_ID, "staff_id": _MEMBER_STAFF_ID, "user_id": _MEMBER_STAFF_ID,
    "role": "employee", "is_owner": 0, "permissions": {"vkpi": "write"},
    "email": "member@w5.test",
}


def _make_client(user, staff):
    """构造一个伪造为 (user, staff) 身份的 TestClient + teardown 闭包。"""
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


# ── (A) Action inbox: admin 全局 vs 成员 own-only ─────────────────────────────
@pytest.fixture()
def member_owned_action():
    """自播成员 own 行 + 公司级行;yield 成员行 id;后置清理。

    用 kol_profile 类(纯提醒、requires_approval=False、不写业务数据),
    给成员一行「属于自己」的可见动作,再给 admin 一行 company/global 动作。
    这样测试不依赖当前库里是否已有其他 suggested 动作,避免空库时 admin/member 集合相等。
    """
    from app.db.connection import get_conn

    conn = get_conn()
    dedupe = f"kol_profile:w5-role-member:{uuid.uuid4().hex[:12]}"
    global_dedupe = f"kol_profile:w5-role-global:{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO vkpi_action_inbox
          (dedupe_key, category, title, detail, priority, entity_type, entity_id,
           suggested_endpoint, requires_approval, owner_staff_id, reason,
           payload_json, status, created_at, updated_at)
        VALUES (?, 'kol_profile', 'W5 role-matrix probe', 'd', 'low', 'kol', '',
                '', true, ?, '', '{}'::jsonb, 'suggested', NOW(), NOW())
        """,
        (dedupe, _MEMBER_STAFF_ID),
    )
    conn.execute(
        """
        INSERT INTO vkpi_action_inbox
          (dedupe_key, category, title, detail, priority, entity_type, entity_id,
           suggested_endpoint, requires_approval, owner_staff_id, reason,
           payload_json, status, created_at, updated_at)
        VALUES (?, 'kol_profile', 'W5 role-matrix global probe', 'd', 'low', 'kol', '',
                '', true, NULL, '', '{}'::jsonb, 'suggested', NOW(), NOW())
        """,
        (global_dedupe,),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM vkpi_action_inbox WHERE dedupe_key = ?", (dedupe,)
    ).fetchone()
    action_id = int(dict(row)["id"])
    try:
        yield action_id
    finally:
        conn.execute(
            "DELETE FROM vkpi_action_inbox WHERE dedupe_key IN (?, ?)",
            (dedupe, global_dedupe),
        )
        conn.commit()


def _inbox_ids(client) -> set[int]:
    resp = client.get("/api/admin/vkpi/actions/inbox?status=suggested&limit=200")
    assert resp.status_code == 200, resp.text[:300]
    return resp.json()


def _inbox_body(user, staff) -> dict:
    """以 (user, staff) 身份取一次 inbox 响应体。

    注意:_make_client patch 的是 **app.main 模块级全局**,同一 app 同时只能承载
    一个身份 —— 故 admin / member 必须 **串行** 取(各自 set→请求→teardown),
    不能同时持有两个 client(否则后者会覆盖前者的全局,导致 admin 误读成 member)。
    """
    client, td = _make_client(user, staff)
    try:
        resp = client.get("/api/admin/vkpi/actions/inbox?status=suggested&limit=200")
        assert resp.status_code == 200, resp.text[:300]
        return resp.json()
    finally:
        td()


def test_inbox_admin_global_member_own_only(member_owned_action):
    member_action_id = member_owned_action

    admin_body = _inbox_body(_ADMIN_USER, _ADMIN_STAFF)
    member_body = _inbox_body(_MEMBER_USER, _MEMBER_STAFF)

    # scope 标签:admin 全局,成员 own。
    assert admin_body["scope"] == "all"
    assert member_body["scope"] == "own"

    admin_ids = {int(i["id"]) for i in admin_body["items"]}
    member_ids = {int(i["id"]) for i in member_body["items"]}

    # 成员只看到自己 owner 的那一行;admin 也看到它(全局含之)。
    assert member_action_id in member_ids
    assert member_action_id in admin_ids

    # 红线4:成员每一条都必须 owner_staff_id==自己(看不到公司级/他人)。
    for item in member_body["items"]:
        assert int(item["owner_staff_id"]) == _MEMBER_STAFF_ID

    # 成员可见集 ⊊ admin 可见集(own-only 是全局的真子集)。
    assert member_ids <= admin_ids
    assert member_ids != admin_ids, "admin 应看到比成员更多(全局含公司级动作)"
    # 成员拿不到全局:admin 计数严格大于成员计数。
    assert len(admin_ids) > len(member_ids)


# POST 走 csrf_origin_middleware:无 auth cookie + Authorization: Bearer ...
# 即视为 API 调用放行(main.py _csrf_request_allowed:has_cookie=False 且
# auth_header 以 'bearer ' 开头 → True)。token 值无关紧要,真实认证仍被 patch 伪造。
_BEARER = {"Authorization": "Bearer w5-test-token"}


# ── (B) Dashboard KPI: admin→None(全局) vs 成员→own id ───────────────────────
def _capture_kpi_scope(monkeypatch):
    """patch build_dashboard_kpi 成纯记录器,返回一个收集 staff_scope_id 的 list。"""
    import app.api.routers.dashboard_account_picker as picker

    captured: list = []

    def _fake_kpi(*, account_type, selected_kol_ids, staff_scope_id):
        captured.append(staff_scope_id)
        return {"ok": True, "account_type": account_type, "echo_scope": staff_scope_id}

    monkeypatch.setattr(picker, "build_dashboard_kpi", _fake_kpi)
    return captured


def test_dashboard_kpi_admin_global_scope_none(monkeypatch):
    captured = _capture_kpi_scope(monkeypatch)
    client, td = _make_client(_ADMIN_USER, _ADMIN_STAFF)
    try:
        resp = client.post(
            "/api/admin/dashboard/kpi", json={"account_type": "all"}, headers=_BEARER
        )
    finally:
        td()
    assert resp.status_code == 200, resp.text[:300]
    # admin can_view_all → effective_staff_id 收口为 None(无 own-only 收窄)。
    assert captured == [None]
    assert resp.json()["echo_scope"] is None


def test_dashboard_kpi_member_scope_is_own_id(monkeypatch):
    captured = _capture_kpi_scope(monkeypatch)
    client, td = _make_client(_MEMBER_USER, _MEMBER_STAFF)
    try:
        resp = client.post(
            "/api/admin/dashboard/kpi", json={"account_type": "all"}, headers=_BEARER
        )
    finally:
        td()
    assert resp.status_code == 200, resp.text[:300]
    # 成员 → effective_staff_id 强制为自己 id(拿不到全局 None)。
    assert captured == [_MEMBER_STAFF_ID]
    assert resp.json()["echo_scope"] == _MEMBER_STAFF_ID

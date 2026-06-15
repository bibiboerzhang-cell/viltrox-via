"""W5 · Action 履约 E2E(inbox 生成→list→approve→execute→ledger 落行)。

全链走真实 HTTP 路由(TestClient + 伪造 admin 身份),针对一条 **自播的**
``kol_profile`` 动作(纯提醒类,执行器走 _exec_skip_reminder → 防御性 skip):

  1. seed   : 直接插一行 owner_staff_id==自己、status=suggested 的 kol_profile 动作
              (避免依赖 generate-daily 的种子数据,使断言确定)。
  2. list   : GET /actions/inbox → 该动作可见(语义正确)。
  3. approve: POST /actions/{id}/approve → ok=True, status=approved。
  4. execute: POST /actions/{id}/execute → 200,outcome=='skipped',
              reason=='profile_refresh_no_executor'(kol_profile 无干净刷新 service,
              executor 诚实 skip;skip 不改 action 终态,仍 approved)。
  5. ledger : vkpi_action_execution_ledger 落了一行 mode='executed'、outcome='skipped'
              的台账(执行有审计痕迹)。
  6. 红线   : 全链零写 vkpi_kol_pool.viltrox_fit_score —— 执行前后对该列做指纹比对,
              必须 byte 级不变(不改 rule_v0、不碰评分域)。

hermetic:伪造 admin 身份(中间件 app.main + 依赖 perms 两道 seam);POST 带
Bearer 头绕过 csrf_origin_middleware;自播行 + 自落 ledger 在 try/finally 全清理。
绝不编辑任何 app 源文件。
"""
from __future__ import annotations

import hashlib
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# 用一个固定的成员身份(is_owner=0、vkpi:write)owns 自播的动作 —— 成员对自己 owner 的
# 动作有完整 list/approve/execute 权(require_tab read/write 都过,scope 也放行)。
_ACTOR_STAFF_ID = 990777
_ACTOR_USER = {"id": _ACTOR_STAFF_ID, "email": "w5flow@test", "role": "employee"}
_ACTOR_STAFF = {
    "id": _ACTOR_STAFF_ID, "staff_id": _ACTOR_STAFF_ID, "user_id": _ACTOR_STAFF_ID,
    "role": "employee", "is_owner": 0, "permissions": {"vkpi": "write"},
    "email": "w5flow@test",
}

# POST 过 csrf_origin_middleware:无 cookie + Bearer 头即放行(token 值无关)。
_BEARER = {"Authorization": "Bearer w5-flow-token"}


@pytest.fixture()
def actor_client():
    """伪造为 _ACTOR 身份的 TestClient(中间件 + 依赖双 seam),teardown 还原。"""
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
    main_mod.get_current_user = lambda request: _ACTOR_USER
    main_mod.staff_context_for_user = lambda u: _ACTOR_STAFF
    perms_mod.staff_context_for_user = lambda u: _ACTOR_STAFF
    app.dependency_overrides[get_user_required] = lambda: _ACTOR_USER

    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        main_mod.get_current_user = saved["main_gcu"]
        main_mod.staff_context_for_user = saved["main_scfu"]
        perms_mod.staff_context_for_user = saved["perms_scfu"]
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved["overrides"])


@pytest.fixture()
def seeded_action():
    """自播一行 owner==actor 的 suggested kol_profile 动作;yield id;后置清 inbox + ledger。"""
    from app.db.connection import get_conn

    conn = get_conn()
    dedupe = f"kol_profile:w5-flow:{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO vkpi_action_inbox
          (dedupe_key, category, title, detail, priority, entity_type, entity_id,
           suggested_endpoint, requires_approval, owner_staff_id, reason,
           payload_json, status, created_at, updated_at)
        VALUES (?, 'kol_profile', 'W5 flow probe', 'detail', 'low', 'kol', '',
                '', true, ?, '', '{}'::jsonb, 'suggested', NOW(), NOW())
        """,
        (dedupe, _ACTOR_STAFF_ID),
    )
    conn.commit()
    action_id = int(
        dict(conn.execute(
            "SELECT id FROM vkpi_action_inbox WHERE dedupe_key = ?", (dedupe,)
        ).fetchone())["id"]
    )
    try:
        yield action_id
    finally:
        conn.execute(
            "DELETE FROM vkpi_action_execution_ledger WHERE action_id = ?", (action_id,)
        )
        conn.execute("DELETE FROM vkpi_action_inbox WHERE id = ?", (action_id,))
        conn.commit()


def _viltrox_fit_score_fingerprint() -> str:
    """对 vkpi_kol_pool.(id, viltrox_fit_score) 全表做稳定指纹(红线:执行前后必须一致)。"""
    from app.db.connection import get_conn

    rows = get_conn().execute(
        "SELECT id, viltrox_fit_score FROM vkpi_kol_pool ORDER BY id"
    ).fetchall()
    h = hashlib.sha256()
    for r in rows:
        d = dict(r)
        h.update(f"{d['id']}:{d['viltrox_fit_score']}|".encode())
    return h.hexdigest()


def _action_status(action_id: int) -> str:
    from app.db.connection import get_conn

    row = get_conn().execute(
        "SELECT status FROM vkpi_action_inbox WHERE id = ?", (action_id,)
    ).fetchone()
    return str(dict(row)["status"]) if row else ""


def _ledger_rows(action_id: int) -> list[dict]:
    from app.db.connection import get_conn

    rows = get_conn().execute(
        "SELECT mode, outcome, error FROM vkpi_action_execution_ledger "
        "WHERE action_id = ? ORDER BY id",
        (action_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def test_action_flow_list_approve_execute_skip_and_ledger(actor_client, seeded_action):
    action_id = seeded_action
    fit_before = _viltrox_fit_score_fingerprint()

    # 2. list:自播动作在 inbox 可见(语义:成员看到自己 owner 的 suggested 动作)。
    listed = actor_client.get(
        "/api/admin/vkpi/actions/inbox?status=suggested&limit=200"
    )
    assert listed.status_code == 200, listed.text[:300]
    listed_ids = {int(i["id"]) for i in listed.json()["items"]}
    assert action_id in listed_ids
    assert _action_status(action_id) == "suggested"

    # 3. approve:suggested → approved。
    appr = actor_client.post(
        f"/api/admin/vkpi/actions/{action_id}/approve", headers=_BEARER
    )
    assert appr.status_code == 200, appr.text[:300]
    appr_body = appr.json()
    assert appr_body.get("ok") is True
    assert appr_body.get("status") == "approved"
    assert _action_status(action_id) == "approved"

    # 4. execute:kol_profile 走 _exec_skip_reminder → 防御性 skip。
    exe = actor_client.post(
        f"/api/admin/vkpi/actions/{action_id}/execute", headers=_BEARER
    )
    assert exe.status_code == 200, exe.text[:300]
    exe_body = exe.json()
    assert exe_body.get("ok") is False
    assert exe_body.get("outcome") == "skipped"
    assert exe_body.get("category") == "kol_profile"
    assert exe_body.get("reason") == "profile_refresh_no_executor"
    assert "ledger_id" in exe_body  # 执行落了台账

    # skip 不改 action 终态(仍 approved,人可再处理/dismiss)。
    assert _action_status(action_id) == "approved"

    # 5. ledger:落了一行 mode='executed'、outcome='skipped' 的执行台账。
    rows = _ledger_rows(action_id)
    assert len(rows) >= 1, "execute 必须留下执行台账行"
    last = rows[-1]
    assert last["mode"] == "executed"
    assert last["outcome"] == "skipped"

    # 6. 红线:全链零写 viltrox_fit_score(执行前后指纹 byte 级不变)。
    fit_after = _viltrox_fit_score_fingerprint()
    assert fit_after == fit_before, (
        "Action 履约链改动了 vkpi_kol_pool.viltrox_fit_score —— 触红线"
    )


def test_execute_unapproved_action_is_gated_skip(actor_client, seeded_action):
    """闸1 反例:未 approve 直接 execute → outcome='skipped'、reason='not_approved'。

    证明执行器第一道闸(仅 status=='approved' 才进执行)生效:跳过 approve 步,
    对 suggested 动作直接打 execute,必须被拦成 skipped(且不写 viltrox_fit_score)。
    """
    action_id = seeded_action
    fit_before = _viltrox_fit_score_fingerprint()
    assert _action_status(action_id) == "suggested"

    exe = actor_client.post(
        f"/api/admin/vkpi/actions/{action_id}/execute", headers=_BEARER
    )
    assert exe.status_code == 200, exe.text[:300]
    body = exe.json()
    assert body.get("outcome") == "skipped"
    assert body.get("reason") == "not_approved"
    # 闸1 拦下:action 状态原封不动(仍 suggested)。
    assert _action_status(action_id) == "suggested"
    assert _viltrox_fit_score_fingerprint() == fit_before

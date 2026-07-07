"""mark-done 端点最小转移测试(approved→executed;非 approved → 409)。

背景:suggested_endpoint 为空的动作(如 gtm_bet)走 /execute 只会 skipped,
行永远停在 approved —— POST /actions/{id}/mark-done 给「人在系统外做完了」一个诚实终态。

  1. approved 源态:POST mark-done → 200,ok=True,status=executed;DB 行落 executed;
     ledger 落一行 mode='executed'、outcome='success'、detail_json.kind='manual_execution'。
  2. 非 approved 源态(suggested):POST mark-done → 409(illegal_state_transition),
     行原封不动。

hermetic:伪造成员身份(中间件 app.main + 依赖 perms 两道 seam,与 test_w5_action_flow 同款);
POST 带 Bearer 头绕 csrf_origin_middleware;自播行 + 自落 ledger 在 try/finally 全清理。
红线:全链不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


_ACTOR_STAFF_ID = 990778
_ACTOR_USER = {"id": _ACTOR_STAFF_ID, "email": "markdone@test", "role": "employee"}
_ACTOR_STAFF = {
    "id": _ACTOR_STAFF_ID, "staff_id": _ACTOR_STAFF_ID, "user_id": _ACTOR_STAFF_ID,
    "role": "employee", "is_owner": 0, "permissions": {"vkpi": "write"},
    "email": "markdone@test",
}

# POST 过 csrf_origin_middleware:无 cookie + Bearer 头即放行(token 值无关)。
_BEARER = {"Authorization": "Bearer mark-done-token"}


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


def _seed_action(status: str) -> int:
    """自播一行 owner==actor 的动作(suggested_endpoint 空,模拟 gtm_bet 形态)。返回 id。"""
    from app.db.connection import get_conn

    conn = get_conn()
    dedupe = f"gtm_bet:mark-done-test:{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO vkpi_action_inbox
          (dedupe_key, category, title, detail, priority, entity_type, entity_id,
           suggested_endpoint, requires_approval, owner_staff_id, reason,
           payload_json, status, created_at, updated_at)
        VALUES (?, 'gtm_bet', 'mark-done probe', 'detail', 'low', 'bet', '',
                '', true, ?, '', '{}'::jsonb, ?, NOW(), NOW())
        """,
        (dedupe, _ACTOR_STAFF_ID, status),
    )
    conn.commit()
    return int(
        dict(conn.execute(
            "SELECT id FROM vkpi_action_inbox WHERE dedupe_key = ?", (dedupe,)
        ).fetchone())["id"]
    )


def _cleanup_action(action_id: int) -> None:
    from app.db.connection import get_conn

    conn = get_conn()
    conn.execute("DELETE FROM vkpi_action_execution_ledger WHERE action_id = ?", (action_id,))
    conn.execute("DELETE FROM vkpi_action_inbox WHERE id = ?", (action_id,))
    conn.commit()


def _action_status(action_id: int) -> str:
    from app.db.connection import get_conn

    row = get_conn().execute(
        "SELECT status FROM vkpi_action_inbox WHERE id = ?", (action_id,)
    ).fetchone()
    return str(dict(row)["status"]) if row else ""


def test_mark_done_approved_to_executed_with_manual_ledger(actor_client):
    """approved → executed;落 manual_execution 台账(kind 在 detail_json)。"""
    from app.db.connection import get_conn

    action_id = _seed_action("approved")
    try:
        resp = actor_client.post(
            f"/api/admin/vkpi/actions/{action_id}/mark-done",
            json={"note": "在系统外人工执行完毕"},
            headers=_BEARER,
        )
        assert resp.status_code == 200, resp.text[:300]
        body = resp.json()
        assert body.get("ok") is True
        assert body.get("status") == "executed"
        assert _action_status(action_id) == "executed"

        rows = [
            dict(r)
            for r in get_conn().execute(
                "SELECT mode, outcome, endpoint, detail_json FROM vkpi_action_execution_ledger "
                "WHERE action_id = ? ORDER BY id",
                (action_id,),
            ).fetchall()
        ]
        assert len(rows) == 1, "mark-done 必须留下且仅留一行执行台账"
        last = rows[-1]
        assert last["mode"] == "executed"
        assert last["outcome"] == "success"
        assert last["endpoint"] == "manual:mark-done"
        detail = last["detail_json"]
        if isinstance(detail, str):
            import json

            detail = json.loads(detail)
        assert detail.get("kind") == "manual_execution"
        assert detail.get("note") == "在系统外人工执行完毕"
    finally:
        _cleanup_action(action_id)


def test_mark_done_non_approved_is_409(actor_client):
    """非 approved 源态(suggested)→ 409,行原封不动、零台账。"""
    from app.db.connection import get_conn

    action_id = _seed_action("suggested")
    try:
        resp = actor_client.post(
            f"/api/admin/vkpi/actions/{action_id}/mark-done",
            json={"note": ""},
            headers=_BEARER,
        )
        assert resp.status_code == 409, resp.text[:300]
        assert resp.json().get("detail") == "illegal_state_transition"
        assert _action_status(action_id) == "suggested"
        n = int(dict(get_conn().execute(
            "SELECT COUNT(*) AS n FROM vkpi_action_execution_ledger WHERE action_id = ?",
            (action_id,),
        ).fetchone())["n"])
        assert n == 0, "非法转移不得落台账"
    finally:
        _cleanup_action(action_id)

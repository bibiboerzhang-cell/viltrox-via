"""L-legal-dsar:DSAR 公开申请通道 / 法务页 SPA 分发 / 员工审批口 / 迁移 309 / 注册与只读白名单。

hermetic:sqlite 内存库(117 + 309 列)+ 独立 FastAPI + TestClient;限流走内存窗口(_get_redis → None)。
红线自证:响应体与日志绝不出现申请人邮箱明文;不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.dependencies import perms  # noqa: E402
from app.api.dependencies.auth import get_user_required  # noqa: E402
from app.api.routers import dsar_public  # noqa: E402
from app.platform import rate_limit_store  # noqa: E402
from app.services.security import rate_limiter  # noqa: E402

EMAIL = "creator.person@example.com"
_ADMIN_USER = {"id": 7, "email": "admin@dsar.test", "role": "admin"}
_ADMIN_STAFF = {"id": 7, "staff_id": 7, "user_id": 7, "role": "admin", "is_owner": 1, "permissions": {"vkpi": "admin"}}

SCHEMA = """
CREATE TABLE vkpi_dsar_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_type TEXT NOT NULL DEFAULT 'erasure',
    subject_kol_pool_id INTEGER,
    subject_handle_snapshot TEXT DEFAULT '',
    subject_platform_snapshot TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    requested_by_staff_id INTEGER,
    approved_by_staff_id INTEGER,
    jurisdiction TEXT DEFAULT '',
    erasure_receipt_json TEXT NOT NULL DEFAULT '{}',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    executed_at TEXT,
    source TEXT NOT NULL DEFAULT 'staff',
    public_ref TEXT,
    requester_contact TEXT NOT NULL DEFAULT '',
    requester_message TEXT NOT NULL DEFAULT '',
    subject_profile_url TEXT NOT NULL DEFAULT '',
    suppression_json TEXT NOT NULL DEFAULT '{}',
    client_ip_hash TEXT NOT NULL DEFAULT ''
);
CREATE TABLE vkpi_kol_pool (
    id INTEGER PRIMARY KEY, platform TEXT, handle TEXT, profile_url TEXT DEFAULT '', email TEXT DEFAULT ''
);
CREATE TABLE vkpi_kol_pool_contacts (
    id INTEGER PRIMARY KEY, kol_pool_id INTEGER, contact_type TEXT, contact_value TEXT
);
"""


def _sqlite_has(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _valid_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "request_type": "erasure",
        "platform": "youtube",
        "handle": "@Demo_Creator",
        "profile_url": "",
        "contact_email": EMAIL,
        "message": "  please   remove  my data ",
        "consent_confirmed": True,
    }
    body.update(overrides)
    return body


@pytest.fixture()
def wired(monkeypatch: pytest.MonkeyPatch):
    # TestClient 把同步路由丢进线程池跑;内存库要跨线程复用。
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    monkeypatch.setattr(dsar_public, "get_conn", lambda: conn)
    monkeypatch.setattr(dsar_public, "table_exists", lambda name: _sqlite_has(conn, name))
    monkeypatch.setattr(dsar_public, "_serve_spa", lambda: PlainTextResponse("spa-index"))
    monkeypatch.setattr(rate_limiter, "_get_redis", lambda: None)
    rate_limit_store._memory_windows.clear()
    monkeypatch.setattr(perms, "staff_context_for_user", lambda user: _ADMIN_STAFF)
    monkeypatch.setattr(perms, "check_tab_permission", lambda staff, tab, level: True)
    app = FastAPI()
    app.include_router(dsar_public.router)
    app.dependency_overrides[get_user_required] = lambda: _ADMIN_USER
    client = TestClient(app)
    try:
        yield client, conn
    finally:
        app.dependency_overrides.clear()
        rate_limit_store._memory_windows.clear()
        conn.close()


# ── 纯函数 ────────────────────────────────────────────────────────────────────


def test_validate_public_request_closed_sets_and_honeypot() -> None:
    from fastapi import HTTPException

    ok = dsar_public.validate_public_request(dsar_public.DsarPublicRequestBody(**_valid_body()))
    assert ok["handle"] == "Demo_Creator" and ok["contact_email"] == EMAIL
    assert ok["message"] == "please remove my data"

    def code_of(**overrides: Any) -> str:
        with pytest.raises(HTTPException) as info:
            dsar_public.validate_public_request(dsar_public.DsarPublicRequestBody(**_valid_body(**overrides)))
        return str(info.value.detail["code"])

    assert code_of(website="http://spam.example") == "rejected"
    assert code_of(request_type="sell_my_data") == "request_type_invalid"
    assert code_of(platform="myspace") == "platform_invalid"
    assert code_of(handle="bad handle!") == "handle_invalid"
    assert code_of(handle="", profile_url="") == "subject_missing"
    assert code_of(handle="", profile_url="http://insecure.example/x") == "profile_url_invalid"
    assert code_of(contact_email="not-an-email") == "contact_email_invalid"
    assert code_of(consent_confirmed=False) == "consent_required"


def test_mask_contact_never_returns_full_local_part() -> None:
    assert dsar_public.mask_contact("Alice.Long@Example.com") == "A***@Example.com"
    assert dsar_public.mask_contact("+1 555 0100") == "***"
    assert dsar_public.mask_contact("") == ""


def test_captcha_gate_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    monkeypatch.delenv(dsar_public.CAPTCHA_MODE_ENV, raising=False)
    dsar_public._captcha_gate("")  # 默认 off:占位,不校验

    monkeypatch.setenv(dsar_public.CAPTCHA_MODE_ENV, "shared_secret")
    monkeypatch.setenv(dsar_public.CAPTCHA_SECRET_ENV, "beta-secret")
    dsar_public._captcha_gate("beta-secret")
    with pytest.raises(HTTPException) as info:
        dsar_public._captcha_gate("wrong")
    assert info.value.status_code == 400 and info.value.detail["code"] == "captcha_failed"

    monkeypatch.setenv(dsar_public.CAPTCHA_MODE_ENV, "turnstile")  # 尚未接入的模式 → 诚实 503,不放行
    with pytest.raises(HTTPException) as info:
        dsar_public._captcha_gate("anything")
    assert info.value.status_code == 503


def test_retention_policy_mirrors_scheduler_module_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol import portal
    from app.services.scheduler import jobs_retention

    by_key = {item["key"]: item for item in dsar_public.RETENTION_POLICY_KEYS}
    assert by_key["VKPI_RETENTION_APIFY_PAYLOAD_DAYS"]["default_days"] == jobs_retention.DEFAULT_APIFY_PAYLOAD_DAYS
    assert by_key["VKPI_RETENTION_COMMENTS_DAYS"]["default_days"] == jobs_retention.DEFAULT_COMMENTS_DAYS
    assert by_key["VKPI_PORTAL_TOKEN_TTL_DAYS"]["default_days"] == portal.portal_token_ttl_days()

    monkeypatch.setenv("VKPI_RETENTION_COMMENTS_DAYS", "30")
    monkeypatch.setenv("VKPI_RETENTION_APIFY_PAYLOAD_DAYS", "not-a-number")
    monkeypatch.delenv(dsar_public.PURGE_GATE_ENV, raising=False)
    monkeypatch.delenv(dsar_public.CONTACT_EMAIL_ENV, raising=False)
    policy = dsar_public.retention_policy_public()
    days = {row["policy_key"]: row["days"] for row in policy["retention"]}
    assert days["VKPI_RETENTION_COMMENTS_DAYS"] == 30 == jobs_retention.retention_policy()["comments_days"]
    assert days["VKPI_RETENTION_APIFY_PAYLOAD_DAYS"] == jobs_retention.DEFAULT_APIFY_PAYLOAD_DAYS
    assert days["contact_suppression"] == 0
    assert policy["draft"] is True and policy["legal_review"] == "pending"
    assert policy["purge_task_key"] == dsar_public.RETENTION_TASK_KEY == "vkpi_data_retention_purge"
    assert policy["purge_enabled"] is False and policy["contact_email_configured"] is False
    assert policy["contact_email"] == dsar_public.CONTACT_EMAIL_PLACEHOLDER
    assert policy["public_form_path"] == "/legal/request" and policy["dsar_sla_days"] == 30

    monkeypatch.setenv(dsar_public.PURGE_GATE_ENV, "1")
    monkeypatch.setenv(dsar_public.CONTACT_EMAIL_ENV, "privacy-real@example.com")
    live = dsar_public.retention_policy_public()
    assert live["purge_enabled"] is True and live["contact_email_configured"] is True


# ── 公开表单 ──────────────────────────────────────────────────────────────────


def test_submit_creates_pending_ticket_without_echoing_contact(wired, caplog: pytest.LogCaptureFixture) -> None:
    client, conn = wired
    caplog.set_level(logging.INFO)
    response = client.post("/api/public/dsar/requests", json=_valid_body(), headers={"X-Forwarded-For": "203.0.113.9"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "received" and payload["sla_days"] == 30 and payload["suppression"] is None
    assert re.fullmatch(r"DSAR-[0-9A-F]{8}", payload["public_ref"])
    assert EMAIL not in response.text and "subject" not in payload  # 不回显邮箱,也不泄漏「是否持有该账号」

    row = dict(conn.execute("SELECT * FROM vkpi_dsar_requests").fetchone())
    assert row["source"] == "public_form" and row["status"] == "pending" and row["request_type"] == "erasure"
    assert row["requester_contact"] == EMAIL and row["requester_message"] == "please remove my data"
    assert row["subject_kol_pool_id"] is None and row["subject_handle_snapshot"] == "Demo_Creator"
    assert row["subject_platform_snapshot"] == "youtube" and row["public_ref"] == payload["public_ref"]
    assert re.fullmatch(r"[0-9a-f]{16}", row["client_ip_hash"]) and "203.0.113.9" not in row["client_ip_hash"]
    assert any("dsar.public.received" in record.getMessage() for record in caplog.records)
    assert EMAIL not in caplog.text and "203.0.113.9" not in caplog.text


def test_submit_resolves_subject_case_insensitively_and_self_suppresses(wired, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol import contact_suppression

    client, conn = wired
    conn.execute("INSERT INTO vkpi_kol_pool (id, platform, handle, profile_url, email) VALUES (41, 'youtube', 'demo_creator', 'https://youtube.com/@demo_creator', 'biz@example.com')")
    captured: list[dict[str, Any]] = []

    def fake_record(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"channel": "email", "status": "inserted"}

    monkeypatch.setattr(contact_suppression, "record_suppression", fake_record)
    response = client.post("/api/public/dsar/requests", json=_valid_body(request_type="do_not_contact"))
    assert response.status_code == 200, response.text
    assert response.json()["suppression"] == {"status": "recorded"}
    assert EMAIL not in response.text

    assert len(captured) == 1
    call = captured[0]
    assert call["kol_pool_id"] == 41 and call["contact_value"] == EMAIL and call["contact_type"] == "email"
    assert call["reason"] == "legal_request" and call["source_type"] == "reply" and call["conn"] is conn
    row = dict(conn.execute("SELECT subject_kol_pool_id, suppression_json FROM vkpi_dsar_requests").fetchone())
    assert row["subject_kol_pool_id"] == 41
    assert json.loads(row["suppression_json"]) == {"status": "suppressed", "channel": "email", "reason": "legal_request"}


def test_do_not_contact_with_unresolved_subject_is_deferred_to_staff(wired) -> None:
    client, conn = wired
    response = client.post(
        "/api/public/dsar/requests",
        json=_valid_body(request_type="do_not_contact", handle="", profile_url="https://www.youtube.com/@nobody"),
    )
    assert response.status_code == 200, response.text
    row = dict(conn.execute("SELECT subject_kol_pool_id, suppression_json, subject_profile_url FROM vkpi_dsar_requests").fetchone())
    assert row["subject_kol_pool_id"] is None and row["subject_profile_url"] == "https://www.youtube.com/@nobody"
    assert json.loads(row["suppression_json"]) == {"status": "deferred", "reason": "subject_unresolved"}


def test_rate_limit_is_per_ip_five_per_hour(wired) -> None:
    client, conn = wired
    headers = {"X-Forwarded-For": "198.51.100.7"}
    for _ in range(5):
        assert client.post("/api/public/dsar/requests", json=_valid_body(), headers=headers).status_code == 200
    blocked = client.post("/api/public/dsar/requests", json=_valid_body(), headers=headers)
    assert blocked.status_code == 429 and blocked.headers.get("X-RateLimit-Bucket") == dsar_public.RATE_BUCKET
    other_ip = client.post("/api/public/dsar/requests", json=_valid_body(), headers={"X-Forwarded-For": "198.51.100.8"})
    assert other_ip.status_code == 200
    assert conn.execute("SELECT COUNT(*) AS n FROM vkpi_dsar_requests").fetchone()["n"] == 6


def test_submit_rejects_validation_before_touching_db_and_503_when_table_missing(wired, monkeypatch: pytest.MonkeyPatch) -> None:
    client, conn = wired
    bad = client.post("/api/public/dsar/requests", json=_valid_body(consent_confirmed=False))
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "consent_required"
    assert conn.execute("SELECT COUNT(*) AS n FROM vkpi_dsar_requests").fetchone()["n"] == 0

    monkeypatch.setattr(dsar_public, "table_exists", lambda name: False)
    gone = client.post("/api/public/dsar/requests", json=_valid_body())
    assert gone.status_code == 503 and gone.json()["detail"]["code"] == "channel_unavailable"


def test_public_policy_endpoint_is_anonymous_and_pii_free(wired) -> None:
    client, _ = wired
    response = client.get("/api/public/legal/policy")
    assert response.status_code == 200
    body = response.json()
    assert body["draft"] is True and body["request_types"] == list(dsar_public.REQUEST_TYPES)
    assert {row["policy_key"] for row in body["retention"]} >= {
        "VKPI_RETENTION_APIFY_PAYLOAD_DAYS", "VKPI_RETENTION_COMMENTS_DAYS", "VKPI_PORTAL_TOKEN_TTL_DAYS",
    }


# ── 法务页 SPA 分发 ───────────────────────────────────────────────────────────


def test_legal_spa_routes_and_aliases(wired) -> None:
    client, _ = wired
    for path in ("/legal", "/legal/terms", "/legal/privacy", "/legal/data-sources", "/legal/request", "/privacy", "/terms"):
        response = client.get(path)
        assert response.status_code == 200 and response.text == "spa-index", path
    assert client.get("/legal/not-a-page").status_code == 404


def test_serve_spa_reuses_main_without_importing_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """分发逻辑只在 app.main 一处;路由用 sys.modules 现取(静态图不加 main↔router 环),未加载则诚实 503。"""
    import types

    from fastapi import HTTPException

    source = Path(dsar_public.__file__).read_text(encoding="utf-8")
    assert "from app import main" not in source and "import app.main" not in source

    fake_main = types.SimpleNamespace(_serve_frontend=lambda: PlainTextResponse("from-main"))
    monkeypatch.setitem(sys.modules, "app.main", fake_main)
    assert dsar_public._serve_spa().body == b"from-main"

    monkeypatch.delitem(sys.modules, "app.main", raising=False)
    with pytest.raises(HTTPException) as info:
        dsar_public._serve_spa()
    assert info.value.status_code == 503


# ── 员工审批口 ────────────────────────────────────────────────────────────────


def _seed_public_ticket(client: TestClient, **overrides: Any) -> str:
    response = client.post("/api/public/dsar/requests", json=_valid_body(**overrides))
    assert response.status_code == 200, response.text
    return str(response.json()["public_ref"])


def test_admin_list_masks_contact_and_detail_reveals_with_audit(wired, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.audit import service as audit_service
    from app.domains.kol import dsar_erasure

    client, conn = wired
    conn.execute("INSERT INTO vkpi_kol_pool (id, platform, handle, email) VALUES (5, 'youtube', 'demo_creator', 'biz@example.com')")
    public_ref = _seed_public_ticket(client)
    audits: list[dict[str, Any]] = []
    monkeypatch.setattr(audit_service, "log_sensitive_access", lambda **kwargs: audits.append(kwargs))
    monkeypatch.setattr(
        dsar_erasure, "collect_subject_footprint",
        lambda kol_pool_id: {"pool": {"email": "biz@example.com"}, "vkpi_kol_pool_contacts": 2},
    )

    listed = client.get("/api/admin/vkpi/dsar/requests", params={"status": "pending"})
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert len(items) == 1 and items[0]["public_ref"] == public_ref
    assert items[0]["requester_contact_masked"] == "c***@example.com" and "requester_contact" not in items[0]
    assert EMAIL not in listed.text

    detail = client.get(f"/api/admin/vkpi/dsar/requests/{items[0]['id']}")
    assert detail.status_code == 200, detail.text
    ticket = detail.json()["ticket"]
    assert ticket["requester_contact"] == EMAIL and ticket["suppression"] == {}
    assert detail.json()["footprint"] == {"vkpi_kol_pool_contacts": 2}  # 主体联系方式快照不给
    assert audits and audits[0]["action_type"] == "dsar_request_view" and audits[0]["staff_id"] == 7
    assert client.get("/api/admin/vkpi/dsar/requests/999").status_code == 404


def test_admin_review_then_execute_do_not_contact_suppresses_stored_contacts(wired, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol import contact_suppression

    client, conn = wired
    conn.execute("INSERT INTO vkpi_kol_pool (id, platform, handle, email) VALUES (5, 'youtube', 'demo_creator', 'Biz@Example.com')")
    conn.executemany(
        "INSERT INTO vkpi_kol_pool_contacts (kol_pool_id, contact_type, contact_value) VALUES (?,?,?)",
        [(5, "email", "biz@example.com"), (5, "business_email", "press@example.com"), (5, "phone", "+1 555")],
    )
    monkeypatch.setattr(contact_suppression, "record_suppression", lambda **kwargs: {"channel": "email"})
    _seed_public_ticket(client, request_type="do_not_contact")
    ticket_id = int(conn.execute("SELECT id FROM vkpi_dsar_requests").fetchone()["id"])

    early = client.post(f"/api/admin/vkpi/dsar/requests/{ticket_id}/execute")
    assert early.status_code == 409  # 未审批不得执行

    bad = client.patch(f"/api/admin/vkpi/dsar/requests/{ticket_id}", json={"status": "done"})
    assert bad.status_code == 400
    approved = client.patch(f"/api/admin/vkpi/dsar/requests/{ticket_id}", json={"status": "approved", "note": "identity  verified", "jurisdiction": "EU"})
    assert approved.status_code == 200 and approved.json()["new_status"] == "approved"
    row = dict(conn.execute("SELECT status, note, jurisdiction, approved_by_staff_id FROM vkpi_dsar_requests").fetchone())
    assert row == {"status": "approved", "note": "identity verified", "jurisdiction": "EU", "approved_by_staff_id": 7}
    assert client.patch(f"/api/admin/vkpi/dsar/requests/{ticket_id}", json={"status": "rejected"}).status_code == 409

    executed = client.post(f"/api/admin/vkpi/dsar/requests/{ticket_id}/execute")
    assert executed.status_code == 200, executed.text
    summary = executed.json()["suppression"]
    # 去重后 2 个邮箱(Biz@Example.com 与 biz@example.com 是不同字符串,交给抑制台账做规范化):attempted=3
    assert executed.json()["status"] == "done" and summary["status"] == "suppressed"
    assert summary["attempted"] == 3 and summary["suppressed"] == 3 and summary["failed"] == 0
    done = dict(conn.execute("SELECT status, executed_at, suppression_json FROM vkpi_dsar_requests").fetchone())
    assert done["status"] == "done" and done["executed_at"] and json.loads(done["suppression_json"])["attempted"] == 3


def test_admin_execute_erasure_delegates_to_existing_erase_subject(wired, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol import dsar_erasure

    client, conn = wired
    conn.execute("INSERT INTO vkpi_kol_pool (id, platform, handle) VALUES (9, 'tiktok', 'demo_creator')")
    calls: list[tuple[int, int]] = []

    def fake_erase(kol_pool_id: int, *, dsar_request_id: int, staff: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((kol_pool_id, dsar_request_id))
        return {"status": "done", "receipt": {"vkpi_kol_pool": 1}}

    monkeypatch.setattr(dsar_erasure, "erase_subject", fake_erase)
    _seed_public_ticket(client, platform="tiktok")
    ticket_id = int(conn.execute("SELECT id FROM vkpi_dsar_requests").fetchone()["id"])
    assert client.patch(f"/api/admin/vkpi/dsar/requests/{ticket_id}", json={"status": "approved"}).status_code == 200
    executed = client.post(f"/api/admin/vkpi/dsar/requests/{ticket_id}/execute")
    assert executed.status_code == 200 and executed.json() == {"status": "done", "receipt": {"vkpi_kol_pool": 1}}
    assert calls == [(9, ticket_id)]

    monkeypatch.setattr(dsar_erasure, "erase_subject", lambda *a, **k: {"status": "blocked", "reason": "dsar request not approved"})
    conn.execute("UPDATE vkpi_dsar_requests SET status='approved'")
    conn.commit()
    assert client.post(f"/api/admin/vkpi/dsar/requests/{ticket_id}/execute").status_code == 409


def test_admin_execute_access_marks_done_and_unlinked_subject_is_409(wired) -> None:
    client, conn = wired
    _seed_public_ticket(client, request_type="access")
    ticket_id = int(conn.execute("SELECT id FROM vkpi_dsar_requests").fetchone()["id"])
    assert client.patch(f"/api/admin/vkpi/dsar/requests/{ticket_id}", json={"status": "approved"}).status_code == 200
    assert client.post(f"/api/admin/vkpi/dsar/requests/{ticket_id}/execute").json()["status"] == "done"

    _seed_public_ticket(client, request_type="erasure", handle="ghost_handle")
    ghost_id = int(conn.execute("SELECT MAX(id) AS id FROM vkpi_dsar_requests").fetchone()["id"])
    assert client.patch(f"/api/admin/vkpi/dsar/requests/{ghost_id}", json={"status": "approved"}).status_code == 200
    assert client.post(f"/api/admin/vkpi/dsar/requests/{ghost_id}/execute").status_code == 409


def test_admin_list_is_honest_when_migration_missing(wired, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = wired
    monkeypatch.setattr(dsar_public, "table_exists", lambda name: False)
    response = client.get("/api/admin/vkpi/dsar/requests")
    assert response.status_code == 200 and response.json() == {"items": [], "count": 0, "available": False, "reason": "migration_117_not_applied"}


# ── 注册 / 只读白名单 / 迁移 309 ──────────────────────────────────────────────


def test_router_is_registered_and_public_reads_are_release_validation_safe() -> None:
    from app.api.routers import ADMIN_ROUTER_MODULES
    from app.core.release_validation import release_validation_request_allowed

    assert "dsar_public" in ADMIN_ROUTER_MODULES
    for path in ("/api/public/legal/policy", "/legal", "/legal/privacy", "/legal/request", "/privacy", "/terms"):
        assert release_validation_request_allowed("GET", path), path
    assert not release_validation_request_allowed("POST", "/api/public/dsar/requests")  # 写口在验收窗口内照常围栏


def test_migration_309_is_additive_reversible_and_runner_owned() -> None:
    from app.db.connection import _validate_runner_owned_transactions

    up = (ROOT / "migrations/309_vkpi_dsar_public_intake.sql").read_text(encoding="utf-8")
    down = (ROOT / "migrations/309_vkpi_dsar_public_intake_down.sql").read_text(encoding="utf-8")
    for column in ("source", "public_ref", "requester_contact", "requester_message", "subject_profile_url", "suppression_json", "client_ip_hash"):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in up, column
        assert f"DROP COLUMN IF EXISTS {column}" in down, column
    assert "'do_not_contact'" in up and "uq_vkpi_dsar_public_ref" in up
    assert "DELETE FROM" not in up and "DROP TABLE" not in up.upper()
    assert "309_vkpi_dsar_public_intake.sql" in down and "DELETE FROM schema_migrations" in down
    sql_only = "\n".join(line for line in (up + "\n" + down).splitlines() if not line.lstrip().startswith("--"))
    assert "viltrox_fit_score" not in sql_only and "rule_v0" not in sql_only  # 红线只许出现在注释声明里
    _validate_runner_owned_transactions(("309_vkpi_dsar_public_intake.sql",))  # 无 BEGIN/COMMIT → 不抛


def test_router_source_never_names_the_fit_score_or_rule_v0() -> None:
    source = Path(dsar_public.__file__).read_text(encoding="utf-8")
    code = source.split('"""', 2)[2]  # 跳过模块 docstring(红线声明本身提到这两个词)
    code = "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("#"))
    assert "viltrox_fit_score" not in code and "rule_v0" not in code

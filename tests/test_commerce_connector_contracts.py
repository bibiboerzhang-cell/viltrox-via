"""Offline connector receipts → real SQLite attribution upserts, no provider IO."""
import base64
import asyncio
import hashlib
import hmac
import json
import sqlite3

import pytest
from starlette.datastructures import Headers

from app.domains import attribution
from app.domains.attribution import integrations, integrations_amazon, reconciliation, revenue
from app.domains.attribution.integrations_money import exact_cents
from app.domains.attribution.integrations_shopify_money import refund_money
from app.domains.commerce import shopify_connect
from app.domains.integrations import goaffpro_connect as go
from app.domains.integrations import goaffpro_connect_http as go_http
from app.domains.integrations.goaffpro_connect_sales import prepare_sales


@pytest.fixture
def ledger(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE orders (id INTEGER PRIMARY KEY);
        CREATE TABLE vkpi_sales_attributions (
          id INTEGER PRIMARY KEY, source_platform TEXT, source_ref TEXT,
          project_id INTEGER, link_id INTEGER, kol_id INTEGER, staff_id INTEGER,
          shopify_order_snapshot_id INTEGER, product_sku TEXT, order_id INTEGER,
          amazon_campaign_id TEXT, revenue_cents INTEGER, commission_cents INTEGER,
          currency TEXT, attribution_model TEXT, confidence TEXT, occurred_at TEXT,
          imported_at TEXT, evidence_json TEXT, created_at TEXT,
          UNIQUE(source_platform,source_ref));
        CREATE TABLE vkpi_shopify_order_snapshots (
          id INTEGER PRIMARY KEY, shopify_order_id TEXT UNIQUE, admin_graphql_api_id TEXT,
          order_name TEXT, order_number TEXT, processed_at TEXT, currency TEXT,
          subtotal_cents INTEGER, total_cents INTEGER, financial_status TEXT,
          fulfillment_status TEXT, refund_status TEXT, cancelled_at TEXT,
          provider_auth_mode TEXT, provider_verified_at TEXT, discount_codes_json TEXT,
          landing_site TEXT, note_attributes_json TEXT, line_items_json TEXT,
          raw_payload_hash TEXT, raw_payload_json TEXT, created_at TEXT, updated_at TEXT);
    """)
    for module in (revenue, reconciliation, integrations):
        monkeypatch.setattr(module, "get_conn", lambda: conn)
    monkeypatch.setattr(revenue, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(integrations, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(revenue, "_project_defaults", lambda _: {})
    monkeypatch.setattr(revenue.scope, "effective_staff_id", lambda _staff, owner: owner)
    monkeypatch.setattr(revenue.scope, "assert_project_access", lambda *_a, **_k: None)
    monkeypatch.setattr(revenue.audit, "log_business_event", lambda **_: None)
    monkeypatch.setattr(reconciliation, "_log_adjustment", lambda **_: None)
    monkeypatch.setattr(reconciliation, "enqueue_unmatched", lambda *_a, **_k: None)
    monkeypatch.setattr(shopify_connect, "get_credentials", lambda: {"webhook_secret": "fixture-secret"})
    monkeypatch.setattr(integrations, "_shopify_ref_context", lambda _: {
        "match": {"project_id": 1, "staff_id": 2}, "product_sku": "fixture", "match_source": "fixture",
    })
    from app.domains.attribution import gmv_outcome_bridge
    monkeypatch.setattr(gmv_outcome_bridge, "handle_attribution_row", lambda *_: None)
    monkeypatch.setattr(gmv_outcome_bridge, "handle_refund_row", lambda *_: None)
    yield conn
    conn.close()


def signed(payload, topic):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    digest = base64.b64encode(hmac.new(b"fixture-secret", raw, hashlib.sha256).digest()).decode()
    return Headers({"x-shopify-hmac-sha256": digest, "x-shopify-topic": topic}), raw


def test_shopify_order_refund_partial_and_replay_use_one_currency_and_delta(ledger):
    order = {"id": 42, "admin_graphql_api_id": "gid://shopify/Order/42", "currency": "EUR",
             "financial_status": "paid", "subtotal_price": "100", "total_price": "120", "current_total_price": "120"}
    for _ in range(2):
        result = integrations.ingest_shopify_order_webhook(*signed(order, "orders/paid"))
    assert result["attribution"]["revenue_cents"] == 12000
    refund = {"id": 99, "order_id": 42, "transactions": [
        {"kind": "refund", "status": "success", "amount": "25", "currency": "EUR"},
        {"kind": "refund", "status": "failure", "amount": "99", "currency": "EUR"},
        {"kind": "sale", "status": "success", "amount": "100", "currency": "EUR"}]}
    integrations.ingest_shopify_refund_webhook(*signed(refund, "refunds/create"))
    replay = integrations.ingest_shopify_refund_webhook(*signed(refund, "refunds/create"))
    assert replay["idempotent"] is True
    assert replay["attribution"]["revenue_cents"] == -2500
    assert replay["attribution"]["currency"] == "EUR"
    assert ledger.execute("SELECT refund_status FROM vkpi_shopify_order_snapshots").fetchone()[0] == "partially_refunded"
    order.update(financial_status="partially_refunded", current_total_price="95", current_subtotal_price="75")
    integrations.ingest_shopify_order_webhook(*signed(order, "orders/updated"))
    assert ledger.execute("SELECT SUM(revenue_cents) FROM vkpi_sales_attributions").fetchone()[0] == 9500
    refund["id"] = 100
    refund["transactions"] = [{"kind": "refund", "status": "success", "amount": "95", "currency": "EUR"}]
    integrations.ingest_shopify_refund_webhook(*signed(refund, "refunds/create"))
    assert ledger.execute("SELECT COUNT(*), SUM(revenue_cents) FROM vkpi_sales_attributions").fetchone()[:] == (3, 0)
    assert ledger.execute("SELECT refund_status FROM vkpi_shopify_order_snapshots").fetchone()[0] == "refunded"


def test_invalid_shopify_signature_never_prepares_database(monkeypatch):
    monkeypatch.setattr(shopify_connect, "get_credentials", lambda: {"webhook_secret": "fixture-secret"})
    monkeypatch.setattr(integrations, "ensure_vkpi_schema", lambda: pytest.fail("unauthenticated DB write"))
    for handler in (integrations.ingest_shopify_order_webhook, integrations.ingest_shopify_refund_webhook):
        with pytest.raises(PermissionError):
            handler(Headers({"x-shopify-hmac-sha256": "invalid"}), b"{}")


@pytest.mark.parametrize("payload", [
    {"total_refunded": "10", "currency": "USD"},
    {"refund_amount": "10"},
    {"transactions": [{"kind": "refund", "status": "pending", "amount": "10", "currency": "USD"}]},
    {"transactions": [{"kind": "refund", "status": "success", "amount": "NaN", "currency": "USD"}]},
])
def test_shopify_unknown_or_cumulative_refund_is_not_a_paid_delta(payload):
    with pytest.raises(ValueError):
        refund_money(payload)


def test_shopify_prefers_shop_money_and_rejects_unconverted_presentment():
    payload = {"transactions": [{"kind": "refund", "status": "success", "amount": "11", "currency": "USD",
                "amount_set": {"shop_money": {"amount": "10", "currency_code": "EUR"},
                               "presentment_money": {"amount": "11", "currency_code": "USD"}}}]}
    assert refund_money(payload, "EUR") == (1000, "EUR")
    del payload["transactions"][0]["amount_set"]
    with pytest.raises(ValueError, match="FX reconciliation"):
        refund_money(payload, "EUR")


def test_shopify_refund_without_confirmed_base_cannot_create_negative_verified_gmv(ledger, monkeypatch):
    from app.domains import business_truth
    monkeypatch.setattr(integrations, "_shopify_ref_context", lambda _: {"match": {}})
    order = {"id": 42, "currency": "EUR", "financial_status": "paid", "total_price": "120"}
    integrations.ingest_shopify_order_webhook(*signed(order, "orders/paid"))
    refund = {"id": 99, "order_id": 42, "refund_amount": "25", "currency": "EUR"}
    result = integrations.ingest_shopify_refund_webhook(*signed(refund, "refunds/create"))
    assert result["attribution"]["confidence"] == "unmatched"
    value = ledger.execute("SELECT COALESCE(SUM(revenue_cents),0) FROM vkpi_sales_attributions WHERE " + business_truth.verified_shopify_attribution_sql()).fetchone()[0]
    assert value == 0


@pytest.mark.parametrize("name,separator", [("report.csv", ","), ("report.tsv", "\t")])
def test_amazon_file_to_real_ledger_keeps_cents_currency_refunds_and_repeat_upload(ledger, name, separator):
    content = separator.join(["date", "tag", "asin", "revenue", "commission", "currency", "type"]) + "\n"
    content += separator.join(["2026-09-04", "fixture", "A1", "12.34", "1.25", "EUR", "sale"]) + "\n"
    content += separator.join(["2026-09-04", "fixture", "A1", "2.34", "0.25", "EUR", "refund"]) + "\n"
    for filename in (name, "renamed." + name.split(".")[1]):
        result = integrations_amazon.import_amazon_report(content.encode(), filename, {"project_id": 1, "staff_id": 2})
    assert result["count"] == 2
    rows = ledger.execute("SELECT revenue_cents,commission_cents,currency,confidence FROM vkpi_sales_attributions ORDER BY revenue_cents DESC").fetchall()
    assert [tuple(row) for row in rows] == [(1234, 125, "EUR", "human_verified"), (-234, -25, "EUR", "human_verified")]


def test_amazon_daily_grain_aggregates_rows_without_overwriting_each_other():
    raw = b"date,tag,asin,revenue,currency\n2026-09-04,T,A,10,USD\n2026-09-04,T,A,20,USD\n"
    rows = integrations_amazon.parse_amazon_report_bytes(raw)
    assert len(rows) == 1 and rows[0]["revenue_cents"] == 3000


@pytest.mark.parametrize("raw", [
    b"date,revenue\n2026-09-04,10\n", b"date,revenue,currency\n2026-09-04,NaN,USD\n",
    b"date,revenue,currency\n2026-09-04,10,USD,extra\n", b"unrecognized\nvalue\n", b"\xff",
    b"revenue,currency\n10,USD\n", b"date,revenue_usd,currency\n2026-09-04,10,EUR\n",
])
def test_amazon_invalid_file_is_rejected_before_import(raw, monkeypatch):
    monkeypatch.setattr(attribution, "import_amazon", lambda *_a, **_k: pytest.fail("invalid file wrote ledger"))
    with pytest.raises((ValueError, UnicodeError)):
        integrations_amazon.import_amazon_report(raw, "report.csv", {})


def test_amazon_json_validates_entire_batch_before_writing(ledger):
    with pytest.raises(ValueError):
        revenue.import_amazon({"rows": [
            {"source_ref": "valid", "revenue_cents": 123, "currency": "USD"},
            {"source_ref": "invalid", "revenue_cents": "inf", "currency": "USD"}]})
    assert ledger.execute("SELECT COUNT(*) FROM vkpi_sales_attributions").fetchone()[0] == 0


@pytest.mark.parametrize("value", ["NaN", "inf", "-inf", True, "1e10000"])
def test_money_nonfinite_or_out_of_bounds_is_rejected(value):
    with pytest.raises(ValueError):
        exact_cents(value)


def test_goaffpro_pagination_follows_reported_total_even_when_provider_caps_page(monkeypatch):
    offsets = []
    def get(_path, params):
        offsets.append(params["offset"])
        return {"ok": True, "data": {"orders": [{"id": params["offset"] + 1}], "total_results": 3}}
    monkeypatch.setattr(go, "_get", get)
    result = go._paginate_rows("admin/orders", {}, ("orders",))
    assert result["ok"] and not result["partial"] and offsets == [0, 1, 2]


def test_goaffpro_repeated_page_and_page_cap_are_explicitly_partial(monkeypatch):
    monkeypatch.setattr(go, "_get", lambda *_: {"ok": True, "data": {"orders": [{"id": 1}], "total_results": 3}})
    result = go._paginate_rows("admin/orders", {}, ("orders",))
    assert result["partial"] and result["error"] == "repeated_page" and len(result["rows"]) == 1
    assert go._paginate_rows("admin/orders", {}, ("orders",), max_pages=1)["error"] == "pagination_page_limit_exceeded"


def test_goaffpro_not_configured_reason_survives_pagination(monkeypatch):
    monkeypatch.setattr(go, "_get", lambda *_: {"ok": False, "reason": "not_configured"})
    result = go.list_orders(fetch_all=True)
    assert not result["ok"] and result["reason"] == "not_configured"


@pytest.mark.parametrize("status", ["pending", "error", "revoked"])
def test_goaffpro_token_presence_does_not_override_authorization_state(monkeypatch, status):
    monkeypatch.setattr(go, "get_credentials", lambda: {"access_token": "fixture", "source": "db", "status": status})
    assert go.connection_status()["status"] == status
    if status == "revoked":
        monkeypatch.setattr(go_http.httpx, "Client", lambda **_: pytest.fail("revoked credential used"))
        assert go_http._get("admin/orders")["reason"] == "revoked"
        assert go_http._post("admin/orders")["reason"] == "revoked"
        assert go_http._patch("admin/orders/1")["reason"] == "revoked"


def test_goaffpro_mapping_preserves_coupon_zero_and_exact_commission():
    mapped = go._map_order({"id": 1, "total": 0, "order_total": 100, "commission": "1.005", "currency": "eur", "coupon": {"code": "HELLO"}})
    rows = prepare_sales([mapped], [{"affiliate_id": "7", "kol_pool_id": 2, "coupon": "hello"}])
    assert rows[0]["total_cents"] == 0 and rows[0]["commission_cents"] == 101
    assert rows[0]["kol_pool_id"] == 2 and rows[0]["currency"] == "EUR"
    links = [{"affiliate_id": "7", "kol_pool_id": 2, "coupon": "HELLO"}, {"affiliate_id": "8", "kol_pool_id": 3, "coupon": "HELLO"}]
    assert prepare_sales([mapped], links)[0]["kol_pool_id"] is None
    mapped["affiliate_id"] = "unknown"
    assert prepare_sales([mapped], links[:1])[0]["kol_pool_id"] is None


@pytest.mark.parametrize("mutation", [{"currency": ""}, {"commission": None}, {"total": "inf"}, {"id": ""}])
def test_goaffpro_unknown_accounting_fields_do_not_become_zero(mutation):
    order = {"id": 1, "total": "10", "commission": "1", "currency": "USD", **mutation}
    with pytest.raises(ValueError):
        prepare_sales([order], [])


@pytest.fixture
def sync_ledger(ledger, monkeypatch):
    from app.domains.attribution import integrations_shopify_sync as sync
    ledger.execute("""CREATE TABLE vkpi_shopify_sync_runs (
        id INTEGER PRIMARY KEY, sync_uid TEXT UNIQUE, source TEXT, started_at TEXT, completed_at TEXT,
        status TEXT, orders_received INTEGER DEFAULT 0, orders_matched INTEGER DEFAULT 0,
        orders_unmatched INTEGER DEFAULT 0, orders_failed INTEGER DEFAULT 0, error_message TEXT,
        triggered_by_staff_id INTEGER, metadata_json TEXT)""")
    monkeypatch.setattr(sync, "get_conn", lambda: ledger)
    monkeypatch.setattr(sync, "ensure_vkpi_reconciliation_schema", lambda: None)
    monkeypatch.setattr(sync, "release_validation_active", lambda: False)
    monkeypatch.setattr(shopify_connect, "get_credentials", lambda: {
        "shop_domain": "fixture.myshopify.com", "status": "connected", "access_token": "fixture-token", "webhook_secret": "fixture-secret"})
    return ledger


class FakeQueue:
    backend_name = "redis-stream"

    def __init__(self):
        self.jobs = {}

    async def enqueue(self, job_type, payload, **kwargs):
        task_id = "task-" + str(len(self.jobs) + 1)
        self.jobs[task_id] = {"task_id": task_id, "job_type": job_type, "payload": payload, "status": "queued", **kwargs}
        return task_id

    async def get_status(self, task_id):
        return self.jobs.get(task_id)

    async def set_status(self, task_id, status, **kwargs):
        self.jobs[task_id].update(status=status, **kwargs)


def api_page(order_id=42, *, after=None, more=False):
    return {"ok": True, "data": {"orders": {"pageInfo": {"hasNextPage": more, "endCursor": after}, "nodes": [{
        "id": f"gid://shopify/Order/{order_id}", "legacyResourceId": str(order_id), "currencyCode": "EUR",
        "displayFinancialStatus": "PAID", "currentTotalPriceSet": {"shopMoney": {"amount": "120", "currencyCode": "EUR"}},
        "totalPriceSet": {"shopMoney": {"amount": "120", "currencyCode": "EUR"}},
        "lineItems": {"nodes": [], "pageInfo": {"hasNextPage": False}}, "discountCodes": []}]}}}


def enqueue_and_run(queue, body=None):
    from app.domains.attribution import integrations_shopify_sync as sync
    from app.services.jobs.processor import process_background_job
    result = asyncio.run(sync.enqueue_sync(queue, body or {}, staff={"id": 1}, mode="sync"))
    asyncio.run(process_background_job(queue, queue.jobs[result["task_id"]]))
    return result, queue.jobs[result["task_id"]]


def test_shopify_api_enqueue_to_worker_then_webhook_and_refund_never_double_count_verified_gmv(sync_ledger, monkeypatch):
    from app.domains import business_truth
    monkeypatch.setattr(shopify_connect, "post_graphql", lambda *_a, **_k: api_page())
    queue = FakeQueue()
    receipt, job = enqueue_and_run(queue)
    assert job["status"] == "completed"
    assert sync_ledger.execute("SELECT status FROM vkpi_shopify_sync_runs").fetchone()[0] == "completed"
    predicate = business_truth.verified_shopify_attribution_sql()
    verified = lambda: sync_ledger.execute("SELECT COALESCE(SUM(revenue_cents),0) FROM vkpi_sales_attributions WHERE " + predicate).fetchone()[0]
    assert verified() == 0
    order = {"id": 42, "admin_graphql_api_id": "gid://shopify/Order/42", "currency": "EUR", "financial_status": "paid", "total_price": "120"}
    integrations.ingest_shopify_order_webhook(*signed(order, "orders/paid"))
    assert verified() == 12000
    enqueue_and_run(queue)
    assert verified() == 12000
    refund = {"id": 99, "order_id": 42, "transactions": [{"kind": "refund", "status": "success", "amount": "25", "currency": "EUR"}]}
    for _ in range(2):
        integrations.ingest_shopify_refund_webhook(*signed(refund, "refunds/create"))
    assert verified() == 9500
    assert sync_ledger.execute("SELECT COUNT(*) FROM vkpi_sales_attributions").fetchone()[0] == 3
    rows = sync_ledger.execute("SELECT evidence_json FROM vkpi_sales_attributions WHERE confidence <> 'refund'").fetchall()
    assert {json.loads(row[0])["source_order_id"] for row in rows} == {"gid://shopify/Order/42"}


def test_shopify_bounded_page_cap_persists_resume_and_continues_without_reimporting_prior_pages(sync_ledger, monkeypatch):
    calls = []
    def query(_query, variables, **_kwargs):
        calls.append(variables["after"])
        number = len(calls)
        return api_page(number, after=f"c{number}", more=number < 6)
    monkeypatch.setattr(shopify_connect, "post_graphql", query)
    queue = FakeQueue()
    first, job = enqueue_and_run(queue)
    assert job["status"] == "failed" and job["result"]["status"] == "partial"
    row = sync_ledger.execute("SELECT status,metadata_json FROM vkpi_shopify_sync_runs WHERE sync_uid=?", (first["sync_uid"],)).fetchone()
    assert row[0] == "partial" and json.loads(row[1])["resume_cursor"] == "c5"
    _, job2 = enqueue_and_run(queue, {"resume_sync_uid": first["sync_uid"]})
    assert job2["status"] == "completed" and calls == [None, "c1", "c2", "c3", "c4", "c5"]
    assert sync_ledger.execute("SELECT COUNT(*) FROM vkpi_sales_attributions").fetchone()[0] == 6


def test_shopify_429_stops_and_saves_last_complete_page_cursor(sync_ledger, monkeypatch):
    responses = iter([api_page(1, more=True, after="c1"), {"ok": False, "status_code": 429}])
    monkeypatch.setattr(shopify_connect, "post_graphql", lambda *_a, **_k: next(responses))
    _, job = enqueue_and_run(FakeQueue())
    assert job["status"] == "failed" and job["result"]["reason"] == "shopify_rate_limited"
    row = sync_ledger.execute("SELECT status,metadata_json,orders_received FROM vkpi_shopify_sync_runs").fetchone()
    assert row[0] == "failed" and json.loads(row[1])["resume_cursor"] == "c1" and row[2] == 1


@pytest.mark.parametrize("configuration", [{}, {"shop_domain": "fixture.myshopify.com", "access_token": "fixture", "status": "pending"}])
def test_shopify_missing_or_unverified_authorization_cannot_enqueue(sync_ledger, monkeypatch, configuration):
    from app.domains.attribution import integrations_shopify_sync as sync
    monkeypatch.setattr(shopify_connect, "get_credentials", lambda: configuration)
    queue = FakeQueue()
    with pytest.raises(RuntimeError):
        asyncio.run(sync.enqueue_sync(queue, {}, staff={}, mode="sync"))
    assert not queue.jobs
    assert sync_ledger.execute("SELECT COUNT(*) FROM vkpi_shopify_sync_runs").fetchone()[0] == 0


def test_shopify_queue_failure_is_terminal_not_falsely_queued(sync_ledger):
    from app.domains.attribution import integrations_shopify_sync as sync
    class BrokenQueue(FakeQueue):
        async def enqueue(self, *_a, **_k):
            raise RuntimeError("queue unavailable")
    with pytest.raises(RuntimeError, match="queue_enqueue_failed"):
        asyncio.run(sync.enqueue_sync(BrokenQueue(), {}, staff={}, mode="sync"))
    assert sync_ledger.execute("SELECT status FROM vkpi_shopify_sync_runs").fetchone()[0] == "failed"


@pytest.mark.parametrize("code,reason", [("THROTTLED", "shopify_rate_limited"), ("ACCESS_DENIED", "shopify_permission_denied")])
def test_shopify_graphql_soft_errors_stop_without_writing_orders(sync_ledger, monkeypatch, code, reason):
    monkeypatch.setattr(shopify_connect, "post_graphql", lambda *_a, **_k: {"ok": False, "errors": [{"extensions": {"code": code}}]})
    _, job = enqueue_and_run(FakeQueue())
    assert job["status"] == "failed" and job["result"]["reason"] == reason
    assert sync_ledger.execute("SELECT COUNT(*) FROM vkpi_sales_attributions").fetchone()[0] == 0


def test_shopify_sync_route_checks_manager_and_release_before_io(sync_ledger, monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.api.routers import vkpi_attribution_metrics as routes
    from app.domains.attribution import integrations_shopify_sync as sync
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_queue=FakeQueue())))
    for handler in (routes.shopify_sync, routes.shopify_backfill):
        with pytest.raises(HTTPException) as denied:
            asyncio.run(handler(request, staff={"id": 1, "role": "employee"}))
        assert denied.value.status_code == 403
    monkeypatch.setattr(sync, "release_validation_active", lambda: True)
    monkeypatch.setattr(shopify_connect, "get_credentials", lambda: pytest.fail("release fence reached credentials"))
    with pytest.raises(HTTPException) as blocked:
        asyncio.run(routes.shopify_sync(request, staff={"id": 1, "role": "manager"}))
    assert blocked.value.status_code == 503


def test_shopify_window_and_inprocess_limits_fail_closed(sync_ledger):
    from app.domains.attribution import integrations_shopify_sync as sync
    from app.services.jobs.queue_common import DURABLE_PROVIDER_JOB_TYPES
    assert sync.JOB_TYPE in DURABLE_PROVIDER_JOB_TYPES
    with pytest.raises(ValueError):
        sync.window({"start_at": sync.iso(sync.now() - sync.timedelta(days=61))})
    queue = FakeQueue()
    queue.backend_name = "inprocess"
    with pytest.raises(RuntimeError, match="durable_queue_required"):
        asyncio.run(sync.enqueue_sync(queue, {}, staff={}, mode="sync"))


def test_goaffpro_failed_metrics_keep_last_complete_values(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE vkpi_goaffpro_kol_links (kol_pool_id INTEGER, affiliate_id TEXT)")
    conn.execute("INSERT INTO vkpi_goaffpro_kol_links VALUES (1,'A')")
    monkeypatch.setattr(go, "get_conn", lambda: conn)
    monkeypatch.setattr(go, "ensure_goaffpro_links_schema", lambda: None)
    monkeypatch.setattr(go, "connection_status", lambda: {"status": "connected"})
    monkeypatch.setattr(go, "affiliate_attribution", lambda _: {"ok": False, "partial": True})
    monkeypatch.setattr(go, "get_affiliate", lambda _: {"ok": False})
    result = go.sync_kol_metrics()
    assert not result["ok"] and result["synced"] == 0 and result["errors"] == 1
    conn.close()


def test_goaffpro_credentials_only_become_connected_after_successful_read_probe(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(go, "get_conn", lambda: conn)
    monkeypatch.setattr(go, "_SCHEMA_READY", False)
    monkeypatch.setattr(go, "release_validation_active", lambda: False)
    monkeypatch.setattr(go, "is_postgres_runtime", lambda: False)
    configured = go.save_credentials({"access_token": "fixture-only-token"})
    assert configured["status"] == "pending"
    assert go.connection_status()["status"] == "pending"
    monkeypatch.setattr(go, "_get", lambda *_: {"ok": True, "data": {"orders": [], "affiliates": []}})
    assert go.sync_stub()["ok"] and go.connection_status()["status"] == "connected"
    go.save_credentials({"access_token": "replacement-fixture-token"})
    assert go.connection_status()["status"] == "pending"
    assert conn.execute("SELECT connected_at FROM vkpi_goaffpro_credentials").fetchone()[0] is None
    monkeypatch.setattr(go, "_get", lambda *_: {"ok": False, "status_code": 401, "error": "http 401"})
    assert not go.sync_stub()["ok"] and go.connection_status()["status"] == "error"
    conn.close()


def test_goaffpro_confirmed_sales_keep_currencies_separate_and_ignore_refunds(monkeypatch):
    orders = [
        {"id": 1, "total": "100", "commission": "10", "currency": "USD", "status": "approved"},
        {"id": 2, "total": "30", "commission": "3", "currency": "EUR", "status": "paid"},
        {"id": 3, "total": "999", "commission": "99", "currency": "USD", "status": "refund"},
    ]
    monkeypatch.setattr(go, "list_traffic", lambda **_: {"ok": True, "total": 4})
    monkeypatch.setattr(go, "_paginate_rows", lambda *_: {"ok": True, "rows": orders})
    result = go.affiliate_attribution("affiliate")
    assert result["ok"] and result["gmv_cents"] == 10000 and result["commission_cents"] == 1000
    assert result["currency"] == "USD" and result["mixed_currency"]
    assert {b["currency"]: b["gmv_cents"] for b in result["by_currency"]} == {"USD": 10000, "EUR": 3000}
    orders[0]["currency"] = ""
    assert not go.affiliate_attribution("affiliate")["ok"]


def test_shopify_same_run_worker_redelivery_resumes_database_checkpoint_and_counters(sync_ledger, monkeypatch):
    from app.domains.attribution.integrations_shopify_sync_runtime import run_sync
    calls = []
    def query(_query, variables, **kwargs):
        assert 0 < kwargs["timeout_seconds"] <= 90
        calls.append(variables["after"])
        if len(calls) == 1:
            return api_page(1, more=True, after="c1")
        if len(calls) == 2:
            return {"ok": False, "status_code": 429}
        return api_page(2)
    monkeypatch.setattr(shopify_connect, "post_graphql", query)
    queue = FakeQueue()
    _, job = enqueue_and_run(queue)
    result = run_sync(job["payload"])  # Original payload still has a null cursor.
    assert result["status"] == "completed" and result["orders_received"] == 2
    assert calls == [None, "c1", "c1"]
    again = run_sync(job["payload"])
    assert again["idempotent"] and again["status"] == "completed" and len(calls) == 3
    assert sync_ledger.execute("SELECT orders_received FROM vkpi_shopify_sync_runs").fetchone()[0] == 2


def test_shopify_running_state_checkpoint_survives_worker_interruption_before_next_page(sync_ledger, monkeypatch):
    from app.domains.attribution import integrations_shopify_sync as sync
    from app.domains.attribution.integrations_shopify_sync_runtime import run_sync
    queue = FakeQueue()
    receipt = asyncio.run(sync.enqueue_sync(queue, {}, staff={"id": 1}, mode="sync"))
    uid = receipt["sync_uid"]
    metadata = json.loads(sync_ledger.execute("SELECT metadata_json FROM vkpi_shopify_sync_runs WHERE sync_uid=?", (uid,)).fetchone()[0])
    metadata.update(pages=1, resume_cursor="c1", has_next_page=True)
    sync.save_state(uid, "running", metadata, received=20, matched=7)

    class SimulatedWorkerExit(BaseException):
        pass

    def interrupted(*_args, **_kwargs):
        raise SimulatedWorkerExit

    # The exit happens immediately after the handler persisted its running
    # state, before a second provider page; BaseException models process death.
    monkeypatch.setattr(shopify_connect, "post_graphql", interrupted)
    original_payload = queue.jobs[receipt["task_id"]]["payload"]
    with pytest.raises(SimulatedWorkerExit):
        run_sync(original_payload)
    stored = sync_ledger.execute("SELECT orders_received,orders_matched,metadata_json FROM vkpi_shopify_sync_runs WHERE sync_uid=?", (uid,)).fetchone()
    assert stored[:2] == (20, 7)
    assert json.loads(stored[2])["resume_cursor"] == "c1"
    cursors = []
    def recovered(_query, variables, **_kwargs):
        cursors.append(variables["after"])
        return api_page(21)
    monkeypatch.setattr(shopify_connect, "post_graphql", recovered)
    result = run_sync(original_payload)
    assert result["status"] == "completed" and result["orders_received"] == 21
    assert cursors == ["c1"]
    assert sync_ledger.execute("SELECT orders_received,orders_matched FROM vkpi_shopify_sync_runs WHERE sync_uid=?", (uid,)).fetchone()[:] == (21, 8)


def test_shopify_late_final_page_is_checkpointed_but_not_claimed_on_time(sync_ledger, monkeypatch):
    from app.domains.attribution import integrations_shopify_sync_runtime as runtime
    clock = [0.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    def late(*_args, **kwargs):
        assert kwargs["timeout_seconds"] == 90
        clock[0] = 91
        return api_page()
    monkeypatch.setattr(shopify_connect, "post_graphql", late)
    _, job = enqueue_and_run(FakeQueue())
    assert job["status"] == "failed" and job["result"]["reason"] == "sync_deadline_exceeded"
    stored = sync_ledger.execute("SELECT metadata_json,orders_received FROM vkpi_shopify_sync_runs").fetchone()
    assert json.loads(stored[0])["deadline_exceeded"] and stored[1] == 1
    monkeypatch.setattr(shopify_connect, "post_graphql", lambda *_a, **_k: pytest.fail("completed page was fetched again"))
    assert runtime.run_sync(job["payload"])["idempotent"]


def test_shopify_canonical_timeout_deducts_credential_preparation(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(shopify_connect.time, "monotonic", lambda: clock[0])
    def credentials():
        clock[0] += 3
        return {"access_token": "fixture", "shop_domain": "fixture.myshopify.com"}
    monkeypatch.setattr(shopify_connect, "_admin_credentials", credentials)
    captured = []
    def post(_creds, _query, _variables, **kwargs):
        captured.append(kwargs["timeout_seconds"])
        return {"ok": True}
    monkeypatch.setattr(shopify_connect, "_post_graphql_with_credentials", post)
    assert shopify_connect.post_graphql("query", timeout_seconds=5)["ok"]
    assert captured == [2]
    assert shopify_connect.post_graphql("query", timeout_seconds=1)["reason"] == "deadline_exceeded"
    assert captured == [2]
    for invalid in (float("nan"), float("inf"), 0):
        assert shopify_connect.post_graphql("query", timeout_seconds=invalid)["reason"] == "deadline_exceeded"


def test_shopify_sync_runtime_respects_existing_complexity_ceiling():
    import ast
    from pathlib import Path
    from app.domains.attribution import integrations_shopify_sync_runtime as runtime
    from scripts.vkpi_engineering_health_collect import collect_complexity

    source = Path(runtime.__file__)
    rows = collect_complexity({source.name: ast.parse(source.read_text())})
    scores = {row.qualified_name: row.cc for row in rows}
    assert scores["run_sync"] <= 40
    assert all(score <= 40 for score in scores.values()), scores
    print(scores)

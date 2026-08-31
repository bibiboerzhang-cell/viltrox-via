"""Behavior locks for affiliate-order reward trace synchronization."""
from __future__ import annotations

from app.services.memory import via_learning_affiliate as affiliate


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, orders, users):
        self.orders = orders
        self.users = users

    def execute(self, sql, _params=()):
        if "FROM platform_ingest_events" in sql:
            return _Rows(self.orders)
        if "FROM users" in sql:
            return _Rows(self.users)
        raise AssertionError(sql)


def _order(identifier: int, *, email: str, external_id: str) -> dict:
    return {
        "id": identifier,
        "external_id": external_id,
        "creator_handle": "",
        "occurred_at": "",
        "processed_at": "",
        "ingest_status": "processed",
        "payload_json": {
            "body": {
                "id": identifier,
                "customer": {"email": email},
                "current_total_price": "125.00",
                "financial_status": "paid",
            }
        },
    }


def test_affiliate_sync_preserves_match_skip_and_insert_accounting(monkeypatch) -> None:
    orders = [
        _order(2, email="creator@example.com", external_id="duplicate"),
        _order(1, email="Creator@Example.com", external_id="new-order"),
    ]
    users = [{"id": 7, "creator_code": "creator-seven", "email": "creator@example.com"}]
    inserted: list[dict] = []
    monkeypatch.setattr(affiliate, "get_conn", lambda: _Conn(orders, users))
    monkeypatch.setattr(
        affiliate,
        "list_recent_via_decisions",
        lambda _limit: [{"user_id": 7, "session_key": "via-7", "decision_id": "decision-7"}],
    )
    monkeypatch.setattr(
        affiliate,
        "get_via_reward_trace_by_idempotency_key",
        lambda key: {"id": 99} if key == "shopify-order:duplicate" else None,
    )
    monkeypatch.setattr(
        affiliate,
        "build_creator_program_snapshot",
        lambda _user: {"effective_commission_rate": 0.12},
    )
    monkeypatch.setattr(
        affiliate,
        "insert_via_reward_trace",
        lambda **kwargs: inserted.append(kwargs) or kwargs,
    )

    result = affiliate._sync_affiliate_order_reward_traces(limit=10, window_days=21)

    assert result == {"imported": 1, "skipped": 1, "matched_users": 2}
    assert len(inserted) == 1
    trace = inserted[0]
    assert trace["session_key"] == "via-7"
    assert trace["decision_id"] == "decision-7"
    assert trace["user_id"] == 7
    assert trace["product_key"] == "creator@example.com"
    assert trace["event_value"] == 125.0
    assert trace["event_payload"]["estimated_commission"] == 15.0
    assert trace["idempotency_key"] == "shopify-order:new-order"


def test_affiliate_sync_keeps_database_failure_fallback(monkeypatch) -> None:
    class _BrokenConn:
        def execute(self, _sql, _params=()):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(affiliate, "get_conn", lambda: _BrokenConn())

    assert affiliate._sync_affiliate_order_reward_traces() == {
        "imported": 0,
        "skipped": 0,
        "matched_users": 0,
    }

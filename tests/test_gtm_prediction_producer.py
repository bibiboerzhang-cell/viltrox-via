"""Hermetic truth tests for the first registered GTM prediction path."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from app.db import connection
from app.domains.market_brain import (
    data_readiness,
    gtm_bets,
    gtm_prediction_producer,
    gtm_windows,
    outreach_reply_truth,
    prediction_ledger,
    prediction_truth,
)


DB_CLOCK = "2026-08-11T01:02:03+00:00"


def _contract(**overrides: Any) -> dict[str, Any]:
    value = prediction_truth.build_registered_gtm_evaluation_contract(
        gtm_prediction_producer.REGISTRY_KEY,
        target_action_inbox_id=41,
        observation_start_at=DB_CLOCK,
    )
    value.update(overrides)
    return value


def _evaluable_payload(contract: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "task_type": "kol_outreach_reply_probability",
        "horizon_days": 7,
        "input_summary": {"evaluation_contract": contract or _contract()},
        "prediction": {
            "metric_key": "reply_outcome", "unit": "ratio", "value": 0.1,
            "p10": 0.05, "p50": 0.1, "p90": 0.2,
        },
        "p10": 0.05,
        "p50": 0.1,
        "p90": 0.2,
        "confidence": "low",
        "source_step": "rule",
    }


@pytest.mark.parametrize(
    ("field", "tampered"),
    [
        ("outcome_action_type", "official_post"),
        ("task_type", "other_task"),
        ("metric_key", "reply_n"),
        ("metric_path", "metrics.reply_n"),
        ("unit", "count"),
        ("evidence_field", "window_14d"),
        ("horizon_days", 14),
    ],
)
def test_server_registry_rejects_any_evaluation_tuple_tamper(field: str, tampered: Any) -> None:
    assert prediction_truth.parse_evaluation_contract(
        {"input_summary": {"evaluation_contract": _contract(**{field: tampered})}}
    ) is None


def test_registered_prediction_point_estimate_must_equal_p50() -> None:
    payload = _evaluable_payload()
    payload["prediction"] = {**payload["prediction"], "value": 0.2}
    assert prediction_truth.evaluable_prediction_error(payload) == "evaluable_prediction_point_mismatch"


def test_real_gtm_kol_bet_emits_only_the_registered_provider_free_seed() -> None:
    items = gtm_bets._kol_outreach_items(
        sku="AF-26",
        goal="exposure",
        plan_id="plan-1",
        kol_section={
            "items": [{
                "kol_pool_id": 17,
                "handle": "creator",
                "platform": "youtube",
                "cost_usd_p50": 100,
                "cost_confidence": "low",
                "expected_views_p50": 5000,
                "risk_labels": [],
            }]
        },
        esc="reply outcome observed -> review",
        ret="no reply -> review",
        now=datetime(2026, 8, 11, tzinfo=timezone.utc),
    )
    seed = items[0]["bet"]["prediction_seed"]
    assert seed["registry_key"] == gtm_prediction_producer.REGISTRY_KEY
    assert seed["p50"] == 0.1 and seed["p10"] == 0.05 and seed["p90"] == 0.2
    assert seed["channel"] == "youtube" and seed["kol_pool_id"] == 17


class _Cursor:
    def __init__(self, *, row: Any = None, rows: list[Any] | None = None) -> None:
        self._row = row
        self._rows = rows or []

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return self._rows


class _ProducerConn:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        del params
        if "FROM vkpi_action_inbox" in sql:
            return _Cursor(rows=[{
                "id": 41,
                "dedupe_key": "gtm_bet:plan:kol:17",
                "payload_json": self.payload,
            }])
        if "SELECT input_summary FROM vkpi_prediction_runs" in sql:
            return _Cursor(row=None)
        if "CURRENT_TIMESTAMP AS server_now" in sql:
            return _Cursor(row={"server_now": DB_CLOCK})
        raise AssertionError(sql)


def _materialized_payload() -> dict[str, Any]:
    return {
        "gtm_plan_id": "plan-1",
        "sku": "AF-26",
        "country": "US",
        "bet": {
            "action_type": "kol_outreach",
            "prediction_seed": {
                "schema": gtm_prediction_producer.PRODUCER_SCHEMA,
                "registry_key": gtm_prediction_producer.REGISTRY_KEY,
                "method": "gtm_outreach_reply_probability_rule",
                "p10": 0.05,
                "p50": 0.1,
                "p90": 0.2,
                "confidence": "low",
                "channel": "youtube",
                "kol_pool_id": 17,
                "basis": ["provider-free rule baseline"],
            },
        },
    }


def test_materialized_producer_uses_one_connection_and_one_database_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _ProducerConn(_materialized_payload())
    captured: dict[str, Any] = {}
    monkeypatch.setattr(connection, "table_exists", lambda _name: True)

    def record(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True, "id": 8, "deduped": False}

    monkeypatch.setattr(prediction_ledger, "record_prediction_run", record)
    result = gtm_prediction_producer.record_materialized_bet_predictions(
        ["gtm_bet:plan:kol:17"], conn=db,
    )

    assert result["recorded"] == 1 and result["failed"] == 0
    assert captured["_connection"] is db
    assert captured["_created_at"] == DB_CLOCK
    contract = prediction_truth.parse_evaluation_contract({
        "input_summary": captured["input_summary"],
    })
    assert contract is not None
    assert contract["observation_start_at"] == DB_CLOCK
    assert captured["prediction"]["value"] == captured["p50"] == 0.1
    assert captured["p10"] == 0.05 and captured["p90"] == 0.2


class _LedgerConn:
    def __init__(self) -> None:
        self.insert_sql = ""
        self.insert_params: tuple[Any, ...] = ()
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        if "INSERT INTO vkpi_prediction_runs" in sql:
            self.insert_sql = sql
            self.insert_params = tuple(params)
            return _Cursor(row={"id": 8})
        raise AssertionError(sql)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        raise AssertionError("registered first write must not roll back")


def test_ledger_writes_contract_clock_as_run_created_at(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _LedgerConn()
    monkeypatch.setattr(connection, "table_exists", lambda _name: True)
    payload = _evaluable_payload()
    result = prediction_ledger.record_prediction_run(
        "gtmact_41_kol_outreach_reply_outcome_7d",
        "gtm_outreach_reply_probability_rule",
        "v1",
        payload["task_type"],
        payload["prediction"],
        product_sku="AF-26",
        market="US",
        channel="youtube",
        horizon_days=7,
        input_summary=payload["input_summary"],
        p10=0.05,
        p50=0.1,
        p90=0.2,
        confidence="low",
        source_step="rule",
        _connection=db,
        _created_at=DB_CLOCK,
    )
    assert result == {"ok": True, "id": 8, "deduped": False}
    assert "source_step, created_at" in db.insert_sql
    assert db.insert_params[-1] == DB_CLOCK
    assert db.commits == 1


def test_ledger_rejects_contract_and_created_at_clock_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _LedgerConn()
    monkeypatch.setattr(connection, "table_exists", lambda _name: True)
    payload = _evaluable_payload()
    result = prediction_ledger.record_prediction_run(
        "gtmact_41_kol_outreach_reply_outcome_7d", "rule", "v1",
        payload["task_type"], payload["prediction"],
        product_sku="AF-26", market="US", channel="youtube", horizon_days=7,
        input_summary=payload["input_summary"], p10=0.05, p50=0.1, p90=0.2,
        confidence="low",
        source_step="rule", _connection=db,
        _created_at="2026-08-11T01:02:04+00:00",
    )
    assert result["reason"] == "evaluable_prediction_server_clock_mismatch"
    assert db.insert_sql == ""


class _AnchorConn:
    def __init__(self, *, created_at: str) -> None:
        self.created_at = created_at

    def execute(self, _sql: str, _params: tuple[Any, ...] = ()) -> _Cursor:
        return _Cursor(row={
            "run_id": gtm_prediction_producer.prediction_run_id(41),
            "created_at": self.created_at,
            "input_summary": json.dumps({"evaluation_contract": _contract()}),
        })


def test_registered_anchor_requires_run_clock_to_equal_contract_clock() -> None:
    exact = gtm_prediction_producer.registered_observation_anchors(
        _AnchorConn(created_at=DB_CLOCK), 41,
    )
    assert exact["window_7d"]["observation_start_at"] == DB_CLOCK
    assert gtm_prediction_producer.registered_observation_anchors(
        _AnchorConn(created_at="2026-08-11T01:02:02+00:00"), 41,
    ) == {}


class _GateConn:
    def __init__(self, evidence: dict[str, Any] | None) -> None:
        self.evidence = evidence

    def execute(self, _sql: str, _params: tuple[Any, ...] = ()) -> _Cursor:
        return _Cursor(row={"id": 71, "window_7d": self.evidence} if self.evidence else None)


def test_registered_verdict_waits_for_the_exact_sealed_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = {
        "window_7d": {
            "prediction_run_id": gtm_prediction_producer.prediction_run_id(41),
            "observation_start_at": DB_CLOCK,
        }
    }
    monkeypatch.setattr(
        gtm_prediction_producer, "registered_observation_anchors",
        lambda *_args, **_kwargs: anchor,
    )
    not_ready = gtm_prediction_producer.registered_prediction_verdict_gate(
        _GateConn(None), 41, outcome_id=71,
    )
    assert not_ready["required"] is True and not_ready["ready"] is False

    evidence = data_readiness.seal_outcome_window_evidence({
        "schema": "vkpi_gtm_observation_window/v1",
        "status": "filled",
        "window": "7d",
        "source": (
            "auto:outreach+fulfillment+gifted"
            "(vkpi_messages/vkpi_shipments/vkpi_content_posts/"
            "vkpi_kol_video_evidence/vkpi_project_kol_assignments)"
        ),
        "window_start": DB_CLOCK,
        "observation_start_at": DB_CLOCK,
        "window_end": "2026-08-18T01:02:03+00:00",
        "filled_at": "2026-08-18T02:00:00+00:00",
        "metrics": {
            "reply_outcome": 1,
            "reply_outcome_bridge_id": 51,
            "reply_outcome_receipt_id": 61,
            "reply_outcome_binding_fingerprint": "a" * 64,
            "reply_outcome_receipt_fingerprint": "b" * 64,
        },
    })
    # A filled generic 7d window must not freeze before the immutable reply
    # receipt exists; otherwise migration 276 would make the actual unreachable.
    without_receipt = gtm_prediction_producer.registered_prediction_verdict_gate(
        _GateConn(evidence), 41, outcome_id=71,
    )
    assert without_receipt["ready"] is False
    monkeypatch.setattr(
        outreach_reply_truth,
        "verified_actual_for_action",
        lambda *_args, **_kwargs: {
            "actual": 1, "binding_id": 51, "receipt_id": 61,
            "prediction_run_id": gtm_prediction_producer.prediction_run_id(41),
            "binding_fingerprint": "a" * 64, "receipt_fingerprint": "b" * 64,
        },
    )
    ready = gtm_prediction_producer.registered_prediction_verdict_gate(
        _GateConn(evidence), 41, outcome_id=71,
    )
    assert ready["required"] is True and ready["ready"] is True


def _action_bound_reply_metrics(
    monkeypatch: pytest.MonkeyPatch,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 8, tzinfo=timezone.utc)

    def rows(_conn: Any, sql: str, _params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "FROM vkpi_messages WHERE" in sql:
            return messages
        return []

    monkeypatch.setattr(gtm_windows, "_rows", rows)
    return gtm_windows._window_7d_metrics(
        object(), kol_pool_id=17, kol_id=9, project_ids=[], start=start, end=end,
        action_inbox_id=41, product_sku="AF-26", channel="youtube",
    )


def _bound_message(
    message_id: int, project_id: int, direction: str, captured_at: str,
    *, action_id: int = 41,
) -> dict[str, Any]:
    return {
        "id": message_id,
        "project_id": project_id,
        "direction": direction,
        "captured_at": captured_at,
        "metadata_json": {"action_inbox_id": action_id},
    }


def test_inbound_before_first_outbound_is_not_a_claimable_action_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = _action_bound_reply_metrics(monkeypatch, [
        _bound_message(1, 10, "inbound", "2026-08-02T00:00:00Z"),
        _bound_message(2, 10, "outbound", "2026-08-03T00:00:00Z"),
    ])
    assert metrics["reply_outcome"] is None
    assert metrics["reply_outcome_correlated_inbound_n"] == 0
    assert metrics["reply_outcome_binding"] == "server_owned_action_project_outreach_bridge_missing"


def test_same_kol_inbound_from_another_project_is_not_a_claimable_action_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = _action_bound_reply_metrics(monkeypatch, [
        _bound_message(1, 10, "outbound", "2026-08-02T00:00:00Z"),
        _bound_message(2, 11, "inbound", "2026-08-03T00:00:00Z"),
    ])
    assert metrics["reply_outcome"] is None
    assert metrics["reply_outcome_correlated_inbound_n"] == 0
    assert metrics["reply_outcome_binding"] == "server_owned_action_project_outreach_bridge_missing"


def test_client_metadata_cannot_create_a_verified_action_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = _action_bound_reply_metrics(monkeypatch, [
        _bound_message(1, 10, "outbound", "2026-08-02T00:00:00Z"),
        _bound_message(2, 10, "inbound", "2026-08-03T00:00:00Z"),
        _bound_message(3, 10, "inbound", "2026-08-04T00:00:00Z"),
        # Late refresh sees it, but the frozen window excludes it.
        _bound_message(4, 10, "inbound", "2026-08-20T00:00:00Z"),
    ])
    assert metrics["reply_outcome"] is None
    assert metrics["reply_outcome_correlated_inbound_n"] == 0
    assert metrics["reply_outcome_binding"] == "server_owned_action_project_outreach_bridge_missing"
    assert "Client-controlled message metadata is not verification" in metrics["reply_outcome_note"]


def test_binary_reply_is_not_evaluable_without_an_outbound_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = _action_bound_reply_metrics(monkeypatch, [
        _bound_message(1, 10, "inbound", "2026-08-04T00:00:00Z"),
    ])
    assert metrics["reply_outcome"] is None


def test_prediction_anchor_builds_an_exact_seven_day_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gtm_windows, "_linked_kol_id", lambda *_args: 0)
    monkeypatch.setattr(gtm_windows, "_project_ids_for_kol", lambda *_args: [])
    payload = gtm_windows._build_window_payload(
        object(),
        {
            "created_at": "2026-08-01T00:00:00Z",
            "observation_start_at": DB_CLOCK,
            "prediction_run_id": "gtmact_41_kol_outreach_reply_outcome_7d",
            "kol_pool_id": None,
        },
        horizon_days=7,
        label="7d",
        now=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    start = prediction_truth.parse_iso_datetime(payload["window_start"])
    end = prediction_truth.parse_iso_datetime(payload["window_end"])
    assert start is not None and end is not None
    assert (end - start).days == 7
    assert payload["window_start"] == payload["observation_start_at"] == DB_CLOCK
    assert payload["prediction_run_id"] == "gtmact_41_kol_outreach_reply_outcome_7d"
    assert prediction_truth.outcome_evidence_is_closed(
        {
            **payload,
            "status": "filled",
            "metrics": {"reply_outcome": 1},
            "filled_at": "2026-08-18T02:00:00+00:00",
        },
        evidence_field="window_7d",
        horizon_days=7,
        run_created_at=DB_CLOCK,
        outcome_decided_at="2026-08-18T03:00:00+00:00",
        observation_start_at=DB_CLOCK,
    ) is True

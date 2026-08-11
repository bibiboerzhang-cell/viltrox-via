"""Bounded provider-free GTM prediction producer.

The first production slice turns the existing provider-free GTM outreach
reply-rate rule baseline into one immutable, outcome-bound prediction when a
``kol_outreach`` bet is materialized.

Truth boundaries:
* no provider/LLM/data collection is invoked here; the frozen forecast seed is
  read from the server-generated action payload;
* the evaluation tuple comes only from ``prediction_truth``'s server registry;
* ``observation_start_at`` is read from the database clock in the same
  transaction in which ``record_prediction_run`` inserts ``created_at``;
* deterministic run ids make a later materialize pass repair a missed mirror
  without rewriting an existing prediction.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.domains.market_brain import prediction_truth

logger = get_logger(__name__)

ACTION_TABLE = "vkpi_action_inbox"
REGISTRY_KEY = "kol_outreach_reply_outcome_7d"
PRODUCER_SCHEMA = "vkpi_gtm_provider_free_prediction_seed/v1"
MODEL_VERSION = "v1"
DEFAULT_ORG = "viltrox"


def prediction_run_id(action_inbox_id: int, registry_key: str = REGISTRY_KEY) -> str:
    return f"gtmact_{int(action_inbox_id)}_{registry_key}"


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _float(value: Any) -> float | None:
    try:
        if isinstance(value, bool) or value in (None, ""):
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _iso(value: Any) -> str | None:
    parsed = prediction_truth.parse_iso_datetime(value)
    return parsed.isoformat() if parsed is not None else None


def _validated_seed(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    bet = payload.get("bet") if isinstance(payload.get("bet"), dict) else {}
    seed = bet.get("prediction_seed") if isinstance(bet.get("prediction_seed"), dict) else None
    if seed is None:
        return None, "prediction_seed_missing"
    if seed.get("schema") != PRODUCER_SCHEMA or seed.get("registry_key") != REGISTRY_KEY:
        return None, "prediction_seed_contract_invalid"
    if _text(bet.get("action_type"), 80) != "kol_outreach":
        return None, "prediction_seed_action_mismatch"
    p10, p50, p90 = (_float(seed.get(key)) for key in ("p10", "p50", "p90"))
    # This task predicts a Bernoulli probability.  Missing bounds, booleans,
    # NaN/Inf, negative values, and values above one are not forecasts and may
    # not be frozen into the immutable prediction ledger.
    if (
        p10 is None or p50 is None or p90 is None
        or not 0.0 <= p10 <= p50 <= p90 <= 1.0
    ):
        return None, "prediction_seed_interval_invalid"
    confidence = _text(seed.get("confidence"), 20).lower()
    if confidence not in {"low", "medium", "high"}:
        return None, "prediction_seed_confidence_invalid"
    channel = _text(seed.get("channel"), 60).lower()
    market = _text(payload.get("country"), 40).upper()
    sku = _text(payload.get("sku"), 120)
    kol_pool_id = seed.get("kol_pool_id")
    try:
        kol_pool_id = int(kol_pool_id)
    except (TypeError, ValueError):
        kol_pool_id = 0
    if not sku or not market or not channel or kol_pool_id <= 0:
        return None, "prediction_seed_dimensions_incomplete"
    return {
        **seed,
        "p10": p10,
        "p50": p50,
        "p90": p90,
        "confidence": confidence,
        "channel": channel,
        "market": market,
        "sku": sku,
        "kol_pool_id": kol_pool_id,
    }, None


def _existing_contract(conn: Any, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT input_summary FROM vkpi_prediction_runs
        WHERE organization_id = ? AND run_id = ?
        """,
        (DEFAULT_ORG, run_id),
    ).fetchone()
    return prediction_truth.parse_evaluation_contract(dict(row)) if row is not None else None


def _server_now(conn: Any) -> str | None:
    row = conn.execute("SELECT CURRENT_TIMESTAMP AS server_now").fetchone()
    return _iso(dict(row).get("server_now")) if row is not None else None


def record_materialized_bet_predictions(
    dedupe_keys: list[str],
    *,
    conn: Any = None,
) -> dict[str, Any]:
    """Mirror eligible materialized bets into immutable prediction runs.

    This is deliberately a repairable best-effort companion to Action Inbox:
    the action is already durable, and every rerun scans the same deterministic
    keys.  A failed mirror is surfaced in the returned counters and can be
    retried; it never invents a prediction from an incomplete seed.
    """
    result: dict[str, Any] = {
        "status": "ok",
        "eligible": 0,
        "recorded": 0,
        "deduped": 0,
        "skipped": 0,
        "failed": 0,
        "reasons": {},
    }
    keys = list(dict.fromkeys(_text(key, 240) for key in dedupe_keys if _text(key, 240)))[:100]
    if not keys:
        return result
    try:
        from app.db.connection import get_conn, table_exists
        from app.domains.market_brain import prediction_ledger

        if not table_exists(ACTION_TABLE) or not table_exists(prediction_ledger.RUNS_TABLE):
            return {**result, "status": "unavailable", "reason": "prediction_tables_missing"}
        db = conn or get_conn()
        placeholders = ",".join("?" for _ in keys)
        rows = db.execute(
            f"""
            SELECT id, dedupe_key, payload_json
            FROM {ACTION_TABLE}
            WHERE dedupe_key IN ({placeholders})
            ORDER BY id
            """,
            tuple(keys),
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            payload = _loads(row.get("payload_json"))
            seed, reason = _validated_seed(payload)
            if seed is None:
                result["skipped"] += 1
                reasons = result["reasons"]
                reasons[str(reason)] = int(reasons.get(str(reason), 0)) + 1
                continue
            result["eligible"] += 1
            action_id = int(row["id"])
            run_id = prediction_run_id(action_id)
            existing = _existing_contract(db, run_id)
            if existing is not None and int(existing["target_action_inbox_id"]) != action_id:
                result["failed"] += 1
                result["reasons"]["existing_contract_mismatch"] = (
                    int(result["reasons"].get("existing_contract_mismatch", 0)) + 1
                )
                continue
            # Replays reuse the first immutable clock.  First writes take the
            # database clock, then pass that exact value as both created_at and
            # observation_start_at to the ledger on the same connection.
            observation_start = (
                str(existing.get("observation_start_at")) if existing is not None
                else _server_now(db)
            )
            if observation_start is None:
                result["failed"] += 1
                result["reasons"]["server_time_unavailable"] = (
                    int(result["reasons"].get("server_time_unavailable", 0)) + 1
                )
                continue
            contract = prediction_truth.build_registered_gtm_evaluation_contract(
                REGISTRY_KEY,
                target_action_inbox_id=action_id,
                observation_start_at=observation_start,
            )
            prediction = {
                "metric_key": contract["metric_key"],
                "unit": contract["unit"],
                "value": seed["p50"],
                "p10": seed["p10"],
                "p50": seed["p50"],
                "p90": seed["p90"],
                "kol_pool_id": seed["kol_pool_id"],
            }
            input_summary = {
                "schema": "vkpi_gtm_prediction_input/v1",
                "evaluation_contract": contract,
                "source": "gtm_bets.provider_free_reply_probability_rule",
                "source_action_dedupe_key": _text(row.get("dedupe_key"), 240),
                "source_gtm_plan_id": _text(payload.get("gtm_plan_id"), 120),
                "sample_size": seed.get("sample_size"),
            }
            out = prediction_ledger.record_prediction_run(
                run_id=run_id,
                model_name=_text(seed.get("method"), 120) or "evidence_quantile_v1",
                model_version=MODEL_VERSION,
                task_type=contract["task_type"],
                prediction=prediction,
                product_sku=seed["sku"],
                market=seed["market"],
                channel=seed["channel"],
                horizon_days=contract["horizon_days"],
                input_summary=input_summary,
                p10=seed["p10"],
                p50=seed["p50"],
                p90=seed["p90"],
                confidence=seed["confidence"],
                basis=seed.get("basis") if isinstance(seed.get("basis"), list) else [],
                source_step="rule",
                _connection=db,
                _created_at=observation_start,
            )
            if out.get("ok"):
                result["recorded"] += 1
                if out.get("deduped"):
                    result["deduped"] += 1
            else:
                result["failed"] += 1
                failure = _text(out.get("reason"), 100) or "prediction_record_failed"
                result["reasons"][failure] = int(result["reasons"].get(failure, 0)) + 1
        return result
    except Exception as exc:  # noqa: BLE001 - report failure; never invent a run
        logger.warning("gtm_prediction_producer failed: %s", exc, exc_info=True)
        return {**result, "status": "error", "failed": result["failed"] + 1,
                "reason": str(exc)[:300]}


def registered_observation_anchors(
    conn: Any,
    action_inbox_id: int,
) -> dict[str, dict[str, str]]:
    """Return verified evidence-field anchors for one action's registered runs."""
    action_id = int(action_inbox_id or 0)
    if action_id <= 0:
        return {}
    run_id = prediction_run_id(action_id)
    try:
        row = conn.execute(
            """
            SELECT run_id, created_at, input_summary
            FROM vkpi_prediction_runs
            WHERE organization_id = ? AND run_id = ?
            """,
            (DEFAULT_ORG, run_id),
        ).fetchone()
        if row is None:
            return {}
        run = dict(row)
        contract = prediction_truth.parse_evaluation_contract(run)
        created = prediction_truth.parse_iso_datetime(run.get("created_at"))
        start = prediction_truth.parse_iso_datetime(
            contract.get("observation_start_at") if contract else None
        )
        if (
            contract is None
            or int(contract["target_action_inbox_id"]) != action_id
            or created is None
            or start is None
            or start != created
        ):
            return {}
        return {
            str(contract["evidence_field"]): {
                "prediction_run_id": str(run["run_id"]),
                "observation_start_at": start.astimezone(timezone.utc).isoformat(),
            }
        }
    except Exception:
        logger.warning(
            "gtm_prediction_producer anchor read failed action_id=%s", action_id, exc_info=True,
        )
        return {}


def registered_prediction_verdict_gate(
    conn: Any,
    action_inbox_id: int,
    *,
    outcome_id: int | None = None,
) -> dict[str, Any]:
    """Keep a linked outcome open until its registered window is durable.

    Unregistered historical bets are unaffected.  A registered bet cannot be
    finalized before gtm_windows has written the exact server-clock-anchored
    window; otherwise finalization would freeze an empty window forever.
    """
    anchors = registered_observation_anchors(conn, int(action_inbox_id or 0))
    anchor = anchors.get("window_7d")
    if anchor is None:
        return {"required": False, "ready": True}
    try:
        if outcome_id is not None:
            row = conn.execute(
                "SELECT id, window_7d FROM vkpi_gtm_outcomes WHERE id = ? AND action_inbox_id = ?",
                (int(outcome_id), int(action_inbox_id)),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT id, window_7d FROM vkpi_gtm_outcomes
                WHERE action_inbox_id = ? AND decision = 'open'
                ORDER BY id LIMIT 1
                """,
                (int(action_inbox_id),),
            ).fetchone()
        evidence = _loads(dict(row).get("window_7d")) if row is not None else {}
        start = prediction_truth.parse_iso_datetime(evidence.get("window_start"))
        end = prediction_truth.parse_iso_datetime(evidence.get("window_end"))
        filled = prediction_truth.parse_iso_datetime(evidence.get("filled_at"))
        expected_start = prediction_truth.parse_iso_datetime(anchor["observation_start_at"])
        from app.domains.market_brain.data_readiness import has_observed_outcome_evidence
        from app.domains.market_brain import outreach_reply_truth

        metrics = evidence.get("metrics") if isinstance(evidence.get("metrics"), dict) else {}
        reply_value = metrics.get("reply_outcome")
        verified_reply = outreach_reply_truth.verified_actual_for_action(
            conn, int(action_inbox_id),
        )
        exact_reply = bool(
            not isinstance(reply_value, bool)
            and reply_value in {0, 1, 0.0, 1.0}
            and verified_reply is not None
            and int(reply_value) == int(verified_reply["actual"])
            and int(metrics.get("reply_outcome_bridge_id") or 0)
                == int(verified_reply["binding_id"])
            and int(metrics.get("reply_outcome_receipt_id") or 0)
                == int(verified_reply["receipt_id"])
            and str(metrics.get("reply_outcome_binding_fingerprint") or "")
                == str(verified_reply["binding_fingerprint"])
            and str(metrics.get("reply_outcome_receipt_fingerprint") or "")
                == str(verified_reply["receipt_fingerprint"])
            and str(verified_reply["prediction_run_id"])
                == str(anchor["prediction_run_id"])
        )

        valid = bool(
            has_observed_outcome_evidence({"window_7d": evidence})
            and evidence.get("schema") == "vkpi_gtm_observation_window/v1"
            and str(evidence.get("status") or "").lower() == "filled"
            and start is not None and end is not None and filled is not None
            and expected_start is not None and start == expected_start
            and end - start == timedelta(days=7)
            and end <= filled
            and exact_reply
        )
        return {
            "required": True,
            "ready": valid,
            "reason": None if valid else "prediction_observation_window_not_ready",
            "prediction_run_id": anchor["prediction_run_id"],
            "observation_start_at": anchor["observation_start_at"],
        }
    except Exception:
        logger.warning(
            "gtm_prediction_producer verdict gate failed action_id=%s",
            action_inbox_id,
            exc_info=True,
        )
        return {
            "required": True,
            "ready": False,
            "reason": "prediction_observation_window_not_ready",
            "prediction_run_id": anchor["prediction_run_id"],
            "observation_start_at": anchor["observation_start_at"],
        }


__all__ = [
    "PRODUCER_SCHEMA", "REGISTRY_KEY", "prediction_run_id",
    "record_materialized_bet_predictions", "registered_observation_anchors",
    "registered_prediction_verdict_gate",
]

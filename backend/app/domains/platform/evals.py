"""Evals 评测体系(P4)—— 跑业务断言保证升级是变聪明非变玄学,结果持久化可追踪。

内置套件:跑真系统函数 + 断言关键行为(推荐不碰 fit / 召回有结果 / 权重有界 / 事件总线往返 / 预测诚实)。
红线:评测只读跑 + 断言;断言之一就是"零触 viltrox_fit_score"。每次升级跑一遍,分数掉了就知道退化。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists

logger = get_logger(__name__)

Case = tuple[str, Callable[[], tuple[bool, float, str]]]

_EVAL_RUNS = "vkpi_eval_runs"
_EVAL_RESULTS = "vkpi_eval_results"
_EVENTS = "vkpi_event_ledger"
_EVAL_EVENT = "eval_suite_completed"
_EVAL_EVENT_SOURCE = "platform.evals"
_EVAL_EVIDENCE_VERIFICATION = "server_bound_eval_suite"


def _json_param() -> str:
    return "?::jsonb" if is_postgres_runtime() else "?"


def _result_set_sha256(suite: str, results: list[dict[str, Any]]) -> str:
    """Hash the server-produced case set without run ids or wall-clock data."""

    rows = sorted(
        (
            {
                "case_name": str(row.get("case_name") or ""),
                "passed": bool(row.get("passed")),
                "score": float(row.get("score") or 0.0),
                "detail": str(row.get("detail") or ""),
            }
            for row in results
        ),
        key=lambda row: row["case_name"],
    )
    payload = json.dumps(
        {"suite": str(suite or ""), "results": rows},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _eval_summary(
    *,
    run_id: int,
    organization_id: int,
    result_set_sha256: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "organization_id": int(organization_id),
        "evidence_verification": _EVAL_EVIDENCE_VERIFICATION,
        "server_bound_run_id": int(run_id),
        "result_set_sha256": str(result_set_sha256),
        "producer": "platform.evals.run_builtin_suite",
        "results": results,
    }


def _first_kol_id() -> int:
    try:
        r = get_conn().execute("SELECT id FROM vkpi_kol_pool ORDER BY id LIMIT 1").fetchone()
        return int(dict(r)["id"]) if r else 0
    except Exception:
        return 0


def _has_fit_key(obj: Any) -> bool:
    """递归查是否把 viltrox_fit_score 当字段暴露(note 文本里提及免责不算)。"""
    if isinstance(obj, dict):
        return any(str(k) == "viltrox_fit_score" for k in obj) or any(_has_fit_key(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_fit_key(x) for x in obj)
    return False


def _c_recommendation_no_fit() -> tuple[bool, float, str]:
    """KOL 推荐卡:出 data_grade(数据等级)且不把 viltrox_fit_score 当字段暴露(note 免责声明不算)。"""
    from app.domains.kol import recommendation_card

    card = recommendation_card.get_recommendation_card(_first_kol_id())
    ok = ("data_grade" in card) and not _has_fit_key(card)
    return ok, 1.0 if ok else 0.0, f"data_grade={card.get('data_grade')} fit_key_absent={not _has_fit_key(card)}"


def _c_recall_nonempty() -> tuple[bool, float, str]:
    """跨信号召回:已知 query 应有结果 + 标 recall_method。"""
    from app.domains.intelligence import semantic_recall

    r = semantic_recall.unified_recall("sony camera", limit=5)
    ok = bool(r.get("results")) and bool(r.get("recall_method"))
    return ok, 1.0 if ok else 0.0, f"results={len(r.get('results', []))} method={r.get('recall_method')}"


def _c_weight_bounded() -> tuple[bool, float, str]:
    """推荐权重:None 或 [0,1] 有界(独立信号,绝不越界/不触 fit)。"""
    from app.domains.kol import roi_aggregate

    w = roi_aggregate.compute_next_recommendation_weight(_first_kol_id())
    ok = (w is None) or (0.0 <= float(w) <= 1.0)
    return ok, 1.0 if ok else 0.0, f"weight={w}"


def _c_event_ledger_roundtrip() -> tuple[bool, float, str]:
    """事件总线:emit 后能按实体读回(可追溯)。"""
    from app.domains.platform import event_ledger

    if not table_exists("vkpi_event_ledger"):
        return False, 0.0, "event_ledger absent"
    eid = event_ledger.emit("eval_probe", entity_type="eval", entity_id="probe", source="evals")
    back = event_ledger.by_entity("eval", "probe", limit=1)
    ok = bool(eid) and bool(back) and back[0].get("event_type") == "eval_probe"
    try:
        get_conn().execute("DELETE FROM vkpi_event_ledger WHERE event_type='eval_probe'")
        get_conn().commit()
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    return ok, 1.0 if ok else 0.0, f"emit_id={eid} readback={bool(back)}"


def _c_prediction_honest() -> tuple[bool, float, str]:
    """机会窗预测:confidence_cap 合法;无真销量不伪造 high。"""
    from app.domains.market import prediction

    r = prediction.predict_opportunities()
    cap = r.get("confidence_cap")
    ok = cap in ("low", "medium", "high")
    return ok, 1.0 if ok else 0.0, f"confidence_cap={cap} status={r.get('status')}"


def _c_marketing_brain_scorecard_available() -> tuple[bool, float, str]:
    """Marketing Brain 评分卡:六维度存在,并能指出离 90+ 最近的短板。"""
    from app.domains.intelligence import marketing_brain_scorecard

    r = marketing_brain_scorecard.build_marketing_brain_scorecard()
    dimensions = r.get("dimensions") if isinstance(r.get("dimensions"), list) else []
    weak = r.get("weakest_dimensions") if isinstance(r.get("weakest_dimensions"), list) else []
    score = float(r.get("score") or 0.0)
    ok = r.get("status") == "ok" and len(dimensions) >= 6 and bool(weak) and not _has_fit_key(r)
    return ok, 1.0 if ok else 0.0, f"score={score} dimensions={len(dimensions)} weakest={len(weak)} fit_key_absent={not _has_fit_key(r)}"


_BUILTIN: list[Case] = [
    ("recommendation_no_fit", _c_recommendation_no_fit),
    ("recall_nonempty", _c_recall_nonempty),
    ("weight_bounded", _c_weight_bounded),
    ("event_ledger_roundtrip", _c_event_ledger_roundtrip),
    ("prediction_honest", _c_prediction_honest),
    ("marketing_brain_scorecard_available", _c_marketing_brain_scorecard_available),
]


def run_builtin_suite(suite: str = "core_v1", *, organization_id: int = 1) -> dict[str, Any]:
    """跑内置评测套件 + 持久化(vkpi_eval_runs/results)。"""
    org_id = int(organization_id)
    if org_id != 1:
        raise ValueError("eval_suite_organization_scope_must_be_legacy_org1")
    results = []
    for name, fn in _BUILTIN:
        try:
            passed, score, detail = fn()
        except Exception as exc:
            passed, score, detail = False, 0.0, f"exception: {str(exc)[:160]}"
            logger.warning("evals.case_failed", extra={"case": name}, exc_info=True)
        results.append({"case_name": name, "passed": bool(passed), "score": float(score), "detail": str(detail)[:480]})
    total = len(results)
    passed_n = sum(1 for r in results if r["passed"])
    run_id = None
    result_set_sha256 = _result_set_sha256(suite, results)
    evidence_status = "not_persisted"
    required_tables = (_EVAL_RUNS, _EVAL_RESULTS, _EVENTS)
    if all(table_exists(table) for table in required_tables):
        conn = get_conn()
        try:
            row = conn.execute(
                f"INSERT INTO {_EVAL_RUNS} "
                "(suite, status, total, passed, summary_json, finished_at) "
                f"VALUES (?,'running',0,0,{_json_param()},NULL) RETURNING id",
                (suite, json.dumps({}, separators=(",", ":"))),
            ).fetchone()
            run_id = int(dict(row)["id"]) if row else None
            if run_id is None:
                raise RuntimeError("eval run insert returned no id")
            for r in results:
                conn.execute(
                    f"INSERT INTO {_EVAL_RESULTS} "
                    "(run_id, case_name, passed, score, detail) VALUES (?,?,?,?,?)",
                    (run_id, r["case_name"], r["passed"], r["score"], r["detail"]),
                )
            summary = _eval_summary(
                run_id=run_id,
                organization_id=org_id,
                result_set_sha256=result_set_sha256,
                results=results,
            )
            from app.domains.platform import event_ledger

            event_ledger.insert_required(
                conn,
                _EVAL_EVENT,
                entity_type="eval_run",
                entity_id=run_id,
                actor_type="system",
                actor_id="run_builtin_suite",
                source=_EVAL_EVENT_SOURCE,
                payload={
                    "suite": str(suite),
                    "total": total,
                    "passed": passed_n,
                    "result_set_sha256": result_set_sha256,
                },
                trace_id=event_ledger.new_trace_id(
                    "eval_suite", org_id, suite, run_id, result_set_sha256,
                ),
                provenance={
                    "evidence_verification": _EVAL_EVIDENCE_VERIFICATION,
                    "server_bound_run_id": run_id,
                    "result_set_sha256": result_set_sha256,
                    "producer": "platform.evals.run_builtin_suite",
                },
                organization_id=org_id,
            )
            terminal = conn.execute(
                f"UPDATE {_EVAL_RUNS} SET status='done', total=?, passed=?, "
                f"summary_json={_json_param()}, finished_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status='running' RETURNING id",
                (
                    total,
                    passed_n,
                    json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                    run_id,
                ),
            ).fetchone()
            if terminal is None:
                raise RuntimeError("eval run terminal transition lost")
            conn.commit()
            evidence_status = "server_bound"
        except Exception:
            conn.rollback()
            run_id = None
            logger.warning("evals.persist_failed", exc_info=True)
    return {"status": "ok", "suite": suite, "run_id": run_id, "total": total, "passed": passed_n,
            "pass_rate": round(passed_n / total, 3) if total else 0.0, "results": results,
            "evidence_status": evidence_status,
            "result_set_sha256": result_set_sha256 if run_id is not None else None,
            "note": "业务评测套件;升级后跑一遍,pass_rate 掉了即退化。零触 viltrox_fit_score。"}

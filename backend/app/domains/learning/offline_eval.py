"""离线评估周链(学习闭环 L 车道):每周一用真数据给模型打分,结果落 vkpi_eval_runs/results。

把五件只读评估工具编成一条固定链(suite='weekly_offline_v1'),每件一个 case:
  1. core_v1            platform/evals.run_builtin_suite('core_v1')(推荐零触 fit / 召回非空 / 权重有界 / 预测诚实)
  2. forecast_backtest  learning/shadow_eval forecast_backtest(留一回测,sha256 指纹)
  3. rerank_holdout     recommendations/rerank_fit.holdout_eval(按推荐时间 80/20:p@10 / AUC vs rule_v0,带 n 与 CI;
                        n<30 诚实记 insufficient_samples)
  4. weekly_scorecard   learning/weekly_scorecard(九组预测台账周命中率)
  5. feature_coverage   feature_store_derived.feature_coverage(v2 派生特征非空率,>40% 才 eligible)

落账协议与 platform.evals.run_builtin_suite 同款(迁移 280 的终态守卫要求:running → results → 事件账本
eval_suite_completed(actor_id='run_builtin_suite' 字面量为守卫硬约束)→ done + server_bound summary);
summary_json 额外带 ``chain`` 明细(有界摘要,不存全量 payload)。

红线:纯读断言 + 评估账本写入;零 LLM;绝不写 viltrox_fit_score、不改 rule_v0、不改影子激活规则
(激活仍只走 rerank_fit.fit_rerank_model 的硬门槛)。任一工具抛错只记该 case 失败,链继续。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists

logger = get_logger(__name__)

SUITE = "weekly_offline_v1"
_EVAL_RUNS = "vkpi_eval_runs"
_EVAL_RESULTS = "vkpi_eval_results"
_EVENTS = "vkpi_event_ledger"
_PRODUCER = "learning.offline_eval.run_weekly_offline_eval"
# 迁移 280 守卫把这两个字面量写死为「服务端绑定证据」的身份,链落账必须沿用。
_GUARD_ACTOR_ID = "run_builtin_suite"
_GUARD_SOURCE = "platform.evals"
_GUARD_EVENT = "eval_suite_completed"
_GUARD_VERIFICATION = "server_bound_eval_suite"
_HONEST_HOLDOUT_STATUSES = frozenset({"ok", "insufficient_samples", "insufficient_class_balance", "tables_missing"})


def _json_param() -> str:
    return "?::jsonb" if is_postgres_runtime() else "?"


def _r(value: Any) -> float:
    try:
        out = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(1.0, out)), 4)


def _case(name: str, passed: bool, score: float, detail: str, summary: dict[str, Any]) -> dict[str, Any]:
    return {"case_name": name, "passed": bool(passed), "score": float(score), "detail": str(detail)[:480], "summary": summary}


def _run_core_v1() -> dict[str, Any]:
    from app.domains.platform import evals

    out = evals.run_builtin_suite("core_v1")
    rate = _r(out.get("pass_rate"))
    detail = f"run_id={out.get('run_id')} passed={out.get('passed')}/{out.get('total')} evidence={out.get('evidence_status')}"
    return _case("core_v1", rate >= 1.0, rate, detail,
                 {"run_id": out.get("run_id"), "passed": out.get("passed"), "total": out.get("total"),
                  "result_set_sha256": out.get("result_set_sha256")})


def _run_forecast_backtest() -> dict[str, Any]:
    from app.domains.learning import shadow_eval

    out = shadow_eval.run_shadow_eval("forecast_backtest")
    status = str(out.get("status") or "")
    challenger = out.get("challenger") or {}
    score = _r(challenger.get("band_hit_rate"))
    samples = out.get("samples") or {}
    detail = (f"status={status} verdict={out.get('verdict')} challenger_hit={challenger.get('band_hit_rate')} "
              f"evaluated={samples.get('evaluated')} fingerprint={str(out.get('fingerprint') or '')[:16]}")
    return _case("forecast_backtest", status in {"ready", "empty", "insufficient"}, score, detail,
                 {"status": status, "verdict": out.get("verdict"), "fingerprint": out.get("fingerprint"),
                  "challenger": challenger, "baseline": out.get("baseline") or {}, "samples": samples})


def _run_rerank_holdout() -> dict[str, Any]:
    from app.domains.recommendations import rerank_fit

    out = rerank_fit.holdout_eval()
    status = str(out.get("status") or "")
    model = out.get("model") or {}
    rule = out.get("rule_v0") or {}
    mp = (model.get("precision_at_k") or {}).get("precision")
    rp = (rule.get("precision_at_k") or {}).get("precision")
    if status == "ok":
        detail = (f"n={out.get('n')} test={out.get('n_test')} p@10 model={mp} rule_v0={rp} "
                  f"auc model={model.get('auc')} ci={model.get('ci95')} rule_v0={rule.get('auc')} verdict={out.get('verdict')}")
    else:
        detail = f"status={status} n={out.get('n')}/{out.get('min_samples')} {out.get('note') or ''}"
    return _case("rerank_holdout", status in _HONEST_HOLDOUT_STATUSES, _r(mp), detail,
                 {k: out.get(k) for k in ("status", "n", "n_train", "n_test", "min_samples", "verdict", "model", "rule_v0", "note")})


def _run_weekly_scorecard() -> dict[str, Any]:
    from app.domains.learning import weekly_scorecard

    out = weekly_scorecard.weekly_scorecard()
    status = str(out.get("status") or "")
    overall = out.get("overall") or {}
    rate = overall.get("in_range_hit_rate")
    pending = out.get("pending_backlog") or {}
    detail = (f"status={status} judged={overall.get('in_range_judged')} hits={overall.get('in_range_hits')} "
              f"hit_rate={rate} pending={pending.get('pending_total')}")
    return _case("weekly_scorecard", status == "ok", _r(rate), detail,
                 {"status": status, "in_range_judged": overall.get("in_range_judged"),
                  "in_range_hits": overall.get("in_range_hits"), "in_range_hit_rate": rate,
                  "pending_total": pending.get("pending_total"), "momentum": overall.get("momentum")})


def _run_feature_coverage() -> dict[str, Any]:
    from app.domains.recommendations import feature_store_derived

    out = feature_store_derived.feature_coverage(sample_limit=200)
    status = str(out.get("status") or "")
    total = int(out.get("feature_count") or 0) or 1
    eligible = int(out.get("eligible_count") or 0)
    detail = f"status={status} sample_n={out.get('sample_n')} eligible={eligible}/{total} ({', '.join(out.get('eligible_features') or [])[:300]})"
    return _case("feature_coverage_v2", status in {"ok", "empty"}, _r(eligible / total), detail,
                 {"status": status, "sample_n": out.get("sample_n"), "nonnull_rate": out.get("nonnull_rate"),
                  "eligible_features": out.get("eligible_features"), "version": out.get("version")})


_CHAIN: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
    ("core_v1", _run_core_v1),
    ("forecast_backtest", _run_forecast_backtest),
    ("rerank_holdout", _run_rerank_holdout),
    ("weekly_scorecard", _run_weekly_scorecard),
    ("feature_coverage_v2", _run_feature_coverage),
)


def _result_set_sha256(suite: str, results: list[dict[str, Any]]) -> str:
    rows = sorted(
        ({"case_name": r["case_name"], "passed": bool(r["passed"]), "score": float(r["score"]), "detail": r["detail"]} for r in results),
        key=lambda row: row["case_name"],
    )
    payload = json.dumps({"suite": suite, "results": rows}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def persist_eval_run(suite: str, results: list[dict[str, Any]], *, chain: dict[str, Any] | None = None) -> dict[str, Any]:
    """按迁移 280 终态协议落一套 eval run;表缺 → not_persisted;失败回滚只告警。"""
    total = len(results)
    passed_n = sum(1 for r in results if r["passed"])
    sha = _result_set_sha256(suite, results)
    if not all(table_exists(t) for t in (_EVAL_RUNS, _EVAL_RESULTS, _EVENTS)):
        return {"run_id": None, "evidence_status": "not_persisted", "result_set_sha256": None}
    conn = get_conn()
    try:
        row = conn.execute(
            f"INSERT INTO {_EVAL_RUNS} (suite, status, total, passed, summary_json, finished_at) "
            f"VALUES (?,'running',0,0,{_json_param()},NULL) RETURNING id",
            (suite, json.dumps({}, separators=(",", ":"))),
        ).fetchone()
        run_id = int(dict(row)["id"]) if row else None
        if run_id is None:
            raise RuntimeError("eval run insert returned no id")
        for r in results:
            conn.execute(
                f"INSERT INTO {_EVAL_RESULTS} (run_id, case_name, passed, score, detail) VALUES (?,?,?,?,?)",
                (run_id, r["case_name"], bool(r["passed"]), float(r["score"]), r["detail"]),
            )
        from app.domains.platform import event_ledger

        event_ledger.insert_required(
            conn, _GUARD_EVENT,
            entity_type="eval_run", entity_id=run_id, actor_type="system", actor_id=_GUARD_ACTOR_ID, source=_GUARD_SOURCE,
            payload={"suite": suite, "total": total, "passed": passed_n, "result_set_sha256": sha},
            trace_id=event_ledger.new_trace_id("eval_suite", 1, suite, run_id, sha),
            provenance={"evidence_verification": _GUARD_VERIFICATION, "server_bound_run_id": run_id,
                        "result_set_sha256": sha, "producer": _PRODUCER},
            organization_id=1,
        )
        summary = {
            "organization_id": 1,
            "evidence_verification": _GUARD_VERIFICATION,
            "server_bound_run_id": run_id,
            "result_set_sha256": sha,
            "producer": _PRODUCER,
            "results": [{k: r[k] for k in ("case_name", "passed", "score", "detail")} for r in results],
            "chain": chain or {},
        }
        terminal = conn.execute(
            f"UPDATE {_EVAL_RUNS} SET status='done', total=?, passed=?, summary_json={_json_param()}, "
            "finished_at=CURRENT_TIMESTAMP WHERE id=? AND status='running' RETURNING id",
            (total, passed_n, json.dumps(summary, ensure_ascii=False, separators=(",", ":"), default=str), run_id),
        ).fetchone()
        if terminal is None:
            raise RuntimeError("eval run terminal transition lost")
        conn.commit()
        return {"run_id": run_id, "evidence_status": "server_bound", "result_set_sha256": sha}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("offline_eval.persist_failed", exc_info=True)
        return {"run_id": None, "evidence_status": "not_persisted", "result_set_sha256": None}


def run_weekly_offline_eval(*, persist: bool = True) -> dict[str, Any]:
    """跑整条链(每件单独吞错)并落账;返回 {status, suite, run_id, total, passed, results, chain}。"""
    results: list[dict[str, Any]] = []
    chain: dict[str, Any] = {}
    for name, fn in _CHAIN:
        try:
            case = fn()
        except Exception as exc:
            logger.warning("offline_eval.case_failed", extra={"case": name}, exc_info=True)
            case = _case(name, False, 0.0, f"exception: {str(exc)[:160]}", {"error": type(exc).__name__})
        chain[name] = case.pop("summary", {})
        results.append(case)
    total = len(results)
    passed_n = sum(1 for r in results if r["passed"])
    persisted = persist_eval_run(SUITE, results, chain=chain) if persist else {"run_id": None, "evidence_status": "skipped", "result_set_sha256": None}
    return {
        "status": "ok",
        "suite": SUITE,
        "total": total,
        "passed": passed_n,
        "pass_rate": round(passed_n / total, 3) if total else 0.0,
        "results": results,
        "chain": chain,
        **persisted,
        "note": "纯读评估链;影子激活规则不因本链改变;n<30 的 holdout 诚实记样本不足。",
    }


__all__ = ["SUITE", "persist_eval_run", "run_weekly_offline_eval"]

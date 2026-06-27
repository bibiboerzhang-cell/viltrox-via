"""Durable Workflow 引擎(P2)—— 轻量自建:流程可暂停/恢复/重放,失败从中间 step 续跑。

一个 run = 有序 steps;每 step 跑完 checkpoint state;失败则 run=failed 停在 current_step,
再次 run(run_id, steps) 从 current_step 续跑(不重跑已完成的)。每步事件入 Event Ledger(P1)。
红线:引擎只编排 + 留痕,业务由 step 回调做;零触 viltrox_fit_score。best-effort 不吞业务异常语义。
"""
from __future__ import annotations

import json
from typing import Any, Callable

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

_RUNS = "vkpi_workflow_runs"
_STEPS = "vkpi_workflow_steps"
_CKPT = "vkpi_workflow_checkpoints"

Step = tuple[str, Callable[[dict[str, Any]], dict[str, Any] | None]]


def _emit(event_type: str, run: dict[str, Any], **extra: Any) -> None:
    try:
        from app.domains.platform import event_ledger

        event_ledger.emit(
            event_type, entity_type="workflow", entity_id=run.get("id"),
            actor_type="system", source="workflow_engine",
            payload={"workflow": run.get("workflow_name"), **extra}, trace_id=str(run.get("trace_id") or ""),
        )
    except Exception:
        pass


def start_run(workflow_name: str, *, input: dict[str, Any] | None = None, entity_type: str = "",
              entity_id: Any = "", organization_id: int = 1) -> dict[str, Any]:
    """新建一个 workflow run(status=running, current_step=0)。"""
    if not table_exists(_RUNS):
        return {"status": "unavailable"}
    from app.domains.platform import event_ledger

    trace = event_ledger.new_trace_id("wf", workflow_name, entity_id)
    conn = get_conn()
    row = conn.execute(
        f"INSERT INTO {_RUNS} (organization_id, workflow_name, status, input_json, entity_type, entity_id, trace_id) "
        f"VALUES (?,?,?,?::jsonb,?,?,?) RETURNING id",
        (int(organization_id), str(workflow_name), "running",
         json.dumps(input or {}, ensure_ascii=False, default=str), str(entity_type or ""), str(entity_id or ""), trace),
    ).fetchone()
    conn.commit()
    run_id = int(dict(row)["id"]) if row else None
    run = {"id": run_id, "workflow_name": workflow_name, "trace_id": trace}
    _emit("workflow_started", run)
    return {"status": "ok", "run_id": run_id, "trace_id": trace}


def _run_row(run_id: int) -> dict[str, Any] | None:
    r = get_conn().execute(f"SELECT * FROM {_RUNS} WHERE id = ?", (int(run_id),)).fetchone()
    return dict(r) if r else None


def _latest_state(run_id: int, input_json: Any) -> dict[str, Any]:
    try:
        r = get_conn().execute(
            f"SELECT state_json FROM {_CKPT} WHERE run_id = ? ORDER BY step_index DESC, id DESC LIMIT 1", (int(run_id),)
        ).fetchone()
        if r:
            s = dict(r).get("state_json")
            return s if isinstance(s, dict) else json.loads(s or "{}")
    except Exception:
        logger.debug("workflow.latest_state_failed", exc_info=True)
    return input_json if isinstance(input_json, dict) else {}


def run(run_id: int, steps: list[Step]) -> dict[str, Any]:
    """从 current_step 续跑剩余 steps;每步 checkpoint;失败停在该步(可再 resume)。"""
    if not table_exists(_RUNS):
        return {"status": "unavailable"}
    row = _run_row(run_id)
    if not row:
        return {"status": "not_found", "run_id": run_id}
    conn = get_conn()
    start = int(row.get("current_step") or 0)
    state = _latest_state(run_id, row.get("input_json"))
    conn.execute(f"UPDATE {_RUNS} SET status='running', updated_at=NOW() WHERE id=?", (int(run_id),))
    conn.commit()
    for i in range(start, len(steps)):
        name, fn = steps[i]
        conn.execute(
            f"INSERT INTO {_STEPS} (run_id, step_index, step_name, status, started_at) VALUES (?,?,?,?,NOW())",
            (int(run_id), i, str(name), "running"),
        )
        conn.commit()
        try:
            out = fn(state) or {}
            if isinstance(out, dict):
                state = {**state, **out}
            conn.execute(
                f"UPDATE {_STEPS} SET status='done', output_json=?::jsonb, finished_at=NOW() "
                f"WHERE run_id=? AND step_index=?",
                (json.dumps(out, ensure_ascii=False, default=str), int(run_id), i),
            )
            conn.execute(
                f"INSERT INTO {_CKPT} (run_id, step_index, state_json) VALUES (?,?,?::jsonb)",
                (int(run_id), i, json.dumps(state, ensure_ascii=False, default=str)),
            )
            conn.execute(f"UPDATE {_RUNS} SET current_step=?, updated_at=NOW() WHERE id=?", (i + 1, int(run_id)))
            conn.commit()
            _emit("workflow_step_done", row, step=i, step_name=name)
        except Exception as exc:
            err = str(exc)[:480]
            conn.execute(
                f"UPDATE {_STEPS} SET status='failed', error=?, finished_at=NOW() WHERE run_id=? AND step_index=?",
                (err, int(run_id), i),
            )
            conn.execute(f"UPDATE {_RUNS} SET status='failed', last_error=?, updated_at=NOW() WHERE id=?", (err, int(run_id)))
            conn.commit()
            logger.warning("workflow.step_failed", extra={"run_id": run_id, "step": i}, exc_info=True)
            _emit("workflow_failed", row, step=i, error=err)
            return {"status": "failed", "run_id": run_id, "failed_step": i, "step_name": name, "error": err,
                    "note": "已停在该步;修因后再 run(run_id, steps) 从此步续跑(不重跑前序)。"}
    conn.execute(f"UPDATE {_RUNS} SET status='completed', updated_at=NOW() WHERE id=?", (int(run_id),))
    conn.commit()
    _emit("workflow_completed", row, steps=len(steps))
    return {"status": "completed", "run_id": run_id, "steps": len(steps), "state": state}


def get_run(run_id: int) -> dict[str, Any]:
    """读 run + steps(可观测:每步状态/输出/错误)。"""
    if not table_exists(_RUNS):
        return {"status": "unavailable"}
    row = _run_row(run_id)
    if not row:
        return {"status": "not_found", "run_id": run_id}
    steps = [dict(r) for r in get_conn().execute(
        f"SELECT step_index, step_name, status, error, started_at, finished_at FROM {_STEPS} "
        f"WHERE run_id=? ORDER BY step_index", (int(run_id),)).fetchall()]
    ckpt_n = 0
    try:  # 可观测:有多少 checkpoint(=可从第几步恢复),best-effort
        ckpt_n = int(dict(get_conn().execute(
            f"SELECT COUNT(*) AS n FROM {_CKPT} WHERE run_id=?", (int(run_id),)).fetchone()).get("n") or 0)
    except Exception:
        pass
    return {"status": "ok",
            "run": {k: row.get(k) for k in ("id", "workflow_name", "status", "current_step", "trace_id", "last_error")},
            "steps": steps, "checkpoints": ckpt_n,
            "resumable": bool(row.get("status") == "failed" and ckpt_n > 0)}

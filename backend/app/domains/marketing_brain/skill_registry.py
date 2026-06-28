"""Marketing Brain① Skill Registry 做实 —— skill 运行落库 + 采纳率/成本/延迟统计。

背景:app/domains/agents/tool_registry.py 已声明 8 个工具的元数据(写库?烧 LLM?成本档?
需审批?输入字段?),但「只声明不执行」、也没有 run / eval / feedback 落库。本模块补上运行账本:

  - register_skill(...)       : 取一个 skill 的合并视图(tool_registry 元数据 + 本地 overlay)。
                                只读声明,不执行、不写业务表。
  - record_skill_run(...)     : 把一次 skill 运行落到 vkpi_skill_runs(model/prompt/context/
                                output/cost/latency,以及事后可补的 human_score/business_result/accepted)。
  - list_skill_runs(...)      : 读回运行明细(可按 skill_name 过滤)。
  - skill_acceptance_stats(...): 聚合采纳率 / 平均成本 / 平均延迟 / 平均人评分。

工程约束(对齐 bet_ledger.py / dealer_scrape.py 既有写法):
  - DB 全走 get_conn() + '?' 占位 + 显式 conn.commit();JSONB 用 ?::jsonb + json.dumps;
  - best-effort table_exists 守卫:缺表返回 {"status": "unavailable"},绝不拖垮调用方;
  - BOOLEAN 读回是 int 1/0(compat 陷阱)—— accepted 用 _truthy 容错归一,不裸 `is True`。

红线:本模块纯运行账本 / 只读统计,绝不触 viltrox_fit_score(唯一合法写点 rule_v0 pool.py)。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

_TABLE = "vkpi_skill_runs"


def _truthy(value: Any) -> bool | None:
    """compat 读回 BOOLEAN 是 int 1/0(非 Python True)。None 透传(未评定),其余按真值归一。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("1", "true", "t", "yes", "y"):
        return True
    if text in ("0", "false", "f", "no", "n", ""):
        return False
    return None


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value or {}, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


def _json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return {}
    try:
        return json.loads(value)
    except Exception:
        logger.warning("skill_registry._json_obj parse failed (returning {})", exc_info=True)
        return {}


def _clean_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def register_skill(skill_name: str, *, skill_version: str = "v1",
                   overlay: dict[str, Any] | None = None) -> dict[str, Any]:
    """取一个 skill 的合并视图:tool_registry 既有元数据 + 本地 overlay(version 等)。

    只声明不执行:返回供编排器 / 前端看的 manifest,绝不写库、绝不触 fit。
    若 tool_registry 里没有同名工具,仍返回一个最小声明(name 即 skill_name)。
    """
    name = str(skill_name or "").strip()
    if not name:
        return {"status": "error", "error": "skill_name required"}
    meta: dict[str, Any] = {}
    try:
        from app.domains.agents import tool_registry

        meta = dict(tool_registry.get_tool(name) or {})
    except Exception as exc:  # tool_registry 缺失不应拖垮注册
        logger.debug("register_skill: tool_registry lookup skipped: %s", exc)
    merged: dict[str, Any] = {
        "skill_name": name,
        "skill_version": str(skill_version or "v1"),
        **meta,
        **(overlay or {}),
    }
    return {"status": "ok", "skill": merged}


def record_skill_run(*, skill_name: str, skill_version: str = "v1",
                     input_schema: dict[str, Any] | None = None,
                     model_used: str | None = None, prompt_version: str | None = None,
                     retrieved_context: dict[str, Any] | None = None,
                     output: dict[str, Any] | None = None,
                     cost_cents: int = 0, latency_ms: int = 0,
                     human_score: float | None = None, business_result: str | None = None,
                     accepted: bool | None = None) -> dict[str, Any]:
    """把一次 skill 运行落到 vkpi_skill_runs。缺表 best-effort 返回 unavailable,绝不抛。"""
    if not table_exists(_TABLE):
        return {"status": "unavailable"}
    name = str(skill_name or "").strip()
    if not name:
        return {"status": "error", "error": "skill_name required"}
    conn = get_conn()
    row = conn.execute(
        f"INSERT INTO {_TABLE} (skill_name, skill_version, input_schema, model_used, "
        f"prompt_version, retrieved_context, output, cost_cents, latency_ms, "
        f"human_score, business_result, accepted) "
        f"VALUES (?,?,?::jsonb,?,?,?::jsonb,?::jsonb,?,?,?,?,?) RETURNING id",
        (
            name,
            str(skill_version or "v1"),
            _json_dumps(input_schema),
            (str(model_used) if model_used is not None else None),
            (str(prompt_version) if prompt_version is not None else None),
            _json_dumps(retrieved_context),
            _json_dumps(output),
            _clean_int(cost_cents, 0),
            _clean_int(latency_ms, 0),
            _clean_float(human_score),
            (str(business_result) if business_result is not None else None),
            (None if accepted is None else bool(accepted)),
        ),
    ).fetchone()
    conn.commit()
    run_id = int(dict(row)["id"]) if row else None
    return {"status": "ok", "run_id": run_id}


def list_skill_runs(skill_name: str = "", limit: int = 50) -> dict[str, Any]:
    """读回运行明细(可按 skill_name 过滤),JSONB 回解为 dict,accepted 经 _truthy 归一。"""
    if not table_exists(_TABLE):
        return {"status": "unavailable", "runs": []}
    where, params = "", []
    name = str(skill_name or "").strip()
    if name:
        where = "WHERE skill_name = ?"
        params.append(name)
    rows = get_conn().execute(
        f"SELECT id, skill_name, skill_version, input_schema, model_used, prompt_version, "
        f"retrieved_context, output, cost_cents, latency_ms, human_score, business_result, "
        f"accepted, created_at FROM {_TABLE} {where} ORDER BY id DESC LIMIT ?",
        (*params, _clean_int(max(1, min(int(limit or 50), 500)), 50)),
    ).fetchall()
    runs: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["input_schema"] = _json_loads(d.get("input_schema"))
        d["retrieved_context"] = _json_loads(d.get("retrieved_context"))
        d["output"] = _json_loads(d.get("output"))
        d["accepted"] = _truthy(d.get("accepted"))
        runs.append(d)
    return {"status": "ok", "runs": runs, "count": len(runs)}


def skill_acceptance_stats(skill_name: str = "") -> dict[str, Any]:
    """聚合统计:总数 / 已评定数 / 采纳数 / 采纳率 / 平均成本 / 平均延迟 / 平均人评分。

    accepted 三态:NULL=未评定(不计入分母),TRUE=采纳,FALSE=拒。采纳率 = 采纳 / 已评定。
    """
    if not table_exists(_TABLE):
        return {"status": "unavailable"}
    where, params = "", []
    name = str(skill_name or "").strip()
    if name:
        where = "WHERE skill_name = ?"
        params.append(name)
    row = get_conn().execute(
        f"SELECT COUNT(*) AS total, "
        f"COUNT(*) FILTER (WHERE accepted IS NOT NULL) AS judged, "
        f"COUNT(*) FILTER (WHERE accepted = TRUE) AS accepted, "
        f"AVG(cost_cents) AS avg_cost_cents, "
        f"AVG(latency_ms) AS avg_latency_ms, "
        f"AVG(human_score) AS avg_human_score "
        f"FROM {_TABLE} {where}",
        tuple(params),
    ).fetchone()
    s = dict(row) if row else {}
    judged = _clean_int(s.get("judged"), 0)
    accepted = _clean_int(s.get("accepted"), 0)
    acceptance_rate = round(accepted / judged, 3) if judged else None
    return {
        "status": "ok",
        "skill_name": name or None,
        "total": _clean_int(s.get("total"), 0),
        "judged": judged,
        "accepted": accepted,
        "acceptance_rate": acceptance_rate,
        "avg_cost_cents": _clean_float(s.get("avg_cost_cents")),
        "avg_latency_ms": _clean_float(s.get("avg_latency_ms")),
        "avg_human_score": _clean_float(s.get("avg_human_score")),
    }

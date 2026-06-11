"""Project-level retrospective aggregation (P5 批3).

Aggregates every ready final_v1 video analysis for a project into one LLM-written
project retrospective. Read-only on vkpi_analysis_cache(video); writes ONLY
vkpi_analysis_cache(target_type='project', derive_method='project_retrospective_v1').

Scoring freeze guarantees:
- This module never SELECTs or UPDATEs vkpi_kol_pool, and never touches
  viltrox_fit_score / viltrox_fit_reason / rule_v0 / V6 / rubric.
- The produced result JSON contains only narrative fields (no score columns).
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.analysis import cache_repo
from app.domains.projects.workflow_common import _int, utcnow
from app.platform import llm_gateway

logger = get_logger(__name__)

JOB_TYPE = "project_retrospective_aggregate"
DERIVE_METHOD = "project_retrospective_v1"
BUDGET_SCOPE = "cron:vkpi_project_retrospective"
SOURCE_DERIVE_METHOD = "video_analysis_final_v1"

# F5 截断/选取(确定性,写入 provenance,保证重新生成可比):
TOP_N_VIDEOS = 15           # 最多纳入的视频数(按 view_count 降序,平手按 evidence_id 升序)
PER_VIDEO_CHARS = 2400      # 每视频摘要截断 ~600 token
# gemini-flash 等 thinking 模型把思考算进 maxOutputTokens;1200 太紧→思考吃光后输出仅
# ~43 token 即被 MAX_TOKENS 截断(整段 JSON 不完整)。提到 gateway 上限 4000,给思考+
# 完整 JSON 输出留够空间。成本仍 <$0.05/次,在闸A 已批 single=$1 之内。
MAX_OUTPUT_TOKENS = 4000


def _triggered_by_user_id(staff: dict[str, Any] | None) -> int | None:
    if not isinstance(staff, dict):
        return None
    return _int(staff.get("user_id")) or _int(staff.get("id"))


def _compact_video_text(item: dict[str, Any]) -> str:
    """Flatten one final_v1 cache entry into a compact, truncated text block."""
    entry = item.get("entry") or {}
    result = entry.get("result") if isinstance(entry, dict) else None
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            result = {"raw": result}
    payload = result if isinstance(result, dict) else {}
    head = (
        f"KOL {item.get('kol_name') or item.get('handle') or '-'} · "
        f"{item.get('platform') or '-'} · 曝光 {item.get('view_count') or 0} · "
        f"赞 {item.get('like_count') or 0} · 评论 {item.get('comment_count') or 0}\n"
        f"标题: {item.get('title') or '-'}\n"
    )
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return (head + body)[:PER_VIDEO_CHARS]


def _build_prompt(project_id: int, selected: list[dict[str, Any]], totals: dict[str, Any]) -> str:
    blocks = []
    for idx, item in enumerate(selected, 1):
        blocks.append(f"[视频 {idx}]\n{_compact_video_text(item)}")
    joined = "\n\n".join(blocks)
    return f"""你是 Viltrox 营销团队的资深复盘分析师。基于以下同一个项目下所有 KOL 成品视频的 final_v1 分析结果,
写一篇**项目级**复盘,只总结证据支持的事实,不编造数据,不给任何 0-100 的打分。

项目 ID: {project_id}
纳入视频数: {len(selected)}(按曝光降序选取的 Top-N)
合计曝光: {totals.get('views') or 0} · 合计互动: {totals.get('engagement') or 0}

各视频分析(已截断):
{joined}

只返回合法 JSON(不要 markdown 包裹),全部文本用简体中文:
{{
  "insight_text": "一段 150-300 字的项目级复盘叙述:整体表现、内容共性、与产品卖点的契合",
  "highlights": ["亮点1", "亮点2"],
  "risks": ["风险/不足1", "风险/不足2"],
  "next_steps": ["下一步建议1", "下一步建议2"]
}}

硬性约束:
- 不输出任何分数字段(score/fit/rating 等),只产出上述四个叙述字段。
- highlights/risks/next_steps 每项一句话,3-6 项以内。
"""


def _parse_llm_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```json\s*|```$", "", str(text or "").strip(), flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}


def _active_job(conn: Any, project_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, job_type, status, payload, created_at, updated_at
        FROM apify_jobs
        WHERE job_type=?
          AND status IN ('queued', 'running')
          AND payload->>'target_type'='project'
          AND payload->>'target_id'=?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (JOB_TYPE, str(int(project_id))),
    ).fetchone()
    return dict(row) if row else None


def latest_retrospective_job(project_id: int) -> dict[str, Any]:
    """R1: cache only stores ready/stale, so failed runs must stay visible via the job row.

    Returns {active: <queued/running job or None>, last: <most recent job with status+last_error>}.
    """
    conn = get_conn()
    active = _active_job(conn, int(project_id))
    last = conn.execute(
        """
        SELECT id, status, last_error, created_at, updated_at
        FROM apify_jobs
        WHERE job_type=? AND payload->>'target_type'='project' AND payload->>'target_id'=?
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (JOB_TYPE, str(int(project_id))),
    ).fetchone()
    return {"active": active, "last": dict(last) if last else None}


def enqueue_project_retrospective(project_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Queue one project retrospective aggregation job. Does NOT run the LLM here.

    Dedups against an active job for the same project. Budget preflight is record-only
    (闸A telemetry; does not block a user-triggered generation). payload omits
    search_session_id so the KOL session sync no-ops.
    """
    conn = get_conn()
    active = _active_job(conn, int(project_id))
    if active:
        return {
            "status": "already_queued" if active.get("status") == "queued" else "already_running",
            "project_id": int(project_id),
            "job": active,
            "diagnostics": {"llm_calls": False, "worker_touched": False, "write_viltrox_fit_score": False},
        }
    # 闸A record-only 预检(不阻断用户触发,只记 telemetry)
    try:
        preflight = llm_gateway.budget_preflight(
            f"project_retrospective project:{int(project_id)}",
            purpose="vkpi_project_retrospective",
            max_output_tokens=MAX_OUTPUT_TOKENS,
            cost_tag=BUDGET_SCOPE,
        )
    except Exception:
        preflight = {"note": "budget_preflight_failed_record_only"}
    staff = staff or {}
    payload = {
        "target_type": "project",
        "target_id": str(int(project_id)),
        "project_id": int(project_id),
        "derive_method": DERIVE_METHOD,
        "analysis_kind": "project_llm",
        "query_text": f"项目复盘 · project:{int(project_id)}",
        "summary": "项目复盘聚合",
        "triggered_by_user_id": _triggered_by_user_id(staff),
    }
    job = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES (?, ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id, job_type, status, payload, created_at, updated_at
        """,
        (JOB_TYPE, json.dumps(payload, ensure_ascii=False)),
    ).fetchone()
    conn.commit()
    return {
        "status": "queued",
        "project_id": int(project_id),
        "job": dict(job) if job else None,
        "budget_preflight": preflight,
        "diagnostics": {"llm_calls": False, "worker_touched": False, "write_viltrox_fit_score": False},
    }


def run_project_retrospective(project_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Worker entry: aggregate ready final_v1 analyses into a project retrospective.

    Uses this module's own get_conn() (sqlite-compat '?'); the worker wraps the call in
    db_connection_sync_scope(). Reads cache(video) via cache_repo; writes cache(project)
    ONLY on LLM success. Failure/empty returns a status dict and writes nothing to cache
    (status CHECK red line). Never touches vkpi_kol_pool / fit_score.
    """
    conn = get_conn()
    data = cache_repo.list_project_video_analysis_cache(project_id, derive_method=SOURCE_DERIVE_METHOD, conn=conn)
    ready = [it for it in (data.get("items") or []) if it.get("state") == "ready" and it.get("entry")]
    if not ready:
        return {"status": "skipped", "reason": "no_ready_video_analysis", "project_id": int(project_id)}

    # F5 确定性选取:view_count 降序、平手按 evidence_id 升序,取 Top-N
    ready.sort(key=lambda it: (-(int(it.get("view_count") or 0)), int(it.get("evidence_id") or 0)))
    selected = ready[:TOP_N_VIDEOS]
    evidence_ids = [int(it.get("evidence_id")) for it in selected if it.get("evidence_id") is not None]
    totals = {
        "views": sum(int(it.get("view_count") or 0) for it in selected),
        "engagement": sum(int(it.get("like_count") or 0) + int(it.get("comment_count") or 0) for it in selected),
    }

    prompt = _build_prompt(int(project_id), selected, totals)
    resp = llm_gateway.invoke(
        prompt,
        purpose="vkpi_project_retrospective",
        max_output_tokens=MAX_OUTPUT_TOKENS,
        # gemini-flash 的动态思考会吃光 maxOutputTokens 导致 JSON 截断(out=43→574 仍不完整);
        # 路由到 openai(gpt-5.4-mini,非重思考、稳定返回完整 JSON)。失败仍按链回退。
        preferred_provider="openai",
        cost_tag=BUDGET_SCOPE,
        staff=staff or {},
        metadata={"project_id": int(project_id), "video_count": len(selected)},
    )
    text = str(resp.get("text") or "").strip()
    if resp.get("status") != "success" or not text:
        # 失败/兜底:不写 cache,只反映在 apify_jobs.status
        return {
            "status": "failed",
            "reason": str(resp.get("status") or "llm_no_text"),
            "project_id": int(project_id),
            "provider": resp.get("provider"),
        }

    parsed = _parse_llm_json(text)
    insight = str(parsed.get("insight_text") or "").strip()
    if not insight:
        # 解析不出结构化 insight。若模型本想返回 JSON 却被截断/损坏(text 以 '{' 开头),
        # 不要把半截 JSON 当正文塞进 cache(会渲染成代码)——判失败,不写 cache,让用户重试。
        if text.lstrip().startswith("{"):
            return {
                "status": "failed",
                "reason": "llm_json_truncated_or_malformed",
                "project_id": int(project_id),
                "provider": resp.get("provider"),
            }
        # 纯散文模型:整段当 insight(非 JSON 输出的优雅兜底)。
        insight = text[:600]
    result = {
        "insight_text": insight,
        "highlights": [str(x) for x in (parsed.get("highlights") or []) if str(x).strip()][:6],
        "risks": [str(x) for x in (parsed.get("risks") or []) if str(x).strip()][:6],
        "next_steps": [str(x) for x in (parsed.get("next_steps") or []) if str(x).strip()][:6],
        "provenance": {
            "video_count": len(selected),
            "evidence_ids": evidence_ids,
            "selection": "top_by_view_count",
            "top_n": TOP_N_VIDEOS,
            "source_derive_method": SOURCE_DERIVE_METHOD,
            "model": resp.get("model"),
            "provider": resp.get("provider"),
            "generated_at": utcnow(),
            "totals": totals,
        },
    }
    now = utcnow()
    conn.execute(
        """
        INSERT INTO vkpi_analysis_cache (
            target_type, target_id, model, derive_method, result, cost, status,
            triggered_by_user_id, created_at, updated_at
        ) VALUES ('project', ?, ?, ?, ?::jsonb, ?, 'ready', ?, ?, ?)
        ON CONFLICT (target_type, target_id, derive_method)
        DO UPDATE SET model=EXCLUDED.model, result=EXCLUDED.result, cost=EXCLUDED.cost,
            status='ready', triggered_by_user_id=EXCLUDED.triggered_by_user_id, updated_at=EXCLUDED.updated_at
        """,
        (
            str(int(project_id)),
            str(resp.get("model") or "llm_gateway"),
            DERIVE_METHOD,
            json.dumps({"schema_version": DERIVE_METHOD, **result}, ensure_ascii=False),
            float(resp.get("cost_cents") or 0) / 100.0,
            _triggered_by_user_id(staff),
            now,
            now,
        ),
    )
    conn.commit()
    return {"status": "ready", "project_id": int(project_id), "result": result}

"""联系 KOL 草稿(2026-06-12 裁令:点联系给"优化后的聊天方式")。

队列铁律:LLM 经 apify_jobs(泳道「联系草稿」可见),不做同步内联。
产物落 vkpi_analysis_cache(target_type='kol_pool', derive_method='kol_outreach_draft_v1'),
字段 llm_ 前缀语义由 derive_method 承载;失败不写 cache(CHECK 红线:仅 ready/stale)。
红线:零触 viltrox_fit_score / 主表零写入。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.platform import llm_gateway

logger = get_logger("viltrox.domains.kol.outreach_draft")

JOB_TYPE = "kol_outreach_draft"
DERIVE_METHOD = "kol_outreach_draft_v1"
MAX_OUTPUT_TOKENS = 900


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enqueue_outreach_draft_job(
    kol_pool_id: int,
    *,
    project_id: int | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """联系草稿入 apify_jobs(幂等:同 KOL 已有活跃任务返回 already_queued)。"""
    conn = get_conn()
    kol = conn.execute(
        "SELECT id, handle, display_name FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)
    ).fetchone()
    if not kol:
        raise LookupError("kol pool item not found")
    active = conn.execute(
        """
        SELECT id FROM apify_jobs
        WHERE job_type=? AND status IN ('queued','running')
          AND payload->>'kol_pool_id'=? LIMIT 1
        """,
        (JOB_TYPE, str(int(kol_pool_id))),
    ).fetchone()
    if active:
        return {"status": "already_queued", "job_id": int(dict(active)["id"])}
    name = str(dict(kol).get("handle") or dict(kol).get("display_name") or kol_pool_id)
    payload = {
        "kol_pool_id": int(kol_pool_id),
        "project_id": int(project_id) if project_id else None,
        "query_text": f"联系草稿 · {name}"[:96],
        "target_type": "kol_profile",
        "target_id": int(kol_pool_id),
        "triggered_by_user_id": (staff or {}).get("user_id"),
        "staff_id": (staff or {}).get("id") or (staff or {}).get("staff_id"),
    }
    job = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES (?, ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id
        """,
        (JOB_TYPE, json.dumps(payload, ensure_ascii=False)),
    ).fetchone()
    conn.commit()
    return {"status": "queued", "job_id": int(dict(job)["id"]) if job else None}


def _kol_context(conn: Any, kol_pool_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT handle, display_name, platform, bio, followers, primary_topic, content_style
        FROM vkpi_kol_pool WHERE id=?
        """,
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row) if row else {}


def _project_context(conn: Any, project_id: int | None) -> dict[str, Any]:
    if not project_id:
        return {}
    row = conn.execute(
        "SELECT project_name, product_name, product_sku FROM vkpi_projects WHERE id=?",
        (int(project_id),),
    ).fetchone()
    return dict(row) if row else {}


def _build_prompt(kol: dict[str, Any], project: dict[str, Any]) -> str:
    kol_lines = [
        f"- 平台/Platform: {kol.get('platform') or '-'}",
        f"- Handle: {kol.get('handle') or '-'}",
        f"- 名称/Name: {kol.get('display_name') or '-'}",
        f"- 粉丝/Followers: {kol.get('followers') or '-'}",
        f"- 主题/Topic: {kol.get('primary_topic') or '-'}",
        f"- 简介/Bio: {str(kol.get('bio') or '')[:400]}",
    ]
    project_lines = [
        f"- 项目/Project: {project.get('project_name') or '-'}",
        f"- 产品/Product: {project.get('product_name') or '-'}",
        f"- SKU: {project.get('product_sku') or '-'}",
    ]
    return (
        "你是 VILTROX(唯卓仕,相机镜头品牌)的 KOL 合作拓展专家。"
        "基于以下 KOL 资料与项目背景,写一份首次联系的外联草稿。\n\n"
        "KOL 资料:\n" + "\n".join(kol_lines) + "\n\n项目背景:\n" + "\n".join(project_lines) + "\n\n"
        "合规硬要求(不可省):message_en 正文末尾必须含:(a) 发件人身份披露——明确写出是 VILTROX/唯卓仕品牌方及其拓展代表;(b) 退订选项——一句说明若不愿再收到此类邮件可回复 unsubscribe / 注明拒绝即不再联系。此为全球最严合规基线,任何辖区都适用。\n"
        "要求:\n"
        "1. message_en:英文私信/邮件正文(120-180 词,提及 TA 的内容方向,说明合作形式=寄送镜头测评,语气专业友好,不卑不亢;末尾含上述身份披露+退订选项)。\n"
        "2. message_cn:同一内容的中文版(供团队内部审阅,同样含身份披露+退订说明)。\n"
        "3. subject:英文邮件主题(<=60 字符)。\n"
        "4. talking_points:3-4 条后续沟通要点(中文)。\n"
        "5. channel_suggestion:建议的首选触达渠道(中文一句,基于平台特性)。\n"
        "只返回 JSON:{\"subject\":...,\"message_en\":...,\"message_cn\":...,\"talking_points\":[...],\"channel_suggestion\":...}"
    )


def run_outreach_draft_for_job(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """worker 入口:生成联系草稿并写 cache(失败不写,状态回 apify_jobs)。"""
    kol_pool_id = int(payload.get("kol_pool_id") or 0)
    if not kol_pool_id:
        raise ValueError("kol_pool_id required")
    conn = get_conn()
    kol = _kol_context(conn, kol_pool_id)
    if not kol:
        return {"status": "failed", "reason": "kol_not_found"}
    project = _project_context(conn, payload.get("project_id"))
    resp = llm_gateway.invoke(
        _build_prompt(kol, project),
        purpose="vkpi_kol_outreach_draft",
        max_output_tokens=MAX_OUTPUT_TOKENS,
        preferred_provider="openai",
        cost_tag="vkpi_kol_outreach_draft",
        staff=staff or {},
        metadata={"kol_pool_id": kol_pool_id, "project_id": payload.get("project_id")},
    )
    text = str(resp.get("text") or "").strip()
    if resp.get("status") != "success" or not text:
        return {"status": "failed", "reason": str(resp.get("status") or "llm_no_text")}
    try:
        cleaned = text
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned[4:] if cleaned.lower().startswith("json") else cleaned
        parsed = json.loads(cleaned)
    except Exception:
        return {"status": "failed", "reason": "llm_json_malformed"}
    message_en = str(parsed.get("message_en") or "").strip()
    if not message_en:
        return {"status": "failed", "reason": "llm_missing_message"}
    result = {
        "schema_version": DERIVE_METHOD,
        "subject": str(parsed.get("subject") or "")[:120],
        "message_en": message_en[:2400],
        "message_cn": str(parsed.get("message_cn") or "")[:2400],
        "talking_points": [str(x) for x in (parsed.get("talking_points") or []) if str(x).strip()][:5],
        "channel_suggestion": str(parsed.get("channel_suggestion") or "")[:200],
        "provenance": {
            "model": resp.get("model"),
            "provider": resp.get("provider"),
            "project_id": payload.get("project_id"),
            "generated_at": _utcnow(),
        },
    }
    now = _utcnow()
    conn.execute(
        """
        INSERT INTO vkpi_analysis_cache (
            target_type, target_id, model, derive_method, result, cost, status,
            triggered_by_user_id, created_at, updated_at
        ) VALUES ('kol_pool', ?, ?, ?, ?::jsonb, ?, 'ready', ?, ?, ?)
        ON CONFLICT (target_type, target_id, derive_method)
        DO UPDATE SET model=EXCLUDED.model, result=EXCLUDED.result, cost=EXCLUDED.cost,
            status='ready', triggered_by_user_id=EXCLUDED.triggered_by_user_id, updated_at=EXCLUDED.updated_at
        """,
        (
            str(int(kol_pool_id)),
            str(resp.get("model") or "llm_gateway"),
            DERIVE_METHOD,
            json.dumps(result, ensure_ascii=False),
            float(resp.get("cost_cents") or 0) / 100.0,
            int((staff or {}).get("user_id") or 0) or None,
            now,
            now,
        ),
    )
    conn.commit()
    return {"status": "ready", "kol_pool_id": kol_pool_id}


def get_outreach_draft(kol_pool_id: int) -> dict[str, Any]:
    """读端:最新联系草稿(cache);无则 state=missing。"""
    row = get_conn().execute(
        """
        SELECT result, model, updated_at FROM vkpi_analysis_cache
        WHERE target_type='kol_pool' AND target_id=? AND derive_method=? AND status='ready'
        ORDER BY updated_at DESC LIMIT 1
        """,
        (str(int(kol_pool_id)), DERIVE_METHOD),
    ).fetchone()
    if not row:
        return {"state": "missing", "kol_pool_id": int(kol_pool_id)}
    data = dict(row)
    result = data.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            result = {}
    return {"state": "ready", "kol_pool_id": int(kol_pool_id), "draft": result, "model": data.get("model"), "updated_at": str(data.get("updated_at") or "")}

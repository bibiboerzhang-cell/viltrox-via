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
from app.domains.kol import outreach_promises
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
    enforce_target_write: bool = False,
) -> dict[str, Any]:
    """联系草稿入 apify_jobs(幂等:同 KOL 已有活跃任务返回 already_queued)。"""
    conn = get_conn()
    target_fence: dict[str, Any] | None = None
    if enforce_target_write:
        from app.domains.kol.my_kol_paid_action_access import build_target_fence

        target_fence = build_target_fence(
            conn,
            action="outreach_draft",
            kol_pool_id=int(kol_pool_id),
            staff=staff,
        )
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
    if target_fence is not None:
        from app.domains.kol.my_kol_paid_action_access import FENCE_KEY

        payload[FENCE_KEY] = target_fence
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


def _personalization_lines(kol_pool_id: int, conn: Any) -> tuple[list[str], dict[str, Any]]:
    """G1 真个性化:招牌拍法 top1 + 最爆代表作(signature_profile 守卫 import,
    零新采集/零 LLM);读不出来诚实空列表,草稿链不因此阻断。"""
    try:
        from app.domains.kol import signature_profile

        sig = signature_profile.signature_profile(int(kol_pool_id), conn=conn)
    except Exception:
        logger.debug("outreach_draft.signature_read_failed (non-fatal)", exc_info=True)
        return [], {"available": False}
    styles = sig.get("shooting_styles") if isinstance(sig, dict) else {}
    styles = styles if isinstance(styles, dict) else {}
    modes = styles.get("modes") if styles.get("status") == "ready" else []
    top_mode = modes[0] if isinstance(modes, list) and modes else {}
    tops = sig.get("top_videos") if isinstance(sig, dict) else {}
    tops = tops if isinstance(tops, dict) else {}
    items = tops.get("items") if tops.get("status") == "ready" else []
    top_video = items[0] if isinstance(items, list) and items else {}
    if not top_mode and not top_video:
        return [], {"available": False}

    style_label = str((top_mode or {}).get("label") or "")[:40]
    video_title = str((top_video or {}).get("title") or "")[:140]
    lines: list[str] = []
    if style_label:
        lines.append(f"- 招牌拍法 TOP1: {style_label}")
    if video_title:
        lines.append(f"- 最爆代表作: 《{video_title}》(播放 {(top_video or {}).get('view_count') or '-'})")
    quote_line = ""
    if video_title:
        quote_line = f"就按你最爆的那条《{video_title}》的" + (f"{style_label}拍法来" if style_label else "拍法来")
        lines.append(
            f"- 正文必须自然引用上面的代表作标题,表达「内容按 TA 最擅长的拍法来,我们不干预创意」"
            f"(中文版可用「{quote_line}」的语气,英文版意译)。"
        )
    meta = {
        "available": True,
        "source": "signature_profile_v1",
        "top_style": style_label or None,
        "top_video_title": video_title or None,
        "quote_line": quote_line or None,
    }
    return lines, meta


def _build_prompt(
    kol: dict[str, Any],
    project: dict[str, Any],
    personalization_lines: list[str] | None = None,
) -> str:
    from app.domains.kol.contact_system import sanitize_contact_values_for_external_processing

    sanitized = sanitize_contact_values_for_external_processing(
        {
            "kol": kol,
            "project": project,
            "personalization_lines": personalization_lines or [],
        }
    )
    kol = dict(sanitized.get("kol") or {})
    project = dict(sanitized.get("project") or {})
    personalization_lines = list(sanitized.get("personalization_lines") or [])
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
    persona_block = (
        "该 KOL 的招牌打法(来自我方真实聚合读数,个性化必用、只准引用不得杜撰):\n"
        + "\n".join(personalization_lines) + "\n\n"
        if personalization_lines
        else ""
    )
    promises_requirement = outreach_promises.prompt_requirement()
    promises_block = (
        promises_requirement.replace("email_en", "message_en").replace("email_zh", "message_cn") + "\n"
        if promises_requirement
        else ""
    )
    return (
        "你是 VILTROX(唯卓仕,相机镜头品牌)的 KOL 合作拓展专家。"
        "基于以下 KOL 资料与项目背景,写一份首次联系的外联草稿。\n\n"
        "KOL 资料:\n" + "\n".join(kol_lines) + "\n\n项目背景:\n" + "\n".join(project_lines) + "\n\n"
        + persona_block
        + promises_block
        + "合规硬要求(不可省):message_en 正文末尾必须含:(a) 发件人身份披露——明确写出是 VILTROX/唯卓仕品牌方及其拓展代表;(b) 退订选项——一句说明若不愿再收到此类邮件可回复 unsubscribe / 注明拒绝即不再联系。此为全球最严合规基线,任何辖区都适用。\n"
        "要求:\n"
        "1. message_en:英文私信/邮件正文(140-220 词,提及 TA 的内容方向/最爆代表作,说明合作形式=寄送镜头测评,语气专业友好,不卑不亢;含三承诺块;末尾含上述身份披露+退订选项)。\n"
        "2. message_cn:同一内容的中文版(供团队内部审阅,同样含三承诺块+身份披露+退订说明)。\n"
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
    persona_lines, persona_meta = _personalization_lines(kol_pool_id, conn)
    resp = llm_gateway.invoke(
        _build_prompt(kol, project, persona_lines),
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
    # G1 三承诺幂等兜底:LLM 漏写也保证承诺块在(开关关则原样返回)。
    message_en = outreach_promises.ensure_promises(message_en[:3200], "en")
    message_cn = outreach_promises.ensure_promises(str(parsed.get("message_cn") or "")[:3200], "zh")
    result = {
        "schema_version": DERIVE_METHOD,
        "subject": str(parsed.get("subject") or "")[:120],
        "message_en": message_en[:4000],
        "message_cn": message_cn[:4000],
        "talking_points": [str(x) for x in (parsed.get("talking_points") or []) if str(x).strip()][:5],
        "channel_suggestion": str(parsed.get("channel_suggestion") or "")[:200],
        # G1:个性化引用来源 + 三承诺态(审计留痕;available=False = signature 无读数)。
        "personalization": persona_meta,
        "promises": {
            "version": outreach_promises.PROMISES_VERSION,
            "enabled": outreach_promises.promises_enabled(),
            "included": outreach_promises.has_promises(message_en, "en") or outreach_promises.has_promises(message_cn, "zh"),
        },
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

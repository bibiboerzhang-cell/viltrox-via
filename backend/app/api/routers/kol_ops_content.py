"""KOL Operations outreach, campaign, content, and attribution routes."""
from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.api.dependencies.perms import require_tab
from app.api.routers.kol_ops_helpers import (
    _insert_id,
    _int,
    _items,
    _log_activity,
    _log_activity_commit,
    _now,
)
from app.db.connection import db_write, get_conn
from app.services.kol.content_analyzer import analyze_kol_content_url
from app.services.kol.content_scorer import score_kol_content
from app.services.kol.metrics import engagement_rate


router = APIRouter()


@router.post("/kols/{kol_id}/outreach")
def create_outreach(kol_id: int, body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    if not body.get("action_type"):
        raise HTTPException(status_code=400, detail="action_type required")
    conn = get_conn()
    if not conn.execute("SELECT id FROM kols WHERE id = ?", (int(kol_id),)).fetchone():
        raise HTTPException(status_code=404, detail="KOL not found")
    cur = conn.execute(
        """
        INSERT INTO kol_outreach (kol_id, staff_id, action_type, action_at, notes, next_action_at)
        VALUES (?,?,?,?,?,?)
        """,
        (int(kol_id), int(staff.get("id") or 0), body.get("action_type"), body.get("action_at") or _now(), body.get("notes", ""), body.get("next_action_at")),
    )
    conn.execute("UPDATE kols SET updated_at = ? WHERE id = ?", (_now(), int(kol_id)))
    _log_activity(conn, staff, "outreach_add", target_type="kol", target_id=int(kol_id), result_count=1, metadata={"action_type": body.get("action_type")})
    conn.commit()
    return {"id": _insert_id(conn, cur, "kol_outreach")}


@router.get("/kols/{kol_id}/outreach")
def list_outreach(kol_id: int, staff=Depends(require_tab("kol_ops", "read"))):
    rows = get_conn().execute("SELECT * FROM kol_outreach WHERE kol_id = ? ORDER BY action_at DESC", (int(kol_id),)).fetchall()
    return {"items": _items(rows)}


@router.post("/kols/{kol_id}/campaigns")
def create_campaign(kol_id: int, body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    conn = get_conn()
    if not conn.execute("SELECT id FROM kols WHERE id = ?", (int(kol_id),)).fetchone():
        raise HTTPException(status_code=404, detail="KOL not found")
    cur = conn.execute(
        """
        INSERT INTO kol_campaigns
            (kol_id, product_sku, staff_id, started_at, ended_at, cost_cents, status, notes, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            int(kol_id),
            body.get("product_sku", ""),
            int(body.get("staff_id") or staff.get("id") or 0),
            body.get("started_at"),
            body.get("ended_at"),
            _int(body.get("cost_cents")),
            body.get("status", "planning"),
            body.get("notes", ""),
            _now(),
        ),
    )
    conn.execute("UPDATE kols SET updated_at = ? WHERE id = ?", (_now(), int(kol_id)))
    campaign_id = _insert_id(conn, cur, "kol_campaigns")
    _log_activity(conn, staff, "campaign_create", target_type="kol", target_id=int(kol_id), result_count=1, metadata={"campaign_id": campaign_id, "product_sku": body.get("product_sku", "")})
    conn.commit()
    return {"id": campaign_id}


@router.patch("/campaigns/{campaign_id}")
def update_campaign(campaign_id: int, body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    allowed = ["product_sku", "staff_id", "started_at", "ended_at", "cost_cents", "status", "notes"]
    fields, params = [], []
    for key in allowed:
        if key in body:
            fields.append(f"{key} = ?")
            params.append(body[key])
    if not fields:
        return {"ok": True}
    params.append(int(campaign_id))
    conn = get_conn()
    conn.execute(f"UPDATE kol_campaigns SET {', '.join(fields)} WHERE id = ?", params)
    _log_activity(conn, staff, "campaign_update", target_type="campaign", target_id=int(campaign_id), result_count=1, metadata={"fields": list(body.keys())})
    conn.commit()
    return {"ok": True}


@router.get("/campaigns/{campaign_id}/content")
def list_campaign_content(campaign_id: int, staff=Depends(require_tab("kol_ops", "read"))):
    rows = get_conn().execute("SELECT * FROM kol_content WHERE campaign_id = ? ORDER BY created_at DESC", (int(campaign_id),)).fetchall()
    return {"items": _items(rows)}


@router.post("/content")
def create_content(body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    if not body.get("campaign_id") or not body.get("content_url") or not body.get("platform"):
        raise HTTPException(status_code=400, detail="campaign_id, content_url and platform required")
    conn = get_conn()
    campaign_id = int(body.get("campaign_id"))
    campaign = conn.execute("SELECT id, kol_id FROM kol_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    views = _int(body.get("views"))
    likes = _int(body.get("likes"))
    comments = _int(body.get("comments"))
    shares = _int(body.get("shares"))
    cur = conn.execute(
        """
        INSERT INTO kol_content
            (campaign_id, content_url, platform, posted_at, views, likes, comments, shares,
             engagement_rate, ai_quality_score, ai_summary, ai_topics_json, last_metric_refresh, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            campaign_id,
            body.get("content_url"),
            body.get("platform"),
            body.get("posted_at"),
            views,
            likes,
            comments,
            shares,
            engagement_rate(likes, comments, shares, views),
            body.get("ai_quality_score"),
            body.get("ai_summary", ""),
            json.dumps(body.get("ai_topics", []), ensure_ascii=False),
            _now(),
            _now(),
        ),
    )
    conn.execute("UPDATE kol_campaigns SET status = ? WHERE id = ?", ("已回片", campaign_id))
    conn.execute("UPDATE kols SET contact_status = ?, updated_at = ? WHERE id = ?", ("已回片", _now(), int(campaign["kol_id"])))
    content_id = _insert_id(conn, cur, "kol_content")
    _log_activity(conn, staff, "content_create", target_type="content", target_id=content_id, result_count=1, metadata={"campaign_id": campaign_id, "views": views})
    conn.commit()
    return {"id": content_id}


@router.patch("/content/{content_id}")
def update_content(content_id: int, body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    allowed = ["content_url", "platform", "posted_at", "views", "likes", "comments", "shares", "ai_quality_score", "ai_summary", "last_metric_refresh"]
    fields, params = [], []
    for key in allowed:
        if key in body:
            fields.append(f"{key} = ?")
            params.append(body[key])
    metric_keys = {"views", "likes", "comments", "shares"}
    if metric_keys.intersection(body.keys()):
        current = get_conn().execute("SELECT views, likes, comments, shares FROM kol_content WHERE id = ?", (int(content_id),)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Content not found")
        views = _int(body.get("views", current["views"]))
        likes = _int(body.get("likes", current["likes"]))
        comments = _int(body.get("comments", current["comments"]))
        shares = _int(body.get("shares", current["shares"]))
        fields.append("engagement_rate = ?")
        params.append(engagement_rate(likes, comments, shares, views))
        fields.append("last_metric_refresh = ?")
        params.append(_now())
    if "ai_topics" in body:
        fields.append("ai_topics_json = ?")
        params.append(json.dumps(body.get("ai_topics") or [], ensure_ascii=False))
    if not fields:
        return {"ok": True}
    params.append(int(content_id))
    conn = get_conn()
    conn.execute(f"UPDATE kol_content SET {', '.join(fields)} WHERE id = ?", params)
    _log_activity(conn, staff, "data_update", target_type="content", target_id=int(content_id), result_count=1, metadata={"fields": list(body.keys())})
    conn.commit()
    return {"ok": True}


@router.post("/content/{content_id}/score")
async def score_content(
    content_id: int,
    request: Request,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("kol_ops", "write")),
):
    if bool(body.get("async")):
        queue = getattr(request.app.state, "job_queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="job queue unavailable")
        task_id = await queue.enqueue("score_kol_content", {"content_id": int(content_id)})
        await db_write(
            lambda: _log_activity_commit(
                staff,
                "ai_score_queue",
                target_type="content",
                target_id=int(content_id),
                api_provider="claude",
                api_calls=1,
                result_count=1,
                metadata={"job_id": task_id},
            )
        )
        return {"status": "queued", "job_id": task_id, "content_id": int(content_id)}
    try:
        result = await score_kol_content(content_id)
        await db_write(
            lambda: _log_activity_commit(
                staff,
                "ai_score",
                target_type="content",
                target_id=int(content_id),
                api_provider="claude",
                api_calls=1,
                result_count=1,
                metadata={"score": result.get("quality_score")},
            )
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/content/{content_id}/analyze-url")
async def analyze_content_url(
    content_id: int,
    staff=Depends(require_tab("kol_ops", "write")),
):
    try:
        result = await analyze_kol_content_url(content_id)
        providers = []
        for layer in result.get("layers_used") or []:
            text = str(layer).lower()
            if "gpt" in text and "openai" not in providers:
                providers.append("openai")
            if "gemini" in text and "gemini" not in providers:
                providers.append("gemini")
            if "claude" in text or "text_" in text:
                if "claude" not in providers:
                    providers.append("claude")
        if not providers and result.get("status") == "failed":
            providers.append("scrape")
        await db_write(
            lambda: _log_activity_commit(
                staff,
                "content_analyze_url",
                target_type="content",
                target_id=int(content_id),
                api_provider="+".join(providers) if providers else str(result.get("method") or "analysis"),
                api_calls=max(1, len(providers)),
                result_count=1 if result.get("status") != "failed" else 0,
                metadata={
                    "status": result.get("status"),
                    "method": result.get("method"),
                    "quality_score": result.get("quality_score"),
                    "error": result.get("error"),
                },
            )
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/content/{content_id}/attribution")
def list_content_attribution(content_id: int, staff=Depends(require_tab("kol_ops", "read"))):
    rows = get_conn().execute("SELECT * FROM kol_attribution WHERE content_id = ? ORDER BY attributed_at DESC", (int(content_id),)).fetchall()
    return {"items": _items(rows)}


@router.post("/content/{content_id}/attribute")
def attribute_content(content_id: int, body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    if not body.get("shopify_order_id"):
        raise HTTPException(status_code=400, detail="shopify_order_id required")
    conn = get_conn()
    if not conn.execute("SELECT id FROM kol_content WHERE id = ?", (int(content_id),)).fetchone():
        raise HTTPException(status_code=404, detail="Content not found")
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO kol_attribution
            (content_id, shopify_order_id, attributed_revenue_cents, attributed_at)
        VALUES (?,?,?,?)
        """,
        (int(content_id), body.get("shopify_order_id"), _int(body.get("attributed_revenue_cents")), _now()),
    )
    attribution = conn.execute(
        """
        SELECT id FROM kol_attribution
        WHERE content_id = ? AND shopify_order_id = ?
        LIMIT 1
        """,
        (int(content_id), body.get("shopify_order_id")),
    ).fetchone()
    _log_activity(conn, staff, "attribution_add", target_type="content", target_id=int(content_id), result_count=1, metadata={"shopify_order_id": body.get("shopify_order_id", "")})
    conn.commit()
    return {"id": int(attribution["id"]) if attribution else None}

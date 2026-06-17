"""官号视频画面质量分析(fit-safe 独立管线)。

为 18 自家官号的视频跑 Gemini final_v1,取 content_quality_score 等画质分,存
vkpi_official_post_visual(channel_id+post_uid),供「每日官号报告」③画面质量读真分。

红线(关键):**完全不进 kol_pool / 不建 evidence / 不触 viltrox_fit_score / 不动 fit 指纹**。
官号视频与外部 KOL 评分域物理隔离。Gemini 走预算闸 cron:official_visual。
- YouTube:analyze_youtube_with_gemini(yt-dlp+File API,prod 可达);
- Instagram reel / TikTok video:复用 worker 同款链路——Apify 解析 content_url 拿 direct_video_url →
  下载到临时目录 → analyze_local_video_with_gemini 同款 Gemini final_v1(下半场已落地)。
  每条多一次 Apify(有成本,官号≤8/号、每轮 max_total 限量),照搬 worker 的 MEDIA_RESOLVE_TIMEOUT
  + blocked/failed 分类避免卡死。
- 其它平台(fb/x/reddit):skip。
增量处理:每次只跑少量(防超时/控成本),幂等可续。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.costs import budget_guard
from app.domains.kol.final_v1_extract import _as_dict, _score_from_value

logger = get_logger(__name__)

_BUDGET_SCOPE = "cron:official_visual"
# YouTube 直跑;IG/TikTok 下半场每条多一次 Apify 解析 + 下载,成本更高(诚实标注):
# 估算 Apify 解析 ~$0.01-0.02 + Gemini final_v1 ~$0.06,合并按 needs_media 单独估。
_EST_COST = 0.06
_EST_COST_NEEDS_MEDIA = 0.08
_MAX_PER_ACCOUNT = 8
# 照搬 worker 的 MEDIA_RESOLVE_TIMEOUT:Apify 解析(拿 direct_video_url)硬超时,避免卡死。
# 复用 worker 同名 env,缺省 90s;官号是顺序处理,超时即判 failed 跳到下一条。
_MEDIA_RESOLVE_TIMEOUT_SECONDS = max(10, int(os.environ.get("APIFY_WORKER_MEDIA_RESOLVE_TIMEOUT_SEC", "90")))


def _is_youtube(url: str) -> bool:
    u = (url or "").lower()
    return "youtube.com/watch" in u or "youtu.be/" in u or "/shorts/" in u


def _video_kind(platform: str, url: str) -> str:
    """判这条 post 是不是可分析的视频,返回 youtube / needs_media(ig/tk,要下载)/ skip。"""
    p = (platform or "").lower()
    u = (url or "").lower()
    if p == "youtube" or _is_youtube(u):
        return "youtube"
    if p == "instagram" and "/reel/" in u:
        return "needs_media"
    if p == "tiktok" and "/video/" in u:
        return "needs_media"
    return "skip"


def _pick_video_posts(conn: Any, channel_id: int, platform: str, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT post_uid, post_url, title, platform FROM vkpi_channel_post_metrics "
        "WHERE channel_id = ? AND post_url IS NOT NULL AND post_url != '' "
        "AND snapshot_date = (SELECT MAX(snapshot_date) FROM vkpi_channel_post_metrics WHERE channel_id = ?) "
        "ORDER BY COALESCE(posted_at, captured_at) DESC NULLS LAST LIMIT 60",
        (int(channel_id), int(channel_id)),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        kind = _video_kind(platform or d.get("platform"), d.get("post_url"))
        if kind == "skip":
            continue
        d["_kind"] = kind
        out.append(d)
        if len(out) >= limit:
            break
    return out


def _already(conn: Any, channel_id: int, post_uid: str) -> bool:
    row = conn.execute(
        "SELECT status FROM vkpi_official_post_visual WHERE channel_id = ? AND post_uid = ?",
        (int(channel_id), str(post_uid)),
    ).fetchone()
    if not row:
        return False
    return str(dict(row).get("status") or "") in ("ready", "skipped")


def _store(conn: Any, *, channel_id: int, post: dict[str, Any], status: str, scores: dict[str, Any] | None = None, summary: str = "", model: str = "", error: str = "") -> None:
    cq = (scores or {}).get("content_quality")
    vh = (scores or {}).get("viewer_heart")
    pp = (scores or {}).get("product_proof")
    conn.execute(
        """
        INSERT INTO vkpi_official_post_visual
            (channel_id, post_uid, platform, post_url, title, status,
             content_quality_score, viewer_heart_score, product_proof_score,
             visual_summary, scores_json, model, error, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now(), now())
        ON CONFLICT (channel_id, post_uid) DO UPDATE SET
            status = EXCLUDED.status,
            content_quality_score = EXCLUDED.content_quality_score,
            viewer_heart_score = EXCLUDED.viewer_heart_score,
            product_proof_score = EXCLUDED.product_proof_score,
            visual_summary = EXCLUDED.visual_summary,
            scores_json = EXCLUDED.scores_json,
            model = EXCLUDED.model, error = EXCLUDED.error, updated_at = now()
        """,
        (
            int(channel_id), str(post.get("post_uid")), post.get("platform"), post.get("post_url"),
            str(post.get("title") or "")[:500], status,
            cq, vh, pp, summary[:1500], json.dumps(scores or {}, ensure_ascii=False), str(model or ""), str(error or "")[:300],
        ),
    )
    conn.commit()


def _extract_scores(raw: dict[str, Any]) -> dict[str, Any]:
    """从 final_v1 分析结果取画质三分(content_quality / viewer_heart / product_proof)。"""
    layer6 = _as_dict(raw.get("layer6_flags_and_scores"))
    scores = _as_dict(layer6.get("scores"))
    out: dict[str, Any] = {}
    for key, alias in (("content_quality_score", "content_quality"), ("viewer_heart_score", "viewer_heart"), ("product_proof_score", "product_proof")):
        s, _conf = _score_from_value(scores.get(key))
        if s is not None:
            out[alias] = float(s)
    return out


def _resolve_direct_video_url(content_url: str, platform: str) -> dict[str, Any]:
    """复用 worker 同款 Apify 解析:content_url → direct_video_url。
    照搬 worker 的 MEDIA_RESOLVE_TIMEOUT(asyncio.wait_for 硬超时)+ blocked/failed 分类:
    - APIFY_TOKEN 缺 → blocked(配置问题,非内容失败,不浪费 Gemini 预算);
    - 超时 / 解析失败 / 拿不到 video_url → failed(可续,下轮重试)。
    返回 {ok, status('ready'|'blocked'|'failed'), reason, direct_video_url}。"""
    if not os.environ.get("APIFY_TOKEN", "").strip():
        return {"ok": False, "status": "blocked", "reason": "apify_not_configured", "direct_video_url": ""}

    from app.services.scraping import apify as apify_scraper

    async def _run() -> dict[str, Any]:
        return await asyncio.wait_for(
            apify_scraper.scrape_with_apify(content_url, platform),
            timeout=_MEDIA_RESOLVE_TIMEOUT_SECONDS,
        )

    try:
        scraped = asyncio.run(_run())
    except asyncio.TimeoutError:
        logger.warning("official_visual.media_resolve_timeout | platform=%s timeout_sec=%s", platform, _MEDIA_RESOLVE_TIMEOUT_SECONDS)
        return {"ok": False, "status": "failed", "reason": "media_resolve_timeout", "direct_video_url": ""}
    except Exception as exc:
        return {"ok": False, "status": "failed", "reason": f"media_resolve_error:{str(exc)[:160]}", "direct_video_url": ""}
    direct_video_url = str((scraped or {}).get("video_url") or "").strip()
    if not direct_video_url:
        return {"ok": False, "status": "failed", "reason": str((scraped or {}).get("error") or "media_resolve_failed")[:200], "direct_video_url": ""}
    return {"ok": True, "status": "ready", "reason": "media_resolved", "direct_video_url": direct_video_url}


def _analyze_needs_media(conn: Any, channel: dict[str, Any], post: dict[str, Any]) -> dict[str, Any]:
    """IG reel / TikTok video 的下半场:解析 → 下载 → 本地 Gemini final_v1(worker 同款链路)。
    幂等地走预算闸(needs_media 单条成本更高,先 check 再跑;成功后才 record)。"""
    from app.services.media.video_download import download_direct_video_url

    cid = int(channel["id"])
    puid = str(post.get("post_uid"))
    platform = str(post.get("platform") or channel.get("platform") or "").lower()
    content_url = str(post.get("post_url") or "")

    if not budget_guard.check_budget(_BUDGET_SCOPE, _EST_COST_NEEDS_MEDIA):
        return {"post_uid": puid, "status": "budget_blocked"}

    resolved = _resolve_direct_video_url(content_url, platform)
    if resolved.get("status") == "blocked":
        # 配置问题(无 Apify token):标 skipped,可见且不重试雪崩,补上 token 后下轮自然跑。
        _store(conn, channel_id=cid, post=post, status="skipped", error=str(resolved.get("reason") or "media_resolve_blocked"))
        return {"post_uid": puid, "status": "skipped", "reason": str(resolved.get("reason"))}
    if not resolved.get("ok"):
        _store(conn, channel_id=cid, post=post, status="failed", error=str(resolved.get("reason") or "media_resolve_failed")[:300])
        return {"post_uid": puid, "status": "failed", "error": str(resolved.get("reason"))[:200]}

    try:
        from app.services.ai.analyzers import gemini_video
        with tempfile.TemporaryDirectory(prefix="vkpi-official-visual-") as tmpdir:
            download = download_direct_video_url(
                str(resolved.get("direct_video_url") or ""),
                tmpdir,
                referer=content_url,
            )
            if not download.get("success") or not download.get("path"):
                # 照搬 worker 的 precheck_terminal:404/403/410/451 等确定不可达 → terminal 原因。
                err = str(download.get("error") or f"direct_video_download_failed:{platform}")[:300]
                _store(conn, channel_id=cid, post=post, status="failed", error=err)
                return {"post_uid": puid, "status": "failed", "error": err[:200]}
            raw = asyncio.run(
                gemini_video.analyze_local_video_with_gemini(
                    str(download["path"]),
                    str(post.get("title") or ""),
                    str(channel.get("account_handle") or ""),
                    schema_version="final_v1",
                )
            )
    except Exception as exc:
        _store(conn, channel_id=cid, post=post, status="failed", error=str(exc)[:300])
        return {"post_uid": puid, "status": "failed", "error": str(exc)[:200]}

    if not raw or not raw.get("analyzed"):
        _store(conn, channel_id=cid, post=post, status="failed", error=str((raw or {}).get("error") or "not_analyzed")[:300])
        return {"post_uid": puid, "status": "failed"}
    try:
        budget_guard.record_cost(_BUDGET_SCOPE, _EST_COST_NEEDS_MEDIA)
    except Exception:
        logger.debug("official_visual.record_cost_failed", exc_info=True)
    scores = _extract_scores(raw)
    summary = str(_as_dict(raw.get("layer1_visual_content")).get("content_summary") or raw.get("content_summary") or "")
    _store(conn, channel_id=cid, post=post, status="ready", scores=scores, summary=summary, model=str(raw.get("method") or "gemini"))
    return {"post_uid": puid, "status": "ready", "scores": scores}


def analyze_one(channel: dict[str, Any], post: dict[str, Any]) -> dict[str, Any]:
    conn = get_conn()
    cid = int(channel["id"])
    puid = str(post.get("post_uid"))
    kind = post.get("_kind") or _video_kind(channel.get("platform"), post.get("post_url"))
    if kind == "needs_media":
        # IG reel / TikTok 下半场:复用 worker 同款链路跑本地管线(Apify 解析→下载→本地 Gemini final_v1)。
        return _analyze_needs_media(conn, channel, post)
    if kind != "youtube":
        _store(conn, channel_id=cid, post=post, status="skipped", error=f"unsupported:{kind}")
        return {"post_uid": puid, "status": "skipped", "reason": kind}
    if not budget_guard.check_budget(_BUDGET_SCOPE, _EST_COST):
        return {"post_uid": puid, "status": "budget_blocked"}
    try:
        from app.services.ai.analyzers import gemini_video
        raw = asyncio.run(
            gemini_video.analyze_youtube_with_gemini(
                str(post.get("post_url")), str(post.get("title") or ""),
                creator_handle=str(channel.get("account_handle") or ""), schema_version="final_v1",
            )
        )
    except Exception as exc:
        _store(conn, channel_id=cid, post=post, status="failed", error=str(exc)[:300])
        return {"post_uid": puid, "status": "failed", "error": str(exc)[:200]}
    if not raw or not raw.get("analyzed"):
        _store(conn, channel_id=cid, post=post, status="failed", error=str((raw or {}).get("error") or "not_analyzed")[:300])
        return {"post_uid": puid, "status": "failed"}
    try:
        budget_guard.record_cost(_BUDGET_SCOPE, _EST_COST)
    except Exception:
        logger.debug("official_visual.record_cost_failed", exc_info=True)
    scores = _extract_scores(raw)
    summary = str(_as_dict(raw.get("layer1_visual_content")).get("content_summary") or raw.get("content_summary") or "")
    _store(conn, channel_id=cid, post=post, status="ready", scores=scores, summary=summary, model=str(raw.get("method") or "gemini"))
    return {"post_uid": puid, "status": "ready", "scores": scores}


def process_pending_official_visuals(*, max_total: int = 5, max_per_account: int = _MAX_PER_ACCOUNT) -> dict[str, Any]:
    """增量入口:跨 18 官号挑尚未分析的视频(每号≤max_per_account),本轮最多跑 max_total 条。幂等可续。"""
    conn = get_conn()
    channels = [dict(r) for r in conn.execute(
        "SELECT id, platform, account_handle FROM vkpi_employee_channels WHERE deleted_at IS NULL ORDER BY id"
    ).fetchall()]
    done = 0
    results: list[dict[str, Any]] = []
    for ch in channels:
        if done >= max_total:
            break
        posts = _pick_video_posts(conn, int(ch["id"]), str(ch.get("platform") or ""), max_per_account)
        for p in posts:
            if done >= max_total:
                break
            if _already(conn, int(ch["id"]), str(p.get("post_uid"))):
                continue
            try:
                r = analyze_one(ch, p)
            except Exception as exc:
                r = {"post_uid": p.get("post_uid"), "status": "error", "error": str(exc)[:200]}
            results.append({"channel_id": int(ch["id"]), **r})
            if r.get("status") in ("ready", "failed", "skipped", "budget_blocked"):
                done += 1
            if r.get("status") == "budget_blocked":
                logger.info("official_visual.budget_blocked_stop")
                return {"processed": done, "results": results, "stopped": "budget"}
    logger.info("official_visual.scan_done | processed=%s", done)
    return {"processed": done, "results": results}


def account_visual_summary(channel_id: int) -> dict[str, Any]:
    """报告③用:该官号已分析视频的画质均分 + 最高/最低 + 覆盖数。无则 available=False。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS n, AVG(content_quality_score) AS avg_cq, MAX(content_quality_score) AS max_cq, "
        "MIN(content_quality_score) AS min_cq, AVG(viewer_heart_score) AS avg_vh "
        "FROM vkpi_official_post_visual WHERE channel_id = ? AND status = 'ready' AND content_quality_score IS NOT NULL",
        (int(channel_id),),
    ).fetchone()
    d = dict(row) if row else {}
    n = int(d.get("n") or 0)
    if not n:
        return {"available": False}

    def _f(v: Any) -> float | None:
        try:
            return round(float(v), 1) if v is not None else None
        except (TypeError, ValueError):
            return None

    samples = conn.execute(
        "SELECT title, content_quality_score, visual_summary FROM vkpi_official_post_visual "
        "WHERE channel_id = ? AND status = 'ready' AND content_quality_score IS NOT NULL "
        "ORDER BY content_quality_score DESC LIMIT 4",
        (int(channel_id),),
    ).fetchall()
    return {
        "available": True,
        "analyzed_count": n,
        "avg_content_quality": _f(d.get("avg_cq")),
        "max_content_quality": _f(d.get("max_cq")),
        "min_content_quality": _f(d.get("min_cq")),
        "avg_viewer_heart": _f(d.get("avg_vh")),
        "top_samples": [
            {"title": str(dict(s).get("title") or "")[:80], "score": _f(dict(s).get("content_quality_score")), "summary": str(dict(s).get("visual_summary") or "")[:120]}
            for s in samples
        ],
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }

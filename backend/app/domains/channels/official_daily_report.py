"""每日官方账号分析报告(LLM)—— 为 18 个自家官号(vkpi_employee_channels)每天生成
「①播放表现 ②评论洞察 ③画面质量 ④数据趋势 ⑤提升建议」报告。

红线 / 安全:
- 这是「我方官号绩效报告」,数据源全是 vkpi_employee_channels / vkpi_channel_metrics /
  vkpi_channel_post_metrics / vkpi_comments,与外部 KOL 的 viltrox_fit_score / rule_v0 /
  kol_pool 物理两套,绝不读写后者。
- LLM 调用前过预算闸 cron:official_daily_report(硬上限,见 migration 157),调用后 record_cost;
  超限 → 跳过 LLM 保留上一份,诚实返回 budget_blocked。
- Claude 优先(对所给真实数据做经营推理,要稳)、Gemini 兜底,两者自带代理。
- 只读所给真实数据,不编造;数据缺口(如官号画面质量尚未接 Gemini)诚实标 pending。
- 一账号一天一轮一份(round_key 区分 中国早8/美西早6 两轮),ON CONFLICT 当轮覆盖刷新。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from app.core.config import CLAUDE_MODEL
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.costs import budget_guard
from app.domains.market.ai_today import _parse_json  # 复用加固的 JSON 抽取

logger = get_logger(__name__)

_BUDGET_SCOPE = "cron:official_daily_report"
_EST_COST = 0.10  # 单账号单次估算
_MAX_COMMENTS = 25
_MAX_POSTS = 8


def _num(value: Any) -> Any:
    if value is None:
        return None
    try:
        f = float(value)
        return int(f) if f == int(f) else round(f, 4)
    except (TypeError, ValueError):
        return value


def _official_channels(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, platform, account_handle, account_display_name "
        "FROM vkpi_employee_channels WHERE deleted_at IS NULL ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def _account_metrics_trend(conn: Any, channel_id: int) -> list[dict[str, Any]]:
    """近 7 天账号日快照(已含预算好的 delta,无需自算)。"""
    rows = conn.execute(
        "SELECT snapshot_date, followers, followers_delta, posts_count, posts_delta, "
        "total_views, views_delta_24h, total_likes, total_comments, engagement_rate "
        "FROM vkpi_channel_metrics WHERE channel_id = ? ORDER BY snapshot_date DESC LIMIT 7",
        (int(channel_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def _top_bottom_posts(conn: Any, channel_id: int) -> dict[str, list[dict[str, Any]]]:
    """最新快照日的逐帖,按播放挑高/低,供 LLM 看哪条内容表现好/差。"""
    latest = conn.execute(
        "SELECT MAX(snapshot_date) AS d FROM vkpi_channel_post_metrics WHERE channel_id = ?",
        (int(channel_id),),
    ).fetchone()
    snap = dict(latest).get("d") if latest else None
    if not snap:
        return {"top": [], "bottom": []}
    rows = conn.execute(
        "SELECT post_uid, title, post_url, posted_at, views, likes, comments, shares, views_delta "
        "FROM vkpi_channel_post_metrics WHERE channel_id = ? AND snapshot_date = ? "
        "ORDER BY COALESCE(views,0) DESC LIMIT 40",
        (int(channel_id), snap),
    ).fetchall()
    posts = [dict(r) for r in rows]
    top = posts[:_MAX_POSTS]
    bottom = posts[-3:] if len(posts) > _MAX_POSTS else []
    return {"top": top, "bottom": bottom, "snapshot_date": str(snap), "post_count": len(posts)}


def _recent_comments(conn: Any, channel_id: int) -> dict[str, Any]:
    """该官号近评论(文本 + 语言 + 点赞),供 LLM 顺手做情绪/诉求小结。"""
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_comments "
        "WHERE post_table = 'vkpi_employee_channels' AND account_id = ?",
        (int(channel_id),),
    ).fetchone()
    rows = conn.execute(
        "SELECT comment_text, language_detected, likes_count, created_at FROM vkpi_comments "
        "WHERE post_table = 'vkpi_employee_channels' AND account_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (int(channel_id), _MAX_COMMENTS),
    ).fetchall()
    return {"total": int(dict(total).get("n") or 0) if total else 0, "recent": [dict(r) for r in rows]}


def _account_visual(cid: int) -> dict[str, Any]:
    # 画面质量:fit-safe 独立管线(official_visual_analysis)已分析的真 content_quality_score;
    # 未分析则诚实标 pending(增量处理中,第二刀)。
    try:
        from app.domains.channels.official_visual_analysis import account_visual_summary

        vis = account_visual_summary(cid)
        if vis.get("available"):
            return {"status": "ready", **vis}
    except Exception:
        logger.debug("official_daily_report.visual_summary_failed", exc_info=True)
    return {"status": "pending", "note": "官号视频 Gemini 画质分析增量处理中,本号暂无已分析视频"}


def _account_data(conn: Any, channel: dict[str, Any]) -> dict[str, Any]:
    cid = int(channel["id"])
    trend = _account_metrics_trend(conn, cid)
    posts = _top_bottom_posts(conn, cid)
    comments = _recent_comments(conn, cid)
    return {
        "channel_id": cid,
        "platform": channel.get("platform"),
        "handle": channel.get("account_handle"),
        "display_name": channel.get("account_display_name"),
        "trend_7d": trend,
        "posts": posts,
        "comments": comments,
        "visual_quality": _account_visual(cid),
    }


def _has_signal(data: dict[str, Any]) -> bool:
    return bool(data.get("trend_7d") or (data.get("posts") or {}).get("top") or (data.get("comments") or {}).get("total"))


def _report_text(data: dict[str, Any]) -> str:
    """把真实数据拼成喂 LLM 的事实块(只给真数,缺口诚实标注)。"""
    lines: list[str] = []
    lines.append(f"账号:{data.get('platform')}:{data.get('handle')}(channel_id={data.get('channel_id')})")
    trend = data.get("trend_7d") or []
    if trend:
        lines.append("\n【近7天日快照(粉丝/帖数/播放/互动,delta 已预算)】")
        for r in trend:
            lines.append(
                f"  {r.get('snapshot_date')}: 粉丝{_num(r.get('followers'))}(Δ{_num(r.get('followers_delta'))}) "
                f"帖{_num(r.get('posts_count'))}(Δ{_num(r.get('posts_delta'))}) "
                f"总播放{_num(r.get('total_views'))}(24h Δ{_num(r.get('views_delta_24h'))}) "
                f"互动率{_num(r.get('engagement_rate'))}"
            )
    else:
        lines.append("\n【近7天日快照】— 无数据(待接入/缺口)")
    posts = data.get("posts") or {}
    if posts.get("top"):
        lines.append(f"\n【最新快照日 {posts.get('snapshot_date')} 表现最好的帖(按播放,共{posts.get('post_count')}帖)】")
        for p in posts["top"]:
            lines.append(
                f"  «{str(p.get('title') or '')[:80]}» 播放{_num(p.get('views'))}(Δ{_num(p.get('views_delta'))}) "
                f"赞{_num(p.get('likes'))} 评{_num(p.get('comments'))} 转{_num(p.get('shares'))}"
            )
    if posts.get("bottom"):
        lines.append("\n【表现偏弱的帖(同日,按播放最低)】")
        for p in posts["bottom"]:
            lines.append(f"  «{str(p.get('title') or '')[:80]}» 播放{_num(p.get('views'))} 赞{_num(p.get('likes'))} 评{_num(p.get('comments'))}")
    comments = data.get("comments") or {}
    lines.append(f"\n【评论(共 {comments.get('total')} 条,取最近 {len(comments.get('recent') or [])} 条原文)】")
    for c in comments.get("recent") or []:
        txt = str(c.get("comment_text") or "").replace("\n", " ")[:160]
        if txt:
            lines.append(f"  [{c.get('language_detected') or '?'}|赞{_num(c.get('likes_count'))}] {txt}")
    if not (comments.get("recent")):
        lines.append("  — 该账号暂无已采集评论(待补评论采集)")
    vq = data.get("visual_quality") or {}
    if vq.get("status") == "ready":
        lines.append(
            f"\n【画面质量(Gemini 真实分析 {vq.get('analyzed_count')} 条视频)】"
            f"content_quality 均分 {vq.get('avg_content_quality')}(最高{vq.get('max_content_quality')}/最低{vq.get('min_content_quality')}),"
            f"viewer_heart 均 {vq.get('avg_viewer_heart')}"
        )
        for s in (vq.get("top_samples") or [])[:4]:
            lines.append(f"  «{str(s.get('title') or '')[:70]}» 画质{s.get('score')} — {str(s.get('summary') or '')[:110]}")
    else:
        lines.append(f"\n【画面质量】{vq.get('note')}(status={vq.get('status')})")
    return "\n".join(lines)


def _build_prompt(report_text: str, handle: str, platform: str, language: str) -> str:
    lang_line = "用中文输出。" if language != "en" else "Output in English."
    return (
        f"你是 Viltrox(唯卓仕)的官方社媒运营负责人。下面是官号 {platform}:{handle} 的真实绩效数据。\n"
        "请生成一份【今日账号分析评估报告】,给运营看,要可执行。要求:\n"
        "- 只基于下面提供的数据做分析,绝不编造数字;标注「待接入/pending/无数据」的缺口要点明是待补,不要硬编结论。\n"
        "- 结论引用具体数字 / 帖子标题 / 评论原文。\n"
        "- 画面质量:若数据【画面质量】段含 Gemini 真实分析(content_quality 均分等),据此点评本号视频画质\n"
        "  水平与高/低分内容差异;若标 pending,则诚实说明真画质分仍在增量分析中、据标题间接评估,不要编造。\n"
        "- 评论洞察:据评论原文归纳受众情绪(正/负/咨询)、高频诉求、负面点(如不支持某卡口、价格、缺货)。\n"
        "- 提升建议:基于本号近况给 3-5 条可执行建议(发什么内容 / 哪类帖该多发 / 怎么回应评论诉求 / 互动率怎么提)。\n"
        "- 每个文本字段控制在 3-4 句精炼输出,别长篇大论(护成本 + 防截断)。\n"
        f"- {lang_line}\n\n"
        "账号数据:\n"
        '"""\n'
        f"{report_text}\n"
        '"""\n\n'
        "严格只输出 JSON(无多余文字 / 无 markdown 代码块):\n"
        "{\n"
        '  "play_performance": "播放表现评估:整体播放走向 + 哪条帖爆/弱 + 为什么(引用数字)",\n'
        '  "comment_insights": "评论洞察:情绪倾向 + 高频诉求 + 负面点(引用评论原文)",\n'
        '  "visual_quality": "画面质量(诚实说明真视觉分析待接入,据现有信息间接评估内容倾向)",\n'
        '  "data_trend": "数据趋势:粉丝/互动/增长走向(增长/停滞/负增长,引用 delta)",\n'
        '  "suggestions": ["可执行提升建议(谁/做什么)", "...3-5 条"],\n'
        '  "headline": "一句话今日总评"\n'
        "}\n"
    )


def _generate(prompt: str) -> tuple[str, str]:
    """Claude 优先(对真实数据做经营推理要稳);失败回退 Gemini。返回 (文本, 模型标签)。"""
    try:
        from app.services.ai.clients.claude_client import get_claude_client
        from app.services.ai.retry import call_ai_with_retry

        client = get_claude_client()
        resp = call_ai_with_retry(
            "channels.official_daily_report",
            lambda: client.messages.create(
                # 3500:中文 5 段式报告较长,2000 会把 JSON 截断到不可解析(_parse_json→{})。
                model=CLAUDE_MODEL, max_tokens=3500, messages=[{"role": "user", "content": prompt}]
            ),
        )
        text = (resp.content[0].text or "").strip() if resp and getattr(resp, "content", None) else ""
        if text:
            return text, f"claude:{CLAUDE_MODEL}"
    except Exception:
        logger.warning("official_daily_report.claude_failed_fallback_gemini", exc_info=True)
    try:
        import app.core.config  # noqa: F401  触发 .env 加载
        import app.services.ai.clients.gemini_client as gc
        from app.core.config import GEMINI_MODEL

        client = getattr(gc, "gemini_client", None)
        if client is not None:
            resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            text = (getattr(resp, "text", "") or "").strip()
            if text:
                return text, f"gemini:{GEMINI_MODEL}"
    except Exception:
        logger.warning("official_daily_report.gemini_failed", exc_info=True)
    return "", ""


def _normalize(content: dict[str, Any]) -> dict[str, Any]:
    def _s(key: str) -> str:
        return str(content.get(key) or "").strip()

    sug = content.get("suggestions")
    suggestions = [str(x).strip() for x in sug if str(x).strip()][:6] if isinstance(sug, list) else []
    return {
        "play_performance": _s("play_performance"),
        "comment_insights": _s("comment_insights"),
        "visual_quality": _s("visual_quality"),
        "data_trend": _s("data_trend"),
        "suggestions": suggestions,
        "headline": _s("headline"),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def _store(conn: Any, channel: dict[str, Any], report_date: str, round_key: str, analysis: dict[str, Any], model: str, facts: dict[str, Any]) -> None:
    payload = {"analysis": analysis, "facts_summary": facts}
    conn.execute(
        """
        INSERT INTO vkpi_official_account_daily_report
            (channel_id, report_date, round_key, platform, handle, report_json, model, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, now(), now())
        ON CONFLICT (channel_id, report_date, round_key) DO UPDATE SET
            platform = EXCLUDED.platform, handle = EXCLUDED.handle,
            report_json = EXCLUDED.report_json, model = EXCLUDED.model, updated_at = now()
        """,
        (
            int(channel["id"]), report_date, round_key, channel.get("platform"),
            channel.get("account_handle"), json.dumps(payload, ensure_ascii=False), str(model or ""),
        ),
    )
    conn.commit()


def generate_one(channel: dict[str, Any], *, report_date: str, round_key: str = "daily", language: str = "zh") -> dict[str, Any]:
    conn = get_conn()
    data = _account_data(conn, channel)
    if not _has_signal(data):
        return {"channel_id": int(channel["id"]), "status": "skipped", "reason": "no_data"}
    if not budget_guard.check_budget(_BUDGET_SCOPE, _EST_COST):
        logger.info("official_daily_report.budget_blocked", extra={"scope": _BUDGET_SCOPE, "channel_id": channel.get("id")})
        return {"channel_id": int(channel["id"]), "status": "budget_blocked"}
    text = _report_text(data)
    raw, model_used = _generate(_build_prompt(text, str(channel.get("account_handle") or ""), str(channel.get("platform") or ""), language))
    analysis = _normalize(_parse_json(raw))
    if not analysis["play_performance"] and not analysis["suggestions"] and not analysis["headline"]:
        return {"channel_id": int(channel["id"]), "status": "analysis_unavailable"}
    try:
        budget_guard.record_cost(scope=_BUDGET_SCOPE, cost_usd=_EST_COST)
    except Exception:
        logger.debug("official_daily_report.record_cost_failed", exc_info=True)
    facts = {
        "trend_days": len(data.get("trend_7d") or []),
        "post_count": (data.get("posts") or {}).get("post_count"),
        "comment_total": (data.get("comments") or {}).get("total"),
    }
    _store(conn, channel, report_date, round_key, analysis, model_used, facts)
    return {"channel_id": int(channel["id"]), "status": "ok", "model": model_used}


def generate_official_daily_reports(*, round_key: str = "daily", language: str = "zh", report_date: str | None = None) -> dict[str, Any]:
    """每日 cron 入口:循环 18 官号各生成一份报告。预算闸逐账号硬限,超限即停余下。"""
    conn = get_conn()
    rdate = report_date or date.today().isoformat()
    channels = _official_channels(conn)
    results: list[dict[str, Any]] = []
    ok = skipped = blocked = failed = 0
    for ch in channels:
        try:
            r = generate_one(ch, report_date=rdate, round_key=round_key, language=language)
        except Exception as exc:
            logger.warning("official_daily_report.account_failed | channel_id=%s error=%s", ch.get("id"), exc)
            r = {"channel_id": int(ch["id"]), "status": "error", "error": str(exc)[:200]}
        results.append(r)
        st = r.get("status")
        if st == "ok":
            ok += 1
        elif st == "skipped":
            skipped += 1
        elif st == "budget_blocked":
            blocked += 1
        else:
            failed += 1
    logger.info(
        "official_daily_report.run_done | round=%s date=%s ok=%s skipped=%s blocked=%s failed=%s",
        round_key, rdate, ok, skipped, blocked, failed,
    )
    return {"report_date": rdate, "round_key": round_key, "total": len(channels), "ok": ok, "skipped": skipped, "blocked": blocked, "failed": failed, "results": results}


def latest_report_for_channel(channel_id: int) -> dict[str, Any] | None:
    """读端:取该官号最新一份报告(任意轮)。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT channel_id, report_date, round_key, platform, handle, report_json, model, updated_at "
        "FROM vkpi_official_account_daily_report WHERE channel_id = ? "
        "ORDER BY report_date DESC, updated_at DESC LIMIT 1",
        (int(channel_id),),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["report"] = json.loads(d.pop("report_json") or "{}")
    except Exception:
        d["report"] = {}
    return d

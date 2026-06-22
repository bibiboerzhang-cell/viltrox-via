"""路线3 · KOL 记忆 provenance(只读:系统"为什么记住这个 KOL")。

compute-on-read 从既有沉淀里组装一个 KOL 的长期记忆来源 + 引用(citations):
- 视频证据(vkpi_kol_video_evidence)→ 来自哪些视频
- 深析记录(vkpi_kol_llm_deep_analysis_results)→ 分析过几次/最近一次(只引存在性/类型,绝不读 fit 值)
- 项目/ROI(roi_aggregate)→ 合作过几个项目、ROI 几何
- 推荐漏斗(vkpi_recommendation_outcomes)→ 是否认领/建项目/触达
让 Agent 建议能带 provenance(来自哪条视频/哪个项目),而非只看实时 query。
红线:全程只读;绝不读写 viltrox_fit_score / llm_v6_fit;只引证据存在性,不臆造。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)


def _rows(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in get_conn().execute(sql, params).fetchall()]
    except Exception:
        logger.debug("provenance.query_failed", extra={"sql": sql[:60]}, exc_info=True)
        return []


def get_kol_provenance(kol_pool_id: int, *, staff: dict[str, Any] | None = None, video_limit: int = 5) -> dict[str, Any]:
    """组装一个 KOL 的记忆来源 + citations + "为什么记住他"一句话。只读。"""
    del staff
    kid = int(kol_pool_id or 0)
    if kid <= 0:
        return {"status": "not_found"}
    vlimit = max(1, min(int(video_limit or 5), 20))

    videos: list[dict[str, Any]] = []
    if table_exists("vkpi_kol_video_evidence"):
        videos = _rows(
            "SELECT id, content_url, video_title, platform, posted_at, view_count "
            "FROM vkpi_kol_video_evidence WHERE kol_pool_id = ? AND COALESCE(is_active, TRUE) = TRUE "
            "ORDER BY posted_at DESC NULLS LAST, id DESC LIMIT ?",
            (kid, vlimit),
        )

    analyses_count = 0
    latest_analysis: dict[str, Any] | None = None
    if table_exists("vkpi_kol_llm_deep_analysis_results"):
        # 只引"分析过几次 / 最近一次的类型与时间",绝不 SELECT llm_v6_fit(红线:不碰 fit 值)。
        rows = _rows(
            "SELECT id, analysis_kind, created_at FROM vkpi_kol_llm_deep_analysis_results "
            "WHERE kol_pool_id = ? ORDER BY created_at DESC NULLS LAST, id DESC LIMIT 50",
            (kid,),
        )
        analyses_count = len(rows)
        latest_analysis = rows[0] if rows else None

    outcomes: dict[str, Any] = {}
    if table_exists("vkpi_recommendation_outcomes"):
        rows = _rows(
            "SELECT was_claimed, project_created, outreach_sent, content_published, agreement_reached "
            "FROM vkpi_recommendation_outcomes WHERE kol_pool_id = ?",
            (kid,),
        )
        if rows:
            outcomes = {
                "records": len(rows),
                "claimed": sum(1 for r in rows if r.get("was_claimed")),
                "project_created": sum(1 for r in rows if r.get("project_created")),
                "outreach_sent": sum(1 for r in rows if r.get("outreach_sent")),
                "content_published": sum(1 for r in rows if r.get("content_published")),
                "agreement_reached": sum(1 for r in rows if r.get("agreement_reached")),
            }

    roi: dict[str, Any] = {}
    try:
        from app.domains.kol import roi_aggregate

        roi = roi_aggregate.get_kol_roi_summary(kid)
    except Exception:
        logger.debug("provenance.roi_failed", exc_info=True)

    # citations:扁平引用列表(给 Agent 建议带 provenance 用)。
    citations: list[dict[str, Any]] = []
    for v in videos:
        citations.append({"type": "video", "id": v.get("id"), "label": v.get("video_title") or v.get("content_url"), "url": v.get("content_url")})
    if latest_analysis:
        citations.append({"type": "deep_analysis", "id": latest_analysis.get("id"), "label": f"深析({latest_analysis.get('analysis_kind') or 'kol'})"})
    for pid in (roi.get("total_projects") and ["roi"] or []):
        citations.append({"type": "roi", "label": f"关联 {roi.get('total_projects')} 个项目"})

    # 一句话:为什么记住他。
    bits: list[str] = []
    if videos:
        bits.append(f"{len(videos)} 条视频证据")
    if analyses_count:
        bits.append(f"深析 {analyses_count} 次")
    if roi.get("total_projects"):
        bits.append(f"合作 {roi['total_projects']} 个项目")
    if outcomes.get("content_published"):
        bits.append(f"产出内容 {outcomes['content_published']} 次")
    why = "系统记住他因为:" + "、".join(bits) + "。" if bits else "暂无沉淀(新 KOL 或尚未分析/合作)。"

    return {
        "status": "ok",
        "kol_pool_id": kid,
        "why_remembered": why,
        "provenance": {
            "videos": videos,
            "deep_analyses_count": analyses_count,
            "latest_analysis": latest_analysis,
            "outcomes": outcomes,
            "roi": roi,
        },
        "citations": citations,
        "note": "记忆 provenance 只读组装,只引证据存在性;绝不读写 viltrox_fit_score。",
    }

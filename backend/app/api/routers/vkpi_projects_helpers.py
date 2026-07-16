"""Side-effect-free helpers shared by V-KPI project routes."""
from __future__ import annotations


def _resolve_video_cached_url(evidence_id: str) -> str | None:
    """按 video 证据 id 解析其 R2 缓存视频地址,供前端内联播放器与分镜分析共用一条轮询。

    背景:URL 结果卡从「会话历史」重建时会丢掉实时算出的 cached_video_url(历史里没存),
    导致分镜出来了、播放器不出。这里在分镜分析缓存接口顺带解析:证据 -> 平台/原生短码
    -> 现成的 cached_video_url_for_item(键与 worker 一致)。纯只读,任何异常静默返回 None,
    绝不影响分析主体渲染,绝不触碰 viltrox_fit_score。
    """
    try:
        eid = int(str(evidence_id).strip())
    except (TypeError, ValueError):
        return None
    if eid <= 0:
        return None
    try:
        from app.db.connection import get_conn
        from app.domains.kol.url_deep_crawl import classify_url
        from app.domains.media.cache import cached_video_url_for_item

        conn = get_conn()
        row = conn.execute(
            "SELECT platform, content_url FROM vkpi_kol_video_evidence WHERE id = ?",
            (eid,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        platform = str(data.get("platform") or "").strip().lower()
        content_url = str(data.get("content_url") or "").strip()
        if not platform or not content_url:
            return None
        classified = classify_url(content_url)
        video_key = str(getattr(classified, "video_id", "") or "").strip()
        if not video_key:
            return None
        return cached_video_url_for_item(platform, video_key)
    except Exception:
        return None

def _material_row_to_item(row: dict) -> dict:
    """物料行出参整理:metadata_json 解成 dict,坏 JSON 容错为空对象,不让单行坏数据炸整表。"""
    import json as _json

    item = dict(row)
    try:
        meta = _json.loads(str(item.pop("metadata_json", None) or "{}"))
    except Exception:
        meta = {}
    item["metadata"] = meta if isinstance(meta, dict) else {}
    return item

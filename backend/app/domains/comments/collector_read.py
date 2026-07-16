"""Read-model helpers for persisted KOL pool comments."""
from __future__ import annotations

from typing import Any


def list_pool_video_comments_impl(
    kol_pool_id: int,
    *,
    evidence_id: int,
    limit: int = 100,
    post_table: str,
    logger: Any,
) -> dict:
    POOL_EVIDENCE_POST_TABLE = post_table
    """读端:该 evidence 视频的已采评论。字段对齐前端 mapCommentRows。

    两源合一:
      1) 主源 vkpi_comments[post_table=evidence](KOL Pool 内置评论采集写入)。
      2) 主源为空时回退 kol_comments(account_dossier 账号扫描写入);经
         list_kol_comments 的 _post_lookup_kol_ids 桥把 kol_pool_id 解析到主
         kols.id,并按本视频 content_url 精确匹配 post_url —— 不混入其它视频。
    回退只在主源为空时触发,严格只增不减;source 字段标注命中源便于排查。
    """
    from app.db.connection import get_conn as _get_conn

    conn = _get_conn()
    owner = conn.execute(
        "SELECT kol_pool_id, content_url FROM vkpi_kol_video_evidence WHERE id=?",
        (int(evidence_id),),
    ).fetchone()
    if not owner or int(dict(owner)["kol_pool_id"] or 0) != int(kol_pool_id):
        raise LookupError("evidence not found for this kol")
    post_url = str(dict(owner).get("content_url") or "")
    safe_limit = max(1, min(500, int(limit or 100)))
    rows = conn.execute(
        """
        SELECT id, comment_text, author_handle, likes_count AS like_count,
               reply_count, created_at, platform
        FROM vkpi_comments
        WHERE post_table=? AND post_id=?
        ORDER BY COALESCE(likes_count,0) DESC, id ASC
        LIMIT ?
        """,
        (POOL_EVIDENCE_POST_TABLE, int(evidence_id), safe_limit),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["post_url"] = post_url
        items.append(item)
    source = "pool_evidence"
    if not items and post_url:
        # 回退:池内采集尚未跑过 → 读 account_dossier 桥下、同一视频(content_url)的评论
        try:
            from app.services.kol.account_dossier import list_kol_comments

            bridged = list_kol_comments(
                int(kol_pool_id), limit=safe_limit, offset=0, post_url=post_url
            )
            for row in bridged.get("items", []) or []:
                items.append(
                    {
                        "id": row.get("id"),
                        "comment_text": row.get("comment_text"),
                        "author_handle": row.get("author_handle"),
                        "like_count": row.get("like_count"),
                        "reply_count": None,
                        "created_at": row.get("created_at"),
                        "platform": row.get("platform"),
                        "post_url": row.get("post_url") or post_url,
                    }
                )
            if items:
                source = "kol_comments_bridge"
        except Exception:
            logger.exception(
                "list_pool_video_comments bridge fallback failed kol=%s evidence=%s",
                kol_pool_id,
                evidence_id,
            )
    return {"items": items, "page": {"total": len(items), "next_offset": 0}, "source": source}

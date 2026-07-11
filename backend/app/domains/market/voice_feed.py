"""市场之声「反馈流」分页只读数据装载(voice_feed v0)。

用途:给前端反馈流(voice-feed)提供 vkpi_comments 的分页原声列表,
每条带身份三分类(kol / owned / user)+ 关联主体 + 原帖 URL + 溯源信息。

数据链(全只读,单条 SQL 三路 LEFT JOIN,身份按 vkpi_comments.post_table 判定):
  vkpi_comments                主表(评论正文/平台/语言/点赞/时间/post_table/post_id);
  vkpi_employee_channels       post_table='vkpi_employee_channels' → identity=owned,
                               identity_ref = 官号显示名(空则 @handle);
                               post_url = account_url(频道 profile URL——该表无帖子级
                               URL,评论只挂到频道行,如实给频道链接,拿不到 = null);
  vkpi_kol_video_evidence      post_table='vkpi_kol_video_evidence' → identity=kol,
                               post_url = evidence.content_url(视频原帖);
      └ vkpi_kol_pool          evidence.kol_pool_id → pool.handle = identity_ref;
  identity_id(溯源身份跳):kol=evidence.kol_pool_id(KOL 档案页锚),
                               owned=employee_channels.id(官号矩阵行锚),user=null——
                               实体自身 id 非作者个人字段,前端据此把身份节点变可点跳转;
  vkpi_sentiment_results       c.sentiment_id → 情感标签(当前表空,过滤器保留、
                               命中为零时如实返回空列表,绝不编造);
  其余 post_table(industry_posts 等)→ identity=user,identity_ref 留空。

显示层宪法:返回体绝不含 raw_data_json / author_id / author_handle 等内部或
个人字段;身份主体只暴露 KOL handle / 官号名(identity_ref),普通用户一律留空。

compat 约定:SQL 占位符用 ?;SQL 字符串零字面 percent(不用 LIKE);
时间戳读回可能是 str,统一转 ISO-8601(UTC,Z 后缀);函数内懒 import get_conn。

红线:纯只读,零写库,零 LLM,零外调;零触 viltrox_fit_score、不碰 rule_v0
(本模块不产生任何评分,输出仅供展示,不回写任何 KOL/SKU 打分链路)。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

SOURCE_TABLE = "vkpi_comments"

# 分页护栏:limit 封顶 50(超出取 50,非法/缺省回落 20),offset 负数归 0。
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
# 评论正文截断长度(字符)
TEXT_MAX_CHARS = 400

# 身份口径:post_table → identity 三分类(合法过滤值)
OWNED_POST_TABLE = "vkpi_employee_channels"
KOL_POST_TABLE = "vkpi_kol_video_evidence"
IDENTITY_VALUES = ("kol", "owned", "user")

# ── SQL 常量(全参数化;身份判定 CASE 分支与 JOIN 同源,测试直接断言此常量)──
# 基础 WHERE:空正文行对反馈流无意义,如实排除(total 也按同口径计数)。
FEED_SELECT_SQL = """
    SELECT
        c.id,
        c.platform,
        c.comment_text,
        c.language_detected,
        c.likes_count,
        COALESCE(c.created_at, c.fetched_at) AS created_at,
        c.fetched_at,
        c.post_table,
        c.post_id,
        CASE
            WHEN c.post_table = 'vkpi_employee_channels' THEN 'owned'
            WHEN c.post_table = 'vkpi_kol_video_evidence' THEN 'kol'
            ELSE 'user'
        END AS identity,
        CASE
            WHEN c.post_table = 'vkpi_employee_channels'
                THEN COALESCE(NULLIF(ec.account_display_name, ''), ec.account_handle, '')
            WHEN c.post_table = 'vkpi_kol_video_evidence'
                THEN COALESCE(kp.handle, '')
            ELSE ''
        END AS identity_ref,
        CASE
            WHEN c.post_table = 'vkpi_employee_channels' THEN ec.id
            WHEN c.post_table = 'vkpi_kol_video_evidence' THEN ev.kol_pool_id
            ELSE NULL
        END AS identity_id,
        CASE
            WHEN c.post_table = 'vkpi_kol_video_evidence' THEN NULLIF(ev.content_url, '')
            WHEN c.post_table = 'vkpi_employee_channels' THEN NULLIF(ec.account_url, '')
            ELSE NULL
        END AS post_url
    FROM vkpi_comments c
    LEFT JOIN vkpi_employee_channels ec
        ON c.post_table = 'vkpi_employee_channels' AND ec.id = c.post_id
    LEFT JOIN vkpi_kol_video_evidence ev
        ON c.post_table = 'vkpi_kol_video_evidence' AND ev.id = c.post_id
    LEFT JOIN vkpi_kol_pool kp
        ON kp.id = ev.kol_pool_id
    LEFT JOIN vkpi_sentiment_results s
        ON s.id = c.sentiment_id
    WHERE c.comment_text IS NOT NULL AND c.comment_text <> ''
"""

# 计数与列表同 FROM/WHERE 口径(JOIN 全是按主键的 1:1 LEFT JOIN,不放大行数)。
FEED_COUNT_SQL = """
    SELECT COUNT(*) AS total
    FROM vkpi_comments c
    LEFT JOIN vkpi_sentiment_results s
        ON s.id = c.sentiment_id
    WHERE c.comment_text IS NOT NULL AND c.comment_text <> ''
"""

FEED_ORDER_SQL = """
    ORDER BY COALESCE(c.created_at, c.fetched_at) DESC NULLS LAST, c.id DESC
    LIMIT ? OFFSET ?
"""

# 身份过滤片段(参数化,值来自 OWNED/KOL_POST_TABLE 常量,不拼用户输入)
_IDENTITY_FILTER_SQL = {
    "owned": " AND c.post_table = ?",
    "kol": " AND c.post_table = ?",
    "user": " AND (c.post_table IS NULL OR (c.post_table <> ? AND c.post_table <> ?))",
}
_SENTIMENT_FILTER_SQL = " AND s.sentiment = ?"


def _int0(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _iso_utc(value: Any) -> str | None:
    """时间戳 → ISO-8601 UTC(Z 后缀);compat 可能读回 str,原样透传修 Z。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    if not text:
        return None
    return text.replace("+00:00", "Z")


def _build_filters(identity: str, sentiment: str) -> tuple[str, list[Any]]:
    """可选过滤 → (追加 WHERE 片段, 参数列表);全参数化,零字符串拼接用户值。"""
    where_sql = ""
    params: list[Any] = []
    if identity == "owned":
        where_sql += _IDENTITY_FILTER_SQL["owned"]
        params.append(OWNED_POST_TABLE)
    elif identity == "kol":
        where_sql += _IDENTITY_FILTER_SQL["kol"]
        params.append(KOL_POST_TABLE)
    elif identity == "user":
        where_sql += _IDENTITY_FILTER_SQL["user"]
        params.extend([OWNED_POST_TABLE, KOL_POST_TABLE])
    if sentiment:
        # sentiment 结果表当前为空:过滤器保留,命中零行如实返回空,不降级不编造。
        where_sql += _SENTIMENT_FILTER_SQL
        params.append(sentiment)
    return where_sql, params


def _row_to_item(rec: dict[str, Any]) -> dict[str, Any]:
    """DB 行 → 契约 item;只暴露契约字段,内部列(author_*、raw_*)一律不出。"""
    text = str(rec.get("comment_text") or "").strip()
    if len(text) > TEXT_MAX_CHARS:
        text = text[:TEXT_MAX_CHARS]
    identity = str(rec.get("identity") or "user")
    post_url = rec.get("post_url")
    post_url = str(post_url).strip() if post_url else None
    post_id = rec.get("post_id")
    identity_id = rec.get("identity_id")
    return {
        "id": _int0(rec.get("id")),
        "source_table": SOURCE_TABLE,
        "platform": str(rec.get("platform") or ""),
        "text": text,
        "language": str(rec.get("language_detected") or ""),
        "identity": identity,
        "identity_ref": str(rec.get("identity_ref") or ""),
        "identity_id": _int0(identity_id) if identity_id is not None else None,
        "post_url": post_url or None,
        "likes": _int0(rec.get("likes_count")),
        "created_at": _iso_utc(rec.get("created_at")),
        "prov": {
            "fetched_at": _iso_utc(rec.get("fetched_at")),
            "post_table": str(rec.get("post_table") or ""),
            "post_id": _int0(post_id) if post_id is not None else None,
        },
    }


def get_voice_feed(
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
    identity: str = "",
    sentiment: str = "",
    *,
    conn: Any = None,
) -> dict[str, Any]:
    """反馈流分页读取:{items, total, offset, limit}(契约与前端并行开发,固定)。

    identity ∈ {'', 'kol', 'owned', 'user'}(非法值抛 ValueError → 路由转 400);
    sentiment 透传到 vkpi_sentiment_results.sentiment(表空 → 如实空结果)。
    纯读,零写库;conn 可注入(测试),缺省懒取 get_conn。
    """
    from app.db.connection import get_conn

    identity = str(identity or "").strip().lower()
    sentiment = str(sentiment or "").strip().lower()
    if identity and identity not in IDENTITY_VALUES:
        raise ValueError("identity 只接受 kol / owned / user 或留空")

    offset = max(0, _int0(offset))
    limit = _int0(limit, DEFAULT_LIMIT)
    limit = max(1, min(limit if limit > 0 else DEFAULT_LIMIT, MAX_LIMIT))

    where_sql, params = _build_filters(identity, sentiment)
    db = conn or get_conn()

    total_row = db.execute(FEED_COUNT_SQL + where_sql, tuple(params)).fetchone()
    total = _int0(dict(total_row or {}).get("total"))

    rows = db.execute(
        FEED_SELECT_SQL + where_sql + FEED_ORDER_SQL,
        tuple(params) + (limit, offset),
    ).fetchall()
    items = [_row_to_item(dict(r)) for r in rows]

    return {"items": items, "total": total, "offset": offset, "limit": limit}

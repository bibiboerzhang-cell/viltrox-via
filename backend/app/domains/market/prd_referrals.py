"""市场之声「转产品部」PRD 转交账本(prd_referrals v1 · market → PRD 真闭环)。

用途:把市场之声的「转产品部」从诚实降级(按钮 disabled)变成真通路——
一条声音 / 一条规则建议转交产品部 = vkpi_market_prd_referrals 落一行(迁移 234)。

写路(create_referral,路由 POST /api/admin/vkpi/market/prd-referrals):
  来源二选一(SOURCE_TABLES 枚举,其余一律拒):
    vkpi_comments  反馈流单条:source_id=评论 id(文本化);写前回查该评论真实存在,
                   不存在 → comment_not_found(路由转 404),绝不给幽灵评论立账;
    lexicon_reco   规则建议(voice-report suggestions,纯规则实时生成、无独立存储表):
                   source_id=调用方给的稳定 key(kind + 标题主干,窗口间稳定),
                   无库可回查 → 如实按 key 幂等落账。
  幂等:UNIQUE(source_table, source_id) + INSERT ON CONFLICT DO NOTHING;
  已转交 → 返回已有行 already_referred=True,并发双击两边都读回同一真实行,
  绝不重复立账、绝不编造成功状态。

读路(list_referrals,路由 GET 同前缀):按 status(referred/accepted/rejected)过滤,
LIMIT 双层封顶 50(SQL 参数 + Python 切片);表未建 → status=absent 诚实空列表。
「已转产品部」KPI 窗口计数住 voice_report_ext._prd_referrals(窗口敏感,本模块不重复)。

显示层宪法:返回体只含本账本自身字段(item 键白名单 _ITEM_KEYS),绝不含
author_handle / author_id / raw_data_json 等评论作者个人字段(转交行本就不存这些)。

compat 约定:SQL 占位符用 ?;SQL 字符串零字面 percent、零 LIKE;时间戳读回可能是
str,统一转 ISO-8601(UTC,Z 后缀);get_conn 模块级引入(测试 monkeypatch 同款缝)。

红线:纯转交账本(判定与登记),零 LLM、零外调、零自动执行;零触 viltrox_fit_score、
不碰 rule_v0(本模块不产生任何评分,也不回写任何 KOL/SKU 打分链路)。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.coerce import _text
from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

TABLE = "vkpi_market_prd_referrals"
SOURCE_TABLES = ("vkpi_comments", "lexicon_reco")
STATUS_VALUES = ("referred", "accepted", "rejected")

# 护栏常量(SQL LIMIT ? + Python 切片双封顶;入库文本各自截断,防超长脏行)
DEFAULT_LIMIT = 50
MAX_LIMIT = 50
SOURCE_ID_MAX_CHARS = 200
TITLE_MAX_CHARS = 300
DETAIL_MAX_CHARS = 2000
CATEGORY_MAX_CHARS = 80

# ── SQL 常量(全 ? 参数化;零字面 percent;测试静态审查)────────────────────
_ROW_SELECT_SQL = """
    SELECT id, source_table, source_id, title, detail, category, status,
           created_by_staff_id, created_at, updated_at
    FROM vkpi_market_prd_referrals
    WHERE source_table = ? AND source_id = ?
    LIMIT 1
"""

_INSERT_SQL = """
    INSERT INTO vkpi_market_prd_referrals (
      source_table, source_id, title, detail, category, status,
      created_by_staff_id, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, 'referred', ?, ?, ?)
    ON CONFLICT (source_table, source_id) DO NOTHING
"""

_COMMENT_PROBE_SQL = """
    SELECT id FROM vkpi_comments WHERE id = ? LIMIT 1
"""

_LIST_SELECT_SQL = """
    SELECT id, source_table, source_id, title, detail, category, status,
           created_by_staff_id, created_at, updated_at
    FROM vkpi_market_prd_referrals
"""

_LIST_ORDER_SQL = """
    ORDER BY id DESC
    LIMIT ?
"""

_STATUS_FILTER_SQL = " WHERE status = ?"

_ITEM_KEYS = (
    "id", "source_table", "source_id", "title", "detail", "category",
    "status", "created_by_staff_id", "created_at", "updated_at",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_utc(value: Any) -> str | None:
    """时间戳 → ISO-8601 UTC(Z 后缀);compat 可能读回 str,原样透传修 Z。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    return text.replace("+00:00", "Z") if text else None


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row.get(key) if hasattr(row, "get") else row[key]
    except Exception:
        try:
            return dict(row).get(key, default)
        except Exception:
            return default


def _table_exists(conn: Any, table_name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _item(row: Any) -> dict[str, Any]:
    """转交行 → 契约 item(键白名单 _ITEM_KEYS;行里混入任何多余列一律不出)。"""
    staff_id = _row_get(row, "created_by_staff_id")
    try:
        staff_id = int(staff_id) if staff_id is not None else None
    except (TypeError, ValueError):
        staff_id = None
    return {
        "id": int(_row_get(row, "id") or 0),
        "source_table": _text(_row_get(row, "source_table")),
        "source_id": _text(_row_get(row, "source_id")),
        "title": _text(_row_get(row, "title")),
        "detail": _text(_row_get(row, "detail")),
        "category": _text(_row_get(row, "category")),
        "status": _text(_row_get(row, "status")),
        "created_by_staff_id": staff_id,
        "created_at": _iso_utc(_row_get(row, "created_at")),
        "updated_at": _iso_utc(_row_get(row, "updated_at")),
    }


# ══════════════════════════════════════════════════════════════════════════
# create_referral:转产品部落账(幂等;已转返回已有行)
# ══════════════════════════════════════════════════════════════════════════
def create_referral(
    source_table: str,
    source_id: str,
    title: str,
    *,
    detail: str = "",
    category: str = "",
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把一条声音 / 一条规则建议转交产品部(vkpi_market_prd_referrals 落一行,幂等)。

    幂等键 (source_table, source_id):已转交 → 返回已有行(already_referred=True),
    绝不重复立账;并发双击 → INSERT ON CONFLICT DO NOTHING,输者按 rowcount=0 判
    already_referred,两边最终都读回同一真实行(不编造成功状态)。
    source_table=vkpi_comments 时写前回查评论存在(comment_not_found → 路由 404)。
    红线:只读 vkpi_comments、只写 vkpi_market_prd_referrals,零触 viltrox_fit_score / rule_v0。
    """
    src_table = _text(source_table).lower()
    src_id = _text(source_id)[:SOURCE_ID_MAX_CHARS]
    clean_title = " ".join(_text(title).split())[:TITLE_MAX_CHARS]

    if src_table not in SOURCE_TABLES:
        return {"ok": False, "reason": "invalid_source_table", "allowed": list(SOURCE_TABLES)}
    if not src_id:
        return {"ok": False, "reason": "source_id_required"}
    if not clean_title:
        return {"ok": False, "reason": "title_required"}

    conn = get_conn()
    if not _table_exists(conn, TABLE):
        # 迁移 234 未 apply:如实报缺表,不假装成功。
        return {"ok": False, "reason": "prd_referrals_table_missing"}

    if src_table == "vkpi_comments":
        # 反馈流来源必须真实存在(不给幽灵评论立账);id 非数字同样按未找到处理。
        try:
            comment_id = int(src_id)
        except (TypeError, ValueError):
            comment_id = 0
        if comment_id <= 0:
            return {"ok": False, "reason": "comment_not_found", "source_id": src_id}
        probe = conn.execute(_COMMENT_PROBE_SQL, (comment_id,)).fetchone()
        if not probe:
            return {"ok": False, "reason": "comment_not_found", "source_id": src_id}
        src_id = str(comment_id)

    existing = conn.execute(_ROW_SELECT_SQL, (src_table, src_id)).fetchone()
    if existing:
        return {"ok": True, "already_referred": True, "item": _item(existing)}

    from app.domains.access import scope as access_scope
    actor = access_scope.actor_staff_id(staff) or None

    cur = conn.execute(
        _INSERT_SQL,
        (
            src_table,
            src_id,
            clean_title,
            _text(detail)[:DETAIL_MAX_CHARS],
            _text(category)[:CATEGORY_MAX_CHARS],
            actor,
            _now_iso(),
            _now_iso(),
        ),
    )
    conn.commit()
    try:
        inserted = int(getattr(cur, "rowcount", 0) or 0) > 0
    except Exception:
        inserted = True

    final = conn.execute(_ROW_SELECT_SQL, (src_table, src_id)).fetchone()
    if not final:
        # INSERT 后读不回 = 真失败(不假装成功)。
        return {"ok": False, "reason": "refer_failed", "source_table": src_table, "source_id": src_id}
    return {"ok": True, "already_referred": not inserted, "item": _item(final)}


# ══════════════════════════════════════════════════════════════════════════
# list_referrals:转交列表(只读;status 枚举过滤;LIMIT 双层封顶)
# ══════════════════════════════════════════════════════════════════════════
def list_referrals(*, status: str = "", limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """列出转交行(只读)。status ∈ {referred, accepted, rejected} 或留空(全量)。

    非法 status → {"ok": False, "reason": "invalid_status"}(路由转 400);
    表未建 → status=absent 诚实空列表(不 500、不编数)。
    """
    st = _text(status).lower()
    if st and st not in STATUS_VALUES:
        return {"ok": False, "reason": "invalid_status", "allowed": list(STATUS_VALUES)}

    conn = get_conn()
    if not _table_exists(conn, TABLE):
        return {
            "ok": True,
            "status": "absent",
            "reason": "vkpi_market_prd_referrals 表未建(迁移 234 未 apply)——诚实空列表。",
            "items": [],
            "count": 0,
        }

    safe_limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    sql = _LIST_SELECT_SQL
    params: list[Any] = []
    if st:
        sql += _STATUS_FILTER_SQL
        params.append(st)
    sql += _LIST_ORDER_SQL
    params.append(safe_limit)

    rows = conn.execute(sql, tuple(params)).fetchall()
    items = [_item(r) for r in list(rows)[:MAX_LIMIT]]
    return {"ok": True, "status": "ready", "items": items, "count": len(items)}

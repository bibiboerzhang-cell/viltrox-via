"""F3 · 活动费用/证据元数据(发票/图片/合同/现场照)。

本模块只管【元数据 + storage_ref】:真实二进制(发票 PDF / 现场照 / 合同扫描)由调用方既有
media 上传链上传到 R2 或本地后,把返回的 R2 key / 本地相对路径作为 storage_ref 落到这里。
本模块【不做】任何二进制 IO。落一行 vkpi_event_evidence,读时给出清单 + 发票金额合计。
红线:全程只读写本表;绝不触 viltrox_fit_score / rule_v0;缺表/异常 → 诚实降级。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

_TABLE = "vkpi_event_evidence"

# kind 白名单:发票 / 现场照 / 合同 / 收据 / 其他。
KIND_WHITELIST = ("invoice", "photo", "contract", "receipt", "other")


def _actor_id(staff: dict[str, Any] | None) -> int | None:
    """从 staff 取操作者 id;取不到返回 None(列可空)。"""
    if not staff:
        return None
    try:
        from app.domains.access import scope

        sid = int(scope.actor_staff_id(staff)) or None
        return sid
    except Exception:
        return None


def _norm_kind(kind: Any) -> str:
    """归一化 kind;不在白名单 → 'other'。"""
    k = str(kind or "").strip().lower()
    return k if k in KIND_WHITELIST else "other"


def record_evidence_meta(
    event_id: str,
    kind: str,
    filename: str,
    storage_ref: str,
    *,
    content_type: str = "",
    size_bytes: int = 0,
    amount_cents: int | None = None,
    note: str = "",
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """落一行证据元数据,返回 {status, id}。

    storage_ref 必填(R2 key 或本地相对路径);kind 经白名单校验,非法值归一到 'other'。
    amount_cents 仅发票/收据类有意义,可空。缺表/写失败 → 诚实降级返回 status 非 ok。
    """
    eid = str(event_id or "").strip()
    sref = str(storage_ref or "").strip()
    if not eid:
        return {"status": "invalid", "reason": "event_id_required"}
    if not sref:
        return {"status": "invalid", "reason": "storage_ref_required"}
    if not table_exists(_TABLE):
        return {"status": "unavailable", "reason": "migration_189_not_applied"}

    k = _norm_kind(kind)
    fname = str(filename or "").strip()
    ctype = str(content_type or "").strip()
    try:
        size = int(size_bytes or 0)
    except (TypeError, ValueError):
        size = 0
    amt: int | None = None
    if amount_cents not in (None, ""):
        try:
            amt = int(amount_cents)
        except (TypeError, ValueError):
            amt = None
    uploaded_by = _actor_id(staff)

    try:
        conn = get_conn()
        row = conn.execute(
            f"INSERT INTO {_TABLE} "
            f"(event_id, kind, filename, content_type, size_bytes, storage_ref, amount_cents, uploaded_by, note) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id, created_at",
            (eid, k, fname, ctype, size, sref, amt, uploaded_by, str(note or "")),
        ).fetchone()
        conn.commit()
        rec = dict(row) if row else {}
        return {
            "status": "ok",
            "id": rec.get("id"),
            "event_id": eid,
            "kind": k,
            "created_at": str(rec.get("created_at") or ""),
        }
    except Exception:
        logger.warning("event_evidence.record_failed", extra={"event_id": eid, "kind": k}, exc_info=True)
        return {"status": "error", "reason": "write_failed"}


def list_evidence(event_id: str, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """该活动证据清单(按 created_at 倒序)+ 发票金额合计。

    invoice_total_cents 只累加有金额的 invoice/receipt 行。缺表 → 空清单 + available=False。
    staff 位置参数仅为与 _guard/router 调用对齐,本身只读不鉴权(读收口在 router _assert_read)。
    """
    del staff
    eid = str(event_id or "").strip()
    if not eid:
        return {"status": "invalid", "reason": "event_id_required", "items": [], "available": False}
    if not table_exists(_TABLE):
        return {"status": "unavailable", "available": False, "items": [], "count": 0, "invoice_total_cents": 0}

    try:
        rows = get_conn().execute(
            f"SELECT id, event_id, kind, filename, content_type, size_bytes, storage_ref, "
            f"amount_cents, uploaded_by, note, created_at "
            f"FROM {_TABLE} WHERE event_id = ? ORDER BY created_at DESC, id DESC",
            (eid,),
        ).fetchall()
    except Exception:
        logger.warning("event_evidence.list_failed", extra={"event_id": eid}, exc_info=True)
        return {"status": "error", "available": False, "items": [], "count": 0, "invoice_total_cents": 0}

    items: list[dict[str, Any]] = []
    invoice_total = 0
    for r in rows:
        rec = dict(r)
        amt = rec.get("amount_cents")
        if rec.get("kind") in ("invoice", "receipt") and amt not in (None, ""):
            try:
                invoice_total += int(amt)
            except (TypeError, ValueError):
                pass
        items.append(
            {
                "id": rec.get("id"),
                "event_id": rec.get("event_id"),
                "kind": rec.get("kind"),
                "filename": rec.get("filename"),
                "content_type": rec.get("content_type"),
                "size_bytes": rec.get("size_bytes"),
                "storage_ref": rec.get("storage_ref"),
                "amount_cents": amt,
                "uploaded_by": rec.get("uploaded_by"),
                "note": rec.get("note"),
                "created_at": str(rec.get("created_at") or ""),
            }
        )

    return {
        "status": "ok",
        "available": True,
        "event_id": eid,
        "count": len(items),
        "invoice_total_cents": invoice_total,
        "items": items,
    }


def delete_evidence(evidence_id: int, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """删一行证据元数据(只删元数据,不动 R2/本地二进制)。

    返回 {status, deleted}。缺表/非法 id → 诚实降级。staff 仅留作审计入参,鉴权在 router 层。
    """
    del staff
    try:
        eid = int(evidence_id)
    except (TypeError, ValueError):
        return {"status": "invalid", "reason": "evidence_id_required", "deleted": 0}
    if not table_exists(_TABLE):
        return {"status": "unavailable", "reason": "migration_189_not_applied", "deleted": 0}
    try:
        conn = get_conn()
        cur = conn.execute(f"DELETE FROM {_TABLE} WHERE id = ?", (eid,))
        deleted = getattr(cur, "rowcount", 0) or 0
        conn.commit()
        return {"status": "ok", "deleted": int(deleted), "evidence_id": eid}
    except Exception:
        logger.warning("event_evidence.delete_failed", extra={"evidence_id": eid}, exc_info=True)
        return {"status": "error", "reason": "delete_failed", "deleted": 0}

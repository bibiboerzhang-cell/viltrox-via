"""P0-1 二期:KOL 池联系方式真值展开(二次确认 + 访问审计 hook)。

读端默认脱敏(pool_common.mask_pool_item 在 list_pool/get_item 出口套用,掩码 e***@d***)。
真值仅经本模块 view_kol_contact 展开:必须 confirm=True 二次确认,且每次展开写
vkpi_sensitive_access_logs(audit/service.log_sensitive_access)+ 更新 vkpi_kol_pool 审计计数列
(118 迁移:contact_reveal_count / contact_last_revealed_at / contact_last_revealed_by_staff_id)。

红线:只读真值 email/other_contacts_json + 写审计列;绝不触 viltrox_fit_score 或任何业务/排序列。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime

from app.core.logging import get_logger

logger = get_logger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_contact_audit_schema() -> None:
    """本地 SQLite 兼容:幂等补 118 迁移的审计列(Postgres 走正式迁移序列,不进此分支)。"""
    if is_postgres_runtime():
        return
    conn = get_conn()
    try:
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(vkpi_kol_pool)").fetchall()}
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        return
    try:
        if "contact_reveal_count" not in cols:
            conn.execute("ALTER TABLE vkpi_kol_pool ADD COLUMN contact_reveal_count INTEGER NOT NULL DEFAULT 0")
        if "contact_last_revealed_at" not in cols:
            conn.execute("ALTER TABLE vkpi_kol_pool ADD COLUMN contact_last_revealed_at TEXT")
        if "contact_last_revealed_by_staff_id" not in cols:
            conn.execute("ALTER TABLE vkpi_kol_pool ADD COLUMN contact_last_revealed_by_staff_id INTEGER")
        conn.commit()
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass


def _loads(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return []


def view_kol_contact(
    kol_pool_id: int,
    *,
    confirm: bool = False,
    staff: dict[str, Any] | None = None,
    page_path: str = "",
    ip: str = "",
    user_agent: str = "",
    purpose: str = "",
) -> dict[str, Any]:
    """展开单个 KOL 池条目的真值联系方式。

    confirm 必须显式为 True(前端二次确认弹窗回传)否则返回脱敏值 + need_confirm,不写审计。
    confirm=True:写 sensitive access 审计 + 更新展开计数,返回真值 email/other_contacts。
    """
    from app.domains.kol.pool_common import _mask_email, _mask_contacts_json

    conn = get_conn()
    row = conn.execute(
        "SELECT id, email, other_contacts_json FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    real_email = str(row["email"] or "")
    real_contacts = _loads(row["other_contacts_json"])

    if not confirm:
        return {
            "status": "need_confirm",
            "kol_pool_id": int(kol_pool_id),
            "email": _mask_email(real_email),
            "other_contacts": _loads(_mask_contacts_json(row["other_contacts_json"])),
            "contact_masked": True,
        }

    from app.domains.kol.contact_access import authorize_plaintext_contacts
    from app.core.permissions import check_kol_pool_employee_contact_permission

    authorized = authorize_plaintext_contacts(
        staff,
        resource_type="kol_pool",
        resource_id=int(kol_pool_id),
        page_path=page_path or f"/kol-pool/{int(kol_pool_id)}/contacts/reveal",
        ip=ip,
        user_agent=user_agent,
        metadata={
            "revealed": ["email", "other_contacts_json"],
            "purpose": str(purpose or ""),
            "has_email": bool(real_email),
            "ip_present": bool(ip),
            "user_agent_present": bool(user_agent),
        },
        permission_check=check_kol_pool_employee_contact_permission,
    )
    if not authorized:
        return {
            "status": "masked",
            "reason": "contacts_reveal_not_authorized_or_audit_failed",
            "kol_pool_id": int(kol_pool_id),
            "email": _mask_email(real_email),
            "other_contacts": _loads(_mask_contacts_json(row["other_contacts_json"])),
            "contact_masked": True,
        }

    from app.domains.kol.pool_contacts import merge_canonical_contacts

    merged = merge_canonical_contacts(
        {"email": real_email, "other_contacts_json": real_contacts},
        conn=conn,
        kol_pool_id=int(kol_pool_id),
    )
    real_email = str(merged.get("email") or "")
    real_contacts = _loads(merged.get("other_contacts_json"))

    staff_id = int((staff or {}).get("staff_id") or (staff or {}).get("id") or 0)

    # 更新展开计数留痕(118 列;SQLite 幂等建)。明文审计已成功，此处是辅助计数。
    try:
        _ensure_contact_audit_schema()
        conn.execute(
            """
            UPDATE vkpi_kol_pool
            SET contact_reveal_count = COALESCE(contact_reveal_count, 0) + 1,
                contact_last_revealed_at = ?,
                contact_last_revealed_by_staff_id = ?
            WHERE id = ?
            """,
            (_utcnow(), staff_id or None, int(kol_pool_id)),
        )
        conn.commit()
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass

    return {
        "status": "revealed",
        "kol_pool_id": int(kol_pool_id),
        "email": real_email,
        "other_contacts": real_contacts,
        "contact_masked": False,
    }

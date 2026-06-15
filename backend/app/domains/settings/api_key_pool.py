"""多账号 API key 池(设置位,7月手动填轮转)。

预留 H2 多账号 key 输入位:用户 7 月手动填,系统不自动轮转(worker 未接)。
安全契约:
- key 单向(只写入/不回读到前端);list/upsert 响应只回 key_prefix 掩码。
- key_encrypted 仅以 Fernet 密文落库;未配置加密密钥时降级为不可逆占位(sha256
  片段),**绝不明文落库、绝不明文进日志、绝不进 git**。
- pick_active_key() 解密仅在运行时内存返回,绝不写日志、绝不出 API(7月启用,worker 未接线)。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains import audit
from app.domains.access import scope

logger = get_logger(__name__)

# provider 白名单(同 migration 146 的 CHECK 约束 + secrets_admin.PROVIDER_ENV_KEYS)。
ALLOWED_PROVIDERS = ("gemini", "openai", "anthropic", "apify", "youtube", "resend")

_FERNET_ENV = "VKPI_KEY_POOL_FERNET_KEY"
_PLACEHOLDER_PREFIX = "UNENCRYPTED_PLACEHOLDER::"

_fernet_cache: Any = None
_fernet_loaded = False


# ---------------------------------------------------------------------------
# 时间 / JSON / 布尔适配(范式照抄 notifications.py)
# ---------------------------------------------------------------------------
def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _db_bool(value: Any) -> bool | int:
    return bool(value) if is_postgres_runtime() else (1 if value else 0)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 加密 helper(模块级)。cryptography==46.0.6 / Fernet 已装可用。
# ---------------------------------------------------------------------------
def _fernet() -> Any:
    """返回 Fernet 实例;VKPI_KEY_POOL_FERNET_KEY 未配置时返回 None(缓存)。"""
    global _fernet_cache, _fernet_loaded
    if _fernet_loaded:
        return _fernet_cache
    _fernet_loaded = True
    raw = (os.environ.get(_FERNET_ENV) or "").strip()
    if not raw:
        _fernet_cache = None
        return None
    try:
        from cryptography.fernet import Fernet

        _fernet_cache = Fernet(raw.encode("utf-8"))
    except Exception:
        # 绝不打印 key / 密钥内容
        logger.warning("%s 配置无效,key 池加密不可用,将降级为不可逆占位", _FERNET_ENV)
        _fernet_cache = None
    return _fernet_cache


def mask(plain: str) -> str:
    """前 6 位 + '...';短于 6 则全掩。绝不打印完整 key。"""
    text = str(plain or "")
    if len(text) < 6:
        return "***" if text else ""
    return text[:6] + "..."


def encrypt_key(plain: str) -> tuple[str, str]:
    """明文 -> (key_encrypted, key_prefix)。

    有 Fernet:返回密文。无:logger.warning + 返回不可逆占位(sha256 片段),
    **绝不返回/落库明文**。
    """
    prefix = mask(plain)
    fernet = _fernet()
    if fernet is not None:
        return fernet.encrypt(plain.encode("utf-8")).decode("utf-8"), prefix
    logger.warning(
        "%s 未配置,key 以不可逆占位入库,7月填真 key 前必须配置加密密钥",
        _FERNET_ENV,
    )
    digest = hashlib.sha256(plain.encode("utf-8")).hexdigest()[:16]
    placeholder = f"{_PLACEHOLDER_PREFIX}{digest}(set {_FERNET_ENV})"
    return placeholder, prefix


def decrypt_key(enc: str) -> str | None:
    """密文 -> 明文(供轮转 helper 用)。占位/解密失败返回 None。

    绝不抛、绝不打印密文/明文。
    """
    text = str(enc or "")
    if not text or text.startswith(_PLACEHOLDER_PREFIX):
        return None
    fernet = _fernet()
    if fernet is None:
        return None
    try:
        return fernet.decrypt(text.encode("utf-8")).decode("utf-8")
    except Exception:
        # 绝不把密文/明文写进 logger
        logger.warning("key 池条目解密失败(密文已跳过,不记录内容)")
        return None


# ---------------------------------------------------------------------------
# SQLite 自愈 schema(镜像 146 的 PG DDL;PG runtime 直接 return)
# ---------------------------------------------------------------------------
def ensure_vkpi_api_key_pool_schema() -> None:
    if is_postgres_runtime():
        return
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_api_key_pool (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_name TEXT NOT NULL,
            provider TEXT NOT NULL,
            key_encrypted TEXT NOT NULL DEFAULT '',
            key_prefix TEXT NOT NULL DEFAULT '',
            daily_quota INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            rotation_cursor INTEGER NOT NULL DEFAULT 0,
            last_used_at TEXT,
            updated_by_staff_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            CONSTRAINT vkpi_api_key_pool_provider_chk
                CHECK (provider IN ('gemini','openai','anthropic','apify','youtube','resend')),
            CONSTRAINT vkpi_api_key_pool_quota_chk CHECK (daily_quota >= 0),
            CONSTRAINT vkpi_api_key_pool_account_chk CHECK (length(trim(account_name)) > 0),
            CONSTRAINT vkpi_api_key_pool_uq UNIQUE (provider, account_name)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vkpi_api_key_pool_provider_enabled
            ON vkpi_api_key_pool(provider, enabled)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vkpi_api_key_pool_updated
            ON vkpi_api_key_pool(updated_at DESC)
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 行 -> 脱敏 payload(key_encrypted 永不出域)
# ---------------------------------------------------------------------------
def _row_to_public(row: Any) -> dict[str, Any]:
    item = dict(row)
    return {
        "id": int(item.get("id") or 0),
        "account_name": item.get("account_name") or "",
        "provider": item.get("provider") or "",
        "key_prefix": item.get("key_prefix") or "",
        "daily_quota": int(item.get("daily_quota") or 0),
        "enabled": bool(item.get("enabled")),
        "last_used_at": item.get("last_used_at") or None,
        "updated_at": item.get("updated_at") or "",
    }


def _validate(provider: str, account_name: str, daily_quota: int) -> None:
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError(f"unsupported provider (allowed: {', '.join(ALLOWED_PROVIDERS)})")
    if not account_name:
        raise ValueError("account_name required")
    if daily_quota < 0:
        raise ValueError("daily_quota must be >= 0")


# ---------------------------------------------------------------------------
# CRUD(全部 get_conn() + '?' + commit,SQL 禁裸 %)
# ---------------------------------------------------------------------------
def list_keys() -> dict[str, Any]:
    """脱敏列表;绝不回 key_encrypted / 明文。"""
    ensure_vkpi_api_key_pool_schema()
    rows = get_conn().execute(
        """
        SELECT id, account_name, provider, key_prefix, daily_quota, enabled,
               last_used_at, updated_at
        FROM vkpi_api_key_pool
        ORDER BY provider ASC, account_name ASC
        """
    ).fetchall()
    return {"keys": [_row_to_public(row) for row in rows]}


def upsert_key(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """新增 / 更新一条 key。key 非空才覆盖密文;空则保留旧密文(切换 enabled 用)。"""
    ensure_vkpi_api_key_pool_schema()
    provider = str((payload or {}).get("provider") or "").strip().lower()
    account_name = str((payload or {}).get("account_name") or "").strip()
    daily_quota = _int((payload or {}).get("daily_quota"), 0)
    enabled = bool((payload or {}).get("enabled", True))
    _validate(provider, account_name, daily_quota)

    actor = scope.actor_staff_id(staff)
    now = _utcnow()
    conn = get_conn()

    raw_key = (payload or {}).get("key")
    plain = str(raw_key) if raw_key is not None else ""
    has_key = bool(plain.strip())

    existing = conn.execute(
        "SELECT id, key_encrypted, key_prefix FROM vkpi_api_key_pool WHERE provider=? AND account_name=?",
        (provider, account_name),
    ).fetchone()

    if has_key:
        key_encrypted, key_prefix = encrypt_key(plain)
        audit_full = plain  # 仅供 sha256 hash;log_settings_change 内部不落明文
    elif existing:
        # 保留旧密文(不覆盖)
        existing_item = dict(existing)
        key_encrypted = existing_item.get("key_encrypted") or ""
        key_prefix = existing_item.get("key_prefix") or ""
        audit_full = ""
    else:
        # 新行且无 key:占位空密文,7月再填
        key_encrypted = ""
        key_prefix = ""
        audit_full = ""

    if existing:
        conn.execute(
            """
            UPDATE vkpi_api_key_pool
            SET key_encrypted=?, key_prefix=?, daily_quota=?, enabled=?,
                updated_by_staff_id=?, updated_at=?
            WHERE provider=? AND account_name=?
            """,
            (
                key_encrypted,
                key_prefix,
                daily_quota,
                _db_bool(enabled),
                actor or None,
                now,
                provider,
                account_name,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO vkpi_api_key_pool
                (account_name, provider, key_encrypted, key_prefix, daily_quota,
                 enabled, rotation_cursor, updated_by_staff_id, created_at, updated_at, metadata_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                account_name,
                provider,
                key_encrypted,
                key_prefix,
                daily_quota,
                _db_bool(enabled),
                0,
                actor or None,
                now,
                now,
                "{}",
            ),
        )
    conn.commit()

    audit.log_settings_change(
        staff_id=actor,
        change_type="api_key_pool_upsert",
        setting_key=f"{provider}:{account_name}",
        new_value_redacted=key_prefix,
        full_value=audit_full,  # 仅供 sha256;log_settings_change 内部不落明文
        metadata={"provider": provider, "enabled": enabled, "daily_quota": daily_quota, "key_changed": has_key},
    )

    row = conn.execute(
        """
        SELECT id, account_name, provider, key_prefix, daily_quota, enabled,
               last_used_at, updated_at
        FROM vkpi_api_key_pool WHERE provider=? AND account_name=?
        """,
        (provider, account_name),
    ).fetchone()
    return _row_to_public(row)


def delete_key(key_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_api_key_pool_schema()
    actor = scope.actor_staff_id(staff)
    conn = get_conn()
    cursor = conn.execute("DELETE FROM vkpi_api_key_pool WHERE id=?", (int(key_id),))
    conn.commit()
    deleted = getattr(cursor, "rowcount", 0) or 0
    audit.log_settings_change(
        staff_id=actor,
        change_type="api_key_pool_delete",
        setting_key=str(int(key_id)),
        metadata={"deleted": int(deleted)},
    )
    return {"deleted": int(deleted)}


# ---------------------------------------------------------------------------
# 轮转读取 helper(预埋,本轮不接 worker)
# ---------------------------------------------------------------------------
def pick_active_key(provider: str) -> dict[str, Any] | None:
    """挑一个启用的 key(解密明文仅运行时内存)。

    7月启用,worker 未接线。返回 {'account_name', 'key'(解密明文)} 或 None。
    **解密明文只在内部返回,绝不写日志、绝不出 API。**
    解密失败的条目自动跳过。
    """
    provider = str(provider or "").strip().lower()
    if provider not in ALLOWED_PROVIDERS:
        return None
    ensure_vkpi_api_key_pool_schema()
    rows = get_conn().execute(
        """
        SELECT id, account_name, key_encrypted
        FROM vkpi_api_key_pool
        WHERE provider=? AND enabled=?
        ORDER BY rotation_cursor ASC, last_used_at ASC NULLS FIRST, id ASC
        """
        if is_postgres_runtime()
        else """
        SELECT id, account_name, key_encrypted
        FROM vkpi_api_key_pool
        WHERE provider=? AND enabled=?
        ORDER BY rotation_cursor ASC, (last_used_at IS NULL) DESC, last_used_at ASC, id ASC
        """,
        (provider, _db_bool(True)),
    ).fetchall()
    for row in rows:
        item = dict(row)
        plain = decrypt_key(item.get("key_encrypted") or "")
        if plain:
            return {"account_name": item.get("account_name") or "", "key": plain}
    return None

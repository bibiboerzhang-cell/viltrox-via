"""
services/auth/token_revocation.py — 登录令牌的服务端吊销(users.token_version,S-02)。

JWT 本身无状态;吊销靠版本号:签发时把 users.token_version 写进载荷 ``tv``,
校验时(core/security.get_current_user)比对,不等即拒。改密 / 重置密码 / 登出 /
管理员踢人 → :func:`revoke_user_sessions`,一次让该用户全部既有令牌失效。

存储口径:列允许 NULL,NULL 等价 0(从未吊销过);旧令牌载荷缺 ``tv`` 也按 0 处理,
所以上线当刻不会踢任何在线用户。Postgres 由迁移 307 建列;本地 SQLite 运行时不跑
migrations/*.sql,首次读写时自愈加列(与 domains 里其它 ADD COLUMN 自愈同款)。
Postgres 缺列则直接抛错——fail closed,绝不把「读不到版本号」当成「版本号为 0」。

本模块刻意不 import core.security(它反向依赖本模块),缓存前缀在这里定义、那边引用。
"""
from __future__ import annotations

import threading

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.services.cache import cache_clear

logger = get_logger(__name__)

#: core/security 的用户认证缓存前缀(`auth:user:<uid>:<token-digest>`);单一真源在此。
AUTH_USER_CACHE_PREFIX = "auth:user:"
#: JWT 载荷里的版本号字段名。
TOKEN_VERSION_CLAIM = "tv"
_COLUMN = "token_version"
_sqlite_column_lock = threading.Lock()
_sqlite_column_ready = False


def _reset_for_tests() -> None:
    """测试用:每个用例换新 SQLite 库时重置「列已就绪」标记。"""
    global _sqlite_column_ready
    with _sqlite_column_lock:
        _sqlite_column_ready = False


def _pragma_column_name(row) -> str:
    try:
        return str(row["name"] or "")
    except (KeyError, IndexError, TypeError):
        pass
    try:
        return str(row[1] or "")
    except (IndexError, TypeError):
        return ""


def _iter_result_rows(result) -> list:
    """兼容层/围栏/测试桩的结果对象不一定有 fetchall(UserResult、FakeResult 只有 fetchone)。
    顺序:fetchall → 可迭代 → 有上限且去重的 fetchone 循环(测试桩的 fetchone 会反复返回同一行,
    无上限循环会挂死——09-02 实测)。"""
    fetchall = getattr(result, "fetchall", None)
    if callable(fetchall):
        try:
            return list(fetchall())
        except (AttributeError, TypeError):
            pass
    if hasattr(result, "__iter__"):
        try:
            return list(result)
        except (AttributeError, TypeError):
            pass
    rows: list = []
    fetchone = getattr(result, "fetchone", None)
    last = object()
    for _ in range(4096):
        if not callable(fetchone):
            break
        row = fetchone()
        if row is None or row is last:
            break
        rows.append(row)
        last = row
    return rows

def _ensure_sqlite_column(conn) -> None:
    """SQLite 运行时自愈加列;Postgres 由迁移 307 负责,这里不做任何事。

    探列不解析 PRAGMA(兼容层/围栏/测试桩的结果对象形状各异,09-02 曾因此挂死):
    直接 SELECT token_version;能查到 = 列在;只有 sqlite 明确报「no such column」才 ALTER。
    任何其它异常都视为「无法确认」,不改表、不阻断认证路径。
    """
    global _sqlite_column_ready
    if _sqlite_column_ready or is_postgres_runtime():
        return
    with _sqlite_column_lock:
        if _sqlite_column_ready:
            return
        missing = False
        try:
            conn.execute(f"SELECT {_COLUMN} FROM users LIMIT 1").fetchone()
        except Exception as exc:  # noqa: BLE001 - 兼容层异常类型不统一,按消息判定
            missing = "no such column" in str(exc).lower()
            if not missing:
                logger.debug("auth.token_version_column_probe_inconclusive | %s", type(exc).__name__)
                return
        if missing:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {_COLUMN} INTEGER")
                conn.commit()
                logger.warning("auth.token_version_column_added_sqlite")
            except Exception as exc:  # noqa: BLE001 - 并发下另一进程可能已加列
                if "duplicate column" not in str(exc).lower():
                    logger.warning("auth.token_version_column_add_failed | %s", type(exc).__name__)
                    return
        _sqlite_column_ready = True

def coerce_token_version(value) -> int:
    """NULL / 缺失 / 脏值一律视作 0;负数夹到 0。"""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def read_token_version(user_id: int, conn=None) -> int:
    """读当前版本号(NULL 等价 0);用户不存在返回 0。"""
    conn = conn if conn is not None else get_conn()
    _ensure_sqlite_column(conn)
    try:
        row = conn.execute(
            "SELECT COALESCE(token_version, 0) AS token_version FROM users WHERE id=?",
            (int(user_id),),
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 - 列缺失/桩连接:按 0 处理,不阻断认证
        logger.debug("auth.token_version_read_failed | %s", type(exc).__name__)
        return 0
    if not row:
        return 0
    try:
        value = row["token_version"]
    except (KeyError, IndexError, TypeError):
        value = getattr(row, "token_version", None)
    return coerce_token_version(value)


def token_version_matches(payload: dict, current_version) -> bool:
    """JWT 载荷 tv 与库里版本号一致才放行;缺 tv 的旧令牌按 0 比对。"""
    claimed = coerce_token_version((payload or {}).get(TOKEN_VERSION_CLAIM))
    return claimed == coerce_token_version(current_version)


def invalidate_auth_cache(user_id: int) -> None:
    """清该用户全部认证缓存条目(所有 token 摘要),让版本号立即生效。"""
    cache_clear(prefix=f"{AUTH_USER_CACHE_PREFIX}{int(user_id)}:")


def revoke_user_sessions(user_id: int, *, reason: str = "") -> int:
    """users.token_version +1 并清认证缓存;返回新版本号。

    调用方:登出、改密、重置密码、管理员踢人。同一事务内只碰这一列。
    """
    uid = int(user_id)
    conn = get_conn()
    _ensure_sqlite_column(conn)
    conn.execute(
        "UPDATE users SET token_version = COALESCE(token_version, 0) + 1 WHERE id=?",
        (uid,),
    )
    conn.commit()
    new_version = read_token_version(uid, conn)
    invalidate_auth_cache(uid)
    logger.info(
        "auth.sessions_revoked uid=%s token_version=%s reason=%s",
        uid,
        new_version,
        reason or "unspecified",
    )
    return new_version


__all__ = [
    "AUTH_USER_CACHE_PREFIX",
    "TOKEN_VERSION_CLAIM",
    "coerce_token_version",
    "invalidate_auth_cache",
    "read_token_version",
    "revoke_user_sessions",
    "token_version_matches",
]

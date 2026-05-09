"""scripts/_smoke_seed.py

R58E: 共享的 smoke seed helper.

之前 8+ 个 smoke 各自实现了 _seed_admin 方法,~300 行重复代码。
这个 helper 抽出共享逻辑,所有 smoke import 即可。

设计原则:
  - 不强制重写已经过的 smoke (现有 _seed_admin 继续可用)
  - 新 smoke / 重构时主动用这个 helper
  - 兼容 SQLite 和 Postgres 双环境
  - 容错 staff 表 schema 多版本

使用示例:
    from _smoke_seed import seed_admin

    class Smoke:
        def __init__(self):
            self.marker = f"vkpi-xxx-{int(time.time())}"
            self.conn = get_conn()
            self.user_id, self.staff_id = seed_admin(
                self.conn, marker=self.marker
            )
            self.token = make_token(self.user_id, "admin")
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    """ISO 时间戳"""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_postgres() -> bool:
    """检测当前是不是 Postgres 环境"""
    backend = os.environ.get("DB_RUNTIME_BACKEND", "").lower()
    if backend == "postgres":
        return True
    if backend == "sqlite":
        return False
    url = os.environ.get("DATABASE_URL", "").lower()
    return url.startswith(("postgres://", "postgresql://"))


def _bool(value: bool) -> Any:
    """当前 users/staff 运行表仍使用 INTEGER 存布尔值,统一写 0/1。"""
    return 1 if value else 0


def _json(value: Any) -> str:
    """JSON 序列化"""
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def seed_admin(
    conn,
    *,
    marker: str,
    suffix: str = "admin",
    role: str = "admin",
    vkpi_permission: str = "write",
    is_owner: bool = True,
    extra_permissions: dict[str, str] | None = None,
) -> tuple[int, int]:
    """
    Seed 一个 admin user + staff,返回 (user_id, staff_id).
    
    自动处理:
      - users 表 password_hash NOT NULL
      - users 表 email_verified boolean
      - staff 表 schema 多版本 (有/无 is_owner / email_domain_verified 等)
      - permissions_json 包含完整 vkpi 权限
      - SQLite/Postgres 双兼容
    
    参数:
      conn: 数据库连接
      marker: smoke 标记 (用于后续 cleanup)
      suffix: user 标识后缀 (默认 "admin")
      role: user.role (默认 "admin")
      vkpi_permission: vkpi tab 权限 (默认 "write")
      is_owner: 是不是 owner (默认 True,绕过 RBAC 边界检查)
      extra_permissions: 额外权限 (例如 {"kol_ops": "write"})
    
    返回:
      (user_id, staff_id)
    
    抛出:
      RuntimeError: 如果 staff INSERT 失败
    """
    n = _now()
    email = f"{marker}-{suffix}@example.com"
    name = f"{marker}-{suffix}"
    
    # ─── 1. INSERT users ──────────────────────
    conn.execute(
        """
        INSERT INTO users (
            created_at, email, password_hash, name,
            status, role, email_verified, avatar_url
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            n, email, "v2:00:00", name,
            "approved", role, _bool(True),
            f"https://avatar.example/{name}.png",
        ),
    )
    
    user_row = conn.execute(
        "SELECT id FROM users WHERE email=?", (email,)
    ).fetchone()
    if not user_row:
        raise RuntimeError(f"_smoke_seed: user INSERT failed for {email}")
    user_id = int(user_row["id"])
    
    # ─── 2. INSERT staff (兼容多版本 schema) ─
    permissions = {"vkpi": vkpi_permission}
    if extra_permissions:
        permissions.update(extra_permissions)
    
    # 完整版字段 (新 schema)
    full_cols = [
        "user_id", "role", "permissions_json",
        "mfa_enabled", "active", "invited_at",
        "is_owner", "email_domain_verified",
    ]
    full_values = [
        user_id, role, _json(permissions),
        _bool(False), _bool(True), n,
        _bool(is_owner), _bool(True),
    ]
    
    # 最小版字段 (旧 schema)
    min_cols = [
        "user_id", "role", "permissions_json",
        "mfa_enabled", "active", "invited_at",
    ]
    min_values = [
        user_id, role, _json(permissions),
        _bool(False), _bool(True), n,
    ]
    
    # 尝试完整版,失败降级到最小版
    try:
        ph = ",".join("?" for _ in full_cols)
        conn.execute(
            f"INSERT INTO staff ({', '.join(full_cols)}) VALUES ({ph})",
            full_values,
        )
    except Exception:
        ph = ",".join("?" for _ in min_cols)
        conn.execute(
            f"INSERT INTO staff ({', '.join(min_cols)}) VALUES ({ph})",
            min_values,
        )
    
    staff_row = conn.execute(
        "SELECT id FROM staff WHERE user_id=?", (user_id,)
    ).fetchone()
    if not staff_row:
        raise RuntimeError(f"_smoke_seed: staff INSERT failed for user_id={user_id}")
    staff_id = int(staff_row["id"])
    
    conn.commit()
    
    # 失效 user cache (避免上次 stale 数据)
    try:
        from app.core.security import invalidate_user_cache
        invalidate_user_cache(user_id)
    except Exception:
        pass
    
    return user_id, staff_id


def cleanup_admin(conn, *, user_id: int | None = None, staff_id: int | None = None) -> None:
    """
    清理 seed_admin 创建的 user + staff.
    
    设计原则:
      - 只清理 caller 持有 ID 的行,不依赖 marker
      - 失败不抛错 (cleanup 是 best-effort)
    """
    try:
        if staff_id:
            conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
        if user_id:
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    except Exception:
        pass
    
    # 失效 cache
    if user_id:
        try:
            from app.core.security import invalidate_user_cache
            invalidate_user_cache(user_id)
        except Exception:
            pass

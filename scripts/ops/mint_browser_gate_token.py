#!/usr/bin/env python3
"""Mint one short-lived admin JWT for the post-deploy browser gate.

The command is deliberately production-only and read-only.  It loads the
active release environment on the remote host, proves an approved/verified
active admin principal in PostgreSQL, signs with the active JWT secret, and
writes exactly the token to stdout.  Secrets, DSNs, and principal metadata are
never printed or written to disk.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any


MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 300
DEFAULT_TTL_SECONDS = 180
INSECURE_LOCAL_JWT_SECRET = "viltrox2-local-dev-secret-change-me"
ADMIN_QUERY = """
SELECT u.id
FROM users AS u
JOIN staff AS s ON s.user_id = u.id
WHERE lower(COALESCE(u.status, '')) = 'approved'
  AND COALESCE(u.email_verified, 0) = 1
  AND COALESCE(s.active, 0) = 1
  AND lower(COALESCE(s.role, '')) = 'admin'
ORDER BY COALESCE(s.is_owner, 0) DESC, u.id
LIMIT 1
"""


class MintError(RuntimeError):
    """Fail-closed browser-gate token mint error."""


def _backend_path() -> Path:
    return Path(__file__).resolve().parents[2] / "backend"


def load_runtime_contract() -> tuple[str, str, str, str]:
    """Load production DB/JWT settings without exposing their values."""

    os.environ.setdefault("LOG_LEVEL", "CRITICAL")
    backend = _backend_path()
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))

    from app.core.config import (  # noqa: PLC0415
        DB_RUNTIME_BACKEND,
        DB_RUNTIME_URL,
        ENVIRONMENT,
        IS_PRODUCTION,
        JWT_SECRET,
    )
    from app.core.security import JWT_AUDIENCE, JWT_ISSUER  # noqa: PLC0415

    if not IS_PRODUCTION or str(ENVIRONMENT).lower() not in {"production", "staging"}:
        raise MintError("browser gate token mint requires the production runtime")
    if str(DB_RUNTIME_BACKEND).lower() != "postgres" or not str(DB_RUNTIME_URL).strip():
        raise MintError("browser gate token mint requires PostgreSQL")
    secret = str(JWT_SECRET)
    if len(secret.encode("utf-8")) < 32 or secret == INSECURE_LOCAL_JWT_SECRET:
        raise MintError("browser gate token mint requires a strong production JWT secret")
    return str(DB_RUNTIME_URL), secret, str(JWT_ISSUER), str(JWT_AUDIENCE)


def select_admin_user_id(database_url: str) -> int:
    """Select one real active admin in a server-enforced read-only session."""

    try:
        import psycopg  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - production packaging guard
        raise MintError("PostgreSQL driver unavailable") from exc

    try:
        with psycopg.connect(
            database_url,
            connect_timeout=10,
            options="-c default_transaction_read_only=on",
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                readonly = cursor.fetchone()
                if not readonly or str(readonly[0]).lower() not in {"on", "true", "1"}:
                    raise MintError("admin lookup did not enter a read-only transaction")
                cursor.execute(ADMIN_QUERY)
                row = cursor.fetchone()
            conn.rollback()
    except MintError:
        raise
    except Exception as exc:
        raise MintError("admin lookup failed") from exc
    if not row or int(row[0] or 0) <= 0:
        raise MintError("no approved active admin principal is available")
    return int(row[0])


def mint_admin_token(
    *,
    user_id: int,
    secret: str,
    issuer: str,
    audience: str,
    ttl_seconds: int,
    now: int | None = None,
) -> str:
    """Create and self-verify the bounded token without persistence."""

    ttl = int(ttl_seconds)
    if ttl < MIN_TTL_SECONDS or ttl > MAX_TTL_SECONDS:
        raise MintError(
            f"token TTL must be within [{MIN_TTL_SECONDS}, {MAX_TTL_SECONDS}] seconds"
        )
    if int(user_id) <= 0 or len(str(secret).encode("utf-8")) < 32:
        raise MintError("invalid signing contract")
    if not str(issuer).strip() or not str(audience).strip():
        raise MintError("invalid JWT audience contract")

    import jwt  # noqa: PLC0415

    issued_at = int(time.time() if now is None else now)
    payload: dict[str, Any] = {
        "uid": int(user_id),
        "role": "admin",
        "iss": str(issuer),
        "aud": str(audience),
        "iat": issued_at,
        "nbf": issued_at,
        "exp": issued_at + ttl,
    }
    token = str(jwt.encode(payload, secret, algorithm="HS256"))
    if not token or "\n" in token or "\r" in token:
        raise MintError("JWT encoder returned an invalid token")
    verified = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience=audience,
        issuer=issuer,
        options={"require": ["uid", "role", "iss", "aud", "iat", "nbf", "exp"]},
    )
    if int(verified.get("uid") or 0) != int(user_id) or verified.get("role") != "admin":
        raise MintError("JWT self-verification failed")
    return token


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Mint a transient browser-gate admin JWT")
    result.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        database_url, secret, issuer, audience = load_runtime_contract()
        user_id = select_admin_user_id(database_url)
        token = mint_admin_token(
            user_id=user_id,
            secret=secret,
            issuer=issuer,
            audience=audience,
            ttl_seconds=args.ttl_seconds,
        )
    except Exception:
        sys.stderr.write("browser gate token mint failed\n")
        return 1
    sys.stdout.write(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

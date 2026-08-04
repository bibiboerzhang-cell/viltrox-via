#!/usr/bin/env python3
"""Derive or validate the local Web PgBouncer URL without logging secrets.

stdin contains the direct PostgreSQL URL followed by an optional pool URL.  The
only output is the validated URL for the caller's command substitution; errors
are intentionally generic so credentials never reach startup logs.
"""

from __future__ import annotations

import sys
from urllib.parse import SplitResult, parse_qsl, unquote, urlsplit, urlunsplit


MAX_INPUT_BYTES = 16 * 1024
ENDPOINT_QUERY_KEYS = {"database", "dbname", "host", "hostaddr", "port", "service"}
LOCAL_DIRECT_HOSTS = {"127.0.0.1", "localhost", "::1"}


class LocalPoolUrlError(ValueError):
    pass


def _database_identity(raw: str, *, pool: bool) -> tuple[SplitResult, str]:
    if not raw or any(character in raw for character in "\r\n\0"):
        raise LocalPoolUrlError
    try:
        parsed = urlsplit(raw)
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        raise LocalPoolUrlError from None
    hostname = str(parsed.hostname or "").casefold()
    database = unquote(parsed.path[1:]) if parsed.path.startswith("/") else ""
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not database
        or "/" in database
        or parsed.path.count("/") != 1
        or parsed.fragment
        or any(str(key).casefold() in ENDPOINT_QUERY_KEYS for key, _value in query)
    ):
        raise LocalPoolUrlError
    if pool:
        if hostname != "127.0.0.1" or port != 6432:
            raise LocalPoolUrlError
    elif hostname not in LOCAL_DIRECT_HOSTS or port == 6432:
        raise LocalPoolUrlError
    return parsed, database


def derive_local_pool_url(direct_url: str, existing_pool_url: str = "") -> str:
    direct, direct_database = _database_identity(direct_url, pool=False)
    if existing_pool_url:
        _pool, pool_database = _database_identity(existing_pool_url, pool=True)
        if pool_database != direct_database:
            raise LocalPoolUrlError
        return existing_pool_url

    raw_netloc = str(direct.netloc)
    raw_userinfo = raw_netloc.rsplit("@", 1)[0] + "@" if "@" in raw_netloc else ""
    return urlunsplit(
        (
            str(direct.scheme),
            f"{raw_userinfo}127.0.0.1:6432",
            str(direct.path),
            str(direct.query),
            "",
        )
    )


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        sys.stderr.write("local PgBouncer URL derivation failed\n")
        return 1
    try:
        text = raw.decode("utf-8")
        values = text.splitlines()
        direct_url = values[0] if values else ""
        existing_pool_url = values[1] if len(values) > 1 else ""
        if len(values) > 2:
            raise LocalPoolUrlError
        result = derive_local_pool_url(direct_url, existing_pool_url)
    except (LocalPoolUrlError, UnicodeDecodeError):
        sys.stderr.write("local PgBouncer URL derivation failed\n")
        return 1
    sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

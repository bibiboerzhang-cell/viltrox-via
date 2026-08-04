"""Database URL query contract shared by the staging-clone controller."""

from __future__ import annotations

from urllib.parse import parse_qsl


SAFE_DATABASE_QUERY_PARAMETERS = frozenset(
    {
        "application_name",
        "channel_binding",
        "connect_timeout",
        "fallback_application_name",
        "gssencmode",
        "keepalives",
        "keepalives_count",
        "keepalives_idle",
        "keepalives_interval",
        "ssl_min_protocol_version",
        "ssl_max_protocol_version",
        "sslcrl",
        "sslcrldir",
        "sslmode",
        "sslrootcert",
        "sslsni",
        "tcp_user_timeout",
    }
)


def query_preserves_database_identity(query: str) -> bool:
    """Reject libpq parameters that can override endpoint or database identity."""

    pairs = parse_qsl(
        query,
        keep_blank_values=True,
        strict_parsing=True,
        max_num_fields=64,
    )
    return all(key.lower() in SAFE_DATABASE_QUERY_PARAMETERS for key, _ in pairs)


__all__ = ["SAFE_DATABASE_QUERY_PARAMETERS", "query_preserves_database_identity"]

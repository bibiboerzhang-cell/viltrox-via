"""Tests for N8 multi-key pool selection in the model router (pure, offline).

These exercise the *selection* logic only (rotation + quota/health/cooldown
gating). No DB, no decryption, no real LLM call — that wiring is a later cut.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.platform.models import router
from app.platform.models.router import (
    _key_available,
    _pool_provider_name,
    select_key_from_rows,
)

NOW = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)


def _row(**over):
    base = {
        "id": 1,
        "account_name": "acct",
        "provider": "gemini",
        "key_encrypted": "enc",
        "daily_quota": 0,
        "enabled": 1,
        "rotation_cursor": 0,
        "last_used_at": None,
        "metadata_json": "{}",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Provider alias bridging (google -> gemini etc.)
# ---------------------------------------------------------------------------
def test_provider_alias_google_maps_to_gemini():
    assert _pool_provider_name("google") == "gemini"
    assert _pool_provider_name("GEMINI") == "gemini"
    assert _pool_provider_name("openai") == "openai"
    assert _pool_provider_name("anthropic") == "anthropic"
    # Unknown passes through normalised (lower/strip), not dropped.
    assert _pool_provider_name("  Foo ") == "foo"
    assert _pool_provider_name("") == ""


# ---------------------------------------------------------------------------
# Empty / no-candidate -> None (caller falls back to single key)
# ---------------------------------------------------------------------------
def test_empty_pool_returns_none():
    assert select_key_from_rows([], now=NOW) is None


def test_all_disabled_returns_none():
    rows = [_row(id=1, enabled=0), _row(id=2, enabled=0)]
    assert select_key_from_rows(rows, now=NOW) is None


# ---------------------------------------------------------------------------
# Rotation: round-robin by rotation_cursor, then least-recently-used, then id
# ---------------------------------------------------------------------------
def test_lowest_rotation_cursor_wins():
    rows = [
        _row(id=1, account_name="a", rotation_cursor=5),
        _row(id=2, account_name="b", rotation_cursor=1),
        _row(id=3, account_name="c", rotation_cursor=9),
    ]
    chosen = select_key_from_rows(rows, now=NOW)
    assert chosen is not None
    assert chosen["account_name"] == "b"


def test_never_used_preferred_over_recently_used():
    rows = [
        _row(id=1, account_name="used", rotation_cursor=0,
             last_used_at="2026-06-29T10:00:00Z"),
        _row(id=2, account_name="fresh", rotation_cursor=0, last_used_at=None),
    ]
    chosen = select_key_from_rows(rows, now=NOW)
    assert chosen["account_name"] == "fresh"


def test_least_recently_used_among_used():
    rows = [
        _row(id=1, account_name="recent", rotation_cursor=0,
             last_used_at="2026-06-29T11:59:00Z"),
        _row(id=2, account_name="older", rotation_cursor=0,
             last_used_at="2026-06-29T08:00:00Z"),
    ]
    chosen = select_key_from_rows(rows, now=NOW)
    assert chosen["account_name"] == "older"


def test_tie_breaks_on_id():
    rows = [
        _row(id=7, account_name="seven", rotation_cursor=0, last_used_at=None),
        _row(id=3, account_name="three", rotation_cursor=0, last_used_at=None),
    ]
    chosen = select_key_from_rows(rows, now=NOW)
    assert chosen["account_name"] == "three"


# ---------------------------------------------------------------------------
# Quota gating: daily_quota==0 unlimited; used>=quota -> skip
# ---------------------------------------------------------------------------
def test_quota_zero_means_unlimited():
    rows = [_row(id=1, daily_quota=0, metadata_json='{"quota_used": 9999}')]
    assert select_key_from_rows(rows, now=NOW) is not None


def test_exhausted_quota_skipped():
    rows = [
        _row(id=1, account_name="full", daily_quota=100,
             metadata_json='{"quota_used": 100}'),
        _row(id=2, account_name="ok", daily_quota=100,
             metadata_json='{"quota_used": 10}'),
    ]
    chosen = select_key_from_rows(rows, now=NOW)
    assert chosen["account_name"] == "ok"


def test_all_quota_exhausted_returns_none():
    rows = [
        _row(id=1, daily_quota=50, metadata_json='{"quota_used": 50}'),
        _row(id=2, daily_quota=50, metadata_json='{"quota_used": 80}'),
    ]
    assert select_key_from_rows(rows, now=NOW) is None


# ---------------------------------------------------------------------------
# Health gating
# ---------------------------------------------------------------------------
def test_unhealthy_key_skipped():
    rows = [
        _row(id=1, account_name="bad", metadata_json='{"health": "down"}'),
        _row(id=2, account_name="good", metadata_json='{"health": "ok"}'),
    ]
    chosen = select_key_from_rows(rows, now=NOW)
    assert chosen["account_name"] == "good"


def test_missing_health_treated_ok():
    rows = [_row(id=1, account_name="x", metadata_json="{}")]
    assert select_key_from_rows(rows, now=NOW) is not None


# ---------------------------------------------------------------------------
# Cooldown gating
# ---------------------------------------------------------------------------
def test_key_in_cooldown_skipped():
    future = (NOW + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [
        _row(id=1, account_name="cooling",
             metadata_json='{"cooldown_until": "%s"}' % future),
        _row(id=2, account_name="ready", metadata_json="{}"),
    ]
    chosen = select_key_from_rows(rows, now=NOW)
    assert chosen["account_name"] == "ready"


def test_expired_cooldown_is_usable():
    past = (NOW - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [_row(id=1, account_name="recovered",
                 metadata_json='{"cooldown_until": "%s"}' % past)]
    chosen = select_key_from_rows(rows, now=NOW)
    assert chosen is not None
    assert chosen["account_name"] == "recovered"


# ---------------------------------------------------------------------------
# Combined gate (BOOLEAN int readback tolerance + dict metadata)
# ---------------------------------------------------------------------------
def test_enabled_bool_true_readback():
    # PG-style bool True should be accepted just like int 1.
    rows = [_row(id=1, account_name="pg", enabled=True)]
    assert select_key_from_rows(rows, now=NOW) is not None


def test_metadata_as_dict_supported():
    rows = [_row(id=1, account_name="d", metadata_json={"health": "down"})]
    assert select_key_from_rows(rows, now=NOW) is None


def test_key_available_defaults_permissive():
    assert _key_available(_row(), now=NOW) is True


# ---------------------------------------------------------------------------
# select_key_for: when settings module is unreachable -> None (no crash)
# ---------------------------------------------------------------------------
def test_select_key_for_unknown_provider_none():
    assert router.select_key_for("") is None


def test_select_key_for_returns_none_or_dict(monkeypatch):
    # With no pool rows configured, must degrade to None (single-key fallback),
    # never raise.
    result = router.select_key_for("nonexistent_provider_xyz")
    assert result is None or isinstance(result, dict)

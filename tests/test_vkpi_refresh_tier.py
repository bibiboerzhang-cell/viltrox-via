from __future__ import annotations

from typing import Any

from app.services.vkpi import refresh_tier


def _kol(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": 101,
        "pool_uid": "pool:unit",
        "platform": "youtube",
        "handle": "unit_creator",
        "linked_main_kol_id": None,
    }
    base.update(overrides)
    return base


def _install_compute_harness(monkeypatch, kol: dict[str, Any], existing: dict[str, Any] | None = None) -> None:
    monkeypatch.setattr(refresh_tier, "_load_kol_row", lambda _kol_pool_id: dict(kol))
    monkeypatch.setattr(refresh_tier, "_existing_tier", lambda _kol_pool_id: dict(existing or {}))
    monkeypatch.setattr(refresh_tier, "_active_campaign_count", lambda _kol: 0)
    monkeypatch.setattr(refresh_tier, "_recent_project_count", lambda _kol, days=180: 0)
    monkeypatch.setattr(refresh_tier, "_shipping_count", lambda _kol, days=90: 0)
    monkeypatch.setattr(
        refresh_tier,
        "_brand_signal_counts",
        lambda _kol, days=60: {"self_total": 0, "viltrox_mentions": 0, "sku_mentions": 0},
    )


def test_compute_tier_manual_hot_wins(monkeypatch) -> None:
    _install_compute_harness(monkeypatch, _kol(), {"manual_hot_flag": True})

    result = refresh_tier.compute_kol_tier(101)

    assert result["tier"] == "hot"
    assert result["tier_reason"] == "manual_hot_flag"


def test_compute_tier_recent_sku_signal_is_hot(monkeypatch) -> None:
    _install_compute_harness(monkeypatch, _kol())
    monkeypatch.setattr(
        refresh_tier,
        "_brand_signal_counts",
        lambda _kol, days=60: {"self_total": 2, "viltrox_mentions": 0, "sku_mentions": 2},
    )

    result = refresh_tier.compute_kol_tier(101)

    assert result["tier"] == "hot"
    assert result["tier_reason"] == "sku_mention_60d"


def test_compute_tier_recent_search_is_warm(monkeypatch) -> None:
    _install_compute_harness(monkeypatch, _kol(), {"search_count_30d": 1})

    result = refresh_tier.compute_kol_tier(101)

    assert result["tier"] == "warm"
    assert result["tier_reason"] == "search_30d"


def test_qualified_refresh_rows_do_not_fallback_without_tier_table(monkeypatch) -> None:
    monkeypatch.setattr(refresh_tier, "_table_exists", lambda table: False)

    rows = refresh_tier.qualified_refresh_rows(limit=50)
    counts = refresh_tier.qualified_source_counts()

    assert rows == []
    assert counts["selector_ready"] is False
    assert counts["source_total"] == 0


def test_qualified_refresh_rows_default_to_never_refreshed(monkeypatch) -> None:
    class Cursor:
        def fetchall(self) -> list[dict[str, Any]]:
            return []

    class Conn:
        def __init__(self) -> None:
            self.sql = ""
            self.params: tuple[object, ...] = ()

        def execute(self, sql: str, params: tuple[object, ...]):
            self.sql = sql
            self.params = params
            return Cursor()

    conn = Conn()
    monkeypatch.setattr(refresh_tier, "_table_exists", lambda table: table == "vkpi_kol_refresh_tier")
    monkeypatch.setattr(refresh_tier, "get_conn", lambda: conn)

    rows = refresh_tier.qualified_refresh_rows(limit=25, tiers={"hot"})

    assert rows == []
    assert "rt.last_refresh_at IS NULL" in conn.sql
    assert "COALESCE(rt.last_refresh_at" in conn.sql  # ORDER BY only; filter must stay tier-owned.
    assert "last_refresh_at, kp.last_seen_at" not in conn.sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]


def test_qualified_refresh_rows_stale_cutoff_includes_never_refreshed(monkeypatch) -> None:
    class Cursor:
        def fetchall(self) -> list[dict[str, Any]]:
            return []

    class Conn:
        def __init__(self) -> None:
            self.sql = ""
            self.params: tuple[object, ...] = ()

        def execute(self, sql: str, params: tuple[object, ...]):
            self.sql = sql
            self.params = params
            return Cursor()

    conn = Conn()
    cutoff = "2026-05-23T00:00:00Z"
    monkeypatch.setattr(refresh_tier, "_table_exists", lambda table: table == "vkpi_kol_refresh_tier")
    monkeypatch.setattr(refresh_tier, "get_conn", lambda: conn)

    rows = refresh_tier.qualified_refresh_rows(limit=25, tiers={"hot"}, stale_before=cutoff)

    assert rows == []
    assert "(rt.last_refresh_at IS NULL OR rt.last_refresh_at < ?)" in conn.sql
    assert cutoff in conn.params


def test_record_kol_search_preserves_existing_hot_evidence(monkeypatch) -> None:
    class Row(dict):
        def keys(self):
            return super().keys()

    class Cursor:
        def __init__(self, row=None) -> None:
            self.row = row

        def fetchone(self):
            return self.row

        def fetchall(self) -> list[dict[str, Any]]:
            return []

    class Conn:
        def __init__(self) -> None:
            self.statements: list[str] = []
            self.commits = 0

        def execute(self, sql: str, params: tuple[object, ...] = ()):
            self.statements.append(sql)
            if "FROM vkpi_kol_pool" in sql:
                return Cursor(Row({"id": 42, "platform": "youtube", "handle": "unit"}))
            if "FROM vkpi_kol_refresh_tier" in sql:
                return Cursor(Row({
                    "kol_pool_id": 42,
                    "tier": "hot",
                    "tier_reason": "viltrox_mention_60d",
                    "tier_reason_json": '{"viltrox_mentions": 1}',
                    "search_count_30d": 2,
                    "last_searched_at": "2026-05-23T00:00:00Z",
                }))
            return Cursor()

        def commit(self) -> None:
            self.commits += 1

    conn = Conn()
    monkeypatch.setattr(refresh_tier, "get_conn", lambda: conn)
    monkeypatch.setattr(refresh_tier, "ensure_refresh_tier_schema", lambda: None)
    monkeypatch.setattr(refresh_tier, "_table_exists", lambda table: table == "vkpi_kol_refresh_tier")

    result = refresh_tier.record_kol_search(42)

    insert_sql = next(sql for sql in conn.statements if "ON CONFLICT(kol_pool_id)" in sql)
    assert "tier_reason_json=CASE WHEN vkpi_kol_refresh_tier.tier='cold'" in insert_sql
    assert result["tier"] == "hot"
    assert result["tier_reason"] == "viltrox_mention_60d"
    assert conn.commits == 1


def test_mark_kol_refreshed_commits(monkeypatch) -> None:
    class Conn:
        def __init__(self) -> None:
            self.executed: list[tuple[str, tuple[object, ...]]] = []
            self.commits = 0

        def execute(self, sql: str, params: tuple[object, ...]):
            self.executed.append((sql, params))
            return None

        def commit(self) -> None:
            self.commits += 1

    conn = Conn()
    monkeypatch.setattr(refresh_tier, "_table_exists", lambda table: table == "vkpi_kol_refresh_tier")
    monkeypatch.setattr(refresh_tier, "get_conn", lambda: conn)

    refresh_tier.mark_kol_refreshed(1554, status="synced")

    assert conn.executed
    assert conn.executed[0][1][-1] == 1554
    assert conn.commits == 1

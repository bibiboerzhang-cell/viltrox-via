"""富化写端的国家来源契约(2026-09-02 T 车道实测:在线新发现入池 ``country=''``)。

根因之一是富化 ``_write_enriched_item`` 压根不写 ``country`` 列——平台自报了也落不下。
本测试钉:
* 国家只认平台字段(YT ``snippet.country`` / TT ``authorMeta.region`` / IG 商家地址),
  取不到 → 留空(SQL 与旧版逐字相同,绝不用查询 market 冒充);
* 写时来源盖进 raw_platform_data(``country_source=platform_profile``);
* 库里已有的人工 / 历史国家绝不被抓取覆盖;只有「本来就是平台自报」的才刷新。
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.domains.kol import pool_enrich
from app.domains.kol import pool_enrich_country as pc


# ── 纯函数 ─────────────────────────────────────────────────────────────────


def test_country_code_aliases_and_honest_blank() -> None:
    assert pc.country_code("uk") == "GB"
    assert pc.country_code("United States") == "US"
    assert pc.country_code("de") == "DE"
    assert pc.country_code("global") == ""
    assert pc.country_code("worldwide") == ""
    assert pc.country_code("") == ""
    assert pc.country_code("Somewhere over the rainbow") == ""


def test_platform_country_hint_reads_each_platform_field() -> None:
    assert pc.platform_country_hint({"snippet": {"country": "DE"}}) == "DE"
    assert pc.platform_country_hint({"authorMeta": {"name": "x", "region": "GB"}}) == "GB"
    assert pc.platform_country_hint({"countryCode": "jp"}) == "JP"
    assert pc.platform_country_hint(
        {"businessAddress": json.dumps({"city_name": "Austin", "country_code": "US"})},
    ) == "US"
    assert pc.platform_country_hint({"business_address": {"country": "France"}}) == "FR"
    assert pc.platform_country_hint({"snippet": {"title": "no country"}}) == ""
    assert pc.platform_country_hint({"businessAddress": "{broken json"}) == ""
    assert pc.platform_country_hint(None) == ""


def test_derive_profile_country_from_enrich_raw_data_shapes() -> None:
    youtube_raw = {"profile": {"items": [{"id": "UC1", "snippet": {"country": "DE"}}]}}
    assert pc.derive_profile_country(youtube_raw, "youtube") == "DE"
    tiktok_raw = {"profile": {"items": [{"authorMeta": {"name": "tt", "region": "KR"}}]}}
    assert pc.derive_profile_country(tiktok_raw, "tiktok") == "KR"
    assert pc.derive_profile_country(json.dumps(youtube_raw)) == "DE"
    assert pc.derive_profile_country({"profile": {"items": [{"id": "UC1"}]}}) == ""
    assert pc.derive_profile_country("", "youtube") == ""


@pytest.mark.parametrize(
    ("existing_country", "existing_raw", "derived", "expected"),
    [
        ("", None, "", ""),                                         # 平台没给 → 不写
        ("", None, "DE", "DE"),                                     # 库里为空 → 写
        ("US", None, "DE", ""),                                     # 人工/历史值 → 不覆盖
        ("US", json.dumps({"country_source": "manual"}), "DE", ""),
        ("US", {"country_source": "platform_profile"}, "DE", "DE"),  # 本来就是平台自报 → 刷新
        ("", None, "global", ""),                                   # 全球词不是国家
    ],
)
def test_country_write_decision(existing_country: Any, existing_raw: Any, derived: Any, expected: str) -> None:
    assert pc.country_write_decision(
        existing_country=existing_country, existing_raw=existing_raw, derived=derived,
    ) == expected


def test_resolve_enrich_country_stamps_provenance_only_when_writing() -> None:
    raw: dict[str, Any] = {"profile": {"items": [{"snippet": {"country": "DE"}}]}}
    assert pc.resolve_enrich_country({"country": ""}, "youtube", raw) == "DE"
    assert raw["country_source"] == pc.COUNTRY_SOURCE_PLATFORM
    assert raw["declared_country"]["value"] == "DE"

    untouched: dict[str, Any] = {"profile": {"items": [{"id": "UC1"}]}}
    assert pc.resolve_enrich_country({"country": ""}, "youtube", untouched) == ""
    assert "country_source" not in untouched


# ── 富化写端 ───────────────────────────────────────────────────────────────


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


def _run_enrich(
    monkeypatch: pytest.MonkeyPatch, *, item: dict[str, Any], profile: dict[str, Any],
) -> list[tuple[str, tuple]]:
    """跑 enrich_item 并抓主 UPDATE 的 (sql, params);其余步骤全部打桩(零库零网)。"""
    updates: list[tuple[str, tuple]] = []

    class Conn:
        def execute(self, sql: str, params: Any = None) -> _Rows:
            if sql.lstrip().startswith("SELECT *"):
                return _Rows([dict(item)])
            updates.append((sql, tuple(params or ())))
            return _Rows([])

        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

    class Crawler:
        configured = True

        def crawl_channel_profile(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"provider_status": "ready", "sync_status": "synced", "items": [profile]}

        def crawl_channel_videos(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"items": [{"id": "v1", "viewCount": 100}]}

    scoring = SimpleNamespace(score=88, strengths=["fit"], concerns=[], breakdown={"fit": 88})
    monkeypatch.setattr(pool_enrich, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(pool_enrich, "get_conn", lambda: Conn())
    monkeypatch.setattr(pool_enrich, "get_crawler", lambda _platform: Crawler())
    monkeypatch.setattr(pool_enrich, "calculate_kpis", lambda _raw: {"followers": 1200, "posts": 1})
    monkeypatch.setattr(
        pool_enrich.ScoringRegistry, "get", lambda _name: SimpleNamespace(score=lambda *_a, **_k: scoring),
    )
    monkeypatch.setattr(pool_enrich, "_stamp_enrich_avatar", lambda *_a, **_k: None)
    monkeypatch.setattr(pool_enrich, "apply_raw_fields", lambda *_a, **_k: None)
    monkeypatch.setattr(pool_enrich, "_derive_enrich_topic", lambda *_a, **_k: None)
    monkeypatch.setattr(pool_enrich, "_regate_enriched_item", lambda *_a, **_k: None)
    monkeypatch.setattr(pool_enrich, "_clear_kol_pool_read_cache", lambda: None)

    pool_enrich.enrich_item(int(item["id"]), max_posts=3)
    return updates


_ITEM = {"id": 7, "platform": "youtube", "handle": "creator", "profile_url": "https://youtube.com/@creator", "country": ""}


def test_enrich_writes_platform_country_with_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    updates = _run_enrich(
        monkeypatch, item=_ITEM, profile={"id": "UC7", "title": "Creator", "snippet": {"country": "DE"}},
    )
    assert len(updates) == 1
    sql, params = updates[0]
    assert "country=?" in sql
    assert sql.rstrip().endswith("WHERE id=?")
    assert params[-1] == 7
    assert params[-2] == "DE"
    raw_written = json.loads(params[14])
    assert raw_written["country_source"] == pc.COUNTRY_SOURCE_PLATFORM
    assert raw_written["declared_country"]["value"] == "DE"
    assert raw_written["declared_country"]["source"] == pc.COUNTRY_SOURCE_PLATFORM


def test_enrich_leaves_country_blank_when_platform_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    updates = _run_enrich(monkeypatch, item=_ITEM, profile={"id": "UC7", "title": "Creator"})
    sql, params = updates[0]
    assert "country" not in sql
    assert len(params) == 18
    assert "country_source" not in json.loads(params[14])


def test_enrich_never_overwrites_manual_country(monkeypatch: pytest.MonkeyPatch) -> None:
    manual = {**_ITEM, "country": "US", "raw_platform_data": json.dumps({"source": "import"})}
    updates = _run_enrich(
        monkeypatch, item=manual, profile={"id": "UC7", "title": "Creator", "snippet": {"country": "DE"}},
    )
    sql, params = updates[0]
    assert "country" not in sql
    assert "DE" not in params


def test_enrich_refreshes_country_that_was_platform_reported_before(monkeypatch: pytest.MonkeyPatch) -> None:
    platform_owned = {
        **_ITEM, "country": "US",
        "raw_platform_data": json.dumps({"country_source": pc.COUNTRY_SOURCE_PLATFORM}),
    }
    updates = _run_enrich(
        monkeypatch, item=platform_owned, profile={"id": "UC7", "title": "Creator", "snippet": {"country": "DE"}},
    )
    sql, params = updates[0]
    assert "country=?" in sql
    assert params[-2] == "DE"


def test_enrich_tiktok_region_lands_in_country_column(monkeypatch: pytest.MonkeyPatch) -> None:
    tiktok_item = {**_ITEM, "platform": "tiktok", "profile_url": "https://www.tiktok.com/@creator"}
    updates = _run_enrich(
        monkeypatch, item=tiktok_item,
        profile={"authorMeta": {"name": "creator", "region": "GB", "fans": 1200}, "text": "lens test"},
    )
    sql, params = updates[0]
    assert "country=?" in sql
    assert params[-2] == "GB"

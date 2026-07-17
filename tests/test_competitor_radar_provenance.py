from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.domains.market import competitor_radar


class _Result:
    def __init__(self, row: dict[str, Any] | None = None):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row: dict[str, Any] | None = None):
        self.row = row
        self.insert_params: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None):
        if "SELECT snapshot_date" in sql:
            return _Result(self.row)
        if "INSERT INTO vkpi_competitor_radar" in sql:
            self.insert_params = params
        return _Result()

    def commit(self) -> None:
        return None


def test_public_url_gets_external_provenance() -> None:
    result = competitor_radar.normalize_content_provenance(
        {
            "source_url": "https://petapixel.com/2026/07/sigma-lens-review",
            "source_platform": "website",
            "published_at": "2026-07-09T12:00:00Z",
        },
        observed_at="2026-07-10T09:00:00Z",
    )

    assert result == {
        "content_origin": "external",
        "source_platform": "website",
        "source_url": "https://petapixel.com/2026/07/sigma-lens-review",
        "published_at": "2026-07-09T12:00:00Z",
        "observed_at": "2026-07-10T09:00:00Z",
    }


@pytest.mark.parametrize(
    "source_url",
    [
        "https://viltrox.com/products/example",
        "https://www.instagram.com/viltrox.official/",
        "https://www.youtube.com/@viltroxofficial/videos",
        "https://www.reddit.com/r/VILTROX_GLOBAL/comments/example",
    ],
)
def test_viltrox_owned_urls_override_external_claim(source_url: str) -> None:
    result = competitor_radar.normalize_content_provenance(
        {"content_origin": "external", "source_url": source_url}
    )

    assert result["content_origin"] == "owned"


def test_third_party_viltrox_article_is_not_mistaken_for_owned() -> None:
    result = competitor_radar.normalize_content_provenance(
        {"source_url": "https://petapixel.com/reviews/viltrox-35mm-review"}
    )

    assert result["content_origin"] == "external"


def test_explicit_or_missing_unknown_provenance_stays_unknown() -> None:
    explicit = competitor_radar.normalize_content_provenance(
        {
            "content_origin": "unknown",
            "source_url": "https://petapixel.com/unverified-item",
        }
    )
    missing = competitor_radar.normalize_content_provenance({})
    known_platform = competitor_radar.normalize_signal_item({"source_platform": "reddit"})

    assert explicit["content_origin"] == "unknown"
    assert missing == {
        "content_origin": "unknown",
        "source_platform": "unknown",
        "source_url": "",
        "published_at": "",
        "observed_at": "",
    }
    assert known_platform["content_origin"] == "unknown"
    assert known_platform["source_platform"] == "reddit"


def test_viltrox_social_post_without_account_proof_stays_unknown() -> None:
    ambiguous = competitor_radar.normalize_content_provenance(
        {
            "brand": "Viltrox",
            "content_origin": "external",
            "source_platform": "youtube",
            "source_url": "https://www.youtube.com/watch?v=unresolved",
        }
    )
    title_only = competitor_radar.normalize_content_provenance(
        {
            "content_origin": "external",
            "source_platform": "youtube",
            "source_url": "https://www.youtube.com/watch?v=unresolved-title",
            "title": "Viltrox official launch video",
        }
    )
    third_party = competitor_radar.normalize_content_provenance(
        {
            "brand": "Viltrox",
            "source_platform": "youtube",
            "source_url": "https://www.youtube.com/watch?v=third-party",
            "channel_name": "PetaPixel",
        }
    )

    assert ambiguous["content_origin"] == "unknown"
    assert title_only["content_origin"] == "unknown"
    assert third_party["content_origin"] == "external"


def test_generate_grounded_three_tuple_adds_provenance(monkeypatch) -> None:
    conn = _Conn()
    monkeypatch.setattr(competitor_radar, "get_conn", lambda: conn)
    monkeypatch.setattr(competitor_radar.budget_guard, "check_budget", lambda *_args: True)
    monkeypatch.setattr(competitor_radar.budget_guard, "record_cost", lambda **_kwargs: None)
    monkeypatch.setattr(
        competitor_radar,
        "_generate",
        lambda _prompt: (
            json.dumps(
                {
                    "items": [
                        {
                            "signal_type": "competitor",
                            "brand": "Sigma",
                            "title": "Legacy radar item",
                            "summary": "Old shape remains readable",
                            "impact": "Opportunity",
                            "content_origin": "external",
                            "source_platform": "website",
                            "source_url": "https://petapixel.com/sigma-grounded",
                            "published_at": "2026-07-09T12:00:00Z",
                        }
                    ]
                }
            ),
            "legacy:model",
            [{"title": "Sigma grounded report", "url": "https://petapixel.com/sigma-grounded"}],
        ),
    )

    result = competitor_radar.generate_competitor_radar()

    assert result["status"] == "ok"
    assert result["result_status"] == "ready"
    assert result["items"] == 1
    assert conn.insert_params is not None
    payload = json.loads(str(conn.insert_params[0]))
    item = payload["items"][0]
    assert item["brand"] == "Sigma"
    assert item["title"] == "Legacy radar item"
    assert item["content_origin"] == "external"
    assert item["source_platform"] == "website"
    assert item["source_url"] == "https://petapixel.com/sigma-grounded"
    assert item["published_at"] == "2026-07-09T12:00:00Z"
    assert item["observed_at"] == payload["generated_at"]


def test_generate_matches_cjk_parenthetical_brand_to_grounding(monkeypatch) -> None:
    """「Meike (美科)」式中英混写品牌必须能命中英文接地源标题(2026-07-17
    线上两连发 item_source_not_grounded 的回归钉)。"""
    conn = _Conn()
    monkeypatch.setattr(competitor_radar, "get_conn", lambda: conn)
    monkeypatch.setattr(competitor_radar.budget_guard, "check_budget", lambda *_args: True)
    monkeypatch.setattr(competitor_radar.budget_guard, "record_cost", lambda **_kwargs: None)
    monkeypatch.setattr(
        competitor_radar,
        "_generate",
        lambda _prompt: (
            json.dumps(
                {
                    "items": [
                        {
                            "signal_type": "competitor",
                            "brand": "Meike (美科)",
                            "title": "Meike 发布 85mm f/1.4 II",
                            "summary": "升级版人像定焦开售",
                            "impact": "同级价格压力",
                            "content_origin": "external",
                            "source_platform": "website",
                            # 线上真实形态:条目自带 URL 但与引文 URL 不同,只能靠品牌命中接地。
                            "source_url": "https://petapixel.com/meike-85-announcement",
                            "published_at": "2026-07-16T12:00:00Z",
                        }
                    ]
                }
            ),
            "gemini:test+google_search",
            [{"title": "Meike launches 85mm f/1.4 II lens", "url": "https://petapixel.com/meike-85"}],
        ),
    )

    result = competitor_radar.generate_competitor_radar()

    assert result["status"] == "ok"
    assert result["result_status"] == "ready"
    assert conn.insert_params is not None
    payload = json.loads(str(conn.insert_params[0]))
    item = payload["items"][0]
    assert item["brand"] == "Meike (美科)"
    # 接地源要真的挂到条目上,前端「可回源」计数才有 URL 可点。
    attached_urls = {str(s.get("source_url") or "") for s in item["sources"]}
    assert "https://petapixel.com/meike-85" in attached_urls


def test_generate_resolves_vertexaisearch_redirect_shells(monkeypatch) -> None:
    """接地壳 URL(域名级 title + vertexaisearch 重定向)解析成真实文章 URL 后,
    品牌经真 URL slug 命中、落库源=可回跳真链(2026-07-17 线上恒败根因回归钉)。"""
    conn = _Conn()
    monkeypatch.setattr(competitor_radar, "get_conn", lambda: conn)
    monkeypatch.setattr(competitor_radar.budget_guard, "check_budget", lambda *_args: True)
    monkeypatch.setattr(competitor_radar.budget_guard, "record_cost", lambda **_kwargs: None)
    monkeypatch.setattr(
        competitor_radar,
        "_fetch_final_url",
        lambda _url, timeout_seconds=6.0: "https://petapixel.com/2026/07/16/meike-85mm-f14-ii-launch",
    )
    monkeypatch.setattr(
        competitor_radar,
        "_generate",
        lambda _prompt: (
            json.dumps(
                {
                    "items": [
                        {
                            "signal_type": "competitor",
                            "brand": "Meike (美科)",
                            "title": "Meike 发布 85mm f/1.4 II",
                            "summary": "升级版人像定焦开售",
                            "impact": "同级价格压力",
                            "content_origin": "external",
                            "source_platform": "website",
                            "source_url": "https://petapixel.com/some-other-page",
                            "published_at": "2026-07-16T12:00:00Z",
                        }
                    ]
                }
            ),
            "gemini:test+google_search",
            # 线上真实形态:title 只有域名,URL 是重定向壳。
            [{"title": "petapixel.com", "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQF"}],
        ),
    )

    result = competitor_radar.generate_competitor_radar()

    assert result["status"] == "ok"
    assert result["result_status"] == "ready"
    assert conn.insert_params is not None
    payload = json.loads(str(conn.insert_params[0]))
    top_urls = {str(s.get("source_url") or "") for s in payload["sources"]}
    assert "https://petapixel.com/2026/07/16/meike-85mm-f14-ii-launch" in top_urls
    item_urls = {str(s.get("source_url") or "") for s in payload["items"][0]["sources"]}
    assert "https://petapixel.com/2026/07/16/meike-85mm-f14-ii-launch" in item_urls


def test_generate_persists_grounded_subset_and_drops_unmatched(monkeypatch) -> None:
    """引文只覆盖部分品牌时:落库已接地子集,丢弃未接地条目(告别整批连坐)。"""
    conn = _Conn()
    monkeypatch.setattr(competitor_radar, "get_conn", lambda: conn)
    monkeypatch.setattr(competitor_radar.budget_guard, "check_budget", lambda *_args: True)
    monkeypatch.setattr(competitor_radar.budget_guard, "record_cost", lambda **_kwargs: None)
    monkeypatch.setattr(
        competitor_radar,
        "_generate",
        lambda _prompt: (
            json.dumps(
                {
                    "items": [
                        {
                            "signal_type": "competitor",
                            "brand": "Tamron",
                            "title": "Tamron 12-20mm F2.8 发布",
                            "summary": "超广角变焦开卖",
                            "impact": "定价压力",
                            "content_origin": "external",
                            "source_platform": "website",
                            "source_url": "https://fstoppers.com/tamron-item-page",
                            "published_at": "2026-07-15T12:00:00Z",
                        },
                        {
                            "signal_type": "competitor",
                            "brand": "Canon (佳能)",
                            "title": "Canon 新机传闻",
                            "summary": "无引文覆盖的传闻",
                            "impact": "未知",
                            "content_origin": "external",
                            "source_platform": "website",
                            "source_url": "https://example.com/canon-rumor",
                            "published_at": "2026-07-16T12:00:00Z",
                        },
                    ]
                }
            ),
            "gemini:test+google_search",
            [{"title": "fstoppers.com", "url": "https://fstoppers.com/gear/tamron-12-20mm-f28-ultra-wide"}],
        ),
    )

    result = competitor_radar.generate_competitor_radar()

    assert result["status"] == "ok"
    assert conn.insert_params is not None
    payload = json.loads(str(conn.insert_params[0]))
    brands = [item["brand"] for item in payload["items"]]
    assert brands == ["Tamron"]
    assert payload["dropped_ungrounded"] == 1


def test_generate_keeps_shell_and_stays_degraded_when_resolution_fails(monkeypatch) -> None:
    """解析失败必须保壳 + 照旧 degraded 拒绝落库(fail-closed,不因解析器放宽闸)。"""
    conn = _Conn()
    monkeypatch.setattr(competitor_radar, "get_conn", lambda: conn)
    monkeypatch.setattr(competitor_radar.budget_guard, "check_budget", lambda *_args: True)
    monkeypatch.setattr(competitor_radar.budget_guard, "record_cost", lambda **_kwargs: None)

    def _boom(_url, timeout_seconds=6.0):
        raise RuntimeError("network down")

    monkeypatch.setattr(competitor_radar, "_fetch_final_url", _boom)
    monkeypatch.setattr(
        competitor_radar,
        "_generate",
        lambda _prompt: (
            json.dumps(
                {
                    "items": [
                        {
                            "signal_type": "competitor",
                            "brand": "Meike (美科)",
                            "title": "Meike 发布 85mm f/1.4 II",
                            "summary": "升级版人像定焦开售",
                            "impact": "同级价格压力",
                            "content_origin": "external",
                            "source_platform": "website",
                            "source_url": "https://petapixel.com/some-other-page",
                            "published_at": "2026-07-16T12:00:00Z",
                        }
                    ]
                }
            ),
            "gemini:test+google_search",
            [{"title": "petapixel.com", "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQF"}],
        ),
    )

    result = competitor_radar.generate_competitor_radar()

    assert result["status"] == "degraded"
    assert result["reason"] == "item_source_not_grounded"
    assert conn.insert_params is None


def test_generate_does_not_persist_legacy_ungrounded_result(monkeypatch) -> None:
    conn = _Conn()
    monkeypatch.setattr(competitor_radar, "get_conn", lambda: conn)
    monkeypatch.setattr(competitor_radar.budget_guard, "check_budget", lambda *_args: True)
    monkeypatch.setattr(competitor_radar.budget_guard, "record_cost", lambda **_kwargs: None)
    monkeypatch.setattr(
        competitor_radar,
        "_generate",
        lambda _prompt: (
            json.dumps(
                {
                    "items": [
                        {
                            "signal_type": "competitor",
                            "brand": "Sigma",
                            "title": "Unverified item",
                            "summary": "Complete item without retained grounding",
                            "impact": "Needs verification",
                            "content_origin": "external",
                            "source_platform": "website",
                            "source_url": "https://example.com/unverified",
                            "published_at": "2026-07-09T12:00:00Z",
                        }
                    ]
                }
            ),
            "legacy:model",
        ),
    )

    result = competitor_radar.generate_competitor_radar()

    assert result["status"] == "ungrounded"
    assert result["result_status"] == "degraded"
    assert result["reason"] == "no_grounded_citations"
    assert conn.insert_params is None


def test_get_preserves_old_fields_and_orders_external_before_unknown_and_owned(monkeypatch) -> None:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    row = {
        "snapshot_date": generated_at[:10],
        "model": "test:model",
        "created_at": generated_at,
        "content_json": json.dumps(
            {
                "generated_at": generated_at,
                "items": [
                    {
                        "brand": "Viltrox",
                        "title": "Owned post",
                        "summary": "First-party post",
                        "impact": "Context only",
                        "content_origin": "external",
                        "source_url": "https://www.youtube.com/@viltroxofficial/videos",
                    },
                    {
                        "brand": "Unknown",
                        "title": "Legacy unknown",
                        "summary": "No source retained",
                        "impact": "Needs review",
                    },
                    {
                        "brand": "Sigma",
                        "title": "External article",
                        "summary": "Third-party coverage",
                        "impact": "Opportunity",
                        "source_url": "https://petapixel.com/sigma-example",
                        "source_platform": "website",
                        "published_at": "2026-07-09T12:00:00Z",
                    },
                ],
                "sources": [],
            }
        ),
    }
    conn = _Conn(row)
    monkeypatch.setattr(competitor_radar, "get_conn", lambda: conn)
    monkeypatch.setattr(competitor_radar, "_market_sources", lambda *_args, **_kwargs: [])

    result = competitor_radar.get_competitor_radar()

    assert result["available"] is True
    assert result["status"] == "degraded"
    assert result["is_ready"] is False
    items = result["content"]["items"]
    assert [item["title"] for item in items] == ["External article", "Legacy unknown", "Owned post"]
    assert [item["content_origin"] for item in items] == ["external", "unknown", "owned"]
    assert items[0]["sources"][0]["url"] == "https://petapixel.com/sigma-example"
    assert items[0]["sources"][0]["source_url"] == "https://petapixel.com/sigma-example"
    assert items[1]["source_url"] == ""
    assert items[2]["source_url"].startswith("https://www.youtube.com/@viltroxofficial")
    for item in items:
        assert {"brand", "title", "summary", "impact"}.issubset(item)
        assert {
            "content_origin",
            "source_platform",
            "source_url",
            "published_at",
            "observed_at",
        }.issubset(item)


def test_generate_rejects_items_string_without_character_coercion(monkeypatch) -> None:
    conn = _Conn()
    monkeypatch.setattr(competitor_radar, "get_conn", lambda: conn)
    monkeypatch.setattr(competitor_radar.budget_guard, "check_budget", lambda *_args: True)
    monkeypatch.setattr(competitor_radar.budget_guard, "record_cost", lambda **_kwargs: None)
    monkeypatch.setattr(
        competitor_radar,
        "_generate",
        lambda _prompt: (
            json.dumps({"items": "not-a-list"}),
            "gemini:test+google_search",
            [{"url": "https://example.com/source", "relation_type": "grounding"}],
        ),
    )

    result = competitor_radar.generate_competitor_radar()

    assert result["status"] == "invalid"
    assert result["reason"] == "invalid_result_contract"
    assert "items:expected_list" in result["validation_errors"]
    assert conn.insert_params is None


def test_generate_rejects_item_url_not_present_in_grounding(monkeypatch) -> None:
    conn = _Conn()
    monkeypatch.setattr(competitor_radar, "get_conn", lambda: conn)
    monkeypatch.setattr(competitor_radar.budget_guard, "check_budget", lambda *_args: True)
    monkeypatch.setattr(competitor_radar.budget_guard, "record_cost", lambda **_kwargs: None)
    monkeypatch.setattr(
        competitor_radar,
        "_generate",
        lambda _prompt: (
            json.dumps(
                {
                    "items": [
                        {
                            "signal_type": "competitor",
                            "brand": "Sigma",
                            "title": "Grounding mismatch",
                            "summary": "The item URL is not among provider citations",
                            "impact": "Must not be persisted",
                            "content_origin": "external",
                            "source_platform": "website",
                            "source_url": "https://example.com/item",
                            "published_at": "2026-07-09T12:00:00Z",
                        }
                    ]
                }
            ),
            "gemini:test+google_search",
            [{"url": "https://example.com/different", "relation_type": "grounding"}],
        ),
    )

    result = competitor_radar.generate_competitor_radar()

    assert result["status"] == "degraded"
    assert result["reason"] == "item_source_not_grounded"
    assert conn.insert_params is None


def test_generate_rejects_malformed_source_url(monkeypatch) -> None:
    conn = _Conn()
    monkeypatch.setattr(competitor_radar, "get_conn", lambda: conn)
    monkeypatch.setattr(competitor_radar.budget_guard, "check_budget", lambda *_args: True)
    monkeypatch.setattr(competitor_radar.budget_guard, "record_cost", lambda **_kwargs: None)
    monkeypatch.setattr(
        competitor_radar,
        "_generate",
        lambda _prompt: (
            json.dumps(
                {
                    "items": [
                        {
                            "signal_type": "competitor",
                            "brand": "Sigma",
                            "title": "Malformed URL",
                            "summary": "Invalid source URL",
                            "impact": "Must not be persisted",
                            "content_origin": "external",
                            "source_platform": "website",
                            "source_url": "javascript:alert(1)",
                        }
                    ]
                }
            ),
            "gemini:test+google_search",
            [{"url": "https://example.com/source", "relation_type": "grounding"}],
        ),
    )

    result = competitor_radar.generate_competitor_radar()

    assert result["status"] == "invalid"
    assert any("invalid_public_http_url" in error for error in result["validation_errors"])
    assert conn.insert_params is None


def test_stale_complete_radar_cannot_report_ready(monkeypatch) -> None:
    generated_at = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    source_url = "https://example.com/sigma"
    row = {
        "snapshot_date": generated_at[:10],
        "model": "gemini:test+google_search",
        "created_at": generated_at,
        "content_json": json.dumps(
            {
                "generated_at": generated_at,
                "items": [
                    {
                        "signal_type": "competitor",
                        "brand": "Sigma",
                        "title": "Complete stale item",
                        "summary": "The item satisfies the structural contract",
                        "impact": "Recheck before acting",
                        "content_origin": "external",
                        "source_platform": "website",
                        "source_url": source_url,
                        "published_at": generated_at,
                    }
                ],
                "sources": [
                    {
                        "title": "Sigma source",
                        "url": source_url,
                        "provider": "google_search",
                        "relation_type": "grounding",
                    }
                ],
            }
        ),
    }
    monkeypatch.setattr(competitor_radar, "get_conn", lambda: _Conn(row))
    monkeypatch.setattr(competitor_radar, "_market_sources", lambda *_args, **_kwargs: [])

    result = competitor_radar.get_competitor_radar()

    assert result["available"] is True
    assert result["freshness_status"] == "stale"
    assert result["status"] == "degraded"
    assert result["is_ready"] is False

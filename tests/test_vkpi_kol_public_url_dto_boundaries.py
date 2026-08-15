"""Public KOL DTOs keep identity pages and reject contact-route URLs."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.domains.kol import contact_system, natural_search, pool as kol_pool
from app.domains.kol import search_sessions, search_sessions_attach, search_sessions_serde
from app.domains.kol.pool_common import KOL_POOL_LIST_COLUMNS, mask_pool_item


CONTACT_ROUTE_URLS = (
    "https://wa.me/14155552671",
    "https://instagram.com/direct/t/1234567",
    "https://x.com/messages/compose",
    "https://facebook.com/messages/t/camera_creator",
    "https://discord.com/channels/123456789012345678/987654321098765432",
    "https://creator.example/12345678",
    "https://creator.example/profile?phone=123456789",
    "https://creator.example/profile#phone=12345678",
    "https://creator.example/profile/leak%2540ex.test",
    "https://creator.example/profile?email=leak%2540ex.test",
    "https://creator.example/profile/%252B123456789",
    "https://instagram.com/%2564irect/t/1234567",
    "https://instagram.com/%252564irect/t/1234567",
)
SAFE_PROFILE_INPUT = "https://youtube.com/@camera_creator#portfolio"
SAFE_PROFILE_URL = "https://youtube.com/@camera_creator"
SAFE_CHANNEL_INPUT = "https://instagram.com/camera_creator#portfolio"
SAFE_CHANNEL_URL = "https://instagram.com/camera_creator"


class _Result:
    def __init__(
        self,
        *,
        row: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._row = row
        self._rows = rows or []

    def fetchone(self) -> dict[str, Any] | None:
        return self._row

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


def test_pool_mask_and_value_free_projection_gate_all_profile_channel_aliases() -> None:
    aliases = ("profile_url", "profileUrl", "channel_url", "channelUrl")
    for alias in aliases:
        for contact_url in CONTACT_ROUTE_URLS:
            projected = mask_pool_item({"id": 7, "handle": "creator", alias: contact_url})
            assert projected[alias] == ""
            recursively_projected = contact_system.value_free_contact_projection(
                {"history": {alias: contact_url}}
            )
            assert recursively_projected["history"][alias] == ""

        safe = mask_pool_item({"id": 7, "handle": "creator", alias: SAFE_PROFILE_INPUT})
        assert safe[alias] == SAFE_PROFILE_URL
        recursively_safe = contact_system.value_free_contact_projection(
            {"history": {alias: SAFE_CHANNEL_INPUT}}
        )
        assert recursively_safe["history"][alias] == SAFE_CHANNEL_URL


def test_pool_list_dto_drops_contact_routes_and_keeps_public_profile(
    monkeypatch,
) -> None:
    rows = [
        {"id": index, "handle": f"creator{index}", "profile_url": url}
        for index, url in enumerate(CONTACT_ROUTE_URLS, start=1)
    ] + [{"id": 99, "handle": "safe", "profile_url": SAFE_PROFILE_INPUT}]

    class _ListConn:
        def execute(self, _sql: str, _params: tuple[Any, ...] = ()) -> _Result:
            return _Result(rows=rows)

    monkeypatch.setattr(kol_pool, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(kol_pool, "get_conn", lambda: _ListConn())
    monkeypatch.setattr(
        kol_pool,
        "_table_columns",
        lambda *_args: set(KOL_POOL_LIST_COLUMNS),
    )
    monkeypatch.setattr(kol_pool, "cache_get", lambda _key: None)
    monkeypatch.setattr(
        kol_pool,
        "_kol_pool_cache_store",
        lambda _key, payload: payload,
    )

    result = kol_pool.list_pool(limit=20)

    assert [item["profile_url"] for item in result["items"][:-1]] == [""] * len(
        CONTACT_ROUTE_URLS
    )
    assert result["items"][-1]["profile_url"] == SAFE_PROFILE_URL
    assert not any(url in str(result) for url in CONTACT_ROUTE_URLS)


def test_pool_detail_dto_drops_contact_routes_and_keeps_public_profile(
    monkeypatch,
) -> None:
    rows = {
        **{
            index: {"id": index, "handle": f"creator{index}", "profile_url": url}
            for index, url in enumerate(CONTACT_ROUTE_URLS, start=1)
        },
        99: {"id": 99, "handle": "safe", "profile_url": SAFE_PROFILE_INPUT},
    }

    class _DetailConn:
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            assert "SELECT * FROM vkpi_kol_pool WHERE id=" in " ".join(sql.split())
            return _Result(row=rows[int(params[0])])

    monkeypatch.setattr(kol_pool, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(kol_pool, "get_conn", lambda: _DetailConn())
    monkeypatch.setattr(kol_pool, "_v6_breakdown_for_item", lambda _item: {})
    monkeypatch.setattr(kol_pool, "_video_evidence_for_kol", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        contact_system,
        "contact_summary",
        lambda *_args, **_kwargs: {
            "status": "empty",
            "has_contact": False,
            "known_contact_count": 0,
            "verified_contact_count": 0,
            "channel_types": [],
            "verified_channel_types": [],
            "actionability": "requires_reveal",
            "reveal_required": True,
        },
    )

    for kol_id in range(1, len(CONTACT_ROUTE_URLS) + 1):
        detail = kol_pool.get_item(kol_id)
        assert detail["item"]["profile_url"] == ""
        assert CONTACT_ROUTE_URLS[kol_id - 1] not in str(detail)
    assert kol_pool.get_item(99)["item"]["profile_url"] == SAFE_PROFILE_URL


def test_natural_search_dto_gates_list_and_history_channel_aliases(
    monkeypatch,
) -> None:
    list_rows = [
        {
            "id": index,
            "platform": "youtube",
            "handle": f"creator{index}",
            "channel_url" if index % 2 else "channelUrl": url,
        }
        for index, url in enumerate(CONTACT_ROUTE_URLS, start=1)
    ]
    list_rows.append(
        {
            "id": 90,
            "platform": "instagram",
            "handle": "safe_list",
            "channel_url": SAFE_CHANNEL_INPUT,
        }
    )
    history_rows = [
        {
            "id": "pool:91",
            "platform": "youtube",
            "handle": "history_route",
            "channelUrl": "https://discord.com/channels/1/2",
        },
        {
            "id": "pool:92",
            "platform": "youtube",
            "handle": "history_safe",
            "channelUrl": SAFE_PROFILE_INPUT,
        },
    ]
    monkeypatch.setattr(
        natural_search,
        "list_kols",
        lambda **_kwargs: {"kols": list_rows},
    )
    monkeypatch.setattr(
        natural_search.history_match,
        "search_pool_for_natural",
        lambda *_args, **_kwargs: history_rows,
    )

    result = natural_search._natural_search_payload(
        {"query": "找达人", "limit": 30}, staff={"id": 7}
    )

    by_handle = {item["handle"]: item for item in result["items"]}
    for index in range(1, len(CONTACT_ROUTE_URLS) + 1):
        alias = "channel_url" if index % 2 else "channelUrl"
        assert by_handle[f"creator{index}"][alias] == ""
    assert by_handle["safe_list"]["channel_url"] == SAFE_CHANNEL_URL
    assert by_handle["history_route"]["channelUrl"] == ""
    assert by_handle["history_safe"]["channelUrl"] == SAFE_PROFILE_URL
    assert not any(url in str(result) for url in CONTACT_ROUTE_URLS)


def test_session_attach_write_side_projects_existing_new_and_recall_urls(
    monkeypatch,
) -> None:
    captures: list[list[dict[str, Any]]] = []

    def record_items(
        session_id: int,
        items: list[dict[str, Any]],
        *,
        status: str,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        captures.append(items)
        return {
            "id": session_id,
            "items": items,
            "status": status,
            "result_summary": summary,
        }

    monkeypatch.setattr(search_sessions, "record_items", record_items)
    monkeypatch.setattr(
        search_sessions,
        "get_session",
        lambda *_args, **_kwargs: {"result_summary": {}},
    )

    search_sessions_attach.attach_new_discovery_result(
        44,
        {
            "status": "ready",
            "platforms": ["instagram"],
            "existing_matches": [
                {
                    "handle": "route_existing",
                    "channel_url": "https://discord.com/channels/1/2",
                }
            ],
            "new_creators": [
                {
                    "handle": "safe_new",
                    "channel_url": SAFE_CHANNEL_INPUT,
                }
            ],
        },
    )
    discovery_items = captures[-1]
    assert discovery_items[0]["item_type"] == "existing_kol"
    assert discovery_items[0]["source_url"] == ""
    assert discovery_items[0]["payload"]["source_url"] == ""
    assert discovery_items[0]["payload"]["channel_url"] == ""
    assert discovery_items[1]["item_type"] == "new_creator"
    assert discovery_items[1]["source_url"] == SAFE_CHANNEL_URL
    assert discovery_items[1]["payload"]["source_url"] == SAFE_CHANNEL_URL
    assert discovery_items[1]["payload"]["channel_url"] == SAFE_CHANNEL_URL

    search_sessions_attach.attach_recall_result(
        44,
        {
            "items": [
                {
                    "kol_pool_id": 1,
                    "bucket": "creator",
                    "profile_url": "https://instagram.com/%2564irect/t/1234567",
                },
                {
                    "kol_pool_id": 2,
                    "bucket": "reviewer",
                    "profile_url": SAFE_PROFILE_INPUT,
                },
            ],
            "buckets": {},
        },
    )
    recall_items = captures[-1]
    assert recall_items[0]["item_type"] == "recall_candidate"
    assert recall_items[0]["source_url"] == ""
    assert recall_items[0]["payload"]["profile_url"] == ""
    assert recall_items[1]["source_url"] == SAFE_PROFILE_URL
    assert recall_items[1]["payload"]["profile_url"] == SAFE_PROFILE_URL


def test_session_historical_row_mapper_projects_identity_urls_but_keeps_video_url() -> None:
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    identity_aliases = (
        "source_url",
        "sourceUrl",
        "profile_url",
        "profileUrl",
        "channel_url",
        "channelUrl",
    )
    for item_type in ("existing_kol", "new_creator", "recall_candidate"):
        projected = search_sessions_serde._row_to_item(
            {
                "id": 1,
                "session_id": 44,
                "dedupe_key": item_type,
                "item_type": item_type,
                "status": "ready",
                "stage": "identified",
                "rank": 1,
                "source_url": "https://creator.example/profile#phone=12345678",
                "payload_json": {
                    alias: "https://instagram.com/%2564irect/t/1234567"
                    for alias in identity_aliases
                },
                "created_at": now,
                "updated_at": now,
            }
        )
        assert projected["source_url"] == ""
        assert all(projected["payload"][alias] == "" for alias in identity_aliases)

        safe = search_sessions_serde._row_to_item(
            {
                "id": 2,
                "session_id": 44,
                "dedupe_key": f"safe-{item_type}",
                "item_type": item_type,
                "status": "ready",
                "stage": "identified",
                "rank": 2,
                "source_url": SAFE_PROFILE_INPUT,
                "payload_json": {
                    alias: SAFE_CHANNEL_INPUT for alias in identity_aliases
                },
                "created_at": now,
                "updated_at": now,
            }
        )
        assert safe["source_url"] == SAFE_PROFILE_URL
        assert all(
            safe["payload"][alias] == SAFE_CHANNEL_URL for alias in identity_aliases
        )

    video_url = "https://youtube.com/watch?v=video123"
    video = search_sessions_serde._row_to_item(
        {
            "id": 3,
            "session_id": 44,
            "dedupe_key": "video",
            "item_type": "url_video",
            "status": "ready",
            "stage": "identified",
            "rank": 3,
            "source_url": video_url,
            "payload_json": {"source_url": video_url},
            "created_at": now,
            "updated_at": now,
        }
    )
    assert video["source_url"] == video_url
    assert video["payload"]["source_url"] == video_url


def test_search_session_history_recursively_gates_channel_aliases(
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    session_row = {
        "id": 44,
        "query_text": "camera creators",
        "query_type": "text_recall",
        "source": "smart_kol_input",
        "status": "ready",
        "created_by": 7,
        "input_payload_json": {},
        "result_summary_json": {},
        "approved_kol_ids": [],
        "created_at": now,
        "updated_at": now,
    }
    item_rows = [
        {
            "id": 1,
            "session_id": 44,
            "dedupe_key": "route",
            "item_type": "new_creator",
            "status": "ready",
            "stage": "identified",
            "rank": 1,
            "kol_pool_id": None,
            "source_url": "https://instagram.com/%2564irect/t/1234567",
            "payload_json": {
                "channel_url": "https://discord.com/channels/1/2",
                "history": {"channelUrl": "https://wa.me/14155552671"},
            },
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": 2,
            "session_id": 44,
            "dedupe_key": "safe",
            "item_type": "new_creator",
            "status": "ready",
            "stage": "identified",
            "rank": 2,
            "kol_pool_id": None,
            "source_url": SAFE_PROFILE_INPUT,
            "payload_json": {
                "channel_url": SAFE_PROFILE_INPUT,
                "history": {"channelUrl": SAFE_CHANNEL_INPUT},
            },
            "created_at": now,
            "updated_at": now,
        },
    ]

    class _SessionConn:
        def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> _Result:
            compact = " ".join(sql.split())
            if "FROM vkpi_kol_search_sessions WHERE" in compact:
                return _Result(row=session_row)
            if "FROM vkpi_kol_search_session_items" in compact:
                return _Result(rows=item_rows)
            raise AssertionError(f"unexpected SQL: {compact}")

    monkeypatch.setattr(search_sessions, "get_conn", lambda: _SessionConn())
    monkeypatch.setattr(
        search_sessions,
        "_refresh_enrichment_queue_states",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        search_sessions,
        "_attach_progress_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        search_sessions,
        "_apply_reach_display_gate",
        lambda _conn, items: (items, {"visible": len(items), "hidden": 0}),
    )

    result = search_sessions.get_session(44)

    route_payload = result["items"][0]["payload"]
    safe_payload = result["items"][1]["payload"]
    assert result["items"][0]["source_url"] == ""
    assert route_payload["channel_url"] == ""
    assert route_payload["history"]["channelUrl"] == ""
    assert result["items"][1]["source_url"] == SAFE_PROFILE_URL
    assert safe_payload["channel_url"] == SAFE_PROFILE_URL
    assert safe_payload["history"]["channelUrl"] == SAFE_CHANNEL_URL

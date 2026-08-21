from __future__ import annotations

import asyncio

from fastapi import Response

from app.api.routers import vkpi_kol_pool as router
from app.domains.kol.pool_common import CONTACT_VISIBILITY_MASKED


def test_list_and_single_get_never_enter_refresh_write_or_queue_helper(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    def list_stub(**kwargs):
        assert kwargs["contact_visibility"] == CONTACT_VISIBILITY_MASKED
        calls.append(("list", 7))
        return {"items": [{"id": 7, "handle": "@creator"}], "count": 1}

    def item_stub(kol_pool_id: int, *, contact_visibility: str):
        assert contact_visibility == CONTACT_VISIBILITY_MASKED
        calls.append(("item", int(kol_pool_id)))
        return {"item": {"id": int(kol_pool_id), "handle": "@creator"}}

    async def refresh_side_effect_bomb(*_args, **_kwargs):
        raise AssertionError("GET must not record search, write DB, or enqueue refresh")

    monkeypatch.setattr(router.kol_pool, "list_pool", list_stub)
    monkeypatch.setattr(router.kol_pool, "get_item", item_stub)
    monkeypatch.setattr(router, "_maybe_enqueue_refresh", refresh_side_effect_bomb)
    staff = {"id": 10, "role": "member", "permissions": {"vkpi": "read"}}

    for requested_refresh in (False, True):
        listed = asyncio.run(
            router.list_pool(
                request=object(),
                limit=10,
                offset=0,
                platform="",
                query="creator",
                country="",
                data_status="",
                sort_by="fit",
                enrichable=None,
                refresh_if_stale=requested_refresh,
                staff=staff,
            )
        )
        item = asyncio.run(
            router.get_item(
                request=object(),
                response=Response(),
                kol_pool_id=7,
                refresh_if_stale=requested_refresh,
                staff=staff,
            )
        )

        assert listed["items"][0]["id"] == 7
        assert "refresh" not in listed
        assert item["item"]["id"] == 7
        assert item["contact_projection_reason"] == "summary_only"
        assert "refresh" not in item
        assert "freshness" not in item

    assert calls == [("list", 7), ("item", 7), ("list", 7), ("item", 7)]

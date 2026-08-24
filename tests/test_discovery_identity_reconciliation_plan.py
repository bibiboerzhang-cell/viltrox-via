from __future__ import annotations

from app.domains.discovery import enroll, federation
from app.domains.kol.identity import canonical_creator_aliases
from app.domains.kol.identity_reconciliation_plan import (
    build_identity_reconciliation_plan,
)
from app.domains.kol import search_sessions, search_sessions_history
from app.domains.kol.search_sessions_history import (
    apply_discovery_account_display_gate,
)
from app.domains.kol.search_sessions_items import canonicalize_session_creator_items


UC_ONE = "UCjYD2qQxWn9tY5mN0AbCdEf"
UC_TWO = "UCkYD2qQxWn9tY5mN0AbCdEf"
UC_THREE = "UClYD2qQxWn9tY5mN0AbCdEf"


def test_unknown_or_content_urls_never_become_creator_url_aliases() -> None:
    shared_product = "https://www.amazon.com/dp/B0DP75SP1N?tag=campaign"
    first = canonical_creator_aliases(
        {"platform": "youtube", "handle": "alpha", "profile_url": shared_product}
    )
    second = canonical_creator_aliases(
        {"platform": "youtube", "handle": "beta", "profile_url": shared_product}
    )

    assert first == {"youtube:handle:alpha"}
    assert second == {"youtube:handle:beta"}
    assert first.isdisjoint(second)
    assert canonical_creator_aliases(
        {"platform": "youtube", "source_url": "https://youtube.com/watch?v=video-one"}
    ) == set()
    assert canonical_creator_aliases(
        {"platform": "youtube", "source_url": "https://youtu.be/video-two"}
    ) == set()
    assert canonical_creator_aliases(
        {"platform": "instagram", "source_url": "https://instagram.com/reel/reel-one"}
    ) == set()
    assert "website:url:https://creator.example/about" in canonical_creator_aliases(
        {"platform": "website", "profile_url": "https://creator.example/about?ref=campaign"}
    )


def test_different_youtube_watch_pages_do_not_fold_as_one_creator() -> None:
    folded = canonicalize_session_creator_items(
        [
            {
                "id": 1,
                "item_type": "new_creator",
                "dedupe_key": "video-one",
                "source_url": "https://youtube.com/watch?v=video-one",
                "payload": {"platform": "youtube", "channel_name": "Creator One"},
            },
            {
                "id": 2,
                "item_type": "new_creator",
                "dedupe_key": "video-two",
                "source_url": "https://youtube.com/watch?v=video-two",
                "payload": {"platform": "youtube", "channel_name": "Creator Two"},
            },
        ]
    )

    assert [item["id"] for item in folded] == [1, 2]


def test_content_external_ids_never_become_creator_native_aliases() -> None:
    ambiguous_video = canonical_creator_aliases(
        {
            "platform": "youtube",
            "external_id": "youtube-video-123",
            "video_id": "youtube-video-123",
            "source_url": "https://youtube.com/watch?v=youtube-video-123",
        }
    )
    typed_channel = canonical_creator_aliases(
        {
            "platform": "youtube",
            "external_id": UC_ONE,
            "external_id_kind": "channel_id",
        }
    )

    assert ambiguous_video == set()
    assert typed_channel == {f"youtube:id:{UC_ONE.casefold()}"}
    assert f"youtube:id:{UC_ONE.casefold()}" in canonical_creator_aliases(
        {"platform": "youtube", "channel_id": UC_ONE}
    )


def test_social_content_ids_merge_only_through_creator_handle_or_profile() -> None:
    for platform, handle, first_url, second_url in (
        (
            "tiktok",
            "@alicecamera",
            "https://tiktok.com/@alicecamera/video/111",
            "https://tiktok.com/@alicecamera/video/222",
        ),
        (
            "instagram",
            "alicecamera",
            "https://instagram.com/alicecamera/reel/111",
            "https://instagram.com/alicecamera/p/222",
        ),
    ):
        first = canonical_creator_aliases(
            {
                "platform": platform,
                "handle": handle,
                "external_id": "content-111",
                "source_url": first_url,
            }
        )
        second = canonical_creator_aliases(
            {
                "platform": platform,
                "handle": handle,
                "external_id": "content-222",
                "source_url": second_url,
            }
        )

        assert first.intersection(second)
        assert not any(":id:content-" in alias for alias in first | second)


def test_federation_projects_item_id_as_content_evidence_not_creator_id(monkeypatch) -> None:
    from app.services.intelligence import account_search_discovery

    async def fake_search(platform, _query, *, max_results):
        assert max_results >= 1
        if platform == "youtube":
            return {
                "status": "done",
                "items": [
                    {
                        "id": "video-youtube",
                        "video_id": "video-youtube",
                        "channel_id": UC_ONE,
                        "handle": "@creator",
                        "profile_url": "https://youtube.com/@creator",
                    }
                ],
            }
        return {
            "status": "done",
            "items": [
                {
                    "id": f"{platform}-content",
                    "handle": "@creator",
                    "profile_url": f"https://{platform}.com/@creator",
                }
            ],
        }

    monkeypatch.setattr(account_search_discovery, "search_platform_content", fake_search)
    monkeypatch.setattr(federation, "current_apify_execution_context", lambda: object())
    items, status = federation._apify_search("camera", 6)

    assert status == "ok"
    by_platform = {item["platform"]: item for item in items}
    assert by_platform["youtube"]["external_id"] == UC_ONE
    assert by_platform["youtube"]["external_id_kind"] == "channel_id"
    assert by_platform["youtube"]["content_id"] == "video-youtube"
    for platform in ("tiktok", "instagram"):
        assert by_platform[platform]["external_id"] == ""
        assert by_platform[platform]["external_id_kind"] == ""
        assert by_platform[platform]["content_id"] == f"{platform}-content"
        assert not any(":id:" in alias for alias in canonical_creator_aliases(by_platform[platform]))


def test_federated_enroll_never_uses_ambiguous_external_content_id_as_handle(
    monkeypatch,
) -> None:
    class _Repo:
        @staticmethod
        def exists():
            return True

        def _conn(self):
            raise AssertionError("content-only candidate must be skipped before DB access")

    monkeypatch.setattr(enroll, "KolPoolRepository", _Repo)
    result = enroll.enroll_candidates(
        [
            {
                "source": "provider",
                "platform": "tiktok",
                "external_id": "video-content-id",
                "content_id": "video-content-id",
            }
        ]
    )

    assert result["status"] == "ok"
    assert result["enrolled"] == 0
    assert result["skipped"] == 1
    assert result["enrolled_ids"] == []
    assert result["excluded_official"] == 0


def test_reconciliation_plan_keeps_conflicting_uc_bridge_manual_and_never_applies() -> None:
    pool_rows = [
        {
            "id": 1,
            "platform": "youtube",
            "handle": UC_ONE,
            "profile_url": f"https://youtube.com/channel/{UC_ONE}",
            "display_name": "Safe Creator",
            "avatar_url": "https://yt3.ggpht.com/safe",
            "bio": "Camera filmmaker",
            "raw_platform_data": {},
            "dashboard_account_type": "kol",
            "duplicate_of_id": None,
        },
        {
            "id": 2,
            "platform": "youtube",
            "handle": UC_TWO,
            "profile_url": f"https://youtube.com/channel/{UC_TWO}",
            "display_name": "Conflicted Creator",
            "avatar_url": "",
            "bio": "Camera filmmaker",
            "raw_platform_data": {},
            "dashboard_account_type": "kol",
            "duplicate_of_id": None,
        },
        {
            "id": 3,
            "platform": "tiktok",
            "handle": "viltrox.usa",
            "profile_url": "https://tiktok.com/@viltrox.usa",
            "display_name": "Viltrox USA",
            "avatar_url": "javascript:bad",
            "bio": "Official Viltrox account",
            "raw_platform_data": {},
            "dashboard_account_type": "kol",
            "duplicate_of_id": None,
        },
    ]
    session_items = [
        {
            "id": 11,
            "session_id": 100,
            "item_type": "existing_kol",
            "kol_pool_id": 1,
            "payload": {
                "platform": "youtube",
                "handle": "@safecreator",
                "channel_id": UC_ONE,
            },
        },
        {
            "id": 12,
            "session_id": 101,
            "item_type": "existing_kol",
            "kol_pool_id": 2,
            "payload": {
                "platform": "youtube",
                "handle": "@conflicted",
                "channel_id": UC_TWO,
            },
        },
        {
            "id": 13,
            "session_id": 102,
            "item_type": "existing_kol",
            "kol_pool_id": 2,
            "payload": {
                "platform": "youtube",
                "handle": "@conflicted",
                "channel_id": UC_THREE,
            },
        },
    ]

    plan = build_identity_reconciliation_plan(
        pool_rows=pool_rows,
        alias_rows=[],
        session_items=session_items,
        generated_at="2026-08-24T00:00:00Z",
    )

    alias_plan = plan["identity_alias_backfill"]
    assert plan["mode"] == "dry_run"
    assert plan["writes_performed"] == 0
    assert alias_plan["write_contract"] == {
        "physical_delete_allowed": False,
        "duplicate_pointer_write_allowed": False,
        "master_selection_allowed": False,
        "score_field_write_allowed": False,
        "apply_supported_by_this_planner": False,
    }
    assert alias_plan["safe_bridge_group_count"] == 1
    assert alias_plan["manual_bridge_group_count"] == 1
    assert alias_plan["manual_bridge_groups"][0]["kol_pool_id"] == 2
    assert alias_plan["manual_bridge_groups"][0]["review_reasons"] == [
        "multiple_native_ids"
    ]
    assert plan["official_isolation"]["pool_plan"][0]["id"] == 3
    assert plan["official_isolation"]["pool_plan"][0]["plan_action"] == (
        "propose_company_segment_and_discovery_quarantine"
    )
    assert plan["avatar_integrity"]["pool_visible_rows"]["invalid"] == 1


def test_reconciliation_plan_never_labels_social_system_routes_safe_to_backfill() -> None:
    pool_rows = [
        {
            "id": 1,
            "platform": "facebook",
            "handle": "story.php",
            "profile_url": "https://facebook.com/story.php?story_fbid=101&id=11",
            "dashboard_account_type": "kol",
            "duplicate_of_id": None,
        },
        {
            "id": 2,
            "platform": "youtube",
            "handle": "youtube",
            "profile_url": "https://youtube.com/",
            "dashboard_account_type": "kol",
            "duplicate_of_id": None,
        },
        {
            "id": 3,
            "platform": "instagram",
            "handle": "real.creator",
            "profile_url": "https://instagram.com/real.creator/",
            "dashboard_account_type": "kol",
            "duplicate_of_id": None,
        },
    ]

    plan = build_identity_reconciliation_plan(
        pool_rows=pool_rows,
        alias_rows=[],
        session_items=[],
        generated_at="2026-08-24T00:00:00Z",
    )
    aliases = plan["identity_alias_backfill"]

    assert [item["handle"] for item in aliases["safe_alias_backfills"]] == [
        "real.creator"
    ]
    assert {
        item["canonical_alias"]: item["review_reasons"]
        for item in aliases["review_aliases"]
    } == {
        "facebook:handle:story.php": ["unsafe_or_reserved_locator"],
        "youtube:handle:youtube": ["unsafe_or_reserved_locator"],
    }
    assert aliases["write_contract"]["apply_supported_by_this_planner"] is False


def test_history_display_gate_hides_only_confirmed_official_accounts() -> None:
    items = [
        {
            "id": 1,
            "item_type": "new_creator",
            "payload": {
                "platform": "youtube",
                "handle": "feelworldlofficial",
                "display_name": "FEELWORLD",
            },
        },
        {
            "id": 2,
            "item_type": "new_creator",
            "payload": {
                "platform": "youtube",
                "handle": "alex-films",
                "display_name": "Viltrox",
                "bio": "I'm an independent filmmaker reviewing Viltrox and other lenses.",
            },
        },
        {
            "id": 3,
            "item_type": "video_evidence",
            "payload": {"display_name": "Viltrox Official"},
        },
    ]

    kept, counts = apply_discovery_account_display_gate(items)

    assert [item["id"] for item in kept] == [2, 3]
    assert counts["excluded_total"] == 1
    assert counts["excluded_brand_official"] == 1
    assert counts["excluded_own_brand"] == 0
    assert counts["history_rows_deleted"] == 0


def test_history_reader_keeps_progress_truth_but_hides_official_card(monkeypatch) -> None:
    class _Rows:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class _Conn:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, sql, _params=()):
            compact = " ".join(sql.split())
            self.sql.append(compact)
            if "FROM vkpi_kol_search_sessions" in compact:
                return _Rows(
                    [
                        {
                            "id": 44,
                            "query_text": "camera creators",
                            "query_type": "text_recall",
                            "source": "smart_kol_input",
                            "status": "ready",
                            "created_by": 7,
                            "input_payload_json": {},
                            "result_summary_json": {},
                            "approved_kol_ids": [],
                            "archived_at": None,
                        }
                    ]
                )
            if "FROM vkpi_kol_search_session_items" in compact:
                return _Rows(
                    [
                        {
                            "id": 1,
                            "session_id": 44,
                            "item_type": "new_creator",
                            "status": "ready",
                            "dedupe_key": "official",
                            "payload_json": {
                                "platform": "youtube",
                                "handle": "feelworldlofficial",
                                "display_name": "FEELWORLD",
                            },
                        },
                        {
                            "id": 2,
                            "session_id": 44,
                            "item_type": "new_creator",
                            "status": "ready",
                            "dedupe_key": "independent",
                            "payload_json": {
                                "platform": "youtube",
                                "handle": "alex-films",
                                "display_name": "Viltrox",
                                "bio": "I'm an independent filmmaker reviewing lenses.",
                            },
                        },
                    ]
                )
            raise AssertionError(compact)

    seen: dict[str, int] = {}
    monkeypatch.setattr(
        search_sessions_history,
        "observe_worker_health",
        lambda _conn: {"status": "ready"},
    )

    def fake_progress(_session, items, *, worker_health):
        assert worker_health == {"status": "ready"}
        seen["progress_item_count"] = len(items)
        return {"status": "ready"}

    monkeypatch.setattr(search_sessions_history, "project_search_progress", fake_progress)
    conn = _Conn()
    result = search_sessions_history.list_history(
        staff={"id": 7},
        get_conn_fn=lambda: conn,
        apply_reach_display_gate_fn=lambda _conn, items: (items, {"hidden": 0}),
        mask_contact_payload_fn=lambda payload: payload,
    )

    assert seen["progress_item_count"] == 2
    assert result["items"][0]["item_count"] == 1
    assert result["items"][0]["items_preview"][0]["id"] == 2
    assert result["discovery_account_display_gate"]["excluded_total"] == 1
    assert not any("DELETE" in sql or "UPDATE" in sql for sql in conn.sql)


def test_session_detail_matches_history_official_gate_without_deleting_evidence(
    monkeypatch,
) -> None:
    class _Rows:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class _Conn:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, sql, _params=()):
            compact = " ".join(sql.split())
            self.sql.append(compact)
            if "FROM vkpi_kol_search_sessions WHERE" in compact:
                return _Rows(
                    [
                        {
                            "id": 44,
                            "query_text": "camera creators",
                            "query_type": "text_recall",
                            "source": "smart_kol_input",
                            "status": "ready",
                            "created_by": 7,
                            "input_payload_json": {},
                            "result_summary_json": {},
                            "approved_kol_ids": [],
                            "archived_at": None,
                        }
                    ]
                )
            if "FROM vkpi_kol_search_session_items" in compact:
                return _Rows(
                    [
                        {
                            "id": 1,
                            "session_id": 44,
                            "item_type": "new_creator",
                            "status": "ready",
                            "dedupe_key": "official",
                            "payload_json": {
                                "platform": "youtube",
                                "handle": "feelworldlofficial",
                                "display_name": "FEELWORLD",
                            },
                        },
                        {
                            "id": 2,
                            "session_id": 44,
                            "item_type": "new_creator",
                            "status": "ready",
                            "dedupe_key": "independent",
                            "payload_json": {
                                "platform": "youtube",
                                "handle": "alex-films",
                                "display_name": "Viltrox",
                                "bio": "I'm an independent filmmaker reviewing lenses.",
                            },
                        },
                    ]
                )
            raise AssertionError(compact)

    conn = _Conn()
    seen: dict[str, int] = {}
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)
    monkeypatch.setattr(
        search_sessions,
        "_refresh_enrichment_queue_states",
        lambda _conn, _items: None,
    )
    monkeypatch.setattr(
        search_sessions,
        "hydrate_session_item_previews",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        search_sessions,
        "observe_worker_health",
        lambda _conn: {"status": "ready"},
    )

    def fake_progress(_session, items, *, worker_health):
        assert worker_health == {"status": "ready"}
        seen["progress_item_count"] = len(items)
        return {"status": "ready"}

    monkeypatch.setattr(search_sessions, "project_search_progress", fake_progress)
    monkeypatch.setattr(
        search_sessions,
        "_apply_reach_display_gate",
        lambda _conn, items: (items, {"hidden": 0}),
    )
    monkeypatch.setattr(search_sessions, "mask_contact_payload", lambda payload: payload)

    result = search_sessions.get_session(
        44,
        staff={"id": 7},
        scope_to_staff=True,
    )

    assert seen["progress_item_count"] == 2
    assert [item["id"] for item in result["items"]] == [2]
    assert result["count"] == 1
    assert result["discovery_account_display_gate"]["excluded_total"] == 1
    assert result["discovery_account_display_gate"]["history_rows_deleted"] == 0
    assert not any("DELETE" in sql or "UPDATE" in sql for sql in conn.sql)

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from app.domains.intelligent_query import QueryScopeDenied, QueryValidationError, execute_query


NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
MANAGER = {"id": 7, "staff_id": 7, "role": "manager", "organization_id": 1}
MEMBER = {"id": 8, "staff_id": 8, "role": "employee", "organization_id": 1}


@pytest.fixture()
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
          id INTEGER PRIMARY KEY, platform TEXT, handle TEXT, display_name TEXT,
          country TEXT, duplicate_of_id INTEGER, updated_at TEXT
        );
        CREATE TABLE vkpi_kol_pool_favorites (
          id INTEGER PRIMARY KEY, kol_pool_id INTEGER, staff_id INTEGER
        );
        CREATE TABLE vkpi_kol_pool_members (
          id INTEGER PRIMARY KEY, kol_pool_id INTEGER, staff_id INTEGER
        );
        CREATE TABLE vkpi_kol_video_evidence (
          id INTEGER PRIMARY KEY, kol_pool_id INTEGER, platform TEXT,
          content_url TEXT, video_title TEXT, title TEXT, posted_at TEXT,
          publish_date TEXT, published_at_norm TEXT, confidence TEXT,
          is_active INTEGER, created_at TEXT, updated_at TEXT, scraped_at TEXT
        );
        CREATE TABLE vkpi_kol_llm_deep_analysis_results (
          id INTEGER PRIMARY KEY, kol_pool_id INTEGER, source_evidence_id INTEGER,
          status TEXT, analysis_kind TEXT, created_at TEXT
        );
        CREATE TABLE vkpi_product_aliases (
          id INTEGER PRIMARY KEY, sku TEXT, alias TEXT, alias_norm TEXT,
          confidence REAL
        );
        CREATE TABLE vkpi_projects (
          id INTEGER PRIMARY KEY, project_uid TEXT, project_name TEXT,
          product_sku TEXT, product_name TEXT, platform TEXT, stage TEXT,
          stage_status TEXT, priority TEXT, assigned_staff_id INTEGER,
          created_by_staff_id INTEGER, restricted INTEGER, is_public INTEGER,
          updated_at TEXT
        );
        CREATE TABLE vkpi_project_members (
          id INTEGER PRIMARY KEY, project_id INTEGER, staff_id INTEGER, role TEXT
        );
        CREATE TABLE vkpi_comments (
          id INTEGER PRIMARY KEY, platform TEXT, comment_text TEXT,
          likes_count INTEGER, language_detected TEXT, created_at TEXT,
          fetched_at TEXT
        );
        CREATE TABLE vkpi_reply_queue (
          id INTEGER PRIMARY KEY, platform TEXT, comment_text TEXT,
          intent_tag TEXT, created_at TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO vkpi_kol_pool VALUES (?,?,?,?,?,?,?)",
        [
            (1, "youtube", "alpha", "Alpha", "US", None, "2026-08-04T10:00:00Z"),
            (2, "youtube", "beta", "Beta", "CA", None, "2026-08-04T09:00:00Z"),
            (3, "instagram", "gamma", "Gamma", "DE", None, "2026-08-03T09:00:00Z"),
            (4, "youtube", "alpha-old", "Alpha old", "US", 1, "2026-07-01T09:00:00Z"),
        ],
    )
    conn.executemany(
        "INSERT INTO vkpi_kol_video_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (11, 1, "youtube", "https://example.test/11", "Viltrox 26mm EVO review", "", "2026-08-03", None, "2026-08-03", "high", 1, "2026-08-03T10:00:00Z", "2026-08-03T10:00:00Z", "2026-08-03T10:00:00Z"),
            (12, 2, "youtube", "https://example.test/12", "26mm EVO field test", "", "2026-08-02", None, "2026-08-02", "high", 1, "2026-08-02T10:00:00Z", "2026-08-02T10:00:00Z", "2026-08-02T10:00:00Z"),
            (13, 3, "instagram", "https://example.test/13", "Street photography", "", "2026-08-01", None, "2026-08-01", "high", 1, "2026-08-01T10:00:00Z", "2026-08-01T10:00:00Z", "2026-08-01T10:00:00Z"),
            (14, 4, "youtube", "https://example.test/14", "26mm EVO old duplicate", "", "2026-08-01", None, "2026-08-01", "high", 1, "2026-08-01T10:00:00Z", "2026-08-01T10:00:00Z", "2026-08-01T10:00:00Z"),
        ],
    )
    conn.execute(
        "INSERT INTO vkpi_kol_llm_deep_analysis_results VALUES (1,1,11,'ready','video_final_v1','2026-08-03T12:00:00Z')"
    )
    conn.execute(
        "INSERT INTO vkpi_product_aliases VALUES (1,'AF-26-EVO','26mm EVO','26mm evo',0.95)"
    )
    conn.executemany(
        "INSERT INTO vkpi_kol_pool_favorites VALUES (?,?,?)",
        [(1, 1, 8), (2, 2, 7)],
    )
    conn.execute("INSERT INTO vkpi_kol_pool_members VALUES (1,3,8)")
    conn.executemany(
        "INSERT INTO vkpi_projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (21, "P-21", "26mm EVO launch", "AF-26-EVO", "26mm EVO", "youtube", "outreach", "active", "high", 8, 8, 0, 0, "2026-08-04T08:00:00Z"),
            (22, "P-22", "Private lens plan", "AF-75", "75mm", "youtube", "planning", "active", "normal", 9, 9, 0, 0, "2026-08-04T07:00:00Z"),
            (23, "P-23", "Public creator plan", "AF-35", "35mm", "instagram", "planning", "active", "normal", 9, 9, 0, 1, "2026-08-04T06:00:00Z"),
            (24, "P-24", "Deleted EVO plan", "AF-26", "26mm", "youtube", "done", "deleted", "normal", 8, 8, 0, 0, "2026-08-04T05:00:00Z"),
            (25, "P-25", "Manager-owned plan", "AF-16", "16mm", "youtube", "planning", "active", "normal", 7, 7, 0, 0, "2026-08-04T04:00:00Z"),
        ],
    )
    conn.executemany(
        "INSERT INTO vkpi_comments VALUES (?,?,?,?,?,?,?)",
        [
            (31, "youtube", "Viltrox is an amazing sharp lens, I love it", 10, "en", "2026-08-03T09:00:00Z", None),
            (32, "youtube", "Viltrox autofocus is slow and hunting, bad issue", 6, "en", "2026-08-02T09:00:00Z", None),
            (33, "instagram", "Please make a Viltrox 40mm for Nikon Z", 2, "en", "2026-08-01T09:00:00Z", None),
            (34, "youtube", "old voice", 1, "en", "2026-07-01T09:00:00Z", None),
            (35, "youtube", "26mm EVO looks great", 3, "en", "2026-08-03T08:00:00Z", None),
        ],
    )
    conn.execute(
        "INSERT INTO vkpi_reply_queue VALUES (41,'youtube','Please make a Viltrox 40mm for Nikon Z','question','2026-08-01T09:10:00Z')"
    )
    return conn


def _assert_contract(result: dict) -> None:
    assert result["schema_version"] == "ask_find_v2"
    assert result["request_id"] == result["trace"]["request_id"]
    for key in (
        "status",
        "intent",
        "answer",
        "facts",
        "evidence",
        "coverage",
        "freshness",
        "missing_fields",
        "actions",
        "trace",
    ):
        assert key in result
    assert result["trace"]["deterministic"] is True


def test_pool_overview_counts_only_canonical_rows(db: sqlite3.Connection) -> None:
    result = execute_query(
        {"query": "目前 KOL 数量是多少？", "client_request_id": "overview-1"},
        staff=MANAGER,
        conn=db,
        now=NOW,
    )
    _assert_contract(result)
    assert result["intent"] == "kol.pool.overview"
    facts = {item["key"]: item["value"] for item in result["facts"]}
    assert facts == {
        "kol.total": 3,
        "kol.with_video_evidence": 3,
        "video.evidence_rows": 3,
        "kol.deep_analyzed": 1,
        "kol.merged_duplicates": 1,
    }
    assert result["trace"]["client_request_id"] == "overview-1"


def test_video_topic_counts_title_evidence_not_profile_similarity(db: sqlite3.Connection) -> None:
    result = execute_query(
        {"query": "多少 KOL 做过 26mm EVO 相关视频？", "filters": {"limit": 30}},
        staff=MANAGER,
        conn=db,
        now=NOW,
    )
    _assert_contract(result)
    assert result["intent"] == "kol.video_topic.count"
    facts = {item["key"]: item["value"] for item in result["facts"]}
    assert facts["kol.confirmed_topic_match"] == 2
    assert facts["video.confirmed_topic_match"] == 2
    assert facts["video.deep_analyzed_match"] == 1
    assert {item["entity_id"] for item in result["evidence"]} == {1, 2}
    assert result["status"] == "partial"
    assert any(item["field"] == "transcript_topic_index" for item in result["missing_fields"])


def test_video_topic_injection_is_data_not_sql(db: sqlite3.Connection) -> None:
    result = execute_query(
        {
            "query": "多少 KOL 做过视频？",
            "filters": {"intent": "kol.video_topic.count", "topic": "%' OR 1=1 --"},
        },
        staff=MANAGER,
        conn=db,
        now=NOW,
    )
    assert result["status"] == "empty"
    assert db.execute("SELECT COUNT(*) FROM vkpi_kol_pool").fetchone()[0] == 4


def test_own_kol_scope_uses_favorites_and_shared_rows(db: sqlite3.Connection) -> None:
    result = execute_query(
        {"query": "目前 KOL 数量", "scope": "own"},
        staff=MEMBER,
        conn=db,
        now=NOW,
    )
    facts = {item["key"]: item["value"] for item in result["facts"]}
    assert facts["kol.total"] == 2
    assert facts["kol.with_video_evidence"] == 2


def test_own_kol_scope_also_includes_project_assignments_and_active_claims(
    db: sqlite3.Connection,
) -> None:
    db.execute("ALTER TABLE vkpi_kol_pool ADD COLUMN linked_main_kol_id INTEGER")
    db.executescript(
        """
        CREATE TABLE vkpi_project_kol_assignments (
          id INTEGER PRIMARY KEY, project_id INTEGER, kol_pool_id INTEGER
        );
        CREATE TABLE vkpi_kol_claims (
          id INTEGER PRIMARY KEY, kol_id INTEGER, staff_id INTEGER, status TEXT
        );
        """
    )
    db.executemany(
        "INSERT INTO vkpi_kol_pool "
        "(id,platform,handle,display_name,country,duplicate_of_id,updated_at,linked_main_kol_id) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [
            (5, "youtube", "project-kol", "Project KOL", "US", None, "2026-08-04T11:00:00Z", 500),
            (6, "youtube", "claimed-kol", "Claimed KOL", "US", None, "2026-08-04T11:00:00Z", 600),
        ],
    )
    db.execute("INSERT INTO vkpi_project_kol_assignments VALUES (1,21,5)")
    db.execute("INSERT INTO vkpi_kol_claims VALUES (1,600,8,'active')")

    result = execute_query(
        {"query": "目前 KOL 数量", "scope": "own"},
        staff=MEMBER,
        conn=db,
        now=NOW,
    )
    facts = {item["key"]: item["value"] for item in result["facts"]}
    assert facts["kol.total"] == 4
    assert result["trace"]["scope"]["applied_mode"] == "own"
    assert result["trace"]["scope"]["effective_staff_id"] == 8


def test_member_auto_scope_is_traced_as_own_for_kol_and_projects(db: sqlite3.Connection) -> None:
    overview = execute_query(
        {"query": "目前 KOL 数量"}, staff=MEMBER, conn=db, now=NOW
    )
    projects = execute_query(
        {"query": "搜索项目", "filters": {"intent": "project.search"}},
        staff=MEMBER,
        conn=db,
        now=NOW,
    )
    for result in (overview, projects):
        assert result["trace"]["scope"]["applied_mode"] == "own"
        assert result["trace"]["scope"]["effective_staff_id"] == 8


def test_restricted_project_member_does_not_leak_project_kol_into_own_scope(
    db: sqlite3.Connection,
) -> None:
    db.execute(
        "CREATE TABLE vkpi_project_kol_assignments "
        "(id INTEGER PRIMARY KEY, project_id INTEGER, kol_pool_id INTEGER)"
    )
    db.execute(
        "INSERT INTO vkpi_kol_pool VALUES (5,'youtube','restricted-kol','Restricted KOL','US',NULL,'2026-08-04T11:00:00Z')"
    )
    db.execute("UPDATE vkpi_projects SET restricted=1 WHERE id=22")
    db.execute("INSERT INTO vkpi_project_members VALUES (2,22,8,'viewer')")
    db.execute("INSERT INTO vkpi_project_kol_assignments VALUES (1,22,5)")

    overview = execute_query(
        {"query": "目前 KOL 数量"}, staff=MEMBER, conn=db, now=NOW
    )
    facts = {item["key"]: item["value"] for item in overview["facts"]}
    assert facts["kol.total"] == 2
    projects = execute_query(
        {"query": "搜索项目", "filters": {"intent": "project.search"}},
        staff=MEMBER,
        conn=db,
        now=NOW,
    )
    assert 22 not in {item["entity_id"] for item in projects["evidence"]}


def test_project_search_applies_visibility_and_deleted_filter(db: sqlite3.Connection) -> None:
    result = execute_query(
        {"query": "搜索项目", "filters": {"intent": "project.search"}},
        staff=MEMBER,
        conn=db,
        now=NOW,
    )
    _assert_contract(result)
    assert result["status"] == "ready"
    assert {item["entity_id"] for item in result["evidence"]} == {21, 23}
    assert all(item["entity_id"] != 24 for item in result["evidence"])


def test_manager_own_project_scope_is_not_silently_global(db: sqlite3.Connection) -> None:
    result = execute_query(
        {"query": "搜索项目", "scope": "own", "filters": {"intent": "project.search"}},
        staff=MANAGER,
        conn=db,
        now=NOW,
    )
    assert {item["entity_id"] for item in result["evidence"]} == {25}
    assert result["trace"]["scope"]["effective_staff_id"] == 7


def test_weekly_voice_is_exact_seven_day_internal_sample(db: sqlite3.Connection) -> None:
    result = execute_query(
        {"query": "总结本周市场对于 Viltrox 的评价", "time_range": "7d"},
        staff=MANAGER,
        conn=db,
        now=NOW,
    )
    _assert_contract(result)
    assert result["intent"] == "market.viltrox.weekly_voice"
    facts = {item["key"]: item["value"] for item in result["facts"]}
    assert facts["market.voice_sample"] == 3
    assert facts["market.positive_rule_signals"] == 1
    assert facts["market.complaint_rule_signals"] == 1
    assert facts["market.wishlist_rule_signals"] == 1
    assert facts["market.viltrox_video_mentions"] == 1
    assert result["freshness"]["window_start"] == "2026-07-28T12:00:00Z"
    assert result["freshness"]["window_end"] == "2026-08-04T12:00:00Z"
    assert result["status"] == "partial"
    assert any("排除 1 条" in note for note in result["coverage"]["notes"])
    assert result["actions"][0]["route"] == "marketVoice"


def test_weekly_voice_uses_shared_global_trace_and_rejects_fake_own_scope(
    db: sqlite3.Connection,
) -> None:
    shared = execute_query(
        {"query": "总结本周市场对于 Viltrox 的评价"},
        staff=MEMBER,
        conn=db,
        now=NOW,
    )
    assert shared["trace"]["scope"]["applied_mode"] == "shared_global"
    assert shared["trace"]["scope"]["effective_staff_id"] is None
    with pytest.raises(QueryScopeDenied, match="ownership"):
        execute_query(
            {
                "query": "weekly market feedback for Viltrox",
                "scope": "own",
                "locale": "en-US",
            },
            staff=MEMBER,
            conn=db,
            now=NOW,
        )


def test_title_only_legacy_video_is_counted_and_displayed(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO vkpi_kol_video_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            15,
            3,
            "youtube",
            "https://example.test/15",
            "",
            "Viltrox title-only review",
            "2026-08-03",
            None,
            "2026-08-03",
            "high",
            1,
            "2026-08-03T11:00:00Z",
            "2026-08-03T11:00:00Z",
            "2026-08-03T11:00:00Z",
        ),
    )
    topic = execute_query(
        {
            "query": "How many KOLs reviewed Viltrox?",
            "filters": {"intent": "kol.video_topic.count", "topic": "Viltrox"},
            "locale": "en-US",
        },
        staff=MANAGER,
        conn=db,
        now=NOW,
    )
    title_only = next(item for item in topic["evidence"] if item["id"] == "video-15")
    assert title_only["title"] == "Viltrox title-only review"
    weekly = execute_query(
        {"query": "weekly market feedback for Viltrox", "locale": "en-US"},
        staff=MANAGER,
        conn=db,
        now=NOW,
    )
    facts = {item["key"]: item["value"] for item in weekly["facts"]}
    assert facts["market.viltrox_video_mentions"] == 2
    assert any(item["title"] == "Viltrox title-only review" for item in weekly["evidence"])


def test_weekly_facts_are_omitted_per_unavailable_source(db: sqlite3.Connection) -> None:
    db.execute("DROP TABLE vkpi_comments")
    video_only = execute_query(
        {"query": "总结本周市场对于 Viltrox 的评价"},
        staff=MANAGER,
        conn=db,
        now=NOW,
    )
    video_keys = {item["key"] for item in video_only["facts"]}
    assert video_keys == {"market.viltrox_video_mentions"}
    assert video_only["status"] == "partial"
    assert video_only["trace"]["source_status"]["comments"]["status"] == "absent"

    db.execute("DROP TABLE vkpi_kol_video_evidence")
    unavailable = execute_query(
        {"query": "总结本周市场对于 Viltrox 的评价"},
        staff=MANAGER,
        conn=db,
        now=NOW,
    )
    assert unavailable["status"] == "error"
    assert unavailable["facts"] == []
    assert unavailable["coverage"]["status"] == "unknown"
    assert unavailable["degraded_reason"] == "weekly_market_sources_unavailable"


def test_weekly_text_only_omits_unavailable_video_zero(db: sqlite3.Connection) -> None:
    db.execute("DROP TABLE vkpi_kol_video_evidence")
    result = execute_query(
        {"query": "总结本周市场对于 Viltrox 的评价"},
        staff=MANAGER,
        conn=db,
        now=NOW,
    )
    keys = {item["key"] for item in result["facts"]}
    assert "market.voice_sample" in keys
    assert "market.viltrox_video_mentions" not in keys
    assert "视频 0" not in result["answer"]
    assert result["status"] == "partial"


def test_weekly_uses_content_event_time_not_ingestion_time(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO vkpi_comments VALUES (?,?,?,?,?,?,?)",
        (36, "youtube", "Viltrox old content fetched now", 5, "en", "2026-07-01T09:00:00Z", "2026-08-03T09:00:00Z"),
    )
    db.execute(
        "INSERT INTO vkpi_kol_video_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (18, 1, "youtube", "https://example.test/18", "Viltrox old video updated now", "", "2026-07-01", None, "2026-07-01", "high", 1, "2026-08-03T10:00:00Z", "2026-08-03T10:00:00Z", "2026-08-03T10:00:00Z"),
    )
    result = execute_query(
        {"query": "总结本周市场对于 Viltrox 的评价"},
        staff=MANAGER,
        conn=db,
        now=NOW,
    )
    facts = {item["key"]: item["value"] for item in result["facts"]}
    assert facts["market.voice_sample"] == 3
    assert facts["market.viltrox_video_mentions"] == 1
    snippets = {item.get("snippet") for item in result["evidence"]}
    assert "Viltrox old content fetched now" not in snippets
    assert all(item.get("title") != "Viltrox old video updated now" for item in result["evidence"])


def test_weekly_freshness_uses_latest_event_and_evidence_count_matches_payload(
    db: sqlite3.Connection,
) -> None:
    rows = [
        (
            100 + index,
            "youtube",
            f"Viltrox fresh sample {index}",
            index,
            "en",
            f"2026-08-04T11:{index:02d}:00Z",
            None,
        )
        for index in range(13)
    ]
    db.executemany("INSERT INTO vkpi_comments VALUES (?,?,?,?,?,?,?)", rows)
    result = execute_query(
        {"query": "总结本周市场对于 Viltrox 的评价"},
        staff=MANAGER,
        conn=db,
        now=NOW,
    )
    assert result["freshness"]["data_updated_at"] == "2026-08-04T11:12:00Z"
    assert result["coverage"]["evidence_count"] == len(result["evidence"])
    assert len(result["evidence"]) <= 12


def test_product_alias_normalizer_and_topic_alias_regressions(db: sqlite3.Connection) -> None:
    from app.domains.products.product_aliases import normalize_alias

    assert normalize_alias("35mm f/1.2 LAB") == "35mm f12 lab"
    assert normalize_alias("85mm f1.8") == "85mm f18"
    assert normalize_alias("AF-85MM-F18") == "af 85mm f18"
    db.executemany(
        "INSERT INTO vkpi_product_aliases VALUES (?,?,?,?,?)",
        [
            (2, "AF-35MM-F12-LAB", "35mm f/1.2 LAB", "35mm f12 lab", 0.98),
            (3, "AF-85MM-F18", "85mm f1.8", "85mm f18", 0.98),
            (4, "AF-85MM-F18", "AF-85MM-F18", "af 85mm f18", 1.0),
        ],
    )
    db.executemany(
        "INSERT INTO vkpi_kol_video_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (16, 1, "youtube", "https://example.test/16", "35mm f/1.2 LAB review", "", "2026-08-03", None, "2026-08-03", "high", 1, "2026-08-03T10:00:00Z", "2026-08-03T10:00:00Z", "2026-08-03T10:00:00Z"),
            (17, 2, "youtube", "https://example.test/17", "AF-85MM-F18 field test", "", "2026-08-03", None, "2026-08-03", "high", 1, "2026-08-03T10:00:00Z", "2026-08-03T10:00:00Z", "2026-08-03T10:00:00Z"),
        ],
    )
    for topic, evidence_id in (
        ("35mm f/1.2 LAB", "video-16"),
        ("85mm f1.8", "video-17"),
        ("AF-85MM-F18", "video-17"),
    ):
        result = execute_query(
            {
                "query": "count topic videos",
                "filters": {"intent": "kol.video_topic.count", "topic": topic},
                "locale": "en-US",
            },
            staff=MANAGER,
            conn=db,
            now=NOW,
        )
        assert evidence_id in {item["id"] for item in result["evidence"]}


def test_overview_omits_video_facts_when_video_source_is_missing(
    db: sqlite3.Connection,
) -> None:
    db.execute("DROP TABLE vkpi_kol_video_evidence")
    result = execute_query(
        {"query": "目前 KOL 数量"}, staff=MANAGER, conn=db, now=NOW
    )
    keys = {item["key"] for item in result["facts"]}
    assert "kol.total" in keys
    assert "kol.with_video_evidence" not in keys
    assert "video.evidence_rows" not in keys
    assert all(item["source"] != "vkpi_kol_video_evidence" for item in result["evidence"])
    assert "视频证据" not in result["answer"]
    assert result["status"] == "partial"


def test_overview_labels_raw_records_when_canonical_column_is_missing() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY, updated_at TEXT)")
    conn.executemany(
        "INSERT INTO vkpi_kol_pool VALUES (?,?)",
        [(1, "2026-08-04T10:00:00Z"), (2, "2026-08-04T09:00:00Z")],
    )
    result = execute_query(
        {"query": "目前 KOL 数量"}, staff=MANAGER, conn=conn, now=NOW
    )
    facts = {item["key"]: item for item in result["facts"]}
    assert "kol.total" not in facts
    assert facts["kol.raw_records"]["value"] == 2
    assert facts["kol.raw_records"]["confidence"] == "medium"
    assert "去重口径不可用" in result["answer"]
    assert result["evidence"][0]["title"] == "KOL Pool 原始记录汇总"


def test_missing_requested_kol_filter_fails_closed_without_zero_fact() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY, duplicate_of_id INTEGER, updated_at TEXT)"
    )
    conn.execute("INSERT INTO vkpi_kol_pool VALUES (1,NULL,'2026-08-04T10:00:00Z')")
    result = execute_query(
        {"query": "目前 KOL 数量", "filters": {"country": "US"}},
        staff=MANAGER,
        conn=conn,
        now=NOW,
    )
    assert result["status"] == "error"
    assert result["degraded_reason"] == "requested_filter_unavailable"
    assert result["facts"] == []
    assert result["missing_fields"][0]["field"] == "filters.country"


@pytest.mark.parametrize(
    "payload,expected_reason",
    [
        (
            {"query": "项目 26mm", "filters": {"intent": "project.search", "keyword": "26mm"}},
            "project_keyword_fields_unavailable",
        ),
        (
            {"query": "搜索项目", "filters": {"intent": "project.search", "stage": "planning"}},
            "project_stage_filter_unavailable",
        ),
    ],
)
def test_missing_requested_project_filter_fails_closed(
    payload: dict, expected_reason: str
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE vkpi_projects (id INTEGER PRIMARY KEY, stage_status TEXT, updated_at TEXT)"
    )
    conn.execute("INSERT INTO vkpi_projects VALUES (1,'active','2026-08-04T10:00:00Z')")
    result = execute_query(payload, staff=MANAGER, conn=conn, now=NOW)
    assert result["status"] == "error"
    assert result["degraded_reason"] == expected_reason
    assert result["facts"] == []


def test_weekly_voice_rejects_legacy_30_day_window_before_returning_facts(db: sqlite3.Connection) -> None:
    with pytest.raises(QueryValidationError, match="exact seven-day"):
        execute_query(
            {"query": "总结本周市场对于 Viltrox 的评价", "time_range": "30d"},
            staff=MANAGER,
            conn=db,
            now=NOW,
        )


def test_unknown_intent_does_not_touch_database() -> None:
    class BombConnection:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("unknown intent must not query the database")

    result = execute_query(
        {"query": "这个事情你怎么看？"},
        staff=MEMBER,
        conn=BombConnection(),
        now=NOW,
    )
    _assert_contract(result)
    assert result["status"] == "needs_clarification"
    assert result["intent"] == "unknown"
    assert result["degraded_reason"] == "intent_not_resolved"


def test_cross_staff_scope_denied_before_database_access() -> None:
    class BombConnection:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("scope denial must happen before SQL")

    with pytest.raises(QueryScopeDenied):
        execute_query(
            {"query": "目前 KOL 数量", "scope": {"mode": "own", "staff_id": 99}},
            staff=MEMBER,
            conn=BombConnection(),
            now=NOW,
        )


@pytest.mark.parametrize(
    "staff,payload",
    [
        ({"user_id": 8, "role": "employee"}, {"query": "目前 KOL 数量"}),
        (
            {"id": 8, "role": "employee", "organization_scope_status": "unresolved"},
            {"query": "目前 KOL 数量"},
        ),
        (MANAGER, {"query": "目前 KOL 数量", "scope": "team"}),
    ],
)
def test_missing_unresolved_or_unimplemented_scope_fails_before_sql(staff: dict, payload: dict) -> None:
    class BombConnection:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("invalid scope must fail before SQL")

    with pytest.raises(QueryScopeDenied):
        execute_query(payload, staff=staff, conn=BombConnection(), now=NOW)


def test_zh_contract_fields_and_action_routes_are_localized(db: sqlite3.Connection) -> None:
    overview = execute_query(
        {"query": "目前 KOL 数量", "locale": "zh-CN"},
        staff=MANAGER,
        conn=db,
        now=NOW,
    )
    assert all("统计" in fact["basis"] or "按请求" in fact["basis"] for fact in overview["facts"])
    assert overview["evidence"][0]["title"] == "KOL Pool 去重汇总"

    video = execute_query(
        {"query": "多少 KOL 做过 26mm EVO 视频？", "locale": "zh-CN"},
        staff=MANAGER,
        conn=db,
        now=NOW,
    )
    gap = next(item for item in video["missing_fields"] if item["field"] == "transcript_topic_index")
    assert "仅通过" in gap["reason"]
    assert "可能漏检" in gap["impact"]
    assert {action.get("route") for action in video["actions"]} == {"kol-pool", "dashboard"}

    unknown = execute_query(
        {"query": "这个事情你怎么看？", "locale": "zh-CN"},
        staff=MEMBER,
        conn=db,
        now=NOW,
    )
    assert "未匹配" in unknown["missing_fields"][0]["reason"]
    assert "未知意图" in unknown["coverage"]["notes"][0]


def test_en_contract_fields_are_english(db: sqlite3.Connection) -> None:
    result = execute_query(
        {"query": "How many KOLs reviewed 26mm EVO?", "locale": "en-US"},
        staff=MANAGER,
        conn=db,
        now=NOW,
    )
    assert result["intent"] == "kol.video_topic.count"
    assert result["facts"][0]["label"] == "Confirmed matching KOLs"
    assert "COUNT" in result["facts"][0]["basis"]
    assert "title evidence" in result["coverage"]["notes"][0]


def test_legacy_intent_unavailable_is_not_reported_or_cached_as_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.routers import vkpi_intelligent
    import app.domains.analytics.query_planner as planner
    import app.db.connection as db_connection

    monkeypatch.setattr(planner, "resolve_intent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        planner,
        "run",
        lambda *_args, **_kwargs: {
            "intent": "country_growth",
            "title": "国家增长（数据源未就绪）",
            "columns": ["country", "kols"],
            "rows": [],
            "source_status": "unavailable",
            "source_reason": "verified_country_dimension_missing",
        },
    )
    monkeypatch.setattr(db_connection, "get_conn", lambda: object())
    direct = vkpi_intelligent._try_intent("美国市场增长")
    assert direct is not None
    assert direct["status"] == "degraded"
    assert direct["degraded_reason"] == "verified_country_dimension_missing"
    assert "未生成零值结论" in direct["answer"]

    calls = {"n": 0}

    def unavailable(_question: str) -> dict:
        calls["n"] += 1
        return dict(direct)

    monkeypatch.setattr(vkpi_intelligent, "_try_intent", unavailable)
    with vkpi_intelligent._ASK_CACHE_LOCK:
        vkpi_intelligent._ASK_CACHE.clear()
    manager = {"id": 1, "staff_id": 1, "role": "admin", "is_owner": 1}
    first = vkpi_intelligent.intelligent_ask({"question": "美国市场增长"}, staff=manager)
    second = vkpi_intelligent.intelligent_ask({"question": "美国市场增长"}, staff=manager)
    assert calls["n"] == 2
    assert first["cached"] is False
    assert second["cached"] is False


def test_legacy_intent_sql_error_is_explicit_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.routers import vkpi_intelligent
    import app.domains.analytics.query_planner as planner
    import app.db.connection as db_connection

    monkeypatch.setattr(planner, "resolve_intent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        planner,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )
    monkeypatch.setattr(db_connection, "get_conn", lambda: object())
    result = vkpi_intelligent._try_intent("目前 KOL 数量")
    assert result is not None
    assert result["status"] == "degraded"
    assert result["degraded_reason"] == "intent_query_failed"
    assert result["evidence"][0]["error_type"] == "RuntimeError"


@pytest.mark.parametrize("role", ["manager", "admin", "owner"])
def test_legacy_ask_unresolved_org_scope_denied_before_any_lane(
    monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    from fastapi import HTTPException
    from app.api.routers import vkpi_intelligent

    def bomb(*_args, **_kwargs):
        raise AssertionError("unresolved organization scope must stop before cache/DB/provider lanes")

    monkeypatch.setattr(vkpi_intelligent, "_cache_get", bomb)
    monkeypatch.setattr(vkpi_intelligent, "_try_intent", bomb)
    monkeypatch.setattr(vkpi_intelligent, "_try_search", bomb)
    monkeypatch.setattr(vkpi_intelligent, "_try_synth", bomb)
    staff = {
        "id": 7,
        "staff_id": 7,
        "role": role,
        "is_owner": role == "owner",
        "organization_scope_status": "unresolved",
    }
    with pytest.raises(HTTPException) as exc_info:
        vkpi_intelligent.intelligent_ask({"question": "目前 KOL 数量"}, staff=staff)
    assert exc_info.value.status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": "x" * 513},
        {"query": "ok", "locale": "fr-FR"},
        {"query": "ok", "mode": "agent"},
        {"query": "ok", "scope": {"mode": "own", "staff_id": "bad"}},
    ],
)
def test_invalid_requests_fail_before_sql(payload: dict) -> None:
    with pytest.raises(QueryValidationError):
        execute_query(payload, staff=MEMBER, conn=None, now=NOW)

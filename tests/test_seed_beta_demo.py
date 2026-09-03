"""公测演示种子 seed_beta_demo.py:默认 dry-run 零写;--apply 才落行;全部行带演示标记;幂等;可整批 purge。

hermetic:sqlite 内存库(8 张目标表的最小列集);``pg`` 段用真 Postgres 事务内跑一遍 apply 再回滚。
红线:脚本源码与其 SQL 绝不提及 viltrox_fit_score / rule_v0;演示行只按自己的自然键增删。
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
SCRIPT = ROOT / "backend" / "scripts_local" / "seed_beta_demo.py"
NOW = datetime(2026, 9, 2, 8, 30, tzinfo=timezone.utc)

SCHEMA = """
CREATE TABLE vkpi_dealer_event_candidates (
    organization_id INTEGER NOT NULL, id TEXT NOT NULL, candidate_type TEXT NOT NULL, source_registry_id TEXT NOT NULL,
    source_entity_key TEXT NOT NULL, source_url TEXT NOT NULL, stable_org_key TEXT DEFAULT '', stable_location_key TEXT DEFAULT '',
    content_sha256 TEXT NOT NULL, candidate_payload_json TEXT NOT NULL DEFAULT '{}', review_status TEXT DEFAULT 'pending',
    promotion_gate_status TEXT DEFAULT 'blocked', claim_status TEXT DEFAULT 'descriptive_only', created_at TEXT, updated_at TEXT,
    PRIMARY KEY (organization_id, id)
);
CREATE TABLE vkpi_kol_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pool_uid TEXT UNIQUE, platform TEXT, handle TEXT, profile_url TEXT, display_name TEXT,
    bio TEXT, country TEXT, language TEXT, followers INTEGER, avg_views INTEGER, primary_topic TEXT, sync_status TEXT,
    source_type TEXT, source_ref TEXT, raw_platform_data TEXT, viltrox_fit_score REAL, last_seen_at TEXT, created_at TEXT, updated_at TEXT,
    UNIQUE (platform, handle)
);
CREATE TABLE vkpi_kol_video_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kol_pool_id INTEGER NOT NULL, content_url TEXT UNIQUE, platform TEXT, video_title TEXT,
    title TEXT, posted_at TEXT, view_count INTEGER, like_count INTEGER, comment_count INTEGER, source TEXT, source_ref TEXT,
    confidence TEXT, is_active INTEGER
);
CREATE TABLE vkpi_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_uid TEXT UNIQUE, project_name TEXT, assigned_staff_id INTEGER,
    created_by_staff_id INTEGER, product_sku TEXT, product_name TEXT, platform TEXT, stage TEXT, stage_status TEXT,
    priority TEXT, source_type TEXT, metadata_json TEXT, started_at TEXT, last_activity_at TEXT, is_public INTEGER
);
CREATE TABLE vkpi_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, post_table TEXT, external_post_id TEXT, platform TEXT, external_comment_id TEXT,
    comment_text TEXT, language_detected TEXT, author_handle TEXT, likes_count INTEGER, reply_count INTEGER,
    created_at TEXT, fetched_at TEXT, raw_data_json TEXT, UNIQUE (platform, external_comment_id)
);
CREATE TABLE vkpi_publish_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_table TEXT, source_id TEXT, platform TEXT, account_handle TEXT, title TEXT,
    status TEXT, note TEXT, created_at TEXT, updated_at TEXT, UNIQUE (source_table, source_id)
);
CREATE TABLE vkpi_report_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, report_uid TEXT UNIQUE, report_type TEXT, period_start TEXT, period_end TEXT,
    scope_type TEXT, scope_id INTEGER, triggered_by_staff_id INTEGER, triggered_at TEXT, status TEXT, summary_text TEXT, metadata_json TEXT
);
CREATE TABLE vkpi_weekly_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, staff_id INTEGER, layer INTEGER, template_key TEXT, period_start TEXT, period_end TEXT,
    title TEXT, body_md TEXT, llm_provider TEXT, status TEXT, generated_at TEXT, source_data_status TEXT, source_count INTEGER,
    source_is_partial INTEGER
);
"""
TABLES = (
    "vkpi_dealer_event_candidates", "vkpi_kol_pool", "vkpi_kol_video_evidence", "vkpi_projects",
    "vkpi_comments", "vkpi_publish_approvals", "vkpi_report_runs", "vkpi_weekly_reports",
)


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("seed_beta_demo_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # 3.14 的 dataclass 会按 cls.__module__ 回查 sys.modules 解析字符串注解;不登记就 KeyError。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seed() -> Any:
    return _load_module()


@pytest.fixture()
def conn(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    from app.db import connection

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    monkeypatch.setattr(
        connection, "table_exists",
        lambda name: db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None,
    )
    return db


def _count(db: sqlite3.Connection, table: str) -> int:
    return int(db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])


def _ctx(seed: Any, *, apply: bool, staff_id: int | None = None) -> Any:
    return seed.SeedContext(now=NOW, pg=False, staff_id=staff_id, org_id=1, apply=apply)


def test_dry_run_plans_every_board_but_writes_nothing(seed: Any, conn: sqlite3.Connection) -> None:
    plans = seed.seed_all(conn, _ctx(seed, apply=False))
    endpoints = {plan.endpoint for plan in plans}
    assert endpoints == {
        "events.candidate-staging", "shopify.gmv", "intelligent.ask-video-26mm-evo", "intelligent.ask-project-search",
        "intelligent.ask-weekly-market", "launchpad.publish-approvals", "reports.history", "reports.weekly-read",
    }
    actions = seed._summarize(plans)
    assert actions["skipped_by_design"] == 1 and actions["insert"] == len(plans) - 1
    shopify = next(plan for plan in plans if plan.endpoint == "shopify.gmv")
    assert "HMAC" in shopify.detail and "诚实空态" in shopify.detail
    assert all(_count(conn, table) == 0 for table in TABLES)


def test_apply_seeds_marked_rows_idempotently_and_purge_removes_only_them(seed: Any, conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO vkpi_kol_pool (pool_uid, platform, handle, source_type, viltrox_fit_score) VALUES ('real-1', 'youtube', 'real_creator', 'manual', 77.5)"
    )
    conn.execute("INSERT INTO vkpi_publish_approvals (source_table, source_id, title, status) VALUES ('vkpi_reply_queue', '1', 'real', 'pending')")

    plans = seed.seed_all(conn, _ctx(seed, apply=True, staff_id=12))
    assert seed._summarize(plans) == {"insert": 13, "skipped_by_design": 1}
    conn.commit()

    counts = {table: _count(conn, table) for table in TABLES}
    assert counts == {
        "vkpi_dealer_event_candidates": 2, "vkpi_kol_pool": 2, "vkpi_kol_video_evidence": 2, "vkpi_projects": 1,
        "vkpi_comments": 3, "vkpi_publish_approvals": 3, "vkpi_report_runs": 1, "vkpi_weekly_reports": 1,
    }

    # 演示标记三层:标题前缀 / JSON 标记 / 稳定自然键
    kol = dict(conn.execute("SELECT * FROM vkpi_kol_pool WHERE handle=?", (seed.DEMO_HANDLE,)).fetchone())
    assert kol["display_name"].startswith(seed.DEMO_PREFIX) and kol["source_type"] == "demo_seed"
    assert json.loads(kol["raw_platform_data"])["is_demo"] is True and kol["viltrox_fit_score"] is None
    for table, column in (("vkpi_dealer_event_candidates", "candidate_payload_json"), ("vkpi_projects", "metadata_json"),
                          ("vkpi_comments", "raw_data_json"), ("vkpi_report_runs", "metadata_json")):
        for row in conn.execute(f"SELECT {column} AS payload FROM {table}").fetchall():
            if table == "vkpi_comments" or json.loads(row["payload"]).get("demo_seed") == seed.SEED_TAG:
                assert json.loads(row["payload"])["is_demo"] is True, table
    titles = [r[0] for r in conn.execute("SELECT title FROM vkpi_publish_approvals WHERE source_table=?", (seed.DEMO_SOURCE_TABLE,))]
    assert titles and all(t.startswith(seed.DEMO_PREFIX) for t in titles)
    assert all(r[0].startswith(seed.DEMO_PREFIX) for r in conn.execute("SELECT comment_text FROM vkpi_comments"))
    assert all(r[0].startswith(seed.DEMO_PREFIX) for r in conn.execute("SELECT video_title FROM vkpi_kol_video_evidence"))
    assert all(r[0].startswith(seed.DEMO_PREFIX) for r in conn.execute("SELECT project_name FROM vkpi_projects"))
    assert all(r[0].startswith(seed.DEMO_PREFIX) for r in conn.execute("SELECT title FROM vkpi_weekly_reports"))
    project = dict(conn.execute("SELECT * FROM vkpi_projects").fetchone())
    assert project["assigned_staff_id"] == 12 and project["is_public"] == 1 and project["source_type"] == "demo_seed"
    weekly = dict(conn.execute("SELECT * FROM vkpi_weekly_reports").fetchone())
    assert weekly["source_data_status"] == "partial" and weekly["source_is_partial"] == 1 and weekly["staff_id"] == 12
    assert weekly["period_start"] == "2026-08-24" and weekly["period_end"] == "2026-08-30"
    evidence = [dict(r) for r in conn.execute("SELECT * FROM vkpi_kol_video_evidence")]
    assert all(e["kol_pool_id"] == kol["id"] and e["source"] == "demo_seed" and e["confidence"] == "low" for e in evidence)
    candidates = [dict(r) for r in conn.execute("SELECT * FROM vkpi_dealer_event_candidates")]
    assert all(len(c["content_sha256"]) == 64 and c["id"].startswith("cand_") and len(c["id"]) == 37 for c in candidates)
    assert all(c["promotion_gate_status"] == "blocked" and c["review_status"] == "pending" for c in candidates)

    # 幂等:第二次全 exists,行数不变
    again = seed.seed_all(conn, _ctx(seed, apply=True, staff_id=12))
    assert seed._summarize(again) == {"exists": 13, "skipped_by_design": 1}
    assert {table: _count(conn, table) for table in TABLES} == counts

    # purge:dry-run 只报数;apply 只删演示行,真实行留下
    dry = seed.purge_all(conn, _ctx(seed, apply=False))
    assert {p.table: p.action for p in dry} == {table: "deleted" for table in TABLES}
    assert {table: _count(conn, table) for table in TABLES} == counts
    wet = seed.purge_all(conn, _ctx(seed, apply=True))
    assert sum(p.extra["rows"] for p in wet) == 13
    assert {table: _count(conn, table) for table in TABLES} == {
        "vkpi_dealer_event_candidates": 0, "vkpi_kol_pool": 1, "vkpi_kol_video_evidence": 0, "vkpi_projects": 0,
        "vkpi_comments": 0, "vkpi_publish_approvals": 1, "vkpi_report_runs": 0, "vkpi_weekly_reports": 0,
    }
    survivor = dict(conn.execute("SELECT handle, viltrox_fit_score FROM vkpi_kol_pool").fetchone())
    assert survivor == {"handle": "real_creator", "viltrox_fit_score": 77.5}
    assert [p.action for p in seed.purge_all(conn, _ctx(seed, apply=True))] == ["absent"] * len(TABLES)


def test_missing_tables_are_reported_honestly_not_faked(seed: Any, conn: sqlite3.Connection) -> None:
    conn.executescript("DROP TABLE vkpi_publish_approvals; DROP TABLE vkpi_kol_video_evidence;")
    plans = seed.seed_all(conn, _ctx(seed, apply=True))
    by_endpoint = {plan.endpoint: plan for plan in plans if plan.action == "table_absent"}
    assert set(by_endpoint) == {"launchpad.publish-approvals", "intelligent.ask-video-26mm-evo"}
    assert "173" in by_endpoint["launchpad.publish-approvals"].detail
    assert _count(conn, "vkpi_kol_pool") == 0  # 视频板块表缺 → 连演示创作者也不种
    purge = {p.table: p.action for p in seed.purge_all(conn, _ctx(seed, apply=True))}
    assert purge["vkpi_publish_approvals"] == "table_absent" and purge["vkpi_projects"] == "deleted"


def test_cli_defaults_to_dry_run_and_render_uses_stdout_utils(seed: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    args = seed._parse_args([])
    assert args.apply is False and args.purge is False and args.staff_id is None and args.org_id == 1 and args.json is False
    args = seed._parse_args(["--apply", "--purge", "--staff-id", "3", "--org-id", "2", "--json"])
    assert (args.apply, args.purge, args.staff_id, args.org_id, args.json) == (True, True, 3, 2, True)

    lines: list[str] = []
    payloads: list[dict[str, Any]] = []
    monkeypatch.setattr(seed, "out", lambda text="", **kw: lines.append(str(text)))
    monkeypatch.setattr(seed, "out_json", lambda payload, **kw: payloads.append(payload))
    plans = [seed.Plan("Events", "events.candidate-staging", "t", "k", "insert"), seed.Plan("Shopify", "shopify.gmv", "t", "*", "skipped_by_design", "why")]
    seed._render(plans, _ctx(seed, apply=False), purge=False, as_json=False)
    assert lines[0].startswith("[SEED/DRY-RUN]") and any("未写库" in line for line in lines) and any("— why" in line for line in lines)
    seed._render(plans, _ctx(seed, apply=True), purge=True, as_json=True)
    assert payloads[0]["mode"] == "PURGE/APPLY" and payloads[0]["summary"] == {"insert": 1, "skipped_by_design": 1}


def test_script_source_respects_redlines() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    body = source.split('"""', 2)[2]  # 跳过模块 docstring(红线声明本身提到这两个词)
    assert "viltrox_fit_score" not in body and "rule_v0" not in body
    assert "print(" not in body and "from stdout_utils import out" in source
    assert "PURGE_TARGETS" in source and "--apply" in source


@pytest.mark.pg
def test_apply_against_real_postgres_inside_rolled_back_transaction(seed: Any, pg_compat: Any) -> None:
    """真 PG:CHECK 约束(cand_ id / registry 正则 / jsonb 列)全过;事务由 fixture 回滚,零残留。"""
    ctx = seed.SeedContext(now=NOW, pg=True, staff_id=None, org_id=1, apply=True)
    plans = seed.seed_all(pg_compat, ctx)
    summary = seed._summarize(plans)
    assert summary.get("skipped_by_design") == 1 and summary.get("table_absent", 0) == 0
    assert summary.get("insert", 0) + summary.get("exists", 0) == len(plans) - 1
    demo = pg_compat.execute("SELECT id, display_name FROM vkpi_kol_pool WHERE handle=?", (seed.DEMO_HANDLE,)).fetchone()
    assert demo is not None and str(dict(demo)["display_name"]).startswith(seed.DEMO_PREFIX)
    candidates = pg_compat.execute(
        "SELECT COUNT(*) AS n FROM vkpi_dealer_event_candidates WHERE source_registry_id=?", (seed.DEMO_REGISTRY_ID,)
    ).fetchone()
    assert int(dict(candidates)["n"]) == 2
    purged = seed.purge_all(pg_compat, ctx)
    assert sum(p.extra.get("rows", 0) for p in purged) >= 13

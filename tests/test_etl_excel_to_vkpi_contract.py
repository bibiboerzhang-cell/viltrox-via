from __future__ import annotations

import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from scripts import etl_excel_to_vkpi as etl


def _row(
    *,
    row: int,
    name: str,
    stage: str,
    video: str = "",
    published: str = "",
    platform: str = "youtube",
) -> etl.ExcelRow:
    return etl.ExcelRow(
        sheet="(5.25) Demo Lens",
        excel_row=row,
        kol_name=name,
        stage_raw=stage,
        stage=etl.map_stage(stage),
        staff_name="Owner One",
        platform=platform,
        country="US",
        created_at=datetime(2026, 8, 1),
        video_cell=video,
        published_cell=published,
        channel_url="https://youtube.com/@creator",
        sku="AF 35mm",
        affiliate_id="AFF-1",
        source_columns={"红人/媒体": name},
    )


def test_legacy_facade_and_incremental_import_contract() -> None:
    required = {
        "AssignmentPlan",
        "ExcelRow",
        "PoolRecord",
        "apply_assignments",
        "apply_evidence",
        "apply_needs_scrape",
        "apply_new_pools",
        "apply_projects",
        "build_evidence_plans",
        "build_project_plans",
        "classify_account_type",
        "clean_excel_kol_name",
        "fetch_pool_records",
        "fetch_staff_map",
        "load_dotenv",
        "load_excel",
        "match_kol_to_pool",
        "merge_assignments",
        "normalize_name",
        "project_name",
        "project_uid",
        "slugify",
        "text",
    }
    assert not {name for name in required if not hasattr(etl, name)}

    command = (
        "import sys; sys.path.insert(0, 'scripts'); "
        "import etl_promo_plan_incremental as incremental; "
        "assert incremental.legacy.project_uid('Demo') == 'EXCEL-demo'"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", command],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_matching_merge_and_evidence_golden_contract() -> None:
    duplicate_url = "https://youtube.com/watch?v=golden"
    rows = [
        _row(row=2, name="Creator One", stage="已联系-待回复", video=duplicate_url),
        _row(
            row=3,
            name="Creator.One",
            stage="终审通过",
            video=duplicate_url,
            published="https://photographyblog.com/reviews/demo",
        ),
        _row(row=4, name="Fresh Media【MEDIA】", stage="目标-待联系", platform="media"),
        _row(row=5, name="Trainer【WORKSHOP】", stage="目标-待联系"),
    ]
    pools = [
        etl.PoolRecord(
            id=7,
            handle="creator.one",
            display_name="Creator One",
            platform="youtube",
        )
    ]

    assignments, report = etl.merge_assignments(
        {"(5.25) Demo Lens": rows}, pools, {"ownerone": 11}
    )

    assert len(assignments) == 2
    exact = next(plan for plan in assignments if plan.kol_pool_id == 7)
    new_media = next(plan for plan in assignments if plan.kol_pool_id < 0)
    assert exact.stage == "reviewed"
    assert exact.staff_id == 11
    assert exact.is_placeholder_tracking is True
    assert [row.excel_row for row in exact.rows] == [2, 3]
    assert exact.metadata["merged_from_excel_rows"] == [2, 3]
    assert new_media.platform == "media"
    assert report["duplicate_groups"] == 1
    assert report["duplicate_extra_rows"] == 1
    assert report["stats"]["exact"] == 2
    assert report["stats"]["unmatched_new_pool_rows"] == 1
    assert report["stats"]["unmatched_workshop_skipped"] == 1
    assert report["new_pool_plans"][0]["dashboard_account_type"] == "media"

    evidence, stats = etl.build_evidence_plans(assignments)
    assert [item["content_url"] for item in evidence] == [
        duplicate_url,
        "https://photographyblog.com/reviews/demo",
    ]
    assert [item["evidence_type"] for item in evidence] == [
        "video",
        "media_article",
    ]
    assert stats[("回片链接", "duplicate_url")] == 1
    assert stats[("内容发布链接", "media_domain")] == 1


class RecordingCursor:
    def __init__(self, returned_ids: list[int] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.returned_ids = iter(returned_ids or [])
        self.rowcount = 4

    def execute(self, query: str, params: Any = None) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> tuple[int]:
        return (next(self.returned_ids),)


def test_writer_sql_preserves_idempotency_and_stage_monotonicity() -> None:
    project = {
        "sheet": "Demo",
        "project_uid": "EXCEL-demo",
        "project_name": "Demo",
        "product_sku": "AF 35mm",
        "product_name": "Demo",
        "platform": "youtube",
        "assigned_staff_id": 1,
        "created_by_staff_id": 1,
        "source_type": "excel_promo_plan",
        "metadata_json": "{}",
    }
    pool = {
        "temp_id": -1,
        "pool_uid": "EXCEL-NEW-demo",
        "handle": "demo",
        "display_name": "Demo",
        "platform": "youtube",
        "source_type": "excel_promo_plan_new",
        "source_ref": "excel:Demo:2",
        "sync_status": "imported",
        "dashboard_account_type": "kol",
        "dashboard_tier": None,
        "followers": None,
    }
    assignment = etl.AssignmentPlan(
        sheet="Demo",
        project_key="EXCEL-demo",
        project_id=101,
        kol_pool_id=201,
        stage="reviewed",
        stage_raw="终审通过",
        staff_id=1,
        platform="youtube",
        country="US",
        created_at=datetime(2026, 8, 1),
        source_ref="excel:Demo:2",
        tracking_number="UPS-FAKE-0201-0101-0801",
        is_placeholder_tracking=True,
        metadata={"excel_row": 2},
    )
    evidence = {
        "kol_pool_id": 201,
        "project_id": 101,
        "content_url": "https://youtube.com/watch?v=golden",
        "platform": "youtube",
        "source": "excel_huipian",
        "source_ref": "excel:Demo:2:回片链接",
        "confidence": "high",
        "evidence_type": "video",
        "posted_at": datetime(2026, 8, 1).date(),
        "created_at": datetime(2026, 8, 1),
    }

    cursor = RecordingCursor([101, 201])
    assert etl.apply_projects(cursor, [project]) == {"Demo": 101}
    assert etl.apply_new_pools(cursor, [pool]) == {-1: 201}
    etl.apply_assignments(cursor, [assignment])
    etl.apply_evidence(cursor, [evidence])
    assert etl.apply_needs_scrape(cursor) == 4

    queries = [query for query, _params in cursor.calls]
    assert "ON CONFLICT (project_uid) DO UPDATE SET" in queries[0]
    assert "ON CONFLICT (platform, handle) DO UPDATE SET" in queries[1]
    assert "ON CONFLICT (project_id, kol_pool_id) DO UPDATE SET" in queries[2]
    assert "CASE WHEN" in queries[2]
    assert "COALESCE(EXCLUDED.tracking_number" in queries[2]
    assert "ON CONFLICT (content_url) DO NOTHING" in queries[3]
    assert "has_video_evidence = FALSE" in queries[4]


class ContextCursor:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __enter__(self) -> ContextCursor:
        self.events.append("cursor_enter")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.events.append("cursor_exit")


class ContextConnection:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __enter__(self) -> ContextConnection:
        self.events.append("connection_enter")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.events.append("connection_exit")

    def cursor(self) -> ContextCursor:
        return ContextCursor(self.events)


@pytest.mark.parametrize("commit", [False, True])
def test_main_keeps_one_connection_transaction_and_dry_run_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    commit: bool,
) -> None:
    excel = tmp_path / "fixture.xlsx"
    excel.write_bytes(b"fixture is intercepted before parsing")
    events: list[str] = []
    connection = ContextConnection(events)
    project = {
        "sheet": "Demo",
        "project_uid": "EXCEL-demo",
        "project_name": "Demo",
    }
    report = {
        "stats": Counter(),
        "workshop_skipped": [],
        "new_pool_plans": [],
    }

    monkeypatch.setattr(etl, "load_excel", lambda *_args: ([], [], {"Demo": []}))
    monkeypatch.setattr(etl, "connect", lambda: connection)
    monkeypatch.setattr(etl, "fetch_pool_records", lambda _conn: [])
    monkeypatch.setattr(etl, "fetch_staff_map", lambda _conn: {})
    monkeypatch.setattr(etl, "merge_assignments", lambda *_args: ([], report))
    monkeypatch.setattr(etl, "build_project_plans", lambda *_args: [project])
    monkeypatch.setattr(etl, "build_evidence_plans", lambda _rows: ([], Counter()))
    monkeypatch.setattr(etl, "fetch_existing_evidence_urls", lambda _conn: set())
    monkeypatch.setattr(etl, "fetch_active_pool_ids", lambda _conn: set())
    monkeypatch.setattr(etl, "fetch_pool_details", lambda *_args: {})
    monkeypatch.setattr(etl, "print_report", lambda **_kwargs: events.append("report"))
    monkeypatch.setattr(
        etl, "apply_projects", lambda _cur, _rows: events.append("projects") or {"Demo": 101}
    )
    monkeypatch.setattr(
        etl, "apply_new_pools", lambda _cur, _rows: events.append("pools") or {}
    )
    monkeypatch.setattr(
        etl, "apply_assignments", lambda _cur, _rows: events.append("assignments")
    )
    monkeypatch.setattr(
        etl, "apply_evidence", lambda _cur, _rows: events.append("evidence")
    )
    monkeypatch.setattr(
        etl, "apply_needs_scrape", lambda _cur: events.append("needs_scrape") or 0
    )
    argv = ["etl_excel_to_vkpi.py", "--excel", os.fspath(excel)]
    if commit:
        argv.append("--commit")
    monkeypatch.setattr(sys, "argv", argv)

    assert etl.main() == 0

    assert events[0] == "connection_enter"
    assert events[-1] == "connection_exit"
    assert events.count("connection_enter") == 1
    assert events.count("connection_exit") == 1
    if commit:
        assert events == [
            "connection_enter",
            "cursor_enter",
            "projects",
            "pools",
            "assignments",
            "evidence",
            "needs_scrape",
            "cursor_exit",
            "report",
            "connection_exit",
        ]
    else:
        assert events == ["connection_enter", "report", "connection_exit"]


def test_cli_shape_is_unchanged() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "etl_excel_to_vkpi.py"
    completed = subprocess.run(
        [sys.executable, "-B", os.fspath(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    for option in ("--excel", "--commit", "--dry-run", "--evidence-only"):
        assert option in completed.stdout

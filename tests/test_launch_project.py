"""Tests for N3 新品 Launch Project(加性).

覆盖:
- launch project 字段 schema(required / repeated 字段齐全)。
- normalize_launch_metadata 清洗(扁平 + 嵌套 + 缺 required 抛错)。
- create_launch_project dry_run 默认不写库;dry_run=False 真正落 project 且
  source_type='launch' + launch 元数据落 metadata_json.launch。
- generate_launch_plan 复用现有 new_launch_match 出候选 + 生成内容验证任务 / 观察窗口占位。

红线:零触 viltrox_fit_score。
"""
from __future__ import annotations

import json

import pytest

from app.db.connection import get_conn
from app.domains.projects import launch_project as lp
from app.services.vkpi.schema import ensure_vkpi_schema


MARKER = "VKPI-LAUNCH-PROJECT-TEST"


@pytest.fixture(autouse=True)
def _ensure_schema():
    ensure_vkpi_schema()
    yield


@pytest.fixture
def cleanup_projects():
    conn = get_conn()

    def _cleanup() -> None:
        rows = conn.execute(
            "SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ?",
            (f"{MARKER}%", f"%{MARKER}%"),
        ).fetchall()
        for row in rows:
            pid = int(row["id"])
            conn.execute("DELETE FROM vkpi_project_stage_events WHERE project_id=?", (pid,))
            conn.execute("DELETE FROM vkpi_kol_claims WHERE project_id=?", (pid,))
            conn.execute("DELETE FROM vkpi_projects WHERE id=?", (pid,))
        conn.commit()

    _cleanup()
    yield
    _cleanup()


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------
def test_launch_project_schema_has_required_and_repeated_fields():
    schema = lp.launch_project_schema()
    assert schema["project_type"] == "launch"
    assert schema["source_type"] == "launch"
    keys = {field["key"] for field in schema["fields"]}
    # 所有产品要求的字段都要在 schema 里。
    assert {
        "sku",
        "price_band",
        "target_countries",
        "selling_points",
        "competitors",
        "target_audience",
        "validation_hypotheses",
    } <= keys
    assert "sku" in schema["required_fields"]
    # repeated 字段标记正确。
    repeated = {field["key"] for field in schema["fields"] if field.get("repeated")}
    assert {"target_countries", "selling_points", "competitors", "validation_hypotheses"} <= repeated


# ---------------------------------------------------------------------------
# normalize_launch_metadata
# ---------------------------------------------------------------------------
def test_normalize_flat_input_coerces_repeated_fields():
    meta = lp.normalize_launch_metadata(
        {
            "sku": "AF-50MM",
            "price_band": "99-149 USD",
            "target_countries": "US, DE, JP",
            "selling_points": ["sharp", "light", "sharp"],
            "target_audience": "hybrid shooters",
            "validation_hypotheses": "demand exists",
        }
    )
    assert meta["sku"] == "AF-50MM"
    assert meta["target_countries"] == ["US", "DE", "JP"]
    # 去重保序。
    assert meta["selling_points"] == ["sharp", "light"]
    assert meta["competitors"] == []
    assert meta["validation_hypotheses"] == ["demand exists"]


def test_normalize_nested_input():
    meta = lp.normalize_launch_metadata({"launch": {"sku": "X1", "competitors": ["Sony", "Sigma"]}})
    assert meta["sku"] == "X1"
    assert meta["competitors"] == ["Sony", "Sigma"]


def test_normalize_missing_required_raises():
    with pytest.raises(ValueError):
        lp.normalize_launch_metadata({"price_band": "low"})


# ---------------------------------------------------------------------------
# create_launch_project
# ---------------------------------------------------------------------------
def test_create_launch_project_dry_run_does_not_write(cleanup_projects):
    conn = get_conn()
    before = conn.execute(
        "SELECT COUNT(*) AS c FROM vkpi_projects WHERE project_name LIKE ?",
        (f"%{MARKER}%",),
    ).fetchone()["c"]

    result = lp.create_launch_project(
        {"project_name": f"{MARKER} dry", "sku": "DRY-SKU", "target_countries": "US"},
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["would_create"] is True
    assert result["source_type"] == "launch"
    assert result["launch"]["sku"] == "DRY-SKU"
    # metadata 预览里 launch 命名空间存在。
    assert result["metadata_json_preview"]["launch"]["sku"] == "DRY-SKU"
    assert result["metadata_json_preview"]["project_type"] == "launch"

    after = conn.execute(
        "SELECT COUNT(*) AS c FROM vkpi_projects WHERE project_name LIKE ?",
        (f"%{MARKER}%",),
    ).fetchone()["c"]
    assert after == before  # 真的没写库


def test_create_launch_project_persists_when_not_dry_run(cleanup_projects):
    result = lp.create_launch_project(
        {
            "project_uid": f"{MARKER}-001",
            "project_name": f"{MARKER} real",
            "sku": "REAL-SKU",
            "price_band": "199-299",
            "target_countries": ["US", "CA"],
            "selling_points": ["fast AF"],
            "validation_hypotheses": ["creators want fast AF"],
        },
        dry_run=False,
    )
    assert result["dry_run"] is False
    assert result["source_type"] == "launch"
    project_id = int(result["id"])
    assert project_id > 0

    row = get_conn().execute(
        "SELECT source_type, metadata_json FROM vkpi_projects WHERE id=?",
        (project_id,),
    ).fetchone()
    assert str(row["source_type"]) == "launch"
    meta = json.loads(row["metadata_json"])
    assert meta["project_type"] == "launch"
    assert meta["launch"]["sku"] == "REAL-SKU"
    assert meta["launch"]["target_countries"] == ["US", "CA"]


# ---------------------------------------------------------------------------
# generate_launch_plan
# ---------------------------------------------------------------------------
def test_generate_launch_plan_reuses_match_and_builds_placeholders(cleanup_projects):
    created = lp.create_launch_project(
        {
            "project_uid": f"{MARKER}-PLAN",
            "project_name": f"{MARKER} plan",
            "sku": "PLAN-SKU",
            "target_countries": ["US", "DE"],
            "validation_hypotheses": ["h1", "h2"],
        },
        dry_run=False,
    )
    project_id = int(created["id"])

    plan = lp.generate_launch_plan(project_id, candidate_limit=5)
    assert plan["project_id"] == project_id
    assert plan["project_type"] == "launch"
    assert plan["product_query"] == "PLAN-SKU"

    # KOL 候选块永远存在(就绪→有 items,未就绪→available=False 优雅降级)。
    candidates = plan["kol_candidates"]
    assert "available" in candidates
    assert isinstance(candidates["items"], list)

    # 内容验证任务:两条假设 → 两条任务占位。
    tasks = plan["content_validation_tasks"]
    assert len(tasks) == 2
    assert all(t["status"] == "placeholder" for t in tasks)
    assert {t["hypothesis"] for t in tasks} == {"h1", "h2"}

    # 观察窗口:两个目标国家 → 两个窗口占位。
    windows = plan["observation_windows"]
    assert len(windows) == 2
    assert {w["market"] for w in windows} == {"US", "DE"}
    assert all(w["status"] == "placeholder" for w in windows)

    assert plan["summary"]["content_task_count"] == 2
    assert plan["summary"]["observation_window_count"] == 2


def test_generate_launch_plan_missing_project_raises():
    with pytest.raises(ValueError):
        lp.generate_launch_plan(999_000_111)

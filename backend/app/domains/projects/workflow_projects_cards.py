"""ProjectCard stage-bucket constants + deliverable enrichment for V-KPI workflow.

Moved verbatim from workflow_projects.py (behavior-preserving extraction).
"""
from __future__ import annotations

from typing import Any

PROJECT_CARD_STAGE_KEYS = [
    "discovery",
    "contacted",
    "replied",
    "agreed",
    "shipped",
    "received",
    "published",
    "measured",
    "closed",
]

ASSIGNMENT_STAGE_TO_CARD_STAGE = {
    "discovered": "discovery",
    "contacted": "contacted",
    "replied": "replied",
    "agreed": "agreed",
    "device_sent": "shipped",
    "arrived": "received",
    "content_posted": "published",
    "reviewed": "measured",
    "closed": "closed",
    "churned": "closed",
    # 双词表案读侧兼容:历史误写的项目词表行照常入桶(写侧已归一,此为存量保底)
    "shipped": "shipped",
    "received": "received",
    "published": "published",
    "measured": "measured",
}

CARD_STAGE_LABELS = {
    "discovered": "1.发现",
    "contacted": "2.已联系",
    "replied": "3.已回复",
    "agreed": "4.已合作",
    "device_sent": "5.已发货",
    "arrived": "6.已到货",
    "content_posted": "7.已发布",
    "reviewed": "8.已统计",
    "closed": "9.已关闭",
    "churned": "9.已关闭",
}

CARD_STAGE_INDEX = {key: index for index, key in enumerate(PROJECT_CARD_STAGE_KEYS)}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _empty_stage_counts() -> dict[str, int]:
    return {key: 0 for key in PROJECT_CARD_STAGE_KEYS}


def _enrich_project_card_fields(conn, projects: list[dict[str, Any]]) -> None:
    """Attach deliverable ProjectCard fields from assignment/kol_pool truth."""
    project_ids = [int(project.get("id") or 0) for project in projects if int(project.get("id") or 0)]
    if not project_ids:
        return
    placeholders = ",".join("?" for _ in project_ids)
    rows = conn.execute(
        f"""
        SELECT
            a.project_id,
            COALESCE(a.stage, '') AS stage,
            COALESCE(kp.platform, '') AS platform,
            COALESCE(kp.has_video_evidence, FALSE) AS has_video_evidence,
            COUNT(DISTINCT a.kol_pool_id) AS kol_count
        FROM vkpi_project_kol_assignments a
        LEFT JOIN vkpi_kol_pool kp ON kp.id = a.kol_pool_id
        WHERE a.project_id IN ({placeholders})
        GROUP BY a.project_id, COALESCE(a.stage, ''), COALESCE(kp.platform, ''), COALESCE(kp.has_video_evidence, FALSE)
        """,
        project_ids,
    ).fetchall()
    breakdowns: dict[int, dict[str, Any]] = {
        project_id: {
            "platforms": set(),
            "stage_counts": _empty_stage_counts(),
            "raw_stage_counts": {},
            "kol_count": 0,
            "kol_with_evidence": 0,
            "published_count": 0,
            "churned_count": 0,
            "stage_index_sum": 0,
            "stage_index_count": 0,
        }
        for project_id in project_ids
    }
    for row in rows:
        project_id = int(row["project_id"] or 0)
        data = breakdowns.get(project_id)
        if not data:
            continue
        count = int(row["kol_count"] or 0)
        raw_stage = str(row["stage"] or "").strip().lower()
        platform = str(row["platform"] or "").strip().lower()
        if platform:
            data["platforms"].add(platform)
        data["kol_count"] += count
        if bool(row["has_video_evidence"]):
            data["kol_with_evidence"] += count
        if raw_stage in ("content_posted", "published"):
            data["published_count"] += count
        if raw_stage == "churned":
            data["churned_count"] += count
        data["raw_stage_counts"][raw_stage] = data["raw_stage_counts"].get(raw_stage, 0) + count
        card_stage = ASSIGNMENT_STAGE_TO_CARD_STAGE.get(raw_stage)
        if card_stage:
            data["stage_counts"][card_stage] += count
            if raw_stage != "churned":
                data["stage_index_sum"] += CARD_STAGE_INDEX.get(card_stage, 0) * count
                data["stage_index_count"] += count

    for project in projects:
        project_id = int(project.get("id") or 0)
        data = breakdowns.get(project_id)
        if not data:
            project.update({
                "platforms": [],
                "stage_counts": _empty_stage_counts(),
                "published_count": 0,
                "health_score": 0,
                "health_basis": "simplified_no_reliable_time",
                "health_breakdown": {"output_score": 0, "progress_score": 0, "output_rate": 0, "avg_stage_index": 0},
                "needs_followup_count": None,
                "overdue_count": None,
                "current_focus": "暂无 KOL",
                "bottleneck": "暂无 KOL",
                "churned_count": 0,
            })
            continue
        kol_count = int(data["kol_count"] or 0)
        output_rate = (int(data["kol_with_evidence"] or 0) / kol_count) if kol_count else 0
        avg_stage_index = (float(data["stage_index_sum"]) / int(data["stage_index_count"])) if int(data["stage_index_count"] or 0) else 0
        output_score = output_rate * 60
        progress_score = (avg_stage_index / 8) * 40
        health_score = int(round(_clamp(output_score + progress_score, 0, 100)))
        bottleneck_stage = ""
        bottleneck_count = 0
        for stage, count in data["raw_stage_counts"].items():
            if stage in {"content_posted", "churned", "reviewed", "closed"}:
                continue
            if int(count or 0) > bottleneck_count:
                bottleneck_stage = stage
                bottleneck_count = int(count or 0)
        bottleneck = f"{CARD_STAGE_LABELS.get(bottleneck_stage, bottleneck_stage or '当前阶段')} {bottleneck_count}人" if bottleneck_count else "暂无瓶颈"
        project.update({
            "platforms": sorted(data["platforms"]),
            "stage_counts": data["stage_counts"],
            "published_count": int(data["published_count"] or 0),
            "health_score": health_score,
            "health_basis": "simplified_no_reliable_time",
            "health_breakdown": {
                "output_score": round(output_score, 1),
                "progress_score": round(progress_score, 1),
                "output_rate": round(output_rate, 4),
                "avg_stage_index": round(avg_stage_index, 2),
            },
            "needs_followup_count": None,
            "overdue_count": None,
            "current_focus": bottleneck,
            "bottleneck": bottleneck,
            "churned_count": int(data["churned_count"] or 0),
        })

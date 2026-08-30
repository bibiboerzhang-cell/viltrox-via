"""Project and KOL-assignment read stages for the project detail model."""
from __future__ import annotations

from typing import Any, Callable


ASSIGNMENT_STAGE_RANK_SQL = """
    CASE a.stage
        WHEN 'reviewed' THEN 1
        WHEN 'measured' THEN 1
        WHEN 'content_posted' THEN 2
        WHEN 'published' THEN 2
        WHEN 'device_sent' THEN 3
        WHEN 'shipped' THEN 3
        WHEN 'received' THEN 3
        WHEN 'agreed' THEN 4
        WHEN 'replied' THEN 5
        WHEN 'contacted' THEN 6
        WHEN 'discovered' THEN 7
        WHEN 'churned' THEN 8
        ELSE 9
    END
"""
ASSIGNMENT_SORT_NAME_SQL = "COALESCE(kp.display_name, '')"
ASSIGNMENT_INT_FIELDS = (
    "assignment_id",
    "project_id",
    "kol_pool_id",
    "assigned_staff_id",
    "followers",
    "video_evidence_count",
    "evidence_count",
    "total_views",
    "total_likes",
    "total_comments",
)


def fetch_project_row(conn: Any, project_id: int) -> Any:
    """Fetch the project shell and its aggregate assignment/evidence metrics."""
    return conn.execute(
        """
        SELECT p.*,
               CASE
                   WHEN COALESCE(pa.kol_count, 0) > 1 THEN CAST(pa.kol_count AS TEXT) || ' KOL'
                   ELSE COALESCE(pk.display_name, '')
               END AS kol_name,
               CASE
                   WHEN COALESCE(pa.kol_count, 0) > 1 THEN 'multi'
                   ELSE COALESCE(pk.platform, '')
               END AS kol_platform,
               pk.handle AS handle,
               pk.avatar_url AS kol_avatar,
               pa.primary_kol_pool_id AS kol_pool_id,
               COALESCE(pa.kol_count, 0) AS kol_count,
               COALESCE(pa.assignment_count, 0) AS assignment_count,
               COALESCE(pa.kol_with_evidence, 0) AS kol_with_evidence,
               COALESCE(ev.evidence_count, 0) AS evidence_count,
               COALESCE(ev.evidence_kol_count, 0) AS evidence_kol_count,
               COALESCE(ev.total_views, 0) AS total_views,
               COALESCE(s.name, assignment_owner.name) AS staff_name
        FROM vkpi_projects p
        LEFT JOIN (
            SELECT
                a.project_id,
                COUNT(*) AS assignment_count,
                COUNT(DISTINCT a.kol_pool_id) AS kol_count,
                COUNT(DISTINCT CASE WHEN kp.has_video_evidence THEN a.kol_pool_id END) AS kol_with_evidence,
                MIN(a.kol_pool_id) AS primary_kol_pool_id
            FROM vkpi_project_kol_assignments a
            LEFT JOIN vkpi_kol_pool kp ON kp.id = a.kol_pool_id
            WHERE a.project_id=?
            GROUP BY a.project_id
        ) pa ON pa.project_id = p.id
        LEFT JOIN (
            SELECT project_id, assigned_staff_id
            FROM (
                SELECT
                    project_id,
                    assigned_staff_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY project_id
                        ORDER BY COUNT(*) DESC, assigned_staff_id ASC
                    ) AS rn
                FROM vkpi_project_kol_assignments
                WHERE project_id=? AND assigned_staff_id IS NOT NULL
                GROUP BY project_id, assigned_staff_id
            ) ranked_assignment_staff
            WHERE rn = 1
        ) assignment_owner_pick ON assignment_owner_pick.project_id = p.id
        LEFT JOIN vkpi_kol_pool pk ON pk.id = pa.primary_kol_pool_id
        LEFT JOIN (
            SELECT
                project_id,
                COUNT(*) AS evidence_count,
                COUNT(DISTINCT kol_pool_id) AS evidence_kol_count,
                COALESCE(SUM(COALESCE(view_count, 0)), 0) AS total_views
            FROM vkpi_kol_video_evidence
            WHERE project_id=?
            GROUP BY project_id
        ) ev ON ev.project_id = p.id
        LEFT JOIN staff st ON st.id = p.assigned_staff_id
        LEFT JOIN users s ON s.id = st.user_id
        LEFT JOIN staff assignment_st ON assignment_st.id = assignment_owner_pick.assigned_staff_id
        LEFT JOIN users assignment_owner ON assignment_owner.id = assignment_st.user_id
        WHERE p.id=?
        """,
        (int(project_id), int(project_id), int(project_id), int(project_id)),
    ).fetchone()


def _cursor_clause(cursor_values: tuple[int, str, int] | None) -> tuple[str, list[Any]]:
    if cursor_values is None:
        return "", []
    cursor_rank, cursor_name, cursor_id = cursor_values
    return (
        f"""
        AND (
            ({ASSIGNMENT_STAGE_RANK_SQL}) > ?
            OR (({ASSIGNMENT_STAGE_RANK_SQL}) = ? AND (
                ({ASSIGNMENT_SORT_NAME_SQL}) > ?
                OR (({ASSIGNMENT_SORT_NAME_SQL}) = ? AND a.id > ?)
            ))
        )
        """,
        [cursor_rank, cursor_rank, cursor_name, cursor_name, cursor_id],
    )


def fetch_assignment_rows(
    conn: Any,
    project_id: int,
    *,
    assignment_limit: int | None,
    cursor_values: tuple[int, str, int] | None,
) -> list[dict[str, Any]]:
    """Fetch the legacy full set or one ordered keyset assignment page."""
    assignment_cursor_sql, cursor_params = _cursor_clause(cursor_values)
    assignment_params: list[Any] = [int(project_id), int(project_id), int(project_id), int(project_id)]
    assignment_params.extend(cursor_params)
    assignment_limit_sql = ""
    if assignment_limit is not None:
        assignment_limit_sql = "LIMIT ?"
        assignment_params.append(assignment_limit + 1)
    return [
        dict(item)
        for item in conn.execute(
            f"""
            SELECT
                a.id AS assignment_id,
                a.project_id,
                a.kol_pool_id,
                a.stage,
                a.stage_status,
                a.assigned_staff_id,
                a.tracking_number,
                a.is_placeholder_tracking,
                a.source,
                a.source_ref,
                a.excel_progress,
                a.metadata_json,
                a.created_at,
                a.updated_at,
                assigned_user.name AS assigned_staff_name,
                assigned_user.email AS assigned_staff_email,
                kp.handle,
                kp.display_name AS kol_name,
                kp.display_name,
                kp.platform AS kol_platform,
                kp.platform,
                kp.profile_url,
                kp.avatar_url,
                kp.country,
                kp.followers,
                kp.dashboard_account_type,
                kp.dashboard_tier,
                kp.has_video_evidence,
                kp.video_evidence_count,
                COALESCE(ev.evidence_count, 0) AS evidence_count,
                COALESCE(ev.total_views, 0) AS total_views,
                COALESCE(ev.total_likes, 0) AS total_likes,
                COALESCE(ev.total_comments, 0) AS total_comments,
                ev.latest_publish_date,
                top_ev.content_url AS evidence_url,
                top_ev.content_url AS video_url,
                COALESCE(top_ev.title, top_ev.video_title, top_ev.content_url) AS evidence_title,
                top_ev.thumbnail_url AS evidence_thumbnail_url,
                top_ev.publish_date AS evidence_publish_date,
                latest_ev.content_url AS latest_evidence_url,
                latest_ev.content_url AS latest_video_url,
                COALESCE(latest_ev.title, latest_ev.video_title, latest_ev.content_url) AS latest_evidence_title,
                latest_ev.thumbnail_url AS latest_evidence_thumbnail_url,
                latest_ev.publish_date AS latest_evidence_publish_date,
                ({ASSIGNMENT_STAGE_RANK_SQL}) AS assignment_stage_rank,
                ({ASSIGNMENT_SORT_NAME_SQL}) AS assignment_sort_name
            FROM vkpi_project_kol_assignments a
            LEFT JOIN vkpi_kol_pool kp ON kp.id = a.kol_pool_id
            LEFT JOIN staff assigned_staff ON assigned_staff.id = a.assigned_staff_id
            LEFT JOIN users assigned_user ON assigned_user.id = assigned_staff.user_id
            LEFT JOIN (
                SELECT
                    project_id,
                    kol_pool_id,
                    COUNT(*) AS evidence_count,
                    COALESCE(SUM(COALESCE(view_count, 0)), 0) AS total_views,
                    COALESCE(SUM(COALESCE(like_count, 0)), 0) AS total_likes,
                    COALESCE(SUM(COALESCE(comment_count, 0)), 0) AS total_comments,
                    MAX(publish_date) AS latest_publish_date
                FROM vkpi_kol_video_evidence
                WHERE project_id=?
                GROUP BY project_id, kol_pool_id
            ) ev ON ev.project_id = a.project_id AND ev.kol_pool_id = a.kol_pool_id
            LEFT JOIN (
                SELECT DISTINCT ON (project_id, kol_pool_id)
                    project_id,
                    kol_pool_id,
                    content_url,
                    title,
                    video_title,
                    thumbnail_url,
                    view_count,
                    publish_date,
                    id
                FROM vkpi_kol_video_evidence
                WHERE project_id=? AND COALESCE(evidence_type, 'video') = 'video'
                ORDER BY
                    project_id,
                    kol_pool_id,
                    COALESCE(view_count, 0) DESC,
                    publish_date DESC NULLS LAST,
                    id DESC
            ) top_ev ON top_ev.project_id = a.project_id AND top_ev.kol_pool_id = a.kol_pool_id
            LEFT JOIN (
                SELECT DISTINCT ON (project_id, kol_pool_id)
                    project_id,
                    kol_pool_id,
                    content_url,
                    title,
                    video_title,
                    thumbnail_url,
                    publish_date,
                    id
                FROM vkpi_kol_video_evidence
                WHERE project_id=? AND COALESCE(evidence_type, 'video') = 'video'
                ORDER BY
                    project_id,
                    kol_pool_id,
                    publish_date DESC NULLS LAST,
                    id DESC
            ) latest_ev ON latest_ev.project_id = a.project_id AND latest_ev.kol_pool_id = a.kol_pool_id
            WHERE a.project_id=?
            {assignment_cursor_sql}
            ORDER BY
                ({ASSIGNMENT_STAGE_RANK_SQL}) ASC,
                ({ASSIGNMENT_SORT_NAME_SQL}) ASC,
                a.id ASC
            {assignment_limit_sql}
            """,
            tuple(assignment_params),
        ).fetchall()
    ]


def prepare_assignment_page(
    assignment_rows: list[dict[str, Any]],
    assignment_limit: int | None,
    *,
    to_canonical: Callable[[Any, Any], str],
    stage_label_zh: Callable[[str], str],
) -> tuple[list[dict[str, Any]], bool]:
    has_more = bool(assignment_limit is not None and len(assignment_rows) > assignment_limit)
    participating_kols = assignment_rows[:assignment_limit] if assignment_limit is not None else assignment_rows
    for item in participating_kols:
        item.pop("assignment_stage_rank", None)
        item.pop("assignment_sort_name", None)
        for key in ASSIGNMENT_INT_FIELDS:
            item[key] = int(item.get(key) or 0)
        item["has_video_evidence"] = bool(item.get("has_video_evidence"))
        item["canonical_stage"] = to_canonical(item.get("stage"), item.get("stage_status", ""))
        item["canonical_stage_label"] = stage_label_zh(item["canonical_stage"])
    return participating_kols, has_more


def apply_full_assignment_summary(project: dict[str, Any], participating_kols: list[dict[str, Any]]) -> None:
    """Reproduce the full-response assignment metrics and display fallback."""
    if not participating_kols:
        return
    project["kol_count"] = len(
        {
            int(item.get("kol_pool_id") or 0)
            for item in participating_kols
            if int(item.get("kol_pool_id") or 0)
        }
    )
    project["kol_with_evidence"] = sum(
        1 for item in participating_kols if bool(item.get("has_video_evidence"))
    )
    project["evidence_count"] = sum(int(item.get("evidence_count") or 0) for item in participating_kols)
    project["total_views"] = sum(int(item.get("total_views") or 0) for item in participating_kols)
    if project["kol_count"] > 1:
        project["kol_name"] = f"{project['kol_count']} KOL"
        project["kol_platform"] = "multi"
        return
    only = participating_kols[0]
    project["kol_name"] = only.get("kol_name") or project.get("kol_name") or ""
    project["kol_platform"] = only.get("kol_platform") or project.get("kol_platform") or ""

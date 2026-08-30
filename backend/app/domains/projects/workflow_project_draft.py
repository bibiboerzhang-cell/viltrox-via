"""Smart-search session to project-draft orchestration.

The workflow-projects facade injects its operations on every call so existing
authorization, database, and test monkeypatch boundaries remain intact.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class DraftOperations:
    get_session: Callable[..., dict[str, Any]]
    update_session_result_summary: Callable[..., Any]
    to_int: Callable[..., int]
    staff_id: Callable[[dict[str, Any] | None], int]
    loads: Callable[[Any], Any]
    get_conn: Callable[[], Any]
    lock_owned_session: Callable[..., None]
    create_project: Callable[..., dict[str, Any]]
    add_project_kols: Callable[..., dict[str, Any]]
    estimate_cost_for_kols: Callable[..., dict[str, Any]]
    warning: Callable[..., Any]


def _approved_kol_pool_ids(
    session: dict[str, Any],
    *,
    to_int: Callable[..., int],
) -> list[int]:
    approved_ids: list[int] = []
    approved_seen: set[int] = set()
    for value in session.get("approved_kol_ids") or []:
        kid = to_int(value)
        if kid and kid not in approved_seen:
            approved_seen.add(kid)
            approved_ids.append(kid)

    # The server-approved set is the only source. Keep the second guard as an
    # explicit fail-closed contract even though the current source is already
    # normalized above.
    raw_ids = approved_ids
    kol_pool_ids: list[int] = []
    seen: set[int] = set()
    for value in raw_ids:
        kid = to_int(value)
        if kid and kid not in seen:
            seen.add(kid)
            kol_pool_ids.append(kid)
    if not kol_pool_ids:
        raise ValueError("no approved KOLs on this session; approve candidates first")
    unapproved_ids = [kid for kid in kol_pool_ids if kid not in approved_seen]
    if unapproved_ids:
        raise ValueError(
            "kol_pool_ids must be a subset of approved session candidates: "
            + ",".join(str(kid) for kid in unapproved_ids)
        )
    return kol_pool_ids


def _draft_context(
    *,
    session_id: int,
    session: dict[str, Any],
    body: dict[str, Any],
    kol_pool_ids: list[int],
) -> dict[str, Any]:
    input_payload = (
        session.get("input_payload")
        if isinstance(session.get("input_payload"), dict)
        else {}
    )
    result_summary = (
        session.get("result_summary")
        if isinstance(session.get("result_summary"), dict)
        else {}
    )
    session_plan: dict[str, Any] = {}
    for source in (
        result_summary.get("llm_query_plan"),
        input_payload.get("llm_query_plan"),
        input_payload,
    ):
        if isinstance(source, dict) and (
            source.get("product_positioning") or source.get("target_persona")
        ):
            session_plan = source
            break
    query_text = str(session.get("query_text") or "").strip()
    positioning = str(
        body.get("product_positioning")
        or session_plan.get("product_positioning")
        or ""
    ).strip()
    persona = str(
        body.get("target_persona")
        or session_plan.get("target_persona")
        or query_text
    ).strip()
    product_name = str(body.get("product_name") or "").strip()
    project_name = str(body.get("project_name") or "").strip()
    if not project_name:
        base = product_name or query_text or f"Smart Search #{int(session_id)}"
        project_name = f"{base} · 合作草案"[:200]
    brief = {
        "product_positioning": positioning,
        "target_persona": persona,
        "query_text": query_text,
        "source": "smart_search",
        "search_session_id": int(session_id),
        "approved_kol_count": len(kol_pool_ids),
    }
    return {
        "input_payload": input_payload,
        "result_summary": result_summary,
        "session_plan": session_plan,
        "query_text": query_text,
        "product_name": product_name,
        "project_name": project_name,
        "brief": brief,
    }


def _owner_connection(
    *,
    session_id: int,
    session: dict[str, Any],
    staff: dict[str, Any] | None,
    ops: DraftOperations,
) -> tuple[int, int, Any | None]:
    actor_staff_id = ops.staff_id(staff)
    session_owner_id = ops.to_int(session.get("created_by"))
    if not actor_staff_id or (session_owner_id and session_owner_id != actor_staff_id):
        raise LookupError(f"search session not found: {session_id}")
    conn = ops.get_conn() if session_owner_id else None
    if conn is not None and session_owner_id:
        ops.lock_owned_session(
            conn,
            session_id=int(session_id),
            owner_id=session_owner_id,
        )
    return actor_staff_id, session_owner_id, conn


def _find_reusable_row(
    *,
    session_id: int,
    result_summary: dict[str, Any],
    actor_staff_id: int,
    conn: Any | None,
    to_int: Callable[..., int],
) -> Any | None:
    draft_summary = (
        result_summary.get("draft_project")
        if isinstance(result_summary.get("draft_project"), dict)
        else {}
    )
    recorded_project_id = to_int(draft_summary.get("project_id"))
    reusable_row = None
    if recorded_project_id and actor_staff_id and conn is not None:
        reusable_row = conn.execute(
            """
            SELECT * FROM vkpi_projects
            WHERE id=? AND stage_status <> 'deleted' AND source_type='smart_search'
              AND (created_by_staff_id=? OR assigned_staff_id=?)
            """,
            (recorded_project_id, actor_staff_id, actor_staff_id),
        ).fetchone()
    if not reusable_row and actor_staff_id and conn is not None:
        reusable_row = conn.execute(
            """
            SELECT * FROM vkpi_projects
            WHERE stage_status <> 'deleted' AND source_type='smart_search'
              AND metadata_json->>'search_session_id'=?
              AND (created_by_staff_id=? OR assigned_staff_id=?)
            ORDER BY id ASC
            LIMIT 1
            """,
            (str(int(session_id)), actor_staff_id, actor_staff_id),
        ).fetchone()
    return reusable_row


def _missing_kol_ids(
    attach_result: dict[str, Any],
    *,
    to_int: Callable[..., int],
) -> list[int]:
    return [
        to_int(value)
        for value in attach_result.get("missing_kol_pool_ids") or []
        if to_int(value)
    ]


def _missing_kol_warning(missing_kol_pool_ids: list[int]) -> str:
    if not missing_kol_pool_ids:
        return ""
    return "KOL pool items no longer exist: " + ",".join(
        str(value) for value in missing_kol_pool_ids
    )


def _attach_reusable_kols(
    *,
    project_id: int,
    kol_pool_ids: list[int],
    staff: dict[str, Any] | None,
    ops: DraftOperations,
) -> tuple[int, list[int], str]:
    attached = 0
    warning = ""
    missing: list[int] = []
    try:
        attach_result = ops.add_project_kols(
            project_id,
            {"kol_pool_ids": kol_pool_ids},
            staff=staff,
        )
        attached = ops.to_int(attach_result.get("inserted")) + ops.to_int(
            attach_result.get("skipped_existing")
        )
        missing = _missing_kol_ids(attach_result, to_int=ops.to_int)
        warning = _missing_kol_warning(missing)
    except ValueError as exc:
        warning = str(exc)
    return attached, missing, warning


def _reuse_existing_draft(
    *,
    session_id: int,
    session: dict[str, Any],
    reusable_row: Any,
    kol_pool_ids: list[int],
    brief: dict[str, Any],
    staff: dict[str, Any] | None,
    ops: DraftOperations,
) -> dict[str, Any] | None:
    reusable = dict(reusable_row)
    project_id = ops.to_int(reusable.get("id"))
    metadata = ops.loads(reusable.get("metadata_json"))
    if not isinstance(metadata, dict) or ops.to_int(metadata.get("search_session_id")) != int(
        session_id
    ):
        return None
    attached, missing, warning = _attach_reusable_kols(
        project_id=project_id,
        kol_pool_ids=kol_pool_ids,
        staff=staff,
        ops=ops,
    )
    ops.update_session_result_summary(
        int(session_id),
        status=str(session.get("status") or "ready"),
        summary_patch={
            "draft_project": {
                "project_id": project_id,
                "project_uid": reusable.get("project_uid"),
                "attached_kol_count": attached,
                "requested_kol_count": len(kol_pool_ids),
                "missing_kol_pool_ids": missing,
                "kol_attach_warning": warning,
                "reused": True,
            }
        },
    )
    return {
        "ok": True,
        "reused": True,
        "project_id": project_id,
        "project_uid": reusable.get("project_uid"),
        "project_name": reusable.get("project_name"),
        "stage": reusable.get("stage"),
        "attached_kol_count": attached,
        "requested_kol_count": len(kol_pool_ids),
        "missing_kol_pool_ids": missing,
        "kol_attach_warning": warning,
        "brief": metadata.get("brief") if isinstance(metadata.get("brief"), dict) else brief,
        "cost_estimate": (
            metadata.get("cost_estimate")
            if isinstance(metadata.get("cost_estimate"), dict)
            else {}
        ),
    }


def _estimate_cost(
    kol_pool_ids: list[int],
    *,
    staff: dict[str, Any] | None,
    estimate_cost_for_kols: Callable[..., dict[str, Any]],
    warning: Callable[..., Any],
) -> dict[str, Any]:
    try:
        return estimate_cost_for_kols(kol_pool_ids, staff=staff)
    except Exception:
        warning("create_project_draft: cost estimate skipped", exc_info=True)
        return {}


def _new_draft_body(
    *,
    session_id: int,
    session: dict[str, Any],
    session_owner_id: int,
    actor_staff_id: int,
    body: dict[str, Any],
    context: dict[str, Any],
    kol_pool_ids: list[int],
    cost_estimate: dict[str, Any],
) -> dict[str, Any]:
    session_plan = context["session_plan"]
    return {
        "project_name": context["project_name"],
        "stage": "discovery",
        "source_type": "smart_search",
        "product_sku": body.get("product_sku") or session_plan.get("product_sku") or "",
        "product_name": context["product_name"],
        "platform": str(body.get("platform") or ""),
        "metadata": {
            "brief": context["brief"],
            "search_session_id": int(session_id),
            "cost_estimate": cost_estimate,
            "source": {
                "type": "smart_search_session",
                "search_session_id": int(session_id),
                "session_owner_id": session_owner_id or actor_staff_id,
                "query_type": session.get("query_type"),
                "query_text": context["query_text"],
                "approved_kol_pool_ids": kol_pool_ids,
            },
        },
        "note": "draft from smart-search session",
    }


def _attach_new_draft_kols(
    *,
    project_id: int,
    kol_pool_ids: list[int],
    staff: dict[str, Any] | None,
    ops: DraftOperations,
) -> tuple[int, list[int], str]:
    attached = 0
    warning = ""
    missing: list[int] = []
    if not project_id:
        return attached, missing, warning
    try:
        result = ops.add_project_kols(
            project_id,
            {"kol_pool_ids": kol_pool_ids},
            staff=staff,
        )
        if isinstance(result, dict):
            attached = ops.to_int(result.get("inserted")) + ops.to_int(
                result.get("skipped_existing")
            )
            missing = _missing_kol_ids(result, to_int=ops.to_int)
            warning = _missing_kol_warning(missing)
    except ValueError as exc:
        warning = str(exc)
    return attached, missing, warning


def _record_new_draft(
    *,
    session_id: int,
    session: dict[str, Any],
    created: dict[str, Any],
    project_id: int,
    kol_pool_ids: list[int],
    attached: int,
    missing: list[int],
    warning_text: str,
    ops: DraftOperations,
) -> None:
    try:
        ops.update_session_result_summary(
            int(session_id),
            status=str(session.get("status") or "ready"),
            summary_patch={
                "draft_project": {
                    "project_id": project_id,
                    "project_uid": created.get("project_uid"),
                    "attached_kol_count": attached,
                    "requested_kol_count": len(kol_pool_ids),
                    "missing_kol_pool_ids": missing,
                    "kol_attach_warning": warning_text,
                }
            },
        )
    except Exception:
        ops.warning("suppressed exception (hardening: was silent)", exc_info=True)


def create_project_draft_from_session(
    session_id: int,
    body: dict[str, Any] | None,
    *,
    staff: dict[str, Any] | None,
    ops: DraftOperations,
) -> dict[str, Any]:
    body = body or {}
    session = ops.get_session(
        int(session_id),
        staff=staff,
        scope_to_staff=True,
    )
    kol_pool_ids = _approved_kol_pool_ids(session, to_int=ops.to_int)
    context = _draft_context(
        session_id=session_id,
        session=session,
        body=body,
        kol_pool_ids=kol_pool_ids,
    )
    actor_staff_id, session_owner_id, conn = _owner_connection(
        session_id=session_id,
        session=session,
        staff=staff,
        ops=ops,
    )
    reusable_row = _find_reusable_row(
        session_id=session_id,
        result_summary=context["result_summary"],
        actor_staff_id=actor_staff_id,
        conn=conn,
        to_int=ops.to_int,
    )
    if reusable_row:
        reused = _reuse_existing_draft(
            session_id=session_id,
            session=session,
            reusable_row=reusable_row,
            kol_pool_ids=kol_pool_ids,
            brief=context["brief"],
            staff=staff,
            ops=ops,
        )
        if reused is not None:
            return reused

    cost_estimate = _estimate_cost(
        kol_pool_ids,
        staff=staff,
        estimate_cost_for_kols=ops.estimate_cost_for_kols,
        warning=ops.warning,
    )
    create_body = _new_draft_body(
        session_id=session_id,
        session=session,
        session_owner_id=session_owner_id,
        actor_staff_id=actor_staff_id,
        body=body,
        context=context,
        kol_pool_ids=kol_pool_ids,
        cost_estimate=cost_estimate,
    )
    created = ops.create_project(create_body, staff=staff)
    project_id = ops.to_int(created.get("id"))
    attached, missing, warning_text = _attach_new_draft_kols(
        project_id=project_id,
        kol_pool_ids=kol_pool_ids,
        staff=staff,
        ops=ops,
    )
    _record_new_draft(
        session_id=session_id,
        session=session,
        created=created,
        project_id=project_id,
        kol_pool_ids=kol_pool_ids,
        attached=attached,
        missing=missing,
        warning_text=warning_text,
        ops=ops,
    )
    return {
        "ok": bool(project_id),
        "reused": False,
        "project_id": project_id,
        "project_uid": created.get("project_uid"),
        "project_name": context["project_name"],
        "stage": created.get("stage"),
        "attached_kol_count": attached,
        "requested_kol_count": len(kol_pool_ids),
        "missing_kol_pool_ids": missing,
        "kol_attach_warning": warning_text,
        "brief": context["brief"],
        "cost_estimate": cost_estimate,
    }

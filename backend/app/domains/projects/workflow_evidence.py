"""Message, content, terms, and shipment write operations for V-KPI workflow."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.domains import audit
from app.domains.access import scope
from app.platform.db.schema import ensure_vkpi_schema
from app.domains.projects import stage_canonical
from app.domains.projects.workflow_common import SIDE_STAGES, _amount_cents, _int, _json, _loads, normalize_stage, staff_id, utcnow

# 行为不变搬迁:视频元数据抓取内聚簇移至 sibling 模块,这里 re-export 兜住全部调用点
# (含下划线私有名)。函数体逐字未变 → 行为必然不变。
from app.domains.projects.workflow_evidence_video_metadata import (  # noqa: F401
    _text,
    _compact_int,
    _first,
    _detect_video_platform,
    _youtube_video_id,
    _duration_seconds,
    _published_pair,
    _youtube_api_metadata,
    _apify_actor_for,
    _apify_input,
    _apify_item_metadata,
    _apify_metadata,
    _fetch_video_metadata,
)

# 批B #5(2026-06-12):assignment 阶段受控集合 = assignment 词表 + side stages。
# normalize_stage 是单跳别名表,delivered→received / posted→published 落在项目词表,
# 这里二跳归一到 assignment 词表后再校验,词表外一律拒收。
_ASSIGNMENT_STAGE_FALLBACK_ALIASES = {
    "discovery": "discovered",
    "shipped": "device_sent",
    "received": "arrived",
    "published": "content_posted",
    "measured": "reviewed",
}
ASSIGNMENT_STAGES = {
    "discovered",
    "contacted",
    "replied",
    "agreed",
    "device_sent",
    "arrived",
    "content_posted",
    "reviewed",
    "closed",
    "churned",
}
_CONTROLLED_ASSIGNMENT_STAGES = ASSIGNMENT_STAGES | SIDE_STAGES


def _db_bool(value: Any) -> bool | int:
    return bool(value) if is_postgres_runtime() else (1 if value else 0)


def _assignment_row(conn, project_id: int, kol_ref: str | int):
    text_ref = str(kol_ref or "").replace("assignment:", "").strip()
    numeric_ref = _int(text_ref)
    clauses = ["project_id=?"]
    params: list[Any] = [int(project_id)]
    if numeric_ref:
        clauses.append("(id=? OR kol_pool_id=?)")
        params.extend([numeric_ref, numeric_ref])
    else:
        clauses.append("source_ref=?")
        params.append(text_ref)
    # 撞号防御(全盘扫描 P1):id 与 kol_pool_id 同号时优先 id 精确命中,避免改错行
    order_sql = ""
    if numeric_ref:
        order_sql = " ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END, id DESC"
        params.append(numeric_ref)
    return conn.execute(
        f"SELECT * FROM vkpi_project_kol_assignments WHERE {' AND '.join(clauses)}{order_sql} LIMIT 1",
        tuple(params),
    ).fetchone()


def advance_project_kol_assignment(project_id: int, kol_ref: str | int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    row = _assignment_row(conn, project_id, kol_ref)
    if not row:
        raise LookupError("project kol assignment not found")
    to_stage = normalize_stage(str(body.get("to_stage") or body.get("stage") or body.get("action") or "").strip())
    if not to_stage:
        raise ValueError("to_stage required")
    to_stage = _ASSIGNMENT_STAGE_FALLBACK_ALIASES.get(to_stage, to_stage)
    if to_stage not in _CONTROLLED_ASSIGNMENT_STAGES:
        raise ValueError("unsupported stage")
    now = utcnow()
    terminal_status = to_stage if to_stage in {"stalled", "lost", "released", "cancelled"} else "active"
    metadata = _loads(row["metadata_json"])
    metadata.setdefault("ui_actions", []).append({
        "kind": "stage_action",
        "from_stage": row["stage"],
        "to_stage": to_stage,
        "reason": str(body.get("reason") or body.get("note") or ""),
        "at": now,
        "staff_id": staff_id(staff),
    })
    conn.execute(
        """
        UPDATE vkpi_project_kol_assignments
        SET stage=?, stage_status=?, metadata_json=?, updated_at=?
        WHERE id=?
        """,
        (to_stage, terminal_status, _json(metadata), now, int(row["id"])),
    )
    conn.commit()
    updated = dict(conn.execute("SELECT * FROM vkpi_project_kol_assignments WHERE id=?", (int(row["id"]),)).fetchone())
    audit.log_business_event(
        staff_id=staff_id(staff),
        action_type="assignment_stage_update",
        target_type="project_kol_assignment",
        target_id=updated.get("id", ""),
        detail=f"{row['stage']} -> {to_stage}",
        metadata={"project_id": int(project_id), "assignment_id": updated.get("id"), "kol_pool_id": updated.get("kol_pool_id"), "to_stage": to_stage},
    )
    return {"assignment": updated}


def update_project_kol_shipping(project_id: int, kol_ref: str | int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    row = _assignment_row(conn, project_id, kol_ref)
    if not row:
        raise LookupError("project kol assignment not found")
    tracking_number = str(body.get("tracking_number") or body.get("trackingNo") or body.get("no") or "").strip()
    if not tracking_number:
        raise ValueError("tracking_number required")
    now = utcnow()
    metadata = _loads(row["metadata_json"])
    metadata["shipping"] = {
        "carrier": str(body.get("carrier") or ""),
        "tracking_number": tracking_number,
        "status": str(body.get("shipping_status") or body.get("status") or "shipped"),
        "products": body.get("products") or [],
        "shipping_cost_usd": body.get("shipping_cost_usd", body.get("shippingFee")),
        "product_cost_usd": body.get("product_cost_usd", body.get("productCost")),
        "updated_at": now,
        "staff_id": staff_id(staff),
    }
    current_stage = str(row["stage"] or "").strip().lower()
    preship_stages = {
        "discovered",
        "discovery",
        "claimed",
        "contacted",
        "replied",
        "in_discussion",
        "negotiating",
        "agreed",
        "confirmed",
        "sample_preparing",
    }
    next_stage = "shipped" if current_stage in preship_stages else str(row["stage"] or "shipped")
    conn.execute(
        """
        UPDATE vkpi_project_kol_assignments
        SET stage=?, stage_status='active', tracking_number=?, is_placeholder_tracking=?, metadata_json=?, updated_at=?
        WHERE id=?
        """,
        (next_stage, tracking_number, _db_bool(False), _json(metadata), now, int(row["id"])),
    )
    conn.commit()
    updated = dict(conn.execute("SELECT * FROM vkpi_project_kol_assignments WHERE id=?", (int(row["id"]),)).fetchone())
    audit.log_business_event(
        staff_id=staff_id(staff),
        action_type="assignment_shipping_update",
        target_type="project_kol_assignment",
        target_id=updated.get("id", ""),
        detail=tracking_number[:240],
        metadata={"project_id": int(project_id), "assignment_id": updated.get("id"), "kol_pool_id": updated.get("kol_pool_id"), "tracking_number": tracking_number},
    )
    return {"assignment": updated}


# ── 点4:履约交付信号写路径(系统侧,由 17track 物流同步在「已签收/Delivered」时调用)──
# 现状根因(审计履约 62 分拖底):17track 同步只写 assignment.metadata_json.shipping,
# 既不落 vkpi_shipments.delivered_at、也不把 assignment.stage 推进到 delivered。下游
# observation_windows / fulfillment_observation / automation_audit 都读 delivered_at,
# 因此长期「物流断流」、due_list 恒空。本函数只在【真实 delivered 事件】被调用,纯增量:
#   ① upsert 一行 vkpi_shipments(把 delivered_at 真正落实);
#   ② 通过 stage_canonical 归一把 assignment 推进到 canonical `delivered`(= assignment
#      原始词 `arrived`),走受控词表校验,绝不裸 UPDATE 绕过。
# 守住红线:不自动判履约/结项/付款,不触发观察扫描(只把 delivered_at 写对,让人审有据)。

# assignment 原始词中「已到达 delivered 或更靠后」的集合 —— 命中即不再向前推
# (单调:已发布/已复盘/已关闭的派单绝不被签收事件回退到 delivered)。
# 覆盖双词表:received/delivered=已签收;content_posted/posted/published/content_published=已发布;
# reviewed/measured=已复盘;closed/churned/cancelled/lost/released=终态。
_DELIVERED_OR_BEYOND_ASSIGNMENT_STAGES = frozenset({
    "arrived", "received", "delivered",
    "content_posted", "content_published", "posted", "published",
    "reviewed", "measured",
    "closed", "churned", "cancelled", "lost", "released",
})


def _shipments_has_delivered_column(conn: Any) -> bool:
    """vkpi_shipments.delivered_at 是否存在(DDL migrations/035 已含;防御缺列环境)。"""
    try:
        conn.execute("SELECT delivered_at FROM vkpi_shipments WHERE 1=0")
        return True
    except Exception:
        return False


def record_delivered_signal(
    *,
    project_id: int,
    assignment_id: int,
    tracking_number: str = "",
    carrier: str = "",
    raw_status: str = "Delivered",
    delivered_at: str | None = None,
    last_checked_at: str | None = None,
    kol_pool_id: int | None = None,
) -> dict[str, Any]:
    """系统侧:把一次【真实 delivered 事件】落成可被下游读取的交付信号。

    幂等 + 单调:
      - vkpi_shipments 按 (project_id, tracking_number) upsert 一行,只在 delivered_at
        尚未落实时写入 delivered_at(已落实则保留首签时间,只刷 last_checked_at/raw_status)。
      - assignment.stage 仅当当前不在 delivered-或更靠后 集合时,经 stage_canonical 归一
        推进到 `delivered`(assignment 原始词 `arrived`);否则保持不动(no regression)。

    本函数【不】触发观察扫描、【不】判履约/结项/付款。返回 dict 说明实际发生了什么,
    供调用方(17track worker)汇总;不抛 ScopeDenied(系统作业,无 human actor)。
    """
    conn = get_conn()
    now = utcnow()
    delivered_ts = str(delivered_at or now)
    checked_ts = str(last_checked_at or now)
    raw = str(raw_status or "Delivered")
    tracking = str(tracking_number or "").strip()

    result: dict[str, Any] = {
        "project_id": int(project_id),
        "assignment_id": int(assignment_id),
        "shipment_action": "skipped",
        "stage_action": "skipped",
    }

    # ── ① vkpi_shipments upsert(只落真实 delivered_at)──
    if _shipments_has_delivered_column(conn):
        existing = conn.execute(
            """
            SELECT id, delivered_at FROM vkpi_shipments
            WHERE project_id=? AND COALESCE(tracking_number,'')=?
            ORDER BY id DESC LIMIT 1
            """,
            (int(project_id), tracking),
        ).fetchone()
        meta = {
            "source": "17track",
            "assignment_id": int(assignment_id),
            "kol_pool_id": int(kol_pool_id) if kol_pool_id else None,
            "raw_status": raw,
            "last_checked_at": checked_ts,
        }
        if existing is not None:
            ex = dict(existing)
            # 已有 delivered_at → 保留首签时间(单调),只刷元数据/last_checked_at。
            keep_delivered = ex.get("delivered_at") or delivered_ts
            conn.execute(
                """
                UPDATE vkpi_shipments
                SET delivered_at=?, status='delivered', carrier=COALESCE(NULLIF(?,''), carrier),
                    metadata_json=?, updated_at=?
                WHERE id=?
                """,
                (keep_delivered, str(carrier or ""), _json(meta), now, int(ex["id"])),
            )
            result["shipment_action"] = "updated"
            result["shipment_id"] = int(ex["id"])
        else:
            conn.execute(
                """
                INSERT INTO vkpi_shipments (
                    project_id, sample_asset_id, carrier, tracking_number, status,
                    shipping_cost_cents, currency, shipped_at, delivered_at, evidence_url,
                    note, metadata_json, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(project_id),
                    None,
                    str(carrier or ""),
                    tracking,
                    "delivered",
                    0,
                    "USD",
                    None,
                    delivered_ts,
                    "",
                    "",
                    _json(meta),
                    now,
                    now,
                ),
            )
            ins = conn.execute(
                "SELECT id FROM vkpi_shipments WHERE project_id=? AND COALESCE(tracking_number,'')=? ORDER BY id DESC LIMIT 1",
                (int(project_id), tracking),
            ).fetchone()
            result["shipment_action"] = "inserted"
            result["shipment_id"] = int(dict(ins)["id"]) if ins else None
    else:
        result["shipment_action"] = "no_column"

    # ── ② assignment.stage 经 canonical 归一推进到 delivered(单调,不裸 UPDATE 绕校验)──
    row = conn.execute(
        "SELECT id, stage, stage_status, metadata_json, kol_pool_id FROM vkpi_project_kol_assignments WHERE id=? AND project_id=?",
        (int(assignment_id), int(project_id)),
    ).fetchone()
    if row is None:
        conn.commit()
        result["stage_action"] = "assignment_missing"
        return result

    arow = dict(row)
    current_raw = str(arow.get("stage") or "").strip().lower()
    current_norm = normalize_stage(current_raw)
    # 已到达 delivered 或更靠后 → 不回退(单调)。
    if current_norm in _DELIVERED_OR_BEYOND_ASSIGNMENT_STAGES or current_raw in _DELIVERED_OR_BEYOND_ASSIGNMENT_STAGES:
        result["stage_action"] = "already_delivered_or_beyond"
        result["from_stage"] = current_raw
        conn.commit()
        return result

    # canonical 归一:从 canonical 源 'delivered' 经现有归一表得到 assignment 受控原始词。
    #   stage_canonical.to_canonical('delivered') == 'delivered'(canonical 源,单一事实源);
    #   normalize_stage('delivered') -> 'received' -> _ASSIGNMENT_STAGE_FALLBACK_ALIASES -> 'arrived'。
    # 不裸写:经 _CONTROLLED_ASSIGNMENT_STAGES 词表 + 单调集合双校验,任一不过即不写。
    if stage_canonical.to_canonical("delivered") != "delivered":  # 防 canonical 源漂移
        result["stage_action"] = "canonical_source_drift"
        conn.commit()
        return result
    target_raw = normalize_stage("delivered")  # delivered -> received
    target_raw = _ASSIGNMENT_STAGE_FALLBACK_ALIASES.get(target_raw, target_raw)  # received -> arrived
    # 受控词表内 + 落在「delivered 或更靠后」集合(与单调守卫同一口径,杜绝词表漂移误推)。
    if target_raw not in _CONTROLLED_ASSIGNMENT_STAGES or target_raw not in _DELIVERED_OR_BEYOND_ASSIGNMENT_STAGES:
        result["stage_action"] = "unsupported_target"
        result["target_raw"] = target_raw
        conn.commit()
        return result

    metadata = _loads(arow.get("metadata_json"))
    metadata.setdefault("ui_actions", []).append({
        "kind": "stage_action",
        "from_stage": current_raw,
        "to_stage": target_raw,
        "reason": "logistics_delivered",
        "source": "17track",
        "at": now,
        "staff_id": 0,
    })
    conn.execute(
        """
        UPDATE vkpi_project_kol_assignments
        SET stage=?, stage_status='active', metadata_json=?, updated_at=?
        WHERE id=?
        """,
        (target_raw, _json(metadata), now, int(arow["id"])),
    )
    conn.commit()
    result["stage_action"] = "advanced"
    result["from_stage"] = current_raw
    result["to_stage"] = target_raw
    # 审计(系统作业 staff_id=0 时 log_business_event 自行 skip,不抛错)。
    audit.log_business_event(
        staff_id=0,
        action_type="assignment_delivered_signal",
        target_type="project_kol_assignment",
        target_id=int(arow["id"]),
        detail=f"{current_raw} -> {target_raw} (17track delivered)",
        metadata={
            "project_id": int(project_id),
            "assignment_id": int(arow["id"]),
            "kol_pool_id": arow.get("kol_pool_id"),
            "tracking_number": tracking,
            "source": "17track",
        },
    )
    return result


def record_project_kol_video(project_id: int, kol_ref: str | int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    video_url = _text(body.get("video_url") or body.get("url") or body.get("content_url"))
    if not re.match(r"^https?://", video_url, flags=re.I):
        raise ValueError("valid video_url required")
    conn = get_conn()
    row = _assignment_row(conn, project_id, kol_ref)
    if not row:
        raise LookupError("project kol assignment not found")
    metadata = _fetch_video_metadata(video_url)
    now = datetime.now(timezone.utc).isoformat()
    source_ref = f"project_video:{int(project_id)}:{int(row['id'])}"
    title = _text(metadata.get("title") or body.get("title") or video_url)
    evidence_cursor = conn.execute(
        """
        INSERT INTO vkpi_kol_video_evidence (
            kol_pool_id, project_id, content_url, platform, video_title, title,
            posted_at, publish_date, view_count, like_count, comment_count, share_count,
            evidence_type, source, source_ref, confidence, is_active,
            duration_seconds, thumbnail_url, channel_id, channel_name,
            scrape_status, scrape_source, scraped_at, metrics_scraped_at, metrics_source,
            scrape_error, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (content_url) DO UPDATE SET
            platform=excluded.platform,
            video_title=excluded.video_title,
            title=excluded.title,
            posted_at=COALESCE(excluded.posted_at, vkpi_kol_video_evidence.posted_at),
            publish_date=COALESCE(excluded.publish_date, vkpi_kol_video_evidence.publish_date),
            view_count=excluded.view_count,
            like_count=excluded.like_count,
            comment_count=excluded.comment_count,
            share_count=excluded.share_count,
            evidence_type=excluded.evidence_type,
            source=excluded.source,
            source_ref=excluded.source_ref,
            confidence=excluded.confidence,
            is_active=excluded.is_active,
            duration_seconds=excluded.duration_seconds,
            thumbnail_url=excluded.thumbnail_url,
            channel_id=excluded.channel_id,
            channel_name=excluded.channel_name,
            scrape_status=excluded.scrape_status,
            scrape_source=excluded.scrape_source,
            scraped_at=excluded.scraped_at,
            metrics_scraped_at=excluded.metrics_scraped_at,
            metrics_source=excluded.metrics_source,
            scrape_error=excluded.scrape_error,
            updated_at=excluded.updated_at
        RETURNING *
        """,
        (
            int(row["kol_pool_id"]),
            int(project_id),
            video_url,
            _text(metadata.get("platform")),
            title,
            title,
            metadata.get("posted_at"),
            metadata.get("publish_date"),
            metadata.get("view_count"),
            metadata.get("like_count"),
            metadata.get("comment_count"),
            metadata.get("share_count"),
            "video",
            "manual_url",
            source_ref,
            "high",
            _db_bool(True),
            metadata.get("duration_seconds"),
            _text(metadata.get("thumbnail_url")),
            _text(metadata.get("channel_id")),
            _text(metadata.get("channel_name")),
            "success",
            _text(metadata.get("scrape_source")),
            now,
            now,
            _text(metadata.get("scrape_source")),
            _text(metadata.get("scrape_error")),
            now,
            now,
        ),
    )
    evidence = dict(evidence_cursor.fetchone())
    assignment_metadata = _loads(row["metadata_json"])
    assignment_metadata.setdefault("ui_actions", []).append({
        "kind": "video_capture",
        "url": video_url,
        "evidence_id": evidence.get("id"),
        "at": now,
        "staff_id": staff_id(staff),
        "scrape_source": metadata.get("scrape_source"),
    })
    current_stage = _text(row["stage"]).lower()
    # 批B #6(2026-06-12):只前进不后退——published/measured(历史项目词表行)/reviewed/
    # content_posted 及终态之后补录视频,不把阶段倒退回 content_posted。
    _no_regress_stages = {"content_posted", "published", "measured", "reviewed", "closed", "churned"}
    next_stage = "content_posted" if current_stage not in _no_regress_stages else current_stage
    conn.execute(
        """
        UPDATE vkpi_project_kol_assignments
        SET stage=?, stage_status='active', metadata_json=?, updated_at=?
        WHERE id=?
        """,
        (next_stage, _json(assignment_metadata), now, int(row["id"])),
    )
    if body.get("shopify_url"):
        conn.execute("UPDATE vkpi_projects SET shopify_link=?, updated_at=? WHERE id=?", (_text(body.get("shopify_url")), now, int(project_id)))
    conn.commit()
    updated_assignment = dict(conn.execute("SELECT * FROM vkpi_project_kol_assignments WHERE id=?", (int(row["id"]),)).fetchone())
    audit.log_business_event(
        staff_id=staff_id(staff),
        action_type="assignment_video_capture",
        target_type="project_kol_assignment",
        target_id=int(row["id"]),
        detail=video_url[:240],
        metadata={
            "project_id": int(project_id),
            "assignment_id": int(row["id"]),
            "kol_pool_id": int(row["kol_pool_id"]),
            "evidence_id": evidence.get("id"),
            "scrape_source": metadata.get("scrape_source"),
            "platform": metadata.get("platform"),
        },
    )
    return {
        "ok": True,
        "status": "success",
        "message": "视频已抓取并写入 evidence。",
        "project_id": int(project_id),
        "assignment_id": int(row["id"]),
        "assignment": updated_assignment,
        "evidence": evidence,
        "metrics": {
            "platform": evidence.get("platform"),
            "title": evidence.get("title") or evidence.get("video_title"),
            "view_count": evidence.get("view_count"),
            "like_count": evidence.get("like_count"),
            "comment_count": evidence.get("comment_count"),
            "share_count": evidence.get("share_count"),
            "publish_date": evidence.get("publish_date"),
            "scrape_source": evidence.get("scrape_source"),
        },
    }


def project_kol_action_stub(project_id: int, kol_ref: str | int, body: dict[str, Any], *, kind: str, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    row = _assignment_row(conn, project_id, kol_ref)
    if not row:
        raise LookupError("project kol assignment not found")
    # 截图真存证(2026-06-12 裁令):文件已由 /evidence/uploads 落盘,这里把 file_url
    # 记入 vkpi_messages(internal_note,evidence_url 列)——沟通/证据流可查,不再是 stub。
    file_url = str(body.get("file_url") or "").strip()
    if kind == "screenshot" and file_url:
        project = conn.execute("SELECT kol_id FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
        note = str(body.get("note") or "").strip()
        stage = str(body.get("stage") or "")
        snippet = note or f"阶段截图存证({stage})"
        conn.execute(
            """
            INSERT INTO vkpi_messages (
                project_id, kol_id, staff_id, source, direction, sender, receiver,
                body, snippet, evidence_url, captured_at, metadata_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(project_id),
                _int(dict(project or {}).get("kol_id")) or None,
                staff_id(staff) or None,
                "stage_screenshot",
                "internal_note",
                "",
                "",
                snippet,
                snippet[:240],
                file_url,
                utcnow(),
                json.dumps({"stage": stage, "assignment_id": int(row["id"]), "kol_pool_id": row["kol_pool_id"], "file_name": body.get("file_name")}, ensure_ascii=False),
                utcnow(),
            ),
        )
        conn.commit()
        audit.log_business_event(
            staff_id=staff_id(staff),
            action_type="assignment_screenshot_stored",
            target_type="project_kol_assignment",
            target_id=row["id"],
            detail=str(body.get("file_name") or file_url)[:240],
            metadata={"project_id": int(project_id), "assignment_id": int(row["id"]), "kol_pool_id": row["kol_pool_id"], "file_url": file_url, "stage": stage},
        )
        return {
            "status": "stored",
            "kind": kind,
            "file_url": file_url,
            "project_id": int(project_id),
            "assignment_id": int(row["id"]),
        }
    audit.log_business_event(
        staff_id=staff_id(staff),
        action_type=f"assignment_{kind}_stub",
        target_type="project_kol_assignment",
        target_id=row["id"],
        detail=str(body.get("file_name") or body.get("url") or body.get("note") or kind)[:240],
        metadata={"project_id": int(project_id), "assignment_id": int(row["id"]), "kol_pool_id": row["kol_pool_id"], "stub": True, "payload": body},
    )
    return {
        "status": "pending_integration",
        "message": "功能开发中：待接入文件存储、LLM 或抓取处理。",
        "kind": kind,
        "project_id": int(project_id),
        "assignment_id": int(row["id"]),
    }

def add_project_message(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    project = conn.execute("SELECT kol_id, assigned_staff_id FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not project:
        raise LookupError("project not found")
    now = utcnow()
    message_body = str(body.get("body") or body.get("message") or body.get("snippet") or "").strip()
    # P2:INSERT 取行一律 RETURNING——并发下 ORDER BY id DESC 会取到别人的行。
    row = conn.execute(
        """
        INSERT INTO vkpi_messages (
            project_id, kol_id, staff_id, source, direction, sender, receiver,
            body, snippet, evidence_url, follow_up_due_at, captured_at, metadata_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        RETURNING *
        """,
        (
            int(project_id),
            _int(project["kol_id"]) or None,
            staff_id(staff) or _int(project["assigned_staff_id"]) or None,
            str(body.get("source") or "manual"),
            str(body.get("direction") or "outbound"),
            str(body.get("sender") or ""),
            str(body.get("receiver") or ""),
            message_body,
            str(body.get("snippet") or message_body[:240]),
            str(body.get("evidence_url") or ""),
            body.get("follow_up_due_at"),
            str(body.get("captured_at") or now),
            _json(body.get("metadata")),
            now,
        ),
    ).fetchone()
    conn.commit()
    item = dict(row) if row else {}
    if item:
        audit.log_business_event(
            staff_id=staff_id(staff) or _int(project["assigned_staff_id"]),
            action_type="message_capture",
            target_type="message",
            target_id=item.get("id", ""),
            detail=str(item.get("body") or item.get("snippet") or "")[:240],
            metadata={"project_id": int(project_id), "kol_id": _int(project["kol_id"]) or None},
        )
    return item

def add_project_content(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    post_url = str(body.get("post_url") or body.get("url") or "").strip()
    if not post_url:
        raise ValueError("post_url required")
    conn = get_conn()
    project = conn.execute("SELECT kol_id, platform FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not project:
        raise LookupError("project not found")
    now = utcnow()
    conn.execute(
        """
        INSERT INTO vkpi_content_posts (
            project_id, kol_id, link_id, platform, post_url, title, thumbnail_url,
            published_at, content_type, views, likes, comments, shares,
            rights_status, ad_usage_allowed, metadata_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(project_id, post_url) DO UPDATE SET
            kol_id=excluded.kol_id,
            link_id=excluded.link_id,
            platform=excluded.platform,
            title=excluded.title,
            thumbnail_url=excluded.thumbnail_url,
            published_at=excluded.published_at,
            content_type=excluded.content_type,
            views=excluded.views,
            likes=excluded.likes,
            comments=excluded.comments,
            shares=excluded.shares,
            rights_status=excluded.rights_status,
            ad_usage_allowed=excluded.ad_usage_allowed,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (
            int(project_id),
            _int(project["kol_id"]) or None,
            _int(body.get("link_id")) or None,
            str(body.get("platform") or project["platform"] or ""),
            post_url,
            str(body.get("title") or ""),
            str(body.get("thumbnail_url") or ""),
            body.get("published_at"),
            str(body.get("content_type") or "video"),
            _int(body.get("views")),
            _int(body.get("likes")),
            _int(body.get("comments")),
            _int(body.get("shares")),
            str(body.get("rights_status") or "unknown"),
            _db_bool(body.get("ad_usage_allowed")),
            _json(body.get("metadata")),
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_content_posts WHERE project_id=? AND post_url=?", (int(project_id), post_url)).fetchone()
    item = dict(row) if row else {}
    if item:
        asset_url = str(body.get("asset_url") or body.get("thumbnail_url") or "").strip()
        if asset_url:
            existing_asset = conn.execute(
                "SELECT id FROM vkpi_content_assets WHERE post_id=? AND asset_url=? ORDER BY id DESC LIMIT 1",
                (int(item["id"]), asset_url),
            ).fetchone()
            if not existing_asset:
                conn.execute(
                    """
                    INSERT INTO vkpi_content_assets (
                        post_id, project_id, asset_url, asset_type, usage_rights, metadata_json, created_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        int(item["id"]),
                        int(project_id),
                        asset_url,
                        str(body.get("asset_type") or body.get("content_type") or "content"),
                        str(body.get("usage_rights") or body.get("rights_status") or "unknown"),
                        _json({
                            "source": "project_detail_form",
                            "marker": (body.get("metadata") or {}).get("marker") if isinstance(body.get("metadata"), dict) else None,
                            "content_post_id": item.get("id"),
                        }),
                        now,
                    ),
                )
                conn.commit()
                asset_row = conn.execute(
                    "SELECT id FROM vkpi_content_assets WHERE post_id=? AND asset_url=? ORDER BY id DESC LIMIT 1",
                    (int(item["id"]), asset_url),
                ).fetchone()
                audit.log_business_event(
                    staff_id=staff_id(staff),
                    action_type="content_asset_add",
                    target_type="content_post",
                    target_id=item.get("id", ""),
                    detail=asset_url[:240],
                    metadata={"project_id": int(project_id), "kol_id": _int(project["kol_id"]) or None, "asset_id": asset_row["id"] if asset_row else None},
                )
        audit.log_business_event(
            staff_id=staff_id(staff),
            action_type="content_capture",
            target_type="content_post",
            target_id=item.get("id", ""),
            detail=str(item.get("title") or item.get("post_url") or "")[:240],
            metadata={"project_id": int(project_id), "kol_id": _int(project["kol_id"]) or None, "post_url": post_url},
        )
    return item

def upsert_project_terms(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    if not conn.execute("SELECT id FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone():
        raise LookupError("project not found")
    now = utcnow()
    actor = staff_id(staff) or None
    shopify_link = str(body.get("shopify_url") or body.get("shopify_link") or "").strip()
    if shopify_link:
        conn.execute(
            "UPDATE vkpi_projects SET shopify_link=?, updated_at=? WHERE id=?",
            (shopify_link, now, int(project_id)),
        )
    terms_keys = {
        "cash_fee_usd",
        "cash_fee",
        "currency",
        "sample_terms",
        "deliverables",
        "usage_rights",
        "due_at",
    }
    has_terms_payload = any(key in body for key in terms_keys)
    if shopify_link and not has_terms_payload:
        conn.commit()
        row = conn.execute("SELECT * FROM vkpi_project_terms WHERE project_id=?", (int(project_id),)).fetchone()
        item = dict(row) if row else {"project_id": int(project_id)}
        item["shopify_link"] = shopify_link
        item["terms_updated"] = False
        audit.log_business_event(
            staff_id=actor,
            action_type="project_shopify_link_update",
            target_type="project",
            target_id=int(project_id),
            detail=shopify_link[:240],
            metadata={"project_id": int(project_id), "terms_updated": False},
        )
        return item
    conn.execute(
        """
        INSERT INTO vkpi_project_terms (
            project_id, cash_fee_cents, currency, sample_terms, deliverables_json,
            usage_rights, due_at, note, created_by_staff_id, updated_by_staff_id,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(project_id) DO UPDATE SET
            cash_fee_cents=excluded.cash_fee_cents,
            currency=excluded.currency,
            sample_terms=excluded.sample_terms,
            deliverables_json=excluded.deliverables_json,
            usage_rights=excluded.usage_rights,
            due_at=excluded.due_at,
            note=excluded.note,
            updated_by_staff_id=excluded.updated_by_staff_id,
            updated_at=excluded.updated_at
        """,
        (
            int(project_id),
            _amount_cents(body.get("cash_fee_usd", body.get("cash_fee", 0))),
            str(body.get("currency") or "USD"),
            str(body.get("sample_terms") or ""),
            json.dumps(body.get("deliverables") if isinstance(body.get("deliverables"), list) else [], ensure_ascii=False),
            str(body.get("usage_rights") or ""),
            body.get("due_at"),
            str(body.get("note") or ""),
            actor,
            actor,
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_project_terms WHERE project_id=?", (int(project_id),)).fetchone()
    item = dict(row) if row else {}
    if item:
        audit.log_business_event(
            staff_id=actor,
            action_type="terms_upsert",
            target_type="project",
            target_id=int(project_id),
            detail=str(body.get("note") or shopify_link or item.get("sample_terms") or "")[:240],
            metadata={"project_id": int(project_id), "terms_id": item.get("id"), "shopify_link_updated": bool(shopify_link)},
        )
    return item

def add_project_shipment(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    from app.repositories.projects_repo import ProjectsRepository

    project = ProjectsRepository().get_sample_fields(int(project_id))  # L2:走 repo
    if not project:
        raise LookupError("project not found")
    # 发货审批门槛(P0):已发起审批但未通过 → 拦截发货(人审真生效)。无审批记录默认放行(向后兼容)。
    from app.domains.projects import shipment_approval

    shipment_approval.assert_shippable(int(project_id), _int(project["kol_id"]) or 0, staff=staff)
    now = utcnow()
    conn.execute(
        """
        INSERT INTO vkpi_sample_assets (
            project_id, kol_id, product_sku, product_name, serial_number,
            sample_cost_cents, currency, return_required, status, shipped_at,
            received_at, note, metadata_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(project_id),
            _int(project["kol_id"]) or None,
            str(body.get("product_sku") or project["product_sku"] or ""),
            str(body.get("product_name") or project["product_name"] or ""),
            str(body.get("serial_number") or ""),
            _amount_cents(body.get("sample_cost_usd", body.get("sample_cost", 0))),
            str(body.get("currency") or "USD"),
            _db_bool(body.get("return_required")),
            str(body.get("sample_status") or "shipped"),
            str(body.get("shipped_at") or now),
            body.get("received_at"),
            str(body.get("note") or ""),
            _json(body.get("metadata")),
            now,
            now,
        ),
    )
    sample_id = conn.execute("SELECT id FROM vkpi_sample_assets WHERE project_id=? ORDER BY id DESC LIMIT 1", (int(project_id),)).fetchone()
    sample_asset_id = int(sample_id["id"]) if sample_id else None
    conn.execute(
        """
        INSERT INTO vkpi_shipments (
            project_id, sample_asset_id, carrier, tracking_number, status,
            shipping_cost_cents, currency, shipped_at, delivered_at, evidence_url,
            note, metadata_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(project_id),
            sample_asset_id,
            str(body.get("carrier") or ""),
            str(body.get("tracking_number") or ""),
            str(body.get("shipping_status") or "shipped"),
            _amount_cents(body.get("shipping_cost_usd", body.get("shipping_cost", 0))),
            str(body.get("currency") or "USD"),
            str(body.get("shipped_at") or now),
            body.get("delivered_at"),
            str(body.get("evidence_url") or ""),
            str(body.get("note") or ""),
            _json(body.get("metadata")),
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT sh.*, sa.product_sku, sa.product_name, sa.serial_number, sa.sample_cost_cents
        FROM vkpi_shipments sh
        LEFT JOIN vkpi_sample_assets sa ON sa.id = sh.sample_asset_id
        WHERE sh.project_id=?
        ORDER BY sh.id DESC
        LIMIT 1
        """,
        (int(project_id),),
    ).fetchone()
    item = dict(row) if row else {}
    if item:
        audit.log_business_event(
            staff_id=staff_id(staff),
            action_type="shipment_add",
            target_type="shipment",
            target_id=item.get("id", ""),
            detail=str(item.get("tracking_number") or item.get("carrier") or "")[:240],
            metadata={"project_id": int(project_id), "sample_asset_id": item.get("sample_asset_id"), "product_sku": item.get("product_sku")},
        )
    return item

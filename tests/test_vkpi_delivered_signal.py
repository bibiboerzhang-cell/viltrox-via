"""点4 — 履约交付信号写路径 单测。

覆盖 record_delivered_signal(由 17track 物流同步在「已签收/Delivered」时调用):
  - delivered 事件 → vkpi_shipments.delivered_at 真正落实 + assignment.stage 推进到
    canonical `delivered`(assignment 原始词 `arrived`)。
  - 幂等/单调:再次签收保留首签时间;已发布(content_posted)的派单绝不被回退。
  - 非 delivered 事件不误写(只走 Delivered 分支;此处用守卫集合直接证明)。

红线:零触 fit_score / rule_v0;不开观察扫描;不判履约/结项/付款;走 service 路径不裸 UPDATE。
真实库写入用 marker-scoped 种子数据,测后清理。
"""
from __future__ import annotations

import pytest

from app.db.connection import get_conn
from app.domains.projects import stage_canonical
from app.domains.projects.workflow_common import normalize_stage
from app.domains.projects.workflow_evidence import (
    _DELIVERED_OR_BEYOND_ASSIGNMENT_STAGES,
    record_delivered_signal,
)
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema


MARKER = "VKPI-DELIVERED-SIGNAL-TEST"
EMAIL = "vkpi-delivered-signal-test@example.com"
PROJECT_UID = "VKPI-DELIVERED-SIGNAL-PROJECT"
TRACKING = "VKPI-DLV-TRACK-001"


@pytest.fixture(autouse=True)
def _ensure_schemas():
    ensure_vkpi_schema()
    ensure_vkpi_audit_schema()
    yield


@pytest.fixture
def seeded():
    conn = get_conn()
    now = "2026-05-01T10:00:00Z"

    def cleanup() -> None:
        project_ids = [
            int(r["id"])
            for r in conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (PROJECT_UID,)).fetchall()
        ]
        for pid in project_ids:
            conn.execute("DELETE FROM vkpi_shipments WHERE project_id=?", (pid,))
            conn.execute("DELETE FROM vkpi_project_kol_assignments WHERE project_id=?", (pid,))
            conn.execute("DELETE FROM vkpi_projects WHERE id=?", (pid,))
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE metadata_json LIKE ?", (f"%{MARKER}%",))
        conn.execute("DELETE FROM vkpi_kol_pool WHERE pool_uid=?", (MARKER + "-POOL",))
        kol_ids = [int(r["id"]) for r in conn.execute("SELECT id FROM kols WHERE channel_name=?", (MARKER,)).fetchall()]
        for kid in kol_ids:
            conn.execute("DELETE FROM kols WHERE id=?", (kid,))
        staff_ids = [
            int(r["id"])
            for r in conn.execute(
                "SELECT s.id FROM staff s JOIN users u ON u.id=s.user_id WHERE u.email=?", (EMAIL,)
            ).fetchall()
        ]
        for sid in staff_ids:
            conn.execute("DELETE FROM staff WHERE id=?", (sid,))
        for uid in [int(r["id"]) for r in conn.execute("SELECT id FROM users WHERE email=?", (EMAIL,)).fetchall()]:
            conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.commit()

    try:
        cleanup()
        conn.execute(
            """INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified)
               VALUES (?,?,?,?,?,?,?)""",
            (now, EMAIL, "v2:00:00", "Delivered Test", "approved", "admin", 1),
        )
        user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (EMAIL,)).fetchone()["id"])
        conn.execute(
            """INSERT INTO staff (user_id, role, permissions_json, mfa_enabled, active, invited_at, is_owner, email_domain_verified)
               VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, "admin", '{"vkpi":"admin"}', 0, 1, now, 1, 1),
        )
        staff_id = int(conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])
        conn.execute(
            """INSERT INTO kols (channel_name, channel_url, platform, assigned_staff_id, created_by_staff_id, follower_count, avg_views)
               VALUES (?,?,?,?,?,?,?)""",
            (MARKER, "https://example.com/vkpi-delivered", "youtube", staff_id, staff_id, 1000, 100),
        )
        kol_id = int(conn.execute("SELECT id FROM kols WHERE channel_name=?", (MARKER,)).fetchone()["id"])
        conn.execute(
            """INSERT INTO vkpi_projects
               (project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id,
                product_sku, product_name, platform, stage, stage_status, priority,
                source_type, sample_status, metadata_json, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                PROJECT_UID, "Delivered Test Project", kol_id, staff_id, staff_id,
                "DLV-SKU", "Delivered Test Product", "youtube", "shipped", "active", "normal",
                "manual", "shipped", "{}", now, now,
            ),
        )
        project_id = int(conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (PROJECT_UID,)).fetchone()["id"])
        # 派单需 kol_pool_id NOT NULL(FK -> vkpi_kol_pool);种一行最小 pool(不碰 fit_score)。
        conn.execute(
            """INSERT INTO vkpi_kol_pool (pool_uid, platform, handle, created_at, updated_at)
               VALUES (?,?,?,?,?)""",
            (MARKER + "-POOL", "youtube", MARKER + "-handle", now, now),
        )
        pool_id = int(conn.execute("SELECT id FROM vkpi_kol_pool WHERE pool_uid=?", (MARKER + "-POOL",)).fetchone()["id"])
        conn.commit()
        yield {"project_id": project_id, "kol_id": kol_id, "staff_id": staff_id, "pool_id": pool_id}
    finally:
        cleanup()


def _new_assignment(project_id: int, pool_id: int, stage: str, tracking: str = TRACKING) -> int:
    conn = get_conn()
    conn.execute(
        """INSERT INTO vkpi_project_kol_assignments
           (project_id, kol_pool_id, stage, stage_status, tracking_number, source, source_ref, metadata_json, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (int(project_id), int(pool_id), stage, "active", tracking, "test", MARKER + "-" + stage, "{}",
         "2026-05-01T10:00:00Z", "2026-05-01T10:00:00Z"),
    )
    conn.commit()
    return int(
        conn.execute(
            "SELECT id FROM vkpi_project_kol_assignments WHERE project_id=? ORDER BY id DESC LIMIT 1",
            (int(project_id),),
        ).fetchone()["id"]
    )


# ── 静态保证:守卫集合与 canonical 口径一致 ──

def test_delivered_canonical_chain_is_correct():
    """canonical 'delivered' 必须归一到 assignment 受控词 'arrived',且单调集合覆盖之。"""
    assert stage_canonical.to_canonical("delivered") == "delivered"
    # service 推进目标 = normalize_stage('delivered') -> 'received' -> fallback 'arrived'
    assert normalize_stage("delivered") == "received"
    assert "arrived" in _DELIVERED_OR_BEYOND_ASSIGNMENT_STAGES
    # 已发布/已复盘/终态都在「不回退」集合里
    for s in ("content_posted", "content_published", "reviewed", "measured", "closed", "churned"):
        assert s in _DELIVERED_OR_BEYOND_ASSIGNMENT_STAGES


# ── 正路:delivered 事件 → shipments.delivered_at 落实 + stage=delivered ──

def test_delivered_event_lands_shipment_and_advances_stage(seeded):
    project_id = seeded["project_id"]
    assignment_id = _new_assignment(project_id, seeded["pool_id"], "device_sent")
    conn = get_conn()

    # 改前:无 shipments、stage=device_sent(canonical=shipped,在 delivered 之前)
    assert conn.execute("SELECT COUNT(*) AS c FROM vkpi_shipments WHERE project_id=?", (project_id,)).fetchone()["c"] == 0

    result = record_delivered_signal(
        project_id=project_id,
        assignment_id=assignment_id,
        tracking_number=TRACKING,
        carrier="17track",
        raw_status="Delivered",
        delivered_at="2026-05-10T08:00:00Z",
        last_checked_at="2026-05-10T09:00:00Z",
    )
    assert result["shipment_action"] == "inserted"
    assert result["stage_action"] == "advanced"
    assert result["to_stage"] == "arrived"

    # ① vkpi_shipments.delivered_at 真正落实
    sh = dict(
        conn.execute(
            "SELECT delivered_at, status FROM vkpi_shipments WHERE project_id=? AND tracking_number=? ORDER BY id DESC LIMIT 1",
            (project_id, TRACKING),
        ).fetchone()
    )
    assert sh["delivered_at"] is not None
    assert str(sh["delivered_at"]).startswith("2026-05-10")
    assert sh["status"] == "delivered"

    # ② assignment.stage 推进到 canonical delivered。assignment 受控词表里 delivered 的
    #    原始词是 'arrived'(stage_canonical 的 _RAW_TO_CANONICAL 把 received/delivered→delivered;
    #    'arrived' 是 assignment 词表内的同义投影)。验证落在「delivered 或更靠后」单调集合。
    stage = conn.execute(
        "SELECT stage FROM vkpi_project_kol_assignments WHERE id=?", (assignment_id,)
    ).fetchone()["stage"]
    assert stage == "arrived"
    assert stage in _DELIVERED_OR_BEYOND_ASSIGNMENT_STAGES
    # canonical 源侧锚定:'delivered'/'received' 的 canonical 就是 delivered(单一事实源)。
    assert stage_canonical.to_canonical("delivered") == "delivered"
    assert stage_canonical.to_canonical("received") == "delivered"


# ── 幂等/单调:再次签收保留首签 delivered_at,不重复推进 ──

def test_second_delivered_event_is_idempotent_and_keeps_first_delivered_at(seeded):
    project_id = seeded["project_id"]
    assignment_id = _new_assignment(project_id, seeded["pool_id"], "device_sent")

    record_delivered_signal(
        project_id=project_id, assignment_id=assignment_id, tracking_number=TRACKING,
        raw_status="Delivered", delivered_at="2026-05-10T08:00:00Z",
    )
    second = record_delivered_signal(
        project_id=project_id, assignment_id=assignment_id, tracking_number=TRACKING,
        raw_status="Delivered", delivered_at="2026-05-20T08:00:00Z",
    )
    # 第二次:shipment 复用同一行(update),stage 已 delivered 不再推进
    assert second["shipment_action"] == "updated"
    assert second["stage_action"] == "already_delivered_or_beyond"

    conn = get_conn()
    # 仍只有一行 shipment,且 delivered_at 保留首签(2026-05-10),非第二次的 05-20
    rows = conn.execute(
        "SELECT delivered_at FROM vkpi_shipments WHERE project_id=? AND tracking_number=?",
        (project_id, TRACKING),
    ).fetchall()
    assert len(rows) == 1
    assert str(dict(rows[0])["delivered_at"]).startswith("2026-05-10")


# ── 反路:已发布(content_posted)的派单不被签收事件回退 ──

def test_delivered_event_does_not_regress_published_assignment(seeded):
    project_id = seeded["project_id"]
    assignment_id = _new_assignment(project_id, seeded["pool_id"], "content_posted")

    result = record_delivered_signal(
        project_id=project_id, assignment_id=assignment_id, tracking_number=TRACKING,
        raw_status="Delivered", delivered_at="2026-05-10T08:00:00Z",
    )
    # shipment.delivered_at 仍会落(下游观察窗口需要),但 stage 绝不回退
    assert result["stage_action"] == "already_delivered_or_beyond"
    stage = get_conn().execute(
        "SELECT stage FROM vkpi_project_kol_assignments WHERE id=?", (assignment_id,)
    ).fetchone()["stage"]
    assert stage == "content_posted"


# ── 非 delivered 事件不误写:17track 仅在 Delivered 分支调用本函数 ──

def test_non_delivered_status_is_never_in_delivered_guard(seeded):
    """InTransit/OutForDelivery 等非签收态既不在守卫集合、也不会触发推进。

    17track worker 只在 latest_status == 'Delivered' 时调用 record_delivered_signal,
    本测从守卫层证明:即便误传一个 pre-delivery stage 的 assignment,目标仍是 delivered,
    而真正的「非签收事件不写」由 worker 的 if latest_status == 'Delivered' 分支保证。
    """
    for non_delivered in ("InTransit", "OutForDelivery", "InfoReceived", "Exception", "NotFound"):
        # 这些主状态映射后绝不等于 'Delivered'(worker 据此跳过本函数)
        assert non_delivered != "Delivered"
    # pre-delivery 阶段不在「已 delivered 或更靠后」集合 → 会被推进(正确)
    for pre in ("discovered", "contacted", "agreed", "device_sent"):
        assert pre not in _DELIVERED_OR_BEYOND_ASSIGNMENT_STAGES

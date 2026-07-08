"""PG 写端点/域函数 输入校验回归(PG测试3)。

铁律契约:核心写路径遇非法输入必须 400/404 而非 500 / 脏落库。域层用异常类型
表达路由映射:ValueError → 400、LookupError → 404;任何 psycopg.Error / 其它异常
穿透 = 路由 500(路由 _guard 只把 ValueError/LookupError 收编)。故本文件对域函数
直接调用,断言「抛 ValueError/LookupError」或「不落孤儿行」。

覆盖(claim/assignment 无独立写域函数,以 cost/event/shipping 三域收口):
  cost      : update_cost 金额溢出 / 币种非白名单;add_cost 溢出/FK/空必填(固化)
  event     : add_expense 金额 int32 溢出;add_material qty 溢出;create_event owner_id FK;
              add_expense id 超长
  shipping  : approve 对不存在 project/kol 落孤儿(request_approval 已修,approve 漏网)

隔离:域函数走 app 全局 get_conn() 会真提交,故用 MARKER 显式清理(前置+teardown 双清),
且每次预期失败后 rollback 解除 aborted-transaction 中毒。pg marker 缺库自动 skip 不红。
绝不写 viltrox_fit_score / rule_v0;只造测试数据。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.db.connection import get_conn
from app.domains import costs
from app.domains.events import service as events_service
from app.domains.projects import shipment_approval

pytestmark = pytest.mark.pg


MARKER = "VKPI-WRITE-VALID"
EMAIL = "vkpi-write-valid@example.com"
PROJECT_UID = "VKPI-WRITE-VALID-PROJECT"
EVENT_ID = "VKPI-WRITE-VALID-EVENT"
# 哨兵 id:极大且不存在,用于「对不存在 project/kol 写」的 FK / 孤儿断言。
SENTINEL_PID = 2_140_000_001
SENTINEL_KID = 2_140_000_002

# bigint 上界 9.22e18;此值超界必触发 NumericValueOutOfRange。
BIGINT_OVERFLOW_CENTS = 10 ** 19
# int32 上界 2_147_483_647;+1 触发 integer out of range。
INT32_OVERFLOW = 2_147_483_648


def _capture(fn, *args, **kwargs) -> tuple[Any, BaseException | None]:
    """跑 fn,吞异常返回 (result, exc);finally 一律 rollback 解除可能的 TX 中毒。

    域函数内部自行 commit;成功后 rollback 是空操作,不会撤销已提交写。失败(psycopg
    报错使 TX aborted)后 rollback 让后续 SELECT / cleanup 不被 'transaction is aborted' 连累。
    """
    try:
        return fn(*args, **kwargs), None
    except BaseException as exc:  # noqa: BLE001 — 测试要观察真实异常类型
        return None, exc
    finally:
        try:
            get_conn().rollback()
        except Exception:
            pass


def _owner_staff(staff_id: int) -> dict[str, Any]:
    return {"id": staff_id, "staff_id": staff_id, "role": "admin", "is_owner": 1}


@pytest.fixture()
def seeded() -> Any:
    """造 user/staff(owner)/kol/project/event;前置+teardown 双清,幂等可重复跑。"""
    from app.platform.db.schema import ensure_vkpi_schema
    from app.platform.db.schema_audit import ensure_vkpi_audit_schema

    ensure_vkpi_schema()
    ensure_vkpi_audit_schema()
    costs.ensure_product_cost_schema()

    conn = get_conn()
    now = "2026-05-01T10:00:00Z"

    def cleanup() -> None:
        # 顺序:子表 → 父表,避免 FK。全部按 MARKER / 哨兵 精确删。
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE metadata_json LIKE ?", (f"%{MARKER}%",))
        conn.execute("DELETE FROM vkpi_cost_ledger WHERE source_ref LIKE ?", (f"{MARKER}%",))
        conn.execute(
            "DELETE FROM vkpi_shipment_approvals WHERE project_id IN (?, ?) OR kol_pool_id IN (?, ?)",
            (SENTINEL_PID, SENTINEL_KID, SENTINEL_PID, SENTINEL_KID),
        )
        for sub in (
            "vkpi_event_expenses",
            "vkpi_event_materials",
            "vkpi_event_products",
            "vkpi_event_tasks",
            "vkpi_event_kol_invites",
        ):
            conn.execute(f"DELETE FROM {sub} WHERE event_id LIKE ?", (f"{MARKER}%",))
        conn.execute("DELETE FROM vkpi_events WHERE id LIKE ?", (f"{MARKER}%",))
        for pid in [int(r["id"]) for r in conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (PROJECT_UID,)).fetchall()]:
            conn.execute("DELETE FROM vkpi_cost_ledger WHERE project_id=?", (pid,))
            conn.execute("DELETE FROM vkpi_projects WHERE id=?", (pid,))
        for kid in [int(r["id"]) for r in conn.execute("SELECT id FROM kols WHERE channel_name=?", (MARKER,)).fetchall()]:
            conn.execute("DELETE FROM kols WHERE id=?", (kid,))
        for sid in [
            int(r["id"])
            for r in conn.execute(
                "SELECT s.id FROM staff s JOIN users u ON u.id=s.user_id WHERE u.email=?", (EMAIL,)
            ).fetchall()
        ]:
            conn.execute("DELETE FROM staff WHERE id=?", (sid,))
        for uid in [int(r["id"]) for r in conn.execute("SELECT id FROM users WHERE email=?", (EMAIL,)).fetchall()]:
            conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.commit()

    try:
        cleanup()

        conn.execute(
            """INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified)
               VALUES (?,?,?,?,?,?,?)""",
            (now, EMAIL, "v2:00:00", "Write Valid Test", "approved", "admin", 1),
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
            (MARKER, "https://example.com/vkpi-write-valid", "youtube", staff_id, staff_id, 1000, 100),
        )
        kol_id = int(conn.execute("SELECT id FROM kols WHERE channel_name=?", (MARKER,)).fetchone()["id"])

        conn.execute(
            """INSERT INTO vkpi_projects
               (project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id,
                product_sku, product_name, platform, stage, stage_status, priority,
                source_type, sample_status, metadata_json, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                PROJECT_UID, "Write Valid Project", kol_id, staff_id, staff_id,
                "VKPI-WV-SKU", "WV Product", "youtube", "shipped", "active", "normal",
                "manual", "shipped", "{}", now, now,
            ),
        )
        project_id = int(conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (PROJECT_UID,)).fetchone()["id"])

        conn.execute(
            """INSERT INTO vkpi_events
               (id, title, type_key, status, start_date, end_date, owner_id, is_public,
                product_sku, product_name, created_at, updated_at)
               VALUES (?,?,?,?,CURRENT_DATE,CURRENT_DATE,?,FALSE,?,?, NOW(), NOW())""",
            (EVENT_ID, "WV Event", "other", "planning", staff_id, "", ""),
        )
        conn.commit()

        yield {"staff_id": staff_id, "kol_id": kol_id, "project_id": project_id, "event_id": EVENT_ID}
    finally:
        cleanup()


def _make_cost(project_id: int, staff: dict[str, Any], suffix: str) -> int:
    """用 add_cost 造一条合法成本行,返回 cost_id(source_ref 带 MARKER 便于清理)。"""
    created = costs.add_cost(
        {
            "project_id": project_id,
            "cost_type": "other",
            "amount_cents": 1000,
            "currency": "USD",
            "status": "actual",
            "source_ref": f"{MARKER}-{suffix}",
            "note": MARKER,
            "metadata": {"marker": MARKER},
        },
        staff=staff,
    )["cost"]
    return int(created["id"])


# ── COST ────────────────────────────────────────────────────────────────────
def test_update_cost_amount_overflow_is_400_not_500(seeded):
    """BUG 目标:update_cost 缺 add_cost 的 MAX_AMOUNT_CENTS 上界闸 → bigint 溢出 500。"""
    staff = _owner_staff(seeded["staff_id"])
    cost_id = _make_cost(seeded["project_id"], staff, "overflow")

    result, exc = _capture(costs.update_cost, cost_id, {"amount_cents": BIGINT_OVERFLOW_CENTS}, staff=staff)

    assert isinstance(exc, ValueError), (
        "update_cost 金额越界应抛 ValueError(→400),实际 "
        + (f"抛 {type(exc).__name__}: {exc}(→500)" if exc is not None else f"接受并落库脏值 result={result}")
    )


def test_update_cost_invalid_currency_is_rejected(seeded):
    """BUG 目标:add_cost 走 normalize_currency 白名单,update_cost 无校验 → 脏币种落库。"""
    staff = _owner_staff(seeded["staff_id"])
    cost_id = _make_cost(seeded["project_id"], staff, "curr")

    result, exc = _capture(costs.update_cost, cost_id, {"currency": "NOTACURRENCY"}, staff=staff)

    # 契约:非白名单币种应 400(ValueError)。当前无校验 → 脏落库。二次确认列真被写脏。
    persisted = dict(get_conn().execute("SELECT currency FROM vkpi_cost_ledger WHERE id=?", (cost_id,)).fetchone())
    assert isinstance(exc, ValueError), (
        "update_cost 非法币种应抛 ValueError(→400);实际接受,持久化 currency="
        f"{persisted.get('currency')!r}(脏落库)"
    )


def test_add_cost_amount_overflow_is_valueerror_fixation(seeded):
    """固化:add_cost 已有金额上界闸,越界应 ValueError(→400)。与 update_cost 缺闸形成对照。"""
    staff = _owner_staff(seeded["staff_id"])
    result, exc = _capture(
        costs.add_cost,
        {"project_id": seeded["project_id"], "cost_type": "other", "amount_cents": BIGINT_OVERFLOW_CENTS,
         "source_ref": f"{MARKER}-addof", "note": MARKER, "metadata": {"marker": MARKER}},
        staff=staff,
    )
    assert isinstance(exc, ValueError), f"add_cost 越界应 ValueError,实际 {type(exc).__name__ if exc else None}: {exc}"


def test_add_cost_nonexistent_project_is_404_no_orphan(seeded):
    """固化(任务①):add_cost 对不存在 project → LookupError(→404),且不落孤儿成本行。"""
    staff = _owner_staff(seeded["staff_id"])
    result, exc = _capture(
        costs.add_cost,
        {"project_id": SENTINEL_PID, "cost_type": "other", "amount_cents": 100,
         "source_ref": f"{MARKER}-orphan", "note": MARKER, "metadata": {"marker": MARKER}},
        staff=staff,
    )
    orphan = get_conn().execute(
        "SELECT COUNT(*) AS n FROM vkpi_cost_ledger WHERE project_id=? OR source_ref=?",
        (SENTINEL_PID, f"{MARKER}-orphan"),
    ).fetchone()
    assert isinstance(exc, LookupError), f"add_cost 不存在 project 应 LookupError(→404),实际 {type(exc).__name__ if exc else None}: {exc}"
    assert int(dict(orphan)["n"]) == 0, "add_cost 对不存在 project 落了孤儿成本行"


def test_add_cost_missing_project_id_is_valueerror_fixation(seeded):
    """固化(任务④):add_cost 空必填 project_id → ValueError(→400)。"""
    staff = _owner_staff(seeded["staff_id"])
    result, exc = _capture(
        costs.add_cost,
        {"cost_type": "other", "amount_cents": 100, "source_ref": f"{MARKER}-noproj", "metadata": {"marker": MARKER}},
        staff=staff,
    )
    assert isinstance(exc, ValueError), f"add_cost 缺 project_id 应 ValueError,实际 {type(exc).__name__ if exc else None}: {exc}"


# ── EVENT ───────────────────────────────────────────────────────────────────
def test_add_expense_amount_int32_overflow_is_400_not_500(seeded):
    """BUG 目标:add_expense 未过 _validate_event_numeric,amount 直入 int32 列 → 溢出 500。"""
    result, exc = _capture(
        events_service.add_expense,
        seeded["event_id"],
        {"id": f"{MARKER}-exp-of", "amount": INT32_OVERFLOW, "category": "other"},
        _owner_staff(seeded["staff_id"]),
    )
    assert isinstance(exc, ValueError), (
        "add_expense amount 越界应抛 ValueError(→400),实际 "
        + (f"抛 {type(exc).__name__}: {exc}(→500)" if exc is not None else f"接受 result={result}")
    )


def test_add_material_qty_int32_overflow_is_400_not_500(seeded):
    """BUG 目标:add_material qty 无上界校验,直入 int32 列 → 溢出 500。"""
    result, exc = _capture(
        events_service.add_material,
        seeded["event_id"],
        {"id": f"{MARKER}-mat-of", "name": "WV", "qty": INT32_OVERFLOW},
        _owner_staff(seeded["staff_id"]),
    )
    assert isinstance(exc, ValueError), (
        "add_material qty 越界应抛 ValueError(→400),实际 "
        + (f"抛 {type(exc).__name__}: {exc}(→500)" if exc is not None else f"接受 result={result}")
    )


def test_add_expense_overlong_id_is_400_not_500(seeded):
    """BUG 目标(任务⑤):子资源 id 为 varchar(64),超长 id 直入 → StringDataRightTruncation 500。"""
    result, exc = _capture(
        events_service.add_expense,
        seeded["event_id"],
        {"id": "X" * 100, "amount": 1, "category": "other"},
        _owner_staff(seeded["staff_id"]),
    )
    assert isinstance(exc, ValueError), (
        "add_expense 超长 id 应抛 ValueError(→400),实际 "
        + (f"抛 {type(exc).__name__}: {exc}(→500)" if exc is not None else f"接受 result={result}")
    )


def test_create_event_nonexistent_owner_id_is_400_not_500(seeded):
    """BUG 目标(任务①):create_event 未校验 owner_id,不存在 staff → owner_id FK 500。"""
    result, exc = _capture(
        events_service.create_event,
        {"id": f"{MARKER}-badowner", "title": "WV", "owner_id": 2_140_000_099},
        _owner_staff(seeded["staff_id"]),
    )
    orphan = get_conn().execute(
        "SELECT COUNT(*) AS n FROM vkpi_events WHERE id=?", (f"{MARKER}-badowner",)
    ).fetchone()
    assert isinstance(exc, (ValueError, LookupError)), (
        "create_event 非法 owner_id 应 ValueError/LookupError(→400/404),实际 "
        + (f"抛 {type(exc).__name__}: {exc}(→500)" if exc is not None else f"接受 result={result}")
    )
    assert int(dict(orphan)["n"]) == 0, "create_event 对不存在 owner_id 落了孤儿活动行"


# ── SHIPPING(发货审批)──────────────────────────────────────────────────────
def test_shipment_approve_nonexistent_project_no_orphan(seeded):
    """BUG 目标:request_approval 已加 project/kol 存在校验,approve/_decide 漏网 →
    对不存在 (project,kol) INSERT 孤儿审批行(vkpi_shipment_approvals 无 FK)。"""
    staff = _owner_staff(seeded["staff_id"])
    result, exc = _capture(
        shipment_approval.approve, SENTINEL_PID, SENTINEL_KID, staff=staff, reason=MARKER
    )
    orphan = get_conn().execute(
        "SELECT COUNT(*) AS n FROM vkpi_shipment_approvals WHERE project_id=? AND kol_pool_id=?",
        (SENTINEL_PID, SENTINEL_KID),
    ).fetchone()
    assert int(dict(orphan)["n"]) == 0, (
        "approve 对不存在 project/kol 落了孤儿审批行(request_approval 已修,approve 漏网);"
        f"返回 result={result}, exc={exc!r}"
    )


def test_request_approval_nonexistent_project_rejected_no_orphan_fixation(seeded):
    """固化:request_approval 对不存在 project 返回 ok:False 且不落孤儿(与 approve 漏网对照)。"""
    staff = _owner_staff(seeded["staff_id"])
    result, exc = _capture(
        shipment_approval.request_approval, SENTINEL_PID, SENTINEL_KID, staff=staff, reason=MARKER
    )
    orphan = get_conn().execute(
        "SELECT COUNT(*) AS n FROM vkpi_shipment_approvals WHERE project_id=? AND kol_pool_id=?",
        (SENTINEL_PID, SENTINEL_KID),
    ).fetchone()
    assert exc is None and isinstance(result, dict) and result.get("ok") is False, (
        f"request_approval 对不存在 project 应 ok:False;实际 result={result}, exc={exc!r}"
    )
    assert int(dict(orphan)["n"]) == 0, "request_approval 落了孤儿审批行(应已修复)"

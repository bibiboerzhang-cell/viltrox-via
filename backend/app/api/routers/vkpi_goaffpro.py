"""V-KPI GOAFFPRO routes — Affiliate 接入只读骨架(D1,无 key 可建)。

前缀 /api/admin/vkpi/goaffpro;镜像 vkpi_shopify 的鉴权与 separation-of-duties:
- 读连接状态 GET /creds:require_tab("vkpi","read"),只回 masked。
- 写 creds POST /creds:require_tab("vkpi","admin")(公司级 token 仅 admin/owner 可改,
  与 api_key_pool / shopify creds 同档)。
- 手动 sync stub POST /sync:require_tab("vkpi","admin"),探活拉一页,不落库不归因。
- 管理层 GET /affiliates、GET /orders:显式预览 REST client,release validation 下禁用。
- 产品目录/解析会访问 provider,仅保留管理层 POST,避免 GET 被预取或重放触发外调。

无真 GOAFFPRO key 时,connection_status -> not_configured,各 list 端点 ->
{ok:false, reason:'not_configured'},Dashboard 继续诚实显示「待接入」。绝不编数。
**本刀只加 GOAFFPRO,不删/不隐藏现有自建 Links**(下一刀做)。

字段映射「待 key 校准」见 domains/integrations/goaffpro_connect.py。
与 KOL 评分域物理隔离:无 viltrox_fit_score / rule_v0 触点。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.manager_guard import require_manager_staff, require_manager_tab
from app.api.dependencies.provider_mutation import manager_provider_mutation
from app.api.dependencies.perms import require_tab
from app.api.routers import vkpi_goaffpro_summary_helpers as summary_helpers
from app.core.release_validation import release_validation_active
from app.db.connection import get_conn, table_exists
from app.domains.access import scope
from app.domains.audit.decorator import audit_action
from app.domains.integrations import goaffpro_connect


router = APIRouter(prefix="/api/admin/vkpi/goaffpro", tags=["vkpi-goaffpro"])

# 员工基础权限可自改的佣金/折扣上限(百分比);超过或非百分比(固定金额)需管理层授权。
# 读裁决 2026-07-07:0-15% 员工基础可改,>15% 升级 owner+manager。
EMPLOYEE_PCT_CAP = 15.0

# 确认态销售口径(与 goaffpro_connect._CONFIRMED_SALE_STATUSES 同白名单):归因 GMV/佣金 SUM
# 只算 approved/paid/confirmed/completed,排除 refund/cancelled/declined/pending/void/空;
# 且先按币种分组绝不把 EUR cents 加进 USD(无 FX 表)。真 key 到后按 GoAffPro 实际 status 校准。
_CONFIRMED_SALE_STATUSES = ("approved", "paid", "confirmed", "completed")


def _pct_within_employee_cap(value, kind: str) -> bool:
    """判定该笔佣金/折扣是否落在员工基础可改区间(仅 percentage 且 0-15%)。

    非 percentage(固定金额无 % 概念、更烧钱)或数值非法一律 False → 升级管理层。
    """
    if str(kind or "percentage").strip().lower() != "percentage":
        return False
    try:
        return 0.0 <= float(value) <= EMPLOYEE_PCT_CAP
    except (TypeError, ValueError):
        return False


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface DB/serialize errors as 500 with msg
        raise HTTPException(status_code=500, detail=f"goaffpro error: {exc}") from exc


# --- creds-ready: connection creds (encrypted store + settings-page fill) -----

@router.post("/creds")
def save_goaffpro_creds(
    body=Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "admin")),
):
    """Persist GOAFFPRO creds (encrypted). Returns masked-only; never echoes plaintext token.

    admin-gated: mirrors api_key_pool / shopify creds separation-of-duties —
    only admin/owner may manage company-wide live tokens.
    body: {access_token, public_token?, private_token?, api_base?}
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    return _guard(goaffpro_connect.save_credentials, body, staff)


@router.get("/creds")
def get_goaffpro_creds(
    staff=Depends(require_tab("vkpi", "read")),
):
    """Masked GOAFFPRO connection status {api_base, access_token_configured, ..., source}."""
    return _guard(goaffpro_connect.connection_status)


# --- explicit provider previews (creds-ready, manager-only) ------------------


def _assert_goaffpro_provider_read_allowed(staff: dict | None) -> None:
    """Provider-backed reads expose commerce data and consume remote capacity."""
    require_manager_staff(staff or {})
    if release_validation_active():
        raise HTTPException(status_code=503, detail="release_validation_fenced")


@router.get("/affiliates")
def list_goaffpro_affiliates(
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    staff=Depends(require_tab("vkpi", "read")),
):
    """Manager-triggered affiliate preview; may include contact fields."""
    _assert_goaffpro_provider_read_allowed(staff)
    return _guard(goaffpro_connect.list_affiliates, limit=limit, offset=offset)


@router.get("/orders")
def list_goaffpro_orders(
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    staff=Depends(require_tab("vkpi", "read")),
):
    """Manager-triggered commerce order preview."""
    _assert_goaffpro_provider_read_allowed(staff)
    return _guard(goaffpro_connect.list_orders, limit=limit, offset=offset)


# --- explicit authorization probe (business sync is separate) --------

@router.post("/sync")
def sync_goaffpro(
    staff=Depends(require_tab("vkpi", "admin")),
):
    """Probe affiliate/order read access and store authorization state, not sales."""
    return _guard(goaffpro_connect.sync_stub)


# --- D2: 一键给 KOL 建 affiliate + 追踪链 + 优惠码 + 销售归因(KOL 零注册)----------


def _load_kol_identity(conn, kol_pool_id: int) -> dict | None:
    """只读 KOL 名/handle；affiliate 查询边界内不读取任何联系人值。"""
    row = conn.execute(
        """
        SELECT id, display_name, handle
        FROM vkpi_kol_pool
        WHERE id = ?
        """,
        (kol_pool_id,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    name = str(d.get("display_name") or "").strip() or str(d.get("handle") or "").strip()
    return {"kol_pool_id": kol_pool_id, "name": name}


def _load_link(conn, kol_pool_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT kol_pool_id, affiliate_id, ref_code, tracking_url, coupon, created_at
        FROM vkpi_goaffpro_kol_links
        WHERE kol_pool_id = ?
        """,
        (kol_pool_id,),
    ).fetchone()
    return dict(row) if row else None


def _load_cached_affiliate_state(conn, affiliate_id: object) -> dict:
    """Read the last persisted provider snapshot without contacting GOAFFPRO."""
    aid = str(affiliate_id or "").strip()
    if not aid or not table_exists("vkpi_goaffpro_kol_metrics"):
        return {}
    row = conn.execute(
        """
        SELECT commission_rate, status, synced_at
        FROM vkpi_goaffpro_kol_metrics
        WHERE affiliate_id = ?
        """,
        (aid,),
    ).fetchone()
    return dict(row) if row else {}


def _assert_goaffpro_target_writable(conn, kol_pool_id: int, staff: dict | None) -> int:
    """Require a real actor and a target that is writable in MY KOL.

    Shared membership is deliberately read-only. Managers retain their existing
    all-KOL write scope; regular staff must own a favorite row for this target.
    """
    actor_id = scope.actor_staff_id(staff)
    if actor_id <= 0:
        raise HTTPException(status_code=403, detail="staff_identity_required")
    row = conn.execute(
        "SELECT id, duplicate_of_id FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="kol_pool_not_found")
    if int(dict(row).get("duplicate_of_id") or 0):
        raise HTTPException(status_code=409, detail="kol_pool_duplicate_not_writable")
    if scope.can_view_all(staff):
        return int(actor_id)
    favorite = conn.execute(
        """
        SELECT id
        FROM vkpi_kol_pool_favorites
        WHERE kol_pool_id=? AND staff_id=?
        LIMIT 1
        """,
        (int(kol_pool_id), int(actor_id)),
    ).fetchone()
    if not favorite:
        raise HTTPException(status_code=403, detail="my_kol_goaffpro_write_forbidden")
    return int(actor_id)


def _assert_goaffpro_target_readable(conn, kol_pool_id: int, staff: dict | None) -> int:
    """Allow direct-ID reads only for managers, owners, or shared members."""
    actor_id = scope.actor_staff_id(staff)
    if actor_id <= 0:
        raise HTTPException(status_code=403, detail="staff_identity_required")
    row = conn.execute(
        "SELECT id FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="kol_pool_not_found")
    if scope.can_view_all(staff):
        return int(actor_id)
    favorite = conn.execute(
        """
        SELECT id
        FROM vkpi_kol_pool_favorites
        WHERE kol_pool_id=? AND staff_id=?
        LIMIT 1
        """,
        (int(kol_pool_id), int(actor_id)),
    ).fetchone()
    if favorite:
        return int(actor_id)
    shared = conn.execute(
        """
        SELECT id
        FROM vkpi_kol_pool_members
        WHERE kol_pool_id=? AND staff_id=?
        LIMIT 1
        """,
        (int(kol_pool_id), int(actor_id)),
    ).fetchone()
    if not shared:
        raise HTTPException(status_code=403, detail="my_kol_goaffpro_read_forbidden")
    return int(actor_id)


def _assert_goaffpro_project_readable(project_id: int, staff: dict | None) -> None:
    """Apply the shared project scope before reading project commerce data."""
    try:
        scope.assert_project_access(int(project_id), staff, write=False)
    except scope.ScopeDenied as exc:
        raise HTTPException(status_code=403, detail="goaffpro_project_read_forbidden") from exc


def _assert_goaffpro_provider_write_allowed() -> None:
    """Fail closed before synchronous provider work during release validation."""
    if release_validation_active():
        raise HTTPException(status_code=503, detail="release_validation_fenced")


def _effective_tracking_url(link: dict) -> str:
    """已存映射的追踪链自愈:旧映射可能存了「光店铺首页(无 ?ref=)」——若如此,按存的
    ref_code 用修好的 referral_link 现拼 {store}/?ref={ref_code},让历史映射也自动修正,
    无需重建 affiliate。已是真追踪链(含 ref=)则原样返回。"""
    stored = str(link.get("tracking_url") or "").strip()
    low = stored.lower()
    if stored and ("ref=" in low or "/ref/" in low):
        return stored
    ref_code = str(link.get("ref_code") or "").strip()
    if ref_code:
        return goaffpro_connect.referral_link(None, ref_code)
    return stored


def _ref_is_bad(ref_code, affiliate_id) -> bool:
    """ref_code 失效判定:空,或等于 affiliate_id(数字编号被误当 ref 码 → 链追不到,须回查真码)。"""
    rc = str(ref_code or "").strip()
    return (not rc) or (rc == str(affiliate_id or "").strip())


def _tracks_now(ref_code: str, status: str) -> bool:
    """诚实标记:ref_code 拿到=链可拼可追踪;status 仅影响佣金结算(pending 也能追踪)。"""
    return bool(ref_code) and str(status or "").lower() in ("", "approved", "active", "1", "pending")


def _store_kol_link(conn, kol_pool_id: int, res: dict) -> dict:
    """把 resolve_affiliate 结果写入 vkpi_goaffpro_kol_links 并返回标准响应体。"""
    affiliate_id = str(res.get("affiliate_id") or "")
    ref_code = str(res.get("ref_code") or "")
    coupon = str(res.get("coupon") or "")
    status = str(res.get("status") or "")
    tracking_url = goaffpro_connect.referral_link(res.get("affiliate"), ref_code)
    conn.execute(
        """
        INSERT INTO vkpi_goaffpro_kol_links
            (kol_pool_id, affiliate_id, ref_code, tracking_url, coupon, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (kol_pool_id) DO UPDATE SET
            affiliate_id = excluded.affiliate_id,
            ref_code = excluded.ref_code,
            tracking_url = excluded.tracking_url,
            coupon = excluded.coupon
        """,
        (kol_pool_id, affiliate_id, ref_code, tracking_url, coupon, _utcnow()),
    )
    conn.commit()
    return {
        "ok": True,
        "linked": True,
        "affiliate_id": affiliate_id,
        "ref_code": ref_code,
        "tracking_url": tracking_url,
        "coupon": coupon,
        "status": status,
        "commission_rate": str(res.get("commission_rate") or ""),
        "tracks_now": _tracks_now(ref_code, status),
    }


def _commission_for(affiliate_id) -> str:
    """佣金比例(人话,如 '10%')—— best-effort 拉 affiliate 读 commission;失败返 ''。"""
    aid = str(affiliate_id or "").strip()
    if not aid:
        return ""
    try:
        got = goaffpro_connect.get_affiliate(aid)
        return str(got.get("commission_rate") or "") if got.get("ok") else ""
    except Exception:  # noqa: BLE001
        return ""


def _product_link_fields(ref_code: str | None, product: str | None) -> dict:
    """按产品出链:product(项目/活动的产品名或 SKU)→ Shopify handle → {store}/products/{h}?ref=。

    解析不到产品 → product_url=None(调用方/前端退回首页链,仍可追踪)。
    """
    out: dict = {"product_url": None, "product_handle": None, "product_name": None}
    code = str(ref_code or "").strip()
    q = str(product or "").strip()
    if not q or not code:
        return out
    found = goaffpro_connect.find_product_handle(q)
    if found.get("ok") and found.get("handle"):
        out["product_handle"] = found["handle"]
        out["product_name"] = found.get("name")
        out["product_url"] = goaffpro_connect.product_referral_link(found["handle"], code)
    return out


@router.post("/kol/{kol_pool_id}/link")
@audit_action(
    action_type="goaffpro_kol_link_generate",
    target_type="kol_pool",
    metadata_extractor=lambda result, kwargs: {
        "provider": "goaffpro",
        "linked": bool(result.get("linked")) if isinstance(result, dict) else False,
        "already_linked": bool(result.get("already_linked")) if isinstance(result, dict) else False,
    },
)
def link_kol_affiliate(
    kol_pool_id: int,
    product: str | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "write")),
):
    """一键给 KOL 出追踪链 + 优惠码(KOL 零注册),幂等可重生。

    已存映射且 ref_code 非空 → 直接返回；否则只按 KOL 名查已有 affiliate。
    当前 GOAFFPRO 建号需要 email，路由不得把联系人明文或合成邮箱发给 provider；
    查无已有账号时稳定返回 409，由员工在 GOAFFPRO 授权流程中完成建号。
    """
    conn = get_conn()
    _assert_goaffpro_target_writable(conn, kol_pool_id, staff)
    _assert_goaffpro_provider_write_allowed()
    goaffpro_connect.ensure_goaffpro_links_schema()
    existing = _load_link(conn, kol_pool_id)
    if existing and not _ref_is_bad(existing.get("ref_code"), existing.get("affiliate_id")):
        # 已有**有效** ref_code → 幂等返回(用 _effective_tracking_url 修正历史光链 tracking_url)。
        # ref_code 失效(空 / 等于 affiliate_id)则不走幂等,落到下面 resolve 回查真码。
        return {
            "ok": True,
            "linked": True,
            "already_linked": True,
            "affiliate_id": existing.get("affiliate_id"),
            "ref_code": existing.get("ref_code"),
            "tracking_url": _effective_tracking_url(existing),
            "coupon": existing.get("coupon"),
            "commission_rate": _commission_for(existing.get("affiliate_id")),
            "tracks_now": _tracks_now(str(existing.get("ref_code") or ""), ""),
            **_product_link_fields(existing.get("ref_code"), product),
        }

    identity = _load_kol_identity(conn, kol_pool_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="kol_pool_id not found")
    if not identity.get("name"):
        raise HTTPException(status_code=400, detail="kol has no display_name/handle to name the affiliate")

    # Provider 边界只允许 name-only lookup；禁止真实/合成联系人值和隐式建号。
    res = goaffpro_connect.resolve_affiliate(name=identity["name"], create=False)
    if not res.get("ok"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "goaffpro_affiliate_creation_requires_contact",
                "message": "GOAFFPRO affiliate creation requires an authorized contact workflow",
                "retryable": False,
            },
        )
    out = _store_kol_link(conn, kol_pool_id, res)
    out["created"] = False
    out.update(_product_link_fields(out.get("ref_code"), product))
    return out


@router.get("/kol/{kol_pool_id}/link")
def get_kol_affiliate_link(
    kol_pool_id: int,
    product: str | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
):
    """Pure local read of the persisted KOL↔affiliate mapping.

    GET never creates schema, calls GOAFFPRO, resolves products, or repairs a
    mapping. Invalid/legacy refs are reported as ``needs_regenerate`` so an
    authorized user may explicitly repair them with POST.
    """
    conn = get_conn()
    _assert_goaffpro_target_readable(conn, kol_pool_id, staff)
    if not table_exists("vkpi_goaffpro_kol_links"):
        return {
            "linked": False,
            "kol_pool_id": kol_pool_id,
            "needs_regenerate": False,
        }
    link = _load_link(conn, kol_pool_id)
    if not link:
        return {
            "linked": False,
            "kol_pool_id": kol_pool_id,
            "needs_regenerate": False,
        }
    ex_ref = str(link.get("ref_code") or "").strip()
    needs_regenerate = _ref_is_bad(ex_ref, link.get("affiliate_id"))
    cached = _load_cached_affiliate_state(conn, link.get("affiliate_id"))
    status = str(cached.get("status") or "")
    return {
        "linked": True,
        "kol_pool_id": kol_pool_id,
        "affiliate_id": link.get("affiliate_id"),
        "ref_code": link.get("ref_code"),
        "tracking_url": "" if needs_regenerate else _effective_tracking_url(link),
        "coupon": link.get("coupon"),
        "created_at": link.get("created_at"),
        "commission_rate": str(cached.get("commission_rate") or ""),
        "commission_snapshot_at": cached.get("synced_at"),
        "status": status,
        "needs_regenerate": needs_regenerate,
        "tracks_now": (not needs_regenerate) and _tracks_now(ex_ref, status),
        # Product resolution is provider-backed and therefore POST-only.
        "product_url": None,
        "product_handle": None,
        "product_name": None,
    }


@router.post("/sync-metrics")
@manager_provider_mutation(action_type="goaffpro_metrics_sync", target_type="integration", release_check=lambda: release_validation_active())
def sync_goaffpro_metrics(
    limit: int | None = Query(default=None, ge=1, le=500),
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    """手动刷新 GOAFFPRO 指标缓存(点击/订单/GMV/佣金)→ vkpi_goaffpro_kol_metrics。

    数据追踪/项目页的「刷新」按钮调它;之后 summary 读缓存秒出。也由定时任务每 20 分钟自动跑。
    """
    return goaffpro_connect.sync_kol_metrics(limit=limit)


@router.post("/kol/{kol_pool_id}/commission")
@audit_action(
    action_type="goaffpro_kol_commission_update",
    target_type="kol_pool",
    metadata_extractor=lambda result, kwargs: {
        "provider": "goaffpro",
        "ok": bool(result.get("ok")) if isinstance(result, dict) else False,
    },
)
def update_kol_commission(
    kol_pool_id: int,
    body: dict = Body(default={}),
    staff=Depends(require_tab("vkpi", "write")),
):
    """调整该 KOL affiliate 的佣金比例 → PATCH 推回 GOAFFPRO 总台。

    body {rate:number(整数), type?:'percentage'|'fixed_amount', on?:'product'|'order'}。
    改完回读 GOAFFPRO 确认(总台与 V-KPI 一致)。返回 {ok, commission_rate}。
    分档闸(读裁决 2026-07-07):percentage 且 0-15% → 员工基础可改;
    >15% 或 fixed_amount(固定金额)→ 升级 owner+manager,员工得 403。
    """
    conn = get_conn()
    _assert_goaffpro_target_writable(conn, kol_pool_id, staff)
    _assert_goaffpro_provider_write_allowed()
    goaffpro_connect.ensure_goaffpro_links_schema()
    link = _load_link(conn, kol_pool_id)
    affiliate_id = str((link or {}).get("affiliate_id") or "").strip()
    if not affiliate_id:
        raise HTTPException(status_code=400, detail="该 KOL 还没生成追踪链(无 affiliate),先生成再调佣金")
    if not isinstance(body, dict) or body.get("rate") is None:
        raise HTTPException(status_code=400, detail="rate is required")
    # 分档授权:超出员工佣金区间(>15% 或固定金额)先过管理层闸再落总台。
    if not _pct_within_employee_cap(body.get("rate"), body.get("type") or "percentage"):
        require_manager_staff(staff)
    res = goaffpro_connect.update_affiliate_commission(
        affiliate_id,
        body.get("rate"),
        ctype=str(body.get("type") or "percentage"),
        on=str(body.get("on") or "product"),
    )
    if not res.get("ok"):
        return {
            "ok": False,
            "error": res.get("error") or res.get("reason") or "update commission failed",
            "raw": res.get("raw"),
        }
    return {"ok": True, "commission_rate": res.get("commission_rate")}


@router.post("/kol/{kol_pool_id}/coupon")
@audit_action(
    action_type="goaffpro_kol_coupon_update",
    target_type="kol_pool",
    metadata_extractor=lambda result, kwargs: {
        "provider": "goaffpro",
        "ok": bool(result.get("ok")) if isinstance(result, dict) else False,
    },
)
def update_kol_coupon(
    kol_pool_id: int,
    body: dict = Body(default={}),
    staff=Depends(require_tab("vkpi", "write")),
):
    """设/改该 KOL 的专属优惠码 → PATCH 推回 GOAFFPRO 总台 + 更新本地映射。

    body {code, discount_value?, discount_type?}。code 顾客结账用即归因该 KOL。
    分档闸(读裁决 2026-07-07):percentage 折扣且 0-15% → 员工基础可设;
    >15% 或 fixed_amount(固定金额折扣)→ 升级 owner+manager,员工得 403。
    """
    conn = get_conn()
    _assert_goaffpro_target_writable(conn, kol_pool_id, staff)
    _assert_goaffpro_provider_write_allowed()
    goaffpro_connect.ensure_goaffpro_links_schema()
    link = _load_link(conn, kol_pool_id)
    affiliate_id = str((link or {}).get("affiliate_id") or "").strip()
    if not affiliate_id:
        raise HTTPException(status_code=400, detail="该 KOL 还没生成追踪链(无 affiliate),先生成再设优惠码")
    code = str((body or {}).get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="code is required")
    # 分档授权:超出员工折扣区间(>15% 或固定金额)先过管理层闸再落总台。
    if not _pct_within_employee_cap(
        (body or {}).get("discount_value", 10), (body or {}).get("discount_type") or "percentage"
    ):
        require_manager_staff(staff)
    res = goaffpro_connect.update_affiliate_coupon(
        affiliate_id,
        code,
        discount_value=(body or {}).get("discount_value", 10),
        discount_type=str((body or {}).get("discount_type") or "percentage"),
    )
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or res.get("reason"), "raw": res.get("raw")}
    new_coupon = str(res.get("coupon") or code)
    conn.execute(
        "UPDATE vkpi_goaffpro_kol_links SET coupon=? WHERE kol_pool_id=?",
        (new_coupon, kol_pool_id),
    )
    conn.commit()
    return {"ok": True, "coupon": new_coupon}


@router.post("/sync-sales")
@manager_provider_mutation(action_type="goaffpro_sales_sync", target_type="integration", release_check=lambda: release_validation_active())
def sync_goaffpro_sales(
    limit: int | None = Query(default=None, ge=1, le=500),
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    """拉 GOAFFPRO 销售 → 按 affiliate_id 回找 kol_pool_id → upsert vkpi_goaffpro_sales。

    no creds -> {ok:false, reason:'not_configured'}。返回 {synced, matched, unmatched}。
    """
    goaffpro_connect.ensure_goaffpro_links_schema()
    # 默认拉全量(循环翻页,防只取一页漏账);显式传 limit 时才单页封顶(调用方主动限流)。
    orders = goaffpro_connect.list_orders(limit=limit) if limit else goaffpro_connect.list_orders(fetch_all=True)
    if not orders.get("ok") or orders.get("partial"):
        return {
            "ok": False,
            "partial": bool(orders.get("partial")),
            "reason": orders.get("reason"),
            "error": orders.get("error"),
            "synced": 0,
            "matched": 0,
            "unmatched": 0,
        }

    conn = get_conn()
    # affiliate_id -> kol_pool_id 映射(一次性拉全表,销售匹配热路径)。
    from app.domains.integrations.goaffpro_connect_sales import prepare_sales

    links = [dict(r) for r in conn.execute("SELECT affiliate_id, kol_pool_id, coupon FROM vkpi_goaffpro_kol_links").fetchall()]
    try:
        prepared = prepare_sales(orders.get("orders") or [], links)
    except ValueError as exc:
        return {"ok": False, "reason": "invalid_sales_contract", "error": str(exc), "synced": 0, "matched": 0, "unmatched": 0}

    synced = 0
    matched = 0
    unmatched = 0
    now = _utcnow()
    for o in prepared:
        sale_id = str(o.get("id") or "").strip()
        if not sale_id:
            continue  # 无主键的行无法幂等 upsert,跳过
        affiliate_id = str(o.get("affiliate_id") or "").strip()
        kol_pool_id = o["kol_pool_id"]
        if kol_pool_id is not None:
            matched += 1
        else:
            unmatched += 1
        conn.execute(
            """
            INSERT INTO vkpi_goaffpro_sales
                (sale_id, affiliate_id, kol_pool_id, total_cents, commission_cents,
                 currency, status, occurred_at, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (sale_id) DO UPDATE SET
                affiliate_id = excluded.affiliate_id,
                kol_pool_id = excluded.kol_pool_id,
                total_cents = excluded.total_cents,
                commission_cents = excluded.commission_cents,
                currency = excluded.currency,
                status = excluded.status,
                occurred_at = excluded.occurred_at,
                synced_at = excluded.synced_at
            """,
            (
                sale_id,
                affiliate_id,
                kol_pool_id,
                o["total_cents"],
                o["commission_cents"],
                str(o.get("currency") or ""),
                str(o.get("status") or ""),
                str(o.get("created_at") or "") or None,
                now,
            ),
        )
        synced += 1
    conn.commit()
    return {"ok": True, "synced": synced, "matched": matched, "unmatched": unmatched}


def _aggregate_confirmed_sales(conn, where_sql: str, where_params: tuple) -> dict:
    """按确认态 + 按币种聚合 vkpi_goaffpro_sales,返回主币种口径 + by_currency 明细 + mixed_currency。

    where_sql = 行选择条件(如 'kol_pool_id = ?' / 'kol_pool_id IN (?, ?)'),where_params = 其绑定值。
    只算确认态(status 白名单,大小写/空白不敏感),GROUP BY currency 后取主币种(GMV 最大,并列比
    单量)上报标量;绝不把不同币种 cents 相加(无 FX 表)。返回
    {sales_count, total_cents, commission_cents, currency, by_currency, mixed_currency}——
    标量三件套均为主币种口径。NULLIF/TRIM 施于 TEXT 的 currency/status 列(非 timestamptz)。
    """
    status_ph = ",".join(["?"] * len(_CONFIRMED_SALE_STATUSES))
    rows = conn.execute(
        f"""
        SELECT COALESCE(NULLIF(TRIM(currency), ''), '') AS currency,
               COUNT(*) AS sales_count,
               COALESCE(SUM(total_cents), 0) AS total_cents,
               COALESCE(SUM(commission_cents), 0) AS commission_cents
        FROM vkpi_goaffpro_sales
        WHERE {where_sql}
          AND LOWER(TRIM(COALESCE(status, ''))) IN ({status_ph})
        GROUP BY COALESCE(NULLIF(TRIM(currency), ''), '')
        ORDER BY total_cents DESC
        """,
        (*where_params, *_CONFIRMED_SALE_STATUSES),
    ).fetchall()
    by_currency: list[dict] = []
    for r in rows:
        d = dict(r)
        by_currency.append(
            {
                "currency": str(d.get("currency") or ""),
                "sales_count": int(d.get("sales_count") or 0),
                "total_cents": int(d.get("total_cents") or 0),
                "commission_cents": int(d.get("commission_cents") or 0),
            }
        )
    if by_currency:
        primary = max(by_currency, key=lambda b: (b["total_cents"], b["sales_count"]))
    else:
        primary = {"currency": "", "sales_count": 0, "total_cents": 0, "commission_cents": 0}
    return {
        "sales_count": primary["sales_count"],
        "total_cents": primary["total_cents"],
        "commission_cents": primary["commission_cents"],
        "currency": primary["currency"],
        "by_currency": by_currency,
        "mixed_currency": len(by_currency) > 1,
    }


@router.get("/attribution")
def goaffpro_attribution(
    kol_pool_id: int | None = Query(default=None, ge=1),
    project_id: int | None = Query(default=None, ge=1),
    staff=Depends(require_tab("vkpi", "read")),
):
    """按 KOL 或项目聚合 vkpi_goaffpro_sales(SUM total/commission, COUNT)。

    诚实:没数据返回空 + note,绝不编数。
    - ?kol_pool_id= :单 KOL 聚合。
    - ?project_id=  :经 vkpi_project_kol_assignments 找该项目下所有 kol_pool_id 再聚合。
    """
    if kol_pool_id is not None:
        conn = get_conn()
        _assert_goaffpro_target_readable(conn, int(kol_pool_id), staff)
        goaffpro_connect.ensure_goaffpro_links_schema()
        agg = _aggregate_confirmed_sales(conn, "kol_pool_id = ?", (kol_pool_id,))
        count = int(agg.get("sales_count") or 0)
        if not agg.get("by_currency"):
            note = "no confirmed GOAFFPRO sales for this KOL yet"
        elif agg.get("mixed_currency"):
            note = "multiple currencies present; totals are the dominant currency only (no FX conversion)"
        else:
            note = None
        return {
            "scope": "kol",
            "kol_pool_id": kol_pool_id,
            "sales_count": count,
            "total_cents": int(agg.get("total_cents") or 0),
            "commission_cents": int(agg.get("commission_cents") or 0),
            "currency": agg.get("currency") or "",
            "by_currency": agg.get("by_currency") or [],
            "mixed_currency": bool(agg.get("mixed_currency")),
            "note": note,
        }

    if project_id is not None:
        _assert_goaffpro_project_readable(int(project_id), staff)
        goaffpro_connect.ensure_goaffpro_links_schema()
        conn = get_conn()
        kol_rows = conn.execute(
            """
            SELECT kol_pool_id
            FROM vkpi_project_kol_assignments
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchall()
        kol_ids = [int(dict(r)["kol_pool_id"]) for r in kol_rows if dict(r).get("kol_pool_id") is not None]
        if not kol_ids:
            return {
                "scope": "project",
                "project_id": project_id,
                "sales_count": 0,
                "total_cents": 0,
                "commission_cents": 0,
                "kol_count": 0,
                "note": "no KOLs assigned to this project (vkpi_project_kol_assignments)",
            }
        placeholders = ",".join(["?"] * len(kol_ids))
        agg = _aggregate_confirmed_sales(conn, f"kol_pool_id IN ({placeholders})", tuple(kol_ids))
        count = int(agg.get("sales_count") or 0)
        if not agg.get("by_currency"):
            note = "project has KOLs but no confirmed GOAFFPRO sales yet"
        elif agg.get("mixed_currency"):
            note = "multiple currencies present; totals are the dominant currency only (no FX conversion)"
        else:
            note = None
        return {
            "scope": "project",
            "project_id": project_id,
            "kol_count": len(kol_ids),
            "sales_count": count,
            "total_cents": int(agg.get("total_cents") or 0),
            "commission_cents": int(agg.get("commission_cents") or 0),
            "currency": agg.get("currency") or "",
            "by_currency": agg.get("by_currency") or [],
            "mixed_currency": bool(agg.get("mixed_currency")),
            "note": note,
        }

    raise HTTPException(status_code=400, detail="kol_pool_id or project_id is required")


def _empty_totals() -> dict:
    return {"kol_count": 0, "clicks": 0, "orders": 0, "gmv_usd": 0.0, "commission_usd": 0.0}


@router.get("/summary")
def goaffpro_summary(
    limit: int = Query(default=200, ge=1, le=1000),
    project_id: int | None = Query(default=None, ge=1),
    search: str | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
):
    """归因汇总表:每个已建链 KOL 一行(点击/订单/GMV/佣金,读缓存秒出)。

    供数据追踪表 + 项目卡复用。?project_id= 限定项目;?search= 按 KOL名/handle/ref码/优惠码 过滤。
    单次 JOIN(links+kol_pool+metrics),按 GMV 降序(头部 KOL 在前),上千 KOL 不卡。
    """
    release_fenced = release_validation_active()
    if release_fenced:
        required_tables = (
            "vkpi_goaffpro_kol_links",
            "vkpi_goaffpro_kol_metrics",
        )
        if not all(table_exists(name) for name in required_tables):
            return {
                "ok": True,
                "items": [],
                "count": 0,
                "totals": _empty_totals(),
                "note": "release validation: cached GOAFFPRO tables unavailable",
            }
    if project_id is not None:
        _assert_goaffpro_project_readable(int(project_id), staff)
    if not release_fenced:
        goaffpro_connect.ensure_goaffpro_links_schema()
    conn = get_conn()
    project_kol_ids: set[int] | None = None
    if project_id is not None:
        project_kol_ids = summary_helpers.project_kol_ids(conn, project_id)
        if not project_kol_ids:
            return {"ok": True, "items": [], "count": 0, "totals": _empty_totals(), "note": "该项目暂无派单 KOL"}
    where, sql_params = summary_helpers.build_where(staff, project_kol_ids, search)
    sql_params.append(limit)
    # 单次 JOIN:links + kol_pool(名/头像/平台)+ metrics(缓存指标),按 GMV 降序。零逐行查询。
    links = conn.execute(
        f"""
        SELECT l.kol_pool_id, l.affiliate_id, l.ref_code, l.coupon, l.tracking_url,
               kp.display_name, kp.handle, kp.avatar_url, kp.platform,
               m.clicks AS m_clicks, m.orders AS m_orders, m.gmv_cents AS m_gmv_cents,
               m.commission_cents AS m_commission_cents, m.commission_rate AS m_commission_rate,
               m.status AS m_status, m.currency AS m_currency, m.partial AS m_partial,
               m.synced_at AS m_synced_at
        FROM vkpi_goaffpro_kol_links l
        LEFT JOIN vkpi_kol_pool kp ON kp.id = l.kol_pool_id
        LEFT JOIN vkpi_goaffpro_kol_metrics m ON m.affiliate_id = l.affiliate_id
        WHERE {where}
        ORDER BY COALESCE(m.gmv_cents, 0) DESC, COALESCE(m.clicks, 0) DESC, l.created_at DESC
        LIMIT ?
        """,
        tuple(sql_params),
    ).fetchall()

    items, partial_count, stale_count, last_synced_at = summary_helpers.summary_items(links)
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "totals": summary_helpers.summary_totals(items),
        "partial_count": partial_count,
        "stale_count": stale_count,
        "last_synced_at": last_synced_at or None,
        "note": summary_helpers.summary_note(items, stale_count, partial_count),
    }


@router.post("/products")
def goaffpro_products(
    keyword: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=250),
    staff=Depends(require_tab("vkpi", "read")),
):
    """管理层显式请求店铺产品；POST 防止导航预取触发 provider 网络调用。"""
    _assert_goaffpro_provider_read_allowed(staff)
    return goaffpro_connect.list_products(keyword=keyword, limit=limit)


@router.post("/resolve-product")
def goaffpro_resolve_product(
    query: str = Query(..., min_length=1),
    staff=Depends(require_tab("vkpi", "read")),
):
    """管理层显式解析产品 handle；可能翻页访问 provider,因此不暴露为 GET。"""
    _assert_goaffpro_provider_read_allowed(staff)
    return goaffpro_connect.find_product_handle(query)

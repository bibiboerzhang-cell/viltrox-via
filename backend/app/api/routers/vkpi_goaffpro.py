"""V-KPI GOAFFPRO routes — Affiliate 接入只读骨架(D1,无 key 可建)。

前缀 /api/admin/vkpi/goaffpro;镜像 vkpi_shopify 的鉴权与 separation-of-duties:
- 读连接状态 GET /creds:require_tab("vkpi","read"),只回 masked。
- 写 creds POST /creds:require_tab("vkpi","admin")(公司级 token 仅 admin/owner 可改,
  与 api_key_pool / shopify creds 同档)。
- 手动 sync stub POST /sync:require_tab("vkpi","admin"),探活拉一页,不落库不归因。
- 只读 GET /affiliates、GET /orders:require_tab("vkpi","read"),薄透传 REST client。

无真 GOAFFPRO key 时,connection_status -> not_configured,各 list 端点 ->
{ok:false, reason:'not_configured'},Dashboard 继续诚实显示「待接入」。绝不编数。
**本刀只加 GOAFFPRO,不删/不隐藏现有自建 Links**(下一刀做)。

字段映射「待 key 校准」见 domains/integrations/goaffpro_connect.py。
与 KOL 评分域物理隔离:无 viltrox_fit_score / rule_v0 触点。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.db.connection import get_conn
from app.domains.integrations import goaffpro_connect


router = APIRouter(prefix="/api/admin/vkpi/goaffpro", tags=["vkpi-goaffpro"])


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


# --- read-only: thin REST passthrough (creds-ready) ---------------------------

@router.get("/affiliates")
def list_goaffpro_affiliates(
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    staff=Depends(require_tab("vkpi", "read")),
):
    """List affiliates. no creds -> {ok:false, reason:'not_configured'}. 字段映射待 key 校准。"""
    return _guard(goaffpro_connect.list_affiliates, limit=limit, offset=offset)


@router.get("/orders")
def list_goaffpro_orders(
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    staff=Depends(require_tab("vkpi", "read")),
):
    """List affiliate orders. no creds -> {ok:false, reason:'not_configured'}. 字段映射待 key 校准。"""
    return _guard(goaffpro_connect.list_orders, limit=limit, offset=offset)


# --- manual sync stub (D1: probe-only, no persistence/attribution yet) --------

@router.post("/sync")
def sync_goaffpro(
    staff=Depends(require_tab("vkpi", "admin")),
):
    """Manual sync stub — probe one page of affiliates + orders. no creds -> ok:false, no throw.

    D1 骨架:仅探活,不落库、不归因(落账/折扣码映射是后续刀)。
    """
    return _guard(goaffpro_connect.sync_stub)


# --- D2: 一键给 KOL 建 affiliate + 追踪链 + 优惠码 + 销售归因(KOL 零注册)----------


def _load_kol_identity(conn, kol_pool_id: int) -> dict | None:
    """读 KOL 名/handle/email:主读 vkpi_kol_pool,email 为空时退到 vkpi_kol_pool_contacts。

    返回 {kol_pool_id, name, email}。KOL 不存在 → None。
    """
    row = conn.execute(
        """
        SELECT id, display_name, handle, email
        FROM vkpi_kol_pool
        WHERE id = ?
        """,
        (kol_pool_id,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    name = str(d.get("display_name") or "").strip() or str(d.get("handle") or "").strip()
    email = str(d.get("email") or "").strip()
    if not email:
        # email 可能在联系方式留痕表(115);取最早的一条 email/business_email。
        try:
            crow = conn.execute(
                """
                SELECT contact_value
                FROM vkpi_kol_pool_contacts
                WHERE kol_pool_id = ?
                  AND contact_type IN ('email', 'business_email')
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (kol_pool_id,),
            ).fetchone()
            if crow:
                email = str(dict(crow).get("contact_value") or "").strip()
        except Exception:  # noqa: BLE001 — contacts 表缺失/无权时静默退回无 email
            email = ""
    return {"kol_pool_id": kol_pool_id, "name": name, "email": email}


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


@router.post("/kol/{kol_pool_id}/link")
def link_kol_affiliate(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "write")),
):
    """一键给 KOL 建 affiliate + 拼追踪链 + 优惠码,存映射(KOL 零注册)。

    幂等:已存映射 → 直接返回。否则:取该 KOL 名/email → create_affiliate →
    referral_link + coupon → 存 vkpi_goaffpro_kol_links。
    GOAFFPRO 建 affiliate 失败 → {ok:false, error, raw}(透出 GOAFFPRO 原始响应便于校准)。
    """
    goaffpro_connect.ensure_goaffpro_links_schema()
    conn = get_conn()
    existing = _load_link(conn, kol_pool_id)
    if existing:
        return {
            "ok": True,
            "linked": True,
            "already_linked": True,
            "affiliate_id": existing.get("affiliate_id"),
            "ref_code": existing.get("ref_code"),
            "tracking_url": _effective_tracking_url(existing),
            "coupon": existing.get("coupon"),
        }

    identity = _load_kol_identity(conn, kol_pool_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="kol_pool_id not found")
    if not identity.get("name"):
        # GOAFFPRO affiliate 至少需要一个 name;KOL 无名无 handle 时拒绝。
        raise HTTPException(status_code=400, detail="kol has no display_name/handle to name the affiliate")

    created = goaffpro_connect.create_affiliate(
        name=identity["name"],
        email=identity.get("email") or None,
    )
    if not created.get("ok"):
        # 透出 GOAFFPRO 原始响应便于校准(create body 字段 / 必填项)。
        return {
            "ok": False,
            "error": created.get("error") or created.get("reason") or "create_affiliate failed",
            "reason": created.get("reason"),
            "status_code": created.get("status_code"),
            "raw": created.get("raw"),
        }

    affiliate_raw = created.get("affiliate") or {}
    ref_code = str(created.get("ref_code") or "")
    affiliate_id = str(affiliate_raw.get("id") or ref_code or "")
    coupon = goaffpro_connect.coupon_for(affiliate_raw)
    status = str(affiliate_raw.get("status") or "")
    # GOAFFPRO 新建 affiliate 的 POST 响应当下常不返回 ref_code → 回查一次拿分配好的码,
    # 否则 referral_link 拼出来是光店铺链(追不到 KOL)。这就是之前「生成的不是追踪链」的真因。
    if not ref_code and affiliate_id:
        refetched = goaffpro_connect.get_affiliate(affiliate_id)
        if refetched.get("ok"):
            ref_code = str(refetched.get("ref_code") or ref_code or "")
            coupon = coupon or str(refetched.get("coupon") or "")
            status = status or str(refetched.get("status") or "")
            if refetched.get("affiliate"):
                affiliate_raw = refetched["affiliate"]
    tracking_url = goaffpro_connect.referral_link(affiliate_raw, ref_code)

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
        # 诚实提示:ref_code 拿到=链可拼;但归因生效还看 GOAFFPRO 审批(status)。
        "tracks_now": bool(ref_code) and status.lower() in ("", "approved", "active", "1"),
        "raw": created.get("raw"),  # 透出便于真 key 校准字段名
    }


@router.get("/kol/{kol_pool_id}/link")
def get_kol_affiliate_link(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
):
    """读 KOL↔affiliate 映射。无映射 → {linked:false}。"""
    goaffpro_connect.ensure_goaffpro_links_schema()
    conn = get_conn()
    link = _load_link(conn, kol_pool_id)
    if not link:
        return {"linked": False, "kol_pool_id": kol_pool_id}
    return {
        "linked": True,
        "kol_pool_id": kol_pool_id,
        "affiliate_id": link.get("affiliate_id"),
        "ref_code": link.get("ref_code"),
        "tracking_url": _effective_tracking_url(link),
        "coupon": link.get("coupon"),
        "created_at": link.get("created_at"),
    }


@router.post("/sync-sales")
def sync_goaffpro_sales(
    limit: int | None = Query(default=None, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "write")),
):
    """拉 GOAFFPRO 销售 → 按 affiliate_id 回找 kol_pool_id → upsert vkpi_goaffpro_sales。

    no creds -> {ok:false, reason:'not_configured'}。返回 {synced, matched, unmatched}。
    """
    goaffpro_connect.ensure_goaffpro_links_schema()
    orders = goaffpro_connect.list_orders(limit=limit)
    if not orders.get("ok"):
        return {
            "ok": False,
            "reason": orders.get("reason"),
            "error": orders.get("error"),
            "synced": 0,
            "matched": 0,
            "unmatched": 0,
        }

    conn = get_conn()
    # affiliate_id -> kol_pool_id 映射(一次性拉全表,销售匹配热路径)。
    aff_to_kol: dict[str, int] = {}
    for r in conn.execute(
        "SELECT affiliate_id, kol_pool_id FROM vkpi_goaffpro_kol_links"
    ).fetchall():
        d = dict(r)
        aid = str(d.get("affiliate_id") or "")
        if aid:
            aff_to_kol[aid] = d.get("kol_pool_id")

    synced = 0
    matched = 0
    unmatched = 0
    now = _utcnow()
    for o in orders.get("orders") or []:
        sale_id = str(o.get("id") or "").strip()
        if not sale_id:
            continue  # 无主键的行无法幂等 upsert,跳过
        affiliate_id = str(o.get("affiliate_id") or "").strip()
        kol_pool_id = aff_to_kol.get(affiliate_id)
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
                goaffpro_connect.to_cents(o.get("total")),
                goaffpro_connect.to_cents(o.get("commission")),
                str(o.get("currency") or ""),
                str(o.get("status") or ""),
                str(o.get("created_at") or "") or None,
                now,
            ),
        )
        synced += 1
    conn.commit()
    return {"ok": True, "synced": synced, "matched": matched, "unmatched": unmatched}


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
    goaffpro_connect.ensure_goaffpro_links_schema()
    conn = get_conn()

    if kol_pool_id is not None:
        row = conn.execute(
            """
            SELECT COUNT(*) AS sales_count,
                   COALESCE(SUM(total_cents), 0) AS total_cents,
                   COALESCE(SUM(commission_cents), 0) AS commission_cents
            FROM vkpi_goaffpro_sales
            WHERE kol_pool_id = ?
            """,
            (kol_pool_id,),
        ).fetchone()
        d = dict(row) if row else {}
        count = int(d.get("sales_count") or 0)
        return {
            "scope": "kol",
            "kol_pool_id": kol_pool_id,
            "sales_count": count,
            "total_cents": int(d.get("total_cents") or 0),
            "commission_cents": int(d.get("commission_cents") or 0),
            "note": None if count else "no synced GOAFFPRO sales for this KOL yet",
        }

    if project_id is not None:
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
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS sales_count,
                   COALESCE(SUM(total_cents), 0) AS total_cents,
                   COALESCE(SUM(commission_cents), 0) AS commission_cents
            FROM vkpi_goaffpro_sales
            WHERE kol_pool_id IN ({placeholders})
            """,
            tuple(kol_ids),
        ).fetchone()
        d = dict(row) if row else {}
        count = int(d.get("sales_count") or 0)
        return {
            "scope": "project",
            "project_id": project_id,
            "kol_count": len(kol_ids),
            "sales_count": count,
            "total_cents": int(d.get("total_cents") or 0),
            "commission_cents": int(d.get("commission_cents") or 0),
            "note": None if count else "project has KOLs but no synced GOAFFPRO sales yet",
        }

    raise HTTPException(status_code=400, detail="kol_pool_id or project_id is required")

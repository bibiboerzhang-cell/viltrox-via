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

import re
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


def _effective_kol_email(identity: dict | None, kol_pool_id: int) -> tuple[str, bool]:
    """KOL 有真 email 用真的;没有 → 合成**确定性**占位邮箱。

    为什么:实测 GOAFFPRO 建 affiliate 缺 email 会软失败(HTTP 200 + {error})。确定性
    (基于 name + kol_pool_id)保证 POST 建 / GET 搜命中同一个邮箱 → 幂等不重复建号。
    返回 (email, is_synthetic)。占位邮箱用 viltroxvia.com 域,用户拿到 KOL 真邮箱后可在
    GOAFFPRO 后台改。
    """
    real = str((identity or {}).get("email") or "").strip()
    if real:
        return real, False
    base = re.sub(r"[^a-z0-9]+", "", str((identity or {}).get("name") or "").lower())[:30] or f"kol{kol_pool_id}"
    return f"{base}.kol{kol_pool_id}@viltroxvia.com", True


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
def link_kol_affiliate(
    kol_pool_id: int,
    product: str | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "write")),
):
    """一键给 KOL 出追踪链 + 优惠码(KOL 零注册),幂等可重生。

    流程(2026-06-17 根因重构):已存映射且 ref_code 非空 → 直接返回。否则取 KOL 名/email →
    resolve_affiliate(search-first:先搜已存在的,找不到再建+审批)→ ?id= 回查 ref_code →
    存 vkpi_goaffpro_kol_links。search-first 修掉了「早期建失败留空映射 + 不能重生」的坑。
    """
    goaffpro_connect.ensure_goaffpro_links_schema()
    conn = get_conn()
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

    # 缺真 email → 合成确定性占位邮箱(GOAFFPRO 缺 email 软失败的修法)。
    email, email_synth = _effective_kol_email(identity, kol_pool_id)
    # search-first + create-if-absent + ?id= 回查 ref_code(一站式确保 affiliate 真存在)。
    res = goaffpro_connect.resolve_affiliate(name=identity["name"], email=email, create=True)
    if not res.get("ok"):
        return {
            "ok": False,
            "error": res.get("error") or res.get("reason") or "resolve_affiliate failed",
            "reason": res.get("reason"),
            "status_code": res.get("status_code"),
            "raw": res.get("raw"),
        }
    out = _store_kol_link(conn, kol_pool_id, res)
    out["created"] = res.get("created", False)
    out["email_synthetic"] = email_synth
    out.update(_product_link_fields(out.get("ref_code"), product))
    return out


@router.get("/kol/{kol_pool_id}/link")
def get_kol_affiliate_link(
    kol_pool_id: int,
    product: str | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
):
    """读 KOL↔affiliate 映射。无映射 → {linked:false}。

    自愈(只读不建号):旧映射 ref_code 为空(早期建失败/端点错留下的废映射)→ 用
    resolve_affiliate(create=False) **搜**已存在的 affiliate 补码,搜不到则标 needs_regenerate
    (让前端显「重新生成」按钮);GET 不创建 affiliate(建号是 POST 的副作用)。
    """
    goaffpro_connect.ensure_goaffpro_links_schema()
    conn = get_conn()
    link = _load_link(conn, kol_pool_id)
    if not link:
        return {"linked": False, "kol_pool_id": kol_pool_id}
    ex_ref = str(link.get("ref_code") or "").strip()
    needs_regenerate = False
    # 失效=空 或 ref_code 等于 affiliate_id(数字编号误当 ref 码)→ 搜回真码自愈。
    if _ref_is_bad(ex_ref, link.get("affiliate_id")):
        identity = _load_kol_identity(conn, kol_pool_id)
        email = _effective_kol_email(identity, kol_pool_id)[0] if identity else None
        res = goaffpro_connect.resolve_affiliate(
            name=(identity or {}).get("name") or "",
            email=email,  # 同 POST 的确定性占位邮箱 → 搜得到 POST 建的那个
            create=False,  # GET 只搜不建
        )
        if res.get("ok") and res.get("ref_code") and not _ref_is_bad(res.get("ref_code"), res.get("affiliate_id")):
            new_url = goaffpro_connect.referral_link(res.get("affiliate"), res["ref_code"])
            new_coupon = str(link.get("coupon") or "") or str(res.get("coupon") or "")
            conn.execute(
                "UPDATE vkpi_goaffpro_kol_links SET affiliate_id=?, ref_code=?, tracking_url=?, coupon=? WHERE kol_pool_id=?",
                (str(res.get("affiliate_id") or ""), str(res["ref_code"]), new_url, new_coupon, kol_pool_id),
            )
            conn.commit()
            link = dict(link, affiliate_id=res.get("affiliate_id"), ref_code=res["ref_code"], tracking_url=new_url, coupon=new_coupon)
            ex_ref = str(res["ref_code"])
        else:
            # 搜不到 affiliate(早期废映射)→ 让前端给「重新生成」按钮触发 POST 真建号。
            needs_regenerate = True
    return {
        "linked": True,
        "kol_pool_id": kol_pool_id,
        "affiliate_id": link.get("affiliate_id"),
        "ref_code": link.get("ref_code"),
        "tracking_url": _effective_tracking_url(link),
        "coupon": link.get("coupon"),
        "created_at": link.get("created_at"),
        "commission_rate": _commission_for(link.get("affiliate_id")) if not needs_regenerate else "",
        "needs_regenerate": needs_regenerate,
        "tracks_now": _tracks_now(ex_ref, ""),
        **_product_link_fields(link.get("ref_code"), product),
    }


@router.post("/sync-metrics")
def sync_goaffpro_metrics(
    limit: int | None = Query(default=None, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    """手动刷新 GOAFFPRO 指标缓存(点击/订单/GMV/佣金)→ vkpi_goaffpro_kol_metrics。

    数据追踪/项目页的「刷新」按钮调它;之后 summary 读缓存秒出。也由定时任务每 20 分钟自动跑。
    """
    return goaffpro_connect.sync_kol_metrics(limit=limit)


@router.post("/kol/{kol_pool_id}/commission")
def update_kol_commission(
    kol_pool_id: int,
    body: dict = Body(default={}),
    staff=Depends(require_tab("vkpi", "write")),
):
    """调整该 KOL affiliate 的佣金比例 → PATCH 推回 GOAFFPRO 总台。

    body {rate:number(整数), type?:'percentage'|'fixed_amount', on?:'product'|'order'}。
    改完回读 GOAFFPRO 确认(总台与 V-KPI 一致)。返回 {ok, commission_rate}。
    """
    goaffpro_connect.ensure_goaffpro_links_schema()
    conn = get_conn()
    link = _load_link(conn, kol_pool_id)
    affiliate_id = str((link or {}).get("affiliate_id") or "").strip()
    if not affiliate_id:
        raise HTTPException(status_code=400, detail="该 KOL 还没生成追踪链(无 affiliate),先生成再调佣金")
    if not isinstance(body, dict) or body.get("rate") is None:
        raise HTTPException(status_code=400, detail="rate is required")
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


def _empty_totals() -> dict:
    return {"kol_count": 0, "clicks": 0, "orders": 0, "gmv_usd": 0.0, "commission_usd": 0.0}


@router.get("/summary")
def goaffpro_summary(
    limit: int = Query(default=100, ge=1, le=500),
    project_id: int | None = Query(default=None, ge=1),
    staff=Depends(require_tab("vkpi", "read")),
):
    """归因汇总表:每个已建链的 KOL 一行(点击/订单/GMV/佣金),实时来自 GOAFFPRO。

    供 Shopify Hub「数据追踪」表 + 项目卡复用。?project_id= → 只汇总该项目下的 KOL。
    返回 {items, totals(汇总 clicks/orders/gmv_usd/commission_usd), count, note}。诚实空表带 note。
    """
    goaffpro_connect.ensure_goaffpro_links_schema()
    conn = get_conn()
    # project_id → 限定该项目下的 kol_pool_id(经 vkpi_project_kol_assignments)。
    project_kol_ids: set[int] | None = None
    if project_id is not None:
        rows = conn.execute(
            "SELECT DISTINCT kol_pool_id FROM vkpi_project_kol_assignments WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        project_kol_ids = {int(dict(r)["kol_pool_id"]) for r in rows if dict(r).get("kol_pool_id") is not None}
        if not project_kol_ids:
            return {"ok": True, "items": [], "count": 0, "totals": _empty_totals(), "note": "该项目暂无派单 KOL"}
    # 修 bug:project_id 时把 IN(...) 过滤放进 SQL(LIMIT 之前),避免「先 LIMIT 后过滤」漏掉
    # 排在 100 名之后的该项目 KOL。
    where = "COALESCE(affiliate_id, '') <> '' AND COALESCE(ref_code, '') <> ''"
    sql_params: list = []
    if project_kol_ids is not None:
        where += " AND kol_pool_id IN (" + ",".join(["?"] * len(project_kol_ids)) + ")"
        sql_params.extend(sorted(project_kol_ids))
    sql_params.append(limit)
    links = conn.execute(
        f"""
        SELECT kol_pool_id, affiliate_id, ref_code, coupon, tracking_url
        FROM vkpi_goaffpro_kol_links
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        tuple(sql_params),
    ).fetchall()

    # 性能落库:从指标缓存表读(秒出),不再逐 KOL 实时打 GOAFFPRO 3 次。
    metrics_by_aid: dict[str, dict] = {}
    for mr in conn.execute("SELECT * FROM vkpi_goaffpro_kol_metrics").fetchall():
        md = dict(mr)
        metrics_by_aid[str(md.get("affiliate_id") or "")] = md

    items: list[dict] = []
    partial_count = 0
    stale_count = 0
    last_synced_at = ""
    for r in links:
        d = dict(r)
        aid = str(d.get("affiliate_id") or "")
        if not aid:
            continue
        name_row = conn.execute(
            "SELECT display_name, handle, avatar_url, platform FROM vkpi_kol_pool WHERE id = ?",
            (d.get("kol_pool_id"),),
        ).fetchone()
        nd = dict(name_row) if name_row else {}
        handle = str(nd.get("handle") or "").strip()
        nm = str(nd.get("display_name") or "").strip() or handle
        m = metrics_by_aid.get(aid)
        if m is None:
            # 还没同步过(刚建链)→ 显 0 + stale,等下次定时/手动刷新填实。
            stale_count += 1
            m = {}
        else:
            sa = str(m.get("synced_at") or "")
            if sa > last_synced_at:
                last_synced_at = sa
        is_partial = bool(m.get("partial"))
        if is_partial:
            partial_count += 1
        gmv_cents = int(m.get("gmv_cents") or 0)
        comm_cents = int(m.get("commission_cents") or 0)
        items.append(
            {
                "kol_pool_id": d.get("kol_pool_id"),
                "kol_name": nm or f"KOL#{d.get('kol_pool_id')}",
                "kol_handle": handle,
                "kol_avatar": str(nd.get("avatar_url") or ""),
                "kol_platform": str(nd.get("platform") or ""),
                "affiliate_id": aid,
                "ref_code": d.get("ref_code"),
                "coupon": d.get("coupon"),
                "commission_rate": str(m.get("commission_rate") or ""),
                "status": str(m.get("status") or ""),
                "tracking_url": d.get("tracking_url"),
                "source_label": "GOAFFPRO",
                "source_type": "goaffpro",
                "product_sku": "—",
                "clicks": int(m.get("clicks") or 0),
                "orders": int(m.get("orders") or 0),
                "gmv_usd": round(gmv_cents / 100, 2),
                "commission_usd": round(comm_cents / 100, 2),
                "currency": str(m.get("currency") or ""),
                "partial": is_partial,
                "stale": aid not in metrics_by_aid,  # True = 尚未同步过
            }
        )
    totals = {
        "kol_count": len(items),
        "clicks": sum(int(it.get("clicks") or 0) for it in items),
        "orders": sum(int(it.get("orders") or 0) for it in items),
        "gmv_usd": round(sum(float(it.get("gmv_usd") or 0) for it in items), 2),
        "commission_usd": round(sum(float(it.get("commission_usd") or 0) for it in items), 2),
    }
    note = None if items else "尚无已建链的 KOL;在 KOL 详情或项目里生成追踪链后出现在此。"
    if stale_count:
        note = f"{stale_count} 个 KOL 刚建链还没同步,点「刷新」拉取最新数据。"
    elif partial_count:
        note = f"⚠️ {partial_count} 个 KOL 上次同步查询失败,显示值可能偏低(非真零)。"
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "totals": totals,
        "partial_count": partial_count,
        "stale_count": stale_count,
        "last_synced_at": last_synced_at or None,
        "note": note,
    }


@router.get("/products")
def goaffpro_products(
    keyword: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=250),
    staff=Depends(require_tab("vkpi", "read")),
):
    """列店铺真实产品(带 handle),供按产品出链的产品选择器。可 keyword 搜。"""
    return goaffpro_connect.list_products(keyword=keyword, limit=limit)


@router.get("/resolve-product")
def goaffpro_resolve_product(
    query: str = Query(..., min_length=1),
    staff=Depends(require_tab("vkpi", "read")),
):
    """把产品名/SKU 解析成 Shopify handle(项目/活动绑产品出链用)。无信心匹配 → ok:false。"""
    return goaffpro_connect.find_product_handle(query)

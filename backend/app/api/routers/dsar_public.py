"""DSAR 公开申请通道 + 法务页 SPA 分发 + 员工审批口(公测阻断 B:L1+L3 / BK-08)。

三个子路由挂在一个 ``router`` 下(注册表 ADMIN_ROUTER_MODULES 只 include ``.router``):

* ``/api/public/dsar/requests``(POST,匿名,按 IP 限流 5 次/小时,验证码占位)
  → 落既有 ``vkpi_dsar_requests`` 工单(status=pending,source=public_form);
  勿联系申请再走既有 ``contact_suppression.record_suppression``(只抑制申请人自报的邮箱,
  主体档案里的其他联系方式留给员工审批后抑制——防止匿名表单被用来批量封锁外联)。
* ``/api/public/legal/policy``(GET,匿名)→ 隐私页读取的保留期策略键(与
  ``app.services.scheduler.jobs_retention`` 同名 env 键、同默认值)与联系邮箱占位。
* ``/legal`` / ``/legal/{page}``(GET)→ SPA 分发(与 /activate /reset 同款)。
* ``/api/admin/vkpi/dsar/requests``(vkpi:admin)→ 列表(联系邮箱脱敏)/ 详情(审计留痕)/
  审批 / 执行(erasure → 既有 ``dsar_erasure.erase_subject``;do_not_contact → 主体档案
  联系方式逐条进抑制台账;access → 人工答复后标记 done)。

红线:响应与日志绝不回显申请人邮箱明文;不触 viltrox_fit_score / rule_v0;
同步路由用 get_conn(禁 async 路由直连);任何读端失败诚实 5xx,不伪装成功。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists
from app.services.security.rate_limiter import get_client_ip, rate_limit

logger = get_logger(__name__)

router = APIRouter(tags=["dsar"])
public_router = APIRouter(prefix="/api/public", tags=["dsar-public"])
admin_router = APIRouter(prefix="/api/admin/vkpi/dsar", tags=["dsar-admin"])
legal_router = APIRouter(tags=["legal-pages"])

REQUEST_TYPES = ("erasure", "access", "do_not_contact")
PLATFORMS = ("youtube", "instagram", "tiktok", "bilibili", "other")
LEGAL_PAGES = ("terms", "privacy", "data-sources", "request")
RATE_BUCKET = "dsar_public"
RATE_MAX_REQUESTS = 5
RATE_WINDOW_SEC = 3600
DSAR_SLA_DAYS = 30
POLICY_VERSION = "2026-09-02-draft"
CAPTCHA_MODE_ENV = "VKPI_DSAR_CAPTCHA_MODE"  # off(默认,占位) | shared_secret
CAPTCHA_SECRET_ENV = "VKPI_DSAR_CAPTCHA_SECRET"
IP_HASH_KEY_ENV = "VKPI_DSAR_IP_HASH_KEY"
BRAND_SCOPE_ENV = "VKPI_DSAR_BRAND_SCOPE"
CONTACT_EMAIL_ENV = "VKPI_PRIVACY_CONTACT_EMAIL"
CONTACT_EMAIL_PLACEHOLDER = "privacy@viltrox.com"
PURGE_GATE_ENV = "VKPI_DATA_RETENTION_PURGE"
RETENTION_TASK_KEY = "vkpi_data_retention_purge"
# 与 app.services.scheduler.jobs_retention.retention_policy() 同键同默认(该模块禁被 API 层 import,
# 这里镜像键名;tests/test_dsar_public_router.py 钉住两边一致)。
RETENTION_POLICY_KEYS: tuple[dict[str, Any], ...] = (
    {
        "bucket": "apify_payload",
        "key": "VKPI_RETENTION_APIFY_PAYLOAD_DAYS",
        "default_days": 90,
        "label_zh": "第三方平台原始抓取载荷(任务终态后)",
        "label_en": "Raw platform fetch payloads (after job completion)",
    },
    {
        "bucket": "comments",
        "key": "VKPI_RETENTION_COMMENTS_DAYS",
        "default_days": 180,
        "label_zh": "公开评论原文",
        "label_en": "Public comment text",
    },
    {
        "bucket": "portal_tokens",
        "key": "VKPI_PORTAL_TOKEN_TTL_DAYS",
        "default_days": 90,
        "label_zh": "创作者门户访问令牌",
        "label_en": "Creator portal access tokens",
    },
)
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[A-Za-z]{2,24}$")
_HANDLE_RE = re.compile(r"^[A-Za-z0-9._\-]{1,120}$")
_TRUTHY = {"1", "true", "yes", "on"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        return default
    return value if value > 0 else default


def _brand_scope() -> str:
    configured = os.getenv(BRAND_SCOPE_ENV, "").strip().lower()
    if configured:
        return configured
    try:
        from app.domains.platform.tenancy import DEFAULT_ORG_ID
    except ImportError:
        return "organization:1"
    return f"organization:{int(DEFAULT_ORG_ID)}"


def _ip_hash(ip: str) -> str:
    key = (os.getenv(IP_HASH_KEY_ENV) or os.getenv("VKPI_CONTACT_SUPPRESSION_HMAC_KEY") or "vkpi-dsar-ip-v1").encode("utf-8")
    return hmac.new(key, str(ip or "").encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def mask_contact(value: Any) -> str:
    """邮箱脱敏:a***@example.com;非邮箱一律 ***。"""
    text = str(value or "").strip()
    if "@" not in text:
        return "***" if text else ""
    local, _, domain = text.partition("@")
    return f"{local[:1]}***@{domain}"


def retention_policy_public() -> dict[str, Any]:
    """隐私页消费的保留期策略(键名 + 当前生效天数 + 放量闸状态),零 PII。"""
    buckets = [
        {
            "bucket": item["bucket"],
            "policy_key": item["key"],
            "days": _env_int(item["key"], int(item["default_days"])),
            "default_days": int(item["default_days"]),
            "label_zh": item["label_zh"],
            "label_en": item["label_en"],
        }
        for item in RETENTION_POLICY_KEYS
    ]
    buckets.append(
        {
            "bucket": "suppressed_contacts",
            "policy_key": "contact_suppression",
            "days": 0,
            "default_days": 0,
            "label_zh": "已抑制(勿联系)的联系方式明文——即时清理",
            "label_en": "Suppressed (do-not-contact) contact details — cleared immediately",
        }
    )
    contact_email = os.getenv(CONTACT_EMAIL_ENV, "").strip()
    return {
        "status": "draft",
        "draft": True,
        "legal_review": "pending",
        "version": POLICY_VERSION,
        "contact_email": contact_email or CONTACT_EMAIL_PLACEHOLDER,
        "contact_email_configured": bool(contact_email),
        "retention": buckets,
        "purge_task_key": RETENTION_TASK_KEY,
        "purge_gate_env": PURGE_GATE_ENV,
        "purge_enabled": os.getenv(PURGE_GATE_ENV, "").strip().lower() in _TRUTHY,
        "dsar_sla_days": DSAR_SLA_DAYS,
        "public_form_path": "/legal/request",
        "request_types": list(REQUEST_TYPES),
        "platforms": list(PLATFORMS),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 公开表单
# ─────────────────────────────────────────────────────────────────────────────
class DsarPublicRequestBody(BaseModel):
    request_type: str = Field(default="", max_length=32)
    platform: str = Field(default="", max_length=32)
    handle: str = Field(default="", max_length=160)
    profile_url: str = Field(default="", max_length=500)
    contact_email: str = Field(default="", max_length=254)
    message: str = Field(default="", max_length=2000)
    captcha_token: str = Field(default="", max_length=2048)
    consent_confirmed: bool = False
    # 蜜罐:真人看不见的字段,机器人填了就拒。
    website: str = Field(default="", max_length=200)


def _reject(code: str, message_zh: str, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message_zh})


def _captcha_gate(token: str) -> None:
    """验证码占位:默认 off(不校验);shared_secret 模式要求 token 等于共享密钥。

    接 Turnstile / hCaptcha 时只替换本函数(server-side verify),表单字段名不变。
    """
    mode = os.getenv(CAPTCHA_MODE_ENV, "off").strip().lower() or "off"
    if mode == "off":
        return
    if mode != "shared_secret":
        raise _reject("captcha_mode_invalid", "验证码配置无效,请稍后再试", status_code=503)
    secret = os.getenv(CAPTCHA_SECRET_ENV, "").strip()
    if not secret or not hmac.compare_digest(str(token or ""), secret):
        raise _reject("captcha_failed", "验证码校验未通过")


def _normalize_handle(raw: str) -> str:
    text = str(raw or "").strip().lstrip("@")
    if not text:
        return ""
    if not _HANDLE_RE.fullmatch(text):
        raise _reject("handle_invalid", "账号名只能包含字母、数字、点、下划线和连字符")
    return text


def _normalize_profile_url(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if not text.lower().startswith("https://") or len(text) > 500 or any(ch.isspace() for ch in text):
        raise _reject("profile_url_invalid", "主页链接必须是 https:// 开头的完整地址")
    return text


def _closed_choice(raw: str, choices: tuple[str, ...], code: str, message_zh: str) -> str:
    value = str(raw).strip().lower()
    if value not in choices:
        raise _reject(code, message_zh)
    return value


def _normalize_email(raw: str) -> str:
    email = str(raw).strip().lower()
    if not _EMAIL_RE.fullmatch(email):
        raise _reject("contact_email_invalid", "回复邮箱格式无效")
    return email


def _require_subject_and_consent(handle: str, profile_url: str, consent_confirmed: bool) -> None:
    if not handle and not profile_url:
        raise _reject("subject_missing", "请至少填写账号名或主页链接")
    if not consent_confirmed:
        raise _reject("consent_required", "请确认你是该账号本人或其授权代表")


def validate_public_request(body: DsarPublicRequestBody) -> dict[str, Any]:
    """闭集校验;返回规范化 dict。任何失败 400 + 稳定 code(前端按 code 映射文案)。"""
    if str(body.website).strip():
        raise _reject("rejected", "请求被拒绝")
    request_type = _closed_choice(body.request_type, REQUEST_TYPES, "request_type_invalid", "申请类型无效")
    platform = _closed_choice(body.platform, PLATFORMS, "platform_invalid", "平台无效")
    handle = _normalize_handle(body.handle)
    profile_url = _normalize_profile_url(body.profile_url)
    email = _normalize_email(body.contact_email)
    _require_subject_and_consent(handle, profile_url, bool(body.consent_confirmed))
    return {
        "request_type": request_type,
        "platform": platform,
        "handle": handle,
        "profile_url": profile_url,
        "contact_email": email,
        "message": " ".join(str(body.message).split())[:2000],
        "captcha_token": str(body.captcha_token),
    }


def resolve_subject(conn: Any, platform: str, handle: str, profile_url: str) -> dict[str, Any] | None:
    """按 platform+handle(不区分大小写)或 profile_url 定位池内主体;找不到诚实 None。"""
    if not table_exists("vkpi_kol_pool"):
        return None
    row = None
    if handle and platform != "other":
        row = conn.execute(
            "SELECT id, platform, handle FROM vkpi_kol_pool WHERE platform=? AND LOWER(handle)=LOWER(?) ORDER BY id LIMIT 1",
            (platform, handle),
        ).fetchone()
    if row is None and profile_url:
        row = conn.execute(
            "SELECT id, platform, handle FROM vkpi_kol_pool WHERE LOWER(profile_url)=LOWER(?) ORDER BY id LIMIT 1",
            (profile_url,),
        ).fetchone()
    return dict(row) if row is not None else None


def _insert_ticket(conn: Any, payload: dict[str, Any], subject: dict[str, Any] | None, ip_hash: str) -> tuple[int, str]:
    public_ref = f"DSAR-{secrets.token_hex(4).upper()}"
    note = f"public_form; subject_resolved={'yes' if subject else 'no'}"
    cursor = conn.execute(
        """
        INSERT INTO vkpi_dsar_requests
            (request_type, subject_kol_pool_id, subject_handle_snapshot, subject_platform_snapshot,
             status, jurisdiction, note, source, public_ref, requester_contact, requester_message,
             subject_profile_url, suppression_json, client_ip_hash)
        VALUES (?,?,?,?,'pending','',?,'public_form',?,?,?,?,'{}',?)
        RETURNING id
        """,
        (
            payload["request_type"],
            int(subject["id"]) if subject else None,
            str((subject or {}).get("handle") or payload["handle"] or ""),
            str((subject or {}).get("platform") or payload["platform"] or ""),
            note,
            public_ref,
            payload["contact_email"],
            payload["message"],
            payload["profile_url"],
            ip_hash,
        ),
    )
    row = cursor.fetchone()
    ticket_id = int(dict(row).get("id") or row[0])
    conn.commit()
    return ticket_id, public_ref


def apply_self_suppression(conn: Any, subject_id: int | None, contact_email: str) -> dict[str, Any]:
    """勿联系:只抑制申请人自报的邮箱(本人可无条件要求不被联系);主体未定位 → 留给员工。"""
    if subject_id is None:
        return {"status": "deferred", "reason": "subject_unresolved"}
    from app.domains.kol import contact_suppression as suppression

    try:
        result = suppression.record_suppression(
            kol_pool_id=int(subject_id),
            contact_type="email",
            contact_value=contact_email,
            brand_scope=_brand_scope(),
            reason="legal_request",
            source_type="reply",
            conn=conn,
        )
    except suppression.SuppressionConfigurationError:
        return {"status": "deferred", "reason": "fingerprint_key_unavailable"}
    except suppression.ContactValidationError:
        return {"status": "deferred", "reason": "contact_invalid"}
    except Exception:
        logger.warning("dsar.public.suppression_failed", exc_info=True)
        return {"status": "deferred", "reason": "suppression_failed"}
    return {"status": "suppressed", "channel": result.get("channel"), "reason": "legal_request"}


def _store_suppression_summary(conn: Any, ticket_id: int, summary: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE vkpi_dsar_requests SET suppression_json=? WHERE id=?",
        (json.dumps(summary, ensure_ascii=False), int(ticket_id)),
    )
    conn.commit()


@public_router.post("/dsar/requests")
@rate_limit(RATE_BUCKET, max_requests=RATE_MAX_REQUESTS, window_sec=RATE_WINDOW_SEC)
def submit_public_dsar_request(body: DsarPublicRequestBody, request: Request) -> dict[str, Any]:
    payload = validate_public_request(body)
    _captcha_gate(payload["captcha_token"])
    if not table_exists("vkpi_dsar_requests"):
        raise _reject("channel_unavailable", "申请通道暂不可用,请通过隐私页邮箱联系我们", status_code=503)
    conn = get_conn()
    subject = resolve_subject(conn, payload["platform"], payload["handle"], payload["profile_url"])
    try:
        ticket_id, public_ref = _insert_ticket(conn, payload, subject, _ip_hash(get_client_ip(request)))
    except Exception:
        conn.rollback()
        logger.exception("dsar.public.insert_failed")
        raise _reject("channel_unavailable", "申请暂时无法登记,请稍后再试", status_code=503) from None
    if payload["request_type"] == "do_not_contact":
        summary = apply_self_suppression(conn, (subject or {}).get("id"), payload["contact_email"])
        _store_suppression_summary(conn, ticket_id, summary)
    logger.info(
        "dsar.public.received",
        extra={"ticket_id": ticket_id, "request_type": payload["request_type"], "platform": payload["platform"]},
    )
    # 刻意不回显 subject 是否命中(避免成为「我们是否持有某账号数据」的探针)。
    return {
        "status": "received",
        "public_ref": public_ref,
        "request_type": payload["request_type"],
        "sla_days": DSAR_SLA_DAYS,
        "suppression": {"status": "recorded"} if payload["request_type"] == "do_not_contact" else None,
    }


@public_router.get("/legal/policy")
def public_legal_policy() -> dict[str, Any]:
    return retention_policy_public()


# ─────────────────────────────────────────────────────────────────────────────
# 法务页 SPA 分发(与 /activate /reset 同款;admin 与 public 两种角色都需要能打开)
# ─────────────────────────────────────────────────────────────────────────────
def _serve_spa() -> Any:
    # 分发逻辑(index.html + no-store 头 + X-VKPI-Build-SHA)只在 app.main 一处;它挂载本路由时必然已加载。
    # 用 sys.modules 现取而不写 import 语句:静态图上不给 main ↔ router 添环(环只减不增棘轮)。
    main_module = sys.modules.get("app.main")
    if main_module is None:
        raise HTTPException(status_code=503, detail="Frontend host is not initialised")
    return main_module._serve_frontend()


@legal_router.get("/legal", include_in_schema=False)
@legal_router.get("/privacy", include_in_schema=False)
@legal_router.get("/terms", include_in_schema=False)
def legal_index() -> Any:
    """/privacy 与 /terms 是测试者手册 / 交接包里写定的短地址;前端路由把它们转到 /legal/*。"""
    return _serve_spa()


@legal_router.get("/legal/{page}", include_in_schema=False)
def legal_page(page: str) -> Any:
    if page not in LEGAL_PAGES:
        raise HTTPException(status_code=404, detail="Legal page not found")
    return _serve_spa()


# ─────────────────────────────────────────────────────────────────────────────
# 员工审批口(vkpi:admin)
# ─────────────────────────────────────────────────────────────────────────────
_ADMIN_LIST_COLUMNS = (
    "id, request_type, subject_kol_pool_id, subject_handle_snapshot, subject_platform_snapshot, "
    "status, source, public_ref, requester_contact, subject_profile_url, suppression_json, "
    "jurisdiction, note, created_at, executed_at, requested_by_staff_id, approved_by_staff_id"
)


def _staff_id(staff: dict[str, Any] | None) -> int:
    try:
        return int((staff or {}).get("id") or (staff or {}).get("staff_id") or 0)
    except (TypeError, ValueError):
        return 0


def _public_row(row: dict[str, Any], *, reveal_contact: bool) -> dict[str, Any]:
    item = dict(row)
    contact = str(item.pop("requester_contact", "") or "")
    item["requester_contact_masked"] = mask_contact(contact)
    if reveal_contact:
        item["requester_contact"] = contact
    raw = item.get("suppression_json")
    try:
        item["suppression"] = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
    except ValueError:
        item["suppression"] = {"status": "unreadable"}
    item.pop("suppression_json", None)
    return item


def _load_ticket(conn: Any, ticket_id: int) -> dict[str, Any]:
    row = conn.execute(f"SELECT {_ADMIN_LIST_COLUMNS} FROM vkpi_dsar_requests WHERE id=?", (int(ticket_id),)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="DSAR request not found")
    return dict(row)


@admin_router.get("/requests")
def admin_list_requests(
    status: str = Query(default="pending", max_length=24),
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "admin")),
) -> dict[str, Any]:
    """列表口:联系邮箱只回脱敏形态;全文只在详情口(留审计)。"""
    del staff
    if not table_exists("vkpi_dsar_requests"):
        return {"items": [], "count": 0, "available": False, "reason": "migration_117_not_applied"}
    conn = get_conn()
    wanted = str(status or "pending").strip().lower()
    if wanted == "all":
        rows = conn.execute(
            f"SELECT {_ADMIN_LIST_COLUMNS} FROM vkpi_dsar_requests ORDER BY created_at DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_ADMIN_LIST_COLUMNS} FROM vkpi_dsar_requests WHERE status=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (wanted, int(limit)),
        ).fetchall()
    items = [_public_row(dict(r), reveal_contact=False) for r in rows]
    return {"items": items, "count": len(items), "available": True, "status_filter": wanted}


@admin_router.get("/requests/{ticket_id}")
def admin_get_request(ticket_id: int, staff=Depends(require_tab("vkpi", "admin"))) -> dict[str, Any]:
    """详情口:回联系邮箱全文(答复主体必需)+ 主体删除足迹预览;每次访问写敏感访问审计。"""
    if not table_exists("vkpi_dsar_requests"):
        raise HTTPException(status_code=503, detail="DSAR channel unavailable")
    conn = get_conn()
    ticket = _public_row(_load_ticket(conn, ticket_id), reveal_contact=True)
    footprint: dict[str, Any] | None = None
    if ticket.get("subject_kol_pool_id"):
        from app.domains.kol.dsar_erasure import collect_subject_footprint

        footprint = collect_subject_footprint(int(ticket["subject_kol_pool_id"]))
        # 足迹里 pool 快照含 email/other_contacts —— 详情口只给计数,不给主体联系方式。
        if isinstance(footprint, dict):
            footprint.pop("pool", None)
    try:
        from app.domains.audit.service import log_sensitive_access

        log_sensitive_access(
            staff_id=_staff_id(staff),
            action_type="dsar_request_view",
            resource_type="dsar_request",
            resource_id=str(int(ticket_id)),
            metadata={"request_type": ticket.get("request_type"), "status": ticket.get("status")},
        )
    except Exception:
        logger.warning("dsar.admin.audit_failed", exc_info=True)
    return {"ticket": ticket, "footprint": footprint}


class DsarReviewBody(BaseModel):
    status: str = Field(default="", max_length=24)
    note: str = Field(default="", max_length=2000)
    jurisdiction: str = Field(default="", max_length=64)


@admin_router.patch("/requests/{ticket_id}")
def admin_review_request(ticket_id: int, body: DsarReviewBody, staff=Depends(require_tab("vkpi", "admin"))) -> dict[str, Any]:
    """审批:pending → approved | rejected(只改状态与备注;执行另走 /execute)。"""
    wanted = str(body.status or "").strip().lower()
    if wanted not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="status must be approved or rejected")
    conn = get_conn()
    ticket = _load_ticket(conn, ticket_id)
    if str(ticket.get("status")) != "pending":
        raise HTTPException(status_code=409, detail=f"ticket is {ticket.get('status')}, not pending")
    note = " ".join(str(body.note or "").split())[:2000]
    conn.execute(
        "UPDATE vkpi_dsar_requests SET status=?, approved_by_staff_id=?, note=CASE WHEN ?='' THEN note ELSE ? END, "
        "jurisdiction=CASE WHEN ?='' THEN jurisdiction ELSE ? END WHERE id=?",
        (wanted, _staff_id(staff) or None, note, note, body.jurisdiction.strip(), body.jurisdiction.strip(), int(ticket_id)),
    )
    conn.commit()
    return {"status": "success", "ticket_id": int(ticket_id), "new_status": wanted}


def _stored_subject_emails(conn: Any, subject_id: int) -> list[str]:
    """主体档案 email + vkpi_kol_pool_contacts 的邮箱类联系方式(去重、去空,保持原始大小写交抑制台账规范化)。"""
    values: list[str] = []
    pool = conn.execute("SELECT email FROM vkpi_kol_pool WHERE id=?", (int(subject_id),)).fetchone()
    if pool is not None:
        values.append(str(dict(pool).get("email") or "").strip())
    if table_exists("vkpi_kol_pool_contacts"):
        rows = conn.execute(
            "SELECT contact_value FROM vkpi_kol_pool_contacts WHERE kol_pool_id=? AND contact_type IN ('email','business_email')",
            (int(subject_id),),
        ).fetchall()
        values.extend(str(dict(r).get("contact_value") or "").strip() for r in rows)
    return [value for value in dict.fromkeys(values) if value]


def _suppress_one_contact(conn: Any, subject_id: int, value: str, staff_id: int) -> str:
    """单条进抑制台账;返回 suppressed | blocked_fingerprint_key_unavailable | failed(不抛)。"""
    from app.domains.kol import contact_suppression as suppression

    try:
        suppression.record_suppression(
            kol_pool_id=int(subject_id), contact_type="email", contact_value=value,
            brand_scope=_brand_scope(), reason="legal_request", source_type="compliance",
            staff_id=int(staff_id), conn=conn,
        )
    except suppression.SuppressionConfigurationError:
        return "blocked_fingerprint_key_unavailable"
    except Exception:
        logger.warning("dsar.admin.suppress_contact_failed", exc_info=True)
        return "failed"
    return "suppressed"


def _suppress_subject_contacts(conn: Any, subject_id: int, staff_id: int) -> dict[str, Any]:
    """勿联系执行:主体档案里的邮箱逐条进抑制台账(员工动作,source=compliance);只回计数,不回明文。"""
    summary = {"status": "suppressed", "attempted": 0, "suppressed": 0, "failed": 0, "reason": "legal_request"}
    for value in _stored_subject_emails(conn, subject_id):
        summary["attempted"] += 1
        outcome = _suppress_one_contact(conn, subject_id, value, staff_id)
        if outcome == "suppressed":
            summary["suppressed"] += 1
            continue
        summary["failed"] += 1
        if outcome != "failed":
            summary["status"] = outcome
    if summary["attempted"] == 0:
        summary["status"] = "no_stored_contacts"
    return summary


def _execute_do_not_contact(conn: Any, ticket: dict[str, Any], staff: dict[str, Any]) -> dict[str, Any]:
    subject_id = ticket.get("subject_kol_pool_id")
    if not subject_id:
        raise HTTPException(status_code=409, detail="subject_kol_pool_id is not set; link the subject first")
    summary = _suppress_subject_contacts(conn, int(subject_id), _staff_id(staff))
    done = summary["status"] in {"suppressed", "no_stored_contacts"} and summary["failed"] == 0
    conn.execute(
        "UPDATE vkpi_dsar_requests SET status=?, suppression_json=?, executed_at=? WHERE id=?",
        ("done" if done else "approved", json.dumps(summary, ensure_ascii=False), _utcnow() if done else None, int(ticket["id"])),
    )
    conn.commit()
    return {"status": "done" if done else "partial", "suppression": summary}


@admin_router.post("/requests/{ticket_id}/execute")
def admin_execute_request(ticket_id: int, staff=Depends(require_tab("vkpi", "admin"))) -> dict[str, Any]:
    """执行已审批工单:erasure → erase_subject 级联删除;do_not_contact → 抑制台账;access → 人工答复后标 done。"""
    conn = get_conn()
    ticket = _load_ticket(conn, ticket_id)
    if str(ticket.get("status")) != "approved":
        raise HTTPException(status_code=409, detail="ticket must be approved before execution")
    request_type = str(ticket.get("request_type") or "")
    if request_type == "do_not_contact":
        return _execute_do_not_contact(conn, ticket, staff)
    if request_type == "access":
        conn.execute("UPDATE vkpi_dsar_requests SET status='done', executed_at=? WHERE id=?", (_utcnow(), int(ticket_id)))
        conn.commit()
        return {"status": "done", "note": "access request marked fulfilled (manual reply)"}
    subject_id = ticket.get("subject_kol_pool_id")
    if not subject_id:
        raise HTTPException(status_code=409, detail="subject_kol_pool_id is not set; link the subject first")
    from app.domains.kol.dsar_erasure import erase_subject

    result = erase_subject(int(subject_id), dsar_request_id=int(ticket_id), staff=staff)
    if result.get("status") == "blocked":
        raise HTTPException(status_code=409, detail=str(result.get("reason") or "blocked"))
    return {"status": "done", "receipt": result.get("receipt") or result}


router.include_router(public_router)
router.include_router(legal_router)
router.include_router(admin_router)

__all__ = [
    "DSAR_SLA_DAYS",
    "LEGAL_PAGES",
    "PLATFORMS",
    "RATE_BUCKET",
    "REQUEST_TYPES",
    "RETENTION_POLICY_KEYS",
    "apply_self_suppression",
    "mask_contact",
    "resolve_subject",
    "retention_policy_public",
    "router",
    "validate_public_request",
]

"""local_workers/dispatch_policy.py — 本地派发策略 v0 + 过期租约回收(W2 件)。

策略口径:
- 只有四类安全任务可下放本地(白名单单一真源在 registry.SAFE_TASK_TYPES);
- 任务 payload 不得携带任何敏感字段(key 命中敏感标记、值命中长期凭据形态、体积超限
  任一条即不合格)——本地 worker 永不持有长期 API key,payload 里也绝不能夹带;
- 回收策略:过期租约任务回归 queued 可领状态。W1 的领活标记协议是
  apify_jobs.payload.local_lease_id(绝不动 apify_jobs.status),因此「回归 queued」
  = 置租约 expired + 清 payload 标记(复用 registry.reclaim_expired),本模块再补一层
  孤儿标记清扫(标记指向的租约已非活跃却没清干净的残留)。

红线:绝不写 viltrox_fit_score、绝不触 rule_v0、绝不直写核心业务表。
compat SQL 约束:占位符一律 ?;SQL 文本零字面百分号、零 jsonb 问号操作符。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

# 同域复用 W1 契约(白名单/TTL/标记协议/基础回收),绝不再抄一份防漂移。
from app.domains.local_workers.registry import (  # noqa: PLC2701 — 同域契约复用
    LEASE_TTL_SECONDS,
    SAFE_TASK_TYPES,
    _MARKER_LEASE_ID,
    _release_job_marker,
    reclaim_expired,
)

logger = get_logger(__name__)

LEASE_TABLE = "vkpi_local_task_leases"
DEVICE_TABLE = "vkpi_worker_devices"

MAX_PAYLOAD_BYTES = 262_144  # 256 KB:payload 超限不下放(防夹带大块不明数据)

# key 名命中任一标记即视为敏感(小写子串匹配)。刻意不含裸 auth(误伤 author)、
# 不含 session(误伤 search_session_id 这类业务键)。
SENSITIVE_KEY_MARKERS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "passwd",
    "credential",
    "cookie",
    "authorization",
    "private_key",
    "access_key",
    "token",
)

# 值命中任一形态即视为夹带长期凭据(常见 key 形态:OpenAI/Google/Apify/Shopify/AWS/GitHub/PEM/Bearer)。
_SENSITIVE_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"apify_api_[A-Za-z0-9]{8,}"),
    re.compile(r"shpat_[0-9a-fA-F]{8,}"),
    re.compile(r"shpss_[0-9a-fA-F]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{16,}"),
)

_WALK_MAX_DEPTH = 8
_WALK_MAX_ITEMS = 500


def _loads(raw: Any, default: Any) -> Any:
    if raw in (None, "", b""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return default
    return parsed if parsed is not None else default


# ── payload 敏感字段扫描 ──────────────────────────────────────────────────────


def payload_sensitive_fields(payload: Any) -> list[str]:
    """递归扫 payload,返回命中的敏感点(路径:原因)。空列表=干净。"""
    hits: list[str] = []
    seen_items = 0

    def _walk(node: Any, path: str, depth: int) -> None:
        nonlocal seen_items
        if depth > _WALK_MAX_DEPTH or seen_items > _WALK_MAX_ITEMS or len(hits) >= 20:
            return
        seen_items += 1
        if isinstance(node, dict):
            for key, value in node.items():
                key_str = str(key)
                lowered = key_str.lower()
                child_path = f"{path}.{key_str}" if path else key_str
                for marker in SENSITIVE_KEY_MARKERS:
                    if marker in lowered:
                        hits.append(f"{child_path}: key matches sensitive marker '{marker}'")
                        break
                _walk(value, child_path, depth + 1)
            return
        if isinstance(node, list):
            for idx, item in enumerate(node[:_WALK_MAX_ITEMS]):
                _walk(item, f"{path}[{idx}]", depth + 1)
            return
        if isinstance(node, str) and len(node) >= 8:
            for pattern in _SENSITIVE_VALUE_PATTERNS:
                if pattern.search(node):
                    hits.append(f"{path}: value matches credential pattern '{pattern.pattern[:40]}'")
                    break

    _walk(payload, "", 0)
    return hits


# ── 派发合格性 ────────────────────────────────────────────────────────────────


def explain_eligibility(job: dict[str, Any]) -> dict[str, Any]:
    """eligible_for_local 的可解释版本:eligible + 逐条 reasons。"""
    job = job if isinstance(job, dict) else {}
    task_type = str(job.get("task_type") or job.get("local_task_type") or job.get("job_type") or "")
    reasons: list[str] = []

    if task_type not in SAFE_TASK_TYPES:
        reasons.append(f"task_type not in safe whitelist: {task_type or '(empty)'}")

    payload = _loads(job.get("payload"), None)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        reasons.append("payload is not a JSON object")
        payload = {}

    try:
        payload_bytes = len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        payload_bytes = -1
        reasons.append("payload is not JSON-serializable")
    if payload_bytes > MAX_PAYLOAD_BYTES:
        reasons.append(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")

    for hit in payload_sensitive_fields(payload):
        reasons.append(f"sensitive payload field: {hit}")

    return {
        "eligible": not reasons,
        "task_type": task_type,
        "payload_bytes": payload_bytes,
        "reasons": reasons,
    }


def eligible_for_local(job: dict[str, Any]) -> bool:
    """契约函数:四类白名单 + payload 无敏感字段才允许下放本地。"""
    return bool(explain_eligibility(job)["eligible"])


# ── 回收策略:过期租约任务回归 queued 可领 ─────────────────────────────────────


def reclaim_expired_leases(*, limit: int = 200, conn: Any | None = None) -> dict[str, Any]:
    """过期租约回收 + 孤儿 claimed 标记清扫。幂等,可手动(POST /reclaim)或周期触发。

    第一层复用 registry.reclaim_expired():leased 且 expires_at<=NOW() 的租约置
    expired、清 payload 标记(任务由此回归可领=回归 queued 语义)、心跳超窗设备置 offline。
    第二层孤儿清扫:apify_jobs 仍是 queued 且带 local_lease_id 标记、但标记指向的租约
    已不活跃(不存在,或状态不在 leased/submitted)——这种残留会让任务永远领不出去。
    submitted 状态刻意算活跃:标记按 W1 设计保留到校验桥收口,不能在这里误清。
    """
    db = conn or get_conn()
    if not table_exists(LEASE_TABLE):
        return {"ok": False, "reason": f"{LEASE_TABLE} missing - apply migration 213 first"}

    base = reclaim_expired()

    orphan_rows = db.execute(
        f"""
        SELECT id, payload->>'{_MARKER_LEASE_ID}' AS marker_lease_id
        FROM apify_jobs
        WHERE status = 'queued'
          AND payload->>'{_MARKER_LEASE_ID}' IS NOT NULL
        ORDER BY id
        LIMIT ?
        """,
        (max(1, min(1000, int(limit or 200))),),
    ).fetchall()

    orphans_cleared = 0
    details: list[dict[str, Any]] = []
    for row in orphan_rows:
        job_id = int(row["id"])
        lease_id = 0
        try:
            lease_id = int(str(row["marker_lease_id"] or "0"))
        except ValueError:
            lease_id = 0
        lease_status = ""
        if lease_id > 0:
            lease_row = db.execute(
                f"SELECT status FROM {LEASE_TABLE} WHERE id = ?",
                (lease_id,),
            ).fetchone()
            lease_status = str(lease_row["status"] or "") if lease_row is not None else "missing"
        else:
            lease_status = "unparseable_marker"
        if lease_status in ("leased", "submitted"):
            continue  # 活跃租约(submitted 等校验桥收口),标记合法保留
        _release_job_marker(db, job_id, lease_id)
        orphans_cleared += 1
        details.append({"job_id": job_id, "marker_lease_id": lease_id, "lease_status": lease_status})
    if orphans_cleared:
        db.commit()

    out = {
        "ok": True,
        "leases_expired": int(base.get("leases_expired") or 0),
        "devices_offlined": int(base.get("devices_offlined") or 0),
        "orphan_markers_cleared": orphans_cleared,
        "orphan_details": details[:20],
        "policy": "expired lease = lease status expired + claimed marker released, job becomes leasable again (queued untouched by design)",
    }
    if base.get("leases_expired") or orphans_cleared:
        logger.info(
            "local_workers dispatch reclaim: leases_expired=%s orphans_cleared=%s",
            base.get("leases_expired"), orphans_cleared,
        )
    return out


# ── 策略快照(GET /policy) ───────────────────────────────────────────────────


def policy_snapshot() -> dict[str, Any]:
    """派发策略 v0 的只读快照:白名单、敏感字段口径、TTL、回收规则、表就绪态。"""
    dev_insecure = not (
        str(os.environ.get("JWT_SECRET") or "").strip()
        or str(os.environ.get("VKPI_LOCAL_WORKER_SECRET") or "").strip()
    )
    return {
        "version": "dispatch_policy_v0",
        "safe_task_types": list(SAFE_TASK_TYPES),
        "lease_ttl_seconds": LEASE_TTL_SECONDS,
        "max_payload_bytes": MAX_PAYLOAD_BYTES,
        "sensitive_key_markers": list(SENSITIVE_KEY_MARKERS),
        "sensitive_value_pattern_count": len(_SENSITIVE_VALUE_PATTERNS),
        "reclaim_policy": {
            "expired_lease": "status -> expired, error_code=lease_expired, claimed marker released (job leasable again)",
            "orphan_marker": "queued job carrying a marker whose lease is not leased/submitted gets the marker cleared",
            "devices": "heartbeat older than online window flips device back to offline",
        },
        "ingest_policy": {
            "metadata_extract": "via app.domains.kol.video_evidence.ensure_video_evidence_from_url (score-guarded)",
            "video_precheck": "pending_ingest (no reusable writer yet - staging only, honest)",
            "download_frames": "pending_ingest (sha256+bytes manifest validated, bytes never enter this bridge)",
            "comment_clean": "pending_ingest (comments domain owns the real tables)",
        },
        "tables": {
            DEVICE_TABLE: bool(table_exists(DEVICE_TABLE)),
            LEASE_TABLE: bool(table_exists(LEASE_TABLE)),
        },
        "dev_insecure": bool(dev_insecure),
        "redlines": [
            "local workers never hold long-lived API keys",
            "local submissions are untrusted until server-side validation",
            "no direct writes to evidence/kol_pool/score columns from the local path",
            "never writes viltrox_fit_score, never touches rule_v0",
        ],
    }

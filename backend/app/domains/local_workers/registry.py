"""local_workers/registry.py — 设备注册 + 短期任务租约 + token 签发验证(W1 地基件)。

契约(跨代理固定,W2 从本模块 import):
- issue_task_token(lease_id, job_id, expires_at) -> str
  短期 token = HMAC-SHA256(secret, lease_id+job_id+expires_at) 的 hex;
  签发时只存 token_hash = sha256(token),库里绝不落 token 明文。
- verify_task_token(lease_id, token) -> bool
  验 sha256(token) 与库中 token_hash 恒时比较 + 租约未过期 + 状态仍 leased。

安全红线:
- 本地 worker 永不持有长期 API key,只拿短期任务 token;
- 领活只从 apify_jobs 挑 queued 且 job_type 在四类安全白名单内的任务,
  claimed 标记走 payload 的 local_lease_id 键(绝不改 apify_jobs 表结构、绝不动其 status);
- 提交结果一律先落 staging(vkpi_local_task_leases.result_json),服务端校验
  (结构/hash/URL 匹配)通过才标 result_validated=1;绝不直写核心业务表
  (evidence/kol_pool/分数列),落库由服务端校验桥走既有函数;
- 绝不写 viltrox_fit_score、绝不触 rule_v0。

token 签名密钥来源(诚实降级链):
  env JWT_SECRET(既有 SECRET 类 env)→ env VKPI_LOCAL_WORKER_SECRET → 固定 dev 值
  (最后一档在日志与 payload 里诚实标 dev_insecure=true)。绝不编辑 .env。

compat SQL 约束:占位符一律 ?;SQL 文本零字面百分号;jsonb 参数用 ?::jsonb 显式转型;
读回陷阱:jsonb 读回是 JSON 字符串、BOOLEAN 读回是 int、timestamptz 读回是 ...Z 字符串,
因此时间比较全部下沉到 SQL(expires_at > NOW())算成 int 列读回。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets as _secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

# 安全任务类型 v0:仅此四类可租(硬编码白名单,库层不放开)。
SAFE_TASK_TYPES: tuple[str, ...] = (
    "video_precheck",
    "metadata_extract",
    "download_frames",
    "comment_clean",
)

LEASE_TTL_SECONDS = 900          # 租约有效期 15 分钟(过期即拒收+回收重派)
ONLINE_WINDOW_SECONDS = 300      # 心跳 5 分钟窗口内算在线(与 presence 口径一致)
RESULT_MAX_BYTES = 512_000       # staging 结果体积上限(防洪水)
_DEV_FALLBACK_SECRET = "vkpi-local-worker-dev-secret-insecure"

# apify_jobs.payload 上的 claimed 标记键(绝不改表结构,只动 payload)。
_MARKER_LEASE_ID = "local_lease_id"
_MARKER_DEVICE = "local_lease_device"
_MARKER_LEASED_AT = "local_leased_at"

_dev_secret_warned = False


# ── token 签发与验证(跨代理契约函数,W2 直接 import) ─────────────────────────


def _worker_secret() -> tuple[str, bool]:
    """返回 (secret, dev_insecure)。优先既有 SECRET 类 env,绝不编辑 .env。"""
    global _dev_secret_warned
    for env_key in ("JWT_SECRET", "VKPI_LOCAL_WORKER_SECRET"):
        value = str(os.environ.get(env_key) or "").strip()
        if value:
            return value, False
    if not _dev_secret_warned:
        _dev_secret_warned = True
        logger.warning(
            "local_workers token secret falling back to fixed dev value "
            "(JWT_SECRET / VKPI_LOCAL_WORKER_SECRET both missing); dev_insecure=true"
        )
    return _DEV_FALLBACK_SECRET, True


def _canon_expires(expires_at: Any) -> str:
    if isinstance(expires_at, datetime):
        return expires_at.isoformat()
    return str(expires_at)


def issue_task_token(lease_id: int, job_id: int, expires_at: Any) -> str:
    """契约:HMAC-SHA256(secret, lease_id+job_id+expires_at) hex。"""
    secret, _ = _worker_secret()
    message = f"{int(lease_id)}:{int(job_id)}:{_canon_expires(expires_at)}"
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def verify_task_token(lease_id: int, token: str) -> bool:
    """契约:验 token_hash 匹配 + 未过期 + lease 状态仍 leased。任何缺席都 False。"""
    if not token:
        return False
    conn = get_conn()
    row = conn.execute(
        """
        SELECT token_hash, status,
               CASE WHEN expires_at IS NOT NULL AND expires_at > NOW() THEN 1 ELSE 0 END AS alive
        FROM vkpi_local_task_leases
        WHERE id = ?
        """,
        (int(lease_id),),
    ).fetchone()
    if row is None:
        return False
    stored = str(row["token_hash"] or "")
    if not stored or not hmac.compare_digest(stored, _token_hash(token)):
        return False
    if str(row["status"] or "") != "leased":
        return False
    return int(row["alive"] or 0) == 1


# ── 设备注册与心跳 ────────────────────────────────────────────────────────────


def _device_row_to_dict(row: Any) -> dict[str, Any]:
    data = {key: row[key] for key in row.keys()}
    caps = data.get("capabilities")
    if isinstance(caps, str) and caps:
        try:
            data["capabilities"] = json.loads(caps)
        except ValueError:
            data["capabilities"] = {"_raw": caps[:500]}
    return data


def register_device(
    *,
    device_name: str,
    platform: str,
    capabilities: dict[str, Any] | None = None,
    staff_id: int | None = None,
) -> dict[str, Any]:
    """注册(或按 staff_id+device_name+platform 幂等复用)一台本地算力设备。"""
    device_name = str(device_name or "").strip()
    platform = str(platform or "").strip().lower()
    if not device_name:
        raise ValueError("device_name is required")
    caps = capabilities if isinstance(capabilities, dict) else {}
    conn = get_conn()
    existing = conn.execute(
        """
        SELECT device_id FROM vkpi_worker_devices
        WHERE device_name = ? AND COALESCE(platform,'') = ? AND COALESCE(staff_id, 0) = COALESCE(?, 0)
        ORDER BY id LIMIT 1
        """,
        (device_name, platform, staff_id),
    ).fetchone()
    if existing is not None:
        device_id = str(existing["device_id"])
        conn.execute(
            """
            UPDATE vkpi_worker_devices
            SET capabilities = ?::jsonb, last_seen_at = NOW(), status = 'online'
            WHERE device_id = ?
            """,
            (json.dumps(caps, ensure_ascii=False), device_id),
        )
        reused = True
    else:
        device_id = "lwk_" + _secrets.token_hex(8)
        conn.execute(
            """
            INSERT INTO vkpi_worker_devices
                (device_id, staff_id, device_name, platform, capabilities, last_seen_at, status, trust_level)
            VALUES (?, ?, ?, ?, ?::jsonb, NOW(), 'online', 0)
            """,
            (device_id, staff_id, device_name, platform, json.dumps(caps, ensure_ascii=False)),
        )
        reused = False
    conn.commit()
    _, dev_insecure = _worker_secret()
    return {
        "device_id": device_id,
        "device_name": device_name,
        "platform": platform,
        "staff_id": staff_id,
        "trust_level": 0,
        "status": "online",
        "reused": reused,
        "safe_task_types": list(SAFE_TASK_TYPES),
        "dev_insecure": dev_insecure,
    }


def heartbeat(
    device_id: str,
    *,
    current_task: str | None = None,
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """心跳:刷新 last_seen_at 与在线态;运行时信息合并进 capabilities._runtime。"""
    device_id = str(device_id or "").strip()
    if not device_id:
        raise ValueError("device_id is required")
    runtime = {
        "_runtime": {
            "current_task": str(current_task or "") or None,
            "stats": stats if isinstance(stats, dict) else {},
            "reported_at": datetime.now(timezone.utc).isoformat(),
        }
    }
    conn = get_conn()
    cur = conn.execute(
        """
        UPDATE vkpi_worker_devices
        SET last_seen_at = NOW(), status = 'online',
            capabilities = COALESCE(capabilities, '{}'::jsonb) || ?::jsonb
        WHERE device_id = ?
        """,
        (json.dumps(runtime, ensure_ascii=False), device_id),
    )
    if int(getattr(cur, "rowcount", 0) or 0) == 0:
        conn.rollback()
        raise LookupError(f"unknown device_id: {device_id}")
    conn.commit()
    return {"ok": True, "device_id": device_id}


# ── 领活(短期租约签发) ──────────────────────────────────────────────────────


def lease_task(device_id: str, task_types: list[str] | None = None) -> dict[str, Any]:
    """从 apify_jobs 领一条 queued 且 job_type 在四类安全白名单内的任务。

    claimed 标记走 payload.local_lease_id(绝不改 apify_jobs 表结构、绝不动 status);
    返回 payload + 短期 task_token(worker 全程无长期 key)。没有可领任务回 no_task。
    """
    device_id = str(device_id or "").strip()
    if not device_id:
        raise ValueError("device_id is required")
    requested = [str(t or "").strip() for t in (task_types or []) if str(t or "").strip()]
    allowed = [t for t in (requested or list(SAFE_TASK_TYPES)) if t in SAFE_TASK_TYPES]
    if not allowed:
        raise ValueError(f"no task_types in safe whitelist {list(SAFE_TASK_TYPES)}")

    conn = get_conn()
    device = conn.execute(
        "SELECT device_id, trust_level FROM vkpi_worker_devices WHERE device_id = ?",
        (device_id,),
    ).fetchone()
    if device is None:
        raise LookupError(f"unknown device_id: {device_id}")

    # 领活前先把过期租约清场,释放被占的任务(同一事务外的独立 commit,幂等)。
    reclaim_expired()

    placeholders = ",".join(["?"] * len(allowed))
    job = conn.execute(
        f"""
        SELECT id, job_type, payload
        FROM apify_jobs
        WHERE status = 'queued'
          AND job_type IN ({placeholders})
          AND (payload IS NULL OR payload->>'{_MARKER_LEASE_ID}' IS NULL)
        ORDER BY created_at, id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
        """,
        tuple(allowed),
    ).fetchone()
    if job is None:
        conn.commit()
        return {"status": "no_task"}

    job_id = int(job["id"])
    task_type = str(job["job_type"] or "")
    raw_payload = job["payload"]
    if isinstance(raw_payload, str) and raw_payload:
        try:
            job_payload = json.loads(raw_payload)
        except ValueError:
            job_payload = {"_raw": raw_payload[:1000]}
    elif isinstance(raw_payload, dict):
        job_payload = raw_payload
    else:
        job_payload = {}

    expires_dt = datetime.now(timezone.utc) + timedelta(seconds=LEASE_TTL_SECONDS)
    expires_iso = expires_dt.isoformat()
    lease_row = conn.execute(
        """
        INSERT INTO vkpi_local_task_leases (job_id, device_id, task_type, status, expires_at)
        VALUES (?, ?, ?, 'leased', ?)
        RETURNING id
        """,
        (job_id, device_id, task_type, expires_iso),
    ).fetchone()
    lease_id = int(lease_row["id"])

    token = issue_task_token(lease_id, job_id, expires_iso)
    conn.execute(
        "UPDATE vkpi_local_task_leases SET token_hash = ? WHERE id = ?",
        (_token_hash(token), lease_id),
    )
    marker = {
        _MARKER_LEASE_ID: lease_id,
        _MARKER_DEVICE: device_id,
        _MARKER_LEASED_AT: datetime.now(timezone.utc).isoformat(),
    }
    conn.execute(
        """
        UPDATE apify_jobs
        SET payload = COALESCE(payload, '{}'::jsonb) || ?::jsonb, updated_at = NOW()
        WHERE id = ?
        """,
        (json.dumps(marker, ensure_ascii=False), job_id),
    )
    conn.commit()

    _, dev_insecure = _worker_secret()
    logger.info(
        "local lease issued: lease_id=%s job_id=%s task_type=%s device=%s expires=%s",
        lease_id, job_id, task_type, device_id, expires_iso,
    )
    return {
        "lease_id": lease_id,
        "job_id": job_id,
        "task_type": task_type,
        "payload": job_payload,
        "task_token": token,
        "expires_at": expires_iso,
        "dev_insecure": dev_insecure,
    }


# ── 提交(staging + 服务端校验) ──────────────────────────────────────────────


def _basic_validate_result(
    job_payload: dict[str, Any],
    result: Any,
    files_meta: list[dict[str, Any]] | None,
) -> list[str]:
    """W1 基础服务端校验(结构/hash 形态/URL 匹配/体积)。W2 validation.py 再加深。"""
    problems: list[str] = []
    if not isinstance(result, dict):
        problems.append("result must be a JSON object")
        return problems
    try:
        encoded = json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        problems.append("result is not JSON-serializable")
        return problems
    if len(encoded.encode("utf-8")) > RESULT_MAX_BYTES:
        problems.append(f"result exceeds {RESULT_MAX_BYTES} bytes")
    for idx, meta in enumerate(files_meta or []):
        if not isinstance(meta, dict):
            problems.append(f"files_meta[{idx}] must be an object")
            continue
        name = str(meta.get("name") or "").strip()
        sha = str(meta.get("sha256") or "").strip().lower()
        size = meta.get("bytes")
        if not name:
            problems.append(f"files_meta[{idx}].name missing")
        if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
            problems.append(f"files_meta[{idx}].sha256 is not 64-hex")
        if not isinstance(size, int) or size < 0:
            problems.append(f"files_meta[{idx}].bytes must be a non-negative int")
    job_url = str((job_payload or {}).get("url") or "").strip()
    result_url = str(result.get("url") or "").strip()
    if job_url and result_url and job_url != result_url:
        problems.append("result.url does not match leased job payload url")
    return problems


def submit_result(
    lease_id: int,
    *,
    task_token: str,
    result: Any,
    files_meta: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """提交本地执行结果:token 三验(hash/状态/过期)→ 服务端校验 → 只落 staging。

    result_json 是 staging,未经校验桥落库前绝不写核心业务表;
    校验不过 status=rejected 并释放任务标记供重派。
    """
    lease_id = int(lease_id)
    conn = get_conn()
    row = conn.execute(
        """
        SELECT id, job_id, device_id, task_type, status, token_hash,
               CASE WHEN expires_at IS NOT NULL AND expires_at > NOW() THEN 1 ELSE 0 END AS alive
        FROM vkpi_local_task_leases
        WHERE id = ?
        """,
        (lease_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"unknown lease_id: {lease_id}")

    stored = str(row["token_hash"] or "")
    if not stored or not hmac.compare_digest(stored, _token_hash(task_token)):
        # 无效 token 绝不改租约状态(不给伪造者翻转状态的能力),只诚实拒绝。
        conn.rollback()
        return {"accepted": False, "validated": False, "notes": "invalid task_token", "error_code": "invalid_token"}
    status = str(row["status"] or "")
    if status != "leased":
        conn.rollback()
        return {"accepted": False, "validated": False, "notes": f"lease not active (status={status})", "error_code": "lease_not_active"}
    job_id = int(row["job_id"] or 0)
    if int(row["alive"] or 0) != 1:
        conn.execute(
            "UPDATE vkpi_local_task_leases SET status='expired', error_code='lease_expired' WHERE id = ?",
            (lease_id,),
        )
        _release_job_marker(conn, job_id, lease_id)
        conn.commit()
        return {"accepted": False, "validated": False, "notes": "lease expired", "error_code": "lease_expired"}

    job_row = conn.execute("SELECT payload FROM apify_jobs WHERE id = ?", (job_id,)).fetchone()
    raw = job_row["payload"] if job_row is not None else None
    if isinstance(raw, str) and raw:
        try:
            job_payload = json.loads(raw)
        except ValueError:
            job_payload = {}
    elif isinstance(raw, dict):
        job_payload = raw
    else:
        job_payload = {}

    problems = _basic_validate_result(job_payload, result, files_meta)
    # W2 深度校验钩子(validation.py 落地后自动生效;缺席时只跑基础校验,诚实不装深)。
    try:
        from app.domains.local_workers import validation as _validation  # noqa: PLC0415
    except ImportError:
        _validation = None
    if _validation is not None and hasattr(_validation, "validate_submission"):
        try:
            ok, extra_notes = _validation.validate_submission(job_payload, result, files_meta or [])
            if not ok:
                problems.extend(str(n) for n in (extra_notes or ["deep validation failed"]))
        except Exception as exc:  # noqa: BLE001 — 深校验崩不放行,按不通过处理
            problems.append(f"deep validation crashed: {str(exc)[:200]}")

    validated = 0 if problems else 1
    notes = "; ".join(problems)[:1000] if problems else "basic server-side checks passed"
    staging = {
        "result": result if isinstance(result, dict) else {"_raw": str(result)[:1000]},
        "files_meta": files_meta or [],
    }
    if validated:
        conn.execute(
            """
            UPDATE vkpi_local_task_leases
            SET status='submitted', submitted_at=NOW(), result_json=?::jsonb,
                result_validated=1, validation_notes=?, error_code=NULL
            WHERE id = ?
            """,
            (json.dumps(staging, ensure_ascii=False), notes, lease_id),
        )
        # 任务标记保留:等服务端校验桥(后续件)把 staging 结果走既有函数落库后再清。
    else:
        conn.execute(
            """
            UPDATE vkpi_local_task_leases
            SET status='rejected', submitted_at=NOW(), result_json=?::jsonb,
                result_validated=0, validation_notes=?, error_code='invalid_result'
            WHERE id = ?
            """,
            (json.dumps(staging, ensure_ascii=False), notes, lease_id),
        )
        _release_job_marker(conn, job_id, lease_id)
    conn.commit()
    # 收口接线(W2 建议的提交即落库):校验通过即触发落库桥——metadata 走既有安全函数
    # 入 evidence,其余类型诚实 pending_ingest;桥崩不影响已受理的提交(staging 已落,
    # 可经 admin 路由 POST /lease/{id}/validate 手动重触发)。
    if validated:
        try:
            from app.domains.local_workers import validation as _bridge  # noqa: PLC0415
            _bridge.apply_validation(lease_id)
        except Exception:  # noqa: BLE001 — 桥失败留 staging,绝不吞提交
            logger.warning("local lease auto-ingest bridge failed lease_id=%s", lease_id, exc_info=True)
    logger.info(
        "local lease submit: lease_id=%s job_id=%s validated=%s notes=%s",
        lease_id, job_id, validated, notes[:200],
    )
    return {"accepted": True, "validated": bool(validated), "notes": notes, "error_code": None if validated else "invalid_result"}


def _release_job_marker(conn: Any, job_id: int, lease_id: int) -> None:
    """清掉 apify_jobs.payload 上本租约的 claimed 标记(只清自己的,防误伤新租约)。"""
    if not job_id:
        return
    conn.execute(
        f"""
        UPDATE apify_jobs
        SET payload = (payload - '{_MARKER_LEASE_ID}') - '{_MARKER_DEVICE}' - '{_MARKER_LEASED_AT}',
            updated_at = NOW()
        WHERE id = ? AND payload->>'{_MARKER_LEASE_ID}' = ?
        """,
        (int(job_id), str(int(lease_id))),
    )


# ── 过期回收与看板读数 ────────────────────────────────────────────────────────


def reclaim_expired() -> dict[str, Any]:
    """回收过期租约(置 expired + 释放任务标记)并把心跳超窗设备置回 offline。幂等。"""
    conn = get_conn()
    stale = conn.execute(
        "SELECT id, job_id FROM vkpi_local_task_leases WHERE status = 'leased' AND expires_at IS NOT NULL AND expires_at <= NOW()",
    ).fetchall()
    for row in stale:
        lease_id = int(row["id"])
        conn.execute(
            "UPDATE vkpi_local_task_leases SET status='expired', error_code='lease_expired' WHERE id = ?",
            (lease_id,),
        )
        _release_job_marker(conn, int(row["job_id"] or 0), lease_id)
    offline_cur = conn.execute(
        """
        UPDATE vkpi_worker_devices
        SET status = 'offline'
        WHERE status = 'online'
          AND (last_seen_at IS NULL OR last_seen_at < NOW() - make_interval(secs => ?))
        """,
        (ONLINE_WINDOW_SECONDS,),
    )
    devices_offlined = int(getattr(offline_cur, "rowcount", 0) or 0)
    conn.commit()
    if stale or devices_offlined:
        logger.info(
            "local_workers reclaim: leases_expired=%s devices_offlined=%s",
            len(stale), devices_offlined,
        )
    return {"leases_expired": len(stale), "devices_offlined": devices_offlined}


def list_devices() -> dict[str, Any]:
    """设备列表(含在线态与当前任务)。在线口径:心跳落在 5 分钟窗口内。"""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, device_id, staff_id, device_name, platform, capabilities,
               last_seen_at, status, trust_level, created_at,
               CASE WHEN last_seen_at IS NOT NULL AND last_seen_at >= NOW() - make_interval(secs => ?)
                    THEN 1 ELSE 0 END AS online
        FROM vkpi_worker_devices
        ORDER BY id
        """,
        (ONLINE_WINDOW_SECONDS,),
    ).fetchall()
    leases = conn.execute(
        """
        SELECT device_id, id AS lease_id, job_id, task_type, expires_at
        FROM vkpi_local_task_leases
        WHERE status = 'leased'
        ORDER BY id DESC
        """,
    ).fetchall()
    active_by_device: dict[str, dict[str, Any]] = {}
    for lease in leases:
        key = str(lease["device_id"] or "")
        if key and key not in active_by_device:
            active_by_device[key] = {
                "lease_id": int(lease["lease_id"]),
                "job_id": int(lease["job_id"] or 0),
                "task_type": str(lease["task_type"] or ""),
                "expires_at": lease["expires_at"],
            }
    devices = []
    for row in rows:
        data = _device_row_to_dict(row)
        data["online"] = bool(int(row["online"] or 0))
        data["current_task"] = active_by_device.get(str(row["device_id"] or ""))
        devices.append(data)
    return {"devices": devices, "count": len(devices), "online_window_seconds": ONLINE_WINDOW_SECONDS}

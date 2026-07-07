"""local_workers/validation.py — 本地提交结果的深度服务端校验 + 落库校验桥(W2 件)。

职责(安全红线的执行层):
- 本地上传结果一律不可信,按 task_type 分支做结构/类型/URL 匹配深校验;
- 校验通过才 result_validated=1;「可安全落库」的部分只经既有函数写入
  (metadata_extract 走 app.domains.kol.video_evidence.ensure_video_evidence_from_url,
  自带 viltrox_fit_score 守卫与同 URL 错挂冲突拒绝);
- 找不到安全落点的类型(video_precheck / download_frames / comment_clean)诚实标
  pending_ingest,结果留在 lease staging 行,绝不硬写核心业务表;
- 绝不写 viltrox_fit_score、绝不触 rule_v0。

双调用形态(为兼容 W1 registry.submit_result 内置的深校验钩子):
- 契约形态:validate_submission(lease_view, result, files_meta) -> dict{ok, notes, ...}
  lease_view 必须带 task_type(与 payload,payload 缺省按空 dict);
- W1 钩子形态:validate_submission(job_payload, result, files_meta) -> (ok, notes_list)
  首参不含 task_type 时视作 apify_jobs.payload,经 payload.local_lease_id 标记回查
  租约得到 task_type;回查不到时诚实跳过深校验(W1 基础校验仍然生效)。

compat SQL 约束:占位符一律 ?;SQL 文本零字面百分号、零 jsonb 问号操作符;
jsonb 参数用 ?::jsonb;时间比较全部下沉 SQL(NOW())。
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

# 同域复用 W1 的白名单与 payload 标记协议(单一真源,绝不再抄一份常量防漂移)。
from app.domains.local_workers.registry import (  # noqa: PLC2701 — 同域契约复用
    SAFE_TASK_TYPES,
    _MARKER_LEASE_ID,
    _release_job_marker,
)

logger = get_logger(__name__)

LEASE_TABLE = "vkpi_local_task_leases"
INGEST_METHOD = "local_worker_bridge_v1"

# video_precheck:这些 HTTP 终态码与 playable=true 互斥(照搬
# services/media/video_download.py 的 Point 8 终态口径)。
_TERMINAL_HTTP_STATUSES = (401, 403, 404, 410, 451)

_URL_KEYS = ("url", "video_url", "content_url", "source_url", "target_url")
_TEXT_KEYS_COMMENT = ("text", "content", "comment_text")
_LANG_KEYS_COMMENT = ("lang", "language", "lang_code")
_HEX_DIGITS = set("0123456789abcdef")


# ── 小工具(诚实容错:jsonb 读回可能是 str,BOOLEAN 读回可能是 int) ────────────


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


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and float(value).is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _boolish(value: Any) -> bool | None:
    """BOOLEAN 容错:接受 True/False、1/0、'true'/'false'(compat 读回陷阱口径)。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "t", "1", "yes"):
            return True
        if lowered in ("false", "f", "0", "no"):
            return False
    return None


def _norm_url(url: Any) -> str:
    raw = _text(url)
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw.lower()
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    if parsed.query:
        return f"{scheme}://{host}{path}" + "?" + parsed.query
    return f"{scheme}://{host}{path}"


def _pick_url(data: dict[str, Any]) -> str:
    for key in _URL_KEYS:
        candidate = _text((data or {}).get(key))
        if candidate:
            return candidate
    return ""


# ── 深校验分支(纯函数:返回 problems 列表,空列表=通过) ──────────────────────


def _problems_video_precheck(result: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    http_status = _int_or_none(result.get("http_status"))
    if http_status is None or not 0 <= http_status <= 999:
        problems.append("precheck.http_status missing or not an int in 0..999")
    playable = _boolish(result.get("playable"))
    if playable is None:
        problems.append("precheck.playable missing or not a boolean-like value")
    if not _text(result.get("reason")):
        problems.append("precheck.reason missing or empty")
    if playable is True and http_status in _TERMINAL_HTTP_STATUSES:
        problems.append(f"precheck.playable=true conflicts with terminal http_status={http_status}")
    return problems


def _problems_metadata_extract(job_payload: dict[str, Any], result: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if not _text(result.get("title")):
        problems.append("metadata.title missing or empty")
    duration = _int_or_none(result.get("duration_seconds", result.get("duration")))
    if duration is None or duration < 0:
        problems.append("metadata.duration_seconds (or duration) missing or not a non-negative int")
    view_count = _int_or_none(result.get("view_count"))
    if view_count is None or view_count < 0:
        problems.append("metadata.view_count missing or not a non-negative int")
    payload_url = _pick_url(job_payload)
    result_url = _pick_url(result)
    if not payload_url:
        problems.append("job payload has no url field - result url cannot be verified, refusing to trust")
    elif not result_url:
        problems.append("metadata.url missing - must echo the leased job url")
    elif _norm_url(payload_url) != _norm_url(result_url):
        problems.append("metadata url does not match leased job payload url")
    return problems


def _problems_download_frames(result: dict[str, Any], files_meta: list[Any]) -> list[str]:
    problems: list[str] = []
    declared = _int_or_none(result.get("frame_count"))
    if declared is None and isinstance(result.get("frames"), list):
        declared = len(result["frames"])
    if declared is None or declared < 1:
        problems.append("frames.frame_count missing or below 1")
    metas = files_meta if isinstance(files_meta, list) else []
    if not metas:
        problems.append("files_meta is empty - frames submission must declare files")
    for idx, meta in enumerate(metas):
        if not isinstance(meta, dict):
            problems.append(f"files_meta[{idx}] is not an object")
            continue
        if not _text(meta.get("name")):
            problems.append(f"files_meta[{idx}].name missing")
        sha = _text(meta.get("sha256")).lower()
        if len(sha) != 64 or any(ch not in _HEX_DIGITS for ch in sha):
            problems.append(f"files_meta[{idx}].sha256 is not 64-hex")
        size = meta.get("bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            problems.append(f"files_meta[{idx}].bytes must be an int above 0")
    if declared is not None and metas and len(metas) != declared:
        problems.append(f"frame_count={declared} does not match files_meta count={len(metas)}")
    return problems


def _problems_comment_clean(result: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    comments = result.get("comments")
    if not isinstance(comments, list):
        problems.append("comments must be a JSON array under result.comments")
        return problems
    for idx, item in enumerate(comments):
        if len(problems) >= 5:
            problems.append("more problems truncated")
            break
        if not isinstance(item, dict):
            problems.append(f"comments[{idx}] is not an object")
            continue
        if not any(_text(item.get(key)) for key in _TEXT_KEYS_COMMENT):
            problems.append(f"comments[{idx}] has no non-empty text field")
        if not any(_text(item.get(key)) for key in _LANG_KEYS_COMMENT):
            problems.append(f"comments[{idx}] has no language field (lang / language)")
    return problems


def deep_problems(
    task_type: str,
    job_payload: dict[str, Any],
    result: Any,
    files_meta: list[Any] | None,
) -> list[str]:
    """按 task_type 分支的深校验。返回问题列表,空=通过。"""
    if task_type not in SAFE_TASK_TYPES:
        return [f"task_type not in safe whitelist: {task_type or '(empty)'}"]
    if not isinstance(result, dict):
        return ["result must be a JSON object"]
    payload = job_payload if isinstance(job_payload, dict) else {}
    metas = files_meta if isinstance(files_meta, list) else []
    if task_type == "video_precheck":
        return _problems_video_precheck(result)
    if task_type == "metadata_extract":
        return _problems_metadata_extract(payload, result)
    if task_type == "download_frames":
        return _problems_download_frames(result, metas)
    return _problems_comment_clean(result)


# ── 双形态入口 ────────────────────────────────────────────────────────────────


def _resolve_task_type_via_marker(job_payload: dict[str, Any]) -> tuple[str, int]:
    """W1 钩子形态:payload.local_lease_id 回查租约拿 task_type。查不到回 ('', 0)。"""
    lease_id = _int_or_none((job_payload or {}).get(_MARKER_LEASE_ID)) or 0
    if lease_id <= 0:
        return "", 0
    try:
        row = get_conn().execute(
            f"SELECT task_type FROM {LEASE_TABLE} WHERE id = ?",
            (lease_id,),
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 — 回查失败按解析不出处理,诚实降级
        logger.warning("local_workers validation marker lookup failed: %s", exc)
        return "", lease_id
    if row is None:
        return "", lease_id
    return str(row["task_type"] or ""), lease_id


def validate_submission(
    lease_or_payload: dict[str, Any],
    result: Any,
    files_meta: list[Any] | None = None,
) -> Any:
    """双形态深校验入口(形态由首参是否带 task_type 判定,见模块 docstring)。

    - lease_view(含 task_type,payload 键为任务 payload)-> dict{ok, notes, problems, task_type}
    - job_payload(W1 registry.submit_result 钩子)-> tuple(ok: bool, notes: list[str])
    """
    first = lease_or_payload if isinstance(lease_or_payload, dict) else {}
    declared_type = str(first.get("task_type") or "")
    if declared_type in SAFE_TASK_TYPES:
        payload = _loads(first.get("payload"), {})
        problems = deep_problems(declared_type, payload, result, files_meta)
        ok = not problems
        return {
            "ok": ok,
            "task_type": declared_type,
            "problems": problems,
            "notes": "; ".join(problems)[:900] if problems else "deep checks passed",
        }

    # W1 钩子形态:首参是 apify_jobs.payload。
    task_type, marker_lease_id = _resolve_task_type_via_marker(first)
    if not task_type:
        # 解析不出任务类型 = 深校验无从分支;诚实跳过(W1 的基础校验仍在),不装深。
        return True, [
            f"deep_validation_skipped: task_type unresolved (payload {_MARKER_LEASE_ID} marker missing, lease_id={marker_lease_id})"
        ]
    problems = deep_problems(task_type, first, result, files_meta)
    return (not problems), problems


# ── 校验桥:staging -> 既有函数落库(POST /lease/{id}/validate 手动触发) ─────────


def _load_job_payload(db: Any, job_id: int) -> dict[str, Any]:
    if not job_id:
        return {}
    row = db.execute("SELECT payload FROM apify_jobs WHERE id = ?", (int(job_id),)).fetchone()
    if row is None:
        return {}
    return _loads(row["payload"], {}) if not isinstance(row["payload"], dict) else row["payload"]


def _metadata_from_result(result: dict[str, Any], url: str) -> dict[str, Any]:
    """把已通过深校验的 metadata_extract 结果映射成既有 evidence 写入函数吃的 metadata。"""
    return {
        "content_url": url,
        "title": _text(result.get("title")),
        "view_count": _int_or_none(result.get("view_count")),
        "like_count": _int_or_none(result.get("like_count")),
        "comment_count": _int_or_none(result.get("comment_count")),
        "share_count": _int_or_none(result.get("share_count")),
        "duration_seconds": _int_or_none(result.get("duration_seconds", result.get("duration"))),
        "platform": _text(result.get("platform")),
        "thumbnail_url": _text(result.get("thumbnail_url")),
        "channel_id": _text(result.get("channel_id")),
        "channel_name": _text(result.get("channel_name")),
        "posted_at": result.get("posted_at"),
        "publish_date": result.get("publish_date"),
        "media_kind": _text(result.get("media_kind")),
    }


def _ingest_validated(
    db: Any,
    task_type: str,
    job_payload: dict[str, Any],
    result: dict[str, Any],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """校验通过后的落库分支。只走既有函数;没有安全落点就诚实 pending_ingest。

    回传 dict 的 final=True 仅当真实落库成功(此时才可把 apify_jobs 标 done)。
    """
    if task_type == "metadata_extract":
        kol_pool_id = _int_or_none(
            job_payload.get("kol_pool_id") or job_payload.get("kol_id") or job_payload.get("pool_id")
        ) or 0
        if kol_pool_id <= 0:
            return {
                "status": "pending_ingest:no_kol_pool_id_in_job_payload",
                "final": False,
                "detail": "job payload carries no kol_pool_id - evidence owner unknown, staging kept, not guessing",
            }
        url = _pick_url(result)
        # 既有安全写入函数:自带分数守卫 + 同 URL 错挂其他 KOL 的 conflict 拒绝(错配红线)。
        from app.domains.kol.video_evidence import ensure_video_evidence_from_url

        try:
            out = ensure_video_evidence_from_url(
                kol_pool_id,
                url,
                metadata=_metadata_from_result(result, url),
                dry_run=dry_run,
                conn=db,
                method=INGEST_METHOD,
            )
        except Exception as exc:  # noqa: BLE001 — 落库失败不硬写,staging 保留待重试
            logger.warning("local_workers metadata ingest failed: %s", exc)
            return {
                "status": f"pending_ingest:ingest_error:{exc.__class__.__name__}",
                "final": False,
                "error": str(exc)[:200],
            }
        if not out.get("ok"):
            return {
                "status": f"ingest_refused:{out.get('status') or 'unknown'}",
                "final": False,
                "detail": "existing evidence writer refused (attribution conflict guard)",
            }
        if dry_run:
            return {"status": f"ingest_dry_run:{out.get('status')}", "final": False, "evidence_id": out.get("evidence_id")}
        return {"status": f"ingested:{out.get('status')}", "final": True, "evidence_id": out.get("evidence_id")}

    if task_type == "video_precheck":
        return {
            "status": "pending_ingest:no_reusable_precheck_writer",
            "final": False,
            "detail": (
                "precheck outcomes land inline in the worker job flow "
                "(apify_jobs_worker_gemini precheck_terminal branch) - no existing public writer; "
                "validated result kept in lease staging for the main-loop hookup"
            ),
        }
    if task_type == "download_frames":
        return {
            "status": "pending_ingest:frames_meta_only",
            "final": False,
            "detail": "frame bytes never travel through this bridge; sha256+bytes manifest validated and kept in staging",
        }
    return {
        "status": "pending_ingest:no_direct_comment_table_write",
        "final": False,
        "detail": "cleaned comments stay in staging; comments domain functions own the real tables",
    }


def _mark_job_done_after_ingest(db: Any, job_id: int, lease_id: int) -> str:
    """真实落库成功后收口任务:仍是 queued 且 claimed 标记是本租约时标 done 并清标记。"""
    if not job_id:
        return "job_missing"
    cur = db.execute(
        f"""
        UPDATE apify_jobs
        SET status = 'done', updated_at = NOW()
        WHERE id = ? AND status = 'queued' AND payload->>'{_MARKER_LEASE_ID}' = ?
        """,
        (int(job_id), str(int(lease_id))),
    )
    if int(getattr(cur, "rowcount", 0) or 0) > 0:
        _release_job_marker(db, int(job_id), int(lease_id))
        return "job_marked_done"
    return "job_not_marked:status_moved_or_marker_mismatch"


def apply_validation(lease_id: int, *, dry_run: bool = False, conn: Any | None = None) -> dict[str, Any]:
    """校验桥主入口:读 staging -> 深校验 -> 通过则经既有函数落库并回填 lease 行。

    - 只处理 status=submitted 的租约(leased=还没交,rejected/expired=已定论,不复活);
    - 通过:result_validated=1 + validation_notes 记录 ingest 去向;
      真实落库成功(final)才把 apify_jobs 标 done 并清 claimed 标记;
    - 不通过:status=rejected + result_validated=0 + 释放 claimed 标记供重派。
    """
    db = conn or get_conn()
    if not table_exists(LEASE_TABLE):
        raise RuntimeError(f"{LEASE_TABLE} missing - apply migration 213 before using the validation bridge")
    row = db.execute(
        f"""
        SELECT id, job_id, device_id, task_type, status, result_json, result_validated
        FROM {LEASE_TABLE}
        WHERE id = ?
        """,
        (int(lease_id),),
    ).fetchone()
    if row is None:
        raise LookupError(f"unknown lease_id: {lease_id}")
    lease = {key: row[key] for key in row.keys()}
    status = str(lease.get("status") or "")
    task_type = str(lease.get("task_type") or "")
    job_id = int(lease.get("job_id") or 0)
    base = {"lease_id": int(lease_id), "job_id": job_id, "task_type": task_type, "dry_run": bool(dry_run)}

    if status != "submitted":
        return {**base, "ok": False, "skipped": True, "reason": f"lease_status_not_submitted:{status or '(empty)'}"}

    staging = _loads(lease.get("result_json"), {})
    result = staging.get("result") if isinstance(staging, dict) else None
    files_meta = staging.get("files_meta") if isinstance(staging, dict) else None
    job_payload = _load_job_payload(db, job_id)

    if not isinstance(result, dict):
        notes = "staging result_json has no result object - rejecting, releasing job for redispatch"
        db.execute(
            f"""
            UPDATE {LEASE_TABLE}
            SET status = 'rejected', result_validated = 0, validation_notes = ?, error_code = 'invalid_result'
            WHERE id = ?
            """,
            (notes, int(lease_id)),
        )
        _release_job_marker(db, job_id, int(lease_id))
        db.commit()
        return {**base, "ok": False, "validated": 0, "notes": notes}

    problems = deep_problems(task_type, job_payload, result, files_meta)
    if problems:
        notes = ("; ".join(problems))[:900]
        db.execute(
            f"""
            UPDATE {LEASE_TABLE}
            SET status = 'rejected', result_validated = 0, validation_notes = ?, error_code = 'invalid_result'
            WHERE id = ?
            """,
            (notes, int(lease_id)),
        )
        _release_job_marker(db, job_id, int(lease_id))
        db.commit()
        logger.info("local lease validation rejected: lease_id=%s notes=%s", lease_id, notes[:200])
        return {**base, "ok": False, "validated": 0, "notes": notes, "problems": problems}

    ingest = _ingest_validated(db, task_type, job_payload, result, dry_run=bool(dry_run))
    notes = f"deep checks passed | ingest={ingest.get('status')}"[:900]
    db.execute(
        f"""
        UPDATE {LEASE_TABLE}
        SET result_validated = 1, validation_notes = ?, error_code = NULL
        WHERE id = ?
        """,
        (notes, int(lease_id)),
    )
    if ingest.get("final"):
        ingest["job_action"] = _mark_job_done_after_ingest(db, job_id, int(lease_id))
    db.commit()
    logger.info(
        "local lease validation passed: lease_id=%s task_type=%s ingest=%s",
        lease_id, task_type, ingest.get("status"),
    )
    return {**base, "ok": True, "validated": 1, "notes": notes, "ingest": ingest}

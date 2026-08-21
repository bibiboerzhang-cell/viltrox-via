"""收口路①:把内容契合深析(地基B)接进 smart-search 流水线的「思考中」段。

裁令(Jianbo 多轮成文):搜索拿到候选(库内召回 + 全网发现)后,对**头部 N 个有视频
证据的库内候选**异步入队 content_fit_analysis;队列把它映到「思考中」桶;完成后结果可读。

本模块只做编排入队 + 营销影响展示计算,**不**自己烧 LLM、**不**写任何评分列:
  - 入队走既有 apify_jobs 骨架,新 job_type='kol_content_fit_analysis';
    worker 端 _process_kol_content_fit_analysis 调 content_fit_analysis.analyze_content_fit
    (后者已是 openai+3 重试、独立 cache target_type='kol'/derive_method='content_fit_v1')。
  - 控量三闸防烧爆:① 只取 top N(默认 6);② 只对**库内候选**(有 kol_pool_id);
    ③ 只对**已有 ready video_analysis_final_v1 证据**的候选入队(无证据 analyze 会
    insufficient_evidence,白烧一次调度);④ 已有 content_fit_v1 cache 的跳过(复用)。
  - exposure_potential(营销影响)= 粉丝 × 真实ER × 内容契合度,**纯展示字段**,
    在 pipeline 汇总时算、随候选透出;绝不写库、绝不并入 viltrox_fit_score。

红线(逐条):
  - 零写 viltrox_fit_score / 任何评分列;viltrox_fit_score 唯一写点仍是 pool.py enrich_item。
  - 不改 rule_v0 评分公式;exposure_potential 是独立展示信号,不回写。
  - LLM 仅由 worker→content_fit_analysis 经 llm_gateway 烧,预算走既有闸;本模块零 LLM。
  - 不新增 viltrox_fit 第二写路径;DB 写只 INSERT apify_jobs(应用路径,与其它入队同构)。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol import content_fit_analysis, search_sessions
from app.domains.tasks.apify_idempotency import active_job_idempotency_key, enqueue_active_apify_job
from app.domains.tasks.search_session_lineage import (
    attach_search_session_lineage_to_job,
    with_search_session_lineage,
)
from app.domains.kol.provider_job_access import (
    CONTENT_FIT_ANALYSIS as CONTENT_FIT_PROVIDER_ACTION,
    FENCE_KEY as PROVIDER_FENCE_KEY,
    build_content_fit_provider_fence,
)
from app.platform import llm_gateway

logger = get_logger("viltrox.domains.kol.content_fit_enqueue")

CONTENT_FIT_JOB_TYPE = "kol_content_fit_analysis"
# 控量:每次 pipeline 至多入队这么多内容契合深析(头部候选),避免烧爆 LLM 预算。
DEFAULT_TOP_N = 6
MAX_TOP_N = 12
VIDEO_DERIVE_METHOD = "video_analysis_final_v1"
CONTENT_FIT_DERIVE_METHOD = "content_fit_v1"
CONTENT_FIT_LLM_PURPOSE = "vkpi_kol_content_fit"
CONTENT_FIT_LLM_PROVIDER = "openai"
CONTENT_FIT_LLM_MAX_OUTPUT_TOKENS = 1400
CONTENT_FIT_LLM_COST_TAG = "vkpi_kol_content_fit"


def _content_fit_ai_readiness() -> dict[str, Any]:
    """Read-only authorization check matching the content-fit LLM call chain."""

    try:
        preflight = llm_gateway.budget_preflight(
            "content fit readiness preflight",
            purpose=CONTENT_FIT_LLM_PURPOSE,
            max_output_tokens=CONTENT_FIT_LLM_MAX_OUTPUT_TOKENS,
            preferred_provider=CONTENT_FIT_LLM_PROVIDER,
            cost_tag=CONTENT_FIT_LLM_COST_TAG,
            require_configured=False,
        )
    except Exception:
        logger.warning("vkpi.content_fit_enqueue.readiness_probe_failed", exc_info=True)
        preflight = {
            "provider_calls_allowed": False,
            "provider_gate_reason": "readiness_check_failed",
            "model_readiness_status": "not_ready",
        }
    allowed = bool(preflight.get("provider_calls_allowed"))
    gate_reason = str(preflight.get("provider_gate_reason") or "provider_calls_blocked")
    return {
        "allowed": allowed,
        "gate_reason": gate_reason,
        "model_readiness_status": str(
            preflight.get("model_readiness_status")
            or ("production_ready" if allowed else "not_ready")
        ),
        "ai_analysis": {
            "state": "queued" if allowed else "not_requested",
            "reason": "analysis_authorized" if allowed else "ai_disabled",
            "gate_reason": gate_reason,
            "model_readiness_status": str(
                preflight.get("model_readiness_status")
                or ("production_ready" if allowed else "not_ready")
            ),
            "provider_calls_allowed": allowed,
        },
    }


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ids_with_video_evidence(conn: Any, kol_pool_ids: list[int]) -> set[int]:
    """只保留**已有 ready video_analysis_final_v1 证据**的候选(纯 SELECT,不触 fit)。

    无证据者 analyze_content_fit 会返回 insufficient_evidence——提前剔除可省一次空调度。
    """
    wanted = [pid for pid in kol_pool_ids if pid and pid > 0]
    if not wanted:
        return set()
    placeholders = ",".join("?" for _ in wanted)
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT e.kol_pool_id
            FROM vkpi_kol_video_evidence e
            JOIN vkpi_analysis_cache ac
              ON ac.target_type = 'video'
             AND ac.target_id = CAST(e.id AS TEXT)
             AND ac.derive_method = ?
             AND ac.status = 'ready'
            WHERE e.kol_pool_id IN ({placeholders})
            """,
            (VIDEO_DERIVE_METHOD, *wanted),
        ).fetchall()
    except Exception:
        logger.warning("vkpi.content_fit_enqueue.evidence_probe_failed", exc_info=True)
        return set()
    return {int(row["kol_pool_id"]) for row in rows if row and row["kol_pool_id"] is not None}


def _ids_with_existing_fit(
    conn: Any,
    kol_pool_ids: list[int],
    product_sku: str | None = None,
) -> set[int]:
    """已有 ready content_fit_v1 cache 的候选(复用,不重复入队/烧 LLM)。纯 SELECT。"""
    wanted = [pid for pid in kol_pool_ids if pid and pid > 0]
    if not wanted:
        return set()
    placeholders = ",".join("?" for _ in wanted)
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT CAST(target_id AS INTEGER) AS kid
            FROM vkpi_analysis_cache
            WHERE target_type = 'kol'
              AND derive_method = ?
              AND status = 'ready'
              AND CAST(target_id AS INTEGER) IN ({placeholders})
            """,
            (content_fit_analysis.content_fit_derive_method(product_sku), *wanted),
        ).fetchall()
    except Exception:
        logger.warning("vkpi.content_fit_enqueue.fit_cache_probe_failed", exc_info=True)
        return set()
    return {int(row["kid"]) for row in rows if row and row["kid"] is not None}


def _already_queued_ids(
    conn: Any,
    session_id: int,
    kol_pool_ids: list[int],
    product_sku: str | None = None,
) -> set[int]:
    """同 session 已有 queued/running 的内容契合 job 的 kol_pool_id(去重,防重复入队)。"""
    wanted = [pid for pid in kol_pool_ids if pid and pid > 0]
    if not wanted:
        return set()
    try:
        rows = conn.execute(
            """
            SELECT (payload->>'kol_pool_id') AS kid
            FROM apify_jobs
            WHERE job_type = ?
              AND status IN ('queued', 'running', 'retrying')
              AND payload->>'search_session_id' = ?
              AND COALESCE(payload->>'product_sku', '') = ?
            """,
            (
                CONTENT_FIT_JOB_TYPE,
                str(int(session_id)),
                content_fit_analysis.normalize_product_sku(product_sku),
            ),
        ).fetchall()
    except Exception:
        logger.warning("vkpi.content_fit_enqueue.dedupe_probe_failed", exc_info=True)
        return set()
    out: set[int] = set()
    for row in rows:
        parsed = _int(row["kid"] if not isinstance(row, dict) else row.get("kid"))
        if parsed > 0:
            out.add(parsed)
    return out


def _top_pool_candidates(session: dict[str, Any], top_n: int) -> list[dict[str, Any]]:
    """从 session items 取**库内候选**(有 kol_pool_id)头部 N 个。

    recall_candidate / existing_kol 才有 kol_pool_id;new_creator 是全网新号(此刻无库内
    视频证据,内容契合需先补全画像→另一条链),故本轮不对 new_creator 入队内容契合。
    items 已按 rank 排序(get_session ORDER BY rank);保序取头部。
    """
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in session.get("items") or []:
        item_type = str(item.get("item_type") or "")
        if item_type not in ("recall_candidate", "existing_kol"):
            continue
        kid = _int(item.get("kol_pool_id"))
        if kid <= 0 or kid in seen:
            continue
        seen.add(kid)
        out.append(item)
        if len(out) >= top_n:
            break
    return out


def _exposure_potential(conn: Any, kol_pool_id: int, fit_confidence: float | None) -> dict[str, Any]:
    """营销影响(纯展示):exposure_potential = 粉丝 × 真实ER × 内容契合度。

    - 粉丝 / ER 读 vkpi_kol_pool(真实列);ER 缺则用 avg_likes/avg_views 兜底估,再缺记为 None。
    - 内容契合度 = content_fit 的 confidence(0~1);此刻可能尚未跑完 → 缺则按 1.0 计基线
      (只用粉丝×ER 的「可触达曝光基线」),并标 fit_factor_applied=False。
    - 绝不写库、绝不并入 viltrox_fit_score;仅作候选「触达/曝光潜力」展示与排序辅助。
    """
    row = conn.execute(
        "SELECT followers, engagement_rate, avg_views, avg_likes, avg_comments FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        return {"kol_pool_id": int(kol_pool_id), "exposure_potential": None, "basis": "kol_not_found"}
    data = dict(row)
    followers = _float_or_none(data.get("followers")) or 0.0
    er = _float_or_none(data.get("engagement_rate"))
    er_basis = "engagement_rate"
    if er is None or er <= 0:
        avg_views = _float_or_none(data.get("avg_views")) or 0.0
        avg_likes = _float_or_none(data.get("avg_likes")) or 0.0
        avg_comments = _float_or_none(data.get("avg_comments")) or 0.0
        if avg_views > 0:
            er = (avg_likes + avg_comments) / avg_views
            er_basis = "avg_likes_comments_over_views"
        else:
            er = None
            er_basis = "missing"
    # ER 入库口径可能是百分数(如 3.2 表示 3.2%)或比率(0.032);>1 视为百分数,归一到比率。
    er_ratio = None
    if er is not None and er > 0:
        er_ratio = er / 100.0 if er > 1 else er
    fit_factor = fit_confidence if (fit_confidence is not None and fit_confidence > 0) else None
    fit_applied = fit_factor is not None
    effective_fit = fit_factor if fit_applied else 1.0
    exposure = None
    if followers > 0 and er_ratio is not None:
        exposure = round(followers * er_ratio * effective_fit, 2)
    return {
        "kol_pool_id": int(kol_pool_id),
        "followers": int(followers) if followers else None,
        "engagement_ratio": round(er_ratio, 6) if er_ratio is not None else None,
        "engagement_basis": er_basis,
        "content_fit_confidence": round(fit_factor, 3) if fit_factor is not None else None,
        "fit_factor_applied": fit_applied,
        "exposure_potential": exposure,
        "formula": "followers * engagement_ratio * content_fit_confidence (display-only; never written to fit score)",
    }


def enqueue_content_fit_for_session(
    *,
    session_id: int,
    product_sku: str = "",
    top_n: int = DEFAULT_TOP_N,
    triggered_by_user_id: int | None = None,
    provider_actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对 session 头部库内候选异步入队内容契合深析(「思考中」),并算 exposure_potential 展示。

    幂等/控量:有视频证据 + 无 ready fit cache + 未在同 session 排队 → 才入队;至多 top_n 个。
    返回入队明细 + 每候选 exposure_potential(纯展示);零写 fit,零烧 LLM。
    """
    sid = int(session_id)
    normalized_product_sku = content_fit_analysis.normalize_product_sku(product_sku)
    derive_method = content_fit_analysis.content_fit_derive_method(normalized_product_sku)
    safe_top_n = max(1, min(_int(top_n, DEFAULT_TOP_N), MAX_TOP_N))
    session = search_sessions.get_session(sid)
    session_owner_user_id = _int(session.get("created_by"))
    if session_owner_user_id <= 0:
        from app.domains.kol.provider_job_access import ProviderJobAccessError

        raise ProviderJobAccessError("content_fit_session_owner_invalid", 403)
    if session_owner_user_id > 0 and (
        not isinstance(provider_actor, dict)
        or provider_actor.get("server_owned") is True
        or _int(provider_actor.get("user_id")) != session_owner_user_id
    ):
        from app.domains.kol.provider_job_access import ProviderJobAccessError

        raise ProviderJobAccessError("content_fit_parent_actor_required", 403)
    candidates = _top_pool_candidates(session, safe_top_n)
    pool_ids = [_int(c.get("kol_pool_id")) for c in candidates]

    conn = get_conn()
    has_evidence = _ids_with_video_evidence(conn, pool_ids)
    # Preserve the historical generic seam (two-argument probes) when no
    # product was selected; product-scoped sessions opt into the extra key.
    # Besides keeping old callers/tests compatible, this makes the migration
    # rule explicit: existing content_fit_v1 rows remain generic-only.
    if normalized_product_sku:
        has_fit = _ids_with_existing_fit(conn, pool_ids, normalized_product_sku)
        already_queued = _already_queued_ids(conn, sid, pool_ids, normalized_product_sku)
    else:
        has_fit = _ids_with_existing_fit(conn, pool_ids)
        already_queued = _already_queued_ids(conn, sid, pool_ids)
    readiness = _content_fit_ai_readiness()

    enqueued: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    exposure: list[dict[str, Any]] = []
    ai_disabled_count = 0

    for cand in candidates:
        kid = _int(cand.get("kol_pool_id"))
        # content_fit confidence 取已 ready 的(若有);否则 exposure 用基线(不含 fit 因子)。
        fit_conf: float | None = None
        if kid in has_fit:
            try:
                from app.domains.kol import content_fit_analysis as _cfa

                cached = _cfa.get_content_fit(kid, normalized_product_sku)
                fit_conf = _float_or_none(((cached or {}).get("result") or {}).get("confidence"))
            except Exception:
                fit_conf = None
        exposure.append(_exposure_potential(conn, kid, fit_conf))

        if kid in has_fit:
            skipped.append({"kol_pool_id": kid, "reason": "content_fit_cache_exists"})
            continue
        if kid not in has_evidence:
            skipped.append({"kol_pool_id": kid, "reason": "no_ready_video_analysis_evidence"})
            continue
        if not readiness["allowed"]:
            ai_disabled_count += 1
            skipped.append(
                {
                    "kol_pool_id": kid,
                    "reason": "ai_disabled",
                    "provider_gate_reason": readiness["gate_reason"],
                }
            )
            continue
        if kid in already_queued:
            skipped.append({"kol_pool_id": kid, "reason": "already_queued_in_session"})
            continue

        payload = with_search_session_lineage({
            "queue_lane": "batch",
            "target_type": "kol",
            "target_id": str(kid),
            "derive_method": derive_method,
            "search_session_id": sid,
            "kol_pool_id": kid,
            "product_sku": normalized_product_sku,
            "handle": (cand.get("payload") or {}).get("handle") if isinstance(cand.get("payload"), dict) else None,
            "platform": (cand.get("payload") or {}).get("platform") if isinstance(cand.get("payload"), dict) else None,
            "prompt": f"content fit analysis · kol:{kid}",
            "summary": f"内容契合深析 · KOL {kid}",
            "triggered_by_user_id": (
                _int((provider_actor or {}).get("user_id"))
                or triggered_by_user_id
            ),
            "staff_id": _int((provider_actor or {}).get("staff_id") or (provider_actor or {}).get("id")) or None,
            "viltrox_fit_score_untouched": True,
        },
            search_session_id=sid,
            search_session_item_id=_int(cand.get("id")) or None,
            role="content_fit",
        )
        payload[PROVIDER_FENCE_KEY] = build_content_fit_provider_fence(
            payload=payload,
            session=session,
            staff=provider_actor,
        )
        try:
            row, inserted = enqueue_active_apify_job(
                conn,
                job_type=CONTENT_FIT_JOB_TYPE,
                payload=payload,
                idempotency_key=active_job_idempotency_key(
                    CONTENT_FIT_JOB_TYPE,
                    "session",
                    sid,
                    kid,
                    normalized_product_sku,
                ),
            )
            conn.commit()
            if inserted:
                enqueued.append({"kol_pool_id": kid, "job_id": row.get("id")})
            else:
                linked = attach_search_session_lineage_to_job(conn, row.get("id"), payload)
                if linked:
                    conn.commit()
                skipped.append({"kol_pool_id": kid, "job_id": row.get("id"), "reason": "already_queued_race"})
        except Exception as exc:
            logger.warning(
                "vkpi.content_fit_enqueue.insert_failed kid=%s exception_type=%s",
                kid,
                type(exc).__name__,
            )
            skipped.append({"kol_pool_id": kid, "reason": "enqueue_failed"})

    status = "queued" if enqueued else "ai_disabled" if ai_disabled_count else "nothing_to_queue"
    ai_analysis = dict(readiness["ai_analysis"])
    if enqueued:
        ai_analysis.update({"state": "queued", "reason": "analysis_queued"})
    elif not ai_disabled_count:
        ai_analysis.update({"state": "not_requested", "reason": "no_eligible_candidate"})
    return {
        "status": status,
        "state": "not_requested" if status == "ai_disabled" else status,
        "stage": "ai_disabled" if status == "ai_disabled" else "analysis",
        "terminal": status in {"ai_disabled", "nothing_to_queue"},
        "session_id": sid,
        "product_sku": normalized_product_sku or None,
        "derive_method": derive_method,
        "top_n": safe_top_n,
        "candidate_count": len(candidates),
        "enqueued": enqueued,
        "enqueued_count": len(enqueued),
        "skipped": skipped,
        "exposure_potential": exposure,
        "job_type": CONTENT_FIT_JOB_TYPE,
        "ai_disabled_count": ai_disabled_count,
        "provider_gate_reason": readiness["gate_reason"],
        "model_readiness_status": readiness["model_readiness_status"],
        "ai_analysis": ai_analysis,
        "write_db": bool(enqueued),
        "writes": ["apify_jobs"] if enqueued else [],
        "viltrox_fit_score_untouched": True,
    }


def enqueue_content_fit_on_demand(
    kol_pool_id: int,
    product_sku: str | None,
    *,
    force: bool,
    staff: dict[str, Any] | None,
    enforce_target_write: bool = False,
) -> dict[str, Any]:
    """T4 · 单 KOL 内容契合深析按需入队(L1:从 vkpi_kol_pool router 抽出)。

    纯 DB、不烧 LLM:已有 ready cache 且非 force → 返缓存;否则入队(worker 跑 LLM)。
    去重:同 KOL queued/running → already_queued。红线:零触 viltrox_fit_score。
    """
    from app.db.connection import get_conn
    from app.domains.kol import content_fit_analysis as kol_content_fit

    kid = int(kol_pool_id)
    conn = get_conn()
    target_fence: dict[str, Any] | None = None
    if enforce_target_write:
        from app.domains.kol.my_kol_paid_action_access import build_target_fence

        target_fence = build_target_fence(
            conn,
            action="content_fit_analysis",
            kol_pool_id=kid,
            staff=staff,
        )
    normalized_product_sku = kol_content_fit.normalize_product_sku(product_sku)
    derive_method = kol_content_fit.content_fit_derive_method(normalized_product_sku)

    if not force:
        cached = kol_content_fit.get_content_fit(kid, normalized_product_sku)
        if cached and str(cached.get("state") or "") not in ("missing", ""):
            return cached

    readiness = _content_fit_ai_readiness()
    if not readiness["allowed"]:
        return {
            "status": "ai_disabled",
            "state": "not_requested",
            "stage": "ai_disabled",
            "terminal": True,
            "kol_pool_id": kid,
            "product_sku": normalized_product_sku or None,
            "derive_method": derive_method,
            "job_id": None,
            "job_type": CONTENT_FIT_JOB_TYPE,
            "reason": "ai_disabled",
            "provider_gate_reason": readiness["gate_reason"],
            "model_readiness_status": readiness["model_readiness_status"],
            "ai_analysis": readiness["ai_analysis"],
            "provider_calls": False,
            "write_db": False,
            "writes": [],
            "viltrox_fit_score_untouched": True,
        }

    existing = conn.execute(
        """
        SELECT id, status FROM apify_jobs
        WHERE job_type=?
          AND status IN ('queued', 'running', 'retrying')
          AND payload->>'kol_pool_id'=?
          AND COALESCE(payload->>'product_sku', '')=?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (CONTENT_FIT_JOB_TYPE, str(kid), normalized_product_sku),
    ).fetchone()
    if existing:
        row = dict(existing)
        return {
            "status": "already_queued" if str(row.get("status")) == "queued" else "already_running",
            "state": "queued",
            "kol_pool_id": kid,
            "product_sku": normalized_product_sku or None,
            "derive_method": derive_method,
            "job_id": int(row.get("id")) if row.get("id") is not None else None,
            "job_type": CONTENT_FIT_JOB_TYPE,
            "ai_analysis": {
                **readiness["ai_analysis"],
                "state": "queued",
                "reason": "already_queued",
            },
            "viltrox_fit_score_untouched": True,
        }

    triggered_by_user_id = (staff or {}).get("user_id") if isinstance(staff, dict) else None
    payload = {
        "queue_lane": "interactive",
        "kol_pool_id": kid,
        "target_type": "kol",
        "target_id": str(kid),
        "product_sku": normalized_product_sku or None,
        "derive_method": derive_method,
        "source": "kol_pool_detail_on_demand",
        "trigger": "content_fit_on_demand",
        "force": bool(force),
        "triggered_by_user_id": triggered_by_user_id,
        "viltrox_fit_score_untouched": True,
        "query_text": f"content fit - kol_pool #{kid}",
    }
    if target_fence is not None:
        from app.domains.kol.my_kol_paid_action_access import FENCE_KEY

        payload[FENCE_KEY] = target_fence
    job, inserted = enqueue_active_apify_job(
        conn,
        job_type=CONTENT_FIT_JOB_TYPE,
        payload=payload,
        idempotency_key=active_job_idempotency_key(
            CONTENT_FIT_JOB_TYPE,
            kid,
            normalized_product_sku,
        ),
    )
    conn.commit()
    job_id = job.get("id")
    return {
        "status": "queued" if inserted else ("already_running" if job.get("status") == "running" else "already_queued"),
        "state": "queued",
        "kol_pool_id": kid,
        "product_sku": normalized_product_sku or None,
        "derive_method": derive_method,
        "job_id": int(job_id) if job_id is not None else None,
        "job_type": CONTENT_FIT_JOB_TYPE,
        "ai_analysis": {
            **readiness["ai_analysis"],
            "state": "queued",
            "reason": "analysis_queued" if inserted else "already_queued",
        },
        "force": bool(force),
        "write_db": inserted,
        "writes": ["apify_jobs"] if inserted else [],
        "viltrox_fit_score_untouched": True,
    }


__all__ = [
    "enqueue_content_fit_for_session",
    "enqueue_content_fit_on_demand",
    "CONTENT_FIT_JOB_TYPE",
    "DEFAULT_TOP_N",
]

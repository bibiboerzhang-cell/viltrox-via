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
from app.domains.kol import search_sessions

logger = get_logger("viltrox.domains.kol.content_fit_enqueue")

CONTENT_FIT_JOB_TYPE = "kol_content_fit_analysis"
# 控量:每次 pipeline 至多入队这么多内容契合深析(头部候选),避免烧爆 LLM 预算。
DEFAULT_TOP_N = 6
MAX_TOP_N = 12
VIDEO_DERIVE_METHOD = "video_analysis_final_v1"
CONTENT_FIT_DERIVE_METHOD = "content_fit_v1"


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


def _ids_with_existing_fit(conn: Any, kol_pool_ids: list[int]) -> set[int]:
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
            (CONTENT_FIT_DERIVE_METHOD, *wanted),
        ).fetchall()
    except Exception:
        logger.warning("vkpi.content_fit_enqueue.fit_cache_probe_failed", exc_info=True)
        return set()
    return {int(row["kid"]) for row in rows if row and row["kid"] is not None}


def _already_queued_ids(conn: Any, session_id: int, kol_pool_ids: list[int]) -> set[int]:
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
            """,
            (CONTENT_FIT_JOB_TYPE, str(int(session_id))),
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
) -> dict[str, Any]:
    """对 session 头部库内候选异步入队内容契合深析(「思考中」),并算 exposure_potential 展示。

    幂等/控量:有视频证据 + 无 ready fit cache + 未在同 session 排队 → 才入队;至多 top_n 个。
    返回入队明细 + 每候选 exposure_potential(纯展示);零写 fit,零烧 LLM。
    """
    sid = int(session_id)
    safe_top_n = max(1, min(_int(top_n, DEFAULT_TOP_N), MAX_TOP_N))
    session = search_sessions.get_session(sid)
    candidates = _top_pool_candidates(session, safe_top_n)
    pool_ids = [_int(c.get("kol_pool_id")) for c in candidates]

    conn = get_conn()
    has_evidence = _ids_with_video_evidence(conn, pool_ids)
    has_fit = _ids_with_existing_fit(conn, pool_ids)
    already_queued = _already_queued_ids(conn, sid, pool_ids)

    enqueued: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    exposure: list[dict[str, Any]] = []

    for cand in candidates:
        kid = _int(cand.get("kol_pool_id"))
        # content_fit confidence 取已 ready 的(若有);否则 exposure 用基线(不含 fit 因子)。
        fit_conf: float | None = None
        if kid in has_fit:
            try:
                from app.domains.kol import content_fit_analysis as _cfa

                cached = _cfa.get_content_fit(kid)
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
        if kid in already_queued:
            skipped.append({"kol_pool_id": kid, "reason": "already_queued_in_session"})
            continue

        payload = {
            "target_type": "kol",
            "target_id": str(kid),
            "derive_method": CONTENT_FIT_DERIVE_METHOD,
            "search_session_id": sid,
            "kol_pool_id": kid,
            "product_sku": str(product_sku or ""),
            "handle": (cand.get("payload") or {}).get("handle") if isinstance(cand.get("payload"), dict) else None,
            "platform": (cand.get("payload") or {}).get("platform") if isinstance(cand.get("payload"), dict) else None,
            "prompt": f"content fit analysis · kol:{kid}",
            "summary": f"内容契合深析 · KOL {kid}",
            "triggered_by_user_id": triggered_by_user_id,
            "viltrox_fit_score_untouched": True,
        }
        try:
            row = conn.execute(
                """
                INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
                VALUES (?, ?::jsonb, 'queued', NOW(), NOW())
                RETURNING id
                """,
                (CONTENT_FIT_JOB_TYPE, search_sessions._json_dumps(payload)),
            ).fetchone()
            conn.commit()
            enqueued.append({"kol_pool_id": kid, "job_id": (dict(row).get("id") if row else None)})
        except Exception as exc:
            logger.warning("vkpi.content_fit_enqueue.insert_failed kid=%s err=%s", kid, exc, exc_info=True)
            skipped.append({"kol_pool_id": kid, "reason": "enqueue_failed", "error": str(exc)[:300]})

    return {
        "status": "queued" if enqueued else "nothing_to_queue",
        "session_id": sid,
        "top_n": safe_top_n,
        "candidate_count": len(candidates),
        "enqueued": enqueued,
        "enqueued_count": len(enqueued),
        "skipped": skipped,
        "exposure_potential": exposure,
        "job_type": CONTENT_FIT_JOB_TYPE,
        "write_db": bool(enqueued),
        "writes": ["apify_jobs"] if enqueued else [],
        "viltrox_fit_score_untouched": True,
    }


__all__ = ["enqueue_content_fit_for_session", "CONTENT_FIT_JOB_TYPE", "DEFAULT_TOP_N"]

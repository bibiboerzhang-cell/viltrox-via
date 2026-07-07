"""影子评测框架(shadow_eval v1)—— 「挑战者赢旧版才上线」的评测发动机(L 轨道)。

通用框架:
  - EVALS 注册表:每个评测 = {label, description, challenger, baseline, runner};
  - list_shadow_evals() → 评测元数据列表(不带 runner,可直接下发前端);
  - run_shadow_eval(eval_name) → 跑一轮,输出 challenger vs baseline 同指标对比
    + verdict(双赢才建议上线)+ 全量证据;未注册评测抛 LookupError(路由转 404)。

第一个真实评测 forecast_backtest(留一回测,零 LLM、零新采集、零写库):
  样本      有真实播放数的 final_v1 深析视频(ORDER BY evidence_id ASC 决定性遍历,
            上限 MAX_SAMPLES=200 防慢;禁止任何随机抽样);
  挑战者    evidence_quantile_v1(第4轮 performance_forecast 的分位数口径):对每个样本,
            用「该 KOL 排除此视频后的历史播放」算 p10/p50/p90(复用 performance_forecast
            ._quantile 线性插值,保证与线上口径逐位一致);命中=实际播放落在 p10~p90 带内;
            误差=|实际-p50|/实际;历史(除此视频)有播放样本 <MIN_HISTORY=3 的样本诚实跳过;
  对照组    global_median_v0:对同一批被评样本,用「全部样本实际播放排除自身」的全局
            p10/p50/p90(留一全局中位数点预测 + 全局带),同口径算带内率与中位相对误差;
  判定      verdict:challenger 带内率≥baseline 且误差≤baseline、并至少一项严格更优
            → challenger_wins(建议上线);反向双输 → baseline_wins;各赢一半 → mixed;
            只有 challenger_wins 才建议切换,其余一律维持旧版。

决定性保证:样本排序/跳过规则/分位数算法全部确定;结果带 fingerprint
(除 generated_at 外全量 payload 的 sha256),两次运行 fingerprint 必须一致。

诚实态:performance_forecast 模块不可用 → status="unavailable";可评样本为 0 →
status="empty" + reason;跳过数/口径全部写进 samples 与 note,绝不编数。

数据源(全只读):vkpi_analysis_cache(derive_method='video_analysis_final_v1',
status='ready') / vkpi_kol_video_evidence / vkpi_kol_pool。

compat 约定:SQL 占位符用 ?;SQL 字符串零字面 percent(不用 LIKE);BOOLEAN 读回
可能是 int 1/0,判断走 _truthy;函数内懒 import get_conn。
红线(永久):本评测只读聚合、绝不写库;影子对比只输出建议,绝不自动切换线上规则;
绝不回写 fit 评分存储列、不碰 rule_v0;零 LLM 调用。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
METHOD = "shadow_eval_v1"

MAX_SAMPLES = 200      # 样本上限(防慢;ORDER BY evidence_id ASC 决定性截断)
SCAN_LIMIT = 1000      # 候选扫描行数上限(Python 侧再过滤 is_active / 播放数)
MIN_HISTORY = 3        # 留一后历史有播放样本 <3 → 分位数不可靠,该样本诚实跳过
_IN_CHUNK = 100        # IN 子句分块大小(占位符逐个 ?,零字面 percent)


# ── 守卫 import(拿不到就诚实降级,不硬猜)────────────────────────────


def _forecast_tools() -> Any:
    """第4轮预测战绩模块(_quantile 线性插值分位数,与线上口径逐位一致)。"""
    try:
        from app.domains.kol import performance_forecast

        return performance_forecast
    except ImportError:
        return None


# ── 小工具(与 signature_profile / performance_forecast 同款容错约定)──


def _text(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


from app.core.coerce import _truthy


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round4(value: float | None) -> float | None:
    return round(float(value), 4) if value is not None else None


def _fingerprint(payload: dict[str, Any]) -> str:
    """除 generated_at 外全量 payload 的 sha256 —— 两次运行必须一致(决定性证明)。"""
    body = {k: v for k, v in payload.items() if k != "generated_at"}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── 数据装载(全只读;查询风格抄 signature_profile)───────────────────


def _load_sample_candidates(conn: Any) -> list[dict[str, Any]]:
    """候选样本:final_v1 ready 深析 × evidence,按 evidence_id ASC 决定性遍历。"""
    rows = conn.execute(
        """
        SELECT
          e.id AS evidence_id,
          e.kol_pool_id,
          COALESCE(e.video_title, e.title, '') AS title,
          e.platform,
          e.view_count,
          e.is_active
        FROM vkpi_analysis_cache ac
        JOIN vkpi_kol_video_evidence e ON e.id::text = ac.target_id
        WHERE ac.target_type = 'video'
          AND ac.derive_method = ?
          AND ac.status = 'ready'
        ORDER BY e.id ASC
        LIMIT ?
        """,
        (FINAL_V1_DERIVE_METHOD, int(SCAN_LIMIT)),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_histories(conn: Any, kol_ids: list[int]) -> dict[int, list[tuple[int, float]]]:
    """各 KOL 有效且有播放的 evidence 历史 {kol_pool_id: [(evidence_id, views), ...]}。"""
    histories: dict[int, list[tuple[int, float]]] = {}
    for start in range(0, len(kol_ids), _IN_CHUNK):
        chunk = kol_ids[start : start + _IN_CHUNK]
        placeholders = ", ".join(["?"] * len(chunk))
        rows = conn.execute(
            "SELECT kol_pool_id, id AS evidence_id, view_count, is_active "
            "FROM vkpi_kol_video_evidence "
            "WHERE kol_pool_id IN (" + placeholders + ") "
            "ORDER BY kol_pool_id ASC, id ASC",
            tuple(int(k) for k in chunk),
        ).fetchall()
        for row in rows:
            data = dict(row)
            if not _truthy(data.get("is_active")):
                continue
            views = _int_or_none(data.get("view_count"))
            kol_id = _int_or_none(data.get("kol_pool_id"))
            eid = _int_or_none(data.get("evidence_id"))
            if views is None or views <= 0 or kol_id is None or eid is None:
                continue
            histories.setdefault(kol_id, []).append((eid, float(views)))
    return histories


def _load_handles(conn: Any, kol_ids: list[int]) -> dict[int, str]:
    handles: dict[int, str] = {}
    for start in range(0, len(kol_ids), _IN_CHUNK):
        chunk = kol_ids[start : start + _IN_CHUNK]
        placeholders = ", ".join(["?"] * len(chunk))
        rows = conn.execute(
            "SELECT id, handle, display_name FROM vkpi_kol_pool WHERE id IN (" + placeholders + ")",
            tuple(int(k) for k in chunk),
        ).fetchall()
        for row in rows:
            data = dict(row)
            kol_id = _int_or_none(data.get("id"))
            if kol_id is None:
                continue
            handles[kol_id] = _text(data.get("handle"), 100) or _text(data.get("display_name"), 100)
    return handles


# ── forecast_backtest:留一回测(挑战者 vs 全局中位数对照组)────────────


def _band_metrics(records: list[dict[str, Any]], which: str, pf: Any) -> dict[str, Any]:
    """同指标聚合:带内率 + 中位相对误差(records 里 which ∈ {challenger, baseline})。"""
    hits = sum(1 for r in records if r[which]["hit"])
    errors = sorted(r[which]["rel_error"] for r in records)
    median_err = pf._quantile(errors, 0.5) if errors else None
    return {
        "band_hit_rate": _round4(hits / len(records)) if records else None,
        "hits": hits,
        "evaluated": len(records),
        "median_rel_error": _round4(median_err),
    }


def _run_forecast_backtest(*, conn: Any = None) -> dict[str, Any]:
    """留一回测:挑战者(分位数法)vs 对照组(全局中位数),双赢才建议上线。"""
    from app.db.connection import get_conn

    pf = _forecast_tools()
    if pf is None:
        return {
            "status": "unavailable",
            "eval": "forecast_backtest",
            "reason": "performance_forecast 模块不可用,分位数口径无从复算 — 本轮评测不出结论。",
            "generated_at": _utcnow_iso(),
        }

    db = conn or get_conn()
    candidates = _load_sample_candidates(db)

    # Python 侧过滤:is_active 宽容判真 + 必须有真实播放数;按 evidence_id 去重(cache 可能多行)。
    samples: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in candidates:
        eid = _int_or_none(row.get("evidence_id"))
        kol_id = _int_or_none(row.get("kol_pool_id"))
        views = _int_or_none(row.get("view_count"))
        if eid is None or kol_id is None or eid in seen:
            continue
        if not _truthy(row.get("is_active")):
            continue
        if views is None or views <= 0:
            continue
        seen.add(eid)
        samples.append({
            "evidence_id": eid,
            "kol_pool_id": kol_id,
            "title": _text(row.get("title"), 160),
            "platform": _text(row.get("platform"), 30),
            "actual_views": float(views),
        })
        if len(samples) >= MAX_SAMPLES:
            break

    if not samples:
        return {
            "status": "empty",
            "eval": "forecast_backtest",
            "reason": (
                f"扫描 {len(candidates)} 条 final_v1 深析记录,没有一条同时满足"
                "「evidence 有效 + 有真实播放数」— 等抓取回填 view_count 后再评。"
            ),
            "samples": {"candidates_scanned": len(candidates), "eligible": 0},
            "generated_at": _utcnow_iso(),
        }

    kol_ids = sorted({s["kol_pool_id"] for s in samples})
    histories = _load_histories(db, kol_ids)
    handles = _load_handles(db, kol_ids)

    # 逐样本留一回测(决定性:samples 已按 evidence_id ASC 排好)。
    records: list[dict[str, Any]] = []
    skipped = 0
    all_actuals = [s["actual_views"] for s in samples]
    for idx, sample in enumerate(samples):
        actual = sample["actual_views"]
        history = [
            views for (eid, views) in histories.get(sample["kol_pool_id"], [])
            if eid != sample["evidence_id"]
        ]
        if len(history) < MIN_HISTORY:
            skipped += 1
            continue

        # 挑战者:该 KOL 留一历史的 p10/p50/p90(与线上 evidence_quantile_v1 同一函数)。
        hist_sorted = sorted(history)
        c_p10 = float(pf._quantile(hist_sorted, 0.10) or 0.0)
        c_p50 = float(pf._quantile(hist_sorted, 0.50) or 0.0)
        c_p90 = float(pf._quantile(hist_sorted, 0.90) or 0.0)

        # 对照组:全部样本实际播放「排除自身」的全局分位数(留一全局中位数点预测+全局带)。
        global_pool = sorted(all_actuals[:idx] + all_actuals[idx + 1 :])
        b_p10 = float(pf._quantile(global_pool, 0.10) or 0.0)
        b_p50 = float(pf._quantile(global_pool, 0.50) or 0.0)
        b_p90 = float(pf._quantile(global_pool, 0.90) or 0.0)

        records.append({
            "evidence_id": sample["evidence_id"],
            "kol_pool_id": sample["kol_pool_id"],
            "history_size": len(history),
            "actual_views": actual,
            "challenger": {
                "p10": round(c_p10), "p50": round(c_p50), "p90": round(c_p90),
                "hit": bool(c_p10 <= actual <= c_p90),
                "rel_error": abs(actual - c_p50) / actual,
            },
            "baseline": {
                "p10": round(b_p10), "p50": round(b_p50), "p90": round(b_p90),
                "hit": bool(b_p10 <= actual <= b_p90),
                "rel_error": abs(actual - b_p50) / actual,
            },
        })

    if not records:
        return {
            "status": "empty",
            "eval": "forecast_backtest",
            "reason": (
                f"{len(samples)} 条合格样本全部因「留一后 KOL 历史有播放样本 <{MIN_HISTORY}」被跳过,"
                "分位数回测无从谈起 — 等各 KOL 证据量长起来再评。"
            ),
            "samples": {
                "candidates_scanned": len(candidates),
                "eligible": len(samples),
                "evaluated": 0,
                "skipped_insufficient_history": skipped,
            },
            "generated_at": _utcnow_iso(),
        }

    challenger = _band_metrics(records, "challenger", pf)
    baseline = _band_metrics(records, "baseline", pf)

    # ── verdict:带内率与误差双赢(≥/≤ 且至少一项严格更优)才建议上线 ──
    c_hit, b_hit = challenger["band_hit_rate"] or 0.0, baseline["band_hit_rate"] or 0.0
    c_err = challenger["median_rel_error"] if challenger["median_rel_error"] is not None else float("inf")
    b_err = baseline["median_rel_error"] if baseline["median_rel_error"] is not None else float("inf")
    hit_better, hit_worse = c_hit > b_hit, c_hit < b_hit
    err_better, err_worse = c_err < b_err, c_err > b_err
    if not hit_worse and not err_worse and (hit_better or err_better):
        verdict = "challenger_wins"
        recommendation = "建议上线:分位数法带内率与中位相对误差双赢全局中位数对照组(影子结论,仍需人工确认切换)。"
    elif not hit_better and not err_better and (hit_worse or err_worse):
        verdict = "baseline_wins"
        recommendation = "维持旧版:对照组两项指标均不差于挑战者,分位数法没有拿出胜绩。"
    else:
        verdict = "mixed"
        recommendation = "维持旧版:两项指标各有胜负,未达「双赢才上线」门槛 — 不建议切换。"

    # 分 KOL 明细(决定性排序:样本数降序 → kol_pool_id 升序)。
    by_kol: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        by_kol.setdefault(record["kol_pool_id"], []).append(record)
    per_kol: list[dict[str, Any]] = []
    for kol_id, recs in by_kol.items():
        c_errors = sorted(r["challenger"]["rel_error"] for r in recs)
        b_errors = sorted(r["baseline"]["rel_error"] for r in recs)
        per_kol.append({
            "kol_pool_id": kol_id,
            "handle": handles.get(kol_id) or None,
            "sample_count": len(recs),
            "challenger_hits": sum(1 for r in recs if r["challenger"]["hit"]),
            "baseline_hits": sum(1 for r in recs if r["baseline"]["hit"]),
            "challenger_band_hit_rate": _round4(sum(1 for r in recs if r["challenger"]["hit"]) / len(recs)),
            "baseline_band_hit_rate": _round4(sum(1 for r in recs if r["baseline"]["hit"]) / len(recs)),
            "challenger_median_rel_error": _round4(pf._quantile(c_errors, 0.5)),
            "baseline_median_rel_error": _round4(pf._quantile(b_errors, 0.5)),
        })
    per_kol.sort(key=lambda item: (-item["sample_count"], item["kol_pool_id"]))

    result: dict[str, Any] = {
        "status": "ready",
        "eval": "forecast_backtest",
        "method": "loo_backtest_v1",
        "verdict": verdict,
        "recommendation": recommendation,
        "challenger": {"name": "evidence_quantile_v1(分 KOL 留一 p10/p50/p90)", **challenger},
        "baseline": {"name": "global_median_v0(全局留一中位数点预测 + 全局 p10~p90 带)", **baseline},
        "evidence": {
            "band_hit_rate_delta": _round4(c_hit - b_hit),
            "median_rel_error_delta": _round4(
                (c_err - b_err) if c_err != float("inf") and b_err != float("inf") else None
            ),
            "win_rule": "挑战者带内率≥对照组 且 中位相对误差≤对照组,且至少一项严格更优,才判 challenger_wins。",
        },
        "samples": {
            "candidates_scanned": len(candidates),
            "eligible": len(samples),
            "evaluated": len(records),
            "skipped_insufficient_history": skipped,
            "kol_count": len(per_kol),
            "cap": MAX_SAMPLES,
            "min_history": MIN_HISTORY,
            "ordering": "evidence_id ASC(决定性遍历,禁止随机抽样)",
        },
        "per_kol": per_kol,
        "note": (
            "留一回测:命中=实际播放落在预测 p10~p90 带内;误差=|实际-p50|/实际;"
            "挑战者与线上 evidence_quantile_v1 共用同一分位数函数;影子结论只建议、绝不自动切换线上规则。"
        ),
        "generated_at": _utcnow_iso(),
    }
    result["fingerprint"] = _fingerprint(result)
    return result


# ── 注册表 + 通用入口 ────────────────────────────────────────────────

EVALS: dict[str, dict[str, Any]] = {
    "forecast_backtest": {
        "label": "预测战绩留一回测",
        "description": (
            "对有真实播放数的 final_v1 深析视频做留一回测:分位数预测法(evidence_quantile_v1)"
            "vs 全局中位数对照组,输出带内率/中位相对误差/分 KOL 明细 — 双赢才建议上线。"
        ),
        "challenger": "evidence_quantile_v1",
        "baseline": "global_median_v0",
        "runner": _run_forecast_backtest,
    },
}


def list_shadow_evals() -> list[dict[str, Any]]:
    """注册表元数据(不带 runner,可直接下发前端);顺序=注册顺序,决定性。"""
    return [
        {
            "name": name,
            "label": str(meta.get("label") or name),
            "description": str(meta.get("description") or ""),
            "challenger": str(meta.get("challenger") or ""),
            "baseline": str(meta.get("baseline") or ""),
        }
        for name, meta in EVALS.items()
    ]


def run_shadow_eval(eval_name: str, *, conn: Any = None) -> dict[str, Any]:
    """跑一轮指定影子评测;未注册抛 LookupError(路由转 404)。全只读,零写库。"""
    meta = EVALS.get(str(eval_name or "").strip())
    if meta is None:
        raise LookupError(f"shadow eval '{eval_name}' not registered")
    runner: Callable[..., dict[str, Any]] = meta["runner"]
    return runner(conn=conn)

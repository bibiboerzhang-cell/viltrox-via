"""异常哨兵(零 LLM)—— 四路原料统一探测,写 vkpi_alerts(alert_key 幂等)。

四路原料(全部只读既有真实表,阈值 env 化,样本不足诚实不报):
  ① 追踪中的 KOL 视频(vkpi_kol_video_metric_tracking.status='active')
     用 vkpi_content_metric_snapshots 的日序列:最近一日增量 vs 前 7 日日增量中位数,
     偏离超过 K 个 MAD(默认 3)→ 「某 KOL 某视频异常爆/衰」。
  ② 官号逐帖(vkpi_channel_post_metrics,snapshot_date 逐日)同法。
  ③ 预测残差漂移:vkpi_prediction_evals.error_abs 近 7 日 vs 前 7 日的 PSI
     (参照分位等频分箱 + 平滑,扣除抽样噪声底)超阈(默认 0.2)→ 模型漂移告警。
  ④ 管道故障聚集:apify_jobs 同 last_error_category 24h 内累计 >= N(默认 20)→ 管道故障告警。

每条告警带中文 explanation(证据行 id + 数值)与 severity;写入走既有
alerts.service.upsert_alert(按 alert_key 幂等:同日同目标重复跑只更新不重复)。
可选 LLM 解释(VKPI_ALERT_EXPLAIN_LLM=1 才开;日上限 30;scope=agent_alert_explain;
预算闸拒绝即回退规则解释)。默认纯规则解释。

compat 约定:占位符 ?;SQL 零 LIKE/百分号;get_conn() 不用 with;时间戳读回双态容错
(PG datetime / sqlite 文本)。红线:零触 viltrox_fit_score / rule_v0;只写 vkpi_alerts。
"""
from __future__ import annotations

import json
import math
import os
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

# ── 阈值 env(缺省保守)──
ENV_MAD_K = "VKPI_ANOMALY_MAD_K"                      # 偏离阈(MAD 倍数),默认 3
ENV_MIN_BASELINE = "VKPI_ANOMALY_MIN_BASELINE_POINTS"  # 基线最少日增量点数,默认 4
ENV_MIN_ABS_DELTA = "VKPI_ANOMALY_MIN_ABS_DELTA"        # 偏离的绝对下限(防死视频 +5 也报),默认 100
ENV_BASELINE_DAYS = "VKPI_ANOMALY_BASELINE_DAYS"        # 基线窗口天数,默认 7
ENV_PIPELINE_FAIL_N = "VKPI_ANOMALY_PIPELINE_FAIL_N"    # 同类失败聚集阈,默认 20
ENV_PSI_THRESHOLD = "VKPI_ANOMALY_PSI_THRESHOLD"        # PSI 阈,默认 0.2
ENV_PSI_MIN_SAMPLE = "VKPI_ANOMALY_PSI_MIN_SAMPLE"      # 两窗各至少样本数,默认 50
ENV_PSI_BINS = "VKPI_ANOMALY_PSI_BINS"                  # PSI 分箱数,默认 5(小样本少箱降噪)
ENV_EXPLAIN_LLM = "VKPI_ALERT_EXPLAIN_LLM"              # 1 才开 LLM 解释,默认关
ENV_EXPLAIN_LLM_DAILY_MAX = "VKPI_ALERT_EXPLAIN_LLM_DAILY_MAX"  # 日上限,默认 30
ENV_EXPLAIN_EST_USD = "VKPI_ALERT_EXPLAIN_EST_USD"      # 单条解释预估成本(预算闸用),默认 0.002

RULE_VIDEO = "anomaly.video_metric_mad"
RULE_CHANNEL_POST = "anomaly.channel_post_mad"
RULE_PSI = "anomaly.prediction_residual_psi"
RULE_PIPELINE = "anomaly.pipeline_failure_cluster"
ALL_RULES: tuple[str, ...] = (RULE_VIDEO, RULE_CHANNEL_POST, RULE_PSI, RULE_PIPELINE)

EXPLAIN_SCOPE = "agent_alert_explain"   # 预算闸 scope(vkpi_provider_budget_caps,迁移 292 种子)
EXPLAIN_PURPOSE = "agent_alert_explain"

_MAD_TO_SIGMA = 1.4826
_MAX_SERIES_ROWS = 60000


# ── 小工具 ──────────────────────────────────────────────────────────────


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in ("1", "true", "yes", "on")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_day(value: Any) -> date | None:
    """PG datetime/date 或 sqlite 文本 → UTC 日期;解析不了诚实 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date()


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _label(value: Any, limit: int = 60) -> str:
    """标题/文案用:折叠换行与多空格,截断。"""
    return " ".join(str(value or "").split())[:limit]


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+,.0f}" if abs(value) >= 10 else f"{value:+.2f}"


# ── 纯函数:日序列 → 日增量 → 稳健 z(MAD) ─────────────────────────────


def daily_deltas(points: list[tuple[Any, Any, Any]]) -> list[dict[str, Any]]:
    """[(ts/date, value, row_id)] → 按 UTC 日取当日最后一点,相邻日做日均增量。

    返回按日升序的 [{day, delta, value, prev_value, row_id, prev_row_id, gap_days}];
    缺值/解析失败的点剔除;不足两日 → 空。
    """
    per_day: dict[date, tuple[float, Any]] = {}
    for ts, value, row_id in points:
        day = _to_day(ts)
        v = _num(value)
        if day is None or v is None:
            continue
        per_day[day] = (v, row_id)  # 输入已按时间升序,后者覆盖前者 = 当日最后一点
    days = sorted(per_day)
    out: list[dict[str, Any]] = []
    for prev_day, day in zip(days, days[1:]):
        gap = max(1, (day - prev_day).days)
        v, rid = per_day[day]
        pv, prid = per_day[prev_day]
        out.append({
            "day": day, "delta": (v - pv) / gap, "value": v, "prev_value": pv,
            "row_id": rid, "prev_row_id": prid, "gap_days": gap,
        })
    return out


def robust_z(latest: float, baseline: list[float]) -> dict[str, Any]:
    """稳健 z:(latest - median) / (1.4826 * MAD);MAD=0 时按 max(1, 10% 中位数) 兜底尺度。"""
    median = statistics.median(baseline)
    mad = statistics.median([abs(x - median) for x in baseline])
    scale = _MAD_TO_SIGMA * mad
    if scale <= 0:
        scale = max(1.0, abs(median) * 0.1)
    return {"median": median, "mad": mad, "scale": scale, "z": (latest - median) / scale}


def detect_mad_anomaly(
    points: list[tuple[Any, Any, Any]],
    *,
    k: float,
    min_baseline: int,
    min_abs_delta: float,
    baseline_days: int,
    today: date | None = None,
) -> dict[str, Any]:
    """单条序列的 MAD 突变判定。返回 {status: anomaly|normal|insufficient|stale, ...}。

    - 最新一对相邻日为「24h 增量」;其前 baseline_days 内的日增量为基线;
    - 基线点数 < min_baseline → insufficient(诚实不报);最新日早于 2 天前 → stale;
    - |z| >= k 且 |latest - median| >= min_abs_delta → anomaly(direction spike/drop)。
    """
    series = daily_deltas(points)
    if len(series) < 2:
        return {"status": "insufficient", "reason": "不足两日快照", "points": len(series)}
    latest = series[-1]
    today_d = today or _now().date()
    if (today_d - latest["day"]).days > 2:
        return {"status": "stale", "reason": f"最新快照日 {latest['day']} 距今超 2 天", "points": len(series)}
    floor_day = latest["day"] - timedelta(days=baseline_days)
    baseline = [p["delta"] for p in series[:-1] if p["day"] > floor_day]
    if len(baseline) < min_baseline:
        return {"status": "insufficient", "reason": f"基线日增量点数 {len(baseline)} < {min_baseline}",
                "points": len(series), "baseline_n": len(baseline)}
    stats = robust_z(latest["delta"], baseline)
    deviation = latest["delta"] - stats["median"]
    anomalous = abs(stats["z"]) >= k and abs(deviation) >= min_abs_delta
    return {
        "status": "anomaly" if anomalous else "normal",
        "direction": "spike" if deviation > 0 else "drop",
        "latest": latest, "baseline_n": len(baseline), "k": k, **stats,
    }


def _severity_from_z(z: float) -> str:
    return "critical" if abs(z) >= 6 else "warning"


# ── 四路探测器(各返回 findings 列表;finding = 未落库的告警草稿)────────────


def _finding(*, alert_key: str, rule_key: str, severity: str, title: str, explanation: str,
             target_type: str, target_id: int | None, evidence_ids: list[Any],
             metrics: dict[str, Any], staff_id: int | None = None) -> dict[str, Any]:
    return {
        "alert_key": alert_key, "rule_key": rule_key, "severity": severity, "title": title,
        "explanation": explanation, "target_type": target_type, "target_id": target_id,
        "staff_id": staff_id, "evidence_ids": [str(x) for x in evidence_ids if x is not None],
        "metrics": metrics,
    }


def _thresholds() -> dict[str, Any]:
    return {
        "k": _env_float(ENV_MAD_K, 3.0),
        "min_baseline": max(2, _env_int(ENV_MIN_BASELINE, 4)),
        "min_abs_delta": _env_float(ENV_MIN_ABS_DELTA, 100.0),
        "baseline_days": max(2, _env_int(ENV_BASELINE_DAYS, 7)),
    }


def _mad_explain(label: str, verdict: dict[str, Any], metric_name: str) -> str:
    latest = verdict["latest"]
    direction = "异常爆量" if verdict["direction"] == "spike" else "异常衰减"
    return (
        f"{label} 最近一日{metric_name}增量 {_fmt(latest['delta'])}"
        f"({latest['prev_value']:,.0f} → {latest['value']:,.0f},{latest['day']}),"
        f"前 {verdict['baseline_n']} 日日增量中位数 {_fmt(verdict['median'])}(MAD {verdict['mad']:,.1f}),"
        f"偏离 {verdict['z']:+.1f} 个 MAD(阈 ±{verdict['k']:g})→ {direction}。"
        f"证据:快照行 id {latest['prev_row_id']} → {latest['row_id']}。"
    )


def detect_tracked_video_anomalies(*, today: date | None = None) -> dict[str, Any]:
    """① 追踪中的 KOL 视频:vkpi_content_metric_snapshots(success)日序列 MAD 突变。"""
    out: dict[str, Any] = {"checked": 0, "findings": [], "skipped": {}}
    needed = ("vkpi_kol_video_metric_tracking", "vkpi_content_metric_snapshots", "vkpi_kol_video_evidence")
    if not all(table_exists(t) for t in needed):
        out["reason"] = "tables_missing"
        return out
    th = _thresholds()
    since = _iso(_now() - timedelta(days=th["baseline_days"] + 3))
    rows = get_conn().execute(
        """
        SELECT s.id AS snap_id, s.evidence_id, s.fetched_at, s.views, s.likes,
               e.kol_pool_id, e.video_title, e.platform, p.handle, p.display_name
        FROM vkpi_content_metric_snapshots s
        JOIN vkpi_kol_video_metric_tracking t ON t.evidence_id = s.evidence_id AND t.status = 'active'
        JOIN vkpi_kol_video_evidence e ON e.id = s.evidence_id
        LEFT JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE s.status = 'success' AND s.fetched_at >= ?
        ORDER BY s.evidence_id ASC, s.fetched_at ASC, s.id ASC
        LIMIT ?
        """,
        (since, _MAX_SERIES_ROWS),
    ).fetchall()
    groups: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        groups.setdefault(int(item["evidence_id"]), []).append(item)
    for evidence_id, items in groups.items():
        out["checked"] += 1
        # 主指标 views;全空则退 likes(部分平台只回赞数)。
        metric = "views" if any(_num(i.get("views")) is not None for i in items) else "likes"
        points = [(i["fetched_at"], i.get(metric), i["snap_id"]) for i in items]
        verdict = detect_mad_anomaly(points, today=today, **th)
        if verdict["status"] != "anomaly":
            out["skipped"][verdict["status"]] = out["skipped"].get(verdict["status"], 0) + 1
            continue
        head = items[-1]
        handle = head.get("handle") or head.get("display_name") or f"kol_pool#{head.get('kol_pool_id')}"
        title = _label(head.get("video_title")) or f"evidence#{evidence_id}"
        label = f"KOL @{handle} 的视频《{title}》"
        metric_name = "播放" if metric == "views" else "点赞"
        day = verdict["latest"]["day"].strftime("%Y%m%d")
        out["findings"].append(_finding(
            alert_key=f"anomaly-video-{evidence_id}-{day}", rule_key=RULE_VIDEO,
            severity=_severity_from_z(verdict["z"]),
            title=f"视频{'异常爆量' if verdict['direction'] == 'spike' else '异常衰减'}:@{handle}《{title}》",
            explanation=_mad_explain(label, verdict, metric_name),
            target_type="kol_video_evidence", target_id=evidence_id,
            evidence_ids=[verdict["latest"]["prev_row_id"], verdict["latest"]["row_id"]],
            metrics={"metric": metric, "latest_delta": verdict["latest"]["delta"], "median": verdict["median"],
                     "mad": verdict["mad"], "z": verdict["z"], "direction": verdict["direction"],
                     "baseline_n": verdict["baseline_n"], "kol_pool_id": head.get("kol_pool_id"),
                     "platform": head.get("platform")},
        ))
    return out


def detect_channel_post_anomalies(*, today: date | None = None) -> dict[str, Any]:
    """② 官号逐帖:vkpi_channel_post_metrics 按 snapshot_date 的日序列 MAD 突变。"""
    out: dict[str, Any] = {"checked": 0, "findings": [], "skipped": {}}
    if not table_exists("vkpi_channel_post_metrics"):
        out["reason"] = "tables_missing"
        return out
    th = _thresholds()
    since_day = (_now() - timedelta(days=th["baseline_days"] + 3)).strftime("%Y-%m-%d")
    has_channels = table_exists("vkpi_employee_channels")
    join_sql = "LEFT JOIN vkpi_employee_channels c ON c.id = m.channel_id" if has_channels else ""
    handle_sql = "c.account_handle AS account_handle, c.staff_id AS staff_id," if has_channels else "'' AS account_handle, NULL AS staff_id,"
    rows = get_conn().execute(
        f"""
        SELECT m.id AS row_id, m.channel_id, m.post_uid, m.platform, m.title, m.snapshot_date, m.views,
               {handle_sql} m.post_url
        FROM vkpi_channel_post_metrics m
        {join_sql}
        WHERE m.snapshot_date >= ?
        ORDER BY m.channel_id ASC, m.post_uid ASC, m.snapshot_date ASC, m.captured_at ASC, m.id ASC
        LIMIT ?
        """,
        (since_day, _MAX_SERIES_ROWS),
    ).fetchall()
    groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        groups.setdefault((int(item["channel_id"]), str(item["post_uid"])), []).append(item)
    for (channel_id, post_uid), items in groups.items():
        out["checked"] += 1
        points = [(i["snapshot_date"], i.get("views"), i["row_id"]) for i in items]
        verdict = detect_mad_anomaly(points, today=today, **th)
        if verdict["status"] != "anomaly":
            out["skipped"][verdict["status"]] = out["skipped"].get(verdict["status"], 0) + 1
            continue
        head = items[-1]
        handle = head.get("account_handle") or f"channel#{channel_id}"
        title = _label(head.get("title")) or post_uid
        label = f"官号 @{handle}({head.get('platform') or '-'})帖子《{title}》"
        day = verdict["latest"]["day"].strftime("%Y%m%d")
        out["findings"].append(_finding(
            alert_key=f"anomaly-post-{channel_id}-{post_uid}-{day}", rule_key=RULE_CHANNEL_POST,
            severity=_severity_from_z(verdict["z"]),
            title=f"官号帖子{'异常爆量' if verdict['direction'] == 'spike' else '异常衰减'}:@{handle}《{title}》",
            explanation=_mad_explain(label, verdict, "播放"),
            target_type="channel_post", target_id=int(head["row_id"]),
            evidence_ids=[verdict["latest"]["prev_row_id"], verdict["latest"]["row_id"]],
            metrics={"metric": "views", "latest_delta": verdict["latest"]["delta"], "median": verdict["median"],
                     "mad": verdict["mad"], "z": verdict["z"], "direction": verdict["direction"],
                     "baseline_n": verdict["baseline_n"], "channel_id": channel_id, "post_uid": post_uid,
                     "platform": head.get("platform"), "post_url": head.get("post_url")},
            staff_id=(int(head["staff_id"]) if head.get("staff_id") not in (None, "", 0) else None),
        ))
    return out


def psi_quantile(reference: list[float], current: list[float], bins: int = 5) -> float | None:
    """参照分位等频分箱 + 加 0.5 平滑的 PSI(小样本稳健版)。

    不复用 drift_monitor.psi:它按合并值域等宽分箱且空箱用 1e-6 兜底,同分布 60 条样本实测
    PSI 0.1 到 0.9 乱跳(尾箱空 → log 项爆),哨兵用它会把抽样噪声当漂移报。等频分箱保证参照侧
    每箱约 1/bins 质量,平滑避免 log(0),同分布下 PSI 期望近似卡方 (bins-1)*(1/n_ref+1/n_cur)。
    """
    ref = sorted(float(x) for x in reference)
    cur = [float(x) for x in current]
    if not ref or not cur:
        return None
    bins = max(2, min(int(bins), 50))
    n = len(ref)
    edges = [ref[min(n - 1, int(n * i / bins))] for i in range(1, bins)]

    def _pct(values: list[float]) -> list[float]:
        counts = [0] * bins
        for v in values:
            idx = 0
            while idx < len(edges) and v > edges[idx]:
                idx += 1
            counts[idx] += 1
        total = len(values) + 0.5 * bins
        return [(c + 0.5) / total for c in counts]

    score = 0.0
    for r_pct, c_pct in zip(_pct(ref), _pct(cur)):
        score += (c_pct - r_pct) * math.log(c_pct / r_pct)
    return round(score, 6)


def psi_noise_floor(n_ref: int, n_cur: int, bins: int) -> float:
    """同分布抽样下 PSI 的噪声上界(约 p95):2 * 卡方期望 (bins-1)*(1/n_ref+1/n_cur)。
    小样本 PSI 天然偏高,只有「PSI - 噪声底」仍超阈才算漂移,避免把抽样噪声当漂移报。"""
    if n_ref <= 0 or n_cur <= 0:
        return 0.0
    return 2.0 * (max(2, bins) - 1) * (1.0 / n_ref + 1.0 / n_cur)


def detect_prediction_drift(*, today: date | None = None) -> dict[str, Any]:
    """③ 预测残差 PSI:vkpi_prediction_evals.error_abs 近 7 日 vs 前 7 日。"""
    out: dict[str, Any] = {"checked": 0, "findings": [], "skipped": {}}
    if not table_exists("vkpi_prediction_evals"):
        out["reason"] = "tables_missing"
        return out
    threshold = _env_float(ENV_PSI_THRESHOLD, 0.2)
    min_sample = max(5, _env_int(ENV_PSI_MIN_SAMPLE, 50))
    bins = max(2, min(50, _env_int(ENV_PSI_BINS, 5)))
    now = _now()
    current_cut = _iso(now - timedelta(days=7))
    reference_cut = _iso(now - timedelta(days=14))
    conn = get_conn()
    cur_rows = conn.execute(
        "SELECT id, error_abs FROM vkpi_prediction_evals WHERE evaluated_at >= ? AND error_abs IS NOT NULL ORDER BY id DESC LIMIT 5000",
        (current_cut,),
    ).fetchall()
    ref_rows = conn.execute(
        "SELECT id, error_abs FROM vkpi_prediction_evals WHERE evaluated_at >= ? AND evaluated_at < ? AND error_abs IS NOT NULL ORDER BY id DESC LIMIT 5000",
        (reference_cut, current_cut),
    ).fetchall()
    cur = [(int(dict(r)["id"]), _num(dict(r)["error_abs"])) for r in cur_rows]
    ref = [(int(dict(r)["id"]), _num(dict(r)["error_abs"])) for r in ref_rows]
    cur = [(i, v) for i, v in cur if v is not None]
    ref = [(i, v) for i, v in ref if v is not None]
    out["checked"] = 1
    out["current_n"], out["reference_n"] = len(cur), len(ref)
    if len(cur) < min_sample or len(ref) < min_sample:
        out["skipped"]["insufficient"] = 1
        out["reason"] = f"两窗样本 {len(ref)}/{len(cur)} 不足 {min_sample}"
        return out
    score = psi_quantile([v for _, v in ref], [v for _, v in cur], bins)
    floor = psi_noise_floor(len(ref), len(cur), bins)
    out["psi"], out["psi_noise_floor"] = score, round(floor, 4)
    if score is None or (score - floor) < threshold:
        out["skipped"]["normal"] = 1
        return out
    day = (today or now.date()).strftime("%Y%m%d")
    ref_ids = (min(i for i, _ in ref), max(i for i, _ in ref))
    cur_ids = (min(i for i, _ in cur), max(i for i, _ in cur))
    ref_med = statistics.median(v for _, v in ref)
    cur_med = statistics.median(v for _, v in cur)
    out["findings"].append(_finding(
        alert_key=f"anomaly-psi-prediction-evals-{day}", rule_key=RULE_PSI,
        severity="critical" if (score - floor) >= 0.35 else "warning",
        title=f"预测残差分布漂移:PSI {score:.3f} 超阈 {threshold:g}",
        explanation=(
            f"vkpi_prediction_evals 残差(error_abs)近 7 日 {len(cur)} 条 vs 前 7 日 {len(ref)} 条,"
            f"PSI {score:.3f}(抽样噪声底 {floor:.3f},净 {score - floor:.3f} ≥ 阈 {threshold:g},{bins} 箱);"
            f"残差中位数 {ref_med:.3f} → {cur_med:.3f}。"
            f"证据:参照期评估行 id {ref_ids[0]}-{ref_ids[1]},当前期 id {cur_ids[0]}-{cur_ids[1]}。"
            "建议复核预测口径/特征是否变化(本哨兵只读不改模型)。"
        ),
        target_type="prediction_evals", target_id=None,
        evidence_ids=[f"{ref_ids[0]}-{ref_ids[1]}", f"{cur_ids[0]}-{cur_ids[1]}"],
        metrics={"psi": score, "psi_noise_floor": round(floor, 4), "bins": bins, "threshold": threshold,
                 "reference_n": len(ref), "current_n": len(cur),
                 "reference_median": ref_med, "current_median": cur_med},
    ))
    return out


def detect_pipeline_failure_clusters(*, today: date | None = None) -> dict[str, Any]:
    """④ 管道故障聚集:apify_jobs 同 last_error_category 24h 内 >= N。"""
    out: dict[str, Any] = {"checked": 0, "findings": [], "skipped": {}}
    if not table_exists("apify_jobs"):
        out["reason"] = "tables_missing"
        return out
    n_threshold = max(1, _env_int(ENV_PIPELINE_FAIL_N, 20))
    since = _iso(_now() - timedelta(hours=24))
    rows = get_conn().execute(
        """
        SELECT last_error_category AS category, COUNT(*) AS n, MIN(id) AS min_id, MAX(id) AS max_id,
               SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_n
        FROM apify_jobs
        WHERE last_error_category IS NOT NULL AND last_error_category <> '' AND updated_at >= ?
        GROUP BY last_error_category
        ORDER BY n DESC
        LIMIT 50
        """,
        (since,),
    ).fetchall()
    day = (today or _now().date()).strftime("%Y%m%d")
    for row in rows:
        item = dict(row)
        out["checked"] += 1
        n = int(item.get("n") or 0)
        category = str(item.get("category") or "")
        if n < n_threshold:
            out["skipped"]["below_threshold"] = out["skipped"].get("below_threshold", 0) + 1
            continue
        safe_cat = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in category)[:60]
        out["findings"].append(_finding(
            alert_key=f"anomaly-pipeline-{safe_cat}-{day}", rule_key=RULE_PIPELINE,
            severity="critical" if n >= 2 * n_threshold else "warning",
            title=f"管道故障聚集:{category} 24h 内 {n} 次",
            explanation=(
                f"apify_jobs 近 24h 同一错误类别 {category} 累计 {n} 次(阈 {n_threshold};"
                f"其中 status=failed {int(item.get('failed_n') or 0)} 次),疑似系统性故障而非偶发。"
                f"证据:任务 id 区间 {item.get('min_id')}-{item.get('max_id')}。"
                "建议先查该类别的执行体/代理/密钥,再决定是否批量重试。"
            ),
            target_type="apify_jobs", target_id=None,
            evidence_ids=[f"{item.get('min_id')}-{item.get('max_id')}"],
            metrics={"category": category, "count_24h": n, "threshold": n_threshold,
                     "failed_n": int(item.get("failed_n") or 0),
                     "min_job_id": item.get("min_id"), "max_job_id": item.get("max_id")},
        ))
    return out


DETECTORS: dict[str, Callable[..., dict[str, Any]]] = {
    "video": detect_tracked_video_anomalies,
    "channel_post": detect_channel_post_anomalies,
    "psi": detect_prediction_drift,
    "pipeline": detect_pipeline_failure_clusters,
}


# ── 可选 LLM 解释(默认关;日限额;预算闸拒绝即回退规则解释)────────────────


def _llm_explain_count_today(today_key: str) -> int:
    """今日已用 LLM 解释的条数(从 vkpi_alerts.metadata_json 读回,零新表)。"""
    try:
        rows = get_conn().execute(
            f"SELECT metadata_json FROM vkpi_alerts WHERE rule_key IN ({','.join('?' for _ in ALL_RULES)}) AND updated_at >= ?",
            (*ALL_RULES, f"{today_key}T00:00:00Z"),
        ).fetchall()
    except Exception:  # noqa: BLE001 — 读不到就当已满,宁可不烧
        logger.debug("anomaly: explain count read failed", exc_info=True)
        return 10**6
    n = 0
    for row in rows:
        try:
            meta = json.loads(str(dict(row).get("metadata_json") or "{}"))
        except (TypeError, ValueError):
            continue
        if isinstance(meta, dict) and meta.get("explain_source") == "llm" and meta.get("explain_llm_day") == today_key:
            n += 1
    return n


def _llm_explain(finding: dict[str, Any]) -> str | None:
    """经 llm_production(gemini-3.6-flash,scope=agent_alert_explain)生成一段中文解释;失败返回 None。"""
    from app.core.gemini_models import DEFAULT_VIDEO_GEMINI_MODEL
    from app.domains.costs import budget_guard
    from app.platform import llm_production

    est = _env_float(ENV_EXPLAIN_EST_USD, 0.002)
    if not budget_guard.check_budget(EXPLAIN_SCOPE, est, require_configured=True):
        return None
    prompt = (
        "你是营销数据运维助手。下面是一条由规则哨兵检出的异常告警,请用中文写 2 句话给运营看:"
        "第一句复述异常与关键数字,第二句给出最可能的原因假设与下一步核查动作。"
        "不要编造数据,不要超过 120 字。\n\n"
        f"标题:{finding['title']}\n规则解释:{finding['explanation']}\n"
        f"指标:{json.dumps(finding.get('metrics') or {}, ensure_ascii=False, default=str)[:800]}"
    )
    result = llm_production.generate_text(
        prompt, provider="google", model=DEFAULT_VIDEO_GEMINI_MODEL, purpose=EXPLAIN_PURPOSE,
        max_output_tokens=240, cost_tag=EXPLAIN_SCOPE,
        metadata={"surface": "anomaly_sentinel", "phase": "alert_explain", "rule_key": finding["rule_key"]},
    )
    text = str(result.get("text") or "").strip()
    return text[:600] or None


def _attach_explanation(finding: dict[str, Any], *, llm_budget: dict[str, Any]) -> None:
    """决定 explanation 来源:默认规则;env 开且今日额度未满才试 LLM,失败/拒绝回退规则。"""
    finding["explain_source"] = "rule"
    if not llm_budget.get("enabled") or llm_budget.get("used", 0) >= llm_budget.get("daily_max", 0):
        return
    try:
        text = _llm_explain(finding)
    except Exception:  # noqa: BLE001 — LLM 不可用绝不阻断哨兵
        logger.info("anomaly: llm explain unavailable, fallback to rule", exc_info=True)
        text = None
    if text:
        llm_budget["used"] = int(llm_budget.get("used", 0)) + 1
        finding["explain_source"] = "llm"
        finding["explanation"] = f"{text}\n\n[规则依据] {finding['explanation']}"


# ── 落库 + 入口 ─────────────────────────────────────────────────────────


def _write_finding(finding: dict[str, Any]) -> str:
    """经既有 alerts.service.upsert_alert 落 vkpi_alerts;返回 created|updated。"""
    from app.domains.alerts import service as alerts_service

    existing = get_conn().execute("SELECT id FROM vkpi_alerts WHERE alert_key=?", (finding["alert_key"],)).fetchone()
    metadata = {
        "source": "anomaly_sentinel", "explain_source": finding.get("explain_source", "rule"),
        "evidence_ids": finding["evidence_ids"], "metrics": finding["metrics"],
    }
    if finding.get("explain_source") == "llm":
        metadata["explain_llm_day"] = _now().strftime("%Y-%m-%d")
    alerts_service.upsert_alert(
        alert_key=finding["alert_key"], title=finding["title"][:200], body=finding["explanation"],
        severity=finding["severity"], target_type=finding["target_type"], target_id=finding["target_id"],
        staff_id=finding.get("staff_id"), rule_key=finding["rule_key"],
        metadata_json=json.dumps(metadata, ensure_ascii=False, default=str),
    )
    return "updated" if existing else "created"


def run_anomaly_sentinel(*, dry_run: bool = False, detectors: list[str] | None = None) -> dict[str, Any]:
    """四路统一探测 → 写 vkpi_alerts(dry_run=True 只探不写)。返回统计 dict,永不抛。"""
    started = _now()
    names = [n for n in (detectors or list(DETECTORS)) if n in DETECTORS]
    stats: dict[str, Any] = {
        "status": "ok", "dry_run": bool(dry_run), "started_at": _iso(started), "detectors": {},
        "findings_total": 0, "alerts_created": 0, "alerts_updated": 0, "explain": {"rule": 0, "llm": 0},
        "errors": [],
    }
    llm_budget = {
        "enabled": _env_flag(ENV_EXPLAIN_LLM) and not dry_run,
        "daily_max": max(0, _env_int(ENV_EXPLAIN_LLM_DAILY_MAX, 30)),
        "used": 0,
    }
    if llm_budget["enabled"]:
        llm_budget["used"] = _llm_explain_count_today(started.strftime("%Y-%m-%d"))
    for name in names:
        try:
            result = DETECTORS[name](today=started.date())
        except Exception as exc:  # noqa: BLE001 — 单路坏不拖垮其余三路
            logger.warning("anomaly sentinel detector %s failed: %s", name, exc, exc_info=True)
            stats["errors"].append({"detector": name, "error": str(exc)[:200]})
            stats["detectors"][name] = {"checked": 0, "findings": 0, "error": str(exc)[:200]}
            continue
        findings = result.get("findings") or []
        summary = {k: v for k, v in result.items() if k != "findings"}
        summary["findings"] = len(findings)
        summary["alert_keys"] = [f["alert_key"] for f in findings]
        stats["detectors"][name] = summary
        stats["findings_total"] += len(findings)
        for finding in findings:
            if dry_run:
                stats["explain"]["rule"] += 1
                continue
            _attach_explanation(finding, llm_budget=llm_budget)
            stats["explain"][finding.get("explain_source", "rule")] += 1
            try:
                outcome = _write_finding(finding)
            except Exception as exc:  # noqa: BLE001
                logger.warning("anomaly sentinel write failed key=%s: %s", finding["alert_key"], exc, exc_info=True)
                stats["errors"].append({"alert_key": finding["alert_key"], "error": str(exc)[:200]})
                continue
            stats["alerts_created" if outcome == "created" else "alerts_updated"] += 1
    stats["llm_explain_used_today"] = llm_budget["used"]
    stats["elapsed_ms"] = int((_now() - started).total_seconds() * 1000)
    if stats["errors"]:
        stats["status"] = "partial"
    return stats


__all__ = [
    "ALL_RULES", "DETECTORS", "EXPLAIN_SCOPE", "RULE_CHANNEL_POST", "RULE_PIPELINE", "RULE_PSI", "RULE_VIDEO",
    "daily_deltas", "detect_channel_post_anomalies", "detect_mad_anomaly", "detect_pipeline_failure_clusters",
    "detect_prediction_drift", "detect_tracked_video_anomalies", "psi_noise_floor", "psi_quantile", "robust_z", "run_anomaly_sentinel",
]

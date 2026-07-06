"""L 轨道 · 低命中复盘(miss_review v1)—— 「哪里没打中、为什么」,失败原因入记忆。

两个入口:
  miss_review_list()            从真实源表抽「未命中/失败」条目,按 action_type 分组,
                                词表聚类失败原因(超时/预算/无数据/解析失败/外部拒绝…),
                                低命中组(hit<0.5 且样本>=5)标 needs_review=true。
  persist_review_findings()     把每组失败原因摘要经既有 agent_memory_writer.record_signal
                                (action_kind="retrospective")写入 vkpi_agent_actions 记忆;
                                dry_run=True 只预览;幂等:同组同日已写则跳过。

分组口径(2026-07-06 真库侦察):
  alert_signal      vkpi_alerts 未闭环(status<>resolved 且 resolved_at 空,67 条);
                    命中率复用 prediction_ledger.hit_rate_for("alert_signal")(处理闭环率代理口径)。
  kol_recommend     vkpi_recommendation_outcomes 拒绝(was_rejected)+ vkpi_recommendation_feedback
                    负向反馈;侦察时两者皆 0 → 诚实空组(776 pending 是没人对答案,不算失败)。
  task:<job_type>   apify_jobs status='failed' 带 last_error(398 条),按 job_type 一组;
                    命中率=该 job_type done/(done+failed) 的执行成功率(源表自算,口径进 basis)。

失败原因聚类=词表规则(lexicon_v1,零 LLM):apify_jobs 先信 last_error_category 列,
unknown/缺失才落词表;识别不了归 unclassified,不硬贴标签;每类引原文样本(截断)。

诚实态:每组缺数据 status="empty" + reason;单组塌了不拖垮整单(status="error");
命中率判不出(status 非 ok / hit_rate=None)时 needs_review=false,绝不编数吓人。

compat 约定:SQL 占位符用 ?;SQL 字符串零字面 percent(不用 LIKE);BOOLEAN 读回
可能是 int 1/0,判断走 _truthy;函数内懒 import get_conn。

红线(永久):读端纯聚合;写只经既有 record_signal(仅学习留痕表 vkpi_agent_actions);
零 LLM、零新采集、零迁移;绝不写 viltrox_fit_score、绝不碰 rule_v0;
复盘结论只入记忆供人看,绝不自动改任何线上规则。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

METHOD = "miss_review_v1"

# needs_review 判定阈值(进 basis,可追溯)
NEEDS_REVIEW_HIT_CEILING = 0.5   # 命中率 < 0.5
NEEDS_REVIEW_MIN_SAMPLE = 5      # 且样本 >= 5(样本荒不吓人)
LEDGER_WINDOW = 50               # 复用预测台账的窗口
SCAN_LIMIT = 2000                # 每源表扫描上限(表都很小)
MAX_REASON_SAMPLES = 3           # 每类失败原因引几条原文
MAX_ITEMS_PER_GROUP = 8          # 每组带回的最近条目样本数

# 记忆写入锚(persist 幂等键的三元组;record_signal 白名单里 retrospective 最贴复盘语义)
MEMORY_ACTION_KIND = "retrospective"
MEMORY_ENTITY_TYPE = "miss_review"

# ── 失败原因词表(lexicon_v1;小写子串匹配;顺序即优先级,首中即归类)──
FAILURE_REASONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("timeout", "超时/卡死", (
        "timeout", "timed out", "stale_running", "deadline", "超时", "卡死",
    )),
    ("budget", "预算受限", (
        "budget", "hard_stop", "hard stop", "quota", "cost cap", "insufficient credit", "预算",
    )),
    ("stalled_process", "流程停滞/待人工", (
        "stalled", "review_gap", "review gap", "停滞", "待复盘", "待人工", "无人处理",
    )),
    ("external_reject", "外部拒绝/风控", (
        "403", "429", "401", "forbidden", "denied", "unauthorized", "rate limit",
        "captcha", "banned", "blocked by", "风控", "拒绝",
    )),
    ("download", "下载失败", (
        "download", "yt-dlp", "ytdlp", "fragment", "connection", "network", "proxy", "ssl", "下载",
    )),
    ("no_data", "无数据/内容不可用", (
        "content_unavailable", "unavailable", "not found", "no data", "empty",
        "private video", "removed", "404", "无数据", "已删除", "不可用",
    )),
    ("parse_error", "解析失败", (
        "media_resolve", "parse", "decode", "keyerror", "unexpected format",
        "invalid json", "json", "解析",
    )),
    ("missing_params", "参数缺失", (
        "must include", "missing", "required field", "缺少", "缺参",
    )),
    ("code_error", "代码错误", (
        "nameerror", "typeerror", "attributeerror", "importerror", "valueerror",
        "indexerror", "zerodivision", "traceback", "exception",
    )),
)
_REASON_LABELS = {key: label for key, label, _terms in FAILURE_REASONS}
_REASON_LABELS["unclassified"] = "未归类(词表识别不了)"

# apify_jobs.last_error_category 直翻(有结构化标签先信标签,unknown 才落词表)
_CATEGORY_TO_REASON = {
    "download": "download",
    "media_resolve": "parse_error",
    "content_unavailable": "no_data",
    "code_error": "code_error",
    "stale_running": "timeout",
    "budget": "budget",
}

# 负向人工反馈词表(与 prediction_ledger 同口径)
_NEGATIVE_FEEDBACK_TYPES = frozenset(
    {"reject", "rejected", "dismiss", "dismissed", "negative", "exclude", "downvote"}
)


# ── 小工具(与 signature_profile / prediction_ledger 同款容错约定)──────


def _truthy(value: Any) -> bool:
    """compat 层 BOOLEAN 读回可能是 int 1/0 / 't' / 'true',统一容错。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "t", "true", "yes", "y")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _text(value: Any, limit: int = 200) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_reason(blob: str) -> str:
    """词表首中即归类;识别不了诚实 unclassified,不硬贴标签。"""
    low = (blob or "").lower()
    if low:
        for key, _label, terms in FAILURE_REASONS:
            if any(term in low for term in terms):
                return key
    return "unclassified"


def _cluster_reasons(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """条目 [{reason_key, sample}] → 按原因聚类 [{key,label,count,samples[]}](count 降序)。"""
    buckets: dict[str, dict[str, Any]] = {}
    for item in entries:
        key = str(item.get("reason_key") or "unclassified")
        bucket = buckets.setdefault(
            key, {"key": key, "label": _REASON_LABELS.get(key, key), "count": 0, "samples": []}
        )
        bucket["count"] += 1
        sample = _text(item.get("sample"), 160)
        if sample and len(bucket["samples"]) < MAX_REASON_SAMPLES and sample not in bucket["samples"]:
            bucket["samples"].append(sample)
    return sorted(buckets.values(), key=lambda b: int(b["count"]), reverse=True)


def _needs_review(hit_rate: Any, sample_count: Any) -> bool:
    """低命中组判定:hit<0.5 且样本>=5;命中率判不出(None)绝不标,防样本荒误伤。"""
    try:
        if hit_rate is None:
            return False
        return float(hit_rate) < NEEDS_REVIEW_HIT_CEILING and int(sample_count or 0) >= NEEDS_REVIEW_MIN_SAMPLE
    except (TypeError, ValueError):
        return False


def _ledger_hit(action_type: str) -> dict[str, Any]:
    """复用第5轮预测台账的命中率(只读;永不 raise);取展示所需子集。"""
    try:
        from app.domains.agents.prediction_ledger import hit_rate_for

        full = hit_rate_for(action_type, window=LEDGER_WINDOW)
        basis = full.get("basis") if isinstance(full.get("basis"), dict) else {}
        return {
            "hit_rate": full.get("hit_rate"),
            "sample_count": int(full.get("sample_count") or 0),
            "status": str(full.get("status") or ""),
            "confidence": str(full.get("confidence") or ""),
            "source": "prediction_ledger_v1",
            "window": LEDGER_WINDOW,
            "note": _text(basis.get("hit_definition"), 200) or None,
        }
    except Exception as exc:  # noqa: BLE001 — 台账失联不拖垮复盘
        logger.warning("miss_review ledger_hit failed action_type=%s: %s", action_type, exc)
        return {"hit_rate": None, "sample_count": 0, "status": "error", "confidence": "none",
                "source": "prediction_ledger_v1", "window": LEDGER_WINDOW, "note": str(exc)[:200]}


def _error_group(action_type: str, label: str, exc: Exception) -> dict[str, Any]:
    logger.warning("miss_review group failed action_type=%s: %s", action_type, exc)
    return {
        "action_type": action_type, "label": label, "status": "error",
        "reason": str(exc)[:300], "miss_count": 0, "reasons": [], "items": [],
        "hit": {"hit_rate": None, "sample_count": 0, "status": "error", "confidence": "none"},
        "needs_review": False, "source": [],
    }


# ── 各组收集器(全只读;单组独立 try/except)─────────────────────────


def _collect_alert_group(conn: Any) -> dict[str, Any]:
    """alert_signal:告警未闭环 = 未命中条目;原因聚类吃 rule_key+标题词表。"""
    label = "规则告警未闭环"
    try:
        rows = conn.execute(
            """
            SELECT id, rule_key, severity, title, status, resolved_at, created_at
            FROM vkpi_alerts
            ORDER BY id DESC
            LIMIT ?
            """,
            (SCAN_LIMIT,),
        ).fetchall()
        entries: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            resolved = str(row.get("status") or "").strip().lower() == "resolved" or bool(_iso(row.get("resolved_at")))
            if resolved:
                continue
            rule_key = _text(row.get("rule_key"), 80)
            title = _text(row.get("title"), 160)
            blob = " ".join(p for p in (rule_key, title) if p)
            entries.append({"reason_key": _classify_reason(blob), "sample": title or rule_key})
            if len(items) < MAX_ITEMS_PER_GROUP:
                items.append({
                    "ref": f"alert:{row.get('id')}",
                    "title": title or rule_key or "(无标题)",
                    "rule_key": rule_key or None,
                    "severity": _text(row.get("severity"), 20) or None,
                    "at": _iso(row.get("created_at")),
                })
        hit = _ledger_hit("alert_signal")
        if not entries:
            return {
                "action_type": "alert_signal", "label": label, "status": "empty",
                "reason": "告警全部已闭环(无 open 条目),本组无未命中可复盘。",
                "miss_count": 0, "reasons": [], "items": [], "hit": hit,
                "needs_review": False, "source": ["vkpi_alerts"],
            }
        return {
            "action_type": "alert_signal", "label": label, "status": "ready",
            "miss_count": len(entries),
            "reasons": _cluster_reasons(entries),
            "items": items,
            "hit": hit,
            "needs_review": _needs_review(hit.get("hit_rate"), hit.get("sample_count")),
            "source": ["vkpi_alerts"],
            "basis": {
                "miss_definition": "status<>resolved 且 resolved_at 为空的告警(未闭环=信号没被处理)",
                "hit_rate_source": f"prediction_ledger.hit_rate_for('alert_signal', window={LEDGER_WINDOW})(处理闭环率代理口径)",
            },
        }
    except Exception as exc:  # noqa: BLE001
        return _error_group("alert_signal", label, exc)


def _collect_recommend_group(conn: Any) -> dict[str, Any]:
    """kol_recommend:拒绝的推荐 + 负向人工反馈 = 未命中条目;原因吃 reject_reason/note 词表。"""
    label = "KOL 推荐负反馈"
    try:
        out_rows = conn.execute(
            """
            SELECT id, recommendation_id, kol_pool_id, was_rejected, reject_reason, rejected_at
            FROM vkpi_recommendation_outcomes
            ORDER BY id DESC
            LIMIT ?
            """,
            (SCAN_LIMIT,),
        ).fetchall()
        fb_rows = conn.execute(
            "SELECT recommendation_id, feedback_type, note, created_at FROM vkpi_recommendation_feedback ORDER BY id DESC LIMIT ?",
            (SCAN_LIMIT,),
        ).fetchall()

        entries: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        scanned = len(out_rows)
        pending = 0
        for raw in out_rows:
            row = dict(raw)
            if not _truthy(row.get("was_rejected")):
                pending += 1
                continue
            reason_text = _text(row.get("reject_reason"), 160)
            entries.append({"reason_key": _classify_reason(reason_text), "sample": reason_text})
            if len(items) < MAX_ITEMS_PER_GROUP:
                items.append({
                    "ref": f"reco_outcome:{row.get('id')}",
                    "title": f"推荐 #{row.get('recommendation_id')} 被拒绝(kol_pool {row.get('kol_pool_id')})",
                    "detail": reason_text or None,
                    "at": _iso(row.get("rejected_at")),
                })
        for raw in fb_rows:
            row = dict(raw)
            ftype = str(row.get("feedback_type") or "").strip().lower()
            if ftype not in _NEGATIVE_FEEDBACK_TYPES:
                continue
            note = _text(row.get("note"), 160)
            entries.append({"reason_key": _classify_reason(" ".join((ftype, note))), "sample": note or ftype})
            if len(items) < MAX_ITEMS_PER_GROUP:
                items.append({
                    "ref": f"reco_feedback:{row.get('recommendation_id')}",
                    "title": f"推荐 #{row.get('recommendation_id')} 负向反馈 {ftype}",
                    "detail": note or None,
                    "at": _iso(row.get("created_at")),
                })
        hit = _ledger_hit("kol_recommend")
        if not entries:
            return {
                "action_type": "kol_recommend", "label": label, "status": "empty",
                "reason": f"扫描 {scanned} 条推荐结果,无 was_rejected、无负向人工反馈;"
                          f"{pending} 条 pending 是没人对答案,不算失败,不进复盘。",
                "miss_count": 0, "reasons": [], "items": [], "hit": hit,
                "needs_review": False, "source": ["vkpi_recommendation_outcomes", "vkpi_recommendation_feedback"],
            }
        return {
            "action_type": "kol_recommend", "label": label, "status": "ready",
            "miss_count": len(entries),
            "reasons": _cluster_reasons(entries),
            "items": items,
            "hit": hit,
            "needs_review": _needs_review(hit.get("hit_rate"), hit.get("sample_count")),
            "source": ["vkpi_recommendation_outcomes", "vkpi_recommendation_feedback"],
            "basis": {
                "miss_definition": "was_rejected 为真的推荐结果 + 负向人工反馈(reject/dismiss 等)",
                "hit_rate_source": f"prediction_ledger.hit_rate_for('kol_recommend', window={LEDGER_WINDOW})",
            },
        }
    except Exception as exc:  # noqa: BLE001
        return _error_group("kol_recommend", label, exc)


def _collect_task_groups(conn: Any) -> list[dict[str, Any]]:
    """task:<job_type>:apify_jobs failed 带 last_error,一 job_type 一组。

    命中率=该 job_type 的执行成功率 done/(done+failed)(源表自算,blocked/triage
    是在途不进分母);原因先信 last_error_category 结构化标签,unknown 落词表。
    """
    try:
        status_rows = conn.execute(
            "SELECT job_type, status, COUNT(*) AS n FROM apify_jobs GROUP BY job_type, status",
        ).fetchall()
        done_by_type: dict[str, int] = {}
        failed_by_type: dict[str, int] = {}
        for raw in status_rows:
            row = dict(raw)
            jt = str(row.get("job_type") or "")
            n = int(row.get("n") or 0)
            status = str(row.get("status") or "").strip().lower()
            if status == "done":
                done_by_type[jt] = done_by_type.get(jt, 0) + n
            elif status == "failed":
                failed_by_type[jt] = failed_by_type.get(jt, 0) + n

        fail_rows = conn.execute(
            """
            SELECT id, job_type, last_error, last_error_category, attempts, updated_at
            FROM apify_jobs
            WHERE status = 'failed'
            ORDER BY id DESC
            LIMIT ?
            """,
            (SCAN_LIMIT,),
        ).fetchall()
        by_type: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for raw in fail_rows:
            row = dict(raw)
            jt = str(row.get("job_type") or "unknown")
            slot = by_type.setdefault(jt, {"entries": [], "items": []})
            err = _text(row.get("last_error"), 200)
            category = str(row.get("last_error_category") or "").strip().lower()
            reason_key = _CATEGORY_TO_REASON.get(category) or _classify_reason(err)
            slot["entries"].append({"reason_key": reason_key, "sample": err})
            if len(slot["items"]) < MAX_ITEMS_PER_GROUP:
                slot["items"].append({
                    "ref": f"apify_job:{row.get('id')}",
                    "title": err or "(无 last_error 文本)",
                    "detail": f"attempts={row.get('attempts')} category={category or 'none'}",
                    "at": _iso(row.get("updated_at")),
                })

        groups: list[dict[str, Any]] = []
        for jt in sorted(by_type, key=lambda k: len(by_type[k]["entries"]), reverse=True):
            slot = by_type[jt]
            done = done_by_type.get(jt, 0)
            failed = failed_by_type.get(jt, len(slot["entries"]))
            sample = done + failed
            hit_rate = round(done / sample, 4) if sample > 0 else None
            hit = {
                "hit_rate": hit_rate,
                "sample_count": sample,
                "status": "ok" if sample > 0 else "empty",
                "confidence": "source_computed",
                "source": "apify_jobs",
                "note": "执行成功率口径 done/(done+failed);blocked/triage 在途不进分母",
            }
            groups.append({
                "action_type": f"task:{jt}",
                "label": f"任务失败 · {jt}",
                "status": "ready",
                "miss_count": len(slot["entries"]),
                "reasons": _cluster_reasons(slot["entries"]),
                "items": slot["items"],
                "hit": hit,
                "needs_review": _needs_review(hit_rate, sample),
                "source": ["apify_jobs"],
                "basis": {
                    "miss_definition": "apify_jobs status='failed'(带 last_error 原文)",
                    "hit_rate_source": "源表自算 done/(done+failed)(执行成功率,非预测命中率)",
                },
            })
        return groups
    except Exception as exc:  # noqa: BLE001 — 任务组整体失败给一个 error 组,不拖垮整单
        return [_error_group("task:apify_jobs", "任务失败 · apify_jobs", exc)]


# ── 对外入口①:复盘清单(纯读)────────────────────────────────────────


def miss_review_list() -> dict[str, Any]:
    """低命中复盘清单:未命中/失败条目按 action_type 分组 + 词表原因聚类 + needs_review 标记。

    永不 raise:整体失败回 {status:"error"};单组失败该组 status="error" 其余照出。
    红线:纯读聚合,零写库、零 LLM、零触 viltrox_fit_score / rule_v0。
    """
    try:
        from app.db.connection import get_conn

        conn = get_conn()
        groups: list[dict[str, Any]] = [
            _collect_alert_group(conn),
            _collect_recommend_group(conn),
        ]
        groups.extend(_collect_task_groups(conn))
        miss_total = sum(int(g.get("miss_count") or 0) for g in groups)
        return {
            "status": "ok",
            "method": METHOD,
            "generated_at": _utcnow_iso(),
            "groups": groups,
            "totals": {
                "groups": len(groups),
                "miss_total": miss_total,
                "needs_review_groups": sum(1 for g in groups if g.get("needs_review")),
                "groups_with_miss": sum(1 for g in groups if int(g.get("miss_count") or 0) > 0),
            },
            "thresholds": {
                "needs_review": f"hit_rate < {NEEDS_REVIEW_HIT_CEILING} 且 sample_count >= {NEEDS_REVIEW_MIN_SAMPLE}",
            },
            "note": "只读聚合真实失败留痕(词表聚类,零 LLM);复盘结论只入记忆供人看,绝不自动改线上规则、绝不触碰评分。",
        }
    except Exception as exc:  # noqa: BLE001 — 契约:永不 raise
        logger.warning("miss_review_list failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300], "groups": [], "generated_at": _utcnow_iso()}


# ── 对外入口②:失败原因入记忆(写只经既有 record_signal)────────────────


def _group_summary_text(group: dict[str, Any]) -> str:
    """一组失败原因 → 一句可读摘要(入 record_signal.reason,≤500 由其自截)。"""
    reasons = group.get("reasons") if isinstance(group.get("reasons"), list) else []
    reason_bits = "、".join(
        f"{r.get('label')}×{int(r.get('count') or 0)}" for r in reasons[:4] if isinstance(r, dict)
    )
    hit = group.get("hit") if isinstance(group.get("hit"), dict) else {}
    hit_rate = hit.get("hit_rate")
    hit_bit = (
        f"命中率{float(hit_rate):.2f}(样本{int(hit.get('sample_count') or 0)})"
        if hit_rate is not None else "命中率判不出"
    )
    sample_bit = ""
    for r in reasons[:1]:
        samples = r.get("samples") if isinstance(r.get("samples"), list) else []
        if samples:
            sample_bit = f";例:{_text(samples[0], 120)}"
            break
    flag = "【低命中需复盘】" if group.get("needs_review") else ""
    return (
        f"{flag}{group.get('action_type')} 失败{int(group.get('miss_count') or 0)}条;"
        f"主因:{reason_bits or '未归类'};{hit_bit}{sample_bit}"
    )


def _already_written_today(conn: Any, action_type: str) -> int | None:
    """同组同日幂等检查:今天(DB 服务器时区 CURRENT_DATE)已写过该组则返回既有 id。

    record_signal 本身无去重语义(已读源码确认),幂等在本层用
    (action_kind, entity_type, entity_id, created_at>=CURRENT_DATE) 三元组+日界实现。
    """
    row = conn.execute(
        """
        SELECT id FROM vkpi_agent_actions
        WHERE action_kind = ? AND entity_type = ? AND entity_id = ?
          AND created_at >= CURRENT_DATE
        ORDER BY id DESC
        LIMIT 1
        """,
        (MEMORY_ACTION_KIND, MEMORY_ENTITY_TYPE, str(action_type)),
    ).fetchone()
    return int(dict(row)["id"]) if row else None


def persist_review_findings(*, dry_run: bool = True) -> dict[str, Any]:
    """把每组失败原因摘要写入记忆(经既有 agent_memory_writer.record_signal,白名单 kind=retrospective)。

    dry_run=True(默认)只预览要写什么,零写库;False 才真写。
    幂等:同组同日已写(vkpi_agent_actions 今天已有同 kind+entity 行)→ 跳过不重复写。
    只写 miss_count>0 的组(没失败没什么可记);永不 raise。
    红线:写只落学习留痕表 vkpi_agent_actions,零触评分域 / rule_v0。
    """
    try:
        from app.db.connection import get_conn, table_exists
        from app.domains.memory.agent_memory_writer import record_signal

        review = miss_review_list()
        if str(review.get("status")) != "ok":
            return {"status": "error", "reason": f"miss_review_list 失败:{review.get('reason')}",
                    "dry_run": bool(dry_run), "entries": [], "generated_at": _utcnow_iso()}
        if not table_exists("vkpi_agent_actions"):
            return {"status": "error", "reason": "vkpi_agent_actions 表不存在(迁移182未跑),记忆无处可写。",
                    "dry_run": bool(dry_run), "entries": [], "generated_at": _utcnow_iso()}

        conn = get_conn()
        entries: list[dict[str, Any]] = []
        written = skipped_same_day = would_write = failed = 0
        for group in review.get("groups") or []:
            if str(group.get("status")) != "ready" or int(group.get("miss_count") or 0) <= 0:
                continue
            action_type = str(group.get("action_type") or "")
            summary = _group_summary_text(group)
            entry: dict[str, Any] = {
                "action_type": action_type,
                "miss_count": int(group.get("miss_count") or 0),
                "needs_review": bool(group.get("needs_review")),
                "summary": summary,
            }
            existing_id = _already_written_today(conn, action_type)
            if existing_id is not None:
                entry.update({"action": "skip_same_day", "agent_action_id": existing_id})
                skipped_same_day += 1
            elif dry_run:
                entry.update({"action": "would_write", "agent_action_id": None})
                would_write += 1
            else:
                hit = group.get("hit") if isinstance(group.get("hit"), dict) else {}
                new_id = record_signal(
                    action_kind=MEMORY_ACTION_KIND,
                    entity_type=MEMORY_ENTITY_TYPE,
                    entity_id=action_type,
                    reason=summary,
                    detail={
                        "method": METHOD,
                        "review_date_utc": _utcnow_iso()[:10],
                        "miss_count": int(group.get("miss_count") or 0),
                        "needs_review": bool(group.get("needs_review")),
                        "hit_rate": hit.get("hit_rate"),
                        "hit_sample_count": hit.get("sample_count"),
                        "reasons": (group.get("reasons") or [])[:6],
                        "source": group.get("source") or [],
                    },
                )
                if new_id is not None:
                    entry.update({"action": "written", "agent_action_id": new_id})
                    written += 1
                else:  # record_signal best-effort 失败诚实报,不装写成功
                    entry.update({"action": "write_failed", "agent_action_id": None})
                    failed += 1
            entries.append(entry)
        return {
            "status": "ok",
            "method": METHOD,
            "dry_run": bool(dry_run),
            "written": written,
            "skipped_same_day": skipped_same_day,
            "would_write": would_write,
            "write_failed": failed,
            "entries": entries,
            "idempotency": "同组同日不重复写(vkpi_agent_actions 按 kind+entity+CURRENT_DATE 去重)",
            "generated_at": _utcnow_iso(),
        }
    except Exception as exc:  # noqa: BLE001 — 契约:永不 raise
        logger.warning("persist_review_findings failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300], "dry_run": bool(dry_run),
                "entries": [], "generated_at": _utcnow_iso()}

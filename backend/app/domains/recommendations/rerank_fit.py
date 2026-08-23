"""影子重排序·周拟合作业(学习闭环 W-L2·拟合段)。

输入:vkpi_recommendation_feature_snapshot(推荐时刻特征)× vkpi_recommendation_outcomes(真实结果)。
产出:vkpi_recommendation_rerank_model 一行(numpy logistic 权重 + 指标 + 理由码)。

激活规则(硬):样本 < ``VKPI_RECO_FIT_MIN_SAMPLES``(默认 30)或正/负类任一 < 5 → 落账但
``activated=FALSE``(诚实记录「样本不足」),主引擎继续零调整。
周期:由每日 outcome 回流作业顺带触发,``VKPI_RECO_FIT_INTERVAL_DAYS``(默认 7)内已拟合则跳过;
``force=True`` 可手动重拟合。零 LLM、零 provider、零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.recommendations import rerank_shadow as shadow
from app.shared.vkpi_utils import utcnow_iso

logger = get_logger(__name__)

MIN_SAMPLES_ENV = "VKPI_RECO_FIT_MIN_SAMPLES"
INTERVAL_DAYS_ENV = "VKPI_RECO_FIT_INTERVAL_DAYS"
LABEL_WINDOW_DAYS_ENV = "VKPI_RECO_LABEL_WINDOW_DAYS"
DEFAULT_MIN_SAMPLES = 30
MIN_CLASS_COUNT = 5
DEFAULT_INTERVAL_DAYS = 7
DEFAULT_LABEL_WINDOW_DAYS = 14
ACTIVATION_RULE = "samples>=min_samples(30) and positives>=5 and negatives>=5"

# 正向结果节点(任一为真 → 标签 1);was_rejected 为真 → 标签 0;
# 超过标签窗口仍无任何动作 → 标签 0(沉默即负例,窗口内沉默 = 未定,不入样本)。
POSITIVE_NODES: tuple[str, ...] = (
    "was_shortlisted",
    "was_claimed",
    "project_created",
    "outreach_sent",
    "reply_received",
    "agreement_reached",
    "content_published",
    "order_attributed",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _min_samples() -> int:
    return max(1, int(shadow.env_float(MIN_SAMPLES_ENV, float(DEFAULT_MIN_SAMPLES))))


def _interval_days() -> float:
    return max(0.0, shadow.env_float(INTERVAL_DAYS_ENV, float(DEFAULT_INTERVAL_DAYS)))


def _label_window_days() -> float:
    return max(0.0, shadow.env_float(LABEL_WINDOW_DAYS_ENV, float(DEFAULT_LABEL_WINDOW_DAYS)))


# ── 标签回流 ────────────────────────────────────────────────────────────


def label_for_outcome(outcome: dict[str, Any] | None, *, recommended_at: Any, now: datetime | None = None) -> tuple[int | None, list[str]]:
    """从 outcome 行推标签:(label, 命中的正向节点)。返回 (None, []) = 未定,不入样本。"""
    nodes = [node for node in POSITIVE_NODES if outcome and shadow.truthy(outcome.get(node))]
    if nodes:
        return 1, nodes
    if outcome and shadow.truthy(outcome.get("was_rejected")):
        return 0, ["was_rejected"]
    recommended = _parse_ts(recommended_at)
    if recommended is None:
        return None, []
    age = (now or _now()) - recommended
    if age >= timedelta(days=_label_window_days()):
        return 0, ["silent_after_window"]
    return None, []


def label_snapshots(limit: int = 2000) -> dict[str, Any]:
    """把 outcomes 真实结果回流成快照标签(幂等:标签变了才 UPDATE;未定行保持 NULL)。"""
    if not shadow.tables_ready():
        return {"status": "tables_missing", "labeled": 0, "pending": 0, "scanned": 0}
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT s.id AS snapshot_id, s.recommendation_id, s.outcome_label, s.created_at AS snapshot_created_at,
               o.was_shortlisted, o.was_rejected, o.was_claimed, o.project_created, o.outreach_sent,
               o.reply_received, o.agreement_reached, o.content_published, o.order_attributed,
               o.recommended_at
        FROM {shadow.SNAPSHOT_TABLE} s
        LEFT JOIN vkpi_recommendation_outcomes o ON o.recommendation_id = s.recommendation_id
        ORDER BY s.id DESC
        LIMIT ?
        """,
        (int(max(1, min(int(limit or 2000), 10000))),),
    ).fetchall()
    now = _now()
    labeled = 0
    pending = 0
    positives = 0
    for raw in rows:
        row = dict(raw)
        outcome = row if row.get("recommended_at") is not None else None
        label, nodes = label_for_outcome(
            outcome,
            recommended_at=row.get("recommended_at") or row.get("snapshot_created_at"),
            now=now,
        )
        if label is None:
            pending += 1
            continue
        if label == 1:
            positives += 1
        current = row.get("outcome_label")
        if current is not None and int(current) == label:
            continue
        conn.execute(
            f"UPDATE {shadow.SNAPSHOT_TABLE} SET outcome_label=?, outcome_nodes=?, outcome_labeled_at=?, updated_at=? WHERE id=?",
            (int(label), shadow.dumps(nodes), utcnow_iso(), utcnow_iso(), int(row["snapshot_id"])),
        )
        labeled += 1
    conn.commit()
    return {"status": "ok", "scanned": len(rows), "labeled": labeled, "pending": pending, "positives_seen": positives}


# ── 拟合 ────────────────────────────────────────────────────────────────


def _load_training_rows(limit: int = 5000) -> list[dict[str, Any]]:
    rows = get_conn().execute(
        f"""
        SELECT feature_vector, outcome_label
        FROM {shadow.SNAPSHOT_TABLE}
        WHERE outcome_label IS NOT NULL AND feature_keys_version=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (shadow.FEATURE_KEYS_VERSION, int(max(1, min(int(limit or 5000), 50000)))),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        vector = shadow.loads(row.get("feature_vector"), {})
        label = row.get("outcome_label")
        if not vector or label is None:
            continue
        out.append({"vector": vector, "label": int(label)})
    return out


def logistic_fit(
    vectors: list[dict[str, float]],
    labels: list[int],
    *,
    l2: float = 1.0,
    iterations: int = 400,
    learning_rate: float = 0.1,
) -> dict[str, Any]:
    """标准化 + L2 正则的 logistic 回归(numpy 全批梯度下降;无 numpy 则抛 ImportError 由调用方诚实记录)。"""
    import numpy as np

    keys = list(shadow.FEATURE_KEYS)
    matrix = np.array([[float(vec.get(key, 0.0)) for key in keys] for vec in vectors], dtype=float)
    y = np.array(labels, dtype=float)
    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0)
    std[std < 1e-9] = 1.0
    z = (matrix - mean) / std
    n, d = z.shape
    weights = np.zeros(d)
    bias = float(np.log((y.mean() + 1e-6) / (1.0 - y.mean() + 1e-6)))
    # einsum 而非 @:macOS Accelerate 下 numpy 2.x 的 matmul 对全零列会报伪 FP 警告;logits 截断防溢出。
    def _probs(w: Any, b: float) -> Any:
        logits = np.clip(np.einsum("ij,j->i", z, w) + b, -30.0, 30.0)
        return 1.0 / (1.0 + np.exp(-logits))

    for _ in range(int(iterations)):
        probs = _probs(weights, bias)
        grad_w = np.einsum("ij,i->j", z, probs - y) / n + (l2 / n) * weights
        grad_b = float((probs - y).mean())
        weights -= learning_rate * grad_w
        bias -= learning_rate * grad_b
    probs = _probs(weights, bias)
    if not (np.isfinite(weights).all() and np.isfinite(probs).all()):
        raise ValueError("logistic fit diverged (non-finite weights)")
    eps = 1e-9
    log_loss = float(-np.mean(y * np.log(probs + eps) + (1 - y) * np.log(1 - probs + eps)))
    base_rate = float(y.mean())
    base_loss = float(-(base_rate * np.log(base_rate + eps) + (1 - base_rate) * np.log(1 - base_rate + eps)))
    accuracy = float(np.mean((probs >= 0.5) == (y >= 0.5)))
    return {
        "coef": {key: round(float(weights[idx]), 6) for idx, key in enumerate(keys)},
        "mean": {key: round(float(mean[idx]), 6) for idx, key in enumerate(keys)},
        "std": {key: round(float(std[idx]), 6) for idx, key in enumerate(keys)},
        "bias": round(float(bias), 6),
        "base_rate": round(base_rate, 6),
        "metrics": {
            "log_loss": round(log_loss, 6),
            "baseline_log_loss": round(base_loss, 6),
            "accuracy": round(accuracy, 6),
            "n": int(n),
            "l2": l2,
            "iterations": int(iterations),
        },
    }


def _top_reason_codes(coef: dict[str, float]) -> list[str]:
    ranked = sorted(coef.items(), key=lambda pair: abs(float(pair[1] or 0)), reverse=True)
    return [f"hist_{key}_{'up' if float(value) > 0 else 'down'}" for key, value in ranked[:shadow.MAX_REASON_CODES] if abs(float(value or 0)) >= 0.05]


def _store_model(
    *,
    version: str,
    sample_count: int,
    positive_count: int,
    negative_count: int,
    activated: bool,
    rule: str,
    weights: dict[str, Any],
    metrics: dict[str, Any],
    reason_codes: list[str],
) -> dict[str, Any]:
    now = utcnow_iso()
    conn = get_conn()
    conn.execute(
        f"""
        INSERT INTO {shadow.MODEL_TABLE}
            (model_version, feature_keys_version, fitted_at, sample_count, positive_count, negative_count,
             activated, activation_rule, weights, metrics, reason_codes, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            version,
            shadow.FEATURE_KEYS_VERSION,
            now,
            int(sample_count),
            int(positive_count),
            int(negative_count),
            bool(activated),
            rule,
            shadow.dumps(weights),
            shadow.dumps(metrics),
            shadow.dumps(reason_codes),
            now,
        ),
    )
    conn.commit()
    return {
        "model_version": version,
        "fitted_at": now,
        "sample_count": int(sample_count),
        "positive_count": int(positive_count),
        "negative_count": int(negative_count),
        "activated": bool(activated),
        "activation_rule": rule,
        "metrics": metrics,
        "reason_codes": reason_codes,
    }


def last_fit_at() -> datetime | None:
    row = get_conn().execute(
        f"SELECT fitted_at FROM {shadow.MODEL_TABLE} ORDER BY fitted_at DESC, id DESC LIMIT 1"
    ).fetchone()
    return _parse_ts(dict(row).get("fitted_at")) if row else None


def fit_rerank_model(*, force: bool = False) -> dict[str, Any]:
    """一次拟合:标签回流 → 取样 → 门槛 → numpy logistic → 落账(激活与否都诚实记录)。"""
    if not shadow.tables_ready():
        return {"status": "tables_missing", "activated": False}
    labels_result = label_snapshots()
    rows = _load_training_rows()
    sample_count = len(rows)
    positives = sum(1 for row in rows if row["label"] == 1)
    negatives = sample_count - positives
    min_samples = _min_samples()
    version = f"rerank_logit_{_now().strftime('%Y%m%dT%H%M%SZ')}"
    eligible = sample_count >= min_samples and positives >= MIN_CLASS_COUNT and negatives >= MIN_CLASS_COUNT
    if not eligible:
        codes = ["insufficient_samples"]
        if positives < MIN_CLASS_COUNT:
            codes.append("insufficient_positives")
        if negatives < MIN_CLASS_COUNT:
            codes.append("insufficient_negatives")
        stored = _store_model(
            version=version,
            sample_count=sample_count,
            positive_count=positives,
            negative_count=negatives,
            activated=False,
            rule=ACTIVATION_RULE,
            weights={},
            metrics={"min_samples": min_samples},
            reason_codes=codes[: shadow.MAX_REASON_CODES],
        )
        stored.update({"status": "not_activated", "labels": labels_result, "force": bool(force)})
        return stored
    try:
        fitted = logistic_fit([row["vector"] for row in rows], [row["label"] for row in rows])
    except (ImportError, ValueError) as exc:
        failure = "numpy_unavailable" if isinstance(exc, ImportError) else "fit_diverged"
        logger.warning("rerank_fit.%s: %s", failure, exc)
        stored = _store_model(
            version=version,
            sample_count=sample_count,
            positive_count=positives,
            negative_count=negatives,
            activated=False,
            rule=ACTIVATION_RULE,
            weights={},
            metrics={"error": failure},
            reason_codes=[failure],
        )
        stored.update({"status": "not_activated", "labels": labels_result, "force": bool(force)})
        return stored
    metrics = dict(fitted.pop("metrics"))
    improves = metrics["log_loss"] <= metrics["baseline_log_loss"]
    codes = _top_reason_codes(fitted["coef"]) if improves else ["no_lift_over_baseline"]
    stored = _store_model(
        version=version,
        sample_count=sample_count,
        positive_count=positives,
        negative_count=negatives,
        activated=bool(improves),
        rule=ACTIVATION_RULE + " and log_loss<=baseline",
        weights=fitted,
        metrics=metrics,
        reason_codes=codes,
    )
    stored.update({"status": "activated" if improves else "not_activated", "labels": labels_result, "force": bool(force)})
    logger.info("rerank_fit.fitted version=%s n=%s activated=%s", version, sample_count, bool(improves))
    return stored


def maybe_weekly_fit(*, force: bool = False) -> dict[str, Any]:
    """周节流:距上次拟合不足 interval 天且非 force → skipped_recent;否则跑一次 fit。"""
    if not shadow.tables_ready():
        return {"status": "tables_missing", "activated": False}
    last = last_fit_at()
    interval = _interval_days()
    if last is not None and not force and (_now() - last) < timedelta(days=interval):
        return {"status": "skipped_recent", "last_fit_at": last.isoformat(), "interval_days": interval, "activated": None}
    return fit_rerank_model(force=force)


# ── 离线 holdout 评估(L 车道 2026-08-23:每周一评估链调用,纯读,零落账)────────
#
# 按推荐时间 80/20 切分已打标快照:前 80% 拟合 logistic,后 20% 上比较
#   model(logistic 概率排序) vs rule_v0(快照 base_score 排序)
# 的 precision@10 与 AUC,各带 n 与 95% 置信区间(p@k Wilson;AUC Hanley-McNeil)。
# n < DEFAULT_MIN_SAMPLES 诚实记 insufficient_samples(n/30),绝不硬算。

HOLDOUT_TRAIN_SHARE = 0.8
HOLDOUT_K = 10


def _wilson(successes: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def _auc(scores: list[float], labels: list[int]) -> dict[str, Any]:
    """Mann-Whitney AUC(并列计半分)+ Hanley-McNeil 95% CI;任一类为空 → None。"""
    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]
    if not pos or not neg:
        return {"auc": None, "ci95": [None, None], "n_pos": len(pos), "n_neg": len(neg)}
    wins = 0.0
    for p in pos:
        for q in neg:
            wins += 1.0 if p > q else (0.5 if p == q else 0.0)
    auc = wins / (len(pos) * len(neg))
    q1 = auc / (2 - auc)
    q2 = 2 * auc * auc / (1 + auc)
    var = (auc * (1 - auc) + (len(pos) - 1) * (q1 - auc * auc) + (len(neg) - 1) * (q2 - auc * auc)) / (len(pos) * len(neg))
    se = var ** 0.5 if var > 0 else 0.0
    return {
        "auc": round(auc, 4),
        "ci95": [round(max(0.0, auc - 1.96 * se), 4), round(min(1.0, auc + 1.96 * se), 4)],
        "n_pos": len(pos),
        "n_neg": len(neg),
    }


def _precision_at_k(scores: list[float], labels: list[int], k: int) -> dict[str, Any]:
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))[: max(1, min(k, len(scores)))]
    hits = sum(1 for i in order if labels[i] == 1)
    lo, hi = _wilson(hits, len(order))
    return {"precision": round(hits / len(order), 4) if order else None, "k": len(order), "hits": hits, "ci95": [lo, hi]}


def _predict_proba(fitted: dict[str, Any], vector: dict[str, Any]) -> float:
    import math

    z = float(fitted.get("bias") or 0.0)
    coef = fitted.get("coef") or {}
    mean = fitted.get("mean") or {}
    std = fitted.get("std") or {}
    for key, w in coef.items():
        s = float(std.get(key) or 1.0) or 1.0
        z += float(w) * ((float(vector.get(key, 0.0) or 0.0) - float(mean.get(key) or 0.0)) / s)
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def _load_holdout_rows(limit: int = 5000) -> list[dict[str, Any]]:
    rows = get_conn().execute(
        f"""
        SELECT feature_vector, outcome_label, base_score, created_at
        FROM {shadow.SNAPSHOT_TABLE}
        WHERE outcome_label IS NOT NULL AND feature_keys_version=?
        ORDER BY created_at ASC, id ASC
        LIMIT ?
        """,
        (shadow.FEATURE_KEYS_VERSION, int(max(1, min(int(limit or 5000), 50000)))),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        vector = shadow.loads(row.get("feature_vector"), {})
        if not vector or row.get("outcome_label") is None:
            continue
        out.append({"vector": vector, "label": int(row["outcome_label"]), "base_score": float(row.get("base_score") or 0.0)})
    return out


def holdout_eval(*, limit: int = 5000, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """按推荐时间 80/20 holdout:model(logistic) vs rule_v0(base_score) 的 p@10 / AUC(带 n 与 CI)。纯读。"""
    if rows is None:
        if not shadow.tables_ready():
            return {"status": "tables_missing", "n": 0, "min_samples": _min_samples()}
        rows = _load_holdout_rows(limit)
    n = len(rows)
    min_samples = _min_samples()
    base: dict[str, Any] = {
        "method": "time_split_holdout_v1", "n": n, "min_samples": min_samples,
        "train_share": HOLDOUT_TRAIN_SHARE, "k": HOLDOUT_K, "feature_keys_version": shadow.FEATURE_KEYS_VERSION,
    }
    if n < min_samples:
        return {**base, "status": "insufficient_samples", "note": f"样本不足 {n}/{min_samples},不出 holdout 结论。"}
    split = max(1, int(n * HOLDOUT_TRAIN_SHARE))
    train, test = rows[:split], rows[split:]
    train_pos = sum(1 for r in train if r["label"] == 1)
    test_pos = sum(1 for r in test if r["label"] == 1)
    base.update({"n_train": len(train), "n_test": len(test), "train_pos": train_pos, "test_pos": test_pos})
    if not test or train_pos < MIN_CLASS_COUNT or (len(train) - train_pos) < MIN_CLASS_COUNT or test_pos == 0 or test_pos == len(test):
        return {**base, "status": "insufficient_class_balance",
                "note": "训练集正/负类不足 5 或测试集单一类别,AUC/p@k 无定义。"}
    try:
        fitted = logistic_fit([r["vector"] for r in train], [r["label"] for r in train])
    except (ImportError, ValueError) as exc:
        return {**base, "status": "fit_failed", "note": f"{type(exc).__name__}: {str(exc)[:120]}"}
    fitted.pop("metrics", None)
    labels = [r["label"] for r in test]
    model_scores = [_predict_proba(fitted, r["vector"]) for r in test]
    rule_scores = [r["base_score"] for r in test]
    model = {"precision_at_k": _precision_at_k(model_scores, labels, HOLDOUT_K), **_auc(model_scores, labels)}
    rule = {"precision_at_k": _precision_at_k(rule_scores, labels, HOLDOUT_K), **_auc(rule_scores, labels)}
    mp, rp = model["precision_at_k"]["precision"], rule["precision_at_k"]["precision"]
    verdict = "model_not_worse" if (mp is not None and rp is not None and mp >= rp) else "rule_v0_better"
    return {
        **base,
        "status": "ok",
        "model": model,
        "rule_v0": rule,
        "verdict": verdict,
        "activation_gate": "samples>=30 and pos/neg>=5 and model p@10 >= rule_v0 p@10",
        "note": "纯读 holdout;影子激活仍走 fit_rerank_model 硬规则,本结果只记录不改排序。",
    }

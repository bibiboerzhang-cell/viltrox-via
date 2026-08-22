"""影子重排序(学习闭环 W-L2·重排序段)。

链路:推荐时刻冻结特征向量(快照表)→ 结果回流给标签 → 周拟合(rerank_fit)产出
logistic 权重 → 主引擎按权重给每条候选一个 **rerank_adjustment(影子微调量)** 与
<=3 条理由码。

红线(不可越):
  * 绝不写 viltrox_fit_score,绝不改 rule_v0 / new_launch_match 的确定性评分公式;
    引擎落库的 ``score`` 列保持原值,影子调整只可能改变 **排序次序**(treatment arm)。
  * 评分公式、权重与理由码是后端内部字段;前端若要展示只给一句
    ``DISPLAY_NOTE``,不给数字。
  * A/B 默认关(``VKPI_RECO_AB=0``)→ arm=off:只记录影子量,不改排序。
    开启后按 staff id 稳定哈希分流(``VKPI_RECO_AB_TREATMENT_PCT`` 默认 50),
    无 staff 身份(cron)恒 control。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.shared.vkpi_utils import utcnow_iso

logger = get_logger(__name__)

SNAPSHOT_TABLE = "vkpi_recommendation_feature_snapshot"
MODEL_TABLE = "vkpi_recommendation_rerank_model"
FEATURE_KEYS_VERSION = "rerank_features_v1"
AB_FLAG_ENV = "VKPI_RECO_AB"
AB_PCT_ENV = "VKPI_RECO_AB_TREATMENT_PCT"
RERANK_SCALE_ENV = "VKPI_RECO_RERANK_SCALE"
RERANK_MAX_ENV = "VKPI_RECO_RERANK_MAX"
DISPLAY_NOTE = "推荐排序已按历史结果微调"
ARM_OFF = "off"
ARM_CONTROL = "control"
ARM_TREATMENT = "treatment"
MAX_REASON_CODES = 3

# 固定顺序的特征键(拟合与推断共用同一版本;新增特征必须升 FEATURE_KEYS_VERSION)。
FEATURE_KEYS: tuple[str, ...] = (
    "log_followers",
    "log_avg_views",
    "log_avg_likes",
    "log_avg_comments",
    "log_posts_count",
    "engagement_rate",
    "base_score_norm",
    "platform_youtube",
    "platform_instagram",
    "platform_tiktok",
    "catalog_match_norm",
    "competitor_risk_norm",
    "feedback_adj_norm",
    "product_match_norm",
    "cooperation_norm",
    "region_norm",
    "contact_norm",
    "freshness_norm",
    "engine_product_analysis",
)

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS vkpi_recommendation_feature_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER NOT NULL UNIQUE,
    run_id INTEGER,
    kol_pool_id INTEGER,
    launch_id INTEGER,
    staff_id INTEGER,
    engine TEXT NOT NULL DEFAULT '',
    arm TEXT NOT NULL DEFAULT 'off',
    feature_keys_version TEXT NOT NULL DEFAULT 'rerank_features_v1',
    feature_vector TEXT NOT NULL DEFAULT '{}',
    base_score REAL,
    rerank_adjustment REAL NOT NULL DEFAULT 0,
    rerank_applied INTEGER NOT NULL DEFAULT 0,
    rerank_reason_codes TEXT NOT NULL DEFAULT '[]',
    rerank_model_version TEXT NOT NULL DEFAULT '',
    outcome_label INTEGER,
    outcome_nodes TEXT NOT NULL DEFAULT '[]',
    outcome_labeled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vkpi_recommendation_rerank_model (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version TEXT NOT NULL UNIQUE,
    feature_keys_version TEXT NOT NULL DEFAULT 'rerank_features_v1',
    fitted_at TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    positive_count INTEGER NOT NULL DEFAULT 0,
    negative_count INTEGER NOT NULL DEFAULT 0,
    activated INTEGER NOT NULL DEFAULT 0,
    activation_rule TEXT NOT NULL DEFAULT '',
    weights TEXT NOT NULL DEFAULT '{}',
    metrics TEXT NOT NULL DEFAULT '{}',
    reason_codes TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
"""


# ── 小工具 ───────────────────────────────────────────────────────────────


def env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("rerank_shadow.env_float_invalid name=%s value=%r", name, raw)
        return default


def _f(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out) or math.isinf(out):
        return default
    return out


def _log1p(value: Any) -> float:
    return math.log1p(max(0.0, _f(value)))


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def truthy(value: Any) -> bool:
    """compat 读回 BOOLEAN 可能是 int 1/0 / 't' / True:统一判真。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "t", "true", "yes", "on"}


def loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None or value == "":
        return default
    try:
        loaded = json.loads(str(value))
    except (TypeError, ValueError):
        return default
    return loaded if isinstance(loaded, type(default)) else default


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def tables_ready() -> bool:
    """PG 靠迁移 288;sqlite(dev/hermetic)按需建表。缺表诚实返回 False,调用方计数跳过。"""
    if is_postgres_runtime():
        return table_exists(SNAPSHOT_TABLE) and table_exists(MODEL_TABLE)
    conn = get_conn()
    for statement in [part.strip() for part in _SQLITE_DDL.split(";") if part.strip()]:
        conn.execute(statement)
    conn.commit()
    return True


# ── A/B arm ─────────────────────────────────────────────────────────────


def ab_enabled() -> bool:
    return env_flag(AB_FLAG_ENV, False)


def treatment_pct() -> int:
    return int(_clip(env_float(AB_PCT_ENV, 50.0), 0.0, 100.0))


def staff_bucket(staff_id: int) -> int:
    digest = hashlib.sha256(f"vkpi-reco-ab:{int(staff_id)}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def arm_for_staff(staff: Any) -> str:
    """按 staff id 稳定哈希分流。A/B 关 → off;无身份 → control(cron/匿名绝不进 treatment)。"""
    if not ab_enabled():
        return ARM_OFF
    if isinstance(staff, dict):
        staff_id = int(staff.get("id") or staff.get("staff_id") or 0)
    else:
        try:
            staff_id = int(staff or 0)
        except (TypeError, ValueError):
            staff_id = 0
    if staff_id <= 0:
        return ARM_CONTROL
    return ARM_TREATMENT if staff_bucket(staff_id) < treatment_pct() else ARM_CONTROL


# ── 特征向量 ────────────────────────────────────────────────────────────


def feature_vector(
    *,
    base_score: Any,
    engine: str,
    profile: dict[str, Any] | None = None,
    breakdown: dict[str, Any] | None = None,
) -> dict[str, float]:
    """推荐时刻的数值特征(有界、缺失=0)。profile=pool 行/feature_store 快照,breakdown=引擎分项。"""
    prof = profile or {}
    bd = breakdown or {}
    platform = str(prof.get("platform") or "").strip().lower()
    engagement = _f(prof.get("engagement_rate"))
    if engagement > 1.0:  # 百分比口径 → 比例
        engagement = engagement / 100.0
    vec = {
        "log_followers": _log1p(prof.get("followers")),
        "log_avg_views": _log1p(prof.get("avg_views")),
        "log_avg_likes": _log1p(prof.get("avg_likes")),
        "log_avg_comments": _log1p(prof.get("avg_comments")),
        "log_posts_count": _log1p(prof.get("posts_count")),
        "engagement_rate": _clip(engagement, 0.0, 1.0),
        "base_score_norm": _clip(_f(base_score) / 100.0, 0.0, 1.0),
        "platform_youtube": 1.0 if platform in {"youtube", "yt"} else 0.0,
        "platform_instagram": 1.0 if platform in {"instagram", "ig"} else 0.0,
        "platform_tiktok": 1.0 if platform in {"tiktok", "tt"} else 0.0,
        "catalog_match_norm": _clip(_f(prof.get("matched_catalog_product_count")) / 10.0, 0.0, 1.0),
        "competitor_risk_norm": _clip(_f(prof.get("competitor_risk_score")) / 100.0, 0.0, 1.0),
        "feedback_adj_norm": _clip(_f(prof.get("operator_feedback_adjustment")) / 45.0, -1.0, 1.0),
        "product_match_norm": _clip(_f(bd.get("product_match")) / 40.0, 0.0, 1.0),
        "cooperation_norm": _clip(_f(bd.get("cooperation_strength")) / 20.0, 0.0, 1.0),
        "region_norm": _clip(_f(bd.get("region_match")) / 10.0, 0.0, 1.0),
        "contact_norm": _clip(_f(bd.get("contact_availability")) / 10.0, 0.0, 1.0),
        "freshness_norm": _clip(_f(bd.get("data_freshness")) / 10.0, 0.0, 1.0),
        "engine_product_analysis": 1.0 if engine == "product_analysis" else 0.0,
    }
    return {key: round(float(vec.get(key, 0.0)), 6) for key in FEATURE_KEYS}


# ── 模型读取与推断 ───────────────────────────────────────────────────────


def load_active_model() -> dict[str, Any] | None:
    """最新一条 activated 的拟合行;缺表/无行 → None(引擎退化为零调整,诚实不伪造)。"""
    if not tables_ready():
        return None
    row = get_conn().execute(
        f"SELECT * FROM {MODEL_TABLE} WHERE activated=? ORDER BY fitted_at DESC, id DESC LIMIT 1",
        (True,),
    ).fetchone()
    if not row:
        return None
    model = dict(row)
    model["weights"] = loads(model.get("weights"), {})
    model["metrics"] = loads(model.get("metrics"), {})
    if str(model.get("feature_keys_version") or "") != FEATURE_KEYS_VERSION:
        logger.warning("rerank_shadow.model_feature_version_mismatch version=%s", model.get("model_version"))
        return None
    if not model["weights"].get("coef"):
        return None
    return model


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def adjustment_for(model: dict[str, Any] | None, vector: dict[str, float]) -> tuple[float, list[str]]:
    """影子调整量(分制同引擎 0-100,绝对值 <= VKPI_RECO_RERANK_MAX,默认 5)+ <=3 条理由码。"""
    if not model:
        return 0.0, []
    weights = model.get("weights") or {}
    coef = weights.get("coef") or {}
    mean = weights.get("mean") or {}
    std = weights.get("std") or {}
    bias = _f(weights.get("bias"))
    base_rate = _clip(_f(weights.get("base_rate"), 0.5), 0.0, 1.0)
    contributions: list[tuple[str, float]] = []
    logit = bias
    for key in FEATURE_KEYS:
        scale = _f(std.get(key), 1.0) or 1.0
        z = (_f(vector.get(key)) - _f(mean.get(key))) / scale
        contribution = _f(coef.get(key)) * z
        logit += contribution
        contributions.append((key, contribution))
    probability = _sigmoid(logit)
    scale_points = env_float(RERANK_SCALE_ENV, 10.0)
    max_points = abs(env_float(RERANK_MAX_ENV, 5.0))
    adjustment = _clip((probability - base_rate) * scale_points, -max_points, max_points)
    contributions.sort(key=lambda pair: abs(pair[1]), reverse=True)
    codes = [
        f"hist_{key}_{'up' if value > 0 else 'down'}"
        for key, value in contributions[:MAX_REASON_CODES]
        if abs(value) >= 0.05
    ]
    return round(adjustment, 3), codes


def apply_shadow_rerank(
    items: list[dict[str, Any]],
    *,
    arm: str,
    model: dict[str, Any] | None,
    engine: str,
    profile_of: Any,
    breakdown_of: Any,
    score_key: str = "score",
) -> dict[str, Any]:
    """给每条候选挂 rerank_adjustment / rerank_reason_codes / rerank_vector(内部字段)。

    treatment arm 才按 score+adjustment 稳定重排(只动次序,绝不改 score 本身);
    off/control 只记录。返回策略摘要,供引擎放进响应的 ``rerank_policy``。
    """
    applied = bool(model) and arm == ARM_TREATMENT
    adjusted = 0
    for item in items:
        vector = feature_vector(
            base_score=item.get(score_key),
            engine=engine,
            profile=profile_of(item) or {},
            breakdown=breakdown_of(item) or {},
        )
        adjustment, codes = adjustment_for(model, vector)
        item["rerank_vector"] = vector
        item["rerank_adjustment"] = adjustment
        item["rerank_reason_codes"] = codes
        if adjustment != 0.0:
            adjusted += 1
    if applied:
        items.sort(key=lambda item: _f(item.get(score_key)) + _f(item.get("rerank_adjustment")), reverse=True)
    return {
        "arm": arm,
        "applied": applied,
        "model_version": str((model or {}).get("model_version") or ""),
        "candidates_adjusted": adjusted,
        "display_note": DISPLAY_NOTE if applied else "",
        "provider_calls": False,
    }


def breakdown_entry(item: dict[str, Any], arm: str, applied: bool, model_version: str) -> dict[str, Any]:
    return {
        "adjustment": _f(item.get("rerank_adjustment")),
        "reason_codes": list(item.get("rerank_reason_codes") or []),
        "arm": arm,
        "applied": bool(applied),
        "model_version": model_version,
        "feature_keys_version": FEATURE_KEYS_VERSION,
    }


# ── 快照落库 ────────────────────────────────────────────────────────────


def write_snapshot(
    *,
    recommendation_id: int,
    run_id: int | None,
    kol_pool_id: int | None,
    launch_id: int | None,
    staff_id: int | None,
    engine: str,
    arm: str,
    vector: dict[str, float],
    base_score: Any,
    adjustment: float,
    applied: bool,
    reason_codes: list[str],
    model_version: str,
) -> bool:
    """幂等:一条推荐一行快照(已存在即跳过)。缺表(PG 未跑 288)诚实返回 False。"""
    rec_id = int(recommendation_id or 0)
    if rec_id <= 0 or not tables_ready():
        return False
    conn = get_conn()
    existing = conn.execute(
        f"SELECT id FROM {SNAPSHOT_TABLE} WHERE recommendation_id=?", (rec_id,)
    ).fetchone()
    if existing:
        return False
    now = utcnow_iso()
    conn.execute(
        f"""
        INSERT INTO {SNAPSHOT_TABLE}
            (recommendation_id, run_id, kol_pool_id, launch_id, staff_id, engine, arm,
             feature_keys_version, feature_vector, base_score, rerank_adjustment, rerank_applied,
             rerank_reason_codes, rerank_model_version, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            rec_id,
            int(run_id) if run_id else None,
            int(kol_pool_id) if kol_pool_id else None,
            int(launch_id) if launch_id else None,
            int(staff_id) if staff_id else None,
            str(engine or ""),
            str(arm or ARM_OFF),
            FEATURE_KEYS_VERSION,
            dumps(vector),
            _f(base_score),
            _f(adjustment),
            bool(applied),
            dumps(list(reason_codes or [])[:MAX_REASON_CODES]),
            str(model_version or ""),
            now,
            now,
        ),
    )
    conn.commit()
    return True


def write_snapshots_for_items(
    items: list[dict[str, Any]],
    *,
    engine: str,
    arm: str,
    applied: bool,
    model_version: str,
    staff_id: int | None,
    run_id: int | None,
    launch_id: int | None,
    rec_id_of: Any,
) -> dict[str, int]:
    """批量落快照(单条失败只告警计数,绝不阻断引擎主流程)。"""
    written = 0
    skipped = 0
    failed = 0
    for item in items:
        rec_id = int(rec_id_of(item) or 0)
        if rec_id <= 0:
            skipped += 1
            continue
        try:
            ok = write_snapshot(
                recommendation_id=rec_id,
                run_id=run_id,
                kol_pool_id=item.get("kol_pool_id") or item.get("id"),
                launch_id=launch_id,
                staff_id=staff_id,
                engine=engine,
                arm=arm,
                vector=item.get("rerank_vector") or {},
                base_score=item.get("score"),
                adjustment=_f(item.get("rerank_adjustment")),
                applied=applied,
                reason_codes=list(item.get("rerank_reason_codes") or []),
                model_version=model_version,
            )
        except Exception:
            failed += 1
            logger.warning("rerank_shadow.snapshot_failed rec_id=%s", rec_id, exc_info=True)
            continue
        if ok:
            written += 1
        else:
            skipped += 1
    return {"written": written, "skipped": skipped, "failed": failed}

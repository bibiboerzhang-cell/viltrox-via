"""market_brain/bandit.py — bandit-lite arm 权重纯函数(C4 W10 放权能力,deploy dark)。

作用域:GTM 探索/利用的最小可证据化实验框架。arm = 一组可对比的投放组合
  (sku x market x channel x content_angle x creator_or_dealer_type),每条 arm 累积
  「归一化奖励」的均值与样本数,供后续按 UCB 口径给出「下一步探索谁」的纯建议。

红线(W10 开闸前一律构建不启用):
  - 本模块只做统计与建议,绝不自动执行任何外部动作(不发外联/不花钱/不改项目态);
  - 权重更新只按人工/回填账本喂进来的 outcome 做增量统计,LLM 绝不直接改权重;
  - select_arm 是纯建议(reason=explore|exploit),不落库、不触发任何调用方;
  - 零触 viltrox_fit_score、零碰 rule_v0;落账仅进本域新表 vkpi_bandit_arms(迁移 225)。

compat 约定:SQL 占位符用 ?;SQL 零 percent 字面量(不用 LIKE);函数内懒 import
get_conn / table_exists;表未建诚实降级不炸(返回 ok=False + reason,绝不静默)。
"""
from __future__ import annotations

import math
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

TABLE = "vkpi_bandit_arms"

# arm 组合维度(顺序即 arm_key 的拼接顺序,稳定可复算)。
ARM_DIMENSIONS: tuple[str, ...] = (
    "product_sku",
    "market",
    "channel",
    "content_angle",
    "creator_or_dealer_type",
)

# 归一化奖励各分量权重(和为 1.0,故加权结果天然落在 0-1)。
#   posted            发布达成(是否真的发出去)
#   qualified_views   合格观看(有效触达量)
#   comments          评论互动
#   clicks            点击(意向)
#   orders_or_reply   下单或有效回复(最强转化信号,权重最高)
#   margin            毛利贡献(商业结果)
REWARD_WEIGHTS: dict[str, float] = {
    "posted": 0.10,
    "qualified_views": 0.20,
    "comments": 0.15,
    "clicks": 0.20,
    "orders_or_reply": 0.25,
    "margin": 0.10,
}

# 未探索 arm 在 select_arm 里给的探索加成基准(取奖励量纲上限 1.0,
# 乘 explore_rate 后与 mean_reward 同量纲比较,explore_rate 越大越倾向探索新组合)。
UNSEEN_EXPLORE_BONUS = 1.0

DEFAULT_EXPLORE_RATE = 0.10
DEFAULT_LOAD_LIMIT = 200
MAX_LOAD_LIMIT = 2000


# ── 小工具(容错强制转换,与邻域同款口径)─────────────────────────────


def _clamp01(value: Any) -> float:
    """把任意输入压成 [0,1] 浮点;None/非数/异常 → 0.0。"""
    try:
        if value is None:
            return 0.0
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        num = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(num) or math.isinf(num):
        return 0.0
    if num < 0.0:
        return 0.0
    if num > 1.0:
        return 1.0
    return num


def _int0(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _float0(value: Any) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(num) or math.isinf(num):
        return 0.0
    return num


def _text(value: Any, limit: int = 120) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


# ── arm key 与奖励 ───────────────────────────────────────────────────


def arm_key(
    sku: str = "",
    market: str = "",
    channel: str = "",
    content_angle: str = "",
    creator_or_dealer_type: str = "",
) -> str:
    """按固定维度顺序拼稳定 arm_key(维度内小写归一,'|' 分隔,空维度占位不塌位)。"""
    parts = [
        _text(sku).lower(),
        _text(market).lower(),
        _text(channel).lower(),
        _text(content_angle).lower(),
        _text(creator_or_dealer_type).lower(),
    ]
    return "|".join(parts)


def normalized_reward(outcome: dict[str, Any] | None) -> float:
    """把一次 outcome 折算成 0-1 归一化奖励(六分量加权,缺项按 0)。

    每个分量先压到 [0,1](posted 布尔按 1/0),再按 REWARD_WEIGHTS 加权求和;
    权重和为 1.0 故结果落在 [0,1]。纯函数:同输入恒同输出,零副作用。
    """
    payload = outcome or {}
    total = 0.0
    for key, weight in REWARD_WEIGHTS.items():
        total += weight * _clamp01(payload.get(key))
    # 权重和恒为 1.0,total 已在 [0,1];末端再夹一次防浮点越界。
    return _clamp01(total)


def update_arm_weight(prior: dict[str, Any] | None, outcome: dict[str, Any] | None) -> dict[str, Any]:
    """增量更新 arm 的样本数与均值奖励(在线均值,纯统计不做任何执行)。

    prior 取 {n, mean_reward};返回 {n, mean_reward, last_reward}。
    new_mean = mean + (reward - mean) / new_n —— O(1) 在线均值,数值稳定。
    """
    base = prior or {}
    n = _int0(base.get("n"))
    mean = _float0(base.get("mean_reward"))
    reward = normalized_reward(outcome)
    new_n = n + 1
    new_mean = mean + (reward - mean) / new_n
    return {"n": new_n, "mean_reward": new_mean, "last_reward": reward}


def _arm_view(arm: dict[str, Any]) -> dict[str, Any]:
    return {
        "arm_key": _text(arm.get("arm_key"), 300) or arm_key(
            arm.get("product_sku", ""),
            arm.get("market", ""),
            arm.get("channel", ""),
            arm.get("content_angle", ""),
            arm.get("creator_or_dealer_type", ""),
        ),
        "n": _int0(arm.get("n")),
        "mean_reward": _clamp01(arm.get("mean_reward")),
    }


def select_arm(arms: list[dict[str, Any]] | None, explore_rate: float = DEFAULT_EXPLORE_RATE) -> dict[str, Any] | None:
    """给出「下一步该探索/利用哪条 arm」的纯建议(UCB-lite,确定性,不落库不执行)。

    explore_rate <= 0 → 纯利用:选 mean_reward 最高者(reason=exploit)。
    explore_rate > 0  → mean_reward + explore_rate * bonus 最高者;
      已探索 arm 的 bonus = sqrt(2*ln(N+1)/n),未探索(n=0)给 UNSEEN_EXPLORE_BONUS。
      选中者若非 mean_reward 最高者则 reason=explore,否则 exploit。
    平手按 arm_key 升序破,保证确定性(hermetic 可测)。空池 → None。
    返回 {arm_key, reason, score, mean_reward, n}。
    """
    pool = [_arm_view(a) for a in (arms or []) if isinstance(a, dict)]
    if not pool:
        return None

    rate = _float0(explore_rate)
    exploit_best = max(pool, key=lambda a: (a["mean_reward"], a["arm_key"]))

    if rate <= 0:
        chosen = exploit_best
        reason = "exploit"
        score = chosen["mean_reward"]
    else:
        total_n = sum(a["n"] for a in pool)
        log_term = math.log(total_n + 1)

        def _score(a: dict[str, Any]) -> float:
            if a["n"] <= 0:
                bonus = UNSEEN_EXPLORE_BONUS
            else:
                bonus = math.sqrt(2.0 * log_term / a["n"])
            return a["mean_reward"] + rate * bonus

        chosen = max(pool, key=lambda a: (_score(a), a["arm_key"]))
        score = _score(chosen)
        reason = "exploit" if chosen["arm_key"] == exploit_best["arm_key"] else "explore"

    return {
        "arm_key": chosen["arm_key"],
        "reason": reason,
        "score": score,
        "mean_reward": chosen["mean_reward"],
        "n": chosen["n"],
    }


# ── 可选落账(纯统计入本域新表,绝不触发任何执行)─────────────────────


def load_arms(limit: int = DEFAULT_LOAD_LIMIT) -> list[dict[str, Any]]:
    """按 mean_reward 降序读出已记录 arm(缺表/异常 → 空列表,诚实降级)。"""
    capped = max(1, min(MAX_LOAD_LIMIT, _int0(limit) or DEFAULT_LOAD_LIMIT))
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists(TABLE):
            return []
        rows = get_conn().execute(
            f"SELECT arm_key, product_sku, market, channel, content_angle, "
            f"creator_or_dealer_type, n, mean_reward, last_reward "
            f"FROM {TABLE} ORDER BY mean_reward DESC, arm_key ASC LIMIT ?",
            (capped,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.warning("bandit.load_arms failed", exc_info=True)
        return []


def record_arm_reward(
    sku: str = "",
    market: str = "",
    channel: str = "",
    content_angle: str = "",
    creator_or_dealer_type: str = "",
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把一次 outcome 的归一化奖励幂等增量落账到 vkpi_bandit_arms(仅统计)。

    首见 arm → n=1/mean=reward;既有 arm → n+1 且在线均值增量(全在 SQL 里算)。
    返回 {ok, arm_key, reward}；表未建 → {ok: False, reason: 'table_missing'}。
    红线:只写统计三元(n/mean_reward/last_reward),绝不据此自动执行任何动作。
    """
    key = arm_key(sku, market, channel, content_angle, creator_or_dealer_type)
    reward = normalized_reward(outcome)
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists(TABLE):
            return {"ok": False, "arm_key": key, "reward": reward, "reason": "table_missing"}

        conn = get_conn()
        conn.execute(
            f"""
            INSERT INTO {TABLE} (
                arm_key, product_sku, market, channel, content_angle,
                creator_or_dealer_type, n, mean_reward, last_reward, updated_at
            ) VALUES (?,?,?,?,?, ?, 1, ?, ?, NOW())
            ON CONFLICT (arm_key) DO UPDATE SET
                n = {TABLE}.n + 1,
                mean_reward = {TABLE}.mean_reward
                    + (excluded.last_reward - {TABLE}.mean_reward) / ({TABLE}.n + 1),
                last_reward = excluded.last_reward,
                updated_at = NOW()
            """,
            (
                key,
                _text(sku), _text(market), _text(channel), _text(content_angle),
                _text(creator_or_dealer_type),
                reward, reward,
            ),
        )
        conn.commit()
        return {"ok": True, "arm_key": key, "reward": reward}
    except Exception:
        logger.warning("bandit.record_arm_reward failed arm_key=%s", key, exc_info=True)
        return {"ok": False, "arm_key": key, "reward": reward, "reason": "db_error"}

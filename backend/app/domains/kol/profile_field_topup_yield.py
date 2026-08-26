"""车道 1 的**真实产出回读**:按平台算「花出去的抓取,到底补上了几个字段」。

为什么单独一个文件
------------------
``profile_field_topup_enqueue`` 回答的是「这一次要花多少钱、预计能换回几个字段」——
那是**预期**,分母来自 ``MEASURED_FILL_RATE``(prod 2026-08-25 的一次横截面观测)。
本模块回答的是完全不同的一个问题:**已经花掉的那些抓取,事后真的补上了吗**。

用户裁令是三条腿(YouTube / TikTok / Instagram)全开,但要求按平台分别记账,理由是
实测 Instagram 两项填充率都是 0.0(n=145)。裁令同时写明:跑一周后拿真实数据决定
要不要关掉某条腿。要能做这个决定,就必须有一份「不是预估、而是真发生了什么」的账 ——
就是下面这支纯 SELECT。

口径三条,刻意写死,免得日后被读成别的意思:

* **只认自己花的钱**:``payload ->> 'source' = TOPUP_SOURCE``。别的管线(懒回填、内容
  监控、手动深抓)也会把同一个人的 country/language 补上,把那些算进来会让本车道的
  产出率虚高,进而做出「Instagram 其实还行」的错误决定。
* **只看窗口内入队的那一批**,并按 ``kol_pool_id`` 去重:同一个人在窗口内被排过两次队
  只算一次抓取,否则分母虚大、产出率虚低。
* **filled 是当前状态,不是因果证明**。字段在这段时间里也可能被别的管线补上。所以返回
  的键叫 ``filled_now`` 而不是 ``filled_by_us``,``note`` 里也照直说明。宁可让人看见
  口径的边界,也不假装这是一个干净的归因。

零成本:全程只有一条 SELECT,不触发任何抓取、不调用任何模型、零写库。
red line:零写 ``viltrox_fit_score``,零改 rule_v0,不放宽任何质量口径。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol.profile_field_topup_enqueue import TOPUP_FIELDS, TOPUP_SOURCE

logger = get_logger(__name__)

#: 回读窗口默认 7 天 —— 与用户「跑一周后再决定」的裁令对齐。
DEFAULT_WINDOW_DAYS = 7

#: 窗口硬顶,防一次拉穿整张 apify_jobs。
MAX_WINDOW_DAYS = 90

YIELD_SCHEMA = "field_topup_yield_v1"


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _rate(filled: int, fetched: int) -> float | None:
    """产出率。分母为 0 时返回 None,**不返回 0.0** —— 「没抓过」和「抓了但没补上」
    是两个完全不同的结论,混成一个 0 会让人把没跑过的腿当成没产出的腿关掉。"""

    return round(filled / fetched, 3) if fetched > 0 else None


def field_topup_yield_by_platform(
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, Any]:
    """按平台回读本车道的真实产出。纯 SELECT,零成本。

    返回 ``by_platform[platform]`` 每项含:
      ``fetches``      窗口内本车道为该平台入队的去重人数
      ``filled_now``   其中 country / language 现在已经有值的人数(逐字段 + 至少一项)
      ``fill_rate``    上面两者之比;``fetches`` 为 0 时是 ``None`` 而不是 0.0
    """

    days = max(1, min(_int(window_days, DEFAULT_WINDOW_DAYS), MAX_WINDOW_DAYS))
    base = {
        "schema": YIELD_SCHEMA,
        "window_days": days,
        "fetches": 0,
        "by_platform": {},
        "note": (
            "只统计「按需补数据」这条腿自己排的队,按人去重。字段是否已补以当前库值为准 —— "
            "同期其他管线也可能补上同一个字段,因此这是产出观测,不是严格归因。"
        ),
    }
    try:
        rows = get_conn().execute(
            """
            SELECT lower(COALESCE(pool.platform, '')) AS platform,
                   COUNT(*)                            AS fetches,
                   COUNT(NULLIF(TRIM(COALESCE(pool.country, '')), ''))  AS country_filled,
                   COUNT(NULLIF(TRIM(COALESCE(pool.language, '')), '')) AS language_filled,
                   COUNT(*) FILTER (
                       WHERE TRIM(COALESCE(pool.country, '')) <> ''
                          OR TRIM(COALESCE(pool.language, '')) <> ''
                   ) AS any_filled
              FROM (
                    SELECT DISTINCT payload ->> 'kol_pool_id' AS kol_pool_id
                      FROM apify_jobs
                     WHERE job_type = 'kol_profile_deep_crawl'
                       AND payload ->> 'source' = ?
                       AND created_at >= NOW() - make_interval(days => ?)
                   ) AS job
              JOIN vkpi_kol_pool AS pool
                ON pool.id::text = job.kol_pool_id
             GROUP BY lower(COALESCE(pool.platform, ''))
             ORDER BY lower(COALESCE(pool.platform, ''))
            """,
            (TOPUP_SOURCE, days),
        ).fetchall()
    except Exception as exc:
        # 失败方向安全:读不出来就诚实说读不出来,绝不返回一个看起来像 0 产出的空账。
        logger.warning(
            "field_topup_yield_probe_failed window_days=%s reason=%s",
            days, str(exc)[:200], exc_info=True,
        )
        return {**base, "status": "probe_failed", "reason": "topup_yield_probe_failed"}

    by_platform: dict[str, dict[str, Any]] = {}
    total = 0
    for raw in rows:
        row = dict(raw)
        platform = str(row.get("platform") or "").strip().lower() or "unknown"
        fetches = _int(row.get("fetches"))
        total += fetches
        filled = {
            "country": _int(row.get("country_filled")),
            "language": _int(row.get("language_filled")),
        }
        by_platform[platform] = {
            "fetches": fetches,
            "filled_now": {field: filled.get(field, 0) for field in TOPUP_FIELDS},
            "filled_now_any": _int(row.get("any_filled")),
            "fill_rate": {
                field: _rate(filled.get(field, 0), fetches) for field in TOPUP_FIELDS
            },
            "fill_rate_any": _rate(_int(row.get("any_filled")), fetches),
        }
    return {
        **base,
        "status": "ok",
        "fetches": total,
        "by_platform": dict(sorted(by_platform.items())),
    }


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "MAX_WINDOW_DAYS",
    "YIELD_SCHEMA",
    "field_topup_yield_by_platform",
]

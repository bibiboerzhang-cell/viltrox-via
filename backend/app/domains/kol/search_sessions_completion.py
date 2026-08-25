"""会话完成度的唯一真源:一处判形态,写端落 result_summary,门面直接念。

用户诉求(2026-08-25):
    「29/30 完成」和「1/30 完成」和「一条结果都没有」现在共用一个词 partial,
    看不出差别。要能说人话:「30 人已出结果,1 人资料补全中」。

线上只读探针坐实的三件事(prod a05e48dd3,2026-08-25):

1. ``status='partial'`` 的 104 个会话里,真相分五种:
       空会话(0 候选)          13
       全无结果(候选>0,ready=0) 47
       真部分(0<ready<total)    41
       近乎完成(ready>=90%)      2
       其实全好(ready=total)     1
   同一个 ``partial`` 盖住了从「什么都没有」到「其实已经全好」的整个区间。

2. summary 里既有的三个可分辨字段全都靠不住 —— 覆盖率不足且会漂:
       result_state            78/104 有;其中 3 个标 ready 实际 0 结果、
                               22 个标 partial 实际 0 结果、1 个标 empty 实际全好
       counts                  38/104 有
       progress                45/104 有
       item_status             77/104 有,而且是个标量字符串
                               (#1144 有 1 条未完成却写 "ready")
   它们是各写端各写各的快照,写完就不再跟库里的行对账。

3. 反向也漏:``status='ready'`` 的 1034 个会话里,仍有 10 个藏着未完成的行。

所以本模块的口径是**每次持久化时从行里现算**,与 ``origin_breakdown`` 同一处
落库(见 ``search_sessions_items._update_session``),不再让读端自己数、也不留
可漂的快照。``status`` 语义完全不动(不新增取值、不碰迁移 103 的 CHECK),
形态是 ``status`` 之外的**正交**维度。
"""
from __future__ import annotations

from typing import Any, Mapping

from app.domains.kol.search_sessions_serde import _int_or_none, _text


SESSION_COMPLETION_SCHEMA = "kol_search_session_completion_v1"

# ── 结果形态(与 status 正交;五种互斥且穷尽)───────────────────────────────
# 判定顺序即下面的书写顺序,``classify_completion_shape`` 按序返回第一个命中的。
COMPLETION_SHAPE_EMPTY_SESSION = "empty_session"        # 一条候选都没落库
COMPLETION_SHAPE_ALL_COMPLETE = "all_complete"          # 落库的候选全部出了结果
COMPLETION_SHAPE_PARTIAL = "partially_complete"         # 有出结果的,也有没走完的
COMPLETION_SHAPE_CANDIDATES_ONLY = "candidates_only"    # 候选已列出,一条都没被请求推进
COMPLETION_SHAPE_NO_RESULTS = "no_results"              # 推进过,但一条结果都没拿到

COMPLETION_SHAPE_VALUES: tuple[str, ...] = (
    COMPLETION_SHAPE_EMPTY_SESSION,
    COMPLETION_SHAPE_ALL_COMPLETE,
    COMPLETION_SHAPE_PARTIAL,
    COMPLETION_SHAPE_CANDIDATES_ONLY,
    COMPLETION_SHAPE_NO_RESULTS,
)

# ── item.status → 桶。取值与迁移 103 + 293 的 CHECK 字面同步 ────────────────
# ``candidate`` 单独成桶是关键:召回会话把人选写成 identified/matched 就停在那里
# 等人挑(prod 全库 832 + 1094 行),那是**正常的等人**,不是「卡住」。把它和
# partial 混成一桶会让几乎每个会话都显示「未完成」,等于没说。
COMPLETION_BUCKET_DONE = "done"
COMPLETION_BUCKET_STUCK = "stuck"
COMPLETION_BUCKET_IN_FLIGHT = "in_flight"
COMPLETION_BUCKET_FAILED = "failed"
COMPLETION_BUCKET_CANDIDATE = "candidate"

COMPLETION_BUCKET_VALUES: tuple[str, ...] = (
    COMPLETION_BUCKET_DONE,
    COMPLETION_BUCKET_STUCK,
    COMPLETION_BUCKET_IN_FLIGHT,
    COMPLETION_BUCKET_FAILED,
    COMPLETION_BUCKET_CANDIDATE,
)

_STATUS_BUCKETS: dict[str, str] = {
    "ready": COMPLETION_BUCKET_DONE,
    "already_analyzed": COMPLETION_BUCKET_DONE,
    "partial": COMPLETION_BUCKET_STUCK,
    "queued": COMPLETION_BUCKET_IN_FLIGHT,
    "running": COMPLETION_BUCKET_IN_FLIGHT,
    "already_queued": COMPLETION_BUCKET_IN_FLIGHT,
    "failed": COMPLETION_BUCKET_FAILED,
    "planned": COMPLETION_BUCKET_CANDIDATE,
    "identified": COMPLETION_BUCKET_CANDIDATE,
    "matched": COMPLETION_BUCKET_CANDIDATE,
    "skipped": COMPLETION_BUCKET_CANDIDATE,
    "unknown": COMPLETION_BUCKET_CANDIDATE,
}

# stage 是内部流水线阶段名,门面不许直接念(禁术语)。这里给中文对照,
# 让「卡在哪一步」可以说人话。取值与迁移 103 + 293 的 stage CHECK 同步。
COMPLETION_STAGE_LABELS: dict[str, str] = {
    "identified": "刚找到",
    "profile": "基础资料",
    "evidence": "作品取证",
    "analysis": "内容分析",
    "summary": "资料补全",
    "qualified": "初筛通过",
}


def completion_bucket(item_status: Any) -> str:
    """item.status → 桶。库里出现过但表里没登记的取值一律归 candidate(保守:
    不把没见过的状态谎报成「已完成」,也不谎报成「卡住」去制造假告警)。"""
    return _STATUS_BUCKETS.get(_text(item_status).lower(), COMPLETION_BUCKET_CANDIDATE)


def stage_label(stage: Any) -> str:
    """stage → 中文人话。未登记的 stage 原样返回,绝不猜也绝不吞。"""
    key = _text(stage).lower()
    return COMPLETION_STAGE_LABELS.get(key, key or "未知步骤")


def classify_completion_shape(counts: Mapping[str, int]) -> str:
    """按桶计数判形态。互斥穷尽,顺序即优先级。"""
    total = sum(int(counts.get(bucket) or 0) for bucket in COMPLETION_BUCKET_VALUES)
    done = int(counts.get(COMPLETION_BUCKET_DONE) or 0)
    if total <= 0:
        return COMPLETION_SHAPE_EMPTY_SESSION
    if done >= total:
        return COMPLETION_SHAPE_ALL_COMPLETE
    if done > 0:
        return COMPLETION_SHAPE_PARTIAL
    unfinished = sum(
        int(counts.get(bucket) or 0)
        for bucket in (
            COMPLETION_BUCKET_STUCK,
            COMPLETION_BUCKET_IN_FLIGHT,
            COMPLETION_BUCKET_FAILED,
        )
    )
    if unfinished <= 0:
        return COMPLETION_SHAPE_CANDIDATES_ONLY
    return COMPLETION_SHAPE_NO_RESULTS


def completion_headline(counts: Mapping[str, int], shape: str) -> str:
    """一句中文事实,门面直接念。只报数,不评价,不加内部术语。"""
    done = int(counts.get(COMPLETION_BUCKET_DONE) or 0)
    stuck = int(counts.get(COMPLETION_BUCKET_STUCK) or 0)
    in_flight = int(counts.get(COMPLETION_BUCKET_IN_FLIGHT) or 0)
    failed = int(counts.get(COMPLETION_BUCKET_FAILED) or 0)
    candidate = int(counts.get(COMPLETION_BUCKET_CANDIDATE) or 0)
    if shape == COMPLETION_SHAPE_EMPTY_SESSION:
        return "本次没有找到任何人选"
    if shape == COMPLETION_SHAPE_ALL_COMPLETE:
        return f"{done} 人全部完成"
    if shape == COMPLETION_SHAPE_CANDIDATES_ONLY:
        return f"{candidate} 人已列出,尚未开始补全资料"
    parts: list[str] = []
    if done:
        parts.append(f"{done} 人已出结果")
    if stuck:
        parts.append(f"{stuck} 人资料补全中")
    if in_flight:
        parts.append(f"{in_flight} 人处理中")
    if failed:
        parts.append(f"{failed} 人没能完成")
    if candidate:
        parts.append(f"{candidate} 人待挑选")
    return ",".join(parts) if parts else "本次没有找到任何人选"


def completion_from_rows(rows: Any) -> dict[str, Any]:
    """把 ``(status, stage, count)`` 三元组聚合成完成度事实。纯函数,便于测试。

    ``rows`` 是可迭代的三元组序列;计数非正整数一律按 0 处理(不猜、不抛)。
    """
    counts = {bucket: 0 for bucket in COMPLETION_BUCKET_VALUES}
    by_status: dict[str, int] = {}
    stuck_by_stage: dict[str, int] = {}
    in_flight_by_stage: dict[str, int] = {}
    for raw_status, raw_stage, raw_count in rows or []:
        count = _int_or_none(raw_count) or 0
        if count <= 0:
            continue
        status = _text(raw_status).lower() or "unknown"
        stage = _text(raw_stage).lower() or "identified"
        bucket = completion_bucket(status)
        counts[bucket] += count
        by_status[status] = by_status.get(status, 0) + count
        if bucket == COMPLETION_BUCKET_STUCK:
            stuck_by_stage[stage] = stuck_by_stage.get(stage, 0) + count
        elif bucket == COMPLETION_BUCKET_IN_FLIGHT:
            in_flight_by_stage[stage] = in_flight_by_stage.get(stage, 0) + count

    total = sum(counts.values())
    shape = classify_completion_shape(counts)
    return {
        "schema": SESSION_COMPLETION_SCHEMA,
        "shape": shape,
        "total": total,
        "ready": counts[COMPLETION_BUCKET_DONE],
        "stuck": counts[COMPLETION_BUCKET_STUCK],
        "in_flight": counts[COMPLETION_BUCKET_IN_FLIGHT],
        "failed": counts[COMPLETION_BUCKET_FAILED],
        "candidate": counts[COMPLETION_BUCKET_CANDIDATE],
        "by_status": dict(sorted(by_status.items())),
        # 卡住的活儿分别卡在哪一步 —— 门面不用自己数,也不用去翻 item 行。
        "stuck_by_stage": dict(sorted(stuck_by_stage.items())),
        "stuck_by_stage_label": {
            stage_label(stage): count for stage, count in sorted(stuck_by_stage.items())
        },
        "in_flight_by_stage": dict(sorted(in_flight_by_stage.items())),
        "headline": completion_headline(counts, shape),
    }


def session_completion_breakdown(conn: Any, session_id: int) -> dict[str, Any]:
    """按会话直接从库里聚合完成度 —— 权威口径,前端不用自己数。

    一条 GROUP BY,走 ``idx_vkpi_kol_search_session_items_session_origin``
    (迁移 301)的 ``session_id`` 前缀。聚合列都带 ``AS`` 别名(compat 适配器
    读回按名取值)。
    """
    rows = conn.execute(
        """
        SELECT status AS item_status,
               stage AS item_stage,
               COUNT(*) AS item_count
        FROM vkpi_kol_search_session_items
        WHERE session_id=?
        GROUP BY status, stage
        """,
        (int(session_id),),
    ).fetchall()
    triples = []
    for raw in rows or []:
        row = dict(raw)
        triples.append((row.get("item_status"), row.get("item_stage"), row.get("item_count")))
    return completion_from_rows(triples)


__all__ = [
    "COMPLETION_BUCKET_CANDIDATE",
    "COMPLETION_BUCKET_DONE",
    "COMPLETION_BUCKET_FAILED",
    "COMPLETION_BUCKET_IN_FLIGHT",
    "COMPLETION_BUCKET_STUCK",
    "COMPLETION_BUCKET_VALUES",
    "COMPLETION_SHAPE_ALL_COMPLETE",
    "COMPLETION_SHAPE_CANDIDATES_ONLY",
    "COMPLETION_SHAPE_EMPTY_SESSION",
    "COMPLETION_SHAPE_NO_RESULTS",
    "COMPLETION_SHAPE_PARTIAL",
    "COMPLETION_SHAPE_VALUES",
    "COMPLETION_STAGE_LABELS",
    "SESSION_COMPLETION_SCHEMA",
    "classify_completion_shape",
    "completion_bucket",
    "completion_from_rows",
    "completion_headline",
    "session_completion_breakdown",
    "stage_label",
]

"""产量预估的**取数层**:纯 SELECT / COUNT,零 provider、零 LLM、零写库(2026-08-26)。

背景(线上只读取证,池 2036 人):操作员勾「美国 + 英语 + 5 万粉 + 生活方式」
拿到 0 个人,而漏斗每一级都只在事后才看得见:160 → 75 → 49 → 6 → 0。没有任何一处
在**搜之前**告诉他「这个组合只剩 6 个人」。全仓 grep 也只有**花钱**的预估(Apify 报价),
没有一处估「这个组合能出几个人」。

本模块只负责把估数所需的最少事实取回来,判定与阶梯算法在 ``pool_yield_estimate``:

* :func:`load_group_counts` —— **一条 GROUP BY COUNT**,把全池压成几百个
  「(平台, 国家原值, 语言原值, 粉丝下限档, 粉丝上限档) -> 人数」分组。
  本地实测 1787 人压成 367 组、执行 3.6 ms。没勾垂类时只跑这一条。
* :func:`load_row_keys` —— 勾了垂类才走:同样的键,但**逐人**返回(垂类是逐人判的,
  压不进分组)。本地 1787 行 16.6 ms。
* :func:`load_vertical_inputs` —— 只为**真正需要判垂类的那批 id** 取文本列与作品标题。
  取谁由上层按阶梯算出来,通常远小于全池。

三条硬约束:

1. **零成本**:只发 SELECT,绝不调 provider、绝不调 LLM、绝不写库。估一次的代价必须
   远小于搜一次,否则这个功能没有意义。
2. **口径与搜索同源**:候选全集沿用搜索侧的 ``vkpi_kol_pool WHERE duplicate_of_id IS NULL``
   (见 ``profile_recall_precision``),不另立一套「池子」。
3. **只估库内**:联网发现能再补多少人**不在**本模块的射程内 —— 那要花钱且不可预测。
   上层必须把这句话如实透给操作员。

SQL 兼容:占位符 ``?``、不写字面量 ``%`` 也不用 ``LIKE``、聚合带 ``AS`` 别名、
``get_conn()`` 不当上下文管理器。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime


logger = get_logger(__name__)

#: 候选全集 —— 与搜索侧硬筛的 FROM/WHERE 逐字节同源。
POOL_SCOPE_SQL = "FROM vkpi_kol_pool p WHERE p.duplicate_of_id IS NULL"

#: 没勾粉丝下限/上限时绑进 SQL 的哨兵值:人人过下限、人人过上限。
#: 档位仍照常算出来,由上层按「这一维激活了没有」决定要不要采信。
FOLLOWERS_MIN_SENTINEL = 0
FOLLOWERS_MAX_SENTINEL = 9223372036854775807

#: id IN (...) 的单批上限,防止超长参数列表。
_ID_CHUNK = 900

#: 分组键的列(顺序 == GROUP BY 序号)。
#:
#: 平台 / 国家 / 语言三列一律取**原值**,归一化留到上层做,原因有二:
#:
#: 1. **要能复刻召回腿。** 召回腿的硬筛写的是 ``LOWER(COALESCE(p.country,''))``,
#:    没有 btrim。库里带首尾空白的值在那边就是捞不出来。这里若先在 SQL 里 btrim,
#:    就会估出一个搜索给不出的人 —— 正是要治的病。原值取回来,上层才能
#:    「按判定口径 btrim 再 lower」与「按取数腿口径只 lower」两笔账分开算。
#: 2. **btrim 只在该 btrim 的一侧做。** 比对时被 btrim 的是**筛选值**那一侧
#:    (见 ``pool_yield_recall_parity.recall_sql_values``),不是库里的原值。
#:
#: 另:``BTRIM`` 是 Postgres 函数,本仓 sqlite 运行时没有,写进 SQL 会当场炸掉。
KEY_COLUMNS: tuple[str, ...] = (
    "platform_raw",
    "country_raw",
    "language_raw",
    "followers_min_state",
    "followers_max_state",
)

_KEY_SELECT = """
        COALESCE(p.platform, '') AS platform_raw,
        COALESCE(p.country, '') AS country_raw,
        COALESCE(p.language, '') AS language_raw,
        CASE WHEN p.followers IS NULL THEN 'unknown'
             WHEN p.followers >= ? THEN 'pass' ELSE 'mismatch' END AS followers_min_state,
        CASE WHEN p.followers IS NULL THEN 'unknown'
             WHEN p.followers <= ? THEN 'pass' ELSE 'mismatch' END AS followers_max_state
"""


def _followers_params(followers_min: Any, followers_max: Any) -> tuple[int, int]:
    """把可能缺席的粉丝闸收敛成两个必然可绑的整数。"""
    try:
        low = max(0, int(followers_min)) if followers_min not in (None, "") else FOLLOWERS_MIN_SENTINEL
    except (TypeError, ValueError):
        low = FOLLOWERS_MIN_SENTINEL
    try:
        high = max(0, int(followers_max)) if followers_max not in (None, "") else FOLLOWERS_MAX_SENTINEL
    except (TypeError, ValueError):
        high = FOLLOWERS_MAX_SENTINEL
    return low, high


def _key_of(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(row.get(column) or "") for column in KEY_COLUMNS)


def load_group_counts(
    conn: Any,
    *,
    followers_min: Any = None,
    followers_max: Any = None,
) -> tuple[list[tuple[tuple[str, ...], int]], int]:
    """一条 GROUP BY COUNT:返回 ``([(键, 人数), ...], 全池人数)``。

    这是「便宜」这条红线的落点 —— 库里有多少人都只扫一遍、只回来几百行。
    """
    rows = conn.execute(
        f"SELECT {_KEY_SELECT}, COUNT(*) AS group_count {POOL_SCOPE_SQL} GROUP BY 1, 2, 3, 4, 5",
        _followers_params(followers_min, followers_max),
    ).fetchall()
    facts: list[tuple[tuple[str, ...], int]] = []
    total = 0
    for raw in rows:
        item = dict(raw)
        count = int(item.get("group_count") or 0)
        if count <= 0:
            continue
        facts.append((_key_of(item), count))
        total += count
    return facts, total


def load_row_keys(
    conn: Any,
    *,
    followers_min: Any = None,
    followers_max: Any = None,
) -> list[tuple[int, tuple[str, ...]]]:
    """逐人返回同一套键。**只在勾了垂类时**才走这条 —— 垂类判定是逐人的,压不进分组。"""
    rows = conn.execute(
        f"SELECT p.id AS kol_pool_id, {_KEY_SELECT} {POOL_SCOPE_SQL}",
        _followers_params(followers_min, followers_max),
    ).fetchall()
    out: list[tuple[int, tuple[str, ...]]] = []
    for raw in rows:
        item = dict(raw)
        try:
            pool_id = int(item.get("kol_pool_id"))
        except (TypeError, ValueError):
            continue
        out.append((pool_id, _key_of(item)))
    return out


def _chunks(ids: Sequence[int]) -> Iterable[tuple[int, ...]]:
    for start in range(0, len(ids), _ID_CHUNK):
        yield tuple(ids[start : start + _ID_CHUNK])


def load_vertical_inputs(
    conn: Any,
    pool_ids: Sequence[int],
) -> tuple[dict[int, tuple[dict[str, Any], dict[str, Any]]], int]:
    """为指定 id 取「判垂类要用的那几列」+ 作品标题。

    取回的两个字典与搜索侧喂给 ``classify_verticals`` 的形状一致:池行本身,
    以及 ``{"evidence_titles": [...]}``。刻意**不**取分析产物里的产品出镜/品牌露出
    (那要多打两张表),因此本模块的垂类读数是搜索侧的**子集** —— 少判不多判,
    估出来的人数只会偏保守。上层必须如实标注这一点。

    第二个返回值是**实际发出的查询条数**,给上层如实记账用。
    """
    ids = sorted({int(value) for value in pool_ids if value is not None})
    if not ids:
        return {}, 0
    queries = 0
    profiles: dict[int, dict[str, Any]] = {}
    for chunk in _chunks(ids):
        queries += 1
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT p.id AS kol_pool_id, p.bio, p.primary_topic, p.content_style,
                   p.secondary_topics_json, p.topic_details_json, p.tagged_brands_json
            {POOL_SCOPE_SQL} AND p.id IN ({placeholders})
            """,
            chunk,
        ).fetchall()
        for raw in rows:
            item = dict(raw)
            profiles[int(item["kol_pool_id"])] = item
    titles, title_queries = _load_evidence_titles(conn, ids)
    mapping = {
        pool_id: (row, {"evidence_titles": titles.get(pool_id, [])})
        for pool_id, row in profiles.items()
    }
    return mapping, queries + title_queries


def _load_evidence_titles(conn: Any, ids: Sequence[int]) -> tuple[dict[int, list[str]], int]:
    """作品标题(每人最多 12 条,与搜索侧 ``evidence_titles`` 的截断口径一致)。"""
    active = "e.is_active IS NOT FALSE" if is_postgres_runtime() else "COALESCE(e.is_active, 1) != 0"
    out: dict[int, list[str]] = {}
    queries = 0
    for chunk in _chunks(ids):
        placeholders = ",".join("?" for _ in chunk)
        queries += 1
        try:
            rows = conn.execute(
                f"""
                SELECT e.kol_pool_id, e.title, e.video_title
                FROM vkpi_kol_video_evidence e
                WHERE {active} AND e.kol_pool_id IN ({placeholders})
                """,
                chunk,
            ).fetchall()
        except Exception:
            logger.warning("产量预估:作品标题取数失败,本次垂类只用资料信号", exc_info=True)
            return out, queries
        for raw in rows:
            item = dict(raw)
            try:
                pool_id = int(item.get("kol_pool_id"))
            except (TypeError, ValueError):
                continue
            bucket = out.setdefault(pool_id, [])
            if len(bucket) >= 12:
                continue
            text = " ".join(str(item.get("title") or item.get("video_title") or "").split()).strip()
            if text and text not in bucket:
                bucket.append(text)
    return out, queries


def open_connection(get_connection: Callable[[], Any] | None = None) -> Any:
    """统一取连接口子(``get_conn()`` 不是上下文管理器,拿到就用)。"""
    return (get_connection or get_conn)()


__all__ = [
    "FOLLOWERS_MAX_SENTINEL",
    "FOLLOWERS_MIN_SENTINEL",
    "KEY_COLUMNS",
    "POOL_SCOPE_SQL",
    "load_group_counts",
    "load_row_keys",
    "load_vertical_inputs",
    "open_connection",
]

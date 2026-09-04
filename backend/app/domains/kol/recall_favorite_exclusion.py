"""全局排除「已被关注的人」——召回层的一道单向闸(用户裁决 2026-08-25 · 第 2 条)。

用户原话是「每个人工作各干各的」:两个同事不该在搜索结果里撞上同一个 KOL。因此口径是
**全局**——只要**任意一名员工**收藏过某人,之后**任何人**搜索都不再看到他。这不是按员工
隔离(那会让 A 收藏后 B 仍搜得到,正是要避免的撞车),口径与
``auto_poll.py`` 里已有的「任意员工收藏即算」完全一致(同一张
``vkpi_kol_pool_favorites``,不带 staff_id 条件)。

**排除的是「已被收藏的人」,不是「已在池子里的人」。** 池子里 2000+ 人绝大多数没人收藏,
他们照旧全部参与召回;本闸只摘掉收藏表里那一小撮。这两者天差地别,任何改动都不许混淆。

去重行的坑(必须逐条守住)::

    收藏落在 vkpi_kol_pool_favorites.kol_pool_id 上,而召回 SQL 只吐
    ``duplicate_of_id IS NULL`` 的规范行。若某人先被收藏、之后他那条被并成别人的
    duplicate(或反过来:收藏落在 alias 行上、召回吐的是 canonical 行),单纯比 id 会漏。
    因此判定沿 duplicate_of_id 双向各走一跳,并覆盖「同一 canonical 的两个 alias」这一支:

      ① 候选自己被收藏;
      ② 候选的 canonical(candidate.duplicate_of_id)被收藏;
      ③ 候选的某个 alias(alias.duplicate_of_id = candidate.id)被收藏;
      ④ 候选与某个被收藏行同属一个 canonical(兄弟 alias)。

    vkpi_kol_pool 的去重模型只有 canonical/alias 两层(alias.duplicate_of_id 指向
    canonical,canonical 自身为 NULL),上面四支即为闭包,不需要递归。

在线腿没有 pool id(provider 现抓的人拿的是合成 id),所以另走一套身份键:
``(platform, handle)`` 小写归一,对照收藏行在 pool 里的 platform/handle。实测当前重叠是
0%,本闸的价值在于**保证以后也是 0%**。

红线:
  - 纯只读。零 SELECT 以外的语句,零 LLM,零抓取,不写 ``viltrox_fit_score``。
  - 缺表 / 查询异常 → 诚实降级 ``available=False`` 且**不排除任何人**(失败方向 = 保持现状),
    绝不因为查不到收藏就把人误杀。
  - 计数必须如实透出:被摘掉几个人要能在诊断里读到,否则操作员会把「少了几个」
    误读成搜索能力变差。
  - SQL 兼容:占位符 ``?``;零字面 percent(不用 LIKE);``get_conn()`` 不作上下文管理器。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

#: 诊断/回执里统一的排除理由码(前端按此码渲染文案,不解析中文)。
FAVORITE_EXCLUSION_REASON = "already_favorited_by_team"
#: 诊断 schema 版本;新增字段时递增,便于会话快照比对。
FAVORITE_EXCLUSION_SCHEMA = "recall_favorite_exclusion_v1"

FAVORITES_TABLE = "vkpi_kol_pool_favorites"
POOL_TABLE = "vkpi_kol_pool"

#: 诊断里最多带回多少个被排除的 id(全量计数另有 excluded_count,截断可见不静默)。
EXCLUDED_ID_SAMPLE_CAP = 60
#: 单次身份键查询的上限,防止把整张收藏表拖进内存。当前收藏量 21,余量三个数量级。
IDENTITY_KEY_CAP = 5000


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        logger.warning("recall_favorite_exclusion row coercion failed", exc_info=True)
        return {}


def _text_key(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower().lstrip("@")


def identity_key(platform: Any, handle: Any) -> tuple[str, str]:
    """在线腿身份键:平台 + handle,大小写与前导 @ 归一。两端任一为空 → 视为无身份。"""

    return (_text_key(platform), _text_key(handle))


def _sources_available() -> bool:
    try:
        return bool(table_exists(FAVORITES_TABLE)) and bool(table_exists(POOL_TABLE))
    except Exception:
        logger.warning("recall_favorite_exclusion table probe failed", exc_info=True)
        return False


def _connection(get_connection: Callable[[], Any] | None) -> Any:
    """取连接;取不到就返回 None —— 调用方一律按「不排除」处理,绝不误杀。"""

    try:
        return (get_connection or get_conn)()
    except Exception:
        logger.warning("recall_favorite_exclusion connection unavailable", exc_info=True)
        return None


def _unavailable(reason: str, *, considered: int = 0) -> dict[str, Any]:
    """诚实降级的诊断块:没排除任何人,并说清为什么。"""

    return {
        "schema": FAVORITE_EXCLUSION_SCHEMA,
        "available": False,
        "reason_code": FAVORITE_EXCLUSION_REASON,
        "unavailable_reason": reason,
        "scope": "any_staff_favorite_global",
        "considered_count": max(0, int(considered)),
        "excluded_count": 0,
        "excluded_ids": [],
        "excluded_ids_truncated": False,
    }


# ── 库内(本地)腿 ───────────────────────────────────────────────────────────


# 「同一个人」的规范键:canonical(x) = COALESCE(x.duplicate_of_id, x.id)。
# 模块文档里的四支等价类,用这一个键一次覆盖(逐支验算):
#   ① 候选自己被收藏     → canonical 两边同为自身 id;
#   ② 候选是 alias、canonical 被收藏 → canonical(候选)=duplicate_of_id=收藏行 id=canonical(收藏行);
#   ③ 候选是 canonical、alias 被收藏 → canonical(收藏行)=其 duplicate_of_id=候选 id;
#   ④ 兄弟 alias         → 两边 duplicate_of_id 相同。
# 收藏行一侧同样走 canonical,所以「收藏落在哪一行」不影响判定。
_FAVORITED_CLASS_SUBQUERY = """
    SELECT COALESCE(d.duplicate_of_id, d.id) AS class_id
    FROM {favorites} f
    JOIN {pool} d ON d.id = f.kol_pool_id
"""

_FAVORITED_IDS_SQL_TEMPLATE = """
    SELECT p.id AS pool_id
    FROM {pool} p
    WHERE p.id IN ({placeholders})
      AND COALESCE(p.duplicate_of_id, p.id) IN (
""" + _FAVORITED_CLASS_SUBQUERY + """
      )
"""


def favorited_pool_ids(
    candidate_ids: Sequence[int] | Iterable[int],
    *,
    get_connection: Callable[[], Any] | None = None,
) -> set[int]:
    """给定候选 pool id,返回其中「已被任意员工关注」的那些(含去重行等价类)。

    查不到 / 缺表 / 异常一律返回空集 —— 失败方向永远是「不排除」,绝不误杀。
    """

    ids = sorted({_int(value) for value in (candidate_ids or ()) if _int(value) > 0})
    if not ids:
        return set()
    if not _sources_available():
        return set()
    conn = _connection(get_connection)
    if conn is None:
        return set()
    sql = _FAVORITED_IDS_SQL_TEMPLATE.format(
        pool=POOL_TABLE,
        favorites=FAVORITES_TABLE,
        placeholders=",".join("?" for _ in ids),
    )
    try:
        rows = conn.execute(sql, tuple(ids)).fetchall()
    except Exception:
        logger.warning("recall_favorite_exclusion local lookup failed", exc_info=True)
        return set()
    found = {_int(_row_dict(row).get("pool_id")) for row in rows or ()}
    return {value for value in found if value > 0}


def exclude_favorited_hits(
    hits: Sequence[Any],
    *,
    get_connection: Callable[[], Any] | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    """从召回命中里摘掉已被关注的人,并回一份如实的诊断块。

    输入是 ``RecallHit`` 序列(只读 ``.kol_pool_id``),输出保持原顺序。
    """

    kept = list(hits or ())
    considered = len(kept)
    if not kept:
        return kept, _diagnostics(considered=0, excluded=[])
    if not _sources_available():
        return kept, _unavailable("favorites_table_missing", considered=considered)

    candidate_ids = [_int(getattr(hit, "kol_pool_id", 0)) for hit in kept]
    excluded_ids = favorited_pool_ids(candidate_ids, get_connection=get_connection)
    if not excluded_ids:
        return kept, _diagnostics(considered=considered, excluded=[])

    survivors = [
        hit for hit in kept if _int(getattr(hit, "kol_pool_id", 0)) not in excluded_ids
    ]
    hit_excluded = sorted(
        {value for value in candidate_ids if value in excluded_ids}
    )
    logger.info(
        "recall_favorite_exclusion applied considered=%s excluded=%s",
        considered,
        len(hit_excluded),
    )
    return survivors, _diagnostics(considered=considered, excluded=hit_excluded)


def _diagnostics(*, considered: int, excluded: Sequence[int]) -> dict[str, Any]:
    ids = list(excluded or ())
    return {
        "schema": FAVORITE_EXCLUSION_SCHEMA,
        "available": True,
        "reason_code": FAVORITE_EXCLUSION_REASON,
        "unavailable_reason": "",
        "scope": "any_staff_favorite_global",
        "considered_count": max(0, int(considered)),
        "excluded_count": len(ids),
        "excluded_ids": [int(value) for value in ids[:EXCLUDED_ID_SAMPLE_CAP]],
        "excluded_ids_truncated": len(ids) > EXCLUDED_ID_SAMPLE_CAP,
    }


# ── 在线腿 ──────────────────────────────────────────────────────────────────


# 同一套 canonical 键:被关注等价类里每一行的 platform/handle 都算「这个人的身份」,
# 因为在线候选可能撞上他的任意一个马甲。
_FAVORITED_IDENTITIES_SQL_TEMPLATE = """
    SELECT p.platform AS platform, p.handle AS handle
    FROM {pool} p
    WHERE COALESCE(p.duplicate_of_id, p.id) IN (
""" + _FAVORITED_CLASS_SUBQUERY + """
      )
    LIMIT ?
"""


def favorited_identity_keys(
    *,
    get_connection: Callable[[], Any] | None = None,
) -> set[tuple[str, str]]:
    """已被任意员工关注的人的 ``(platform, handle)`` 身份键集合(含去重等价类)。

    在线候选没有 pool id,只能按身份键比。缺表/异常 → 空集(不排除)。
    """

    if not _sources_available():
        return set()
    conn = _connection(get_connection)
    if conn is None:
        return set()
    sql = _FAVORITED_IDENTITIES_SQL_TEMPLATE.format(
        pool=POOL_TABLE,
        favorites=FAVORITES_TABLE,
    )
    try:
        rows = conn.execute(sql, (IDENTITY_KEY_CAP,)).fetchall()
    except Exception:
        logger.warning("recall_favorite_exclusion identity lookup failed", exc_info=True)
        return set()
    keys: set[tuple[str, str]] = set()
    for row in rows or ():
        data = _row_dict(row)
        key = identity_key(data.get("platform"), data.get("handle"))
        if key[0] and key[1]:
            keys.add(key)
    return keys


def exclude_favorited_online_candidates(
    candidates: Sequence[Any],
    *,
    get_connection: Callable[[], Any] | None = None,
    identity_keys: set[tuple[str, str]] | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    """在线腿:按 ``(platform, handle)`` 摘掉已被关注的人。

    候选是 provider 现抓的 dict(还没花钱做资质判定之前就该摘)。身份键缺一半的候选
    **一律保留** —— 判不出身份就不许当成命中排除(失败方向 = 不误杀)。
    """

    kept = list(candidates or ())
    considered = len(kept)
    if not kept:
        return kept, _diagnostics(considered=0, excluded=[])
    keys = identity_keys if identity_keys is not None else favorited_identity_keys(
        get_connection=get_connection,
    )
    if not keys:
        if not _sources_available():
            return kept, _unavailable("favorites_table_missing", considered=considered)
        return kept, _diagnostics(considered=considered, excluded=[])

    survivors: list[Any] = []
    excluded_labels: list[str] = []
    for item in kept:
        data = item if isinstance(item, dict) else {}
        key = identity_key(
            data.get("platform") or data.get("platform_name"),
            data.get("handle") or data.get("username"),
        )
        if key[0] and key[1] and key in keys:
            excluded_labels.append(f"{key[0]}:{key[1]}")
            continue
        survivors.append(item)
    diagnostics = _diagnostics(considered=considered, excluded=[])
    diagnostics.update(
        {
            "excluded_count": len(excluded_labels),
            "excluded_ids": [],
            "excluded_identity_keys": excluded_labels[:EXCLUDED_ID_SAMPLE_CAP],
            "excluded_ids_truncated": len(excluded_labels) > EXCLUDED_ID_SAMPLE_CAP,
        }
    )
    if excluded_labels:
        logger.info(
            "recall_favorite_exclusion online considered=%s excluded=%s",
            considered,
            len(excluded_labels),
        )
    return survivors, diagnostics


def annotate_shortfall(diagnostics: dict[str, Any] | None) -> dict[str, Any] | None:
    """就地补一句人话:这些人是被藏了还是被标注了、还缺几个人。

    **缺口照实说,绝不暗示会拿别人补位。** 这一句是操作员唯一的解释来源:少了人如果没人
    说明,他会当成搜索能力变差;而松绑口径下他们根本没少,只是带了标注 —— 那也得说清楚。
    """

    if not isinstance(diagnostics, dict):
        return diagnostics
    excluded = max(0, _int(diagnostics.get("favorite_excluded_count")))
    annotated = max(0, _int(diagnostics.get("favorite_annotated_count")))
    shortfall = max(0, _int(diagnostics.get("shortfall")))
    if excluded and shortfall:
        note = f"已排除 {excluded} 个已被关注的人;本次仍缺 {shortfall} 人,未用其他人补位。"
    elif excluded:
        note = f"已排除 {excluded} 个已被关注的人。"
    elif annotated:
        note = f"其中 {annotated} 人已被同事关注,已按此标注,未从结果里隐藏。"
    else:
        note = ""
    diagnostics["favorite_exclusion_note"] = note
    return diagnostics


def merge_diagnostics(*blocks: dict[str, Any] | None) -> dict[str, Any]:
    """把同一次搜索里多轮/多腿的排除诊断加总(计数相加,可用性取「全可用才算可用」)。"""

    merged = _diagnostics(considered=0, excluded=[])
    seen_ids: list[int] = []
    seen_keys: list[str] = []
    available = True
    unavailable_reason = ""
    saw_any = False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        saw_any = True
        merged["considered_count"] += max(0, _int(block.get("considered_count")))
        merged["excluded_count"] += max(0, _int(block.get("excluded_count")))
        seen_ids.extend(_int(value) for value in (block.get("excluded_ids") or ()))
        seen_keys.extend(str(value) for value in (block.get("excluded_identity_keys") or ()))
        if block.get("available") is not True:
            available = False
            unavailable_reason = unavailable_reason or str(block.get("unavailable_reason") or "")
    if not saw_any:
        return merged
    merged["available"] = available
    merged["unavailable_reason"] = unavailable_reason
    unique_ids = sorted({value for value in seen_ids if value > 0})
    merged["excluded_ids"] = unique_ids[:EXCLUDED_ID_SAMPLE_CAP]
    if seen_keys:
        merged["excluded_identity_keys"] = seen_keys[:EXCLUDED_ID_SAMPLE_CAP]
    # 「样本被截断」= 明细里带不全被排除的那些人。计数(excluded_count)永远是全量真值。
    merged["excluded_ids_truncated"] = merged["excluded_count"] > (
        len(merged["excluded_ids"]) + len(seen_keys[:EXCLUDED_ID_SAMPLE_CAP])
    )
    return merged


__all__ = [
    "EXCLUDED_ID_SAMPLE_CAP",
    "annotate_shortfall",
    "FAVORITE_EXCLUSION_REASON",
    "FAVORITE_EXCLUSION_SCHEMA",
    "exclude_favorited_hits",
    "exclude_favorited_online_candidates",
    "favorited_identity_keys",
    "favorited_pool_ids",
    "identity_key",
    "merge_diagnostics",
]

"""final_v1 证据 modality 投影(my-kol videos / board-ext recent_videos 共用)。

行级此前只下发 ``llm_viltrox_status``(present/absent/unknown),前端无法得知
品牌证据是「画面看到」「字幕读到」还是「口播听到」。本模块把 latest ready
``video_analysis_final_v1`` 结果里 ``brand_product_evidence.viltrox_evidence[].modality``
抽成 ``viltrox_modalities``:``["visual", "subtitle", "audio"]`` 的子集,固定按该
顺序去重;``metadata`` 模态(标题/描述命中)按 Gemini 结果口径不算真证据,不进
子集;结果缺失/旧版结果无该块/解析失败 → ``[]``(诚实空,绝不猜)。

两条读路径、一个归一器:
* board-ext:``V_CONTENT_CLASSIFIED_CTE`` 用 :data:`FINAL_V1_MODALITIES_PG_EXPR`
  在 SQL 里只投影 modality 字符串数组(证据 detail 原文不出库);
* my-kol videos::func:`final_v1_modalities_for_evidence` 按页批查一次
  vkpi_analysis_cache(latest ready,按 cache id DESC),SQLite 本地镜像走
  ``json_extract`` 同路径。
两路都经 :func:`viltrox_modalities` 归一,保证同一 evidence 两端点输出一致。

红线:纯读、零 LLM、零外调;不下发 detail/timestamp/原文。
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from app.core.logging import get_logger

logger = get_logger("viltrox.domains.kol.video_evidence_projection")

VILTROX_MODALITIES: tuple[str, ...] = ("visual", "subtitle", "audio")
FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
MAX_BATCH = 200

# final_v1 结果里 brand_product_evidence 的两个落点。只有块级状态明确为
# ``present`` 才允许把其中的 modality 当成品牌提及；矛盾的
# ``absent/unknown + evidence`` 旧缓存必须失败关闭，不能制造假阳性。
_PG_MODALITY_BLOCKS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("raw_gemini_video", "brand_product_evidence", "viltrox_status"),
        "$.raw_gemini_video.brand_product_evidence.viltrox_evidence[*].modality",
    ),
    (
        (
            "raw_gemini_video",
            "video_analysis_final_v1",
            "layer1_visual_content",
            "brand_product_evidence",
            "viltrox_status",
        ),
        "$.raw_gemini_video.video_analysis_final_v1.layer1_visual_content.brand_product_evidence.viltrox_evidence[*].modality",
    ),
)
_SQLITE_STATUS_PATHS: tuple[str, ...] = (
    "$.raw_gemini_video.brand_product_evidence.viltrox_status",
    "$.raw_gemini_video.video_analysis_final_v1.layer1_visual_content.brand_product_evidence.viltrox_status",
)
_SQLITE_EVIDENCE_PATHS: tuple[str, ...] = (
    "$.raw_gemini_video.brand_product_evidence.viltrox_evidence",
    "$.raw_gemini_video.video_analysis_final_v1.layer1_visual_content.brand_product_evidence.viltrox_evidence",
)


def modalities_pg_expr(alias: str = "fv") -> str:
    """Postgres 表达式:只投影 modality 字符串的 jsonb 数组(lax 路径缺失 → '[]')。"""
    parts = []
    for status_path, modality_path in _PG_MODALITY_BLOCKS:
        args = ", ".join(f"'{part}'" for part in status_path)
        parts.append(
            "CASE WHEN LOWER(COALESCE("
            f"jsonb_extract_path_text({alias}.result, {args}), '')) = 'present' "
            f"THEN COALESCE(jsonb_path_query_array({alias}.result, '{modality_path}'), '[]'::jsonb) "
            "ELSE '[]'::jsonb END"
        )
    return " || ".join(parts)


FINAL_V1_MODALITIES_PG_EXPR = modalities_pg_expr("fv")


def viltrox_modalities(value: Any) -> list[str]:
    """归一:证据项列表 / modality 字符串数组 / JSON 文本 → 固定顺序去重子集。"""
    if value in (None, ""):
        return []
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, (list, tuple)):
        return []
    seen: set[str] = set()
    for item in value:
        raw = item.get("modality") if isinstance(item, dict) else item
        modality = str(raw or "").strip().lower()
        if modality in VILTROX_MODALITIES:
            seen.add(modality)
    return [modality for modality in VILTROX_MODALITIES if modality in seen]


def merge_modalities(*values: Any) -> list[str]:
    """多路来源合并后归一(顶层块 + layer1 副本)。"""
    combined: list[Any] = []
    for value in values:
        combined.extend(viltrox_modalities(value))
    return viltrox_modalities(combined)


def _is_sqlite(conn: Any) -> bool:
    return callable(getattr(conn, "executescript", None))


def _rollback_quietly(conn: Any) -> None:
    rollback = getattr(conn, "rollback", None)
    if not callable(rollback):
        return
    try:
        rollback()
    except Exception:
        logger.debug("modality projection rollback skipped", exc_info=True)


def final_v1_modalities_for_evidence(conn: Any, evidence_ids: Iterable[int]) -> dict[int, list[str]]:
    """批查 latest ready final_v1 的 modality 子集;查不到/表缺/异常一律 → 缺省 []。

    返回只含查到的 evidence_id;调用方对缺席 id 取 ``[]``。任何异常只记日志,
    绝不让行级投影拖垮整页(诚实降级为空子集)。
    """
    ids: list[int] = []
    for value in evidence_ids:
        try:
            evidence_id = int(value)
        except (TypeError, ValueError):
            continue
        if evidence_id > 0 and evidence_id not in ids:
            ids.append(evidence_id)
        if len(ids) >= MAX_BATCH:
            break
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    if _is_sqlite(conn):
        first, second = _SQLITE_EVIDENCE_PATHS
        status_first, status_second = _SQLITE_STATUS_PATHS
        sql = f"""
            WITH ranked AS (
                SELECT c.target_id AS target_id,
                       CASE WHEN json_valid(c.result) THEN json_extract(c.result, '{status_first}') END AS viltrox_status,
                       CASE WHEN json_valid(c.result) THEN json_extract(c.result, '{status_second}') END AS viltrox_status_layer1,
                       CASE WHEN json_valid(c.result) THEN json_extract(c.result, '{first}') END AS modality_payload,
                       CASE WHEN json_valid(c.result) THEN json_extract(c.result, '{second}') END AS modality_payload_layer1,
                       ROW_NUMBER() OVER (PARTITION BY c.target_id ORDER BY c.id DESC) AS row_num
                FROM vkpi_analysis_cache c
                WHERE c.target_type='video'
                  AND c.derive_method=?
                  AND c.status='ready'
                  AND c.target_id IN ({placeholders})
            )
            SELECT target_id, viltrox_status, viltrox_status_layer1,
                   modality_payload, modality_payload_layer1
            FROM ranked
            WHERE row_num=1
        """
    else:
        sql = f"""
            WITH ranked AS (
                SELECT c.target_id AS target_id,
                       {modalities_pg_expr("c")} AS modality_payload,
                       'present' AS viltrox_status,
                       NULL AS viltrox_status_layer1,
                       NULL AS modality_payload_layer1,
                       ROW_NUMBER() OVER (PARTITION BY c.target_id ORDER BY c.id DESC) AS row_num
                FROM vkpi_analysis_cache c
                WHERE c.target_type='video'
                  AND c.derive_method=?
                  AND c.status='ready'
                  AND c.target_id IN ({placeholders})
            )
            SELECT target_id, viltrox_status, viltrox_status_layer1,
                   modality_payload, modality_payload_layer1
            FROM ranked
            WHERE row_num=1
        """
    try:
        rows = conn.execute(sql, (FINAL_V1_DERIVE_METHOD, *(str(value) for value in ids))).fetchall()
    except Exception:
        # 本地镜像缺 result 列 / 旧库无 jsonpath:行级投影诚实降级为空子集,不拖整页;
        # Postgres 事务已 abort,静默回滚让后续只读语句继续可用(本链零写)。
        logger.warning("final_v1 modality projection unavailable; degrading to empty subsets", exc_info=True)
        _rollback_quietly(conn)
        return {}
    result: dict[int, list[str]] = {}
    for row in rows:
        item = dict(row)
        try:
            evidence_id = int(item.get("target_id") or 0)
        except (TypeError, ValueError):
            continue
        if evidence_id <= 0:
            continue
        first = item.get("modality_payload") if str(item.get("viltrox_status") or "").lower() == "present" else None
        second = (
            item.get("modality_payload_layer1")
            if str(item.get("viltrox_status_layer1") or "").lower() == "present"
            else None
        )
        # PostgreSQL applies both status guards inside the projection and
        # returns their merged array in ``first``.
        if not _is_sqlite(conn):
            first, second = item.get("modality_payload"), None
        result[evidence_id] = merge_modalities(first, second)
    return result


__all__ = [
    "FINAL_V1_MODALITIES_PG_EXPR",
    "VILTROX_MODALITIES",
    "final_v1_modalities_for_evidence",
    "merge_modalities",
    "modalities_pg_expr",
    "viltrox_modalities",
]

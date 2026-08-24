"""MY KOL 一键「数据关注」写边界(波 D·B 车道)。

用户在视频卡上点一下「数据关注」= 关联产品 + 激活持续追踪 + 排一次数值刷新。
SKU 解析三级优先:
  1. 请求体 product_skus(手选,sku_source=manual)
  2. 该视频已有产品关联 vkpi_kol_video_product_links(sku_source=existing)
  3. 保守自动识别:标题归一化(product_aliases.normalize_alias)后按 token 边界
     唯一命中产品 SKU/型号/市场名(sku_source=auto);零命中或多命中 →
     诚实返回 status=sku_required + 候选清单,绝不瞎猜落库。

落地路径 100% 复用 video_tracking.queue_tracked_video(行级权限围栏 / SKU 校验 /
关联落库 / vkpi_kol_video_metric_tracking 激活 / kol_video_metric_refresh 幂等入队),
本文件不重造任何围栏;sku_source=existing 时传空 SKU 列表,不重复写关联行。
红线:纯排队零 provider;绝不触 rule_v0 / fit 分;SQL 全 ? 占位。
"""
from __future__ import annotations

import re
from typing import Any

from app.domains.kol import video_tracking


MAX_CANDIDATES = 20
MAX_PRODUCTS_SCANNED = 2000
# 与 sku_performance._aliases_for 同款保守闸:太短或纯字母短词的别名不参与匹配。
MIN_ALIAS_LEN = 5
MIN_ALIAS_LEN_NO_DIGIT = 10


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalize(value: Any) -> str:
    from app.domains.products.product_aliases import normalize_alias

    return normalize_alias(value)


def _title_text(evidence: dict[str, Any]) -> str:
    return _text(evidence.get("video_title")) or _text(evidence.get("title"))


def _sku_name(row: dict[str, Any]) -> str:
    return (
        _text(row.get("marketing_name"))
        or _text(row.get("model_name"))
        or _text(row.get("sku"))
    )


def _candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {"sku_code": _text(row.get("sku")), "sku_name": _sku_name(row)}


def _alias_usable(alias_norm: str) -> bool:
    if len(alias_norm) < MIN_ALIAS_LEN:
        return False
    if not any(ch.isdigit() for ch in alias_norm) and len(alias_norm) < MIN_ALIAS_LEN_NO_DIGIT:
        return False
    return True


def _existing_link_skus(conn: Any, evidence_id: int) -> list[str]:
    """该视频已落库的产品关联(任意 relation_type),置信度降序,截到追踪上限。"""

    rows = conn.execute(
        """
        SELECT product_sku, MAX(confidence) AS max_confidence
        FROM vkpi_kol_video_product_links
        WHERE evidence_id=?
        GROUP BY product_sku
        ORDER BY max_confidence DESC, product_sku
        LIMIT ?
        """,
        (int(evidence_id), int(video_tracking.MAX_PRODUCT_SKUS)),
    ).fetchall()
    return [sku for sku in (_text(dict(row).get("product_sku")) for row in rows) if sku]


def _catalog_rows(conn: Any, *, limit: int = MAX_PRODUCTS_SCANNED) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT sku, model_name, marketing_name
        FROM vkpi_products
        ORDER BY sku
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [dict(row) for row in rows]


def _match_products_in_title(
    products: list[dict[str, Any]],
    title: str,
) -> list[dict[str, Any]]:
    """标题归一化后按 token 边界找产品;每个产品试 SKU/型号/市场名三个别名。"""

    title_norm = _normalize(title)
    if not title_norm:
        return []
    matched: list[dict[str, Any]] = []
    for row in products:
        sku = _text(row.get("sku"))
        if not sku:
            continue
        for alias in (sku, row.get("model_name"), row.get("marketing_name")):
            alias_norm = _normalize(alias)
            if not alias_norm or not _alias_usable(alias_norm):
                continue
            pattern = r"(?<![a-z0-9])" + re.escape(alias_norm) + r"(?![a-z0-9])"
            if re.search(pattern, title_norm):
                matched.append(row)
                break
    return matched


def _sku_required(conn: Any, evidence: dict[str, Any], matches: list[dict[str, Any]]) -> dict[str, Any]:
    """零命中给全目录候选(截断),多命中只给撞上的几个,让用户点选拍板。"""

    if matches:
        candidates = [_candidate(row) for row in matches[:MAX_CANDIDATES]]
    else:
        candidates = [_candidate(row) for row in _catalog_rows(conn, limit=MAX_CANDIDATES)]
    return {
        "status": "sku_required",
        "evidence_id": _int(evidence.get("id")),
        "candidates": candidates,
    }


def data_watch(
    conn: Any,
    *,
    kol_pool_id: int,
    evidence_id: int,
    staff: dict[str, Any] | None,
    product_skus: Any = None,
) -> dict[str, Any]:
    """一键数据关注:解析 SKU 后走既有追踪路径;解析不出诚实 sku_required。

    调用方把 VideoTrackingError 映射为对应 HTTP 状态,LookupError 映射 404。
    """

    if _int(kol_pool_id) <= 0 or _int(evidence_id) <= 0:
        raise video_tracking.VideoTrackingError("video_evidence_id_invalid")
    # 与追踪路径同一行级围栏先行:未授权者连候选清单也不该看到。
    video_tracking._assert_target_writable(
        conn,
        kol_pool_id=int(kol_pool_id),
        staff=staff,
    )
    evidence = video_tracking._load_evidence_by_id(conn, int(evidence_id))
    if not evidence or _int(evidence.get("kol_pool_id")) != int(kol_pool_id):
        raise LookupError("video_evidence_not_found")

    manual = video_tracking.normalize_product_skus(product_skus)
    if manual:
        skus, sku_source = manual, "manual"
    else:
        existing = _existing_link_skus(conn, _int(evidence.get("id")))
        if existing:
            skus, sku_source = existing, "existing"
        else:
            matches = _match_products_in_title(_catalog_rows(conn), _title_text(evidence))
            if len(matches) == 1:
                skus, sku_source = [_text(matches[0].get("sku"))], "auto"
            else:
                return _sku_required(conn, evidence, matches)

    if sku_source == "auto":
        link_relation_type = "detected"
        link_source = "title_alias_v1"
        link_confidence = 0.6
    else:
        link_relation_type = "manual"
        link_source = video_tracking.TRACKING_SOURCE
        link_confidence = 1.0

    # 关联已在库的不重复写;标题唯一命中只能落 detected,
    # 绝不得冒充员工手选的 manual/1.0 事实。
    queued = video_tracking.queue_tracked_video(
        conn,
        kol_pool_id=int(kol_pool_id),
        content_url=evidence.get("content_url"),
        product_skus=[] if sku_source == "existing" else skus,
        staff=staff,
        product_link_relation_type=link_relation_type,
        product_link_source=link_source,
        product_link_confidence=link_confidence,
    )
    return {
        "status": "tracking",
        "evidence_id": _int(evidence.get("id")),
        "kol_pool_id": int(kol_pool_id),
        "skus": skus,
        "sku_source": sku_source,
        "sku_provenance": (
            {
                "relation_type": "detected",
                "source": "title_alias_v1",
                "confidence": 0.6,
                "requires_human_confirmation": True,
            }
            if sku_source == "auto"
            else {
                "relation_type": link_relation_type if sku_source == "manual" else "existing",
                "source": link_source if sku_source == "manual" else "existing_link",
                "confidence": link_confidence if sku_source == "manual" else None,
                "requires_human_confirmation": False,
            }
        ),
        "tracking": _text(queued.get("metric_tracking_status")) or "active",
        "refresh": _text(queued.get("status")),
    }


__all__ = ["data_watch"]

"""板块 KPI 按日时序 · 参数板构建器(board_series 拆分伴随文件,600 行红线)。

board_series.py 的按域拆分文件:两块「带业务参数/带派生扫描」的板在此——
  sku360    SKU 解析(vkpi_products / vkpi_product_aliases)+ 标题词表匹配按发布日
            计数(与 sku_performance 同源词表同 token 边界匹配器;匹配全在 Python
            零 LIKE;必带 sku,解析不到 LookupError → 路由 404)
  creative  final_v1 深析产物按日 + 窗口行 result payload 按
            creative_segments._decompose_video 同口径计段(段级新增/日)

共享小工具(_day_str/_delta_pct/_flow_metric/_build_metric 等)从 board_series
顶层引入;board_series 对本模块只做函数内懒 import(零循环依赖)。
诚实空态 / LIMIT 双封顶 / 显示层宪法 / 红线约定与 board_series.py 完全一致:
纯读聚合,零写库、零 LLM、零外调;零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from typing import Any

from app.domains.dashboard.board_series import (
    _build_metric,
    _day_str,
    _delta_pct,
    _flow_metric,
)
from app.domains.dashboard.board_series_sql import (
    CREATIVE_READY_COUNT_SQL,
    CREATIVE_READY_DAY_SQL,
    CREATIVE_SCAN_LIMIT,
    CREATIVE_SEGMENT_ROWS_SQL,
    SKU_ALIAS_LOOKUP_SQL,
    SKU_ALIAS_ROWS_LIMIT,
    SKU_ALIASES_SQL,
    SKU_PRODUCT_LOOKUP_SQL,
    SKU_TITLE_DAY_SQL,
    SKU_TITLE_SCAN_LIMIT,
)


# ── sku360:SKU 解析 + 别名词表 + 标题扫描按发布日计数 ─────────────────────


def _resolve_sku(conn: Any, sku: str) -> str:
    """SKU 码 / alias_norm → vkpi_products.sku;解析不到抛 LookupError(路由转 404)。"""
    from app.domains.products.product_aliases import normalize_alias

    row = conn.execute(SKU_PRODUCT_LOOKUP_SQL, (sku,)).fetchone()
    if row:
        return str(dict(row).get("sku") or "")
    norm = normalize_alias(sku)
    if norm:
        row = conn.execute(SKU_ALIAS_LOOKUP_SQL, (norm,)).fetchone()
        if row:
            return str(dict(row).get("sku") or "")
    raise LookupError(f"SKU not found: {sku}")


def _sku_aliases(conn: Any, resolved_sku: str) -> list[dict[str, Any]]:
    """该 SKU 的匹配词表(vkpi_product_aliases,过滤阈值与 sku_performance 同款);
    零别名行 → 用 SKU 码本身归一化兜底(basis 如实注明词表口径)。"""
    from app.domains.products.product_aliases import normalize_alias
    from app.domains.products.sku_performance import MIN_ALIAS_CONFIDENCE

    rows = conn.execute(SKU_ALIASES_SQL, (resolved_sku, SKU_ALIAS_ROWS_LIMIT)).fetchall()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in list(rows)[:SKU_ALIAS_ROWS_LIMIT]:
        rec = dict(r)
        alias_norm = str(rec.get("alias_norm") or "").strip()
        try:
            conf = float(rec.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        if not alias_norm or alias_norm in seen or conf < MIN_ALIAS_CONFIDENCE:
            continue
        if len(alias_norm) < 5:
            continue
        if not any(ch.isdigit() for ch in alias_norm) and len(alias_norm) < 10:
            continue
        seen.add(alias_norm)
        out.append({"alias_norm": alias_norm, "confidence": conf})
    if not out:
        fallback = normalize_alias(resolved_sku)
        if fallback:
            out.append({"alias_norm": fallback, "confidence": 1.0})
    return out


def _sku_mention_day_map(conn: Any, matcher: Any, cur: tuple[str, str]) -> tuple[dict[str, int], int]:
    """窗口内标题命中按日计数;返回 (day_map, 实际扫描条数);SQL/Python 双封顶。"""
    from app.domains.products.sku_performance import _norm

    rows = conn.execute(SKU_TITLE_DAY_SQL, (cur[0], cur[1], SKU_TITLE_SCAN_LIMIT)).fetchall()
    day_map: dict[str, int] = {}
    scanned = 0
    for r in list(rows)[:SKU_TITLE_SCAN_LIMIT]:
        rec = dict(r)
        day = _day_str(rec.get("day"))
        title = str(rec.get("title") or "").strip()
        if not day or not title:
            continue
        scanned += 1
        if matcher.match(_norm(title)):
            day_map[day] = day_map.get(day, 0) + 1
    return day_map, scanned


def _sku360_board(
    conn: Any, out: dict[str, Any], axis: list[str], win: dict[str, Any], sku: str,
) -> None:
    from app.domains.products.sku_performance import _AliasMatcher

    resolved = _resolve_sku(conn, sku)
    aliases = _sku_aliases(conn, resolved)
    matcher = _AliasMatcher(aliases)
    out["params"] = {"sku": sku, "resolved_sku": resolved, "alias_terms": len(aliases)}

    def _mentions() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        cur_map, scanned = _sku_mention_day_map(conn, matcher, win["cur_tz"])
        prev_map, _prev_scanned = _sku_mention_day_map(conn, matcher, win["prev_tz"])
        current, previous = sum(cur_map.values()), sum(prev_map.values())
        series = [{"date": d, "count": cur_map.get(d, 0)} for d in axis]
        return series, {
            "status": "ready",
            "current": current,
            "previous": previous,
            "delta_pct": _delta_pct(current, previous),
            "table": "vkpi_kol_video_evidence",
            "unit": "rows",
            "scanned": scanned,
        }

    _build_metric(
        out, "sku_mentions",
        "vkpi_kol_video_evidence(is_active IS NOT FALSE,标题非空)× vkpi_product_aliases "
        "alias_norm 词表(sku_performance 同源同 token 边界匹配器;零别名行时用 SKU 码"
        "归一化兜底)按 COALESCE(published_at_norm, publish_date) 发布日计数;"
        "匹配全在 Python(零 LIKE);发布时间双缺失行如实不进序列;只出计数不出标题",
        _mentions,
    )


# ── creative:深析新增按日 + 段级新增按日(_decompose_video 同口径计段)────


def _segment_count(payload: Any) -> int:
    """单条 final_v1 result → 段级条目数(creative_segments._decompose_video 同口径:
    开头段 + 分镜段逐镜 + 产品露出段;解不开 payload 如实 0 段)。纯函数零 SQL。"""
    from app.domains.content.creative_segments import (
        _MAX_SCENES_PER_VIDEO,
        _as_dict,
        _as_list,
        _final_layers,
        _loads,
        _text,
    )

    root = _as_dict(_loads(payload))
    layers = _final_layers(root)
    layer1 = _as_dict(layers.get("layer1_visual_content"))
    if not layer1:
        return 0
    layer2 = _as_dict(layers.get("layer2_viewer_emotion"))
    scenes = [s for s in _as_list(layer1.get("scene_timeline"))[:_MAX_SCENES_PER_VIDEO] if isinstance(s, dict)]

    count = 0
    opening_parts: list[str] = []
    if scenes:
        opening_parts.append(_text(scenes[0].get("what"), 300))
        opening_parts.append(_text(scenes[0].get("why_it_matters"), 200))
    first3 = _text(layer2.get("first_three_seconds_feeling"), 200)
    if first3:
        opening_parts.append(first3)
    if " ".join(p for p in opening_parts if p):
        count += 1
    for scene in scenes:
        what = _text(scene.get("what"), 300)
        why = _text(scene.get("why_it_matters"), 200)
        if " ".join(p for p in (what, why) if p):
            count += 1
    presence = _text(layer1.get("product_presence"), 400)
    exposure = _text(layer1.get("brand_exposure"), 400)
    if " ".join(p for p in (presence, exposure) if p):
        count += 1
    return count


def _creative_segment_day_map(conn: Any, window: tuple[str, str]) -> dict[str, int]:
    from app.domains.content.creative_segments import FINAL_V1_DERIVE_METHOD

    rows = conn.execute(
        CREATIVE_SEGMENT_ROWS_SQL,
        (FINAL_V1_DERIVE_METHOD, window[0], window[1], CREATIVE_SCAN_LIMIT),
    ).fetchall()
    out: dict[str, int] = {}
    for r in list(rows)[:CREATIVE_SCAN_LIMIT]:
        rec = dict(r)
        day = _day_str(rec.get("day"))
        if not day:
            continue
        out[day] = out.get(day, 0) + _segment_count(rec.get("result"))
    return out


def _creative_board(conn: Any, out: dict[str, Any], axis: list[str], win: dict[str, Any]) -> None:
    from app.domains.content.creative_segments import FINAL_V1_DERIVE_METHOD

    cur, prev = win["cur_tz"], win["prev_tz"]
    _build_metric(
        out, "deep_videos_new",
        "vkpi_analysis_cache(target_type=video,derive_method=video_analysis_final_v1,"
        "status=ready)按 created_at 的 UTC 日计数(新增已深析视频/日);流量型 0 填齐",
        lambda: _flow_metric(conn, day_sql=CREATIVE_READY_DAY_SQL, count_sql=CREATIVE_READY_COUNT_SQL,
                             axis=axis, cur=cur, prev=prev, table="vkpi_analysis_cache",
                             extra=(FINAL_V1_DERIVE_METHOD,)),
    )

    def _segments() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        cur_map = _creative_segment_day_map(conn, cur)
        prev_map = _creative_segment_day_map(conn, prev)
        current, previous = sum(cur_map.values()), sum(prev_map.values())
        series = [{"date": d, "count": cur_map.get(d, 0)} for d in axis]
        return series, {
            "status": "ready",
            "current": current,
            "previous": previous,
            "delta_pct": _delta_pct(current, previous),
            "table": "vkpi_analysis_cache",
            "unit": "rows",
        }

    _build_metric(
        out, "segments_new",
        "vkpi_analysis_cache final_v1 ready 窗口行 result payload 按 "
        "creative_segments._decompose_video 同口径计段(开头段+分镜段逐镜+产品露出段,"
        "解不开 payload 记 0 段如实)按深析入库日求和;段级新增随深析产物产生",
        _segments,
    )

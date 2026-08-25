"""镜头出镜证据:落表(回填)与纯读聚合。

写侧只有 ``backfill_lens_evidence``(回填脚本调用;幂等:按 cache_id + mention_norm
唯一,扫描账本记录版本与缓存时间戳,未变不重扫)。读侧四件:
  * lens_summary     —— 按镜头家族/SKU 聚合:出镜视频数、KOL 数、总播放、modality 分布;
  * kol_lenses       —— 单 KOL 用过哪些镜头 + 逐视频 v_relevance 投影(videos);
  * evidence_relevance —— 按 evidence_id 批量给 v_relevance(内容墙接真源用);
  * coverage         —— 缓存扫描覆盖率(扫了多少、抽出多少、unresolved 比例)。
v_relevance ∈ {confirmed, likely, none} 是只读投影(extractor.v_relevance_for),不落表;
行级 = 该提及行,视频级 = 行里最强者,零提及 = none。
对照工具:export_trace(任一库导出锚定文本)→ replay_trace(本地抽取器重放),prod 零写。
scope 与 MY KOL 看板同款:收藏 ∪ 授权共享(首参 0 = 管理层全团队)。
红线:纯 SELECT + 回填 UPSERT;绝不写 viltrox_fit_score / 不触 rule_v0;SQL 全 ? 占位、
零字面 percent、零 SQL 注释。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from app.domains.kol import lens_evidence as extractor


CONTRACT = "lens_insights_v1"
MAX_LENSES = 60
MAX_SAMPLE_VIDEOS = 4
MAX_UNRESOLVED_SAMPLES = 20
MODALITY_LABELS = {
    "visual": "画面",
    "text": "字幕·文字",
    "voice": "口播",
    "unspecified": "未注明",
}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, (str, bytes, bytearray)) and value:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds") if value.tzinfo else value.isoformat(timespec="seconds")
    return _text(value)


_COLLECTION_COND = """(
        EXISTS (SELECT 1 FROM vkpi_kol_pool_favorites f
                WHERE f.kol_pool_id = le.kol_pool_id AND (? = 0 OR f.staff_id = ?))
        OR EXISTS (SELECT 1 FROM vkpi_kol_pool_members sm
                   WHERE sm.kol_pool_id = le.kol_pool_id AND (? = 0 OR sm.staff_id = ?))
      )"""


# ── 回填 ────────────────────────────────────────────────────────────────────


from app.domains.kol.lens_evidence_candidates import (  # noqa: E402
    candidate_cache_rows as _candidate_cache_rows,
)


def _write_rows(conn: Any, cache: dict[str, Any], rows: list[dict[str, Any]]) -> int:
    cache_id = _int(cache.get("cache_id"))
    evidence_id = _int(cache.get("evidence_id")) or None
    kol_pool_id = _int(cache.get("kol_pool_id")) or None
    keep_norms: list[str] = []
    for row in rows:
        keep_norms.append(row["mention_norm"])
        conn.execute(
            """
            INSERT INTO vkpi_kol_lens_evidence (
                cache_id, evidence_id, kol_pool_id, mention_text, mention_norm, resolution,
                product_sku, lens_key, display_name, category_main, candidate_skus,
                modalities, source_fields, mention_count, extractor_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (cache_id, mention_norm) DO UPDATE SET
                evidence_id = excluded.evidence_id,
                kol_pool_id = excluded.kol_pool_id,
                mention_text = excluded.mention_text,
                resolution = excluded.resolution,
                product_sku = excluded.product_sku,
                lens_key = excluded.lens_key,
                display_name = excluded.display_name,
                category_main = excluded.category_main,
                candidate_skus = excluded.candidate_skus,
                modalities = excluded.modalities,
                source_fields = excluded.source_fields,
                mention_count = excluded.mention_count,
                extractor_version = excluded.extractor_version,
                updated_at = excluded.updated_at
            """,
            (
                cache_id,
                evidence_id,
                kol_pool_id,
                row["mention_text"][:200],
                row["mention_norm"][:200],
                row["resolution"],
                row["product_sku"],
                row["lens_key"][:120],
                row["display_name"][:200],
                row["category_main"][:80],
                _dumps(row["candidate_skus"]),
                _dumps(row["modalities"]),
                _dumps(row["source_fields"]),
                max(1, _int(row["mention_count"])),
                extractor.EXTRACTOR_VERSION,
                _now_iso(),
                _now_iso(),
            ),
        )
    # 重扫时旧版本抽出的、这次没再出现的行删掉(派生表以当前抽取为准)。
    if keep_norms:
        placeholders = ",".join("?" for _ in keep_norms)
        conn.execute(
            f"DELETE FROM vkpi_kol_lens_evidence WHERE cache_id = ? AND mention_norm NOT IN ({placeholders})",
            (cache_id, *keep_norms),
        )
    else:
        conn.execute("DELETE FROM vkpi_kol_lens_evidence WHERE cache_id = ?", (cache_id,))
    status = "no_evidence" if evidence_id is None else ("scanned" if rows else "empty_result")
    conn.execute(
        """
        INSERT INTO vkpi_kol_lens_evidence_scan (
            cache_id, evidence_id, kol_pool_id, extractor_version, cache_updated_at,
            mention_rows, scan_status, scanned_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (cache_id) DO UPDATE SET
            evidence_id = excluded.evidence_id,
            kol_pool_id = excluded.kol_pool_id,
            extractor_version = excluded.extractor_version,
            cache_updated_at = excluded.cache_updated_at,
            mention_rows = excluded.mention_rows,
            scan_status = excluded.scan_status,
            scanned_at = excluded.scanned_at
        """,
        (
            cache_id,
            evidence_id,
            kol_pool_id,
            extractor.EXTRACTOR_VERSION,
            _ts_text(cache.get("updated_at")) or None,
            len(rows),
            status,
            _now_iso(),
        ),
    )
    return len(rows)


def _video_relevance(rows: list[dict[str, Any]]) -> str:
    states = {_text(row.get("v_relevance")) for row in rows}
    if "confirmed" in states:
        return "confirmed"
    return "likely" if rows else "none"


def _new_stats(*, apply: bool, candidates: int) -> dict[str, Any]:
    return {
        "dry_run": not apply,
        "extractor_version": extractor.EXTRACTOR_VERSION,
        "alias_table_version": extractor.ALIAS_TABLE_VERSION,
        "cache_rows_considered": candidates,
        "cache_rows_with_evidence": 0,
        "cache_rows_with_mentions": 0,
        "mention_rows": 0,
        "series_only_rows": 0,
        "by_resolution": {"sku": 0, "family": 0, "unresolved": 0},
        "by_modality": {key: 0 for key in extractor.MODALITIES},
        "by_v_relevance": {key: 0 for key in extractor.V_RELEVANCE if key != "none"},
        "videos_by_v_relevance": {key: 0 for key in extractor.V_RELEVANCE},
        "unresolved_pct": None,
        "unresolved_pct_excl_series": None,
        "unresolved_samples": [],
        "top_lenses": [],
        "written_rows": 0,
    }


def _tally(stats: dict[str, Any], rows: list[dict[str, Any]], lens_counter: dict[str, int], unresolved_counter: dict[str, int]) -> None:
    if rows:
        stats["cache_rows_with_mentions"] += 1
    stats["videos_by_v_relevance"][_video_relevance(rows)] += 1
    for row in rows:
        stats["mention_rows"] += 1
        stats["by_resolution"][row["resolution"]] += 1
        stats["by_v_relevance"][row["v_relevance"]] += 1
        if _text(row.get("lens_key")).startswith(extractor.SERIES_KEY_PREFIX):
            stats["series_only_rows"] += 1
        for modality in row["modalities"]:
            if modality in stats["by_modality"]:
                stats["by_modality"][modality] += 1
        if row["resolution"] == "unresolved":
            unresolved_counter[row["mention_text"]] = unresolved_counter.get(row["mention_text"], 0) + 1
        else:
            lens_counter[row["display_name"]] = lens_counter.get(row["display_name"], 0) + 1


def _finish_stats(stats: dict[str, Any], lens_counter: dict[str, int], unresolved_counter: dict[str, int]) -> dict[str, Any]:
    total = stats["mention_rows"]
    unresolved = stats["by_resolution"]["unresolved"]
    if total:
        stats["unresolved_pct"] = round(100.0 * unresolved / total, 1)
        non_series = total - stats["series_only_rows"]
        stats["unresolved_pct_excl_series"] = round(100.0 * unresolved / non_series, 1) if non_series else None
    stats["unresolved_samples"] = [
        {"mention": text, "count": count}
        for text, count in sorted(unresolved_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_UNRESOLVED_SAMPLES]
    ]
    stats["top_lenses"] = [
        {"lens": name, "videos": count}
        for name, count in sorted(lens_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:15]
    ]
    return stats


def backfill_lens_evidence(
    conn: Any,
    *,
    apply: bool = False,
    limit: int = 5000,
    force: bool = False,
    index: extractor.CatalogIndex | None = None,
    cache_ids: Iterable[int] | None = None,
) -> dict[str, Any]:
    """扫 final_v1 缓存 → 抽取 → (apply 时)落表;返回真实统计。dry-run 默认。

    cache_ids 指定时只看这些行(忽略扫描账本,天然 dry-run 对照用)。"""

    catalog = index or extractor.load_catalog_index(conn)
    candidates = _candidate_cache_rows(conn, limit=max(1, int(limit)), force=force, cache_ids=cache_ids)
    stats = _new_stats(apply=apply, candidates=len(candidates))
    lens_counter: dict[str, int] = {}
    unresolved_counter: dict[str, int] = {}
    for cache in candidates:
        if _int(cache.get("evidence_id")):
            stats["cache_rows_with_evidence"] += 1
        rows = extractor.extract_resolved(cache.get("result"), catalog)
        _tally(stats, rows, lens_counter, unresolved_counter)
        if apply:
            stats["written_rows"] += _write_rows(conn, cache, rows)
    if apply:
        conn.commit()
    return _finish_stats(stats, lens_counter, unresolved_counter)


def explain_cache_rows(conn: Any, *, cache_ids: Iterable[int], index: extractor.CatalogIndex | None = None) -> list[dict[str, Any]]:
    """--cache-id 对照:逐行抽取轨迹(锚点 / 截取 / 归一 / v_relevance)+ 账本里的旧结果。"""

    catalog = index or extractor.load_catalog_index(conn)
    out: list[dict[str, Any]] = []
    for cache in _candidate_cache_rows(conn, limit=500, force=True, cache_ids=cache_ids):
        trace = extractor.explain(cache.get("result"), catalog)
        trace.update(
            {
                "cache_id": _int(cache.get("cache_id")),
                "target_id": _text(cache.get("target_id")),
                "evidence_id": _int(cache.get("evidence_id")) or None,
                "kol_pool_id": _int(cache.get("kol_pool_id")) or None,
                "cache_updated_at": _ts_text(cache.get("updated_at")),
                "result_md5": _result_md5(cache.get("result")),
                "ledger": {
                    "extractor_version": _text(cache.get("scanned_version")) or None,
                    "scan_status": _text(cache.get("scanned_status")) or None,
                    "mention_rows": _int(cache.get("scanned_mention_rows")),
                },
                "video_v_relevance": _video_relevance(trace["rows"]),
            }
        )
        out.append(trace)
    return out


# ── 对照:导出锚定文本 → 另一侧重放 ──────────────────────────────────────────


def _result_md5(result: Any) -> str:
    raw = result if isinstance(result, (str, bytes, bytearray)) else json.dumps(result, ensure_ascii=False, sort_keys=True)
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "ignore")
    return hashlib.md5(bytes(raw)).hexdigest()[:12]


def export_trace(conn: Any, *, limit: int = 5000, cache_ids: Iterable[int] | None = None) -> list[dict[str, Any]]:
    """导出每条 final_v1 缓存的「抽取器实际消费的文本」+ 账本旧结果;只读,可在 prod 跑。"""

    records: list[dict[str, Any]] = []
    for cache in _candidate_cache_rows(conn, limit=max(1, int(limit)), force=True, cache_ids=cache_ids):
        records.append(
            {
                "cache_id": _int(cache.get("cache_id")),
                "target_id": _text(cache.get("target_id")),
                "evidence_id": _int(cache.get("evidence_id")) or None,
                "kol_pool_id": _int(cache.get("kol_pool_id")) or None,
                "cache_updated_at": _ts_text(cache.get("updated_at")),
                "result_md5": _result_md5(cache.get("result")),
                "texts": [[field_name, text[:2000]] for field_name, text in extractor.source_texts(cache.get("result"))],
                "ledger": {
                    "extractor_version": _text(cache.get("scanned_version")) or None,
                    "scan_status": _text(cache.get("scanned_status")) or None,
                    "mention_rows": _int(cache.get("scanned_mention_rows")),
                },
            }
        )
    return records


def _result_from_texts(texts: list[list[str]]) -> dict[str, Any]:
    """把导出的 (field, text) 还原成抽取器认得的最小结果形状(字段名与 source_texts 一一对应)。"""

    layer1: dict[str, Any] = {"scene_timeline": []}
    layer4: dict[str, Any] = {"attribution_breakdown": []}
    layer6: dict[str, Any] = {"scores": {"product_proof_score": {"evidence": []}}}
    raw: dict[str, Any] = {}
    buckets: dict[str, list[str]] = {}
    for field_name, text in texts:
        buckets.setdefault(str(field_name), []).append(str(text))
    for name in ("product_presence", "brand_exposure", "content_summary"):
        layer1[name] = buckets.get(name, [])
    layer1["scene_timeline"] = [{"what": text} for text in buckets.get("scene_timeline", [])]
    layer4["product_contribution"] = buckets.get("product_contribution", [])
    layer4["attribution_breakdown"] = [{"evidence": text} for text in buckets.get("attribution_breakdown", [])]
    layer6["scores"]["product_proof_score"]["evidence"] = buckets.get("product_proof_score", [])
    layer6["key_hook"] = buckets.get("key_hook", [])
    layer6["final_verdict"] = buckets.get("final_verdict", [])
    raw["content_topic"] = buckets.get("raw_content_topic", [])
    raw["viltrox_lens"] = buckets.get("raw_viltrox_lens", [])
    raw["viltrox_products_all"] = buckets.get("raw_viltrox_products_all", [])
    return {"layer1_visual_content": layer1, "layer4_attribution": layer4, "layer6_flags_and_scores": layer6, "raw_gemini_video": raw}


def replay_trace(records: list[dict[str, Any]], index: extractor.CatalogIndex, *, sample_limit: int = 40) -> dict[str, Any]:
    """用本地抽取器 + 本地目录重放另一侧导出的文本:would_write / unresolved / v_relevance + 逐行差异。"""

    stats = _new_stats(apply=False, candidates=len(records))
    stats["mode"] = "replay"
    lens_counter: dict[str, int] = {}
    unresolved_counter: dict[str, int] = {}
    ledger_status: dict[str, int] = {}
    transitions: dict[str, int] = {}
    gained: list[dict[str, Any]] = []
    for record in records:
        if _int(record.get("evidence_id")):
            stats["cache_rows_with_evidence"] += 1
        rows = extractor.extract_resolved(_result_from_texts(record.get("texts") or []), index)
        _tally(stats, rows, lens_counter, unresolved_counter)
        ledger = record.get("ledger") or {}
        before = _text(ledger.get("scan_status")) or "unscanned"
        ledger_status[before] = ledger_status.get(before, 0) + 1
        after = "scanned" if rows else "empty_result"
        transition = f"{before}->{after}"
        transitions[transition] = transitions.get(transition, 0) + 1
        if rows and before != "scanned" and len(gained) < sample_limit:
            gained.append(
                {
                    "cache_id": record.get("cache_id"),
                    "evidence_id": record.get("evidence_id"),
                    "rows": [
                        {"mention": row["mention_text"], "resolution": row["resolution"], "display_name": row["display_name"], "v_relevance": row["v_relevance"]}
                        for row in rows
                    ],
                }
            )
    stats["would_write_rows"] = stats["mention_rows"]
    stats["ledger_status_before"] = dict(sorted(ledger_status.items()))
    stats["scan_transitions"] = dict(sorted(transitions.items()))
    stats["gained_samples"] = gained
    return _finish_stats(stats, lens_counter, unresolved_counter)


def diff_traces(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, Any]:
    """两侧 explain 轨迹按 cache_id 对照:内容指纹是否相同、抽出的行是否一致。"""

    by_id = {_int(item.get("cache_id")): item for item in right}
    same_content = 0
    same_rows = 0
    differing: list[dict[str, Any]] = []
    for item in left:
        other = by_id.get(_int(item.get("cache_id")))
        if other is None:
            differing.append({"cache_id": item.get("cache_id"), "reason": "missing_on_other_side"})
            continue
        if _text(item.get("result_md5")) != _text(other.get("result_md5")):
            differing.append({"cache_id": item.get("cache_id"), "reason": "different_cache_content", "left_md5": item.get("result_md5"), "right_md5": other.get("result_md5")})
            continue
        same_content += 1
        mine = sorted((row["mention_norm"], row["resolution"]) for row in item.get("rows") or [])
        theirs = sorted((row["mention_norm"], row["resolution"]) for row in other.get("rows") or [])
        if mine == theirs:
            same_rows += 1
        else:
            differing.append({"cache_id": item.get("cache_id"), "reason": "different_rows", "left": mine, "right": theirs})
    return {"compared": len(left), "same_content": same_content, "same_rows": same_rows, "differing": differing[:100]}


# ── 纯读聚合 ────────────────────────────────────────────────────────────────


def _scope_params(staff_scope_id: int | None) -> tuple[int, int, int, int]:
    sid = _int(staff_scope_id)
    return (sid, sid, sid, sid)


def _scope_diag(staff_scope_id: int | None, *, scope_all: bool) -> dict[str, Any]:
    sid = _int(staff_scope_id)
    if scope_all:
        return {"mode": "all_analysed", "staff_scope_id": None, "membership": "any_kol_with_final_v1"}
    return {
        "mode": "staff_collection" if sid else "team_collection",
        "staff_scope_id": sid or None,
        "membership": "favorite_or_authorized_share",
    }


def _modality_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in extractor.MODALITIES}
    for row in rows:
        for modality in _json_list(row.get("modalities")):
            if modality in counts:
                counts[modality] += 1
    return counts


def _row_relevance(row: dict[str, Any]) -> tuple[str, str]:
    return extractor.v_relevance_for(
        {
            "resolution": row.get("resolution"),
            "lens_key": row.get("lens_key"),
            "modalities": _json_list(row.get("modalities")),
            "source_fields": _json_list(row.get("source_fields")),
        }
    )


def _relevance_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in extractor.V_RELEVANCE if key != "none"}
    for row in rows:
        counts[_row_relevance(row)[0]] += 1
    return counts


def _videos_by_relevance(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """evidence_id → {v_relevance, cache_id, lenses[], modalities[]}(行里最强者定视频级)。"""

    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        evidence_id = _int(row.get("evidence_id"))
        if not evidence_id:
            continue
        state, reason = _row_relevance(row)
        item = out.setdefault(evidence_id, {"evidence_id": evidence_id, "cache_id": _int(row.get("cache_id")) or None, "v_relevance": "likely", "v_reasons": [], "lenses": [], "modalities": []})
        if state == "confirmed":
            item["v_relevance"] = "confirmed"
        if reason not in item["v_reasons"]:
            item["v_reasons"].append(reason)
        name = _text(row.get("display_name")) or _text(row.get("mention_text"))
        if name and name not in item["lenses"]:
            item["lenses"].append(name)
        for modality in _json_list(row.get("modalities")):
            if modality in extractor.MODALITIES and modality not in item["modalities"]:
                item["modalities"].append(modality)
    return out


def _group_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        resolution = _text(row.get("resolution"))
        state, _reason = _row_relevance(row)
        key = _text(row.get("lens_key")) if resolution != "unresolved" else f"raw:{_text(row.get('mention_norm'))}"
        group = groups.get(key)
        if group is None:
            group = {
                "lens_key": _text(row.get("lens_key")),
                "display_name": _text(row.get("display_name")) or _text(row.get("mention_text")),
                "category_main": _text(row.get("category_main")),
                "resolution": resolution,
                "skus": set(),
                "candidate_skus": set(),
                "evidence_ids": set(),
                "kol_ids": set(),
                "views": {},
                "rows": [],
                "samples": [],
                "v_relevance": "likely",
            }
            groups[key] = group
        if state == "confirmed":
            group["v_relevance"] = "confirmed"
        if _text(row.get("product_sku")):
            group["skus"].add(_text(row.get("product_sku")))
        group["candidate_skus"].update(str(x) for x in _json_list(row.get("candidate_skus")))
        if resolution == "sku" and group["resolution"] == "family":
            group["resolution"] = "sku"
        evidence_id = _int(row.get("evidence_id"))
        if evidence_id:
            group["evidence_ids"].add(evidence_id)
            view_count = row.get("view_count")
            if view_count is not None and evidence_id not in group["views"]:
                group["views"][evidence_id] = _int(view_count)
        if _int(row.get("kol_pool_id")):
            group["kol_ids"].add(_int(row.get("kol_pool_id")))
        group["rows"].append(row)
        if len(group["samples"]) < MAX_SAMPLE_VIDEOS and evidence_id:
            group["samples"].append(
                {
                    "evidence_id": evidence_id,
                    "cache_id": _int(row.get("cache_id")) or None,
                    "v_relevance": state,
                    "kol_pool_id": _int(row.get("kol_pool_id")) or None,
                    "kol_name": _text(row.get("kol_name")),
                    "title": _text(row.get("video_title")) or _text(row.get("title")),
                    "content_url": _text(row.get("content_url")),
                    "platform": _text(row.get("platform")).lower(),
                    "view_count": row.get("view_count"),
                    "modalities": [m for m in _json_list(row.get("modalities")) if m in extractor.MODALITIES],
                }
            )
    out: list[dict[str, Any]] = []
    for group in groups.values():
        measured = list(group["views"].values())
        out.append(
            {
                "lens_key": group["lens_key"],
                "display_name": group["display_name"],
                "category_main": group["category_main"],
                "resolution": group["resolution"],
                "skus": sorted(group["skus"]),
                "candidate_skus": sorted(group["candidate_skus"])[:extractor.MAX_CANDIDATES],
                "videos": len(group["evidence_ids"]),
                "kols": len(group["kol_ids"]),
                "views_total": sum(measured) if measured else None,
                "views_measured_videos": len(measured),
                "mention_rows": len(group["rows"]),
                "modalities": _modality_counts(group["rows"]),
                "v_relevance": group["v_relevance"],
                "v_relevance_rows": _relevance_counts(group["rows"]),
                "samples": group["samples"],
            }
        )
    out.sort(key=lambda item: (item["resolution"] == "unresolved", -item["videos"], -(item["views_total"] or 0), item["display_name"]))
    return out


_ROW_SELECT = """
    SELECT le.id, le.cache_id, le.evidence_id, le.kol_pool_id, le.mention_text, le.mention_norm,
           le.resolution, le.product_sku, le.lens_key, le.display_name, le.category_main,
           le.candidate_skus, le.modalities, le.source_fields, le.mention_count,
           e.view_count, e.title, e.video_title, e.content_url, e.platform,
           COALESCE(NULLIF(kp.display_name, ''), kp.handle, '') AS kol_name
    FROM vkpi_kol_lens_evidence le
    LEFT JOIN vkpi_kol_video_evidence e ON e.id = le.evidence_id
    LEFT JOIN vkpi_kol_pool kp ON kp.id = le.kol_pool_id
"""


def _coverage(conn: Any, *, staff_scope_id: int | None, scope_all: bool) -> dict[str, Any]:
    if scope_all:
        cache_total = conn.execute(
            "SELECT COUNT(*) AS n FROM vkpi_analysis_cache WHERE derive_method = ? AND status = 'ready'",
            (extractor.FINAL_DERIVE_METHOD,),
        ).fetchone()
        scanned = conn.execute(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN mention_rows > 0 THEN 1 ELSE 0 END) AS with_mentions FROM vkpi_kol_lens_evidence_scan",
        ).fetchone()
    else:
        cache_total = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM vkpi_analysis_cache c
            JOIN vkpi_kol_video_evidence e ON c.target_type = 'video' AND c.target_id = CAST(e.id AS TEXT)
            WHERE c.derive_method = ? AND c.status = 'ready'
              AND (
                EXISTS (SELECT 1 FROM vkpi_kol_pool_favorites f
                        WHERE f.kol_pool_id = e.kol_pool_id AND (? = 0 OR f.staff_id = ?))
                OR EXISTS (SELECT 1 FROM vkpi_kol_pool_members sm
                           WHERE sm.kol_pool_id = e.kol_pool_id AND (? = 0 OR sm.staff_id = ?))
              )
            """,
            (extractor.FINAL_DERIVE_METHOD, *_scope_params(staff_scope_id)),
        ).fetchone()
        scanned = conn.execute(
            f"""
            SELECT COUNT(*) AS n, SUM(CASE WHEN le.mention_rows > 0 THEN 1 ELSE 0 END) AS with_mentions
            FROM vkpi_kol_lens_evidence_scan le
            WHERE le.kol_pool_id IS NOT NULL AND {_COLLECTION_COND}
            """,
            _scope_params(staff_scope_id),
        ).fetchone()
    total = _int(dict(cache_total).get("n")) if cache_total else 0
    scanned_row = dict(scanned) if scanned else {}
    return {
        "analysed_videos": total,
        "scanned_videos": _int(scanned_row.get("n")),
        "videos_with_products": _int(scanned_row.get("with_mentions")),
        "videos_without_products": max(0, _int(scanned_row.get("n")) - _int(scanned_row.get("with_mentions"))),
        "unscanned_videos": max(0, total - _int(scanned_row.get("n"))),
    }


def _video_relevance_dist(rows: list[dict[str, Any]], coverage: dict[str, Any]) -> dict[str, int]:
    videos = _videos_by_relevance(rows)
    dist = {key: 0 for key in extractor.V_RELEVANCE}
    for item in videos.values():
        dist[item["v_relevance"]] += 1
    dist["none"] = _int(coverage.get("videos_without_products"))
    return dist


def lens_summary(
    conn: Any,
    *,
    staff_scope_id: int | None,
    scope_all: bool = False,
    limit: int = MAX_LENSES,
) -> dict[str, Any]:
    bound = max(1, min(MAX_LENSES, _int(limit) or MAX_LENSES))
    if scope_all:
        rows = conn.execute(_ROW_SELECT + " ORDER BY le.id ASC LIMIT ?", (20000,)).fetchall()
    else:
        rows = conn.execute(
            _ROW_SELECT + f" WHERE le.kol_pool_id IS NOT NULL AND {_COLLECTION_COND} ORDER BY le.id ASC LIMIT ?",
            (*_scope_params(staff_scope_id), 20000),
        ).fetchall()
    items = [dict(row) for row in rows]
    groups = _group_rows(items)
    resolved = [g for g in groups if g["resolution"] != "unresolved"]
    unresolved = [g for g in groups if g["resolution"] == "unresolved"]
    evidence_ids = {_int(row.get("evidence_id")) for row in items if _int(row.get("evidence_id"))}
    kol_ids = {_int(row.get("kol_pool_id")) for row in items if _int(row.get("kol_pool_id"))}
    coverage = _coverage(conn, staff_scope_id=staff_scope_id, scope_all=scope_all)
    return {
        "contract": CONTRACT,
        "read_only": True,
        "generated_at": _now_iso(),
        "scope": _scope_diag(staff_scope_id, scope_all=scope_all),
        "coverage": coverage,
        "summary": {
            "lenses": len(resolved),
            "videos_with_products": len(evidence_ids),
            "kols_with_products": len(kol_ids),
            "mention_rows": len(items),
            "unresolved_rows": sum(1 for row in items if _text(row.get("resolution")) == "unresolved"),
            "series_only_rows": sum(1 for row in items if _text(row.get("lens_key")).startswith(extractor.SERIES_KEY_PREFIX)),
            "modalities": _modality_counts(items),
            "v_relevance_rows": _relevance_counts(items),
            "v_relevance_videos": _video_relevance_dist(items, coverage),
        },
        "modality_labels": MODALITY_LABELS,
        "v_relevance_labels": extractor.V_RELEVANCE_LABELS,
        "lenses": resolved[:bound],
        "lenses_truncated": len(resolved) > bound,
        "unresolved": [
            {"mention": g["display_name"], "videos": g["videos"], "kols": g["kols"], "candidate_skus": g["candidate_skus"]}
            for g in unresolved[:MAX_UNRESOLVED_SAMPLES]
        ],
        "empty_reason": None if items else "no_lens_evidence",
    }


def kol_lenses(conn: Any, *, kol_pool_id: int) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in conn.execute(
            _ROW_SELECT + " WHERE le.kol_pool_id = ? ORDER BY le.id ASC LIMIT ?",
            (int(kol_pool_id), 2000),
        ).fetchall()
    ]
    groups = _group_rows(rows)
    scan = conn.execute(
        """
        SELECT COUNT(*) AS n, SUM(CASE WHEN mention_rows > 0 THEN 1 ELSE 0 END) AS with_mentions
        FROM vkpi_kol_lens_evidence_scan WHERE kol_pool_id = ?
        """,
        (int(kol_pool_id),),
    ).fetchone()
    analysed = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM vkpi_analysis_cache c
        JOIN vkpi_kol_video_evidence e ON c.target_type = 'video' AND c.target_id = CAST(e.id AS TEXT)
        WHERE c.derive_method = ? AND c.status = 'ready' AND e.kol_pool_id = ?
        """,
        (extractor.FINAL_DERIVE_METHOD, int(kol_pool_id)),
    ).fetchone()
    scan_row = dict(scan) if scan else {}
    analysed_n = _int(dict(analysed).get("n")) if analysed else 0
    coverage = {
        "analysed_videos": analysed_n,
        "scanned_videos": _int(scan_row.get("n")),
        "videos_with_products": _int(scan_row.get("with_mentions")),
        "videos_without_products": max(0, _int(scan_row.get("n")) - _int(scan_row.get("with_mentions"))),
        "unscanned_videos": max(0, analysed_n - _int(scan_row.get("n"))),
    }
    videos = _videos_by_relevance(rows)
    return {
        "contract": CONTRACT,
        "read_only": True,
        "kol_pool_id": int(kol_pool_id),
        "generated_at": _now_iso(),
        "coverage": coverage,
        "modality_labels": MODALITY_LABELS,
        "v_relevance_labels": extractor.V_RELEVANCE_LABELS,
        "v_relevance_videos": _video_relevance_dist(rows, coverage),
        "videos": [videos[key] for key in sorted(videos)],
        "lenses": [g for g in groups if g["resolution"] != "unresolved"],
        "unresolved": [
            {"mention": g["display_name"], "videos": g["videos"], "candidate_skus": g["candidate_skus"]}
            for g in groups if g["resolution"] == "unresolved"
        ][:MAX_UNRESOLVED_SAMPLES],
        "empty_reason": None if rows else ("no_lens_evidence" if analysed_n else "no_analysed_videos"),
    }


def evidence_relevance(conn: Any, *, evidence_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """按 evidence_id 批量投影 v_relevance(内容墙 / 视频卡接真源用)。

    扫描过但零提及 → none;没扫过 → 不在返回里(调用方按 unknown 处理,别装 none)。"""

    ids = sorted({_int(x) for x in evidence_ids if _int(x) > 0})[:500]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = [dict(row) for row in conn.execute(_ROW_SELECT + f" WHERE le.evidence_id IN ({placeholders}) ORDER BY le.id ASC", tuple(ids)).fetchall()]
    out = _videos_by_relevance(rows)
    scanned = conn.execute(
        f"SELECT evidence_id, cache_id FROM vkpi_kol_lens_evidence_scan WHERE evidence_id IN ({placeholders}) AND mention_rows = 0",
        tuple(ids),
    ).fetchall()
    for row in scanned:
        item = dict(row)
        evidence_id = _int(item.get("evidence_id"))
        if evidence_id and evidence_id not in out:
            out[evidence_id] = {"evidence_id": evidence_id, "cache_id": _int(item.get("cache_id")) or None, "v_relevance": "none", "v_reasons": ["no_mentions"], "lenses": [], "modalities": []}
    return out


__all__ = [
    "CONTRACT",
    "MODALITY_LABELS",
    "backfill_lens_evidence",
    "diff_traces",
    "evidence_relevance",
    "explain_cache_rows",
    "export_trace",
    "kol_lenses",
    "lens_summary",
    "replay_trace",
]

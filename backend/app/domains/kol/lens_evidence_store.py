"""镜头出镜证据:落表(回填)与纯读聚合。

写侧只有 ``backfill_lens_evidence``(回填脚本调用;幂等:按 cache_id + mention_norm
唯一,扫描账本记录版本与缓存时间戳,未变不重扫)。读侧三件:
  * lens_summary     —— 按镜头家族/SKU 聚合:出镜视频数、KOL 数、总播放、modality 分布;
  * kol_lenses       —— 单 KOL 用过哪些镜头;
  * coverage         —— 缓存扫描覆盖率(扫了多少、抽出多少、unresolved 比例)。
scope 与 MY KOL 看板同款:收藏 ∪ 授权共享(首参 0 = 管理层全团队)。
红线:纯 SELECT + 回填 UPSERT;绝不写 viltrox_fit_score / 不触 rule_v0;SQL 全 ? 占位、
零字面 percent、零 SQL 注释。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

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


def _candidate_cache_rows(conn: Any, *, limit: int, force: bool) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.id AS cache_id, c.target_type, c.target_id, c.result, c.updated_at,
               e.id AS evidence_id, e.kol_pool_id,
               s.extractor_version AS scanned_version, s.cache_updated_at AS scanned_cache_updated_at
        FROM vkpi_analysis_cache c
        LEFT JOIN vkpi_kol_video_evidence e
          ON c.target_type = 'video' AND c.target_id = CAST(e.id AS TEXT)
        LEFT JOIN vkpi_kol_lens_evidence_scan s ON s.cache_id = c.id
        WHERE c.derive_method = ?
          AND c.status = 'ready'
        ORDER BY c.id ASC
        LIMIT ?
        """,
        (extractor.FINAL_DERIVE_METHOD, int(limit)),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if not force:
            same_version = _text(item.get("scanned_version")) == extractor.EXTRACTOR_VERSION
            same_stamp = _ts_text(item.get("scanned_cache_updated_at")) == _ts_text(item.get("updated_at"))
            if same_version and same_stamp:
                continue
        out.append(item)
    return out


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


def backfill_lens_evidence(
    conn: Any,
    *,
    apply: bool = False,
    limit: int = 5000,
    force: bool = False,
    index: extractor.CatalogIndex | None = None,
) -> dict[str, Any]:
    """扫 final_v1 缓存 → 抽取 → (apply 时)落表;返回真实统计。dry-run 默认。"""

    catalog = index or extractor.load_catalog_index(conn)
    candidates = _candidate_cache_rows(conn, limit=max(1, int(limit)), force=force)
    stats: dict[str, Any] = {
        "dry_run": not apply,
        "extractor_version": extractor.EXTRACTOR_VERSION,
        "cache_rows_considered": len(candidates),
        "cache_rows_with_evidence": 0,
        "cache_rows_with_mentions": 0,
        "mention_rows": 0,
        "by_resolution": {"sku": 0, "family": 0, "unresolved": 0},
        "by_modality": {key: 0 for key in extractor.MODALITIES},
        "unresolved_pct": None,
        "unresolved_samples": [],
        "top_lenses": [],
        "written_rows": 0,
    }
    lens_counter: dict[str, int] = {}
    unresolved_counter: dict[str, int] = {}
    for cache in candidates:
        if _int(cache.get("evidence_id")):
            stats["cache_rows_with_evidence"] += 1
        rows = extractor.extract_resolved(cache.get("result"), catalog)
        if rows:
            stats["cache_rows_with_mentions"] += 1
        for row in rows:
            stats["mention_rows"] += 1
            stats["by_resolution"][row["resolution"]] += 1
            for modality in row["modalities"]:
                if modality in stats["by_modality"]:
                    stats["by_modality"][modality] += 1
            if row["resolution"] == "unresolved":
                unresolved_counter[row["mention_text"]] = unresolved_counter.get(row["mention_text"], 0) + 1
            else:
                lens_counter[row["display_name"]] = lens_counter.get(row["display_name"], 0) + 1
        if apply:
            stats["written_rows"] += _write_rows(conn, cache, rows)
    if apply:
        conn.commit()
    if stats["mention_rows"]:
        stats["unresolved_pct"] = round(100.0 * stats["by_resolution"]["unresolved"] / stats["mention_rows"], 1)
    stats["unresolved_samples"] = [
        {"mention": text, "count": count}
        for text, count in sorted(unresolved_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_UNRESOLVED_SAMPLES]
    ]
    stats["top_lenses"] = [
        {"lens": name, "videos": count}
        for name, count in sorted(lens_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:15]
    ]
    return stats


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


def _group_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        resolution = _text(row.get("resolution"))
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
            }
            groups[key] = group
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
        "unscanned_videos": max(0, total - _int(scanned_row.get("n"))),
    }


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
    return {
        "contract": CONTRACT,
        "read_only": True,
        "generated_at": _now_iso(),
        "scope": _scope_diag(staff_scope_id, scope_all=scope_all),
        "coverage": _coverage(conn, staff_scope_id=staff_scope_id, scope_all=scope_all),
        "summary": {
            "lenses": len(resolved),
            "videos_with_products": len(evidence_ids),
            "kols_with_products": len(kol_ids),
            "mention_rows": len(items),
            "unresolved_rows": sum(1 for row in items if _text(row.get("resolution")) == "unresolved"),
            "modalities": _modality_counts(items),
        },
        "modality_labels": MODALITY_LABELS,
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
    return {
        "contract": CONTRACT,
        "read_only": True,
        "kol_pool_id": int(kol_pool_id),
        "generated_at": _now_iso(),
        "coverage": {
            "analysed_videos": analysed_n,
            "scanned_videos": _int(scan_row.get("n")),
            "videos_with_products": _int(scan_row.get("with_mentions")),
            "unscanned_videos": max(0, analysed_n - _int(scan_row.get("n"))),
        },
        "modality_labels": MODALITY_LABELS,
        "lenses": [g for g in groups if g["resolution"] != "unresolved"],
        "unresolved": [
            {"mention": g["display_name"], "videos": g["videos"], "candidate_skus": g["candidate_skus"]}
            for g in groups if g["resolution"] == "unresolved"
        ][:MAX_UNRESOLVED_SAMPLES],
        "empty_reason": None if rows else ("no_lens_evidence" if analysed_n else "no_analysed_videos"),
    }


__all__ = [
    "CONTRACT",
    "MODALITY_LABELS",
    "backfill_lens_evidence",
    "kol_lenses",
    "lens_summary",
]

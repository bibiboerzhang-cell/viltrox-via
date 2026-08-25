"""Canonical final_v1 source selection for derived lens evidence."""

from __future__ import annotations

from typing import Any, Iterable

from app.domains.analysis.cache_reuse import canonical_final_v1_cache_reuse
from app.domains.kol import lens_evidence as extractor


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ts_text(value: Any) -> str:
    if hasattr(value, "isoformat"):
        try:
            return str(value.isoformat())
        except (TypeError, ValueError):
            pass
    return _text(value)


def candidate_cache_rows(
    conn: Any,
    *,
    limit: int,
    force: bool,
    cache_ids: Iterable[int] | None = None,
    include_unverified: bool = False,
) -> list[dict[str, Any]]:
    ids = sorted({_int(item) for item in (cache_ids or []) if _int(item) > 0})
    id_clause = ""
    params: list[Any] = [extractor.FINAL_DERIVE_METHOD]
    if ids:
        id_clause = " AND c.id IN (" + ",".join("?" for _ in ids) + ")"
        params.extend(ids)
    params.append(int(limit))
    rows = conn.execute(
        """
        SELECT c.id AS cache_id, c.id, c.target_type, c.target_id,
               c.derive_method, c.model, c.prompt_version, c.result, c.status,
               c.updated_at, e.id AS evidence_id, e.kol_pool_id,
               s.extractor_version AS scanned_version,
               s.cache_updated_at AS scanned_cache_updated_at,
               s.scan_status AS scanned_status,
               s.mention_rows AS scanned_mention_rows
        FROM vkpi_analysis_cache c
        LEFT JOIN vkpi_kol_video_evidence e
          ON c.target_type = 'video' AND c.target_id = CAST(e.id AS TEXT)
        LEFT JOIN vkpi_kol_lens_evidence_scan s ON s.cache_id = c.id
        WHERE c.derive_method = ? AND c.status = 'ready'
        """ + id_clause + """
        ORDER BY c.id ASC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for raw in rows:
        item = dict(raw)
        reuse = canonical_final_v1_cache_reuse(
            item,
            target_type="video",
            target_id=_text(item.get("target_id")),
            derive_method=extractor.FINAL_DERIVE_METHOD,
        )
        item.update(reuse)
        if reuse.get("reusable") is not True and not include_unverified:
            continue
        if not force and not ids:
            same_version = _text(item.get("scanned_version")) == extractor.EXTRACTOR_VERSION
            same_stamp = _ts_text(item.get("scanned_cache_updated_at")) == _ts_text(item.get("updated_at"))
            if same_version and same_stamp:
                continue
        out.append(item)
    return out


__all__ = ["candidate_cache_rows"]

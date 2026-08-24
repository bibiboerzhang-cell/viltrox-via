"""Read-only identity, official-account, and avatar projection for KOL Pool.

The historical pool intentionally keeps every evidence row.  Employee-facing
bulk reads use this module to hide confirmed official accounts and collapse
only conflict-free canonical creator components.  No function in this module
updates a row, chooses a durable merge master, or uses content thumbnails as
profile avatars.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from app.domains.kol.discovery_filters import (
    _competitor_brand_terms,
    discovery_account_gate_verdict,
)
from app.domains.kol.identity import (
    YOUTUBE_CHANNEL_ID_RE,
    canonical_creator_aliases,
    canonical_identity_platform,
)
from app.services.intelligence.account_scan_helpers import _avatar_url_policy
from app.domains.kol.pool_read_projection_cache import cached_global_pool_selection
from app.domains.kol.pool_read_avatar_hydration import (
    profile_avatar_document_expression,
    profile_avatar_value_expression,
)


_CREATOR_ITEM_TYPES = {
    "existing_kol",
    "new_creator",
    "online_qualified_candidate",
    "recall_candidate",
}
_PROFILE_OBJECT_KEYS = (
    "profile",
    "channel",
    "author",
    "authorMeta",
    "owner",
    "user",
    "account",
    "page",
)
_PROFILE_AVATAR_KEYS = (
    "avatar_url",
    "avatarUrl",
    "avatar",
    "avatarUri",
    "profilePicUrlHD",
    "profilePicUrl",
    "profilePictureUrl",
    "profile_image_url",
    "channelAvatar",
    "channelThumbnail",
    "originalAvatarUrl",
    "ownerProfilePicUrl",
    "displayProfilePicUrl",
)
_PUBLIC_IMAGE_CACHE_PREFIXES = (
    "/api/vkpi-media/image-cache/",
    "/api/admin/vkpi/media/image-cache/",
)
_CACHE_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_CONTENT_THUMBNAIL_HOSTS = ("ytimg.com", "img.youtube.com")


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, (str, bytes)) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _pool_probe(row: dict[str, Any]) -> dict[str, Any]:
    raw = _json_obj(row.get("raw_platform_data"))
    return {**raw, **row, "raw_platform_data": raw}


def _session_probe(item: dict[str, Any]) -> dict[str, Any]:
    payload = _json_obj(item.get("payload") or item.get("payload_json"))
    return {
        **payload,
        "kol_pool_id": item.get("kol_pool_id") or payload.get("kol_pool_id"),
        "profile_url": payload.get("profile_url") or item.get("source_url"),
        "source_url": item.get("source_url") or payload.get("source_url"),
    }


def _local_cached_avatar(value: Any) -> bool:
    text = str(value or "").strip()
    for prefix in _PUBLIC_IMAGE_CACHE_PREFIXES:
        if text.startswith(prefix):
            return bool(_CACHE_DIGEST_RE.fullmatch(text.removeprefix(prefix)))
    return False


def _content_thumbnail_url(value: Any) -> bool:
    try:
        host = (urlparse(str(value or "").strip()).hostname or "").lower()
    except ValueError:
        return False
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _CONTENT_THUMBNAIL_HOSTS)


def _strict_profile_avatar_candidates(raw_value: Any) -> list[str]:
    """Return profile-only avatar candidates; never inspect posts/videos/covers."""
    raw = _json_obj(raw_value)
    if not raw:
        return []
    root_kind = str(raw.get("kind") or raw.get("type") or "").lower()
    objects: list[tuple[dict[str, Any], bool]] = [
        (raw, any(token in root_kind for token in ("channel", "account", "profile", "user")))
    ]
    seen_objects: set[int] = set()
    candidates: list[str] = []
    seen_urls: set[str] = set()
    while objects:
        source, allow_snippet_thumbnails = objects.pop(0)
        marker = id(source)
        if marker in seen_objects:
            continue
        seen_objects.add(marker)
        for key in _PROFILE_AVATAR_KEYS:
            value = str(source.get(key) or "").strip()
            if value and value not in seen_urls:
                seen_urls.add(value)
                candidates.append(value)
        if allow_snippet_thumbnails:
            snippet = source.get("snippet") if isinstance(source.get("snippet"), dict) else {}
            thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
            for size in ("high", "medium", "default"):
                entry = thumbnails.get(size)
                value = str(entry.get("url") or "").strip() if isinstance(entry, dict) else ""
                if value and value not in seen_urls:
                    seen_urls.add(value)
                    candidates.append(value)
        for key in _PROFILE_OBJECT_KEYS:
            nested = source.get(key)
            if isinstance(nested, dict):
                objects.append((nested, True))
    return candidates


def _default_cached_avatar_lookup(raw_url: str) -> str:
    try:
        from app.domains.media.cache import cached_image_url

        return str(cached_image_url(raw_url) or "")
    except Exception:
        return ""


def _skip_cached_avatar_lookup(_raw_url: str) -> str:
    return ""


def project_pool_avatar(
    row: dict[str, Any],
    *,
    cached_avatar_lookup: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Project one honest avatar health state without provider or DB writes."""
    lookup = cached_avatar_lookup or _default_cached_avatar_lookup
    candidates: list[tuple[str, str]] = []
    direct = str(row.get("avatar_url") or "").strip()
    if direct:
        candidates.append(("pool_avatar_url", direct))
    extracted = str(row.get("raw_profile_avatar_url") or "").strip()
    if extracted and extracted != direct:
        candidates.append(("raw_profile_avatar", extracted))
    candidates.extend(
        ("raw_profile_avatar", candidate)
        for candidate in _strict_profile_avatar_candidates(row.get("raw_platform_data"))
        if candidate not in {direct, extracted}
    )
    terminal_state = "missing"
    for source, raw_url in candidates:
        if source == "pool_avatar_url" and _content_thumbnail_url(raw_url):
            terminal_state = "invalid"
            continue
        if _local_cached_avatar(raw_url):
            return {
                "avatar_url": raw_url,
                "avatar_url_status": "durable",
                "avatar_upstream_status": "durable",
                "avatar_url_source": source,
                "avatar_fallback": "",
                "avatar_health": {
                    "status": "durable",
                    "upstream_status": "durable",
                    "source": source,
                    "fallback": "",
                },
            }
        usable_url, status = _avatar_url_policy(raw_url)
        if status != "missing":
            terminal_state = status
        cached_url = lookup(raw_url) if status in {"ephemeral", "expired"} else ""
        if cached_url:
            return {
                "avatar_url": cached_url,
                "avatar_url_status": "durable",
                "avatar_upstream_status": status,
                "avatar_url_source": "local_prewarm_cache",
                "avatar_fallback": "",
                "avatar_health": {
                    "status": "durable",
                    "upstream_status": status,
                    "source": "local_prewarm_cache",
                    "fallback": "",
                },
            }
        if usable_url:
            return {
                "avatar_url": usable_url,
                "avatar_url_status": status,
                "avatar_upstream_status": status,
                "avatar_url_source": source,
                "avatar_fallback": "",
                "avatar_health": {
                    "status": status,
                    "upstream_status": status,
                    "source": source,
                    "fallback": "",
                },
            }
    fallback = "initials"
    return {
        "avatar_url": "",
        "avatar_url_status": terminal_state,
        "avatar_upstream_status": terminal_state,
        "avatar_url_source": "initials_fallback",
        "avatar_fallback": fallback,
        "avatar_health": {
            "status": terminal_state,
            "upstream_status": terminal_state,
            "source": "initials_fallback",
            "fallback": fallback,
        },
    }


def _union_components(
    rows: list[dict[str, Any]],
    aliases_by_id: dict[int, set[str]],
) -> list[list[dict[str, Any]]]:
    parents = {int(row["id"]): int(row["id"]) for row in rows}

    def find(value: int) -> int:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    owner: dict[str, int] = {}
    for pool_id in sorted(aliases_by_id):
        for alias in sorted(aliases_by_id[pool_id]):
            if alias in owner:
                union(pool_id, owner[alias])
            else:
                owner[alias] = pool_id
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[find(int(row["id"]))].append(row)
    return list(grouped.values())


def _manual_bridge_conflict_ids(
    rows: list[dict[str, Any]],
    aliases_by_id: dict[int, set[str]],
    session_items: list[dict[str, Any]],
) -> set[int]:
    observed_owner: dict[str, set[int]] = defaultdict(set)
    for pool_id, aliases in aliases_by_id.items():
        for alias in aliases:
            observed_owner[alias].add(pool_id)
    evidence: dict[int, dict[str, set[str]]] = defaultdict(
        lambda: {"ids": set(), "handles": set()}
    )
    reverse_bridge_owner: dict[str, set[int]] = defaultdict(set)
    active_ids = set(aliases_by_id)
    for item in session_items:
        if str(item.get("item_type") or "") not in _CREATOR_ITEM_TYPES:
            continue
        probe = _session_probe(item)
        try:
            pool_id = int(probe.get("kol_pool_id") or 0)
        except (TypeError, ValueError):
            continue
        if pool_id not in active_ids:
            continue
        aliases = canonical_creator_aliases(probe)
        ids = {alias for alias in aliases if alias.startswith("youtube:id:")}
        handles = {alias for alias in aliases if alias.startswith("youtube:handle:")}
        if not ids or not handles:
            continue
        evidence[pool_id]["ids"].update(ids)
        evidence[pool_id]["handles"].update(handles)
        for alias in ids | handles:
            reverse_bridge_owner[alias].add(pool_id)

    conflicts: set[int] = set()
    for pool_id, aliases in evidence.items():
        combined = aliases["ids"] | aliases["handles"]
        if len(aliases["ids"]) != 1 or len(aliases["handles"]) != 1:
            conflicts.add(pool_id)
        for alias in combined:
            other_pool_ids = observed_owner.get(alias, set()) - {pool_id}
            other_bridge_ids = reverse_bridge_owner.get(alias, set()) - {pool_id}
            if other_pool_ids or other_bridge_ids:
                conflicts.update({pool_id, *other_pool_ids, *other_bridge_ids})
    return conflicts


def _top_level_handle_aliases(row: dict[str, Any]) -> set[str]:
    return canonical_creator_aliases(
        {"platform": row.get("platform"), "handle": row.get("handle")}
    )


def _stable_avatar_quality(row: dict[str, Any]) -> int:
    candidates = [("pool_avatar_url", str(row.get("avatar_url") or "").strip())]
    candidates.append(("raw_profile_avatar", str(row.get("raw_profile_avatar_url") or "").strip()))
    candidates.extend(
        ("raw_profile_avatar", value)
        for value in _strict_profile_avatar_candidates(row.get("raw_platform_data"))
    )
    best = 0
    for source, candidate in dict.fromkeys(pair for pair in candidates if pair[1]):
        if source == "pool_avatar_url" and _content_thumbnail_url(candidate):
            continue
        if _local_cached_avatar(candidate):
            return 2
        usable_url, status = _avatar_url_policy(candidate, now_epoch=0)
        if usable_url and status == "durable":
            return 2
        if usable_url and status == "ephemeral":
            best = 1
    return best


def _representative_quality(row: dict[str, Any], shared_aliases: set[str]) -> tuple[int, int]:
    platform = canonical_identity_platform(row.get("platform"))
    raw_handle = str(row.get("handle") or "").strip().lstrip("@")
    top_aliases = _top_level_handle_aliases(row)
    human_handle = bool(raw_handle) and not (
        platform == "youtube" and YOUTUBE_CHANNEL_ID_RE.fullmatch(raw_handle)
    )
    score = 10 if human_handle else 2 if raw_handle else 0
    score += 4 if top_aliases.intersection(shared_aliases) else 0
    profile_aliases = canonical_creator_aliases(
        {"platform": row.get("platform"), "profile_url": row.get("profile_url")}
    )
    score += 2 if profile_aliases.intersection(shared_aliases) else 0
    score += _stable_avatar_quality(row)
    score += sum(bool(str(row.get(key) or "").strip()) for key in ("display_name", "bio"))
    return score, -int(row["id"])


@dataclass(frozen=True)
class PoolReadSelection:
    visible_ids: frozenset[int]
    folded_ids: frozenset[int]
    official_ids: frozenset[int]
    canonical_by_id: dict[int, int]
    audit_by_id: dict[int, dict[str, Any]]
    avatar_by_id: dict[int, dict[str, Any]]
    row_by_id: dict[int, dict[str, Any]]
    diagnostics: dict[str, Any]

    @property
    def excluded_ids(self) -> frozenset[int]:
        return self.folded_ids | self.official_ids

    @property
    def visible_count(self) -> int:
        return len(self.visible_ids)


def build_pool_read_selection(
    rows: list[dict[str, Any]],
    *,
    session_items: list[dict[str, Any]] | None,
    bridge_evidence_available: bool,
) -> PoolReadSelection:
    """Build a deterministic projection from already-read evidence rows."""
    physical_rows: list[dict[str, Any]] = []
    for raw_row in rows:
        row = dict(raw_row)
        if row.get("duplicate_of_id"):
            continue
        row["raw_platform_data"] = _json_obj(row.get("raw_platform_data"))
        physical_rows.append(row)
    row_by_id = {int(row["id"]): row for row in physical_rows}
    avatar_by_id = {
        pool_id: project_pool_avatar(row, cached_avatar_lookup=_skip_cached_avatar_lookup)
        for pool_id, row in row_by_id.items()
    }
    competitor_brands = _competitor_brand_terms()
    probes_by_id = {pool_id: _pool_probe(row) for pool_id, row in row_by_id.items()}
    official_verdicts = {
        pool_id: verdict
        for pool_id, probe in probes_by_id.items()
        if (verdict := discovery_account_gate_verdict(
            probe, competitor_brands=competitor_brands,
        ))
    }
    candidate_rows = [row for pool_id, row in row_by_id.items() if pool_id not in official_verdicts]
    aliases_by_id = {
        int(row["id"]): canonical_creator_aliases(probes_by_id[int(row["id"])])
        for row in candidate_rows
    }
    components = _union_components(candidate_rows, aliases_by_id)
    overlap_ids = {
        int(row["id"])
        for group in components if len(group) > 1
        for row in group
    }
    relevant_session_items = [
        item for item in list(session_items or [])
        if int(item.get("kol_pool_id") or 0) in overlap_ids
    ]
    bridge_conflicts = (
        _manual_bridge_conflict_ids(candidate_rows, aliases_by_id, relevant_session_items)
        if bridge_evidence_available
        else overlap_ids
    )
    visible_ids: set[int] = set()
    folded_ids: set[int] = set()
    canonical_by_id: dict[int, int] = {}
    audit_by_id: dict[int, dict[str, Any]] = {}
    conflict_groups = 0
    folded_groups = 0
    for group in components:
        ids = sorted(int(row["id"]) for row in group)
        frequency: Counter[str] = Counter(
            alias for pool_id in ids for alias in aliases_by_id.get(pool_id, set())
        )
        shared_aliases = {alias for alias, count in frequency.items() if count > 1}
        native_ids = {
            alias for pool_id in ids for alias in aliases_by_id.get(pool_id, set())
            if ":id:" in alias
        }
        conflict = len(group) > 1 and (
            bool(set(ids).intersection(bridge_conflicts)) or len(native_ids) > 1
        )
        if len(group) == 1:
            representative_id = ids[0]
            visible_ids.add(representative_id)
            canonical_by_id[representative_id] = representative_id
            audit_by_id[representative_id] = {
                "canonical_pool_id": representative_id,
                "canonical_duplicate_ids": [],
                "canonical_folded_count": 0,
                "canonical_identity_status": "unique",
                "canonical_shared_aliases": [],
            }
            continue
        if conflict:
            conflict_groups += 1
            for pool_id in ids:
                visible_ids.add(pool_id)
                canonical_by_id[pool_id] = pool_id
                audit_by_id[pool_id] = {
                    "canonical_pool_id": pool_id,
                    "canonical_duplicate_ids": [other for other in ids if other != pool_id],
                    "canonical_folded_count": 0,
                    "canonical_identity_status": "manual_review_conflict",
                    "canonical_shared_aliases": sorted(shared_aliases),
                }
            continue
        folded_groups += 1
        representative = max(group, key=lambda row: _representative_quality(row, shared_aliases))
        representative_id = int(representative["id"])
        duplicates = [pool_id for pool_id in ids if pool_id != representative_id]
        visible_ids.add(representative_id)
        folded_ids.update(duplicates)
        for pool_id in ids:
            canonical_by_id[pool_id] = representative_id
        audit_by_id[representative_id] = {
            "canonical_pool_id": representative_id,
            "canonical_duplicate_ids": duplicates,
            "canonical_folded_count": len(duplicates),
            "canonical_identity_status": "canonical_read_folded",
            "canonical_shared_aliases": sorted(shared_aliases),
        }
    official_ids = frozenset(official_verdicts)
    diagnostics = {
        "method": "canonical_pool_read_projection_v1",
        "physical_master_rows": len(physical_rows),
        "visible_rows": len(visible_ids),
        "canonical_folded_groups": folded_groups,
        "canonical_folded_rows": len(folded_ids),
        "canonical_manual_review_groups": conflict_groups,
        "excluded_confirmed_official": len(official_ids),
        "official_verdict_counts": dict(sorted(Counter(official_verdicts.values()).items())),
        "bridge_evidence_available": bool(bridge_evidence_available),
        "history_rows_deleted": 0,
        "pool_rows_deleted": 0,
        "duplicate_pointer_rows_written": 0,
        "writes_performed": 0,
    }
    return PoolReadSelection(
        visible_ids=frozenset(visible_ids),
        folded_ids=frozenset(folded_ids),
        official_ids=official_ids,
        canonical_by_id=canonical_by_id,
        audit_by_id=audit_by_id,
        avatar_by_id=avatar_by_id,
        row_by_id=row_by_id,
        diagnostics=diagnostics,
    )


def _load_bridge_items(conn: Any, pool_ids: list[int]) -> tuple[list[dict[str, Any]], bool]:
    if not pool_ids:
        return [], True
    try:
        placeholders = ",".join("?" for _ in pool_ids)
        item_types = sorted(_CREATOR_ITEM_TYPES)
        type_placeholders = ",".join("?" for _ in item_types)
        rows = conn.execute(
            f"""
            SELECT id, kol_pool_id, item_type, source_url, payload_json
            FROM vkpi_kol_search_session_items
            WHERE kol_pool_id IN ({placeholders})
              AND item_type IN ({type_placeholders})
            """,
            (*pool_ids, *item_types),
        ).fetchall()
        return [dict(row) for row in rows], True
    except Exception:
        return [], False


def _pool_source_revision(conn: Any) -> str:
    """Return a cheap cross-process watermark for employee-visible Pool reads."""
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS row_count,
                   COALESCE(MAX(id), 0) AS max_id,
                   COALESCE(MAX(CAST(updated_at AS TEXT)), '') AS max_updated_at,
                   COALESCE(SUM(CASE WHEN duplicate_of_id IS NULL THEN 0 ELSE 1 END), 0)
                     AS duplicate_rows
            FROM vkpi_kol_pool
            """
        ).fetchone()
        data = dict(row) if row else {}
        required = ("row_count", "max_id", "max_updated_at", "duplicate_rows")
        if not all(key in data for key in required):
            return ""
        return ":".join(str(data[key] or 0) for key in required)
    except Exception:
        return ""


def prepare_pool_read_selection(
    conn: Any,
    *,
    clause: str,
    params: list[Any] | tuple[Any, ...],
) -> PoolReadSelection:
    source_revision = _pool_source_revision(conn)

    def build() -> PoolReadSelection:
        raw_doc = profile_avatar_document_expression(conn)
        raw_avatar = profile_avatar_value_expression(conn)
        materialized = "MATERIALIZED " if conn.__class__.__name__ == "PostgresCompatConnection" else ""
        if raw_avatar == "NULL":
            query = f"""
            SELECT id, platform, handle, profile_url, display_name, avatar_url,
                   bio, duplicate_of_id, avg_views, engagement_rate,
                   viltrox_fit_score, NULL AS raw_profile_avatar_url
            FROM vkpi_kol_pool {clause} ORDER BY id
            """
        else:
            query = f"""
            WITH pool_read_source AS {materialized}(
                SELECT id, platform, handle, profile_url, display_name, avatar_url,
                       bio, duplicate_of_id, avg_views, engagement_rate,
                       viltrox_fit_score, {raw_doc} AS raw_profile_doc
                FROM vkpi_kol_pool {clause}
            )
            SELECT id, platform, handle, profile_url, display_name, avatar_url,
                   bio, duplicate_of_id, avg_views, engagement_rate,
                   viltrox_fit_score, {raw_avatar} AS raw_profile_avatar_url
            FROM pool_read_source
            ORDER BY id
            """
        rows = conn.execute(query, tuple(params)).fetchall()
        pool_rows = [dict(row) for row in rows]
        session_items, available = _load_bridge_items(
            conn, [int(row["id"]) for row in pool_rows],
        )
        selection = build_pool_read_selection(
            pool_rows, session_items=session_items,
            bridge_evidence_available=available,
        )
        selection.diagnostics["source_revision"] = source_revision or "unavailable"
        return selection

    is_postgres = conn.__class__.__name__ == "PostgresCompatConnection"
    enabled = (
        clause.strip() == "WHERE duplicate_of_id IS NULL"
        and not params
        and is_postgres
        and bool(source_revision)
    )
    return cached_global_pool_selection(
        enabled=enabled,
        builder=build,
        cache_key=source_revision,
    )


def project_existing_pool_rows(conn: Any, rows: list[dict[str, Any]]) -> PoolReadSelection:
    copied = [dict(row) for row in rows]
    session_items, available = _load_bridge_items(
        conn,
        [int(row["id"]) for row in copied if row.get("id")],
    )
    return build_pool_read_selection(
        copied,
        session_items=session_items,
        bridge_evidence_available=available,
    )


def clause_with_pool_read_exclusions(
    clause: str,
    params: list[Any] | tuple[Any, ...],
    selection: PoolReadSelection,
) -> tuple[str, tuple[Any, ...]]:
    excluded = sorted(selection.excluded_ids)
    if not excluded:
        return clause, tuple(params)
    predicate = f"id NOT IN ({','.join('?' for _ in excluded)})"
    projected_clause = f"{clause} AND {predicate}" if clause else f"WHERE {predicate}"
    return projected_clause, (*params, *excluded)


def pool_read_match_clause(
    conn: Any,
    clause: str,
    params: list[Any] | tuple[Any, ...],
    selection: PoolReadSelection,
    *,
    canonical_clause: str = "",
    canonical_params: list[Any] | tuple[Any, ...] = (),
    remap_alias_matches: bool = False,
    allowed_ids: frozenset[int] | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """Map any matching duplicate row to one employee-visible canonical id."""
    if not remap_alias_matches:
        projected_clause, projected_params = clause_with_pool_read_exclusions(clause, params, selection)
        if allowed_ids is None:
            return projected_clause, projected_params
        if not allowed_ids:
            return "WHERE 1=0", ()
        allowed = sorted(allowed_ids)
        predicate = f"id IN ({','.join('?' for _ in allowed)})"
        return f"{projected_clause} AND {predicate}", (*projected_params, *allowed)
    rows = conn.execute(f"SELECT id FROM vkpi_kol_pool {clause}", tuple(params)).fetchall()
    matched_ids: set[int] = set()
    for row in rows:
        try:
            source_id = int(dict(row).get("id") or 0)
        except (TypeError, ValueError):
            continue
        if source_id in selection.official_ids:
            continue
        canonical_id = selection.canonical_by_id.get(source_id, source_id)
        if canonical_id in selection.visible_ids:
            matched_ids.add(canonical_id)
    ordered = sorted(matched_ids)
    if not ordered:
        return "WHERE 1=0", ()
    placeholders = ",".join("?" for _ in ordered)
    scope = canonical_clause or "WHERE duplicate_of_id IS NULL"
    scoped_ids = conn.execute(
        f"SELECT id FROM vkpi_kol_pool {scope} AND id IN ({placeholders})",
        (*canonical_params, *ordered),
    ).fetchall()
    verified = sorted(
        int(dict(row).get("id") or 0) for row in scoped_ids
        if dict(row).get("id") and (allowed_ids is None or int(dict(row)["id"]) in allowed_ids)
    )
    if not verified:
        return "WHERE 1=0", ()
    verified_placeholders = ",".join("?" for _ in verified)
    return f"WHERE id IN ({verified_placeholders})", tuple(verified)


def project_pool_match_rows(
    conn: Any,
    rows: list[dict[str, Any]],
    selection: PoolReadSelection,
) -> list[dict[str, Any]]:
    """Project bounded search hits, remapping folded aliases to full canonical rows."""
    output: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw_row in rows:
        row = dict(raw_row)
        try:
            source_id = int(row.get("id") or row.get("kol_pool_id") or 0)
        except (TypeError, ValueError):
            continue
        if source_id not in selection.row_by_id:
            try:
                duplicate_id = int(row.get("duplicate_of_id") or 0)
            except (TypeError, ValueError):
                duplicate_id = 0
            if not duplicate_id:
                output.append(row)
                continue
            canonical_id = selection.canonical_by_id.get(duplicate_id, duplicate_id)
        else:
            canonical_id = selection.canonical_by_id.get(source_id, source_id)
        if source_id in selection.official_ids:
            continue
        if canonical_id in selection.official_ids:
            continue
        if canonical_id in seen or canonical_id not in selection.visible_ids:
            continue
        seen.add(canonical_id)
        if canonical_id != source_id:
            canonical_row = conn.execute(
                "SELECT * FROM vkpi_kol_pool WHERE id=?",
                (canonical_id,),
            ).fetchone()
            if not canonical_row:
                continue
            row = dict(canonical_row)
        output.append(project_pool_read_item(row, selection))
    return output


def project_pool_read_item(
    item: dict[str, Any],
    selection: PoolReadSelection,
) -> dict[str, Any]:
    projected = dict(item)
    try:
        pool_id = int(projected.get("id") or projected.get("kol_pool_id") or 0)
    except (TypeError, ValueError):
        return projected
    source_row = selection.row_by_id.get(pool_id) or {}
    # Signed provider URLs are time-sensitive.  Re-evaluate them at response
    # time instead of preferring the health state captured in the 30s identity
    # selection cache; otherwise an expired URL can be mislabeled as usable.
    projected.update(project_pool_avatar({**source_row, **projected}))
    projected.update(selection.audit_by_id.get(pool_id) or {})
    return projected


def pool_read_public_fields(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "avatar_url_status",
        "avatar_upstream_status",
        "avatar_url_source",
        "avatar_fallback",
        "avatar_health",
        "canonical_pool_id",
        "canonical_duplicate_ids",
        "canonical_folded_count",
        "canonical_identity_status",
        "canonical_shared_aliases",
    )
    return {key: item.get(key) for key in keys if key in item}


def project_pool_recall_items(conn: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map semantic-recall hits onto the same global Pool read projection."""
    from app.domains.kol.pool_read_recall import project_pool_recall_items as project

    return project(conn, items)

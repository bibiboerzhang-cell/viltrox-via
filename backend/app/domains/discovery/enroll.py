"""发现 → 落库(链1 KOL 自增长)—— 联邦发现的外部候选自动落 vkpi_kol_pool + 去重。

口径(见决策记忆):搜到自动落 Pool;进 MY KOL 仍需手动勾选(落 Pool ≠ 归我)。
红线:新档 source_type=discovered,data 薄诚实;绝不臆造指标;零触 viltrox_fit_score。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.domains.kol.discovery_filters import discovery_account_gate_verdict
from app.domains.kol.identity import explicit_creator_external_identity
from app.repositories.kol_pool_repo import KolPoolRepository

logger = get_logger(__name__)


def _candidate_profile_evidence(candidate: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    """Return validated profile/avatar evidence plus its bounded raw record."""
    from app.domains.kol.contact_system import project_public_profile_url
    from app.services.intelligence.account_scan_helpers import _avatar_url_policy

    profile_url = ""
    for value in (
        candidate.get("profile_url"),
        candidate.get("channel_url"),
        candidate.get("handle") if "://" in str(candidate.get("handle") or "") else "",
    ):
        profile_url = project_public_profile_url(value)
        if profile_url:
            break
    avatar_url, avatar_status = _avatar_url_policy(candidate.get("avatar_url"))
    declared_status = str(candidate.get("avatar_url_status") or "").strip().lower()
    if avatar_status == "missing" and declared_status in {"missing", "expired", "invalid"}:
        avatar_status = declared_status
    evidence = {
        "source": str(candidate.get("source") or "federation")[:80],
        "profile_url": profile_url,
        "channel_url": profile_url,
        "avatar_url": avatar_url,
        "avatar_url_status": avatar_status,
        "thumbnail_url": str(candidate.get("thumbnail_url") or "").strip()[:1000],
        "source_url": str(candidate.get("source_url") or "").strip()[:1000],
    }
    for key in (
        "channel_id",
        "account_id",
        "platform_user_id",
        "user_id",
        "native_id",
    ):
        value = candidate.get(key)
        if value not in (None, ""):
            evidence[key] = str(value)[:200]
    external_id, external_kind = explicit_creator_external_identity(candidate)
    if external_id:
        evidence["external_id"] = external_id[:200]
        evidence["external_id_kind"] = external_kind[:40]
    for key in ("content_id", "video_id", "post_id", "media_id", "aweme_id"):
        value = candidate.get(key)
        if value not in (None, ""):
            evidence[key] = str(value)[:200]
    return profile_url, avatar_url, avatar_status, evidence


def _accepted_avatar_update(
    incumbent_url: Any,
    incumbent_raw: Any,
    incoming_url: str,
    incoming_status: str,
) -> tuple[str, str, bool]:
    """Prefer durable evidence; accept signed CDN URLs only into weak slots."""
    from app.services.intelligence.account_scan_helpers import _avatar_url_policy

    current_url, current_status = _avatar_url_policy(incumbent_url)
    if not current_url and current_status == "missing":
        try:
            raw = json.loads(incumbent_raw) if isinstance(incumbent_raw, str) else dict(incumbent_raw or {})
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {}
        declared = str(raw.get("avatar_url_status") or "").strip().lower()
        if declared in {"missing", "expired", "invalid"}:
            current_status = declared
    if not incoming_url or incoming_status not in {"durable", "ephemeral"}:
        return "", current_status, False
    accepted = incoming_status == "durable" or current_status in {"missing", "expired", "invalid"}
    return (incoming_url if accepted else ""), current_status, accepted


def _request_ephemeral_avatar_prewarm(avatar_url: str, avatar_status: str) -> None:
    if not avatar_url or avatar_status != "ephemeral":
        return
    try:
        from app.domains.kol.profile_discovery_provider import _warm_discovery_avatar_cache

        _warm_discovery_avatar_cache([{"avatar_url": avatar_url}], max_items=1)
    except Exception:
        logger.info("federated avatar proxy prewarm unavailable", exc_info=True)


def enroll_candidates(candidates: list[dict[str, Any]], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """把外部发现候选落 Pool(已在池/缺键 → 跳过;按 platform+handle 去重)。L2:走 KolPoolRepository。"""
    del staff
    repo = KolPoolRepository()
    if not repo.exists():
        return {"status": "unavailable", "enrolled": 0, "skipped": 0}
    enrolled, skipped, ids = 0, 0, []
    excluded_official = 0
    for c in candidates or []:
        if c.get("in_pool") or c.get("kol_pool_id"):
            skipped += 1
            continue
        if discovery_account_gate_verdict(c):
            skipped += 1
            excluded_official += 1
            continue
        platform = str(c.get("platform") or "").strip().lower()
        typed_external_id, _typed_external_kind = explicit_creator_external_identity(c)
        handle = str(
            c.get("handle")
            or c.get("channel_handle")
            or c.get("username")
            or c.get("channel_id")
            or c.get("account_id")
            or c.get("platform_user_id")
            or c.get("user_id")
            or c.get("native_id")
            or typed_external_id
            or ""
        ).strip()
        if not platform or not handle:
            skipped += 1
            continue
        conn = repo._conn()
        savepoint = "vkpi_discovery_enroll_identity"
        try:
            conn.execute(f"SAVEPOINT {savepoint}")
            from app.domains.kol.profile_basics import (
                _canonical_existing_pool_id,
                _lock_creator_identity_write_boundary,
                _merge_presence_payload,
                _record_creator_identity_alias,
            )

            identity_probe = {**c, "platform": platform, "handle": handle}
            _lock_creator_identity_write_boundary(conn, identity_probe)
            canonical_id = _canonical_existing_pool_id(conn, identity_probe)
            existing_id = canonical_id or repo.find_id_by_platform_handle(platform, handle)
            profile_url, avatar_url, avatar_status, raw_evidence = _candidate_profile_evidence(c)
            if existing_id:
                existing = conn.execute(
                    "SELECT avatar_url, raw_platform_data FROM vkpi_kol_pool WHERE id=?",
                    (int(existing_id),),
                ).fetchone()
                existing_data = dict(existing) if existing else {}
                avatar_update, incumbent_status, avatar_accepted = _accepted_avatar_update(
                    existing_data.get("avatar_url"),
                    existing_data.get("raw_platform_data"),
                    avatar_url,
                    avatar_status,
                )
                raw_evidence["avatar_observation_v1"] = {
                    "incoming_status": avatar_status,
                    "incumbent_status": incumbent_status,
                    "decision": "accepted" if avatar_accepted else "kept_incumbent",
                    "proxy_prewarm_requested": bool(avatar_accepted and avatar_status == "ephemeral"),
                }
                if not avatar_accepted:
                    raw_evidence.pop("avatar_url", None)
                    raw_evidence.pop("avatar_url_status", None)
                merged_raw = _merge_presence_payload(
                    existing_data.get("raw_platform_data"),
                    raw_evidence,
                )
                conn.execute(
                    """
                    UPDATE vkpi_kol_pool
                    SET profile_url=COALESCE(NULLIF(TRIM(?), ''), profile_url),
                        avatar_url=COALESCE(NULLIF(TRIM(?), ''), avatar_url),
                        raw_platform_data=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (
                        profile_url,
                        avatar_update,
                        json.dumps(merged_raw, ensure_ascii=False, separators=(",", ":")),
                        int(existing_id),
                    ),
                )
                _record_creator_identity_alias(
                    conn,
                    int(existing_id),
                    identity_probe,
                    canonical_match=True,
                )
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                conn.commit()
                _request_ephemeral_avatar_prewarm(avatar_update, avatar_status)
                skipped += 1
                continue
            if avatar_status == "ephemeral" and avatar_url:
                raw_evidence["avatar_proxy_prewarm"] = "requested"
            new_id = repo.insert_discovered(
                platform=platform, handle=handle,
                name=str(c.get("name") or ""), profile_url=profile_url,
                avatar_url=avatar_url,
                raw_platform_data=json.dumps(raw_evidence, ensure_ascii=False, separators=(",", ":")),
                source_ref=str(c.get("source") or "federation"),
            )
            if new_id:
                _record_creator_identity_alias(
                    conn,
                    int(new_id),
                    identity_probe,
                    canonical_match=False,
                )
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                # The row, alias, and identity locks form one durable unit.
                # Committing here also releases transaction advisory locks
                # before optional buildout/event side effects begin.
                conn.commit()
                _request_ephemeral_avatar_prewarm(avatar_url, avatar_status)
                enrolled += 1
                ids.append(new_id)
                # 发现即建档:联邦路径多无相关度分数 → 按分数分档(无分即 light);best-effort。
                try:
                    from app.domains.discovery.buildout import ignite_profile_buildout

                    ignite_profile_buildout(
                        int(new_id),
                        score=float(c.get("score") or c.get("relevance_score") or 0),
                        source="federated_enroll",
                    )
                except Exception:
                    logger.warning("federated buildout ignite skip", exc_info=True)
                try:  # P1 事件总线:新人被发现入流(best-effort)
                    from app.domains.platform import event_ledger

                    event_ledger.emit(
                        "kol_discovered", entity_type="kol", entity_id=new_id,
                        actor_type="agent", source=str(c.get("source") or "federation"),
                        payload={"platform": platform, "handle": handle},
                    )
                except Exception:
                    logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
                    pass
            else:
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                conn.commit()
        except Exception:
            try:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                logger.warning(
                    "enroll.identity_savepoint_rollback_failed",
                    extra={"platform": platform, "handle": handle},
                    exc_info=True,
                )
            conn.rollback()
            logger.warning("enroll.insert_failed", extra={"platform": platform, "handle": handle}, exc_info=True)
            skipped += 1
    return {"status": "ok", "enrolled": enrolled, "skipped": skipped, "excluded_official": excluded_official, "enrolled_ids": ids,
            "note": "外部候选已落 Pool(source_type=discovered,数据薄);进 MY KOL 仍需手动勾选;零触 viltrox_fit_score。"}


def federated_discover_and_enroll(
    query: str,
    *,
    limit: int = 20,
    staff: dict[str, Any] | None = None,
    include_external: bool = False,
) -> dict[str, Any]:
    """联邦发现 + 自动落库:搜 → 外部候选落 Pool → 返回汇总。"""
    from app.domains.discovery import federation

    found = federation.federated_search(
        query,
        limit=limit,
        staff=staff,
        include_external=bool(include_external),
    )
    results = found.get("results", []) if found.get("status") == "ok" else []
    enroll = enroll_candidates(results, staff=staff)
    return {
        "status": "ok",
        "query": query,
        "sources": found.get("sources", {}),
        "found": len(results),
        "enrolled": enroll.get("enrolled", 0),
        "skipped": enroll.get("skipped", 0),
        "enrolled_ids": enroll.get("enrolled_ids", []),
        "note": "联邦发现→自动落 Pool+去重;商业源未配置则只有自有源结果(诚实)。",
    }

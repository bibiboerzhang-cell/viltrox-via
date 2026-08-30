"""AI Today 读端选行/装配层(2026-08-30 从 ai_today.get_ai_today_hot 提出,行为不变)。

三段职责:
- select_snapshot:90 行窗口内选「完整两阶段 pipeline v1 + grounded」快照,
  旧单阶段 grounded 行只作 legacy 兜底(强制 degraded),拒绝原因只累计比可展示行更新的;
- unavailable_payload:一行可展示的都没有时的诚实元数据(invalid / no_grounded_latest);
- ready_payload:选中行 → 门面响应(market 源合并去重、视频证据合同、鲜度与状态归并)。

协作符号(_validate_* / _market_sources / _recommended_video_rows / _freshness_payload 等)
一律经门面 ai_today 在调用时解析 —— tests 对门面的 monkeypatch 原样生效。
红线:纯读装配,零写库,不碰 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import json
from typing import Any

_Row = tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]


def _at() -> Any:
    """调用时解析门面模块:门面上的 monkeypatch / 运行时替换一律生效。"""
    from app.domains.market import ai_today

    return ai_today


def _parse_row(raw_row: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    at = _at()
    d = dict(raw_row)
    try:
        content = json.loads(d.get("content_json") or "{}")
    except (TypeError, ValueError):
        content = {}
    content = content if isinstance(content, dict) else {}
    contract = at._validate_ai_today_content(content)
    source_contract = at._validate_grounding_sources(content.get("sources"))
    return d, content, contract, source_contract


def _ready_row_action(
    d: dict[str, Any],
    content: dict[str, Any],
    contract: dict[str, Any],
    grounding_sources: list[dict[str, Any]],
    legacy: _Row | None,
    skipped_newer_errors: list[str],
) -> tuple[_Row | None, _Row | None]:
    """grounded ready 行的分拣:pipeline v1 → 选中;否则最多记一个 legacy 兜底候选。"""
    # Codex 清单 D 组:读端只把「完整两阶段 pipeline v1」快照当 ready;
    # 旧单阶段(无 pipeline 标记)快照最多作 stale/degraded 展示,绝不冒充今日就绪。
    provenance = content.get("provenance") if isinstance(content.get("provenance"), dict) else {}
    if str(provenance.get("pipeline") or "") == _at()._AI_TODAY_PIPELINE_VERSION:
        return (d, content, contract, grounding_sources), legacy
    if legacy is None:
        legacy = (d, content, contract, grounding_sources)
        skipped_newer_errors.append("legacy_snapshot:pipeline_v1_required")
    return None, legacy


def select_snapshot(
    rows: list[Any],
) -> tuple[_Row | None, bool, tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None, list[str]]:
    """选行:返回 (selected, legacy_fallback, newest, skipped_newer_errors)。"""
    selected: _Row | None = None
    legacy: _Row | None = None
    newest: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None = None
    skipped_newer_errors: list[str] = []
    for raw_row in rows:
        d, content, contract, source_contract = _parse_row(raw_row)
        if newest is None:
            newest = (d, content, contract, source_contract)
        grounding_sources = list(source_contract.get("value") or [])
        if contract.get("status") == "ready" and source_contract.get("status") == "ready":
            selected, legacy = _ready_row_action(d, content, contract, grounding_sources, legacy, skipped_newer_errors)
            if selected is not None:
                break
            continue
        if legacy is None:
            # 只累计「比可展示行更新」的拒绝原因;legacy 兜底行之下的陈年错误不进门面。
            skipped_newer_errors.extend(
                [
                    *list(contract.get("errors") or []),
                    *list(source_contract.get("errors") or []),
                ]
            )

    legacy_fallback = False
    if selected is None and legacy is not None:
        # 没有任何 pipeline v1 快照时,用最新的旧单阶段 grounded 快照兜底展示,
        # 但强制 degraded(is_ready=False),门面口径「历史快照」而非今日结论。
        selected = legacy
        legacy_fallback = True
    return selected, legacy_fallback, newest, skipped_newer_errors


def unavailable_payload(
    newest: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None,
) -> dict[str, Any]:
    """一行可展示的都没有:诚实元数据(metadata 同时充当 content)。"""
    at = _at()
    d, content, contract, source_contract = newest or (
        {},
        {},
        at._contract_result("invalid", {}, ["result:missing"]),
        at._contract_result("degraded", [], ["sources:missing"]),
    )
    generated_at = at._iso_utc(
        content.get("generated_at") or d.get("created_at") or d.get("snapshot_date")
    )
    freshness = at._freshness_payload(generated_at)
    snapshot_date = str(d.get("snapshot_date") or "")
    contract_status = str(contract.get("status") or "invalid")
    source_status = str(source_contract.get("status") or "degraded")
    result_status = "invalid" if "invalid" in {contract_status, source_status} else "degraded"
    reason = "invalid_result_contract" if result_status == "invalid" else "no_grounded_latest"
    validation_errors = [
        *list(contract.get("errors") or []),
        *list(source_contract.get("errors") or []),
    ]
    metadata = {
        "status": result_status,
        "result_status": result_status,
        "contract_status": contract_status,
        "contract_version": at._RESULT_CONTRACT_VERSION,
        "is_ready": False,
        "grounding_status": "ungrounded",
        "generated_at": generated_at,
        "snapshot_date": snapshot_date,
        "sources": [],
        "evidence": [],
        "validation_errors": validation_errors,
        "provenance": content.get("provenance") if isinstance(content.get("provenance"), dict) else {},
        **freshness,
    }
    return {
        "available": False,
        "reason": reason,
        "model": d.get("model"),
        **metadata,
        "content": metadata,
    }


def _merged_sources(content: dict[str, Any], grounding_sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """快照 grounding 源 + market 源合并(按 URL 去重,保序)。"""
    stored_sources = list(grounding_sources)
    source_urls = {str(source.get("url") or "") for source in stored_sources if isinstance(source, dict)}
    for source in _at()._market_sources(content.get("hot_brands")):
        if source["url"] not in source_urls:
            stored_sources.append(source)
            source_urls.add(source["url"])
    return stored_sources


def _enriched_content(
    d: dict[str, Any],
    content: dict[str, Any],
    contract: dict[str, Any],
    freshness: dict[str, Any],
    generated_at: str,
    stored_sources: list[dict[str, Any]],
    grounding_sources: list[dict[str, Any]],
    evidence_contract: dict[str, Any],
    contract_status: str,
    result_status: str,
    legacy_fallback: bool,
    skipped_newer_errors: list[str],
) -> dict[str, Any]:
    at = _at()
    normalized_content = dict(contract.get("value") or {})
    return {
        **content,
        **normalized_content,
        **freshness,
        "status": result_status,
        "result_status": result_status,
        "contract_status": contract_status,
        "contract_version": at._RESULT_CONTRACT_VERSION,
        "is_ready": result_status == "ready",
        "snapshot_date": str(d.get("snapshot_date") or ""),
        "generated_at": generated_at,
        "grounding_status": "grounded",
        "sources": stored_sources,
        "evidence": grounding_sources,
        "recommended_videos": list(evidence_contract.get("value") or []),
        "validation_errors": [
            *list(evidence_contract.get("errors") or []),
            *(["newer_rows_rejected"] if skipped_newer_errors else []),
            *skipped_newer_errors,
        ],
        "provenance": content.get("provenance") if isinstance(content.get("provenance"), dict) else {},
        **({"reason": "legacy_snapshot_pre_pipeline_v1"} if legacy_fallback else {}),
    }


def ready_payload(selected: _Row, legacy_fallback: bool, skipped_newer_errors: list[str]) -> dict[str, Any]:
    """选中行 → 门面响应(市场源合并、视频证据合同、鲜度/状态归并)。"""
    at = _at()
    d, content, contract, grounding_sources = selected
    stored_sources = _merged_sources(content, grounding_sources)
    generated_at = at._iso_utc(content.get("generated_at") or d.get("created_at") or d.get("snapshot_date"))
    freshness = at._freshness_payload(generated_at)
    evidence_contract = at._validate_video_evidence(
        at._rank_video_candidates(at._recommended_video_rows(), dict(contract.get("value") or {}))
    )
    contract_status = (
        "invalid" if evidence_contract.get("status") == "invalid" else str(contract.get("status") or "invalid")
    )
    if (legacy_fallback or skipped_newer_errors) and contract_status == "ready":
        contract_status = "degraded"
    result_status = at._result_status(
        contract_status,
        str(freshness.get("freshness_status") or "unknown"),
        grounded=True,
    )
    enriched = _enriched_content(
        d,
        content,
        contract,
        freshness,
        generated_at,
        stored_sources,
        grounding_sources,
        evidence_contract,
        contract_status,
        result_status,
        legacy_fallback,
        skipped_newer_errors,
    )
    return {
        "available": True,
        "status": result_status,
        "result_status": result_status,
        "contract_status": contract_status,
        "contract_version": at._RESULT_CONTRACT_VERSION,
        "is_ready": result_status == "ready",
        "model": d.get("model"),
        "snapshot_date": enriched["snapshot_date"],
        "generated_at": generated_at,
        "grounding_status": "grounded",
        "sources": stored_sources,
        **freshness,
        **({"reason": "legacy_snapshot_pre_pipeline_v1"} if legacy_fallback else {}),
        "content": enriched,
    }

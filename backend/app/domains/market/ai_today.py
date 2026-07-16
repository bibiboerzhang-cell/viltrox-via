"""AI Today 今日热点 —— 每早 8点(中国时区)用 LLM 据真实行业热点生成「拍摄方案 + 话题 + 重点决策」。

红线 / 安全:
- LLM 调用前过预算闸 `check_budget("cron:ai_today_hot", est)`(硬上限,见 migration 150 seed)。
- claude client 自带代理(本网络直连被墙,走 HTTPS_PROXY);一天一次小调用。
- 只写本域表 `vkpi_ai_today_hot`,绝不碰 vkpi_kol_pool / viltrox_fit_score / 指纹。
- 无可回跳的 Google grounding citation 时不覆盖 latest;Claude 回退只显式标为 ungrounded。
- 【AI Today 只看外部世界】「外部市场样例」在采样查询层排除三类自家内容(非显示层遮罩):
  ①标题/正文含 viltrox(strpos 参数化,判据同 my_kol_board_ext);②官号帖(账号命中
  vkpi_employee_channels);③合作产出(evidence 挂 project_id)。池收窄如实显示,绝不
  回填自家内容凑数;池空=诚实空态。hot_brands / 市场信号来源同口径剔除自家品牌。
"""
from __future__ import annotations

import inspect
import ipaddress
import json
import logging
import math
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.core.config import CLAUDE_MODEL
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.costs import budget_guard
from app.domains.kol.my_kol_board_ext_sql import VILTROX_TOKEN
from app.domains.market.ai_today_contracts import (
    _FRESH_HOURS,
    _RESULT_CONTRACT_VERSION,
    _as_list,
    _contract_result,
    _extract_grounding_sources,
    _field,
    _freshness_payload,
    _iso_utc,
    _parse_datetime,
    _public_http_url,
    _result_status,
    _stored_grounding_sources,
    _strict_text,
    _strict_text_list,
    _validate_ai_today_content,
    _validate_grounding_sources,
    _validate_video_evidence,
)
from app.domains.market.ai_today_evidence import (
    _MAX_RECOMMENDED_VIDEOS,
    _OWN_CONTENT_EXCLUDED_COUNT_SQL,
    _OWN_OFFICIAL_CHANNEL_COND,
    _OWN_PROJECT_COND,
    _OWN_TITLE_MENTION_COND,
    _SAMPLE_POOL_BASE_WHERE,
    _TOPIC_TERMS,
    _account_from_content_url,
    _analysis_value,
    _log_excluded_own_content_counts as _log_excluded_own_content_counts_impl,
    _market_sources as _market_sources_impl,
    _normalized_account,
    _platform_video_id,
    _rank_video_candidates as _rank_video_candidates_impl,
    _read_hot_brands as _read_hot_brands_impl,
    _recommended_video_rows as _recommended_video_rows_impl,
    _video_content_origin,
)

logger = get_logger(__name__)

_BUDGET_SCOPE = "cron:ai_today_hot"
_EST_COST = 0.05  # 单次估算成本(short prompt + ~900 token out)
_COMPETITORS_FALLBACK = "Sony、Sigma、Tamron、DJI、INSTA360、PROFOTO、Godox、尼康、佳能"
_GENERATION_DEADLINE_SECONDS = 75.0
_PROVIDER_TIMEOUT_SECONDS = 20.0
_MAX_TRANSIENT_ATTEMPTS = 2
_TRANSIENT_ERROR_MARKERS = (
    "408",
    "429",
    "500",
    "502",
    "503",
    "504",
    "connection",
    "deadline",
    "high demand",
    "overload",
    "rate limit",
    "resource_exhausted",
    "temporarily",
    "timeout",
    "unavailable",
)


def _log_excluded_own_content_counts() -> None:
    """Log owned-content exclusions while preserving the legacy module injection surface."""
    _log_excluded_own_content_counts_impl(
        connection_factory=get_conn,
        logger=logger,
        viltrox_token=VILTROX_TOKEN,
        count_sql=_OWN_CONTENT_EXCLUDED_COUNT_SQL,
    )


def _recommended_video_rows(limit: int = 240) -> list[dict[str, Any]]:
    return _recommended_video_rows_impl(
        limit,
        connection_factory=get_conn,
        logger=logger,
        viltrox_token=VILTROX_TOKEN,
        sample_pool_base_where=_SAMPLE_POOL_BASE_WHERE,
        own_title_mention_cond=_OWN_TITLE_MENTION_COND,
        own_project_cond=_OWN_PROJECT_COND,
        own_official_channel_cond=_OWN_OFFICIAL_CHANNEL_COND,
        count_sql=_OWN_CONTENT_EXCLUDED_COUNT_SQL,
    )


def _rank_video_candidates(
    rows: list[dict[str, Any]],
    content: dict[str, Any],
) -> list[dict[str, Any]]:
    return _rank_video_candidates_impl(
        rows,
        content,
        max_recommended_videos=_MAX_RECOMMENDED_VIDEOS,
        topic_terms=_TOPIC_TERMS,
    )


def _market_sources(hot_brands: Any, limit: int = 6) -> list[dict[str, Any]]:
    return _market_sources_impl(
        hot_brands,
        limit,
        connection_factory=get_conn,
        logger=logger,
        viltrox_token=VILTROX_TOKEN,
    )


def _read_hot_brands(ops_dir: str = "runtime/ops", limit: int = 6) -> list[str]:
    return _read_hot_brands_impl(
        ops_dir,
        limit,
        logger=logger,
        viltrox_token=VILTROX_TOKEN,
    )


def _parse_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text[3:] else text[3:]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, dict):
            return obj
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    # 兜底:Gemini 接地输出常带引用/前后说明文字 → 抽取第一个 { 到最后一个 } 再解析。
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else {}
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    return {}


def _ensure_schema() -> None:
    get_conn().execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_ai_today_hot (
            snapshot_date DATE PRIMARY KEY,
            content_json  TEXT NOT NULL,
            model         TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    get_conn().commit()


def _is_transient_provider_error(exc: Exception) -> bool:
    message = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in message for marker in _TRANSIENT_ERROR_MARKERS)


def _generation_parts(
    result: Any,
) -> tuple[str, str, Any, dict[str, Any]]:
    """Normalize current and legacy generation results without weakening list types."""
    if isinstance(result, tuple) and len(result) >= 4:
        provenance = dict(result[3]) if isinstance(result[3], dict) else {}
        return str(result[0] or ""), str(result[1] or ""), result[2], provenance
    if isinstance(result, tuple) and len(result) >= 3:
        model = str(result[1] or "")
        provider = "google" if model.startswith("gemini:") else "anthropic" if model.startswith("claude:") else "unknown"
        return str(result[0] or ""), model, result[2], {
            "provider": provider,
            "model": model,
            "fallback_used": provider != "google",
            "attempts": [],
        }
    if isinstance(result, tuple) and len(result) >= 2:
        return str(result[0] or ""), str(result[1] or ""), [], {"provider": "unknown", "attempts": []}
    return "", "", [], {"provider": "none", "attempts": []}


def _generation_provenance(
    *,
    started_at: float,
    provider: str,
    model: str,
    attempts: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    fallback_used: bool,
    status: str,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "grounding_tool": "google_search" if provider == "google" else "none",
        "source_urls": [source["url"] for source in sources if isinstance(source, dict) and source.get("url")],
        "fallback_used": bool(fallback_used),
        "status": status,
        "attempts": attempts,
        "deadline_seconds": _GENERATION_DEADLINE_SECONDS,
        "elapsed_ms": max(0, round((time.monotonic() - started_at) * 1000)),
    }


def _provider_call_attempted(provenance: Any) -> bool:
    if not isinstance(provenance, dict) or not isinstance(provenance.get("attempts"), list):
        return False
    return any(isinstance(attempt, dict) and "attempt" in attempt for attempt in provenance["attempts"])


def _contract_is_ready(
    parsed: Any,
    validator: Callable[[Any], dict[str, Any]] | None,
) -> tuple[bool, str]:
    if not isinstance(parsed, dict):
        return False, "invalid_json_object"
    if validator is None:
        return True, "ready"
    validation = validator(parsed)
    return validation.get("status") == "ready", str(validation.get("status") or "invalid")


def _call_generator(
    generator: Callable[..., Any],
    prompt: str,
    validator: Callable[[Any], dict[str, Any]],
) -> Any:
    """Keep legacy test/caller generators usable while the real generator accepts a validator."""
    try:
        parameters = inspect.signature(generator).parameters.values()
        supports_validator = any(
            parameter.name == "validator" or parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    except (TypeError, ValueError):
        supports_validator = True
    return generator(prompt, validator=validator) if supports_validator else generator(prompt)


def _generate(
    prompt: str,
    validator: Callable[[Any], dict[str, Any]] | None = None,
) -> tuple[str, str, list[dict[str, Any]], dict[str, Any]]:
    """Use bounded direct SDK calls until a strict grounded-JSON boundary exists.

    Migration debt is deliberate: plain ``llm_production.generate_json`` cannot
    request Google Search or return candidate-level citation metadata. Replacing
    this path before both the tool and citation contracts exist would make a
    generated claim look grounded when it is not, or permanently degrade this
    surface to an unpersisted fallback.
    """
    started_at = time.monotonic()
    deadline_at = started_at + _GENERATION_DEADLINE_SECONDS
    attempts: list[dict[str, Any]] = []
    last_raw = ""
    last_model = ""
    last_sources: list[dict[str, Any]] = []

    # Gemini must remain direct: the gateway JSON contract cannot request Google Search
    # or return candidate-level grounding citations. SDK retries are disabled here so
    # this function owns the finite retry budget and the total deadline.
    try:
        import app.core.config  # noqa: F401  触发 .env 加载(GOOGLE/GEMINI key)
        import app.services.ai.clients.gemini_client as gc
        from google.genai import types

        from app.core.config import GEMINI_MODEL

        client = getattr(gc, "gemini_client", None)
        if client is not None:
            candidates: list[str] = []
            for model_name in (GEMINI_MODEL, "gemini-2.5-flash"):
                if model_name and model_name not in candidates:
                    candidates.append(model_name)
            for model_name in candidates:
                for attempt_number in range(1, _MAX_TRANSIENT_ATTEMPTS + 1):
                    remaining = deadline_at - time.monotonic()
                    if remaining <= 0:
                        attempts.append({"provider": "google", "model": model_name, "status": "deadline_exceeded"})
                        break
                    request_timeout = min(_PROVIDER_TIMEOUT_SECONDS, remaining)
                    cfg = types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        response_mime_type="application/json",
                        http_options=types.HttpOptions(
                            timeout=max(1, int(request_timeout * 1000)),
                            retry_options=types.HttpRetryOptions(attempts=1),
                        ),
                    )
                    try:
                        response = client.models.generate_content(
                            model=model_name,
                            contents=prompt,
                            config=cfg,
                        )
                        text = (getattr(response, "text", "") or "").strip()
                        sources = _extract_grounding_sources(response)
                        parsed = _parse_json(text) if text else {}
                        contract_ready, contract_status = _contract_is_ready(parsed, validator)
                        last_raw = text
                        last_model = f"gemini:{model_name}+google_search"
                        last_sources = sources
                        status = "success" if contract_ready and sources else (
                            "missing_grounding" if contract_ready else f"contract_{contract_status}"
                        )
                        attempts.append(
                            {
                                "provider": "google",
                                "model": model_name,
                                "attempt": attempt_number,
                                "status": status,
                            }
                        )
                        if contract_ready and sources:
                            return text, last_model, sources, _generation_provenance(
                                started_at=started_at,
                                provider="google",
                                model=model_name,
                                attempts=attempts,
                                sources=sources,
                                fallback_used=False,
                                status="success",
                            )
                        break
                    except Exception as exc:
                        transient = _is_transient_provider_error(exc)
                        attempts.append(
                            {
                                "provider": "google",
                                "model": model_name,
                                "attempt": attempt_number,
                                "status": "transient_error" if transient else "permanent_error",
                                "error_type": type(exc).__name__,
                            }
                        )
                        if not transient or attempt_number >= _MAX_TRANSIENT_ATTEMPTS:
                            break
                        delay = min(1.0 * (2 ** (attempt_number - 1)), max(0.0, deadline_at - time.monotonic()))
                        if delay:
                            time.sleep(delay)
            logger.warning("ai_today.gemini_unavailable_after_bounded_attempts_fallback_claude")
    except Exception as exc:
        attempts.append(
            {
                "provider": "google",
                "status": "setup_error",
                "error_type": type(exc).__name__,
            }
        )
        logger.warning("ai_today.gemini_failed_fallback_claude", exc_info=True)

    # Claude is an explicitly ungrounded fallback. It is validated with the same
    # contract but never replaces the latest grounded row.
    try:
        from app.services.ai.clients.claude_client import get_claude_client
        from app.services.ai.retry import call_ai_with_retry

        client = get_claude_client()
        if client is None:
            raise RuntimeError("claude client unavailable")
        for attempt_number in range(1, _MAX_TRANSIENT_ATTEMPTS + 1):
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                attempts.append({"provider": "anthropic", "model": CLAUDE_MODEL, "status": "deadline_exceeded"})
                break
            request_timeout = min(_PROVIDER_TIMEOUT_SECONDS, remaining)
            try:
                request_client = client.with_options(timeout=request_timeout, max_retries=0)
                response = call_ai_with_retry(
                    "ai_today.hot",
                    lambda: request_client.messages.create(
                        model=CLAUDE_MODEL,
                        max_tokens=2048,
                        messages=[{"role": "user", "content": prompt}],
                    ),
                    attempts=1,
                    base_delay_sec=0,
                )
                text = (
                    (response.content[0].text or "").strip()
                    if response and getattr(response, "content", None)
                    else ""
                )
                parsed = _parse_json(text) if text else {}
                contract_ready, contract_status = _contract_is_ready(parsed, validator)
                attempts.append(
                    {
                        "provider": "anthropic",
                        "model": CLAUDE_MODEL,
                        "attempt": attempt_number,
                        "status": "success_ungrounded" if contract_ready else f"contract_{contract_status}",
                    }
                )
                return text, f"claude:{CLAUDE_MODEL}", [], _generation_provenance(
                    started_at=started_at,
                    provider="anthropic",
                    model=CLAUDE_MODEL,
                    attempts=attempts,
                    sources=[],
                    fallback_used=True,
                    status="success_ungrounded" if contract_ready else f"contract_{contract_status}",
                )
            except Exception as exc:
                transient = _is_transient_provider_error(exc)
                attempts.append(
                    {
                        "provider": "anthropic",
                        "model": CLAUDE_MODEL,
                        "attempt": attempt_number,
                        "status": "transient_error" if transient else "permanent_error",
                        "error_type": type(exc).__name__,
                    }
                )
                if not transient or attempt_number >= _MAX_TRANSIENT_ATTEMPTS:
                    break
                delay = min(1.0 * (2 ** (attempt_number - 1)), max(0.0, deadline_at - time.monotonic()))
                if delay:
                    time.sleep(delay)
    except Exception as exc:
        attempts.append(
            {
                "provider": "anthropic",
                "status": "setup_error",
                "error_type": type(exc).__name__,
            }
        )
        logger.warning("ai_today.claude_failed", exc_info=True)

    provider = "google" if last_model.startswith("gemini:") else "none"
    model = last_model.removeprefix("gemini:").removesuffix("+google_search") if provider == "google" else ""
    final_status = "deadline_exceeded" if time.monotonic() >= deadline_at else "all_providers_failed"
    return last_raw, last_model, last_sources, _generation_provenance(
        started_at=started_at,
        provider=provider,
        model=model,
        attempts=attempts,
        sources=last_sources,
        fallback_used=True,
        status=final_status,
    )


def generate_ai_today_hot() -> dict[str, Any]:
    """每早一次:预算闸 → Gemini(Google 接地)生成 → 仅 grounded claim 存库。"""
    if not budget_guard.check_budget(_BUDGET_SCOPE, _EST_COST):
        logger.info("ai_today.budget_blocked", extra={"scope": _BUDGET_SCOPE})
        return {
            "status": "budget_blocked",
            "result_status": "degraded",
            "contract_status": "degraded",
            "contract_version": _RESULT_CONTRACT_VERSION,
            "reason": "budget_blocked",
            "provenance": {
                "provider": "none",
                "status": "budget_blocked",
                "attempts": [],
                "fallback_used": False,
            },
        }
    hot = _read_hot_brands()
    hot_line = ("当前竞品/行业热点信号:" + "、".join(hot)) if hot else f"行业主要竞品:{_COMPETITORS_FALLBACK}"
    today_label = datetime.now(tz=timezone.utc).strftime("%Y年%m月%d日")
    prompt = (
        f"【重要·今天的真实日期是 {today_label}】请**严格按此日期**判断「当下/最近」热点;绝不要把往年(如 2025 年)\n"
        f"的赛事/发布当成正在进行的当下事件。若你无法实时联网搜索,就基于这个真实日期给出贴合当前季节的通用拍摄\n"
        f"方向,**不要编造你无法确认的、具体「正在进行」的赛事或新品发布**(宁可笼统也不要错报时间)。\n"
        f"你是 Viltrox(唯卓仕)面向【海外/国际市场】的内容策划。Viltrox 主销欧美/全球,目标受众是\n"
        f"海外摄影/视频创作者。{hot_line}。\n"
        "请先用 Google 搜索查清【当下海外·国际(非中国大陆)摄影/影像圈正在火的真实热点】:国际摄影/\n"
        "影视赛事(如 LensCulture、Sony World Photography Awards、IPA 等)、Instagram/YouTube/Reddit/TikTok\n"
        "上正流行的拍摄玩法/风格、海外创作者热议的话题。**务必只取海外/英文圈内容,绝不要小红书/抖音/微博\n"
        "等中国大陆平台的热点。** 基于搜索到的真实近况,不要编。\n"
        "**关键:热点不是越火越好,要按【与 Viltrox 产品的关联度】筛选+排序** —— Viltrox 主打大光圈定焦\n"
        "(如 AF 27/35/56/85mm F1.x、135mm F1.8 LAB 旗舰)、变形宽荧幕电影镜、轻量广角等。优先选能直接\n"
        "借势到这些镜头/拍法的热点(如弱光人像、电影感Vlog、复古街拍);每条 hot_topic 都要能落到一类\n"
        "我们能借势的镜头或拍法,纯无关的热点(如纯无人机竞速)不要。\n"
        "再据此生成今天的内容建议,具体可执行、贴合海外创作者口味、紧扣真实当下热点。\n"
        "严格只输出 JSON(不要多余文字):\n"
        '{\n'
        '  "headline": "一句今日重点决策(中文,<=28字)",\n'
        '  "shooting_plans": ["拍摄方案1:场景+用哪类镜头+卖点(面向海外创作者)", "方案2", "方案3"],\n'
        '  "hot_topics": ["真实海外当下热点/国际赛事/流行玩法1(带时间或来源)", "热点2", "热点3"]\n'
        '}\n'
    )
    raw, model_used, sources, provenance = _generation_parts(
        _call_generator(_generate, prompt, _validate_ai_today_content)
    )
    content = _parse_json(raw)
    generated_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    if _provider_call_attempted(provenance):
        try:
            budget_guard.record_cost(scope=_BUDGET_SCOPE, cost_usd=_EST_COST)
        except Exception:
            logger.debug("ai_today.record_cost_failed", exc_info=True)

    contract = _validate_ai_today_content(content)
    contract_status = str(contract.get("status") or "invalid")
    if contract_status != "ready":
        reason = "invalid_result_contract" if contract_status == "invalid" else "partial_result_contract"
        logger.warning(
            "ai_today.result_contract_rejected",
            extra={"status": contract_status, "errors": contract.get("errors")},
        )
        return {
            "status": contract_status,
            "result_status": contract_status,
            "contract_status": contract_status,
            "contract_version": _RESULT_CONTRACT_VERSION,
            "reason": reason,
            "validation_errors": list(contract.get("errors") or []),
            "model": str(model_used or ""),
            "provenance": provenance,
            "generated_at": generated_at,
        }

    source_contract = _validate_grounding_sources(sources)
    grounding_sources = list(source_contract.get("value") or [])
    grounding_status = "grounded" if source_contract.get("status") == "ready" else "ungrounded"

    if grounding_status != "grounded":
        is_claude_fallback = str(model_used or "").startswith("claude:")
        reason = (
            "invalid_grounding_contract"
            if source_contract.get("status") == "invalid"
            else "claude_fallback_without_grounding"
            if is_claude_fallback
            else "no_grounded_citations"
        )
        result_status = "invalid" if source_contract.get("status") == "invalid" else "degraded"
        logger.warning(
            "ai_today.ungrounded_not_persisted",
            extra={"model": model_used, "reason": reason},
        )
        return {
            "status": "ungrounded",
            "result_status": result_status,
            "contract_status": result_status,
            "contract_version": _RESULT_CONTRACT_VERSION,
            "reason": reason,
            "grounding_status": "ungrounded",
            "model": str(model_used or ""),
            "sources": [],
            "validation_errors": list(source_contract.get("errors") or []),
            "provenance": provenance,
            "generated_at": generated_at,
        }

    _ensure_schema()
    normalized_content = dict(contract.get("value") or {})
    payload = {
        "headline": normalized_content["headline"],
        "shooting_plans": normalized_content["shooting_plans"],
        "hot_topics": normalized_content["hot_topics"],
        "hot_brands": hot,
        "sources": grounding_sources,
        "evidence": grounding_sources,
        "grounding_status": "grounded",
        "status": "ready",
        "result_status": "ready",
        "contract_status": "ready",
        "contract_version": _RESULT_CONTRACT_VERSION,
        "provenance": provenance,
        "generated_at": generated_at,
    }
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_ai_today_hot (snapshot_date, content_json, model)
        VALUES (CURRENT_DATE, ?, ?)
        ON CONFLICT (snapshot_date) DO UPDATE
          SET content_json = excluded.content_json, model = excluded.model, created_at = now()
        """,
        (json.dumps(payload, ensure_ascii=False), str(model_used or "")),
    )
    conn.commit()
    logger.info("ai_today.generated", extra={"plans": len(payload["shooting_plans"]), "brands": len(hot)})
    return {
        "status": "ok",
        "result_status": "ready",
        "contract_status": "ready",
        "contract_version": _RESULT_CONTRACT_VERSION,
        "shooting_plans": len(payload["shooting_plans"]),
        "grounding_status": "grounded",
        "sources": grounding_sources,
        "provenance": provenance,
        "generated_at": generated_at,
    }


def get_ai_today_hot() -> dict[str, Any]:
    """读最新有直接 grounding citation 的 AI Today，并只读附加市场/视频证据。"""
    try:
        _ensure_schema()
        rows = get_conn().execute(
            "SELECT snapshot_date, content_json, model, created_at FROM vkpi_ai_today_hot "
            "ORDER BY snapshot_date DESC LIMIT 90"
        ).fetchall()
        if not rows:
            return {
                "available": False,
                "status": "invalid",
                "result_status": "invalid",
                "is_ready": False,
                "reason": "not_generated_yet",
            }

        selected: tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]] | None = None
        newest: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None = None
        skipped_newer_errors: list[str] = []
        for raw_row in rows:
            d = dict(raw_row)
            try:
                content = json.loads(d.get("content_json") or "{}")
            except (TypeError, ValueError):
                content = {}
            content = content if isinstance(content, dict) else {}
            contract = _validate_ai_today_content(content)
            source_contract = _validate_grounding_sources(content.get("sources"))
            if newest is None:
                newest = (d, content, contract, source_contract)
            grounding_sources = list(source_contract.get("value") or [])
            if contract.get("status") == "ready" and source_contract.get("status") == "ready":
                selected = (d, content, contract, grounding_sources)
                break
            skipped_newer_errors.extend(
                [
                    *list(contract.get("errors") or []),
                    *list(source_contract.get("errors") or []),
                ]
            )

        if selected is None:
            d, content, contract, source_contract = newest or (
                {},
                {},
                _contract_result("invalid", {}, ["result:missing"]),
                _contract_result("degraded", [], ["sources:missing"]),
            )
            generated_at = _iso_utc(
                content.get("generated_at") or d.get("created_at") or d.get("snapshot_date")
            )
            freshness = _freshness_payload(generated_at)
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
                "contract_version": _RESULT_CONTRACT_VERSION,
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

        d, content, contract, grounding_sources = selected
        stored_sources = list(grounding_sources)
        source_urls = {str(source.get("url") or "") for source in stored_sources if isinstance(source, dict)}
        for source in _market_sources(content.get("hot_brands")):
            if source["url"] not in source_urls:
                stored_sources.append(source)
                source_urls.add(source["url"])
        generated_at = _iso_utc(content.get("generated_at") or d.get("created_at") or d.get("snapshot_date"))
        freshness = _freshness_payload(generated_at)
        evidence_contract = _validate_video_evidence(
            _rank_video_candidates(_recommended_video_rows(), dict(contract.get("value") or {}))
        )
        contract_status = (
            "invalid" if evidence_contract.get("status") == "invalid" else str(contract.get("status") or "invalid")
        )
        if skipped_newer_errors and contract_status == "ready":
            contract_status = "degraded"
        result_status = _result_status(
            contract_status,
            str(freshness.get("freshness_status") or "unknown"),
            grounded=True,
        )
        normalized_content = dict(contract.get("value") or {})
        enriched = {
            **content,
            **normalized_content,
            **freshness,
            "status": result_status,
            "result_status": result_status,
            "contract_status": contract_status,
            "contract_version": _RESULT_CONTRACT_VERSION,
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
        }
        return {
            "available": True,
            "status": result_status,
            "result_status": result_status,
            "contract_status": contract_status,
            "contract_version": _RESULT_CONTRACT_VERSION,
            "is_ready": result_status == "ready",
            "model": d.get("model"),
            "snapshot_date": enriched["snapshot_date"],
            "generated_at": generated_at,
            "grounding_status": "grounded",
            "sources": stored_sources,
            **freshness,
            "content": enriched,
        }
    except Exception:
        logger.debug("ai_today.get_failed", exc_info=True)
        return {
            "available": False,
            "status": "invalid",
            "result_status": "invalid",
            "is_ready": False,
            "reason": "read_error",
        }

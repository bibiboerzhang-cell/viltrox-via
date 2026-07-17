"""domains/market/sentiment_annotate.py — V0g 情绪批注管线(打包批注,默认 dry-run)。

背景:vkpi_sentiment_results(迁移 051)已建但 0 行、vkpi_comments.sentiment_id 全空。
既有 comments/sentiment.py 是「一条评论一次 LLM 调用」的单发路径(12,703 条 = 12,703 次调用,
token 大头全烧在重复的系统指令上)。本模块是它的「打包」兄弟:一次调用装 25-50 条评论,
LLM 返回 JSON 数组 {id, score(-1..1), label(pos/neg/neu), aspects[]},解析后写
vkpi_sentiment_results 并回填 vkpi_comments.sentiment_id。

安全护栏(全部默认最保守):
- annotate_batch(dry_run=True) 是默认:只统计待处理量 + 预估 token/成本,零 LLM、零落库。
- 每次运行硬上限条数:env VKPI_SENTIMENT_ANNOTATE_MAX_PER_RUN(默认 200)。
- 走 llm_production.generate_json(精确模型/运行就绪/原子预留/台账全沿用,绝不绕过);
  严格边界返回降级(预算闸拦/无 key/运行证据不足)→ 立即停跑,绝不写「假中性」占坑
  (占坑会把 sentiment_id 填上,永远挡住真批注)。
- 结果缓存:选中评论若已有 vkpi_sentiment_results 行(任意 prompt_version,如单发路径写过),
  直接回链 sentiment_id,不再烧 LLM;写入侧 ON CONFLICT (comment_id, prompt_version) 幂等。
- 红线:零触 viltrox_fit_score / rule_v0;不碰 .env;additive,不改任何既有表结构。

选型:preferred_provider 默认 google(gemini-flash-latest)—— gateway PROVIDER_CONFIG 里
最便宜(input $0.07/M、output $0.30/M tok;对比 gpt-5.4-mini $0.25/$2.00、claude $0.25/$1.25)。

成本口径(全量 12,703 条 pending、pack=40 → ceil(12703/40)=318 次调用):
  input ≈ 318 × (~180 头部 + 40×~50 评论) ≈ 0.69M tok;output ≈ 318 × (40×40+60) ≈ 0.53M tok。
  gemini-flash-latest:0.69×$0.07 + 0.53×$0.30 ≈ **$0.21**(留冗余按 ~$0.3 报)。
  gpt-5.4-mini ≈ $1.23;claude ≈ $0.83。单 run 硬上限 200 条 = 5 次调用 ≈ $0.0033。

注:任务书返回 schema 为 {id, score, label, aspects};表 051 无 score/aspects 专列——
label 映射进 sentiment 列,|score| 作 sentiment_confidence(极性强度代理),
aspects 仅聚合进本次运行摘要(aspects_top),持久化留待后续迁移(本刀不动表)。
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any

from app.core import model_registry
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.platform import llm_gateway, llm_production

logger = get_logger("viltrox.domains.market.sentiment_annotate")

PROMPT_VERSION = "batch_v1"          # 与单发路径 v1.0 分口径,UNIQUE(comment_id, prompt_version) 互不冲突
PURPOSE = "vkpi_sentiment_annotate"  # gateway cost_scope 自动成 cron:vkpi_sentiment_annotate
TASK_BINDING = "vkpi_sentiment_annotate"  # model_registry 任务绑定键 = 绑定唯一真源
DEFAULT_PROVIDER = "google"          # 仅作展示/成本预估兜底;真绑定见 _sentiment_binding()

HARD_CAP_ENV = "VKPI_SENTIMENT_ANNOTATE_MAX_PER_RUN"
DEFAULT_HARD_CAP = 200
PACK_SIZE_ENV = "VKPI_SENTIMENT_ANNOTATE_PACK_SIZE"
DEFAULT_PACK_SIZE = 40               # 一次调用打包条数(25-50 区间取中偏上)
COMMENT_CHAR_CAP = 400               # 单条评论截断,控 input token
OUTPUT_TOKENS_PER_COMMENT = 60       # 预算 max_output_tokens 用(实付按真用量,上限放宽零成本)
OUTPUT_TOKENS_SLACK = 60
MODEL_ENV = "VKPI_SENTIMENT_ANNOTATE_MODEL"
PROVIDER_ENV = "VKPI_SENTIMENT_ANNOTATE_PROVIDER"

LABEL_MAP = {
    "pos": "positive", "positive": "positive",
    "neg": "negative", "negative": "negative",
    "neu": "neutral", "neutral": "neutral",
}

_PENDING_WHERE = "sentiment_id IS NULL AND comment_text IS NOT NULL AND TRIM(comment_text) <> ''"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_hard_cap() -> int:
    """每次运行硬上限条数(env 可调,默认 200)。"""
    try:
        return max(1, int(os.environ.get(HARD_CAP_ENV, "") or DEFAULT_HARD_CAP))
    except (TypeError, ValueError):
        return DEFAULT_HARD_CAP


def pack_size() -> int:
    """单次 LLM 调用打包条数(env 可调,默认 40,封顶 50)。"""
    try:
        return max(1, min(50, int(os.environ.get(PACK_SIZE_ENV, "") or DEFAULT_PACK_SIZE)))
    except (TypeError, ValueError):
        return DEFAULT_PACK_SIZE


def _est_tokens(text: str) -> int:
    """与 gateway 同口径的保守估算(len/4),不为数 token 发起任何请求。"""
    return max(1, len(str(text or "")) // 4)


def _estimate_cost_usd(input_tokens: int, output_tokens: int, provider: str = DEFAULT_PROVIDER) -> float:
    """按 gateway PROVIDER_CONFIG 单价折 USD(单价单一真源,不在本模块另抄价目)。"""
    cfg = llm_gateway.PROVIDER_CONFIG.get(provider) or {}
    in_rate = float(cfg.get("input_cents_per_million") or 0)
    out_rate = float(cfg.get("output_cents_per_million") or 0)
    return (int(input_tokens or 0) * in_rate + int(output_tokens or 0) * out_rate) / 100.0 / 1_000_000


def _sentiment_binding() -> tuple[str, str]:
    """Resolve one explicit provider/model pair; never return a fallback chain.

    唯一真源 = model_registry 的 vkpi_sentiment_annotate 任务绑定——generate_json 的
    task_binding 硬闸用同一 current_task_model_binding() 校验。此前本函数自带
    GEMINI_MODEL/OPENAI_MODEL/CLAUDE_MODEL defaults 与 registry 分叉:GEMINI_MODEL
    等全局 env 一漂移即恒 task_binding_model_mismatch 停摆。
    VKPI_SENTIMENT_ANNOTATE_PROVIDER/MODEL 显式覆盖仍可用,但必须成对给出;
    只给一个时打警告并回落 registry 默认绑定,防半覆盖分叉出 openai/gemini-* 之类怪胎。
    """

    env_provider = str(os.environ.get(PROVIDER_ENV) or "").strip().lower()
    env_model = str(os.environ.get(MODEL_ENV) or "").strip()
    if bool(env_provider) != bool(env_model):
        logger.warning(
            "sentiment_annotate.partial_env_override_ignored",
            extra={"provider_env": env_provider or "(unset)", "model_env": env_model or "(unset)"},
        )
        binding = model_registry.TASK_MODEL_BINDING.get(TASK_BINDING, "")
    else:
        # 双覆盖时 current_task_model_binding 已按 TASK_MODEL_ENV_KEYS 吃进这对 env;
        # 全缺省时它就是 registry 默认绑定。一条路径,恒与 generate_json 的期望一致。
        binding = model_registry.current_task_model_binding().get(TASK_BINDING, "")
    provider, model = model_registry.split_binding(binding)
    provider = {"gemini": "google", "claude": "anthropic"}.get(provider, provider)
    if not provider or not model:
        raise ValueError("unsupported_sentiment_model_binding")
    return provider, model


def _count_pending(conn: Any) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS n FROM vkpi_comments WHERE {_PENDING_WHERE}").fetchone()
    return int(dict(row).get("n") or 0) if row else 0


def _select_pending(conn: Any, limit: int) -> list[dict[str, Any]]:
    # 随机序取批:回填顺序无业务意义,而确定性排序会让一个 schema_failure
    # 毒包永远排头、fail-fast 停跑后挡死其后全部好包(2026-07-16 排空实测
    # 1,875 条被同一包卡死)。随机序让好包在多轮重跑中自然排空;毒包本体
    # 仍如实停跑、绝不写假中性占坑。
    rows = conn.execute(
        f"""
        SELECT id, comment_text, language_detected
        FROM vkpi_comments
        WHERE {_PENDING_WHERE}
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in (rows or [])]


def _existing_result_ids(conn: Any, comment_ids: list[int]) -> dict[int, int]:
    """结果缓存查询:这些评论里谁已有 sentiment 结果行(任意 prompt_version)→ {comment_id: result_id}。"""
    if not comment_ids:
        return {}
    placeholders = ",".join("?" for _ in comment_ids)
    rows = conn.execute(
        f"SELECT comment_id, id FROM vkpi_sentiment_results WHERE comment_id IN ({placeholders}) ORDER BY id",
        tuple(int(c) for c in comment_ids),
    ).fetchall()
    out: dict[int, int] = {}
    for r in rows or []:
        d = dict(r)
        out[int(d["comment_id"])] = int(d["id"])  # ORDER BY id → 同评论多行时留最新
    return out


def build_packed_prompt(items: list[dict[str, Any]]) -> str:
    """打包 prompt:头部指令一次,评论逐行 [id] text。返回带 items 的严格 JSON 对象。"""
    lines = []
    for it in items:
        text = " ".join(str(it.get("comment_text") or "").split())[:COMMENT_CHAR_CAP]
        lines.append(f"[{int(it['id'])}] {text}")
    body = "\n".join(lines)
    return (
        "You are a sentiment annotator for Viltrox, a camera lens brand, analyzing social media comments.\n"
        f"Below are {len(items)} comments, one per line, formatted as: [id] text\n\n"
        "For EACH comment return exactly one JSON object inside the top-level items array:\n"
        '  {"id": <id>, "score": <float from -1 (very negative) to 1 (very positive)>, '
        '"label": "pos"|"neg"|"neu", "aspects": [<0-3 short lowercase english aspect keywords, '
        'e.g. "autofocus", "price", "build quality">]}\n\n'
        "Rules:\n"
        '- Respond with ONE valid JSON object shaped as {"items": [/* entries */]}. '
        "No markdown, no preamble, no trailing text.\n"
        f"- Exactly {len(items)} objects; ids must match the input ids.\n"
        "- label must agree with score sign (score>0.15 pos, score<-0.15 neg, else neu).\n"
        "- Comments may be in any language; annotate meaning, not language.\n\n"
        "Comments:\n"
        f"{body}"
    )


def _valid_batch_payload(value: Any, expected_ids: set[int]) -> bool:
    """Strict provider contract: complete, unique, typed rows for this exact pack."""

    if not isinstance(value, dict) or set(value) != {"items"}:
        return False
    entries = value.get("items")
    if not isinstance(entries, list) or len(entries) != len(expected_ids):
        return False
    seen: set[int] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not {"id", "score", "label", "aspects"}.issubset(entry):
            return False
        try:
            comment_id = int(entry.get("id"))
        except (TypeError, ValueError):
            return False
        if comment_id not in expected_ids or comment_id in seen:
            return False
        seen.add(comment_id)
        score = entry.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not -1 <= float(score) <= 1:
            return False
        label = str(entry.get("label") or "").strip().lower()
        sentiment = LABEL_MAP.get(label)
        expected_sentiment = "positive" if float(score) > 0.15 else "negative" if float(score) < -0.15 else "neutral"
        if sentiment != expected_sentiment:
            return False
        aspects = entry.get("aspects")
        if (
            not isinstance(aspects, list)
            or len(aspects) > 3
            or any(not isinstance(item, str) or not item.strip() or len(item.strip()) > 80 for item in aspects)
        ):
            return False
    return seen == expected_ids


# 模型输出层失败:provider/预算/绑定都健康,只是这一次的输出不合 schema/解析不出。
# 该任务绑定钉死单一 provider/model(generate_json 传 model_fallbacks=()),声明中的
# 「后备胎」永远不会被尝试 —— 唯一有意义的补救是同绑定原地重试一次;
# 预算闸/无 key/绑定错配等结构性失败绝不重试(重试只会双倍烧钱,结果不变)。
_RETRYABLE_OUTPUT_FAILURES = {"validation_failure", "parse_failure", "empty_response"}


def _is_model_output_failure(response: Any) -> bool:
    result = response if isinstance(response, dict) else {}
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    statuses = {str(item.get("status") or "") for item in errors if isinstance(item, dict)}
    return bool(statuses & _RETRYABLE_OUTPUT_FAILURES)


def _failure_code(value: Any) -> str:
    result = value if isinstance(value, dict) else {}
    failure = result.get("failure") if isinstance(result.get("failure"), dict) else {}
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    latest = errors[-1] if errors and isinstance(errors[-1], dict) else {}
    return str(
        failure.get("code")
        or result.get("failure_code")
        or result.get("reason")
        or latest.get("status")
        or result.get("status")
        or "llm_unavailable"
    )[:120]


def parse_batch_response(text: str, expected_ids: set[int]) -> dict[int, dict[str, Any]]:
    """解析 LLM 返回的 JSON 数组 → {comment_id: {sentiment,score,label,aspects}}。

    容错:剥 markdown 围栏/前后杂讯(取首个 '[' 到末个 ']');陌生 id、非法 label、
    非 dict 条目一律丢弃(诚实跳过,绝不猜)。score 钳制 [-1, 1]。
    """
    s = str(text or "").strip()
    if not s:
        return {}
    start, end = s.find("["), s.rfind("]")
    if start < 0 or end <= start:
        return {}
    try:
        entries = json.loads(s[start : end + 1])
    except (json.JSONDecodeError, TypeError):
        # 截断救援:数组被 max_output_tokens 切断时,退到最后一个完整对象闭合处,
        # 抢救已完整的条目(仍是诚实语义:救不回的照旧 skipped,下轮重进队列)。
        brace = s.rfind("}")
        if brace <= start:
            return {}
        try:
            entries = json.loads(s[start : brace + 1] + "]")
        except (json.JSONDecodeError, TypeError):
            return {}
    if not isinstance(entries, list):
        return {}
    out: dict[int, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            cid = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        if cid not in expected_ids:
            continue
        label = str(entry.get("label") or "").strip().lower()
        sentiment = LABEL_MAP.get(label)
        if not sentiment:
            continue
        try:
            score = max(-1.0, min(1.0, float(entry.get("score"))))
        except (TypeError, ValueError):
            continue
        aspects_raw = entry.get("aspects")
        aspects = []
        if isinstance(aspects_raw, list):
            aspects = [str(a).strip()[:80] for a in aspects_raw if str(a).strip()][:5]
        out[cid] = {"sentiment": sentiment, "score": score, "label": label, "aspects": aspects}
    return out


def _persist_annotation(
    conn: Any,
    comment_id: int,
    entry: dict[str, Any],
    *,
    language_detected: str | None,
    llm_provider: str,
    llm_model: str,
    input_tokens: int,
    output_tokens: int,
    cost_cents: int,
) -> int | None:
    """写 vkpi_sentiment_results(ON CONFLICT 幂等)+ 回填 vkpi_comments.sentiment_id(只填空,不覆盖)。"""
    conn.execute(
        """
        INSERT INTO vkpi_sentiment_results (
          comment_id, sentiment, sentiment_confidence,
          llm_provider, llm_model, prompt_version, language_detected,
          input_tokens, output_tokens, cost_cents, analyzed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (comment_id, prompt_version) DO UPDATE SET
          sentiment = EXCLUDED.sentiment,
          sentiment_confidence = EXCLUDED.sentiment_confidence,
          llm_provider = EXCLUDED.llm_provider,
          llm_model = EXCLUDED.llm_model,
          analyzed_at = EXCLUDED.analyzed_at
        """,
        (
            int(comment_id),
            entry["sentiment"],
            round(abs(float(entry["score"])), 2),  # |score| = 极性强度代理(表 051 无 score 专列)
            llm_provider,
            llm_model,
            PROMPT_VERSION,
            (str(language_detected or "")[:10] or None),
            int(input_tokens or 0),
            int(output_tokens or 0),
            int(cost_cents or 0),
            _utcnow(),
        ),
    )
    row = conn.execute(
        "SELECT id FROM vkpi_sentiment_results WHERE comment_id=? AND prompt_version=?",
        (int(comment_id), PROMPT_VERSION),
    ).fetchone()
    if not row:
        return None
    result_id = int(dict(row)["id"])
    conn.execute(
        "UPDATE vkpi_comments SET sentiment_id=? WHERE id=? AND sentiment_id IS NULL",
        (result_id, int(comment_id)),
    )
    return result_id


def _chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), max(1, int(size)))]


def annotate_batch(
    batch_size: int = 50,
    *,
    dry_run: bool = True,
    conn: Any = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """情绪批注一轮:取 sentiment_id IS NULL 且 comment_text 非空的评论(created_at DESC),
    打包调 llm_gateway,写结果 + 回填 sentiment_id。

    dry_run=True(默认):只返回将处理条数、计划调用数与预估成本 —— 零 LLM、零落库。
    batch_size:本轮处理条数上限,再被 env 硬上限(默认 200)钳制;打包粒度由 pack_size() 决定。
    """
    conn = conn if conn is not None else get_conn()
    cap = run_hard_cap()
    take = max(1, min(int(batch_size or 50), cap))
    pack = pack_size()

    pending_total = _count_pending(conn)
    rows = _select_pending(conn, take)
    selected_ids = [int(r["id"]) for r in rows]
    reuse_map = _existing_result_ids(conn, selected_ids)
    llm_items = [r for r in rows if int(r["id"]) not in reuse_map]
    chunks = _chunked(llm_items, pack)
    prompts = [build_packed_prompt(c) for c in chunks]

    est_in = sum(_est_tokens(p) for p in prompts)
    est_out = len(llm_items) * OUTPUT_TOKENS_PER_COMMENT + len(chunks) * OUTPUT_TOKENS_SLACK
    try:
        provider_pref, model_pref = _sentiment_binding()
        binding_error = ""
    except ValueError as exc:
        provider_pref = str(os.environ.get(PROVIDER_ENV) or DEFAULT_PROVIDER).strip().lower()
        model_pref = str(os.environ.get(MODEL_ENV) or "").strip()
        binding_error = str(exc)
    summary: dict[str, Any] = {
        "mode": "dry_run" if dry_run else "live",
        "prompt_version": PROMPT_VERSION,
        "preferred_provider": provider_pref,
        "preferred_model": model_pref,
        "binding_error": binding_error,
        "pending_total": pending_total,
        "selected": len(rows),
        "reusable_from_cache": len(reuse_map),
        "llm_calls_planned": len(chunks),
        "pack_size": pack,
        "hard_cap": cap,
        "estimated_input_tokens": est_in,
        "estimated_output_tokens": est_out,
        "estimated_cost_usd": round(
            _estimate_cost_usd(est_in, est_out, provider_pref), 6
        ),
    }
    if dry_run:
        summary["note"] = "dry_run:未调用 LLM、未写库。--live / dry_run=False 才真跑。"
        return summary

    # ── 真跑 ────────────────────────────────────────────────────────────
    annotated = 0
    linked_from_cache = 0
    skipped_unparsed = 0
    halted_reason = ""
    providers_used: set[str] = set()
    aspect_counts: dict[str, int] = {}

    # 结果缓存回链:已有结果行的评论直接补 sentiment_id,零 LLM。
    for cid, result_id in reuse_map.items():
        conn.execute(
            "UPDATE vkpi_comments SET sentiment_id=? WHERE id=? AND sentiment_id IS NULL",
            (int(result_id), int(cid)),
        )
        linked_from_cache += 1

    for attempt_index, (chunk, prompt) in enumerate(zip(chunks, prompts), start=1):
        expected = {int(it["id"]) for it in chunk}
        max_out = min(4000, len(chunk) * OUTPUT_TOKENS_PER_COMMENT + OUTPUT_TOKENS_SLACK)
        response: dict[str, Any] = {}
        accepted = False
        for retry_index in range(2):  # 同绑定本地重试:仅模型输出层失败多给 1 次
            try:
                response = llm_production.generate_json(
                    prompt,
                    provider=provider_pref,
                    model=model_pref,
                    purpose=PURPOSE,
                    max_output_tokens=max_out,
                    triggered_by="scheduler:vkpi_sentiment_annotate",
                    staff=staff,
                    required_keys=("items",),
                    validator=lambda value, ids=expected: _valid_batch_payload(value, ids),
                    metadata={
                        "task_binding": TASK_BINDING,
                        "surface": "market_sentiment",
                        "pipeline": "sentiment_annotate_v0g",
                        "pack": len(chunk),
                        "phase": "analysis",
                        "subphase": "sentiment_annotation",
                        "attempt_index": attempt_index + retry_index,
                        "binding_retry": retry_index,
                        # 如实声明:绑定钉死单模型,不存在会被尝试的模型级后备胎。
                        "model_level_fallback": False,
                        "total": len(chunks),
                        "target_label": f"comments {min(expected)}-{max(expected)}",
                    },
                )
            except Exception as exc:  # strict AI-off/readiness failure; base pipeline remains usable
                response = {
                    "status": "failed",
                    "reason": str(exc)[:120] or type(exc).__name__,
                    "provider": "rule_v0",
                    "model": "rule_v0",
                    "json": None,
                }
            status = str(response.get("status") or "")
            accepted = (
                status == "success"
                and str(response.get("provider") or "").strip().lower() == provider_pref
                and str(response.get("model") or "").strip() == model_pref
                and _valid_batch_payload(response.get("json"), expected)
            )
            if accepted:
                break
            if retry_index == 0 and _is_model_output_failure(response):
                logger.info(
                    "sentiment_annotate.binding_retry",
                    extra={"reason": _failure_code(response), "attempt_index": attempt_index},
                )
                continue
            break
        if not accepted:
            # fallback_to_rule = 预算闸/无 key/全 provider 失败 → 停跑。绝不写假中性占坑
            # (占坑会填掉 sentiment_id,永久挡住真批注)。
            halted_reason = _failure_code(response)
            logger.warning(
                "sentiment_annotate.halted",
                extra={"reason": halted_reason, "annotated_so_far": annotated},
            )
            break
        payload = response.get("json") or {}
        parsed = parse_batch_response(json.dumps(payload.get("items") or [], ensure_ascii=False), expected)
        providers_used.add(str(response.get("provider") or "unknown"))
        n = max(1, len(parsed))
        in_share = int(response.get("input_tokens") or 0) // n
        out_share = int(response.get("output_tokens") or 0) // n
        cents_share = int(response.get("cost_micro_usd") or 0) // 10_000 // n
        lang_by_id = {int(it["id"]): it.get("language_detected") for it in chunk}
        for cid in sorted(expected):
            entry = parsed.get(cid)
            if not entry:
                skipped_unparsed += 1  # 本轮没解出来,sentiment_id 留空,下轮自动重进队列
                continue
            _persist_annotation(
                conn,
                cid,
                entry,
                language_detected=lang_by_id.get(cid),
                llm_provider=str(response.get("provider") or "unknown"),
                llm_model=str(response.get("model") or "unknown"),
                input_tokens=in_share,
                output_tokens=out_share,
                cost_cents=cents_share,
            )
            annotated += 1
            for a in entry.get("aspects") or []:
                aspect_counts[a] = aspect_counts.get(a, 0) + 1

    if hasattr(conn, "commit"):
        conn.commit()

    summary.update(
        {
            "annotated": annotated,
            "linked_from_cache": linked_from_cache,
            "skipped_unparsed": skipped_unparsed,
            "halted_reason": halted_reason,
            "providers_used": sorted(providers_used),
            "aspects_top": dict(sorted(aspect_counts.items(), key=lambda kv: -kv[1])[:20]),
        }
    )
    return summary


def full_backlog_estimate(conn: Any = None) -> dict[str, Any]:
    """全量成本测算(运营口径):按当前 pending 总量 × pack_size 推调用数与三家单价。"""
    conn = conn if conn is not None else get_conn()
    pending = _count_pending(conn)
    pack = pack_size()
    calls = math.ceil(pending / pack) if pending else 0
    est_in = calls * 180 + pending * 50   # 头部 ~180 tok/调用 + 评论 ~50 tok/条(含 id/格式)
    est_out = pending * OUTPUT_TOKENS_PER_COMMENT + calls * OUTPUT_TOKENS_SLACK
    return {
        "pending_total": pending,
        "pack_size": pack,
        "llm_calls": calls,
        "estimated_input_tokens": est_in,
        "estimated_output_tokens": est_out,
        "cost_usd_by_provider": {
            p: round(_estimate_cost_usd(est_in, est_out, p), 4) for p in ("google", "openai", "anthropic")
        },
    }

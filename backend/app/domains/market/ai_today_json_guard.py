"""ai_today_json_guard.py — LLM JSON 抽取加固(复用 2026-07-16 修复梯)+ 解析失败封顶重试。

从 ai_today._parse_json 抽出的 sibling(ai_today.py 顶着 999 行软棘轮,新逻辑落位这里),
只做两件事,均为 2026-08-24 审计修复(F7/F9):

1) ``extract_json_object``:围栏剥离 → 直接解析 → 顶层容器切片 → **既有语法修复梯**
   (services/ai/analyzers/gemini_video_results._syntax_repair_candidates,2026-07-16 波原件,
   字符串 token 保险丝原样沿用,绝不改写字符串字面量)→ 截断收口。
   截断收口针对真实故障形状「max_output_tokens 打断 → Unterminated string @char N」
   (2026-08-24 opus-5 evidence_strategy 实证):只做两种确定性动作——
   丢弃最后一个不完整元素、或原地闭合当前字符串,再按括号栈补右括号;
   构造上只截尾 + 追加结构闭合符,绝不改写既有内容、绝不发明字段。
   每条成功的修复路径都如实记日志;修不动如实返回 {}(调用方合同闸照旧 fail-closed)。

2) ``generate_json_with_parse_retry``:门面 generate_json 在网关内解析失败(parse_failure)时,
   坏 JSON 原文按红线已被网关丢弃、调用侧无从重修 → 在预算闸内重打一次
   (截断/漏逗号是随机性故障,第二发大概率成功)。总次数封顶(默认 2 发),
   其余失败(预算/绑定/校验)一律不重打;逐次如实记日志。
   预算注:每次重打就是一次正常计账的 provider 调用,网关预算闸逐发照常生效。

红线:纯解析/编排,零触 viltrox_fit_score / rule_v0;本模块不 import llm_production
(调用方传 callable 进来,LLM 边界仍只在既有 reviewed call sites)。
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from app.core.logging import get_logger
from app.platform.llm_gateway_json import _json_container_candidates
from app.services.ai.analyzers.gemini_video_results import (
    _string_tokens,
    _syntax_repair_candidates,
)

logger = get_logger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_CLOSERS = {"{": "}", "[": "]"}


def _truncation_close_candidates(text: str) -> list[tuple[str, str]]:
    """Bounded closers for output-token truncation (``Unterminated string``).

    单遍扫描定位:顶层容器起点、括号栈、是否停在字符串里、最后一个字符串外逗号
    (= 最后一个完整元素的边界)。产出两个候选,均为「原文前缀 + 结构闭合符」:
    - drop_tail:砍掉最后一个不完整元素再闭合(不保留被截断的半句话);
    - in_place:原地闭合当前字符串再闭合括号(保留半句,内容仍是模型原文前缀)。
    顶层容器已完整闭合 → 不属于截断形状,返回空(让原始错误冒出来)。
    """
    start: int | None = None
    stack: list[str] = []
    in_string = False
    escaped = False
    boundary: tuple[int, tuple[str, ...]] | None = None
    for index, char in enumerate(text):
        if start is None:
            if char in "[{":
                start = index
                stack = [char]
            continue
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append(char)
        elif char in "]}":
            if stack:
                stack.pop()
            if not stack:
                return []
        elif char == ",":
            boundary = (index, tuple(stack))
    if start is None or not stack:
        return []
    candidates: list[tuple[str, str]] = []
    if boundary is not None:
        cut, cut_stack = boundary
        candidates.append(
            (
                text[start:cut] + "".join(_CLOSERS[item] for item in reversed(cut_stack)),
                "truncation_close_drop_tail",
            )
        )
    candidates.append(
        (
            text[start:] + ('"' if in_string else "") + "".join(_CLOSERS[item] for item in reversed(stack)),
            "truncation_close_in_place",
        )
    )
    return candidates


def _loads_object(candidate: str) -> dict[str, Any] | None:
    parsed = json.loads(candidate)
    return parsed if isinstance(parsed, dict) else None


def extract_json_object(raw: str, *, surface: str = "ai_today") -> dict[str, Any]:
    """行为兼容 ai_today._parse_json:永不抛,失败返回 {};成功但非 dict 也返回 {}。"""
    text = _FENCE_RE.sub("", str(raw or "")).strip()
    if not text:
        return {}
    candidates = [text, *_json_container_candidates(text)]
    seen: set[str] = set()
    last_error: Exception | None = None
    for candidate in candidates:
        candidate = str(candidate or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = _loads_object(candidate)
            if parsed is not None:
                return parsed
            continue
        except (TypeError, ValueError) as exc:
            last_error = exc
        # 修复梯第 1 级(2026-07-16 原件复用):token 间语法(尾逗号/行尾漏逗号),
        # 字符串 token 序列保险丝——修复候选的字面量必须与原文逐一相同,否则弃用。
        original_strings = _string_tokens(candidate)
        for repaired in _syntax_repair_candidates(candidate):
            if _string_tokens(repaired) != original_strings:
                continue
            try:
                parsed = _loads_object(repaired)
            except (TypeError, ValueError) as exc:
                last_error = exc
                continue
            if parsed is not None:
                logger.info(
                    "%s.json_repaired", surface, extra={"repair_path": "syntax_repair"}
                )
                return parsed
        # 修复梯第 2 级:截断收口(Unterminated string 形状)。
        for repaired, path in _truncation_close_candidates(candidate):
            try:
                parsed = _loads_object(repaired)
            except (TypeError, ValueError) as exc:
                last_error = exc
                continue
            if parsed is not None:
                logger.info("%s.json_repaired", surface, extra={"repair_path": path})
                return parsed
    logger.warning(
        "%s.json_unrepairable", surface,
        extra={"error": f"{type(last_error).__name__}: {str(last_error)[:200]}" if last_error else "no_json_object"},
    )
    return {}


def _parse_failure_statuses(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return []
    statuses: list[str] = []
    for item in result.get("errors") or []:
        if isinstance(item, dict) and str(item.get("status") or ""):
            statuses.append(str(item.get("status")))
    return statuses


def generate_json_with_parse_retry(
    call: Callable[[], dict[str, Any]],
    *,
    surface: str,
    max_attempts: int = 2,
) -> tuple[dict[str, Any], int]:
    """调用门面 generate_json 的 callable;仅 parse_failure 时封顶重打。

    返回 (最终结果, 实际发数)。成功 / 预算被拦 / 校验失败 / 绑定失败 → 原样立刻返回,
    不多花一分钱;只有网关明确报 parse_failure(花了钱、输出被丢)才值得再打一发。
    """
    attempts = max(1, int(max_attempts))
    result: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        raw_result = call()
        result = raw_result if isinstance(raw_result, dict) else {}
        if str(result.get("status") or "") == "success":
            if attempt > 1:
                logger.info(
                    "%s.parse_retry_recovered", surface, extra={"attempt_total": attempt}
                )
            return result, attempt
        statuses = _parse_failure_statuses(result)
        if "parse_failure" not in statuses or attempt >= attempts:
            return result, attempt
        logger.warning(
            "%s.parse_failure_retrying", surface,
            extra={"attempt": attempt, "max_attempts": attempts, "attempt_statuses": statuses[:6]},
        )
    return result, attempts

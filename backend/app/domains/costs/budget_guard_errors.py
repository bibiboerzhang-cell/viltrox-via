"""成本台账失败的可见性辅助(leaf,零 DB、零反向 import)。

2026-08-22 复盘:ForeignKeyViolation 被包成一句 ``forced_ai_cost_ledger_write_failed``,
根因(staff_id=1 不存在)只在子进程 stderr。这里提供两件小工具:

- ``summarize_exception``:``异常类名: 首行 | 第二行``(脱敏、截断)。psycopg 的 DETAIL 行
  (``Key (staff_id)=(1) is not present in table "staff"``)在第二行,故保留前两行非空文本。
- ``note_cost_ledger_failure``:一行 warning + ``exc.add_note``,不改变抛出的异常类型。
"""
from __future__ import annotations

import re
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# URL userinfo / Bearer / key=value 形密钥全打码(与 worker 侧 _redact_sensitive_text 同口径)。
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b([a-z][a-z0-9+.-]*://)([^/\s@]+@)", re.IGNORECASE), r"\1***@"),
    (re.compile(r"\bbearer\s+[A-Za-z0-9._~+/\-=]+", re.IGNORECASE), "Bearer ***"),
    (
        re.compile(
            r"\b(authorization|proxy|token|api[_-]?key|key|secret|password|passwd|"
            r"access[_-]?token|refresh[_-]?token|client[_-]?secret)\b\s*([:=])\s*(?:bearer\s+)?"
            r"([^,\s'\"}\]]+)",
            re.IGNORECASE,
        ),
        r"\1\2***",
    ),
)


def redact_secrets(text: Any, *, limit: int = 240) -> str:
    """脱敏 + 截断;纯字符串处理,不抛。"""
    value = str(text or "")
    for pattern, repl in _SECRET_PATTERNS:
        value = pattern.sub(repl, value)
    return value[: max(16, int(limit))]


def summarize_exception(exc: BaseException, *, limit: int = 240) -> str:
    """``异常类名: 首行 | 第二行``(脱敏、截断);空信息只回类名。"""
    name = type(exc).__name__
    lines = [line.strip() for line in str(exc or "").splitlines() if line.strip()]
    text = redact_secrets(" | ".join(lines[:2]), limit=limit)
    return f"{name}: {text}" if text else name


def note_cost_ledger_failure(
    exc: BaseException,
    *,
    scope: Any = "",
    staff_id: Any = None,
    unresolved_staff_id: Any = None,
) -> str:
    """记一行 warning 并给异常挂 note;返回摘要。绝不抛。"""
    summary = summarize_exception(exc)
    logger.warning(
        "vkpi.budget_guard.cost_ledger_write_failed | scope=%s staff_id=%s unresolved_staff_id=%s | %s",
        scope,
        staff_id,
        unresolved_staff_id,
        summary,
    )
    try:
        exc.add_note(f"cost_ledger_write_failed: {summary}")
    except Exception as note_exc:  # noqa: BLE001 - note 只是可见性,绝不盖住原异常
        logger.debug("cost_ledger_error_note_skipped: %s", type(note_exc).__name__)
    return summary


__all__ = ["note_cost_ledger_failure", "redact_secrets", "summarize_exception"]

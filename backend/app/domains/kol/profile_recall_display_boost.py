"""库内召回的展示层二段增强(2026-07-02 用户令)——从 ``profile_recall`` 原样抽出。

两段都**只动 ``display_rank_score``**(与「新人优先」同款纯展示信号):

  ① 采纳回流上浮:历史被采纳过的同类候选加一点展示权重;
  ② LLM 头部 rerank:只对头部重排,失败静默降级、诊断字符串留痕。

搬家契约:逻辑逐字节等价于原内联块。所有会被测试 monkeypatch 的钩子
(``_adoption_profile`` / ``_llm_rerank_buckets`` / ``_adoption_boost_for``)
都**由调用方按名传入**,因此 ``monkeypatch.setattr(profile_recall, "_adoption_profile", ...)``
照旧生效——调用方在调用点读自己的模块全局,搬家没有把钩子焊死。

红线:零触 ``viltrox_fit_score``,零触 rule_v0,不写库;异常永远吞成
``stage_skipped:<reason>`` 而不是冒泡 500。
"""
from __future__ import annotations

import os
from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

RERANK_ENABLED_ENV = "RECALL_LLM_RERANK_ENABLED"
_DISABLED_VALUES = {"0", "false", "no"}


def rerank_enabled() -> bool:
    return os.environ.get(RERANK_ENABLED_ENV, "1").strip().lower() not in _DISABLED_VALUES


def apply_display_boost_and_rerank(
    buckets: dict[str, list[dict[str, Any]]],
    *,
    provider_free: bool,
    resolved_text: str,
    persona_text: str,
    product_label: str,
    adoption_profile: Callable[[], Any],
    adoption_boost_for: Callable[[dict[str, Any], Any], Any],
    llm_rerank_buckets: Callable[..., str],
    ranking_key: Callable[[dict[str, Any]], Any],
    to_float: Callable[[Any], float],
) -> str:
    """就地重排 ``buckets``,返回诊断用的 ``display_rerank`` 字符串。"""

    note = ""
    try:
        adoption = adoption_profile()
        boosted = 0
        if adoption:
            for bucket_items in buckets.values():
                for item in bucket_items:
                    bonus = adoption_boost_for(item, adoption)
                    if bonus:
                        item["display_rank_score"] = round(
                            to_float(item.get("display_rank_score")) + bonus, 6
                        )
                        item["adoption_boost"] = bonus
                        boosted += 1
        if provider_free:
            note = "provider_free_initial"
        elif rerank_enabled():
            note = llm_rerank_buckets(buckets, resolved_text, persona_text, product_label)
        if boosted or note.startswith("ok"):
            for bucket_items in buckets.values():
                bucket_items.sort(key=ranking_key, reverse=True)
        return (note or "off") + f" boost:{boosted}"
    except Exception as exc:  # noqa: BLE001 — 展示增强失败绝不许让整次搜索 500
        failure_text = f"{type(exc).__name__} {exc}".lower()
        reason = (
            "rerank_timeout"
            if "timeout" in failure_text or "deadline" in failure_text
            else "rerank_unavailable"
        )
        logger.warning("profile_recall rerank skipped reason=%s", reason, exc_info=True)
        return f"stage_skipped:{reason}"


__all__ = ["RERANK_ENABLED_ENV", "apply_display_boost_and_rerank", "rerank_enabled"]

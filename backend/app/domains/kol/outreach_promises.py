"""外联三承诺(three_promises_v1)—— 打行业三大怨气的书面承诺文案,中英双语模板常量。

行业三大怨气(创作者侧真实痛点)→ 三承诺:
  ① 87% 创作者被拖过款      → 7 天内付款(内容验收后 7 个自然日付清);
  ② 借测逼还文化            → 送测留用不回收(器材寄出即归创作者);
  ③ 差评被删改/黑名单文化   → 书面承诺不干预差评(负面意见照发,不撤稿不施压)。

用法(唯一真源,别处不要复制这段文案):
  - promises_enabled()            开关(env VKPI_OUTREACH_THREE_PROMISES,默认开;
                                    不新增迁移、不碰 .env —— 缺省即启用);
  - promises_block_en()/_zh()     完整承诺文案块(嵌入邮件正文用);
  - ensure_promises(text, lang)   幂等注入:文本里已有承诺块(哨兵句)则原样返回,
                                    否则在正文末尾追加 —— LLM 漏写也兜得住;
  - prompt_requirement()          给 LLM prompt 的硬要求段(要求逐字嵌入两个块);
  - promises_payload()            读端 payload(路由/前端展示用)。

红线:纯文案常量 + 字符串操作,零 SQL、零 LLM、零写库;草稿仍只供人审后手动外发。
"""
from __future__ import annotations

import os
from typing import Any

PROMISES_VERSION = "three_promises_v1"
ENV_FLAG = "VKPI_OUTREACH_THREE_PROMISES"

# 三承诺条目(key 稳定,供前端/审计对齐;文案改动只动这里)。
PROMISE_ITEMS: tuple[dict[str, str], ...] = (
    {
        "key": "pay_within_7_days",
        "zh": "7 天内付款 —— 内容验收后 7 个自然日内付清全部合作款项,绝不拖欠。",
        "en": "Payment within 7 days - full payment within 7 calendar days of content acceptance, no chasing required.",
        "pain_point_zh": "行业 87% 创作者被拖过款",
    },
    {
        "key": "keep_the_gear",
        "zh": "送测留用不回收 —— 测评器材寄出即归您所有,绝不借测逼还。",
        "en": "The gear is yours to keep - review units are never recalled once shipped.",
        "pain_point_zh": "借测逼还文化",
    },
    {
        "key": "no_review_interference",
        "zh": "不干预差评 —— 书面承诺绝不要求删改、软化或撤下负面评价,真实观点优先。",
        "en": "Critical reviews stay up - we commit in writing to never ask you to soften, edit or remove honest criticism.",
        "pain_point_zh": "差评被删改/黑名单文化",
    },
)

# 哨兵句(幂等检测用;LLM 被要求逐字嵌入,含哨兵即视为已注入)。
_MARKER_EN = "Our three commitments to every creator"
_MARKER_ZH = "我们对每位创作者的三项书面承诺"


def promises_enabled() -> bool:
    """开关:env VKPI_OUTREACH_THREE_PROMISES,缺省开;0/false/off/no 关。"""
    raw = str(os.getenv(ENV_FLAG, "1")).strip().lower()
    return raw not in ("0", "false", "off", "no", "n")


def promises_block_en() -> str:
    lines = [_MARKER_EN + ":"]
    for i, item in enumerate(PROMISE_ITEMS, start=1):
        lines.append(f"{i}) {item['en']}")
    return "\n".join(lines)


def promises_block_zh() -> str:
    nums = ("①", "②", "③")
    lines = [_MARKER_ZH + ":"]
    for i, item in enumerate(PROMISE_ITEMS):
        lines.append(f"{nums[i] if i < len(nums) else str(i + 1) + ')'} {item['zh']}")
    return "\n".join(lines)


def has_promises(text: str, lang: str) -> bool:
    """文本是否已含承诺块(按哨兵句判定,大小写不敏感)。"""
    marker = _MARKER_ZH if lang == "zh" else _MARKER_EN
    return marker.lower() in str(text or "").lower()


def ensure_promises(text: str, lang: str) -> str:
    """幂等注入承诺块:开关关/正文为空/已含哨兵 → 原样返回;否则正文末尾追加。

    只对非空正文追加(不无中生有造邮件);追加在末尾 —— 身份披露/退订段之后
    也可接受,人审后可自行挪位置,兜底优先于排版。
    """
    body = str(text or "")
    if not promises_enabled() or not body.strip() or has_promises(body, lang):
        return body
    block = promises_block_zh() if lang == "zh" else promises_block_en()
    return body.rstrip() + "\n\n" + block


def prompt_requirement() -> str:
    """给 LLM prompt 的硬要求段:要求把两个承诺块逐字嵌入对应语言正文。"""
    if not promises_enabled():
        return ""
    return (
        "三承诺硬要求(不可省、不可改写):这是我们打行业痛点(拖款/借测逼还/干预差评)的\n"
        "书面承诺,必须逐字嵌入邮件正文(建议放在合作说明之后、署名之前):\n"
        "- 英文正文逐字嵌入以下块:\n" + promises_block_en() + "\n"
        "- 中文正文逐字嵌入以下块:\n" + promises_block_zh()
    )


def promises_payload() -> dict[str, Any]:
    """读端 payload(路由/前端展示;含开关态与双语块)。"""
    return {
        "version": PROMISES_VERSION,
        "enabled": promises_enabled(),
        "env_flag": ENV_FLAG,
        "items": [dict(item) for item in PROMISE_ITEMS],
        "block_en": promises_block_en(),
        "block_zh": promises_block_zh(),
        "note": "模板常量唯一真源;开关缺省开(env 置 0 可关);草稿仅供人审后手动外发。",
    }

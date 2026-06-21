"""R4 · 合作话术 + SOW 草案生成(LLM · 预算闸 · 仅草案绝不外发)。

generate_outreach(kol_pool_ids, brief=..., product=..., staff=...) → 给选中 KOL 各出一封
个性化合作邀约话术 + 一份 SOW(合作范围)草案,供运营人审后手动外发。

红线(强约束):
- 走 llm_gateway.invoke(内置预算闸 + 代理);预算关/被拦 → 回退 rule_v0 时 honestly 标
  llm_used=False 并给确定性模板,绝不把 planner fallback 文本当话术。
- 只「生成草案」:不发邮件、不写业务表、不承诺价格(SOW 报酬留 placeholder)、零触 viltrox_fit_score。
- N 封有上限(控成本),超出诚实截断并记录。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.platform import llm_gateway

logger = get_logger(__name__)

_MAX_CREATORS = 8  # 单次话术生成上限(控 token 成本);超出截断并记录。


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_json(text: str) -> dict[str, Any]:
    """从 LLM 文本里抠出第一个 JSON 对象(容忍 ```json 围栏/前后噪声)。失败回 {}。"""
    raw = _text(text)
    if not raw:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    candidate = fenced.group(1) if fenced else raw
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(candidate[start : end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _load_creators(kol_pool_ids: list[int]) -> tuple[list[dict[str, Any]], list[int]]:
    if not kol_pool_ids:
        return [], []
    placeholders = ",".join("?" for _ in kol_pool_ids)
    rows = get_conn().execute(
        f"""
        SELECT id, platform, handle, display_name, primary_topic, followers, email
        FROM vkpi_kol_pool
        WHERE id IN ({placeholders})
        """,
        kol_pool_ids,
    ).fetchall()
    found = {int(dict(r)["id"]): dict(r) for r in rows}
    creators = [found[kid] for kid in kol_pool_ids if kid in found]
    missing = [kid for kid in kol_pool_ids if kid not in found]
    return creators, missing


def _creator_label(c: dict[str, Any]) -> str:
    return _text(c.get("display_name")) or _text(c.get("handle")) or f"KOL #{c.get('id')}"


def _template_message(c: dict[str, Any], positioning: str, persona: str) -> dict[str, Any]:
    """LLM 不可用时的确定性模板(诚实占位,人工再润色)。"""
    name = _creator_label(c)
    pos = positioning or "Viltrox 专业影像装备"
    body = (
        f"Hi {name},\n\n"
        f"We're Viltrox. We loved your work on {_text(c.get('platform')) or 'your channel'} "
        f"and think it's a strong fit for our {pos}. "
        f"We'd love to explore a collaboration and send a unit for an honest review.\n\n"
        f"If you're open to it, reply and we'll share details and next steps.\n\n"
        f"Best,\nViltrox Creator Partnerships"
    )
    return {
        "kol_pool_id": _int_or_none(c.get("id")),
        "handle": _text(c.get("handle")),
        "display_name": name,
        "platform": _text(c.get("platform")),
        "subject": f"Viltrox × {name} — collaboration invite",
        "body": body,
        "personalized": False,
    }


def _template_sow(positioning: str, persona: str, creator_count: int) -> dict[str, Any]:
    return {
        "scope": f"Sponsored content collaboration for {positioning or 'Viltrox product'}.",
        "target_creators": persona,
        "deliverables": [
            "1× dedicated review/feature video",
            "1× social post with product tag",
            "Usage rights for Viltrox owned channels (3 months)",
        ],
        "timeline": "Sample ship → 2 weeks to publish after delivery",
        "compensation": "TODO — 待人工按预算与谈判确定(本草案不承诺价格)",
        "creator_count": creator_count,
        "is_draft": True,
    }


def generate_outreach(
    kol_pool_ids: list[Any],
    *,
    brief: dict[str, Any] | None = None,
    product: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
    preferred_provider: str | None = None,
) -> dict[str, Any]:
    """生成 N 封合作话术 + SOW 草案。预算闸内置于 gateway;rule_v0 回退则给确定性模板。"""
    brief = brief or {}
    product = product or {}
    positioning = _text(brief.get("product_positioning")) or _text(product.get("product_name"))
    persona = _text(brief.get("target_persona")) or _text(brief.get("query_text"))

    ids: list[int] = []
    seen: set[int] = set()
    for value in kol_pool_ids or []:
        kid = _int_or_none(value)
        if kid and kid not in seen:
            seen.add(kid)
            ids.append(kid)

    truncated = len(ids) > _MAX_CREATORS
    capped_ids = ids[:_MAX_CREATORS]
    creators, missing = _load_creators(capped_ids)
    if not creators:
        return {
            "ok": False,
            "reason": "no_resolvable_creators",
            "llm_used": False,
            "messages": [],
            "sow_draft": {},
            "missing_kol_pool_ids": missing,
            "truncated": truncated,
        }

    creator_lines = "\n".join(
        f"- id={c.get('id')} | {_creator_label(c)} | platform={_text(c.get('platform')) or 'unknown'} "
        f"| topic={_text(c.get('primary_topic')) or 'n/a'} | followers={c.get('followers') if c.get('followers') is not None else 'n/a'}"
        for c in creators
    )
    prompt = f"""
You are Viltrox's creator-partnerships outreach writer.
Write OUTPUT AS ONE JSON OBJECT ONLY — no markdown fences, no prose before/after.

Product positioning: {positioning or 'Viltrox professional imaging gear'}
Ideal creator/persona: {persona or 'professional content creators and filmmakers'}

Write a short, warm, specific outreach message for EACH creator below (2-4 sentences, English,
reference their platform/topic, invite a paid/seeded collaboration, no fabricated stats, no price promise),
plus ONE shared SOW (statement of work) draft.

Creators:
{creator_lines}

Return JSON with keys:
  messages: array, one per creator, each: {{ "kol_pool_id": number, "subject": string, "body": string }}
  sow_draft: {{ "scope": string, "deliverables": string[], "timeline": string, "usage_rights": string, "compensation": string }}
For compensation, output a neutral placeholder like "to be discussed" — never commit a number.
"""

    response = llm_gateway.invoke(
        prompt,
        purpose="kol_outreach_draft",
        max_output_tokens=min(4000, 600 + 240 * len(creators)),
        preferred_provider=preferred_provider,
        cost_tag="kol_outreach_draft",
        metadata={
            "creator_count": len(creators),
            "positioning": positioning[:120],
            "search_session_id": brief.get("search_session_id"),
        },
        staff=staff,
    )
    provider = _text(response.get("provider"))
    llm_used = provider not in ("", "rule_v0")
    parsed = _extract_json(_text(response.get("text"))) if llm_used else {}

    by_id = {c["id"]: c for c in creators}
    messages: list[dict[str, Any]] = []
    if llm_used and isinstance(parsed.get("messages"), list):
        for raw in parsed["messages"]:
            if not isinstance(raw, dict):
                continue
            kid = _int_or_none(raw.get("kol_pool_id"))
            c = by_id.get(kid) if kid else None
            subject = _text(raw.get("subject"))
            bodytext = _text(raw.get("body"))
            if not bodytext:
                continue
            messages.append({
                "kol_pool_id": kid,
                "handle": _text((c or {}).get("handle")),
                "display_name": _creator_label(c) if c else "",
                "platform": _text((c or {}).get("platform")),
                "subject": subject or f"Viltrox collaboration invite",
                "body": bodytext,
                "personalized": True,
            })
    # LLM 缺漏的创作者(或整体 fallback)→ 用确定性模板补齐,保证每个 KOL 都有话术。
    covered = {m["kol_pool_id"] for m in messages if m.get("kol_pool_id")}
    for c in creators:
        if c["id"] not in covered:
            messages.append(_template_message(c, positioning, persona))

    sow_draft: dict[str, Any] = {}
    if llm_used and isinstance(parsed.get("sow_draft"), dict):
        s = parsed["sow_draft"]
        sow_draft = {
            "scope": _text(s.get("scope")),
            "deliverables": [str(d) for d in (s.get("deliverables") or []) if str(d).strip()],
            "timeline": _text(s.get("timeline")),
            "usage_rights": _text(s.get("usage_rights")),
            # 价格永不由 LLM 落定:无论模型给什么,报酬一律 placeholder(红线4)。
            "compensation": "TODO — 待人工按预算与谈判确定(本草案不承诺价格)",
            "is_draft": True,
        }
    if not sow_draft.get("scope"):
        sow_draft = _template_sow(positioning, persona, len(creators))

    return {
        "ok": True,
        "llm_used": llm_used,
        "provider": provider,
        "model": _text(response.get("model")),
        "reason": "" if llm_used else _text(response.get("reason")) or "llm_unavailable_used_template",
        "messages": messages,
        "sow_draft": sow_draft,
        "creator_count": len(creators),
        "missing_kol_pool_ids": missing,
        "truncated": truncated,
        "max_creators": _MAX_CREATORS,
        "note": "草案仅供人审后手动外发;不自动发送、不承诺价格、零触 viltrox_fit_score。",
    }

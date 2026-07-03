"""C4 · 一键外联包(brief + 中英双语邮件草稿 + 邮箱补抓状态)。

从 KOL 详情「合作」面板一键生成外联包:
- ① 产品契合 brief:纯复用已有产物(content_fit_v1 缓存 / viltrox_fit_reason /
  recommended_product_lines_json / potential_concerns_json),不新跑 LLM 就有数据的直接用。
- ② 个性化邮件草稿:llm_gateway.invoke(内置预算闸 + provider 兜底链,Claude 优先),
  引用该 KOL 的真实内容亮点;LLM 不可用 → 确定性双语模板(诚实标 personalized=False)。
- ③ 邮箱缺失 → 复用既有邮箱富化管线 business_contact_extract.enrich_business_contacts
  (flag/预算/白名单来源全在其内建),不新建任何抓取。
- 产物落 vkpi_analysis_cache(target_type='kol_pool', derive_method='kol_outreach_pack_v1'),
  同 KOL 当日幂等(force=True 才重生成),GET 可重读。

红线(强约束):
- 零写 viltrox_fit_score / viltrox_fit_reason;本模块对主表只读(邮箱写入由既有富化管线完成)。
- 不动 rule_v0 评分、不碰 KOL 归属判定。
- 只「生成草案」:不自动外发、不承诺价格;草稿末尾强制身份披露 + 退订说明(全球最严合规基线)。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.platform import llm_gateway

logger = get_logger("viltrox.domains.kol.outreach_pack")

TARGET_TYPE = "kol_pool"
DERIVE_METHOD = "kol_outreach_pack_v1"
# 预算键沿用 cron: 前缀模式(与 official_daily_report / llm_gateway._cost_scope_for_purpose 同款)。
COST_TAG = "cron:kol_outreach_pack"
MAX_OUTPUT_TOKENS = 1400


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _text(value: Any, limit: int = 0) -> str:
    out = str(value or "").strip()
    return out[:limit] if limit and len(out) > limit else out


def _loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _str_list(value: Any, *, limit: int = 8, each: int = 400) -> list[str]:
    """把 json 列表(元素可能是 str 或 {name/label/...} dict)拍平成字符串列表。"""
    parsed = _loads(value)
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    for item in parsed:
        if isinstance(item, dict):
            text = _text(item.get("name") or item.get("label") or item.get("text") or item.get("reason"), each)
        else:
            text = _text(item, each)
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _parse_llm_json(text: str) -> dict[str, Any]:
    """从 LLM 文本抠第一个 JSON 对象(容忍 ```json 围栏/前后噪声);失败回 {}。"""
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
        logger.debug("outreach_pack.llm_json_malformed", exc_info=True)
        return {}


# ── KOL 上下文(只读) ─────────────────────────────────────────────


def _kol_row(conn: Any, kol_pool_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, platform, handle, display_name, bio, followers, engagement_rate,
               primary_topic, content_style, email, viltrox_fit_reason,
               recommended_product_lines_json, potential_concerns_json, brand_collaborations_json
        FROM vkpi_kol_pool WHERE id=?
        """,
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row) if row else {}


def _kol_label(kol: dict[str, Any]) -> str:
    return _text(kol.get("display_name")) or _text(kol.get("handle")) or f"KOL #{kol.get('id')}"


def _content_fit_snapshot(kol_pool_id: int) -> dict[str, Any]:
    """读已有 content_fit_v1 缓存(不触发新分析、不烧 LLM);无则 {}。"""
    try:
        from app.domains.kol import content_fit_analysis

        cached = content_fit_analysis.get_content_fit(int(kol_pool_id))
        if isinstance(cached, dict) and cached.get("state") == "ready":
            result = cached.get("result")
            return result if isinstance(result, dict) else {}
    except Exception:
        logger.debug("outreach_pack.content_fit_read_failed", exc_info=True)
    return {}


# ── ① 产品契合 brief(纯复用已有产物,零 LLM) ───────────────────────


def _build_brief(kol: dict[str, Any], content_fit: dict[str, Any]) -> dict[str, Any]:
    product_lines = _str_list(kol.get("recommended_product_lines_json"), limit=4, each=120)
    why_fit: list[str] = []
    # 主依据:内容契合深析的逐条证据理由(基于真实视频/评论,最有说服力)。
    for reason in content_fit.get("fit_reasons") or []:
        text = _text(reason, 400)
        if text:
            why_fit.append(text)
        if len(why_fit) >= 6:
            break
    # 辅依据:规则版 fit 理由(只读展示,绝不回写)。
    fit_reason = _text(kol.get("viltrox_fit_reason"), 500)
    if fit_reason and fit_reason not in why_fit:
        why_fit.append(fit_reason)
    return {
        "product_focus": product_lines[0] if product_lines else "VILTROX 镜头",
        "recommended_product_lines": product_lines,
        "creator_type": _text(content_fit.get("creator_type"), 300),
        "content_highlights": _text(content_fit.get("content_summary"), 1200),
        "audience_signal": _text(content_fit.get("audience_signal"), 1200),
        "fit_verdict": _text(content_fit.get("fit_verdict"), 40),
        "why_fit": why_fit[:8],
        "watchouts": _str_list(kol.get("potential_concerns_json"), limit=5, each=300),
        "past_brand_collabs": _str_list(kol.get("brand_collaborations_json"), limit=5, each=200),
        "sources": {
            "content_fit_v1": bool(content_fit),
            "viltrox_fit_reason": bool(fit_reason),
        },
    }


# ── ② 邮件草稿(LLM 润色,失败回落确定性双语模板) ──────────────────


def _build_email_prompt(kol: dict[str, Any], brief: dict[str, Any]) -> str:
    name = _kol_label(kol)
    highlight_lines = []
    if brief.get("content_highlights"):
        highlight_lines.append(f"- 内容综述: {brief['content_highlights'][:600]}")
    for reason in (brief.get("why_fit") or [])[:4]:
        highlight_lines.append(f"- 契合点: {reason}")
    if brief.get("audience_signal"):
        highlight_lines.append(f"- 受众信号: {brief['audience_signal'][:300]}")
    kol_lines = [
        f"- 平台/Platform: {_text(kol.get('platform')) or '-'}",
        f"- Handle: {_text(kol.get('handle')) or '-'}",
        f"- 名称/Name: {name}",
        f"- 粉丝/Followers: {kol.get('followers') if kol.get('followers') is not None else '-'}",
        f"- 主题/Topic: {_text(kol.get('primary_topic')) or '-'}",
        f"- 简介/Bio: {_text(kol.get('bio'), 400) or '-'}",
    ]
    return (
        "你是 VILTROX(唯卓仕,相机镜头品牌)的 KOL 合作拓展专家。"
        "基于以下 KOL 资料与「真实内容亮点」写一封首次外联邮件(中英双语)。\n\n"
        "KOL 资料:\n" + "\n".join(kol_lines) + "\n\n"
        "该 KOL 的真实内容亮点(来自我方已完成的视频/评论分析,只能引用、不得杜撰新事实):\n"
        + ("\n".join(highlight_lines) if highlight_lines else "(暂无深析亮点,基于资料合理措辞,不编造具体作品)")
        + f"\n\n拟推产品方向: {brief.get('product_focus') or 'VILTROX 镜头'}\n\n"
        "合规硬要求(不可省):email_en 正文末尾必须含 (a) 发件人身份披露——明确写出是 VILTROX/唯卓仕"
        "品牌方及其拓展代表;(b) 退订选项——一句说明若不愿再收到此类邮件可回复 unsubscribe 即不再联系。"
        "此为全球最严合规基线,任何辖区都适用。\n"
        "要求:\n"
        "1. email_en:英文邮件正文(130-190 词,开头具体提到 TA 内容亮点里的某一点,说明合作形式=寄送镜头测评,"
        "语气专业友好、不卑不亢,不承诺价格;末尾含上述身份披露+退订选项)。\n"
        "2. email_zh:同一内容的中文版(供团队内部审阅与中文创作者直接使用,同样含身份披露+退订说明)。\n"
        "3. subject:英文邮件主题(<=60 字符,含创作者名或其内容方向)。\n"
        "4. talking_points:3-4 条后续沟通要点(中文,基于上面的真实亮点)。\n"
        '只返回 JSON:{"subject":...,"email_en":...,"email_zh":...,"talking_points":[...]}'
    )


def _template_email_draft(kol: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    """LLM 不可用时的确定性双语模板(引用已有真实字段,诚实标 personalized=False)。"""
    name = _kol_label(kol)
    platform = _text(kol.get("platform")) or "your channel"
    topic = _text(kol.get("primary_topic")) or _text(brief.get("creator_type"), 120)
    product = _text(brief.get("product_focus")) or "VILTROX lenses"
    topic_en = f" around {topic}" if topic else ""
    email_en = (
        f"Hi {name},\n\n"
        f"I'm reaching out from VILTROX, the camera lens brand. We've been following your work on {platform}"
        f"{topic_en} and believe it aligns well with {product}. "
        "We'd love to send you a lens for an honest, no-strings review and explore a paid collaboration if it feels right.\n\n"
        "If you're open to it, just reply and we'll share details and next steps.\n\n"
        "Best regards,\nVILTROX Creator Partnerships (official brand outreach)\n"
        "If you'd prefer not to receive collaboration emails from us, reply \"unsubscribe\" and we won't contact you again."
    )
    email_zh = (
        f"{name} 您好,\n\n"
        f"我们是相机镜头品牌 VILTROX(唯卓仕)。我们关注到您在 {platform} 的内容"
        + (f"(方向:{topic})" if topic else "")
        + f",认为与 {product} 方向契合。"
        "我们希望寄送镜头供您真实测评,若合适也欢迎进一步探讨付费合作。\n\n"
        "如您有兴趣,回复本邮件即可,我们会提供详细方案与后续安排。\n\n"
        "此致\nVILTROX(唯卓仕)创作者合作团队(品牌方官方外联)\n"
        "若您不希望再收到此类合作邮件,回复 unsubscribe 即不再打扰。"
    )
    return {
        "subject": f"VILTROX x {name} - collaboration invite"[:120],
        "email_en": email_en,
        "email_zh": email_zh,
        "talking_points": [],
        "personalized": False,
    }


def _generate_email_draft(kol: dict[str, Any], brief: dict[str, Any], *, staff: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """LLM 生成双语邮件草稿;失败/预算挡 → 模板兜底。返回 (draft, provenance片段)。

    llm_gateway.invoke 内置:预算闸(monthly/single_call/provider/cost_tag 四层)+
    provider 兜底链(preferred_provider 排第一,其余按序回退)+ 台账记录。
    Claude 优先做措辞(参照 official_daily_report 的「Claude 优先、其余兜底」模式)。
    """
    resp = llm_gateway.invoke(
        _build_email_prompt(kol, brief),
        purpose="kol_outreach_pack",
        max_output_tokens=MAX_OUTPUT_TOKENS,
        preferred_provider="anthropic",
        cost_tag=COST_TAG,
        staff=staff or {},
        metadata={"kol_pool_id": kol.get("id")},
    )
    provider = _text(resp.get("provider"))
    llm_used = bool(resp.get("status") == "success" and provider not in ("", "rule_v0"))
    parsed = _parse_llm_json(_text(resp.get("text"))) if llm_used else {}
    email_en = _text(parsed.get("email_en"), 2600)
    email_zh = _text(parsed.get("email_zh"), 2600)
    if llm_used and email_en:
        draft = {
            "subject": _text(parsed.get("subject"), 120) or f"VILTROX x {_kol_label(kol)} - collaboration invite"[:120],
            "email_en": email_en,
            "email_zh": email_zh,
            "talking_points": [_text(x, 300) for x in (parsed.get("talking_points") or []) if _text(x)][:5],
            "personalized": True,
        }
    else:
        draft = _template_email_draft(kol, brief)
    provenance = {
        "provider": provider,
        "model": _text(resp.get("model")),
        "llm_used": llm_used and bool(email_en),
        "reason": "" if (llm_used and email_en) else (_text(resp.get("reason")) or _text(resp.get("status")) or "llm_unavailable_used_template"),
        "cost_cents": resp.get("cost_cents") or 0,
    }
    return draft, provenance


# ── ③ 邮箱状态 + 缺失时复用既有富化管线 ─────────────────────────────


def _email_status(conn: Any, kol_pool_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT email FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    email = _text(dict(row).get("email")) if row else ""
    return {"state": "present" if email else "missing", "email": email}


def _try_enrich_email(kol_pool_id: int, *, staff: dict[str, Any] | None) -> dict[str, Any]:
    """邮箱缺失时复用既有富化管线(flag/预算/白名单/合规留痕全在其内建);失败不阻断外联包。"""
    try:
        from app.domains.kol.business_contact_extract import enrich_business_contacts

        result = enrich_business_contacts(int(kol_pool_id), staff=staff)
        return {
            "attempted": True,
            "status": _text((result or {}).get("status")) or "unknown",
            "reason": _text((result or {}).get("reason"), 300),
        }
    except LookupError:
        return {"attempted": True, "status": "kol_not_found", "reason": ""}
    except Exception as exc:
        logger.warning("outreach_pack.email_enrich_failed (non-fatal) | kol_pool_id=%s", kol_pool_id, exc_info=True)
        return {"attempted": True, "status": "error", "reason": str(exc)[:300]}


# ── 缓存读写(vkpi_analysis_cache,同 outreach_draft 模式) ──────────


def _read_pack_cache(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT result, model, updated_at FROM vkpi_analysis_cache
        WHERE target_type=? AND target_id=? AND derive_method=? AND status='ready'
        ORDER BY updated_at DESC, id DESC LIMIT 1
        """,
        (TARGET_TYPE, str(int(kol_pool_id)), DERIVE_METHOD),
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    result = _loads(data.get("result"))
    if not isinstance(result, dict):
        return None
    return {"pack": result, "model": data.get("model"), "updated_at": str(data.get("updated_at") or "")}


def _write_pack_cache(conn: Any, kol_pool_id: int, pack: dict[str, Any], *, model: str, cost_usd: float, staff: dict[str, Any] | None) -> None:
    now = _utcnow()
    conn.execute(
        """
        INSERT INTO vkpi_analysis_cache (
            target_type, target_id, model, derive_method, result, cost, status,
            triggered_by_user_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?::jsonb, ?, 'ready', ?, ?, ?)
        ON CONFLICT (target_type, target_id, derive_method)
        DO UPDATE SET model=EXCLUDED.model, result=EXCLUDED.result, cost=EXCLUDED.cost,
            status='ready', triggered_by_user_id=EXCLUDED.triggered_by_user_id, updated_at=EXCLUDED.updated_at
        """,
        (
            TARGET_TYPE,
            str(int(kol_pool_id)),
            model or "rule_template",
            DERIVE_METHOD,
            json.dumps(pack, ensure_ascii=False),
            float(cost_usd or 0.0),
            int((staff or {}).get("user_id") or 0) or None,
            now,
            now,
        ),
    )
    conn.commit()


# ── 对外入口 ─────────────────────────────────────────────────────


def get_outreach_pack(kol_pool_id: int) -> dict[str, Any]:
    """读端:最新外联包(cache)+ 实时邮箱状态;无则 state=missing。零 LLM/零外调。"""
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone():
        raise LookupError("kol pool item not found")
    email = _email_status(conn, int(kol_pool_id))
    cached = _read_pack_cache(conn, int(kol_pool_id))
    if not cached:
        return {"state": "missing", "kol_pool_id": int(kol_pool_id), "email": email}
    return {
        "state": "ready",
        "kol_pool_id": int(kol_pool_id),
        "pack": cached["pack"],
        "model": cached.get("model"),
        "updated_at": cached.get("updated_at"),
        "email": email,
        "cached": True,
    }


def generate_outreach_pack(kol_pool_id: int, *, force: bool = False, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """一键生成外联包:brief(复用已有产物)+ 双语邮件草稿(LLM/模板)+ 邮箱补抓。

    同 KOL 当日幂等:今天已生成过且非 force → 直接回缓存,不重复烧 LLM。
    红线:主表只读;绝不写 viltrox_fit_score / 不动 rule_v0 / 不碰归属判定。
    """
    kid = int(kol_pool_id)
    conn = get_conn()
    kol = _kol_row(conn, kid)
    if not kol:
        raise LookupError("kol pool item not found")

    # 当日幂等:缓存里 generated_date 是今天且非 force → 直接复用(邮箱状态仍实时读)。
    if not force:
        cached = _read_pack_cache(conn, kid)
        if cached:
            generated_date = _text(((cached["pack"] or {}).get("provenance") or {}).get("generated_date"))
            if generated_date == _today():
                return {
                    "state": "ready",
                    "kol_pool_id": kid,
                    "pack": cached["pack"],
                    "model": cached.get("model"),
                    "updated_at": cached.get("updated_at"),
                    "email": _email_status(conn, kid),
                    "cached": True,
                }

    # ③ 邮箱缺失 → 先走既有富化管线(可能从已抓 raw 零成本提到,草稿即刻可用)。
    enrich: dict[str, Any] = {"attempted": False, "status": "", "reason": ""}
    if not _text(kol.get("email")):
        enrich = _try_enrich_email(kid, staff=staff)

    # ① brief:纯复用已有 why-fit / 内容契合深析产物,零 LLM。
    content_fit = _content_fit_snapshot(kid)
    brief = _build_brief(kol, content_fit)

    # ② 邮件草稿:LLM 润色(预算闸+兜底链内置);失败回落确定性双语模板。
    draft, llm_provenance = _generate_email_draft(kol, brief, staff=staff)

    pack = {
        "schema_version": DERIVE_METHOD,
        "kol": {
            "kol_pool_id": kid,
            "handle": _text(kol.get("handle")),
            "display_name": _kol_label(kol),
            "platform": _text(kol.get("platform")),
        },
        "brief": brief,
        "email_draft": draft,
        "provenance": {
            **llm_provenance,
            "generated_at": _utcnow(),
            "generated_date": _today(),
        },
        "note": "草案仅供人审后手动外发;不自动发送、不承诺价格;零触 viltrox_fit_score。",
    }
    _write_pack_cache(
        conn,
        kid,
        pack,
        model=_text(llm_provenance.get("model")) or "rule_template",
        cost_usd=float(llm_provenance.get("cost_cents") or 0) / 100.0,
        staff=staff,
    )
    return {
        "state": "ready",
        "kol_pool_id": kid,
        "pack": pack,
        "model": _text(llm_provenance.get("model")) or "rule_template",
        "updated_at": _utcnow(),
        # 富化可能刚写回 email,这里重读给前端最新状态。
        "email": {**_email_status(conn, kid), "enrich": enrich},
        "cached": False,
    }

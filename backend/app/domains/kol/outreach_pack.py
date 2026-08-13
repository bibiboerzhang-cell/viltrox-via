"""C4 · 一键外联包(brief + 中英双语邮件草稿 + 邮箱补抓状态)。

从 KOL 详情「合作」面板一键生成外联包:
- ① 产品契合 brief:纯复用已有产物(content_fit_v1 缓存 / viltrox_fit_reason /
  recommended_product_lines_json / potential_concerns_json),不新跑 LLM 就有数据的直接用。
- ② 个性化邮件草稿:llm_production 精确模型边界(运行就绪+原子预留),
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
from app.core.model_registry import current_task_model_binding, split_binding
from app.db.connection import get_conn
from app.domains.kol import outreach_promises
from app.platform import llm_production

logger = get_logger("viltrox.domains.kol.outreach_pack")

TARGET_TYPE = "kol_pool"
DERIVE_METHOD = "kol_outreach_pack_v1"
# 预算键沿用 cron: 前缀模式。
COST_TAG = "cron:kol_outreach_pack"
MAX_OUTPUT_TOKENS = 1400
MODEL_TASK = "kol_outreach_pack"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _text(value: Any, limit: int = 0) -> str:
    out = str(value or "").strip()
    return out[:limit] if limit and len(out) > limit else out


def _stable_llm_reason(response: dict[str, Any]) -> str:
    failure = response.get("failure") if isinstance(response.get("failure"), dict) else {}
    errors = response.get("errors") if isinstance(response.get("errors"), list) else []
    latest = errors[-1] if errors and isinstance(errors[-1], dict) else {}
    reason = _text(
        failure.get("code")
        or response.get("failure_code")
        or response.get("reason")
        or latest.get("status")
    ).lower()
    if "timeout" in reason or "deadline" in reason:
        return "llm_timeout_used_template"
    if reason in {"all_providers_failed", "budget_exceeded", "not_configured", "provider_unavailable"}:
        return reason
    return "llm_unavailable_used_template"


def _model_binding() -> tuple[str, str]:
    return split_binding(current_task_model_binding().get(MODEL_TASK) or "")


def _valid_email_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ("subject", "email_en", "email_zh"):
        if not isinstance(value.get(key), str) or not str(value.get(key) or "").strip():
            return False
    talking_points = value.get("talking_points")
    return (
        isinstance(talking_points, list)
        and len(talking_points) <= 8
        and all(isinstance(item, str) and item.strip() for item in talking_points)
    )


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


# ── G1 真个性化上下文:招牌拍法 top1 + 最爆代表作(signature_profile 守卫 import)──


def _personalization_context(kid: int, conn: Any) -> dict[str, Any]:
    """该 KOL 的招牌拍法 TOP1 + 最爆代表作标题(纯复用 signature_profile 聚合读数,
    零新采集/零 LLM);signature 读不出来诚实 available=False,外联包不因此阻断。"""
    out: dict[str, Any] = {"available": False, "source": "signature_profile_v1"}
    try:
        from app.domains.kol import signature_profile

        sig = signature_profile.signature_profile(int(kid), conn=conn)
    except LookupError:
        return out
    except Exception:
        logger.debug("outreach_pack.signature_read_failed (non-fatal)", exc_info=True)
        return out
    styles = sig.get("shooting_styles") if isinstance(sig, dict) else {}
    styles = styles if isinstance(styles, dict) else {}
    modes = styles.get("modes") if styles.get("status") == "ready" else []
    top_mode = modes[0] if isinstance(modes, list) and modes else {}
    tops = sig.get("top_videos") if isinstance(sig, dict) else {}
    tops = tops if isinstance(tops, dict) else {}
    items = tops.get("items") if tops.get("status") == "ready" else []
    top_video = items[0] if isinstance(items, list) and items else {}
    if not top_mode and not top_video:
        return out

    style_label = _text((top_mode or {}).get("label"), 40)
    video_title = _text((top_video or {}).get("title"), 140)
    quote_line = ""
    if video_title and style_label:
        quote_line = f"就按你最爆的那条《{video_title}》的{style_label}拍法来"
    elif video_title:
        quote_line = f"就按你最爆的那条《{video_title}》的拍法来"
    elif style_label:
        quote_line = f"就按你最擅长的{style_label}拍法来"
    return {
        "available": True,
        "source": "signature_profile_v1",
        "top_style": {
            "label": style_label,
            "video_count": (top_mode or {}).get("video_count"),
            "avg_views": (top_mode or {}).get("avg_views"),
        }
        if top_mode
        else None,
        "top_video": {
            "title": video_title,
            "view_count": (top_video or {}).get("view_count"),
            "content_url": _text((top_video or {}).get("content_url"), 500),
        }
        if top_video
        else None,
        "quote_line": quote_line,
    }


def _critic_context(kid: int, conn: Any) -> dict[str, Any]:
    """「敢给差评」信号轻量摘要(critic_signal 守卫 import;词表法零 LLM);
    读不出来诚实空态,不阻断外联包。"""
    try:
        from app.domains.kol import critic_signal as critic_signal_domain

        sig = critic_signal_domain.critic_signal(int(kid), conn=conn)
    except Exception:
        logger.debug("outreach_pack.critic_signal_read_failed (non-fatal)", exc_info=True)
        return {"has_critic_history": False, "basis": "critic_signal 读取失败(非阻塞)。"}
    example = sig.get("example") if isinstance(sig, dict) else None
    example = example if isinstance(example, dict) else None
    return {
        "has_critic_history": bool(sig.get("has_critic_history")),
        "basis": _text(sig.get("basis"), 300),
        "example": {
            "quote": _text(example.get("quote"), 220),
            "title": _text(example.get("title"), 140),
            "source": _text(example.get("source"), 40),
        }
        if example
        else None,
        "method": _text(sig.get("method"), 40),
    }


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


def _build_email_prompt(
    kol: dict[str, Any],
    brief: dict[str, Any],
    personalization: dict[str, Any] | None = None,
    critic: dict[str, Any] | None = None,
) -> str:
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

    # G1 真个性化:招牌拍法 top1 + 最爆代表作(signature_profile 真聚合读数,只准引用)。
    persona = personalization if isinstance(personalization, dict) else {}
    persona_lines: list[str] = []
    if persona.get("available"):
        style = persona.get("top_style") or {}
        video = persona.get("top_video") or {}
        if style.get("label"):
            extra = f"(深析样本 {style.get('video_count')} 条)" if style.get("video_count") else ""
            persona_lines.append(f"- 招牌拍法 TOP1: {style['label']}{extra}")
        if video.get("title"):
            views = f"(播放 {video.get('view_count')})" if video.get("view_count") else ""
            persona_lines.append(f"- 最爆代表作: 《{video['title']}》{views}")
        if persona.get("quote_line"):
            persona_lines.append(
                "- 邮件必须自然引用上面的代表作标题,并明确表达「内容就按 TA 最擅长/最爆的拍法来,"
                f"我们不干预创意」(中文版可用「{persona['quote_line']}」的语气,英文版意译)。"
            )
    persona_block = (
        "该 KOL 的招牌打法(来自我方真实聚合读数,个性化必用、只准引用不得杜撰):\n" + "\n".join(persona_lines) + "\n\n"
        if persona_lines
        else ""
    )

    # G1 「敢给差评」可信度信号:有证据才提,措辞=欣赏其真实,呼应承诺③,不翻旧账。
    critic_ctx = critic if isinstance(critic, dict) else {}
    critic_block = ""
    if critic_ctx.get("has_critic_history"):
        example = critic_ctx.get("example") or {}
        critic_block = (
            "可信度信号(词表法证据,只作语气参考、不逐字引用):该 KOL 历史上敢公开指出产品缺点"
            + (f"(如《{example.get('title')}》相关文本:{example.get('quote')})" if example.get("quote") else "")
            + "。邮件里可诚恳点明:我们正是欣赏这种真实测评,欢迎包括批评在内的真实观点"
            "(呼应下方承诺③),但绝不能写成翻旧账或阴阳怪气。\n\n"
        )

    promises_block = outreach_promises.prompt_requirement()
    promises_section = promises_block + "\n" if promises_block else ""

    return (
        "你是 VILTROX(唯卓仕,相机镜头品牌)的 KOL 合作拓展专家。"
        "基于以下 KOL 资料与「真实内容亮点」写一封首次外联邮件(中英双语)。\n\n"
        "KOL 资料:\n" + "\n".join(kol_lines) + "\n\n"
        "该 KOL 的真实内容亮点(来自我方已完成的视频/评论分析,只能引用、不得杜撰新事实):\n"
        + ("\n".join(highlight_lines) if highlight_lines else "(暂无深析亮点,基于资料合理措辞,不编造具体作品)")
        + f"\n\n拟推产品方向: {brief.get('product_focus') or 'VILTROX 镜头'}\n\n"
        + persona_block
        + critic_block
        + promises_section
        + "合规硬要求(不可省):email_en 正文末尾必须含 (a) 发件人身份披露——明确写出是 VILTROX/唯卓仕"
        "品牌方及其拓展代表;(b) 退订选项——一句说明若不愿再收到此类邮件可回复 unsubscribe 即不再联系。"
        "此为全球最严合规基线,任何辖区都适用。\n"
        "要求:\n"
        "1. email_en:英文邮件正文(150-230 词,开头具体提到 TA 内容亮点/最爆代表作里的某一点,"
        "说明合作形式=寄送镜头测评,语气专业友好、不卑不亢,不承诺价格;含三承诺块;末尾含上述身份披露+退订选项)。\n"
        "2. email_zh:同一内容的中文版(供团队内部审阅与中文创作者直接使用,同样含三承诺块+身份披露+退订说明)。\n"
        "3. subject:英文邮件主题(<=60 字符,含创作者名或其内容方向)。\n"
        "4. talking_points:3-4 条后续沟通要点(中文,基于上面的真实亮点)。\n"
        '只返回 JSON:{"subject":...,"email_en":...,"email_zh":...,"talking_points":[...]}'
    )


def _template_email_draft(
    kol: dict[str, Any],
    brief: dict[str, Any],
    personalization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """LLM 不可用时的确定性双语模板(引用已有真实字段,诚实标 personalized=False)。

    G1:有招牌读数时模板也带真个性化引用(最爆代表作标题+招牌拍法),三承诺块
    由 ensure_promises 统一注入(开关关则不带)。
    """
    name = _kol_label(kol)
    platform = _text(kol.get("platform")) or "your channel"
    topic = _text(kol.get("primary_topic")) or _text(brief.get("creator_type"), 120)
    product = _text(brief.get("product_focus")) or "VILTROX lenses"
    topic_en = f" around {topic}" if topic else ""

    persona = personalization if isinstance(personalization, dict) else {}
    video_title = _text(((persona.get("top_video") or {}) if persona.get("available") else {}).get("title"), 140)
    style_label = _text(((persona.get("top_style") or {}) if persona.get("available") else {}).get("label"), 40)
    persona_en = ""
    persona_zh = ""
    if video_title:
        persona_en = (
            f' We especially loved "{video_title}" - if we work together, the content stays 100 percent '
            "in your own style, exactly the way that video was shot."
        )
        persona_zh = f"我们尤其喜欢您的《{video_title}》——若合作,内容完全按您自己的风格来," + (
            f"{persona.get('quote_line')}。" if persona.get("quote_line") else "就按那条的拍法来。"
        )
    elif style_label:
        persona_en = f" Your signature {style_label} style is exactly what we hope the content keeps."
        persona_zh = f"您招牌的「{style_label}」正是我们希望内容保持的样子。"

    email_en = (
        f"Hi {name},\n\n"
        f"I'm reaching out from VILTROX, the camera lens brand. We've been following your work on {platform}"
        f"{topic_en} and believe it aligns well with {product}."
        + persona_en
        + " We'd love to send you a lens for an honest, no-strings review and explore a paid collaboration if it feels right.\n\n"
        "If you're open to it, just reply and we'll share details and next steps.\n\n"
        "Best regards,\nVILTROX Creator Partnerships (official brand outreach)\n"
        "If you'd prefer not to receive collaboration emails from us, reply \"unsubscribe\" and we won't contact you again."
    )
    email_zh = (
        f"{name} 您好,\n\n"
        f"我们是相机镜头品牌 VILTROX(唯卓仕)。我们关注到您在 {platform} 的内容"
        + (f"(方向:{topic})" if topic else "")
        + f",认为与 {product} 方向契合。"
        + persona_zh
        + "我们希望寄送镜头供您真实测评,若合适也欢迎进一步探讨付费合作。\n\n"
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


def _generate_email_draft(
    kol: dict[str, Any],
    brief: dict[str, Any],
    *,
    staff: dict[str, Any] | None,
    personalization: dict[str, Any] | None = None,
    critic: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """LLM 生成双语邮件草稿;失败/预算挡 → 模板兜底。返回 (draft, provenance片段)。

    llm_production 锁定经审核的 provider/model,且每次调用必须通过运行就绪与原子预留。
    单次调用不跨 provider/model 静默兜底;未就绪时直接使用诚实确定性模板。
    G1:prompt 注入招牌拍法/最爆代表作 + 敢给差评信号 + 三承诺硬要求;
    无论 LLM 还是模板产出,三承诺块最后都过 ensure_promises 幂等兜底(开关关则跳过)。
    """
    provider, model = _model_binding()
    try:
        resp = llm_production.generate_json(
            _build_email_prompt(kol, brief, personalization, critic),
            provider=provider,
            model=model,
            purpose="kol_outreach_pack",
            max_output_tokens=MAX_OUTPUT_TOKENS,
            cost_tag=COST_TAG,
            triggered_by=(staff or {}).get("user_id") or MODEL_TASK,
            staff=staff or {},
            required_keys=("subject", "email_en", "email_zh", "talking_points"),
            validator=_valid_email_payload,
            metadata={
                "task_binding": MODEL_TASK,
                "kol_pool_id": kol.get("id"),
                "phase": "kol_outreach",
                "subphase": "bilingual_draft",
                "attempt_index": 1,
                "total": 1,
                "target_label": _kol_label(kol),
            },
        )
    except Exception:  # noqa: BLE001 - template fallback is the product contract
        logger.warning("outreach_pack.llm_failed; using deterministic template", exc_info=True)
        resp = {"status": "unavailable", "provider": "rule_v0", "model": "", "reason": "provider_exception"}
    if not isinstance(resp, dict):
        resp = {"status": "unavailable", "provider": "rule_v0", "model": "", "reason": "invalid_gateway_response"}
    response_provider = _text(resp.get("provider")).lower()
    response_model = _text(resp.get("model"))
    candidate = resp.get("json") if isinstance(resp, dict) else None
    exact_response = (
        resp.get("status") == "success"
        and response_provider == provider
        and response_model == model
    )
    llm_used = bool(exact_response and _valid_email_payload(candidate))
    parsed = candidate if llm_used and isinstance(candidate, dict) else {}
    email_en = _text(parsed.get("email_en"), 3400)
    email_zh = _text(parsed.get("email_zh"), 3400)
    if llm_used and email_en:
        draft = {
            "subject": _text(parsed.get("subject"), 120) or f"VILTROX x {_kol_label(kol)} - collaboration invite"[:120],
            "email_en": email_en,
            "email_zh": email_zh,
            "talking_points": [_text(x, 300) for x in (parsed.get("talking_points") or []) if _text(x)][:5],
            "personalized": True,
        }
    else:
        draft = _template_email_draft(kol, brief, personalization)
    # 三承诺幂等兜底:LLM 漏写/被截断也保证承诺块在(_text 压平了换行,块以单行形态在场)。
    draft["email_en"] = outreach_promises.ensure_promises(str(draft.get("email_en") or ""), "en")
    draft["email_zh"] = outreach_promises.ensure_promises(str(draft.get("email_zh") or ""), "zh")
    draft["promises_included"] = outreach_promises.has_promises(str(draft.get("email_en") or ""), "en") or outreach_promises.has_promises(str(draft.get("email_zh") or ""), "zh")
    provenance = {
        "provider": provider if (llm_used and email_en) else "rule_v0",
        "model": model if (llm_used and email_en) else "rule_template",
        "requested_binding": f"{provider}/{model}",
        "llm_used": llm_used and bool(email_en),
        "reason": "" if (llm_used and email_en) else (
            "exact_model_or_json_contract_mismatch"
            if str(resp.get("status") or "") == "success"
            else _stable_llm_reason(resp)
        ),
        "cost_cents": resp.get("cost_cents") or 0,
    }
    return draft, provenance


# ── ③ 邮箱状态 + 缺失时复用既有富化管线 ─────────────────────────────


def _email_status(conn: Any, kol_pool_id: int, *, reveal: bool = False) -> dict[str, Any]:
    from app.domains.kol.contact_access import project_email_status

    row = conn.execute("SELECT email FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    email = _text(dict(row).get("email")) if row else ""
    return project_email_status(
        {"state": "present" if email else "missing", "email": email},
        reveal=reveal,
    )


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
        return {"attempted": True, "status": "error", "reason": "email_enrichment_failed"}


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


def get_outreach_pack(
    kol_pool_id: int,
    *,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """读端:最新外联包(cache)+ 实时邮箱状态;无则 state=missing。零 LLM/零外调。"""
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone():
        raise LookupError("kol pool item not found")
    del staff
    # Legacy outreach responses remain masked.  Plaintext is available only
    # through the fenced, rate-limited single-item projection/reveal boundary.
    email = _email_status(conn, int(kol_pool_id), reveal=False)
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
                    "email": _email_status(conn, kid, reveal=False),
                    "cached": True,
                }

    # ③ 邮箱缺失 → 先走既有富化管线(可能从已抓 raw 零成本提到,草稿即刻可用)。
    enrich: dict[str, Any] = {"attempted": False, "status": "", "reason": ""}
    if not _text(kol.get("email")):
        enrich = _try_enrich_email(kid, staff=staff)

    # ① brief:纯复用已有 why-fit / 内容契合深析产物,零 LLM。
    content_fit = _content_fit_snapshot(kid)
    brief = _build_brief(kol, content_fit)

    # G1 真个性化上下文:招牌拍法/最爆代表作 + 敢给差评信号(守卫 import,零 LLM)。
    personalization = _personalization_context(kid, conn)
    critic = _critic_context(kid, conn)

    # ② 邮件草稿:LLM 润色(预算闸+兜底链内置);失败回落确定性双语模板。
    draft, llm_provenance = _generate_email_draft(
        kol, brief, staff=staff, personalization=personalization, critic=critic
    )

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
        # G1:个性化引用来源 + 可信度信号 + 三承诺态(前端展示/审计留痕)。
        "personalization": personalization,
        "critic_signal": critic,
        "promises": {
            "version": outreach_promises.PROMISES_VERSION,
            "enabled": outreach_promises.promises_enabled(),
            "included": bool(draft.get("promises_included")),
        },
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
        "email": {**_email_status(conn, kid, reveal=False), "enrich": enrich},
        "cached": False,
    }

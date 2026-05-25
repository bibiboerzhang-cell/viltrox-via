"""Via intent classification and deterministic guidance replies."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.repositories.via_control import (
    get_latest_via_outcome_record,
    get_via_reward_trace_by_idempotency,
    insert_via_decision_record,
    insert_via_outcome_record,
    insert_via_retrieval_evidence,
    insert_via_reward_trace,
    list_via_decision_records,
    list_via_memory_retention_stats,
    list_via_reward_traces,
    upsert_via_memory_retention_stat,
    upsert_via_routing_provider_stat,
    update_via_outcome_record,
)
from app.core.config import VIA_BASE_MODEL
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.db.repositories.via import (
    add_via_memory_ref,
    create_via_session,
    find_via_session,
    get_or_create_via_persona,
    get_via_session_bundle,
    touch_via_session,
    update_via_persona,
)
from app.services.memory.l3_store import record_creator_memory_fact, record_feedback_signal
from app.services.via.activity_pack import resolve_via_activity_state
from app.services.via.business_brain import build_business_context, get_via_business_reply
from app.services.via.decision_ledger import (
    build_context_refs,
    build_decision_candidates,
    estimate_model_cost,
    summarize_control_loop,
)
from app.services.via.external_viltrox_assets import (
    get_external_system_prompt_injection,
    sanitize_external_via_output,
)
from app.services.via.memory_promoter import persist_via_memory_promotions, propose_via_memory_promotions
from app.services.via.model_router import generate_json_with_collab, generate_json_with_route, get_via_model_plan, preview_via_routes
from app.services.via.policy_registry import get_via_policy, get_via_shadow_policy
from app.services.via.product_brain import build_product_context, get_via_product_reply
from app.services.via.reward_aggregator import (
    aggregate_via_reply_outcome,
    merge_via_reward_trace_summary,
    summarize_via_reward_traces,
)
from app.services.via.shadow_learning import (
    evaluate_shadow_memory_promotion,
    evaluate_shadow_model_choice,
    evaluate_shadow_retrieval_plan,
)
from app.services.via.trigger_engine import build_via_trigger_snapshot
from app.services.via.learning_signals import (
    compact_via_profile_context,
    extract_via_learning_signals,
    merge_via_persona_profile,
)
from app.services.via.knowledge_seed import (
    build_via_seed_documents,
    extract_workspace_docx_product_line_catalog,
    extract_workspace_docx_software_catalog,
)
from app.services.via.vector_memory import (
    recall_via_vector_memory,
    store_via_seed_documents,
    store_via_vector_exchange,
    sync_bundle_memory_refs_to_vector,
)

logger = get_logger(__name__)


from app.services.via.session_memory import _memory_teaser

_SENSITIVE_PATTERNS = [
    r"\b(api\s*key|token|secret|password|passwd|credential|auth\s*header)\b",
    r"\b(database|postgres|redis|schema|table|sql|query|migration|prompt|system prompt|admin)\b",
    r"(数据库|表结构|密钥|口令|密码|提示词|系统提示词|后台|管理后台|内部接口|内部文档|sql|redis|postgres)",
    r"(其他用户|别人(的)?数据|客户列表|订单列表|邮箱列表|地址库|原始日志)",
    r"(未发布产品|保密产品|内部 roadmap|内部路线图|供应链细节|库存明细|原型图|源代码|仓库地址|成本|毛利|利润)",
]

_IDENTITY_PATTERNS = [
    r"\b(what ai are you|what model are you|which model are you|who are you)\b",
    r"(你是什么ai|你是啥ai|你是什么模型|你是什么机器人|你是谁)",
]

_JAILBREAK_PATTERNS = [
    r"\b(ignore previous|system prompt|developer message|reveal prompt|jailbreak|bypass|override policy)\b",
    r"(忽略之前|忽略上面|系统提示词|开发者消息|越狱|绕过|无视规则|后门)",
]


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


_MEMORY_PATTERNS = [
    r"\b(remember|memory|what do you know about me|what did i say|last time|earlier)\b",
    r"(记得|记忆|你知道我什么|我之前说过|上次|刚才说的)",
]

_IMAGE_VIDEO_PATTERNS = [
    r"\b(video|image|frame|shot|scene|edit|grading|grading|broll|b-roll|timeline|composition|storyboard|script|monitor|focal length|aperture|shutter|iso|bokeh|anamorphic|full frame|aps-c|crop sensor)\b",
    r"(视频|画面|镜头|构图|剪辑|分镜|脚本|调色|监视器|拍摄|素材|上传分析|焦段|光圈|快门|感光度|虚化|变形宽银幕|全画幅|半画幅|卡口)",
]

_SOFTWARE_PATTERNS = [
    r"\b(nexus focus|nexusfocus|viltrox lens|viltroxlink|weeylite|weeylight|firmware|download center|app|software)\b",
    r"(软件|app|应用|下载|固件|升级|viltrox lens|viltroxlink|nexus focus|weeylite|weeylight)",
]

_PHOTOGRAPHY_BASICS_PATTERNS = [
    r"\b(35mm|50mm|85mm|24mm|focal length|prime lens|portrait lens|street photo|street photography)\b",
    r"\b(shutter|aperture|iso|exposure triangle|depth of field|bokeh|white balance|frame rate|fps)\b",
    r"(焦段|35mm|50mm|85mm|人像|扫街|快门|光圈|感光度|曝光三角|景深|虚化|白平衡|帧率|夜景|短片)",
]

_CASUAL_CHAT_PATTERNS = [
    r"\b(can you chat|small talk|casual chat|hang out|talk with me|chat with me)\b",
    r"\b(city night|night scene|night street|short film|shot list|story beat)\b",
    r"(日常聊天|陪我聊|和我聊聊|你能聊天吗|夜景短片|城市夜景|分镜|镜头安排|怎么拍更像电影)",
]

_DEEP_REASONING_PATTERNS = [
    r"\b(compare|comparison|versus|vs\.?|why|strategy|plan|roadmap|analyze|analysis|break down|best way)\b",
    r"(对比|比较|为什么|策略|方案|路线图|深度分析|拆解|怎么做最好|创作建议)",
]

_FOLLOW_UP_MEMORY_PATTERNS = [
    r"\b(this one|that one|which one|what about it|what about that|same one)\b",
    r"(这个呢|那个呢|哪一个|那支呢|继续说|接着讲|上一个)",
]

_PRODUCT_LINE_PATTERNS = [
    r"\bluna\b",
    r"\bepic\b",
    r"\blab\b",
    r"\bpro\b",
    r"\bevo\b",
    r"\bdf\b",
    r"(?<![a-z])c(\s+series|系列)(?![a-z])",
    r"\bair\b",
    r"\bchip\b",
    r"(luna|epic|lab|pro|evo|df|air|chip)\s*(系列|line|series)",
]

_TRANSACTIONAL_PRODUCT_PATTERNS = [
    r"\b(buy|purchase|shop|price|pricing|budget|recommend|link|spec|specs|mount)\b",
    r"(购买|入手|多少钱|预算|推荐|链接|参数|卡口|价格)",
]


def _matches_any(text: str, patterns: list[str]) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns)


def _reply_lang(bundle: dict[str, Any], text: str) -> str:
    if _contains_cjk(text):
        return "zh"
    persona = bundle.get("persona") or {}
    profile = persona.get("profile") or {}
    preferred = str(profile.get("preferred_language") or "").strip().lower()
    return "zh" if preferred.startswith("zh") else "en"


def _software_context_lines(user_text: str) -> list[str]:
    lowered = str(user_text or "").lower()
    catalog = extract_workspace_docx_software_catalog()
    if not catalog:
        return []
    target_keys: list[str] = []
    if "nexus" in lowered:
        target_keys.append("nexus_focus")
    if "viltrox lens" in lowered or ("lens" in lowered and "viltrox" in lowered):
        target_keys.append("viltrox_lens")
    if "viltroxlink" in lowered:
        target_keys.append("viltroxlink")
    if "weey" in lowered:
        target_keys.append("weeylightpro")
    if not target_keys:
        target_keys = ["viltrox_lens", "nexus_focus", "viltroxlink", "weeylightpro"]
    lines: list[str] = []
    for key in target_keys:
        item = catalog.get(key)
        if not item:
            continue
        notes = [str(note).strip() for note in (item.get("notes") or []) if str(note).strip()]
        links = [str(link).strip() for link in (item.get("links") or []) if str(link).strip()]
        lines.append(
            f"{item.get('name')}: notes={'; '.join(notes[:3]) or 'n/a'} | links={', '.join(links[:2]) or 'n/a'}"
        )
    return lines[:6]


def _product_line_context_lines(user_text: str) -> list[str]:
    lowered = str(user_text or "").lower()
    catalog = extract_workspace_docx_product_line_catalog()
    if not catalog:
        return []
    series_order = ["LUNA", "EPIC", "LAB", "PRO", "EVO", "DF", "C", "AIR", "CHIP"]
    targeted: list[str] = []
    for key in series_order:
        token = key.lower()
        if key == "C":
            if re.search(r"(?<![a-z])c(\s+series|系列)(?![a-z])", lowered):
                targeted.append(key)
            continue
        if token in lowered:
            targeted.append(key)
    if not targeted:
        return []
    lines: list[str] = []
    for key in targeted[:3]:
        item = catalog.get(key)
        if not item:
            continue
        summary = str(item.get("summary") or "").strip()
        models = [str(model).strip() for model in (item.get("models") or []) if str(model).strip()]
        notes = [str(note).strip() for note in (item.get("notes") or []) if str(note).strip()]
        lines.append(
            f"{item.get('name') or key}: {summary or 'n/a'} | models={', '.join(models[:4]) or 'n/a'} | notes={' ; '.join(notes[:3]) or 'n/a'}"
        )
    return lines[:4]


def _product_line_context_payload(user_text: str) -> list[dict[str, Any]]:
    catalog = extract_workspace_docx_product_line_catalog()
    targeted = _targeted_product_line_keys(user_text)
    payload: list[dict[str, Any]] = []
    for key in targeted[:3]:
        item = catalog.get(key)
        if not item:
            continue
        models = [str(model).strip() for model in (item.get("models") or []) if str(model).strip()]
        notes = [str(note).strip() for note in (item.get("notes") or []) if str(note).strip()]
        payload.append(
            {
                "key": key,
                "name": str(item.get("name") or key).strip() or key,
                "summary": str(item.get("summary") or "").strip(),
                "models": models[:5],
                "notes": notes[:4],
            }
        )
    return payload


def _targeted_product_line_keys(user_text: str) -> list[str]:
    lowered = str(user_text or "").lower()
    series_order = ["LUNA", "EPIC", "LAB", "PRO", "EVO", "DF", "C", "AIR", "CHIP"]
    targeted: list[str] = []
    for key in series_order:
        token = key.lower()
        if key == "C":
            if re.search(r"(?<![a-z])c(\s+series|系列)(?![a-z])", lowered):
                targeted.append(key)
            continue
        if token in lowered:
            targeted.append(key)
    return targeted[:3]


def _product_line_guide_reply(bundle: dict[str, Any], user_text: str) -> dict[str, Any] | None:
    if not _matches_any(user_text, _PRODUCT_LINE_PATTERNS):
        return None
    catalog = extract_workspace_docx_product_line_catalog()
    targeted = _targeted_product_line_keys(user_text)
    lines = _product_line_context_lines(user_text)
    structured = _product_line_context_payload(user_text)
    if not lines or not targeted or not structured:
        return None
    lang = _reply_lang(bundle, user_text)
    question_mode = "compare" if len(targeted) >= 2 else "family"
    if len(targeted) >= 2:
        left = catalog.get(targeted[0]) or {}
        right = catalog.get(targeted[1]) or {}
        left_name = str(left.get("name") or targeted[0]).strip() or targeted[0]
        right_name = str(right.get("name") or targeted[1]).strip() or targeted[1]
        if lang == "zh":
            return {
                "title": "产品线区别",
                "text": f"我先按创作感觉帮你拆 {left_name} 和 {right_name}，再顺手把代表型号带上。",
                "quick_actions": ["按拍摄用途讲", "给我说焦段气质", "给我唯卓仕推荐"],
                "lock_ai_override": False,
                "helper_mode": "product_line_guide",
                "guide_draft": {
                    "mode": question_mode,
                    "targeted_lines": [left_name, right_name],
                    "goal": "先讲创作差异，再落到代表型号",
                },
                "product_line_context": lines,
                "product_line_records": structured,
            }
        return {
            "title": "Line difference",
            "text": f"I'll frame {left_name} versus {right_name} by shooting feel first, then anchor it with a few representative lenses.",
            "quick_actions": ["Map it to shooting style", "Explain focal-length feel", "Give a Viltrox pick"],
            "lock_ai_override": False,
            "helper_mode": "product_line_guide",
            "guide_draft": {
                "mode": question_mode,
                "targeted_lines": [left_name, right_name],
                "goal": "Compare creative feel first, then representative models",
            },
            "product_line_context": lines,
            "product_line_records": structured,
        }
    if lang == "zh":
        return {
            "title": "产品线",
            "text": "我先按这条产品线的创作定位给你理一下，再把代表型号收进去。",
            "quick_actions": ["给我说区别", "按拍摄用途讲", "给我唯卓仕推荐"],
            "lock_ai_override": False,
            "helper_mode": "product_line_guide",
            "guide_draft": {
                "mode": question_mode,
                "targeted_lines": [structured[0].get("name") or targeted[0]],
                "goal": "Explain family personality before listing models",
            },
            "product_line_context": lines,
            "product_line_records": structured,
        }
    return {
        "title": "Product line",
        "text": "I'll anchor this family in shooting style first, then pull in the representative models that matter.",
        "quick_actions": ["Explain the difference", "Map it to shooting style", "Give a Viltrox pick"],
        "lock_ai_override": False,
        "helper_mode": "product_line_guide",
        "guide_draft": {
            "mode": question_mode,
            "targeted_lines": [structured[0].get("name") or targeted[0]],
            "goal": "Explain family personality before listing models",
        },
        "product_line_context": lines,
        "product_line_records": structured,
    }


def _software_guide_reply(bundle: dict[str, Any], user_text: str) -> dict[str, Any] | None:
    if not _matches_any(user_text, _SOFTWARE_PATTERNS):
        return None
    lang = _reply_lang(bundle, user_text)
    lowered = str(user_text or "").lower()
    catalog = extract_workspace_docx_software_catalog()
    if not catalog:
        return None
    targets: list[str] = []
    if "nexus" in lowered:
        targets.append("nexus_focus")
    if "viltrox lens" in lowered or ("lens" in lowered and "viltrox" in lowered):
        targets.append("viltrox_lens")
    if "viltroxlink" in lowered:
        targets.append("viltroxlink")
    if "weey" in lowered:
        targets.append("weeylightpro")
    if not targets:
        targets = ["viltrox_lens", "nexus_focus", "viltroxlink"]
    descriptions = {
        "zh": {
            "viltrox_lens": "镜头固件、镜头设置和下载中心这条线主要看它。",
            "nexus_focus": "这更像移动端焦点/跟焦控制入口，偏现场控制。",
            "viltroxlink": "它更偏设备连接、设置同步和配件控制这条线。",
            "weeylightpro": "这条是灯光控制和灯效管理入口。",
        },
        "en": {
            "viltrox_lens": "This is the main lane for lens firmware, lens settings, and the download-center flow.",
            "nexus_focus": "This is the more mobile-first focus-control lane for on-set operation.",
            "viltroxlink": "This one leans toward device linking, settings sync, and accessory control.",
            "weeylightpro": "This is the lighting-control and lighting-scene management lane.",
        },
    }
    lines: list[str] = []
    for key in targets[:3]:
        item = catalog.get(key)
        if not item:
            continue
        notes = [str(note).strip() for note in (item.get("notes") or []) if str(note).strip()]
        links = [str(link).strip() for link in (item.get("links") or []) if str(link).strip()]
        if lang == "zh":
            note_line = descriptions["zh"].get(key) or ("；".join(notes[:2]) if notes else "可在官方下载中心或应用商店获取。")
            link_line = f" 入口：{links[0]}" if links else ""
            lines.append(f"{item.get('name')}: {note_line}{link_line}")
        else:
            note_line = descriptions["en"].get(key) or ("; ".join(notes[:2]) if notes else "You can get it from the official download center or app stores.")
            link_line = f" Entry: {links[0]}" if links else ""
            lines.append(f"{item.get('name')}: {note_line}{link_line}")
    if not lines:
        return None
    if lang == "zh":
        return {
            "title": "软件入口",
            "text": "我先按官方信息给你理清： " + " ".join(lines),
            "quick_actions": ["Viltrox Lens 是什么", "Nexus Focus 干嘛", "发我下载入口"],
            "lock_ai_override": False,
            "helper_mode": "software_guide",
            "software_context": lines,
        }
    return {
        "title": "Software guide",
        "text": "Here is the clean Viltrox software map: " + " ".join(lines),
        "quick_actions": ["What is Viltrox Lens", "What does Nexus Focus do", "Send official links"],
        "lock_ai_override": False,
        "helper_mode": "software_guide",
        "software_context": lines,
    }


def _photography_guide_reply(bundle: dict[str, Any], user_text: str) -> dict[str, Any] | None:
    if not _matches_any(user_text, _PHOTOGRAPHY_BASICS_PATTERNS):
        return None
    lang = _reply_lang(bundle, user_text)
    lowered = str(user_text or "").lower()
    if ("35" in lowered and "50" in lowered) or ("35mm" in lowered and "50mm" in lowered):
        if lang == "zh":
            return {
                "title": "35 vs 50",
                "text": "讲人话：35mm 更像你站在现场里，环境能一起带进来；50mm 更像你往前迈半步，主体更集中、脸更顺。人像更容易先用 50，扫街和环境叙事更容易先用 35。",
                "quick_actions": ["那 85mm 呢", "适合拍 vlog 吗", "给我唯卓仕推荐"],
                "lock_ai_override": False,
                "helper_mode": "photo_basics",
            }
        return {
            "title": "35 vs 50",
            "text": "Plain version: 35mm keeps you inside the scene and shows more environment. 50mm feels one step tighter, cleaner on faces, and more focused on the subject. Start with 50 for portraits, 35 for street and environmental storytelling.",
            "quick_actions": ["What about 85mm", "Good for vlog?", "Show a Viltrox option"],
            "lock_ai_override": False,
            "helper_mode": "photo_basics",
        }
    if any(token in lowered for token in ("shutter", "aperture", "iso", "快门", "光圈", "感光度", "曝光")):
        if lang == "zh":
            return {
                "title": "曝光三件套",
                "text": "快门像时间，越慢越亮但越容易糊；光圈像窗户，越大越亮背景越虚；ISO 像增益，越高越亮但噪点越多。先用快门保清晰，再用光圈定景深，最后拿 ISO 补亮度。",
                "quick_actions": ["夜景怎么设", "室内人像怎么设", "给我新手参数"],
                "lock_ai_override": False,
                "helper_mode": "photo_basics",
            }
        return {
            "title": "Exposure trio",
            "text": "Shutter is time: slower is brighter but blurrier. Aperture is the window: wider is brighter and blurrier in the background. ISO is gain: higher is brighter but noisier. Lock shutter for motion, use aperture for depth, then use ISO to finish exposure.",
            "quick_actions": ["Night settings", "Indoor portrait setup", "Beginner settings"],
            "lock_ai_override": False,
            "helper_mode": "photo_basics",
        }
    if "夜景" in lowered or "night" in lowered:
        if lang == "zh":
            return {
                "title": "夜景拍法",
                "text": "夜景短片先拍三类镜头就够：一条建立环境、一条人物动作、一条细节反光。手持就把快门保在 1/50 左右，宁可抬一点 ISO，也别让人物糊掉。",
                "quick_actions": ["再给我分镜", "怎么控噪点", "推荐唯卓仕镜头"],
                "lock_ai_override": False,
                "helper_mode": "photo_basics",
            }
        return {
            "title": "Night setup",
            "text": "For a city night short, start with just three shots: one wide establishing shot, one human action shot, and one detail/reflection shot. If you are handheld, keep shutter around 1/50 and raise ISO before letting people blur away.",
            "quick_actions": ["Give me a shot list", "How to control noise", "Recommend a Viltrox lens"],
            "lock_ai_override": False,
            "helper_mode": "photo_basics",
        }
    return None


def _casual_companion_reply(bundle: dict[str, Any], user_text: str, *, current_surface: str = "upload") -> dict[str, Any] | None:
    if not _matches_any(user_text, _CASUAL_CHAT_PATTERNS):
        return None
    lang = _reply_lang(bundle, user_text)
    if lang == "zh":
        return {
            "title": "我在",
            "text": "可以聊。我不只是盯上传，也能陪你聊摄影、镜头、分镜、夜景、剪辑节奏，或者先帮你把一个拍摄想法拆成 3 个能落地的镜头。",
            "quick_actions": ["聊夜景短片", "聊镜头怎么选", "先看上传状态"],
            "lock_ai_override": False,
            "helper_mode": "casual_chat",
        }
    return {
        "title": "I am here",
        "text": "Yes, we can actually chat. I can stay with uploads, but I can also talk photography, lenses, shot design, night scenes, and editing rhythm, or help turn one idea into three shootable shots.",
        "quick_actions": ["Talk night short film", "Lens advice", "Check upload status"],
        "lock_ai_override": False,
        "helper_mode": "casual_chat",
    }


def _classify_via_intent(
    bundle: dict[str, Any],
    user_text: str,
    *,
    current_surface: str = "upload",
) -> dict[str, Any]:
    persona = bundle.get("persona") or {}
    session = bundle.get("session") or {}
    text = str(user_text or "").strip()
    lowered = text.lower()
    profile_context = compact_via_profile_context(persona.get("profile") or {})
    session_state = session.get("state") or {}
    business_reply = get_via_business_reply(
        text,
        profile_context=profile_context,
        session_state=session_state,
    )
    product_reply = get_via_product_reply(
        text,
        profile_context=profile_context,
        session_state=session_state,
    )
    memory_query = _matches_any(text, _MEMORY_PATTERNS)
    software_query = _matches_any(text, _SOFTWARE_PATTERNS)
    product_line_query = _matches_any(text, _PRODUCT_LINE_PATTERNS)
    photography_basics_query = _matches_any(text, _PHOTOGRAPHY_BASICS_PATTERNS)
    casual_chat_query = _matches_any(text, _CASUAL_CHAT_PATTERNS)
    image_video_query = _matches_any(text, _IMAGE_VIDEO_PATTERNS) or current_surface in {"upload", "submission", "analysis"}
    deep_reasoning_query = _matches_any(text, _DEEP_REASONING_PATTERNS) or len(text) > 260
    follow_up_query = _matches_any(text, _FOLLOW_UP_MEMORY_PATTERNS)

    if memory_query:
        return {
            "intent": "memory",
            "brain": "memory_fast",
            "needs_memory": True,
            "use_deep_reasoning": False,
            "product_reply": None,
        }
    if software_query:
        return {
            "intent": "quick_chat",
            "brain": "quick_chat",
            "needs_memory": True,
            "use_deep_reasoning": False,
            "business_reply": None,
            "product_reply": None,
        }
    if product_line_query and not _matches_any(text, _TRANSACTIONAL_PRODUCT_PATTERNS):
        return {
            "intent": "creative_guidance",
            "brain": "creative_fast",
            "needs_memory": True,
            "use_deep_reasoning": bool(deep_reasoning_query),
            "business_reply": None,
            "product_reply": None,
        }
    if photography_basics_query:
        return {
            "intent": "creative_guidance",
            "brain": "creative_fast",
            "needs_memory": True,
            "use_deep_reasoning": False,
            "business_reply": None,
            "product_reply": None,
        }
    if casual_chat_query:
        return {
            "intent": "quick_chat",
            "brain": "quick_chat",
            "needs_memory": True,
            "use_deep_reasoning": False,
            "business_reply": None,
            "product_reply": None,
        }
    if image_video_query and deep_reasoning_query:
        return {
            "intent": "visual_reasoning",
            "brain": "deep_reasoning",
            "needs_memory": True,
            "use_deep_reasoning": True,
            "business_reply": None,
            "product_reply": None,
        }
    if business_reply:
        return {
            "intent": "business_support",
            "brain": "business_fast",
            "needs_memory": True,
            "use_deep_reasoning": False,
            "business_reply": business_reply,
            "product_reply": None,
        }
    if product_reply:
        return {
            "intent": "product",
            "brain": "product_fast",
            "needs_memory": True,
            "use_deep_reasoning": False,
            "business_reply": None,
            "product_reply": product_reply,
        }
    if image_video_query:
        return {
            "intent": "creative_guidance",
            "brain": "creative_fast",
            "needs_memory": True,
            "use_deep_reasoning": False,
            "business_reply": None,
            "product_reply": None,
        }
    if deep_reasoning_query:
        return {
            "intent": "deep_reasoning",
            "brain": "deep_reasoning",
            "needs_memory": True,
            "use_deep_reasoning": True,
            "business_reply": None,
            "product_reply": None,
        }
    return {
        "intent": "quick_chat",
        "brain": "quick_chat",
        "needs_memory": True,
        "use_deep_reasoning": False,
        "business_reply": None,
        "product_reply": None,
    }


def _should_use_ai_dialogue(route_info: dict[str, Any] | None, reply_payload: dict[str, Any] | None = None) -> bool:
    route_info = dict(route_info or {})
    reply_payload = dict(reply_payload or {})
    if reply_payload.get("lock_ai_override"):
        return False
    intent = str(route_info.get("intent") or "").strip().lower()
    return intent in {"quick_chat", "creative_guidance", "product", "business_support", "memory"} or bool(route_info.get("use_deep_reasoning"))


def _should_use_dialogue_collab(route_info: dict[str, Any] | None) -> bool:
    route_info = dict(route_info or {})
    intent = str(route_info.get("intent") or "").strip().lower()
    brain = str(route_info.get("brain") or "").strip().lower()
    return bool(route_info.get("use_deep_reasoning")) or intent in {"deep_reasoning", "visual_reasoning"} or brain == "deep_reasoning"


def _guard_sensitive_request(user_text: str) -> dict[str, Any] | None:
    text = str(user_text or "").strip()
    if not text:
        return None
    lowered = text.lower()
    for pattern in _IDENTITY_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            if _contains_cjk(text):
                return {
                    "title": "我是 Via",
                    "text": "我是 Via，Viltrox 的 live companion。你可以把我当成你的唯卓仕创作搭子，我会用公开的 Viltrox 信息、你的会话记忆和当前页面上下文来帮你。",
                    "quick_actions": ["推荐镜头", "看我的上传", "解释 VIP"],
                    "provider": "identity",
                }
            return {
                "title": "I am Via",
                "text": "I am Via, Viltrox's live companion. Think of me as your Viltrox creator guide, using public Viltrox knowledge, your session memory, and your current page context.",
                "quick_actions": ["Recommend a lens", "My uploads", "Explain VIP"],
                "provider": "identity",
            }
    for pattern in _JAILBREAK_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            if _contains_cjk(text):
                return {
                    "title": "规则还在",
                    "text": "我不会切掉安全规则，也不会暴露底层提示、模型、数据库或后台细节。你可以继续问公开的唯卓仕产品、你的上传、VIP、Affiliate 或内容建议。",
                    "quick_actions": ["唯卓仕镜头", "我的上传", "内容建议"],
                    "provider": "policy",
                }
            return {
                "title": "Rules stay on",
                "text": "I do not drop my safety rules or reveal hidden prompts, models, database details, or admin internals. Ask me about public Viltrox products, your uploads, VIP, affiliate, or content guidance instead.",
                "quick_actions": ["Viltrox lenses", "My uploads", "Content help"],
                "provider": "policy",
            }
    for pattern in _SENSITIVE_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            if _contains_cjk(text):
                return {
                    "title": "Private lane",
                    "text": "我不能透露数据库结构、后台细节、提示词、密钥、其他用户数据或未公开的内部产品信息。你可以继续问你自己的上传、VIP、Affiliate、内容建议或公开的 Viltrox 信息。",
                    "quick_actions": ["我的上传", "VIP 进度", "内容建议"],
                    "provider": "policy",
                }
            return {
                "title": "Private lane",
                "text": "I cannot reveal database details, internal prompts, keys, admin internals, other users' data, or unreleased product information. Ask me about your own uploads, VIP, affiliate progress, or public Viltrox signals instead.",
                "quick_actions": ["My uploads", "VIP progress", "Content help"],
                "provider": "policy",
            }
    return None

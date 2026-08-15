"""KOL profile contact values never cross an LLM/provider prompt boundary."""
from __future__ import annotations

import json
import re
from typing import Any

from app.api.routers import vkpi_kol_pool_intel
from app.domains.kol import (
    contact_system,
    outreach_draft,
    outreach_pack,
    profile_recall_projection,
    recall_pipeline,
)
from app.services.kol import account_dossier


NORMAL_PROFILE_TEXT = "cinematic street photography"
NORMAL_IDENTITY_HANDLE = "handle=@creator"
MARKER_EMAIL = "leak1@ex.test"
MARKER_PHONE = "+1 415 555 0199"
MARKER_WHATSAPP = "https://wa.me/447700900123"
MARKER_MAILTO = "mailto:leak2@ex.test"
SAFE_IDENTITY_URLS = (
    "https://youtube.com/@creator",
    "https://instagram.com/creator",
    "https://tiktok.com/@creator",
    "https://x.com/creator",
    "https://creator.example/public-profile",
    "https://creator.example/public-profile#portfolio",
)
MARKER_SOCIAL_URIS = (
    "https://t.me/tgleak",
    "https://telegram.me/tg2leak",
    "https://m.me/msgleak",
    "https://discord.gg/dcleak",
)
MARKER_CONTACT_ROUTE_URIS = (
    "https://x.com/messages/compose",
    "https://twitter.com/messages/compose",
    "https://facebook.com/messages/t/privateuser",
    "https://discord.com/users/123456789012345678",
    "https://discord.com/channels/123456789012345678/987654321098765432",
    "https://instagram.com/direct/t/1234567",
    "https://creator.example/p/leak3@ex.test",
    "https://creator.example/p?email=leak4@ex.test",
    "https://creator.example/p/+12345678",
    "https://creator.example/p?phone=+123456789012345",
    "https://creator.example/12345678",
    "https://creator.example/p?phone=123456789",
    "https://creator.example/p#phone=12345678",
    "https://creator.example/p/leak%2540ex.test",
    "https://creator.example/p/%252B123456789",
    "https://instagram.com/%2564irect/t/1234567",
    "https://instagram.com/%252564irect/t/1234567",
)
MARKER_CONTACT_ROUTE_PREFIXES = (
    "x.com/messages",
    "twitter.com/messages",
    "facebook.com/messages",
    "discord.com/users",
    "discord.com/channels",
    "instagram.com/direct",
    "creator.example/p/",
    "creator.example/p?",
    "creator.example/p#",
    "creator.example/12345678",
)
MARKER_SOCIAL_HANDLES = (
    "@igleak",
    "@ttleak",
    "@xleak",
    "@twleak",
    "@tgleak",
    "@mleak",
    "@dleak",
)
MARKER_UNADORNED_SOCIAL_HANDLES = (
    "privateuser#1234",
    "privateuser",
)
RAW_PROFILE_TEXT = (
    f"{NORMAL_PROFILE_TEXT}; {NORMAL_IDENTITY_HANDLE}; "
    + "; ".join(SAFE_IDENTITY_URLS)
    + f"; {MARKER_EMAIL}; {MARKER_PHONE}; "
    f"WhatsApp: {MARKER_WHATSAPP}; {MARKER_MAILTO}; "
    + "; ".join(MARKER_SOCIAL_URIS)
    + "; "
    + "; ".join(MARKER_CONTACT_ROUTE_URIS)
    + "; Instagram DM: @igleak; TikTok DM: @ttleak; X DM: @xleak; "
    "Twitter DM: @twleak; Telegram: @tgleak; Messenger: @mleak; Discord: @dleak; "
    "Discord: privateuser#1234; Telegram: privateuser"
)


def _assert_prompt_is_contact_free(
    prompt: str,
    *,
    keeps: str = NORMAL_PROFILE_TEXT,
    required_safe_urls: tuple[str, ...] = SAFE_IDENTITY_URLS[:3],
) -> None:
    lowered = prompt.casefold()
    assert keeps.casefold() in lowered
    assert NORMAL_IDENTITY_HANDLE.casefold() in lowered
    for safe_url in required_safe_urls:
        assert safe_url.casefold() in lowered
    assert MARKER_EMAIL.casefold() not in lowered
    assert MARKER_WHATSAPP.casefold() not in lowered
    assert MARKER_MAILTO.casefold() not in lowered
    for marker in (
        *MARKER_SOCIAL_URIS,
        *MARKER_CONTACT_ROUTE_URIS,
        *MARKER_SOCIAL_HANDLES,
        *MARKER_UNADORNED_SOCIAL_HANDLES,
    ):
        assert marker.casefold() not in lowered
    for prefix in MARKER_CONTACT_ROUTE_PREFIXES:
        assert prefix.casefold() not in lowered
    prompt_digits = re.sub(r"\D", "", prompt)
    assert re.sub(r"\D", "", MARKER_PHONE) not in prompt_digits
    assert "447700900123" not in prompt_digits
    assert "12345678" not in prompt_digits
    assert "123456789012345" not in prompt_digits
    assert "123456789012345678" not in prompt_digits
    assert "123456789" not in prompt_digits


def test_external_contact_sanitizer_keeps_identity_handle_but_removes_contact_routes() -> None:
    sanitized = contact_system.sanitize_contact_values_for_external_processing(
        {"handle": "@creator", "bio": RAW_PROFILE_TEXT, "primary_topic": NORMAL_PROFILE_TEXT}
    )

    assert sanitized["handle"] == "@creator"
    _assert_prompt_is_contact_free(
        sanitized["bio"], required_safe_urls=SAFE_IDENTITY_URLS
    )


def test_sync_outreach_pack_redacts_bio_before_generate_json(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(outreach_pack, "_model_binding", lambda: ("anthropic", "test-model"))

    def generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        return {
            "status": "success",
            "provider": "anthropic",
            "model": "test-model",
            "json": {
                "subject": "VILTROX collaboration",
                "email_en": "Hello Creator, this is a collaboration proposal.",
                "email_zh": "Creator 您好，这是一份合作建议。",
                "talking_points": ["真实测评"],
            },
        }

    monkeypatch.setattr(outreach_pack.llm_production, "generate_json", generate_json)

    draft, provenance = outreach_pack._generate_email_draft(
        {
            "id": 41,
            "display_name": "Street Creator",
            "handle": "street_creator",
            "platform": "youtube",
            "bio": RAW_PROFILE_TEXT,
        },
        {"why_fit": ["cinematic camera reviews"]},
        staff={"user_id": 8},
    )

    assert draft["personalized"] is True
    assert provenance["llm_used"] is True
    _assert_prompt_is_contact_free(
        captured["prompt"], required_safe_urls=SAFE_IDENTITY_URLS
    )


def test_outreach_prompt_removes_each_encoded_or_routed_contact_url(
    monkeypatch,
) -> None:
    prompts: list[str] = []
    safe_fragment_url = "https://creator.example/public-profile#portfolio"
    monkeypatch.setattr(outreach_pack, "_model_binding", lambda: ("anthropic", "test-model"))

    def generate_json(prompt: str, **_kwargs: Any) -> dict[str, Any]:
        prompts.append(prompt)
        return {
            "status": "success",
            "provider": "anthropic",
            "model": "test-model",
            "json": {
                "subject": "VILTROX collaboration",
                "email_en": "Hello Creator, this is a collaboration proposal.",
                "email_zh": "Creator 您好，这是一份合作建议。",
                "talking_points": ["真实测评"],
            },
        }

    monkeypatch.setattr(outreach_pack.llm_production, "generate_json", generate_json)

    for contact_url in MARKER_CONTACT_ROUTE_URIS:
        outreach_pack._generate_email_draft(
            {
                "id": 41,
                "display_name": "Street Creator",
                "handle": "@creator",
                "platform": "youtube",
                "bio": (
                    f"{NORMAL_PROFILE_TEXT}; {NORMAL_IDENTITY_HANDLE}; "
                    f"{safe_fragment_url}; {contact_url}"
                ),
            },
            {"why_fit": ["cinematic camera reviews"]},
            staff={"user_id": 8},
        )
        prompt = prompts[-1]
        lowered = prompt.casefold()
        assert NORMAL_PROFILE_TEXT in lowered
        assert NORMAL_IDENTITY_HANDLE in lowered
        assert safe_fragment_url in lowered
        assert contact_url.casefold() not in lowered
        assert "[contact removed]" in lowered

    assert len(prompts) == len(MARKER_CONTACT_ROUTE_URIS)


def test_outreach_draft_worker_redacts_bio_before_gateway(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    conn = object()
    monkeypatch.setattr(outreach_draft, "get_conn", lambda: conn)
    monkeypatch.setattr(
        outreach_draft,
        "_kol_context",
        lambda *_args: {
            "display_name": "Street Creator",
            "handle": "street_creator",
            "platform": "instagram",
            "primary_topic": "photography",
            "bio": RAW_PROFILE_TEXT,
        },
    )
    monkeypatch.setattr(outreach_draft, "_project_context", lambda *_args: {})
    monkeypatch.setattr(outreach_draft, "_personalization_lines", lambda *_args: ([], {"available": False}))

    def invoke(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        return {"status": "provider_unavailable", "text": ""}

    monkeypatch.setattr(outreach_draft.llm_gateway, "invoke", invoke)

    result = outreach_draft.run_outreach_draft_for_job({"kol_pool_id": 41}, staff={"id": 8})

    assert result == {"status": "failed", "reason": "provider_unavailable"}
    _assert_prompt_is_contact_free(captured["prompt"])


def test_translate_bio_redacts_contacts_before_gateway_and_keeps_content(monkeypatch) -> None:
    from app.platform import llm_gateway

    captured: dict[str, Any] = {}
    vkpi_kol_pool_intel._BIO_ZH_CACHE.clear()

    def invoke(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"status": "success", "text": "电影感街头摄影"}

    monkeypatch.setattr(llm_gateway, "invoke", invoke)

    result = vkpi_kol_pool_intel.translate_bio({"text": RAW_PROFILE_TEXT}, staff={"id": 8})

    assert result["status"] == "ready"
    assert result["translated"] == "电影感街头摄影"
    _assert_prompt_is_contact_free(captured["prompt"])


def test_semantic_recall_rerank_redacts_profile_text_before_gateway(monkeypatch) -> None:
    from app.platform import llm_gateway

    captured: dict[str, Any] = {}

    def invoke(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        return {
            "status": "success",
            "provider": "openai",
            "model": "test-model",
            "text": json.dumps([{"i": 1, "s": 92, "why": "摄影内容契合"}], ensure_ascii=False),
        }

    monkeypatch.setattr(llm_gateway, "invoke", invoke)

    scores, note = recall_pipeline._invoke_rerank_llm(
        "street photography creators",
        [{"kol_pool_id": 41, "profile_text": RAW_PROFILE_TEXT}],
    )

    assert scores[41]["s"] == 92
    assert "provider=openai" in note
    assert "scored=1" in note
    _assert_prompt_is_contact_free(captured["prompt"])


def test_profile_recall_rerank_redacts_bio_before_production_provider(monkeypatch) -> None:
    from app.core import model_registry
    from app.platform import llm_production

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        model_registry,
        "current_task_model_binding",
        lambda: {"kol_content_fit_analysis": "openai/test-model"},
    )

    def generate_text(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        return {
            "status": "success",
            "provider": "openai",
            "model": "test-model",
            "text": json.dumps([{"i": i, "s": 90 - i} for i in range(1, 5)]),
        }

    monkeypatch.setattr(llm_production, "generate_text", generate_text)
    candidates = [
        {
            "kol_pool_id": index,
            "handle": f"creator{index}",
            "bio": RAW_PROFILE_TEXT,
            "why_fit": "cinematic photography",
        }
        for index in range(1, 5)
    ]

    note = profile_recall_projection._llm_rerank_buckets(
        {"creator": candidates, "reviewer": []},
        "street photography creators",
        "photographer",
        "VILTROX lens",
    )

    assert note.startswith("ok")
    _assert_prompt_is_contact_free(captured["prompt"])


def test_account_dossier_redacts_nested_contact_aliases_before_claude(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    raw_marker = "raw-secret@ex.test"
    links_marker = "https://wa.me/491701234567"

    def generate_text(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        return {"status": "success", "provider": "anthropic", "model": "test-model", "text": "{}"}

    monkeypatch.setattr(account_dossier, "generate_text", generate_text)
    context = {
        "kol": {
            "id": 41,
            "display_name": "Street Creator",
            "platform": "instagram",
            "bio": RAW_PROFILE_TEXT,
            "primary_topic": NORMAL_PROFILE_TEXT,
            "contact_email": MARKER_EMAIL,
            "phone": MARKER_PHONE,
            "raw_json": {"business_email": raw_marker},
            "contact_links": [{"url": links_marker}],
        },
        "snapshot": {
            "id": 9,
            "follower_count": 12000,
            "raw_json": {"email": raw_marker},
            "contact_email": MARKER_EMAIL,
            "phone": MARKER_PHONE,
            "contact_links": [{"url": links_marker}],
        },
        "posts": [{"title": "A cinematic street photography walk", "views": 5000}],
        "comments": [],
    }

    report = account_dossier._claude_report(context, "AF 35mm F1.2 LAB")

    assert report["method"] == "claude_account_dossier"
    _assert_prompt_is_contact_free(captured["prompt"])
    assert raw_marker not in captured["prompt"]
    assert links_marker not in captured["prompt"]

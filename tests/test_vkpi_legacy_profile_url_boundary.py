"""Legacy profile URLs preserve public identity pages, never contact routes."""
from __future__ import annotations

import pytest

from app.domains.kol.contact_access import project_profile_contacts


@pytest.mark.parametrize(
    "profile_url",
    [
        "https://wa.me/14155552671",
        "https://t.me/privateuser",
        "https://m.me/privateuser",
        "https://discord.gg/privateinvite",
        "https://discord.com/invite/privateinvite",
        "https://discord.com/users/123456789012345678",
        "https://instagram.com/direct/t/123456789",
        "https://x.com/messages/compose?recipient_id=123456789",
        "https://twitter.com/messages/compose?recipient_id=123456789",
        "https://facebook.com/messages/t/creator.name",
        "https://creator.example/profile/private@example.com",
        "https://creator.example/profile?email=private@example.com",
        "https://creator.example/profile/+12345678",
        "https://creator.example/profile?phone=+123456789012345",
        "mailto:private@example.com",
        "tel:+14155552671",
    ],
)
def test_legacy_profile_projection_drops_contact_and_direct_message_urls(
    profile_url: str,
) -> None:
    result = project_profile_contacts(
        {"id": 7, "contacts": {"profile_url": profile_url}}, reveal=False
    )

    assert result["contacts"] == {
        "profile_url": "",
        "contact_masked": True,
        "contact_projection_reason": "summary_only",
    }
    assert result["contact_masked"] is True
    assert result["contact_projection_reason"] == "summary_only"
    assert profile_url not in str(result)


@pytest.mark.parametrize(
    ("profile_url", "expected"),
    [
        (
            "https://www.youtube.com/channel/UC1234567890?view_as=subscriber",
            "https://youtube.com/channel/UC1234567890",
        ),
        (
            "https://www.instagram.com/Creator.Name/?hl=en",
            "https://instagram.com/Creator.Name/",
        ),
        (
            "https://www.tiktok.com/@CameraCreator?lang=en",
            "https://tiktok.com/@CameraCreator",
        ),
        (
            "https://creator.example/public-profile?ref=directory#about",
            "https://creator.example/public-profile",
        ),
        (
            "https://x.com/camera_creator?ref_src=profile",
            "https://x.com/camera_creator",
        ),
        (
            "https://twitter.com/camera_creator?ref_src=profile",
            "https://twitter.com/camera_creator",
        ),
    ],
)
def test_legacy_profile_projection_keeps_public_creator_identity_pages(
    profile_url: str,
    expected: str,
) -> None:
    result = project_profile_contacts(
        {"id": 7, "contacts": {"profile_url": profile_url}}, reveal=False
    )

    assert result["contacts"] == {
        "profile_url": expected,
        "contact_masked": True,
        "contact_projection_reason": "summary_only",
    }
    assert result["contact_masked"] is True
    assert result["contact_projection_reason"] == "summary_only"


def test_legacy_profile_projection_keeps_identity_handle_with_safe_profile_url() -> None:
    result = project_profile_contacts(
        {
            "id": 7,
            "handle": "@camera_creator",
            "contacts": {"profile_url": "https://x.com/camera_creator"},
        },
        reveal=False,
    )

    assert result["handle"] == "@camera_creator"
    assert result["contacts"]["profile_url"] == "https://x.com/camera_creator"
    assert result["contacts"]["contact_masked"] is True

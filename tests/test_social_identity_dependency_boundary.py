from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.db.repositories import users
from app.services.verification import viltrox_official


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("platform", "value", "expected"),
    [
        ("instagram", "https://www.instagram.com/petapixel/?hl=en", ("instagram", "@petapixel", True)),
        ("tiktok", "https://www.tiktok.com/@viltrox.usa", ("tiktok", "@viltrox.usa", True)),
        ("youtube", "https://www.youtube.com/channel/UC123/videos", ("youtube", "@UC123", True)),
        ("reddit", "https://www.reddit.com/user/PetaPixel", ("reddit", "u/PetaPixel", True)),
        ("facebook", "https://www.facebook.com/pages/Name/12345", ("facebook", "@12345", True)),
        ("twitter", "https://x.com/ViltroxOfficial", ("twitter", "@ViltroxOfficial", True)),
        ("instagram", "@creator", ("instagram", "@creator", True)),
        ("instagram", "https://example.com/creator", ("instagram", "", False)),
        ("", "@creator", ("", "", False)),
    ],
)
def test_repository_social_identity_behavior_is_preserved(
    platform: str,
    value: str,
    expected: tuple[str, str, bool],
) -> None:
    assert users.sanitize_social_identity(platform, value) == expected


def test_verification_public_parser_keeps_module_monkeypatch_seam(monkeypatch) -> None:
    monkeypatch.setattr(
        viltrox_official,
        "detect_platform_from_profile_url",
        lambda _url: "instagram",
    )

    assert (
        viltrox_official.extract_handle_from_profile_url(
            "https://example.invalid/creator"
        )
        == "creator"
    )

    monkeypatch.setattr(
        viltrox_official,
        "detect_platform_from_profile_url",
        lambda _url: None,
    )
    assert (
        viltrox_official.extract_handle_from_profile_url(
            "https://www.instagram.com/creator"
        )
        == ""
    )


def test_user_repository_no_longer_imports_verification_service() -> None:
    path = ROOT / "backend/app/db/repositories/users.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }

    assert not any(
        module == "app.services.verification"
        or module.startswith("app.services.verification.")
        for module in imports
    )

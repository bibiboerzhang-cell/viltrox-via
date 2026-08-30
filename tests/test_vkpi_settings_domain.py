import asyncio

from app.domains import settings as settings_domain
from app.domains.settings import use_cases as settings_use_cases


def test_settings_domain_manager_helpers(monkeypatch):
    monkeypatch.setattr(
        settings_use_cases.staff_role_policy,
        "has_manager_staff_role",
        lambda staff: staff.get("role") == "manager",
    )
    monkeypatch.setattr(settings_use_cases.scope, "can_view_all", lambda staff: staff.get("all") is True)

    assert settings_domain.is_manager_staff({"role": "manager"}) is True
    assert settings_domain.is_manager_staff({"role": "member"}) is False
    assert settings_domain.can_view_all({"all": True}) is True


def test_settings_domain_provider_and_crawl_wrappers(monkeypatch):
    calls: dict[str, object] = {}

    monkeypatch.setattr(settings_use_cases.vkpi_settings, "provider_statuses", lambda: {"providers": []})

    async def probe(provider):
        calls["provider"] = provider
        return {"provider": provider, "ok": True}

    monkeypatch.setattr(settings_use_cases.vkpi_settings, "probe", probe)
    monkeypatch.setattr(settings_use_cases.platform_crawl_settings, "feature_flags", lambda: {"flags": {}})
    monkeypatch.setattr(
        settings_use_cases.platform_crawl_settings,
        "update_platform_settings",
        lambda body, *, staff: {"body": body, "staff": staff},
    )

    assert settings_domain.provider_statuses() == {"providers": []}
    assert asyncio.run(settings_domain.provider_probe("youtube")) == {"provider": "youtube", "ok": True}
    assert calls["provider"] == "youtube"
    assert settings_domain.feature_flags() == {"flags": {}}
    assert settings_domain.update_platform_crawl({"youtube": {"enabled": True}}, staff={"id": 1}) == {
        "body": {"youtube": {"enabled": True}},
        "staff": {"id": 1},
    }


def test_settings_domain_preferences_and_notifications(monkeypatch):
    monkeypatch.setattr(
        settings_use_cases.user_preferences,
        "get_preferences",
        lambda *, staff, staff_id: {"staff": staff, "staff_id": staff_id},
    )
    monkeypatch.setattr(
        settings_use_cases.user_preferences,
        "list_preferences",
        lambda *, staff, limit: {"limit": limit, "staff": staff},
    )
    monkeypatch.setattr(
        settings_use_cases.notification_settings,
        "update_notification_settings",
        lambda body, *, staff: {"body": body, "staff": staff},
    )
    monkeypatch.setattr(
        settings_use_cases.notification_settings,
        "list_notification_settings",
        lambda *, staff, limit: {"notifications": [], "limit": limit, "staff": staff},
    )

    assert settings_domain.preference_settings(staff={"id": 2}, staff_id=3) == {"staff": {"id": 2}, "staff_id": 3}
    assert settings_domain.preference_settings_list(staff={"id": 2}, limit=20) == {"limit": 20, "staff": {"id": 2}}
    assert settings_domain.update_notifications({"email": True}, staff={"id": 4}) == {
        "body": {"email": True},
        "staff": {"id": 4},
    }
    assert settings_domain.notifications_list(staff={"id": 4}, limit=10) == {
        "notifications": [],
        "limit": 10,
        "staff": {"id": 4},
    }

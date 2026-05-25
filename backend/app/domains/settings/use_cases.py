"""Settings domain use cases."""
from __future__ import annotations

from typing import Any

from app.domains import staff as staff_domain
import importlib

from app.domains.access import scope
from app.services.vkpi import notification_settings, settings as vkpi_settings, user_preferences

platform_crawl_settings = importlib.import_module("app.domains.settings.platform_crawl")


def is_manager_staff(staff: dict[str, Any]) -> bool:
    return staff_domain.is_manager_staff(staff)


def can_view_all(staff: dict[str, Any]) -> bool:
    return scope.can_view_all(staff)


def provider_statuses() -> dict[str, Any]:
    return vkpi_settings.provider_statuses()


async def provider_probe(provider: str) -> dict[str, Any]:
    return await vkpi_settings.probe(provider)


def feature_flags() -> dict[str, Any]:
    return platform_crawl_settings.feature_flags()


def update_feature_flags(body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    return platform_crawl_settings.update_feature_flags(body, staff=staff)


def platform_crawl() -> dict[str, Any]:
    return platform_crawl_settings.platform_settings()


def update_platform_crawl(body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    return platform_crawl_settings.update_platform_settings(body, staff=staff)


def budget_settings() -> dict[str, Any]:
    return platform_crawl_settings.budget_settings()


def update_budget_settings(body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    return platform_crawl_settings.update_budget_settings(body, staff=staff)


def control_status() -> dict[str, Any]:
    return platform_crawl_settings.control_status()


def comment_alert_settings() -> dict[str, Any]:
    return platform_crawl_settings.comment_alert_settings()


def update_comment_alert_settings(body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    return platform_crawl_settings.update_comment_alert_settings(body, staff=staff)


def preference_settings(*, staff_id: int | None = None, staff: dict[str, Any]) -> dict[str, Any]:
    return user_preferences.get_preferences(staff=staff, staff_id=staff_id)


def update_preference_settings(body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    return user_preferences.update_preferences(body, staff=staff)


def preference_settings_list(*, limit: int = 200, staff: dict[str, Any]) -> dict[str, Any]:
    return user_preferences.list_preferences(staff=staff, limit=limit)


def notifications(*, staff_id: int | None = None, staff: dict[str, Any]) -> dict[str, Any]:
    return notification_settings.get_notification_settings(staff=staff, staff_id=staff_id)


def update_notifications(body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    return notification_settings.update_notification_settings(body, staff=staff)


def notifications_list(*, limit: int = 200, staff: dict[str, Any]) -> dict[str, Any]:
    return notification_settings.list_notification_settings(staff=staff, limit=limit)

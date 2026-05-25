#!/usr/bin/env python3
"""Static smoke for P2.10 comment alert threshold settings UI/API wiring."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    path = ROOT / rel
    assert path.exists(), f"missing {rel}"
    return path.read_text(encoding="utf-8")


def main() -> None:
    settings_router = read("backend/app/api/routers/vkpi_settings.py")
    platform_settings = read("backend/app/domains/settings/platform_crawl.py")
    alerts = read("backend/app/services/vkpi/alerts.py")
    ui_api = read("frontend/src/services/vkpi.ui-api.ts")
    settings_page = read("frontend/src/components/vkpi/pages/SettingsPage.tsx")
    controls = read("frontend/src/components/vkpi/pages/settings/SettingsControlPanels.tsx")

    assert '"comment_intelligence_alerts"' in platform_settings, "comment alert flag default missing"
    assert "comment_alert_settings" in platform_settings and "update_comment_alert_settings" in platform_settings, "settings service missing"
    assert "/settings/comment-alerts" in settings_router, "comment alert settings route missing"
    assert "platform_crawl_settings.comment_alert_settings" in alerts, "alert generator does not read settings"
    assert "min_negative_i" in alerts and "min_hostile_i" in alerts, "threshold normalization missing"
    assert "getCommentAlertSettings" in ui_api and "updateCommentAlertSettings" in ui_api, "frontend API methods missing"
    assert "CommentAlertThresholdCard" in settings_page and "saveCommentAlertSettings" in settings_page, "SettingsPage threshold wiring missing"
    assert "评论风险告警" in controls and "min_hostile" in controls, "threshold settings UI missing"

    print("VKPI_COMMENT_ALERT_SETTINGS_FRONTEND_SMOKE_OK")


if __name__ == "__main__":
    main()

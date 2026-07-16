#!/usr/bin/env python3
"""Static smoke for P2.4 comment intelligence frontend wiring."""
from __future__ import annotations
from stdout_utils import out as stdout_out

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    path = ROOT / rel
    assert path.exists(), f"missing {rel}"
    return path.read_text(encoding="utf-8")


def main() -> None:
    api = read("frontend/src/services/vkpi.ui-api.ts")
    panel = read("frontend/src/components/vkpi/pages/data-analysis/shared/CommentIntelligencePanel.tsx")
    sentiment = read("frontend/src/components/vkpi/pages/data-analysis/tabs/SentimentTab.tsx")
    cross_platform = read("frontend/src/components/vkpi/pages/data-analysis/CrossPlatformPanel.tsx")
    css = read("frontend/src/components/vkpi/pages/data-analysis/styles/data-analysis.css")

    assert "VkpiCommentIntelligenceOverview" in api, "overview type missing"
    assert "getCommentIntelligenceOverview" in api, "overview API method missing"
    assert "distributions" in api and "brand_attitude" in api, "overview distribution types missing"
    assert "processRecentCommentIntelligence" in api, "process-recent API method missing"
    assert "retryCommentIntelligenceRun" in api, "retry API method missing"
    assert "/api/admin/vkpi/comment-intelligence/overview" in api, "overview endpoint missing"
    assert "/api/admin/vkpi/comment-intelligence/process-recent" in api, "process-recent endpoint missing"
    assert "/api/admin/vkpi/comment-intelligence/runs/" in api, "retry endpoint missing"
    assert "CommentIntelligencePanel" in panel, "panel component missing"
    assert "Pipeline Runs" in panel and "Post Pillars" in panel, "panel metrics missing"
    assert "recentRuns" in panel and "da-ci-runs" in panel, "recent run table missing"
    assert "DistributionList" in panel and "Top Pillars" in panel, "distribution chart UI missing"
    assert "处理最近帖子" in panel and "retryCommentIntelligenceRun" in panel, "manual pipeline actions missing"
    assert "CommentIntelligencePanel" in sentiment and "apiToken" in sentiment, "sentiment tab wiring missing"
    assert "<SentimentTab apiToken={apiToken}" in cross_platform, "CrossPlatformPanel must pass token"
    assert ".da-ci-health" in css and ".da-ci-error" in css and ".da-ci-message" in css, "comment intelligence CSS missing"
    assert ".da-ci-distribution-grid" in css and ".da-ci-bar" in css, "distribution CSS missing"

    stdout_out("VKPI_COMMENT_INTELLIGENCE_FRONTEND_SMOKE_OK")


if __name__ == "__main__":
    main()
